import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ArrowUpRight, RefreshCw } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { fetchDownstream, fetchObjects } from "@/lib/api"
import { ApiError, errorLabel, isAbort } from "@/lib/errors"
import { formatPeriod } from "@/lib/format"
import type { ObjectListItem } from "@/lib/types"

/**
 * Business mainline: Strategy → LTCO → PCO → Mission, cascaded on *exact*
 * revisions.
 *
 * Two server-side mechanisms carry the cascade; neither is re-implemented here:
 *
 *  - A Strategy selection becomes `strategy_id` + `basis=current`, and the
 *    Runtime resolves each object's strategy basis through its recorded refs
 *    (Mission → pco_ref → PCO → strategy_ref), comparing object *and* revision.
 *    That narrows all three downstream lanes transitively in one read each.
 *  - An LTCO or PCO selection becomes `/objects/{id}/downstream?revision_id=`,
 *    which only returns children whose recorded `payload_hash` names that exact
 *    revision.  The lane keeps the items that edge set names.
 *
 * `Mission` records no `ltco_ref`/`strategy_ref`, so an LTCO selection cannot
 * narrow the Mission lane in one read.  The lane says so instead of fanning out
 * one request per PCO.
 */

export type LaneKey = "strategy" | "ltco" | "pco" | "mission"
type Status = "formal" | "candidate" | "historical"
type Selection = { objectId: string; revisionId: string; title: string }

interface Lane {
  key: LaneKey
  group: string
  name: string
  en: string
  /** Company-level objects are not scoped by a responsibility domain. */
  company?: boolean
}

const LANES: Lane[] = [
  { key: "strategy", group: "strategy", name: "战略", en: "Strategy", company: true },
  { key: "ltco", group: "ltco", name: "长期目标", en: "LTCO" },
  { key: "pco", group: "pco", name: "阶段目标", en: "PCO" },
  { key: "mission", group: "mission", name: "任务", en: "Mission" },
]

const STATUS_LABEL: Record<Status, string> = {
  formal: "正式",
  candidate: "候选",
  historical: "历史依据",
}

const STATUS_NOTE: Record<Status, string> = {
  formal: "承接所选战略、且当前具备业务效力的内容。",
  candidate: "承接所选战略、但尚未确认生效的内容。",
  historical: "挂接在其它（含已被取代的）战略版本上的内容。",
}

/** Exactly the two statuses that read against the selected strategy. */
const CURRENT_BASIS: Status[] = ["formal", "candidate"]

/** Pages of downstream edges read per selection before the lane says it stopped. */
const EDGE_PAGE_LIMIT = 8

const ALL = "__all__"

const refKey = (objectId: string, revisionId: string) => `${objectId}@${revisionId}`

interface LaneState {
  items: ObjectListItem[]
  loading: boolean
  error: string | null
  hasMore: boolean
  /** Set when the read found no readable strategy at all. */
  noStrategy: boolean
}

const EMPTY_LANE: LaneState = { items: [], loading: true, error: null, hasMore: false, noStrategy: false }

interface EdgeSet {
  keys: Set<string>
  /** True when more edges existed than this view read. */
  stopped: boolean
}

/** All exact child refs of one parent revision, followed to the end of the cursor. */
async function childRefs(parent: Selection, childType: string, signal: AbortSignal): Promise<EdgeSet> {
  const keys = new Set<string>()
  let cursor: string | null = null
  let pages = 0
  for (;;) {
    const page = await fetchDownstream(parent.objectId, parent.revisionId, cursor, signal)
    for (const edge of page.items) {
      if (edge.object_type === childType) keys.add(refKey(edge.object_id, edge.ref.revision_id))
    }
    cursor = page.next_cursor
    pages += 1
    if (!cursor) return { keys, stopped: false }
    if (pages >= EDGE_PAGE_LIMIT) return { keys, stopped: true }
  }
}

