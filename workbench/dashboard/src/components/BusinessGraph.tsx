import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { PanZoomCanvas, type CanvasEdge, type CanvasFocusPoint } from "@/components/PanZoomCanvas"
import { fetchDetail, fetchDownstream } from "@/lib/api"
import { errorLabel, isAbort, isAccessDenial } from "@/lib/errors"
import { RAG_LABELS, TYPE_LABELS, formalBusinessText, referenceLabel } from "@/lib/labels"
import { rulesOfContractVersion, type RulesVersion } from "@/lib/ontology"
import { shortId } from "@/lib/format"
import type { Detail, DownstreamEdge, ResolvedRef } from "@/lib/types"
import { cn } from "@/lib/utils"

const NODE_W = 216
const NODE_H = 132
const GAP_X = 80
const GAP_Y = 18
const PAD = 24
/** Auto-expansion of the formal LTCO→PCO backbone stops above this fan-out. */
const AUTO_EXPAND_FANOUT = 12
const BACKBONE_TYPES = new Set(["LTCO", "PCO"])

const HISTORICAL_STATUSES = new Set(["superseded", "superseded_confirmed", "historical"])
/** Non-formal downstream statuses that fold behind the per-node expander by
 *  default.  Recorded analysis materials (PeriodReview/BusinessFact/…) and
 *  unknown statuses stay visible —可用不等于待审批，未知不按候选处理。 */
const CANDIDATE_STATUSES = new Set(["draft", "candidate", "under_review", "proposed",
                                    "recommendation", "returned"])

export function nodeKey(objectId: string, revisionId: string): string {
  return `${objectId}@${revisionId}`
}

interface Neighbor {
  key: string
  objectId: string
  revisionId: string
  objectType: string
  title: string | null
  formal: boolean | null
  formalStatus: string | null
  rag: string | null
  edgeLabel: string
  /** out = node → neighbor（下一层）；in = neighbor → node（依据/来源）。 */
  direction: "out" | "in"
  kind: "basis" | "upstream" | "downstream"
}

interface NodeState {
  key: string
  objectId: string
  revisionId: string
  objectType: string
  title: string | null
  formal: boolean | null
  formalStatus: string | null
  rag: string | null
  rules: RulesVersion | null
  rulesUnknown: boolean
  basisHistorical?: boolean
  depth: number
  expanded: boolean
  /** Auto-expansion tried once — failures wait for a manual retry, never loop. */
  autoTried: boolean
  loading: boolean
  error: unknown
  /** First-page relations from the latest authoritative detail read. */
  neighbors: Neighbor[] | null
  /** Manually paged-in downstream continuations; cleared on refresh because a
   *  later page cannot be revalidated against the refreshed first page. */
  extraDownstream: Neighbor[]
  extrasCleared: boolean
  downstreamCursor: string | null | undefined
  downstreamHasMore: boolean
}

export interface GraphFocusEntry {
  objectId: string
  revisionId: string | null
  label: string
}

export interface BusinessGraphProps {
  strategyId: string | null
  /** Object (or strategy) the current URL points at; new entries get focused. */
  entryFocus: GraphFocusEntry | null
  /** Authorization/strategy boundary — a change resets the protected graph. */
  entryKey: string
  /** Foreground refresh only runs while the graph view is visible. */
  active: boolean
  selectedObject: string | null
  selectedRevision: string | null
  onOpenObject: (objectId: string, revisionId?: string) => void
  onAccessDenied: () => void
}

function downstreamNeighbor(edge: DownstreamEdge): Neighbor {
  const labels = edge.matched_fields.map(referenceLabel)
  return {
    key: nodeKey(edge.object_id, edge.ref.revision_id),
    objectId: edge.object_id,
    revisionId: edge.ref.revision_id,
    objectType: edge.object_type,
    title: edge.title ?? edge.summary ?? null,
    formal: edge.formal_state.formal,
    formalStatus: edge.formal_state.status,
    rag: edge.state_summary?.rag ?? null,
    edgeLabel: labels.length ? labels.join("、") : "下一层对象",
    direction: "out",
    kind: "downstream",
  }
}

function refNeighbor(ref: ResolvedRef, kind: "basis" | "upstream"): Neighbor {
  return {
    key: nodeKey(ref.object_id, ref.revision_id),
    objectId: ref.object_id,
    revisionId: ref.revision_id,
    objectType: ref.object_type,
    title: ref.title,
    formal: null,
    formalStatus: null,
    rag: null,
    edgeLabel: referenceLabel(ref.path),
    direction: "in",
    kind,
  }
}