function domainOptions(lanes: Record<LaneKey, LaneState>): Array<{ value: string; label: string }> {
  const seen = new Map<string, string>()
  for (const lane of LANES) {
    if (lane.company) continue
    for (const item of lanes[lane.key].items) {
      if (!seen.has(item.domain_id)) {
        seen.set(item.domain_id, item.domain_name ?? item.domain_id.slice(0, 8))
      }
    }
  }
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
}

interface MainlineProps {
  /** Open one object at the exact revision the card names. */
  onOpen: (objectId: string, revisionId: string) => void
  onAuthLost: () => void
}

export function BusinessMainline({ onOpen, onAuthLost }: MainlineProps) {
  const [status, setStatus] = useState<Status>("formal")
  const [domain, setDomain] = useState<string | null>(null)
  const [strategy, setStrategy] = useState<Selection | null>(null)
  const [ltco, setLtco] = useState<Selection | null>(null)
  const [pco, setPco] = useState<Selection | null>(null)
  const [reload, setReload] = useState(0)
  const [lanes, setLanes] = useState<Record<LaneKey, LaneState>>(
    () => ({ strategy: EMPTY_LANE, ltco: EMPTY_LANE, pco: EMPTY_LANE, mission: EMPTY_LANE }))
  const [pcoEdges, setPcoEdges] = useState<EdgeSet | null>(null)
  const [missionEdges, setMissionEdges] = useState<EdgeSet | null>(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  // Same rule the shell uses: only a lost identity ends the session.  A 404 on
  // one lane read is that lane's business result, not a reason to sign out.
  const fail = useCallback((error: unknown): string | null => {
    if (isAbort(error)) return null
    if (error instanceof ApiError && [401, 403].includes(error.status)) { onAuthLost(); return null }
    return errorLabel(error)
  }, [onAuthLost])

  // Lane contents.  The Strategy lane is the picker for every other lane, so it
  // always reads `basis=all`: narrowing it by its own selection would leave one
  // card and no way back.
  useEffect(() => {
    const controller = new AbortController()
    setLanes((current) => {
      const next = { ...current }
      for (const lane of LANES) next[lane.key] = { ...current[lane.key], loading: true, error: null }
      return next
    })
    for (const lane of LANES) {
      const basis = lane.company ? "all" : CURRENT_BASIS.includes(status) ? "current" : "historical"
      void fetchObjects({
        group: lane.group,
        basis,
        strategyId: strategy?.objectId ?? null,
        domainId: lane.company ? null : domain,
        limit: 25,
      }, controller.signal).then((page) => {
        if (!mounted.current || controller.signal.aborted) return
        setLanes((current) => ({ ...current, [lane.key]: {
          items: page.items, loading: false, error: null, hasMore: page.has_more,
          noStrategy: page.hint === "no_current_strategy_is_readable",
        } }))
      }).catch((error: unknown) => {
        if (!mounted.current || controller.signal.aborted) return
        const message = fail(error)
        setLanes((current) => ({ ...current, [lane.key]: {
          ...current[lane.key], loading: false, error: message,
        } }))
      })
    }
    return () => controller.abort()
  }, [status, domain, strategy, reload, fail])

  // Exact-revision edges for the two lanes the server cannot narrow by a query
  // filter.  Both effects clear their edge set the moment the parent changes, so
  // a lane never shows a previous parent's children while the read is in flight.
  useEffect(() => {
    setPcoEdges(null)
    if (!ltco) return
    const controller = new AbortController()
    void childRefs(ltco, "PCO", controller.signal).then((edges) => {
      if (mounted.current && !controller.signal.aborted) setPcoEdges(edges)
    }).catch((error: unknown) => { if (mounted.current) fail(error) })
    return () => controller.abort()
  }, [ltco, reload, fail])

  useEffect(() => {
    setMissionEdges(null)
    if (!pco) return
    const controller = new AbortController()
    void childRefs(pco, "Mission", controller.signal).then((edges) => {
      if (mounted.current && !controller.signal.aborted) setMissionEdges(edges)
    }).catch((error: unknown) => { if (mounted.current) fail(error) })
    return () => controller.abort()
  }, [pco, reload, fail])

  const select = (lane: LaneKey, item: ObjectListItem) => {
    const next: Selection = {
      objectId: item.object_id,
      revisionId: item.basis_revision_id,
      title: item.title ?? "未命名对象",
    }
    const same = (current: Selection | null) =>
      current?.objectId === next.objectId && current?.revisionId === next.revisionId
    // Changing an upstream selection drops every downstream one: a kept child
    // would otherwise sit under a parent it no longer descends from.
    if (lane === "strategy") { setStrategy(same(strategy) ? null : next); setLtco(null); setPco(null) }
    if (lane === "ltco") { setLtco(same(ltco) ? null : next); setPco(null) }
    if (lane === "pco") setPco(same(pco) ? null : next)
  }

  const selectionOf = (lane: LaneKey): Selection | null =>
    lane === "strategy" ? strategy : lane === "ltco" ? ltco : lane === "pco" ? pco : null

  const visible = useCallback((lane: Lane): ObjectListItem[] => {
    const items = lanes[lane.key].items
    // The Strategy lane is a picker over every readable strategy; the status
    // segment applies to the lanes that read against the selected one.
    if (lane.company) return items
    const byStatus = status === "historical"
      ? items
      : items.filter((item) => item.formal_state.formal === (status === "formal"))
    const edges = lane.key === "pco" ? (ltco ? pcoEdges : null)
      : lane.key === "mission" ? (pco ? missionEdges : null) : null
    if (!edges) return byStatus
    return byStatus.filter((item) => edges.keys.has(refKey(item.object_id, item.basis_revision_id)))
  }, [lanes, status, ltco, pco, pcoEdges, missionEdges])

  const pending = (lane: Lane): boolean =>
    lanes[lane.key].loading
    || (lane.key === "pco" && !!ltco && pcoEdges === null)
    || (lane.key === "mission" && !!pco && missionEdges === null)

  /** What narrowed this lane, in the user's terms — or what would. */
  const hint = (lane: Lane): { text: string; kind: "exact" | "advice" } | null => {
    if (lane.key === "ltco" && strategy) return { text: `已按战略「${strategy.title}」的精确版本筛选`, kind: "exact" }
    if (lane.key === "pco") {
      if (ltco) return { text: `已按长期目标「${ltco.title}」的精确版本筛选`, kind: "exact" }
      if (strategy) return { text: `已按战略「${strategy.title}」的精确版本筛选`, kind: "exact" }
    }
    if (lane.key === "mission") {
      if (pco) return { text: `已按阶段目标「${pco.title}」的精确版本筛选`, kind: "exact" }
      if (ltco) return { text: "任务只记录所属阶段目标；选择一个阶段目标可继续收窄", kind: "advice" }
      if (strategy) return { text: `已按战略「${strategy.title}」的精确版本筛选`, kind: "exact" }
    }
    return null
  }

  const stopped = (lane: Lane): boolean =>
    (lane.key === "pco" && !!pcoEdges?.stopped) || (lane.key === "mission" && !!missionEdges?.stopped)

  const [domains, setDomains] = useState<Array<{ value: string; label: string }>>([])
  // Domain options come from the unfiltered reads only.  Deriving them from a
  // filtered read would leave the selected domain as the only option.
  const unfiltered = useMemo(() => domainOptions(lanes), [lanes])
  useEffect(() => { if (!domain) setDomains(unfiltered) }, [domain, unfiltered])
  const noStrategy = LANES.some((lane) => lanes[lane.key].noStrategy)

  return (
    <div className="gov-mainline" data-testid="business-mainline">
      <div className="gov-page-heading">
        <div>
          <p className="gov-eyebrow">业务主线 / STRATEGY → MISSION</p>
          <h2>业务主线：战略 → 长期目标 → 阶段目标 → 任务</h2>
          <p className="gov-page-description">
            选择任意一栏的卡片，其下游按该卡片的<strong>精确版本</strong>筛选；再次选择取消。
            某栏没有对应内容时如实显示「暂无」，不以其它状态填补。所有内容按你当前的权限展示。
          </p>
        </div>
        <Button variant="outline" onClick={() => setReload((value) => value + 1)}>
          <RefreshCw size={15}/>刷新
        </Button>
      </div>

      <div className="gov-mainline-controls">
        <div>
          <span className="gov-control-label" id="mainline-status">版本状态</span>
          <div className="gov-seg" role="group" aria-labelledby="mainline-status">
            {(Object.keys(STATUS_LABEL) as Status[]).map((value) => (
              <button key={value} type="button" aria-pressed={status === value}
                      className={status === value ? "on" : undefined}
                      onClick={() => setStatus(value)}>{STATUS_LABEL[value]}</button>
            ))}
          </div>
        </div>
        <div>
          <Label className="gov-control-label" htmlFor="mainline-domain">责任域筛选</Label>
          <Select value={domain ?? ALL}
                  onValueChange={(value) => setDomain(value === ALL ? null : value)}>
            <SelectTrigger size="sm" className="w-44" id="mainline-domain" data-testid="mainline-domain">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>全部责任域</SelectItem>
              {domains.map((option) => (
                <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <p className="gov-mainline-note">
          {STATUS_NOTE[status]}战略栏始终列出你有权查看的全部战略，状态标在卡片上。
        </p>
      </div>

      {noStrategy && (
        <Alert>
          <AlertDescription>
            没有你有权查看的当前战略，因此下游各栏为空。这不代表系统中没有内容。
          </AlertDescription>
        </Alert>
      )}

      <div className="gov-lane-board">
        {LANES.map((lane) => {
          const state = lanes[lane.key]
          const items = visible(lane)
          const selected = selectionOf(lane.key)
          const note = hint(lane)
          return (
            <section key={lane.key} className="gov-lane" aria-label={`${lane.name} ${lane.en}`}>
              <h3>{lane.name} <span className="gov-lane-en">{lane.en}</span></h3>
              {note && <p className={note.kind === "exact" ? "gov-lane-hint" : "gov-lane-advice"}>{note.text}</p>}
              {stopped(lane) && (
                <p className="gov-lane-advice">下游引用较多，这里只读取了前 {EDGE_PAGE_LIMIT} 页；请选择更靠下的一层继续收窄。</p>
              )}
              {state.error && <p className="gov-lane-advice">{state.error}</p>}
              {pending(lane) ? <p className="gov-lane-empty">正在读取…</p>
                : items.length === 0 ? (
                  <p className="gov-lane-empty">
                    暂无{lane.company ? "可读战略" : `${STATUS_LABEL[status]}对象`}。
                  </p>
                ) : items.map((item) => {
                  const active = selected?.objectId === item.object_id
                    && selected?.revisionId === item.basis_revision_id
                  const label = item.title ?? "未命名对象"
                  const leaf = lane.key === "mission"
                  return (
                    <article key={`${item.object_id}@${item.basis_revision_id}`}
                             className="gov-lane-card" data-active={active || undefined}>
                      <button type="button" className="gov-lane-card-main"
                              data-testid={`lane-${lane.key}-${item.object_id}`}
                              aria-pressed={leaf ? undefined : active}
                              onClick={() => leaf ? onOpen(item.object_id, item.basis_revision_id)
                                                  : select(lane.key, item)}>
                        <strong>{label}</strong>
                        <span className="gov-lane-meta">
                          {item.formal_state.formal ? "正式" : STATUS_LABEL.candidate}
                          {" · v"}{item.object_version}
                          {item.period && ` · ${formatPeriod(item.period)}`}
                          {!lane.company && item.domain_name && ` · ${item.domain_name}`}
                        </span>
                      </button>
                      {!leaf && (
                        <button type="button" className="gov-lane-card-open"
                                data-testid={`open-${item.object_id}`}
                                aria-label={`打开「${label}」的详情`}
                                onClick={() => onOpen(item.object_id, item.basis_revision_id)}>
                          <ArrowUpRight size={15}/>
                        </button>
                      )}
                    </article>
                  )
                })}
              {state.hasMore && !pending(lane) && (
                <p className="gov-lane-advice">本栏还有更多内容；请用上游选择或责任域收窄。</p>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