function detailNeighbors(detail: Detail): Neighbor[] {
  return [
    ...detail.relations.own_basis_refs.map((ref) => {
      const neighbor = refNeighbor(ref, "basis")
      if (detail.basis.status === "historical" && detail.basis.strategy_ref?.object_id === ref.object_id
          && detail.basis.strategy_ref.revision_id === ref.revision_id) neighbor.edgeLabel = "历史战略依据"
      return neighbor
    }),
    ...detail.relations.support_refs.map((ref) => refNeighbor(ref, "basis")),
    ...detail.relations.upstream_refs.map((ref) => refNeighbor(ref, "upstream")),
    ...detail.relations.downstream.items.map(downstreamNeighbor),
  ]
}

function rulesOf(detail: Detail): { rules: RulesVersion | null; rulesUnknown: boolean } {
  const raw = detail.protocol.contract_version
  const rules = rulesOfContractVersion(typeof raw === "string" ? raw : null)
  return { rules, rulesUnknown: rules === null }
}

/** Extra annotation rows below a node card participate in the layout height. */
function nodeHeight(node: NodeState, hiddenCount: number): number {
  let extra = 0
  if (node.expanded) {
    if (node.downstreamHasMore) extra += 24
    if (hiddenCount > 0) extra += 20
    if (node.extrasCleared) extra += 18
    if ((node.neighbors?.length ?? 0) + node.extraDownstream.length === 0) extra += 18
  }
  if (node.error) extra += 22
  return NODE_H + (extra ? extra + 4 : 0)
}

/**
 * 业务关系图：从所选战略（或聚焦对象）沿已记录的精确引用逐步展开。
 * 节点身份 = object_id + revision_id，同一对象的不同版本永不合并。
 * 依据/来源连线永远保留（含历史必需依据）；待确认候选类下游默认收拢、
 * 可随时展开；分析材料与未知效力不按候选收拢。
 */
export function BusinessGraph({ strategyId, entryFocus, entryKey, active, selectedObject,
                                selectedRevision, onOpenObject, onAccessDenied }: BusinessGraphProps) {
  const [nodes, setNodes] = useState<Record<string, NodeState>>({})
  const [order, setOrder] = useState<string[]>([])
  const [focusStack, setFocusStack] = useState<GraphFocusEntry[]>([])
  const [rootError, setRootError] = useState<unknown>(null)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const [showNonFormal, setShowNonFormal] = useState(false)
  const [edgeListOpen, setEdgeListOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [focusPoint, setFocusPoint] = useState<CanvasFocusPoint | null>(null)
  const focusSeq = useRef(0)
  const pendingFocus = useRef<string | null>(null)
  const nodeRefs = useRef(new Map<string, HTMLDivElement>())
  const nodesRef = useRef<Record<string, NodeState>>({})
  // Monotonic request tokens; a reset bumps the generation and aborts all
  // controllers so any in-flight response from the previous graph is
  // discarded, never colliding epochs.
  const tokenSeq = useRef(0)
  const tokens = useRef(new Map<string, number>())
  const controllers = useRef(new Map<string, AbortController>())
  const generationRef = useRef(0)

  useEffect(() => {
    nodesRef.current = nodes
  }, [nodes])

  useEffect(() => () => {
    generationRef.current += 1
    for (const controller of controllers.current.values()) controller.abort()
  }, [])

  const patchNode = useCallback((key: string, patch: Partial<NodeState>) => {
    setNodes((current) => current[key] ? { ...current, [key]: { ...current[key], ...patch } } : current)
  }, [])

  /** Create a node on discovery; an existing node refreshes the metadata the
   *  referring edge knows about (title/formal/rag) — a newer authorized read
   *  must not leave stale labels behind.  Depth stays at first discovery. */
  const upsertNode = useCallback((base: Omit<NodeState, "expanded" | "autoTried" | "loading"
    | "error" | "neighbors" | "extraDownstream" | "extrasCleared" | "downstreamCursor"
    | "downstreamHasMore">) => {
    setNodes((current) => {
      const existing = current[base.key]
      if (existing) {
        return { ...current, [base.key]: { ...existing,
          objectType: existing.objectType || base.objectType,
          title: base.title ?? existing.title,
          formal: base.formal ?? existing.formal,
          formalStatus: base.formalStatus ?? existing.formalStatus,
          rag: base.rag ?? existing.rag,
          rules: base.rules ?? existing.rules,
          rulesUnknown: existing.rulesUnknown || base.rulesUnknown } }
      }
      return { ...current, [base.key]: { ...base, expanded: false, autoTried: false, loading: false,
                                         error: null, neighbors: null, extraDownstream: [],
                                         extrasCleared: false, downstreamCursor: undefined,
                                         downstreamHasMore: false } }
    })
    setOrder((current) => current.includes(base.key) ? current : [...current, base.key])
  }, [])

  const beginRequest = useCallback((key: string) => {
    const token = ++tokenSeq.current
    tokens.current.set(key, token)
    controllers.current.get(key)?.abort()
    const controller = new AbortController()
    controllers.current.set(key, controller)
    return { token, generation: generationRef.current, signal: controller.signal }
  }, [])

  const requestValid = useCallback((key: string, token: number, generation: number) =>
    generationRef.current === generation && tokens.current.get(key) === token, [])

  /** Neighbor nodes are created from one expansion; existing keys keep their
   *  first depth —同一精确版本只有一个节点。 */
  const addNeighbors = useCallback((node: NodeState, neighbors: Neighbor[]) => {
    for (const neighbor of neighbors) {
      upsertNode({
        key: neighbor.key, objectId: neighbor.objectId, revisionId: neighbor.revisionId,
        objectType: neighbor.objectType, title: neighbor.title, formal: neighbor.formal,
        formalStatus: neighbor.formalStatus, rag: neighbor.rag, rules: null, rulesUnknown: false,
        depth: node.depth + (neighbor.direction === "out" ? 1 : -1),
      })
      // A fresh downstream projection is authoritative, including missing
      // state/title values; a plain basis reference cannot overwrite metadata.
      if (neighbor.kind === "downstream") patchNode(neighbor.key, {
        title: neighbor.title, formal: neighbor.formal, formalStatus: neighbor.formalStatus,
        rag: neighbor.rag,
      })
    }
  }, [upsertNode, patchNode])

  /** Apply an authoritative detail read.  Base relations are replaced wholesale
   *  so revoked/vanished neighbors disappear; manual continuation pages are
   *  dropped too (they cannot be revalidated) and the loss is announced. */
  const applyDetail = useCallback((node: NodeState, detail: Detail, neighbors: Neighbor[]) => {
    const hadExtras = (nodesRef.current[node.key]?.extraDownstream.length ?? 0) > 0
    const { rules, rulesUnknown } = rulesOf(detail)
    patchNode(node.key, {
      loading: false, expanded: true, neighbors,
      extraDownstream: [], extrasCleared: hadExtras,
      objectType: node.objectType || detail.object.object_type,
      title: detail.business.title ?? detail.business.summary ?? node.title,
      formal: detail.formal_state.formal,
      formalStatus: detail.formal_state.status,
      basisHistorical: detail.basis.status === "historical",
      rules, rulesUnknown,
      downstreamCursor: detail.relations.downstream.next_cursor,
      downstreamHasMore: detail.relations.downstream.has_more,
    })
  }, [patchNode])

  const expandParams = useCallback(async (params: { key: string; objectId: string;
    revisionId: string; depth: number }, manual: boolean) => {
    const state = nodesRef.current[params.key]
    if (state?.loading || state?.expanded) return
    if (state?.autoTried && !manual) return
    patchNode(params.key, { loading: true, error: null, autoTried: true })
    const { token, generation, signal } = beginRequest(params.key)
    try {
      const detail = await fetchDetail(params.objectId, params.revisionId, strategyId, signal)
      if (!requestValid(params.key, token, generation)) return
      const neighbors = detailNeighbors(detail)
      const host = nodesRef.current[params.key]
        ?? { ...state, ...params, depth: params.depth } as NodeState
      addNeighbors(host, neighbors)
      applyDetail(host, detail, neighbors)
      // Reasonable backbone: formal LTCO/PCO neighbors auto-expand one chain so
      // the selected strategy shows its LTCO/PCO/Mission trunk without clicking
      // every level; everything else stays one click away.
      const backbone = neighbors.filter((neighbor) =>
        neighbor.kind === "downstream" && neighbor.formal === true
        && BACKBONE_TYPES.has(neighbor.objectType))
      if (backbone.length > 0 && backbone.length <= AUTO_EXPAND_FANOUT) {
        for (const neighbor of backbone) {
          void expandParams({ key: neighbor.key, objectId: neighbor.objectId,
                              revisionId: neighbor.revisionId, depth: params.depth + 1 }, false)
        }
      }
    } catch (error) {
      if (!requestValid(params.key, token, generation) || isAbort(error)) return
      if (isAccessDenial(error)) {
        onAccessDenied()
        return
      }
      patchNode(params.key, { loading: false, error })
    }
  }, [strategyId, beginRequest, requestValid, patchNode, addNeighbors, applyDetail, onAccessDenied])

  const expand = useCallback((key: string, manual = false) => {
    const node = nodesRef.current[key]
    if (!node) return
    void expandParams({ key, objectId: node.objectId, revisionId: node.revisionId,
                        depth: node.depth }, manual)
  }, [expandParams])

  const loadMoreDownstream = useCallback(async (key: string) => {
    const node = nodesRef.current[key]
    if (!node || !node.expanded || node.loading) return
    const cursor = node.downstreamCursor
    if (!cursor) return
    patchNode(key, { loading: true, error: null })
    const { token, generation, signal } = beginRequest(`${key}#more`)
    try {
      const page = await fetchDownstream(node.objectId, node.revisionId, cursor, signal)
      if (!requestValid(`${key}#more`, token, generation)) return
      const fresh = page.items.map(downstreamNeighbor)
      addNeighbors(node, fresh)
      setNodes((current) => {
        const existing = current[key]
        if (!existing) return current
        const seen = new Set([...(existing.neighbors ?? []), ...existing.extraDownstream]
          .map((neighbor) => `${neighbor.key}:${neighbor.edgeLabel}`))
        const merged = [...existing.extraDownstream,
                        ...fresh.filter((neighbor) => !seen.has(`${neighbor.key}:${neighbor.edgeLabel}`))]
        return { ...current, [key]: { ...existing, loading: false, extraDownstream: merged,
                                      extrasCleared: false,
                                      downstreamCursor: page.next_cursor,
                                      downstreamHasMore: page.has_more } }
      })
    } catch (error) {
      if (!requestValid(`${key}#more`, token, generation) || isAbort(error)) return
      if (isAccessDenial(error)) {
        onAccessDenied()
        return
      }
      patchNode(key, { loading: false, error })
    }
  }, [beginRequest, requestValid, patchNode, addNeighbors, onAccessDenied])

  // Authorization/strategy boundary: drop every protected node and invalidate
  // all in-flight requests via the generation bump + aborts (no epoch clashes).
  const entryFocusRef = useRef(entryFocus)
  entryFocusRef.current = entryFocus

  useEffect(() => {
    generationRef.current += 1
    tokens.current.clear()
    for (const controller of controllers.current.values()) controller.abort()
    controllers.current.clear()
    setNodes({})
    setOrder([])
    // Rebuild from the current entry focus: an authorization reset with an
    // unchanged URL object must still produce a fresh graph, not an empty one.
    setFocusStack(entryFocusRef.current ? [entryFocusRef.current] : [])
    setRootError(null)
    setRefreshFailed(false)
    setQuery("")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entryKey])

  // Focus the URL-targeted object when it is new to the graph (e.g. arriving
  // from the ontology map).  Node clicks only select; they never re-root.
  useEffect(() => {
    if (!entryFocus) return
    setFocusStack((current) => {
      const top = current[current.length - 1]
      if (top && top.objectId === entryFocus.objectId
          && (!entryFocus.revisionId || top.revisionId === entryFocus.revisionId)) return current
      if (entryFocus.revisionId
          && nodesRef.current[nodeKey(entryFocus.objectId, entryFocus.revisionId)]) return current
      return [...current, entryFocus]
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entryFocus?.objectId, entryFocus?.revisionId])

  // Resolve and auto-expand the current root (top of the focus stack).
  const root = focusStack[focusStack.length - 1] ?? null
  const [rootRetrySeq, setRootRetrySeq] = useState(0)
  useEffect(() => {
    if (!root) return
    let cancelled = false
    const resolve = async () => {
      if (!root.revisionId) {
        // The revision was not pinned by the caller: read the effective version
        // once and anchor the graph at that exact revision.
        const { token, generation, signal } = beginRequest("root#resolve")
        try {
          const detail = await fetchDetail(root.objectId, null, strategyId, signal)
          if (cancelled || !requestValid("root#resolve", token, generation)) return
          const resolved = { ...root, revisionId: detail.selected_revision.revision_id }
          setFocusStack((current) => current.map((entry, index) =>
            index === current.length - 1 && entry.objectId === root.objectId && !entry.revisionId
              ? resolved : entry))
        } catch (error) {
          if (cancelled || !requestValid("root#resolve", token, generation) || isAbort(error)) return
          if (isAccessDenial(error)) {
            onAccessDenied()
            return
          }
          setRootError(error)
        }
        return
      }
      const key = nodeKey(root.objectId, root.revisionId)
      if (!nodesRef.current[key]) {
        // The focus label is only a placeholder until the first detail read
        // fills in the real type/title — never overwrite known metadata.
        upsertNode({ key, objectId: root.objectId, revisionId: root.revisionId,
                     objectType: "", title: root.label, formal: null, formalStatus: null,
                     rag: null, rules: null, rulesUnknown: false, depth: 0 })
      }
      const state = nodesRef.current[key]
      if (state && !state.expanded && !state.loading && !state.autoTried) expand(key)
      // If the node has not committed yet, the next nodes-driven run expands.
    }
    void resolve()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root?.objectId, root?.revisionId, nodes, rootRetrySeq])

  const retryRoot = useCallback(() => {
    setRootError(null)
    const key = root?.revisionId ? nodeKey(root.objectId, root.revisionId) : null
    if (key) {
      patchNode(key, { autoTried: false, error: null })
      expand(key, true)
    } else {
      setRootRetrySeq((value) => value + 1) // re-run the resolve effect
    }
  }, [root, expand, patchNode])

  // Foreground refresh: while the view is visible, re-read every expanded node
  // at its pinned exact revision so statuses/relations stay current; failures
  // keep the last good graph and are announced, never silently emptied.
  const refreshExpanded = useCallback(async () => {
    const expanded = order.filter((key) => nodesRef.current[key]?.expanded
                                   && !nodesRef.current[key]?.loading)
    if (expanded.length === 0) return
    const generation = generationRef.current
    let failed = false
    for (const key of expanded) {
      const node = nodesRef.current[key]
      if (!node) continue
      const { token, signal } = beginRequest(`${key}#refresh`)
      try {
        const detail = await fetchDetail(node.objectId, node.revisionId, strategyId, signal)
        if (!requestValid(`${key}#refresh`, token, generation)) return
        const neighbors = detailNeighbors(detail)
        addNeighbors(node, neighbors)
        applyDetail(node, detail, neighbors)
      } catch (error) {
        if (!requestValid(`${key}#refresh`, token, generation) || isAbort(error)) return
        if (isAccessDenial(error)) {
          onAccessDenied()
          return
        }
        failed = true
      }
    }
    if (generationRef.current === generation) setRefreshFailed(failed)
  }, [order, strategyId, beginRequest, requestValid, addNeighbors, applyDetail, onAccessDenied])

  useEffect(() => {
    if (!active) return
    const tick = () => {
      if (document.visibilityState === "visible") void refreshExpanded()
    }
    const interval = window.setInterval(tick, 5000)
    window.addEventListener("focus", tick)
    document.addEventListener("visibilitychange", tick)
    return () => {
      window.clearInterval(interval)
      window.removeEventListener("focus", tick)
      document.removeEventListener("visibilitychange", tick)
    }
  }, [active, refreshExpanded])

  const focusNode = useCallback((key: string) => {
    const node = nodesRef.current[key]
    if (!node) return
    pendingFocus.current = key
    setFocusStack((current) => [...current, { objectId: node.objectId, revisionId: node.revisionId,
                                              label: node.title ?? node.objectId }])
  }, [])

  const goBack = useCallback(() => {
    setFocusStack((current) => {
      const previous = current[current.length - 2]
      if (previous?.revisionId) pendingFocus.current = nodeKey(previous.objectId, previous.revisionId)
      return current.length > 1 ? current.slice(0, -1) : current
    })
  }, [])

  const visible = useMemo(() => {
    const edges: Array<{ from: string; to: string; label: string; kind: Neighbor["kind"] }> = []
    const hiddenByNode = new Map<string, number>()
    const included = new Set<string>()
    for (const key of order) {
      const node = nodes[key]
      if (!node) continue
      if (focusStack.some((entry) => entry.revisionId
          && nodeKey(entry.objectId, entry.revisionId) === key)) included.add(key)
      for (const neighbor of [...(node.neighbors ?? []), ...node.extraDownstream]) {
        // Recorded basis/source links are business-necessary and never hidden;
        // only candidate-stage downstream neighbors fold behind the expander.
        if (!showNonFormal && neighbor.kind === "downstream" && neighbor.formal === false
            && CANDIDATE_STATUSES.has(neighbor.formalStatus ?? "")) {
          hiddenByNode.set(key, (hiddenByNode.get(key) ?? 0) + 1)
          continue
        }
        edges.push(neighbor.direction === "out"
          ? { from: key, to: neighbor.key, label: neighbor.edgeLabel, kind: neighbor.kind }
          : { from: neighbor.key, to: key, label: neighbor.edgeLabel, kind: neighbor.kind })
        included.add(key)
        included.add(neighbor.key)
      }
      if (node.expanded) included.add(key)
    }
    return { edges, hiddenByNode, included }
  }, [nodes, order, showNonFormal, focusStack])

  const layout = useMemo(() => {
    const byDepth = new Map<number, string[]>()
    for (const key of order) {
      if (!visible.included.has(key)) continue
      const node = nodes[key]
      if (!node) continue
      const list = byDepth.get(node.depth) ?? []
      list.push(key)
      byDepth.set(node.depth, list)
    }
    const depths = [...byDepth.keys()].sort((a, b) => a - b)
    const minDepth = depths[0] ?? 0
    const positions = new Map<string, { x: number; y: number }>()
    let maxBottom = 0
    for (const depth of depths) {
      const list = byDepth.get(depth) ?? []
      let cursorY = PAD
      for (const key of list) {
        const node = nodes[key]
        positions.set(key, { x: PAD + (depth - minDepth) * (NODE_W + GAP_X), y: cursorY })
        cursorY += nodeHeight(node, visible.hiddenByNode.get(key) ?? 0) + GAP_Y
        maxBottom = Math.max(maxBottom, cursorY)
      }
    }
    const edges: CanvasEdge[] = visible.edges.flatMap((edge, index) => {
      const from = positions.get(edge.from)
      const to = positions.get(edge.to)
      if (!from || !to) return []
      return [{ id: `edge-${index}`, x1: from.x + NODE_W, y1: from.y + NODE_H / 2,
                x2: to.x, y2: to.y + NODE_H / 2, label: edge.label,
                dashed: edge.kind !== "downstream" }]
    })
    return { positions, edges,
             width: PAD * 2 + (depths.length ? (depths.length - 1) * (NODE_W + GAP_X) + NODE_W : NODE_W),
             height: maxBottom + PAD }
  }, [nodes, order, visible])

  useEffect(() => {
    const key = pendingFocus.current
    const position = key ? layout.positions.get(key) : null
    if (!position) return
    pendingFocus.current = null
    setFocusPoint({ x: position.x + NODE_W / 2, y: position.y + NODE_H / 2,
      seq: ++focusSeq.current })
  })

  if (!root) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center"
           data-testid="graph-empty-root">
        <p className="max-w-sm text-[12.5px] text-muted-foreground">
          请先在顶部选择一个战略，或从本体地图的「查看实际数据」进入具体对象的关系图。
        </p>
      </div>
    )
  }

  const rootKey = root.revisionId ? nodeKey(root.objectId, root.revisionId) : null
  const matches = query.trim()
    ? order.filter((key) => visible.included.has(key)).filter((key) => {
        const node = nodes[key]
        const text = `${node?.title ?? ""}${node?.objectType ?? ""}${TYPE_LABELS[node?.objectType ?? ""] ?? ""}`
        return text.toLowerCase().includes(query.trim().toLowerCase())
      })
    : []
  const matchSet = new Set(matches)
  const locate = (key: string) => {
    const position = layout.positions.get(key)
    if (position) {
      setFocusPoint({ x: position.x + NODE_W / 2, y: position.y + NODE_H / 2,
                      seq: ++focusSeq.current })
    }
    nodeRefs.current.get(key)?.querySelector("button")?.focus()
  }
  const nodeLabel = (key: string) => {
    const node = nodes[key]
    return node ? `${TYPE_LABELS[node.objectType] ?? node.objectType ?? "对象"}：${node.title ?? node.objectId}`
      : key
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="business-graph">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card px-3 py-2">
        <div className="flex items-center gap-1 text-[12px]">
          <Button size="xs" variant="outline" onClick={goBack} disabled={focusStack.length <= 1}
                  data-testid="graph-back">返回上一级</Button>
          <span className="text-muted-foreground" data-testid="graph-path">
            {focusStack.map((entry) => {
              const key = entry.revisionId ? nodeKey(entry.objectId, entry.revisionId) : null
              return (key && nodes[key]?.title) || entry.label
            }).join(" → ")}
          </span>
        </div>
        <Input value={query} onChange={(event) => setQuery(event.target.value)}
               placeholder="搜索图中对象…" className="h-7 w-44" data-testid="graph-search"
               aria-label="搜索图中对象" />
        {query.trim() ? (
          <div className="flex flex-wrap items-center gap-1" data-testid="graph-search-matches">
            {matches.length === 0
              ? <span className="text-[11px] text-muted-foreground">图中无匹配对象</span>
              : matches.map((key) => (
                <button key={key} type="button"
                        className="rounded border border-border bg-muted/40 px-1.5 py-0.5 text-[11px] hover:bg-muted"
                        onClick={() => locate(key)}>
                  {nodes[key]?.title ?? nodes[key]?.objectId}
                </button>
              ))}
          </div>
        ) : null}
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <input type="checkbox" checked={showNonFormal}
                 onChange={(event) => setShowNonFormal(event.target.checked)}
                 data-testid="toggle-non-formal" />
          显示候选/历史下游邻接
        </label>
        <span className="ml-auto text-[10.5px] text-muted-foreground">
          点击对象名查看详情；「聚焦」定位对象并展开关联
        </span>
      </div>
      {refreshFailed ? (
        <div className="border-b border-amber-300 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-900"
             data-testid="graph-stale">
          网络读取失败，图中显示的是上次成功读取的内容，不代表已刷新。
        </div>
      ) : null}
      {rootError ? (
        <div className="flex items-center gap-2 border-b border-border bg-red-50 px-3 py-1.5 text-[11px] text-destructive"
             data-testid="graph-root-error">
          <span>{errorLabel(rootError)}；关系图未能建立。</span>
          <Button size="xs" variant="outline" onClick={retryRoot}>重试</Button>
        </div>
      ) : null}
      <div className="min-h-0 flex-1 space-y-2 overflow-auto p-3 lg:hidden" data-testid="graph-mobile-list">
        {order.filter((key) => visible.included.has(key)).map((key) => {
          const node = nodes[key]
          return <article key={key} className="rounded-md border bg-card p-3">
            <p className="text-xs text-muted-foreground">{TYPE_LABELS[node.objectType] ?? "对象"} · {node.formalStatus ? formalBusinessText(node.objectType, node.formalStatus, node.formal === true) : "效力待读取"}</p>
            <button type="button" className="my-1 block text-left text-sm font-medium text-primary" onClick={() => onOpenObject(node.objectId, node.revisionId)}>{node.title ?? "未记录标题"}</button>
            <p className="mb-2 text-xs text-muted-foreground">版本 {shortId(node.revisionId)}{node.basisHistorical ? " · 沿用历史依据" : ""}</p>
            <Button size="xs" variant="outline" disabled={node.loading || node.expanded} onClick={() => expand(key, true)}>{node.loading ? "展开中…" : node.expanded ? "已展开" : "展开关联"}</Button>
            {node.downstreamHasMore ? <Button size="xs" variant="outline" onClick={() => void loadMoreDownstream(key)}>继续加载关联</Button> : null}
            {node.error ? <p className="mt-1 text-xs text-destructive">{errorLabel(node.error)}</p> : null}
          </article>
        })}
      </div>
      <div className="hidden min-h-0 flex-1 lg:block">
        <PanZoomCanvas width={layout.width} height={layout.height} edges={layout.edges}
                       focusPoint={focusPoint}
                       emptyHint="实线=下一层；虚线=依据/来源。箭头指向被引用/承接方向。"
                       testId="graph-canvas">
          {order.map((key) => {
            if (!visible.included.has(key)) return null
            const node = nodes[key]
            const position = layout.positions.get(key)
            if (!node || !position) return null
            const selected = selectedObject === node.objectId
              && (!selectedRevision || selectedRevision === node.revisionId)
            const hidden = visible.hiddenByNode.get(key) ?? 0
            return (
              <div key={key} ref={(element) => {
                     if (element) nodeRefs.current.set(key, element)
                     else nodeRefs.current.delete(key)
                   }}
                   className="absolute" style={{ left: position.x, top: position.y }}
                   data-testid={`graph-node-${node.objectId}-${node.revisionId}`}>
                <div className={cn(
                  "flex flex-col rounded-md border bg-background p-2 shadow-sm",
                  selected ? "border-primary ring-2 ring-primary/30"
                    : matchSet.has(key) ? "border-sky-400 ring-2 ring-sky-200"
                    : node.formal ? "border-emerald-300"
                    : node.formalStatus && HISTORICAL_STATUSES.has(node.formalStatus)
                      ? "border-amber-300" : "border-border",
                )} style={{ width: NODE_W, height: NODE_H }}>
                  <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                    <span className="text-[10px] text-muted-foreground">
                      {TYPE_LABELS[node.objectType] ?? (node.objectType || "对象")}
                    </span>
                    {node.formalStatus ? (
                      <Badge variant="outline"
                             className={cn("rounded-sm px-1 text-[9.5px]",
                               node.formal
                                 ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                                 : "border-border bg-muted text-muted-foreground")}>
                        {formalBusinessText(node.objectType, node.formalStatus, node.formal === true)}
                      </Badge>
                    ) : null}
                    {node.rag ? (
                      <Badge variant="outline" className="rounded-sm px-1 text-[9.5px]">
                        {RAG_LABELS[node.rag] ?? "状态未知"}
                      </Badge>
                    ) : null}
                    {node.rules ? (
                      <span className="text-[9.5px] text-muted-foreground">规则 {node.rules}</span>
                    ) : node.rulesUnknown ? (
                      <span className="text-[9.5px] text-amber-800">规则版本未识别</span>
                    ) : null}
                  </div>
                  <button type="button"
                          className="mt-1 shrink-0 truncate text-left text-[12px] font-medium text-primary underline-offset-2 hover:underline"
                          onClick={() => onOpenObject(node.objectId, node.revisionId)}
                          data-testid={`graph-open-${node.objectId}-${node.revisionId}`}>
                    {node.title ?? "（未记录标题）"}
                  </button>
                  <div className="mt-0.5 shrink-0 text-[9.5px] text-muted-foreground">
                    版本 {shortId(node.revisionId)}{node.basisHistorical ? " · 沿用历史依据" : ""}
                  </div>
                  <div className="mt-auto flex items-center gap-1 pt-1">
                    <Button size="xs" variant="outline" disabled={node.expanded || node.loading}
                            onClick={() => expand(key, true)}
                            data-testid={`graph-expand-${node.objectId}-${node.revisionId}`}>
                      {node.loading ? "展开中…" : node.expanded ? "已展开" : "展开"}
                    </Button>
                    <Button size="xs" variant="ghost" onClick={() => focusNode(key)}
                            disabled={rootKey === key}
                            data-testid={`graph-focus-${node.objectId}-${node.revisionId}`}>
                      聚焦
                    </Button>
                  </div>
                </div>
                {node.expanded ? (
                  <div className="mt-1 space-y-0.5">
                    {node.downstreamHasMore ? (
                      <div className="flex items-center gap-1">
                        <span className="rounded bg-amber-50 px-1 py-0.5 text-[9.5px] text-amber-900"
                              data-testid={`graph-partial-${node.objectId}-${node.revisionId}`}>
                          下游部分加载
                        </span>
                        <Button size="xs" variant="outline" disabled={node.loading}
                                onClick={() => void loadMoreDownstream(key)}
                                data-testid={`graph-more-${node.objectId}-${node.revisionId}`}>
                          继续加载
                        </Button>
                      </div>
                    ) : null}
                    {hidden > 0 ? (
                      <button type="button"
                              className="rounded bg-muted px-1 py-0.5 text-[9.5px] text-muted-foreground underline-offset-2 hover:underline"
                              data-testid={`graph-hidden-${node.objectId}-${node.revisionId}`}
                              onClick={() => setShowNonFormal(true)}>
                        收拢 {hidden} 条候选/历史下游，点击查看
                      </button>
                    ) : null}
                    {(node.neighbors?.length ?? 0) + node.extraDownstream.length === 0 ? (
                      <span className="inline-block rounded bg-muted px-1 py-0.5 text-[9.5px] text-muted-foreground">
                        未读到记录在案的关联引用
                      </span>
                    ) : null}
                    {node.extrasCleared ? (
                      <span className="inline-block rounded bg-amber-50 px-1 py-0.5 text-[9.5px] text-amber-900"
                            data-testid={`graph-extras-cleared-${node.objectId}-${node.revisionId}`}>
                        分页续读已随刷新清除，可用「继续加载」重新读取
                      </span>
                    ) : null}
                  </div>
                ) : null}
                {node.error ? (
                  <div className="mt-1 flex items-center gap-1">
                    <span className="rounded bg-red-50 px-1 py-0.5 text-[9.5px] text-destructive"
                          data-testid={`graph-error-${node.objectId}-${node.revisionId}`}>
                      {errorLabel(node.error)}
                    </span>
                    <Button size="xs" variant="outline" disabled={node.loading}
                            onClick={() => expand(key, true)}
                            data-testid={`graph-retry-${node.objectId}-${node.revisionId}`}>
                      重试
                    </Button>
                  </div>
                ) : null}
              </div>
            )
          })}
        </PanZoomCanvas>
      </div>
      {visible.edges.length > 0 ? (
        <Collapsible open={edgeListOpen} onOpenChange={setEdgeListOpen}>
          <div className="border-t border-border bg-card">
            <CollapsibleTrigger asChild>
              <button type="button" data-testid="edge-list-toggle"
                      className="flex w-full items-center justify-between px-3 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-muted/40">
                <span>连线明细（{visible.edges.length} 条精确引用）</span>
                <span>{edgeListOpen ? "收起" : "展开"}</span>
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ul className="max-h-40 space-y-0.5 overflow-auto px-3 pb-2" data-testid="edge-list">
                {visible.edges.map((edge, index) => (
                  <li key={index} className="flex flex-wrap items-center gap-1 text-[11px]">
                    <span>{nodeLabel(edge.from)}</span>
                    <span className="text-muted-foreground">—{edge.label}→</span>
                    <button type="button"
                            className="text-primary underline-offset-2 hover:underline"
                            onClick={() => locate(edge.to)}>
                      {nodeLabel(edge.to)}
                    </button>
                  </li>
                ))}
              </ul>
            </CollapsibleContent>
          </div>
        </Collapsible>
      ) : null}
    </div>
  )
}
