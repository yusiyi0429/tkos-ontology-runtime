import { useEffect, useMemo, useRef, useState } from "react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { PanZoomCanvas, type CanvasEdge, type CanvasFocusPoint } from "@/components/PanZoomCanvas"
import { errorLabel } from "@/lib/errors"
import { RULES_VERSION_LABELS, TYPE_LABELS } from "@/lib/labels"
import { MARKER_LABELS, areasFor, mapEdges, rulesOfContractVersion, typeInfo,
         undocumentedTypes, type RulesVersion } from "@/lib/ontology"
import type { OntologyCatalog } from "@/lib/types"
import { cn } from "@/lib/utils"

const NODE_W = 184
const NODE_H = 58
const GAP_X = 20
const GAP_Y = 16
const AREA_PAD = 14
const AREA_HEADER = 66

interface NodePosition { x: number; y: number }

interface AreaBlock {
  id: string
  name: string
  hint: string
  types: string[]
  undocumented?: boolean
}

interface MapLayout {
  positions: Map<string, NodePosition>
  areaOf: Map<string, string>
  areaBoxes: Array<{ id: string; name: string; hint: string; types: string[]; typeCount: number; open: boolean;
                     x: number; y: number; width: number; height: number }>
  edges: CanvasEdge[]
  width: number
  height: number
}

function blocksFor(rules: RulesVersion, registered: Set<string> | null): AreaBlock[] {
  const areas = areasFor(rules, registered)
  const undocumented = undocumentedTypes(rules, registered)
  return undocumented.length
    ? [...areas, { id: "undocumented", name: "其他已注册类型",
                   hint: "已注册但暂无整理的业务说明，保持可见", types: undocumented,
                   undocumented: true }]
    : areas
}

function computeLayout(rules: RulesVersion, registered: Set<string> | null,
                       openAreas: ReadonlySet<string>): MapLayout {
  const blocks = blocksFor(rules, registered)
  const positions = new Map<string, NodePosition>()
  const areaOf = new Map<string, string>()
  const areaBoxes: MapLayout["areaBoxes"] = []
  // The five business areas form a compact overview; support is alongside the
  // research area, not the next step in a mandatory process.
  const slots: Record<string, [number, number]> = {
    "strategy-structure": [0, 0], objectives: [1, 0],
    operations: [0, 1], "joint-review": [1, 1], research: [0, 2], support: [1, 2],
    undocumented: [0, 3],
  }
  const dimensions = blocks.map((area) => {
    const open = openAreas.has(area.id)
    const cols = Math.min(2, Math.max(1, area.types.length))
    const rows = Math.ceil(area.types.length / cols)
    return { area, open, cols, slot: slots[area.id] ?? [0, 3],
      width: open ? cols * NODE_W + (cols - 1) * GAP_X + AREA_PAD * 2 : 330,
      height: open ? AREA_HEADER + rows * NODE_H + (rows - 1) * GAP_Y + AREA_PAD : 112 }
  })
  const columnWidths = [0, 1].map((column) => Math.max(330,
    ...dimensions.filter((item) => item.slot[0] === column).map((item) => item.width)))
  const rowHeights = [0, 1, 2, 3].map((row) => Math.max(0,
    ...dimensions.filter((item) => item.slot[1] === row).map((item) => item.height)))
  const rowY = [24]
  for (let row = 1; row < rowHeights.length; row++) rowY.push(rowY[row - 1] + rowHeights[row - 1] + 100)
  for (const { area, open, cols, slot, width, height } of dimensions) {
    const x = 32 + (slot[0] === 1 ? columnWidths[0] + 140 : 0)
    const y = rowY[slot[1]]
    areaBoxes.push({ id: area.id, name: area.name, hint: area.hint, types: area.types,
      typeCount: area.types.length, open, x, y, width, height })
    area.types.forEach((type, index) => {
      areaOf.set(type, area.id)
      if (open) positions.set(type, {
        x: x + AREA_PAD + (index % cols) * (NODE_W + GAP_X),
        y: y + AREA_HEADER + Math.floor(index / cols) * (NODE_H + GAP_Y),
      })
    })
  }
  const boxById = new Map(areaBoxes.map((box) => [box.id, box]))
  const edges: CanvasEdge[] = []
  const areaEdges = new Map<string, { labels: string[]; from: string; to: string }>()
  for (const edge of mapEdges(rules, registered)) {
    const from = positions.get(edge.from)
    const to = positions.get(edge.to)
    if (from && to) {
      const rightward = to.x > from.x
      const sameColumn = to.x === from.x
      edges.push({ id: `${edge.from}->${edge.to}:${edge.label}`,
        x1: from.x + (rightward || sameColumn ? NODE_W : 0), y1: from.y + NODE_H / 2,
        x2: to.x + (rightward ? 0 : NODE_W), y2: to.y + NODE_H / 2,
        side: sameColumn ? "right" : undefined,
        label: edge.label,
        labelX: sameColumn ? from.x + NODE_W + 36 : undefined,
      })
      continue
    }
    const fromArea = areaOf.get(edge.from)
    const toArea = areaOf.get(edge.to)
    if (!fromArea || !toArea || fromArea === toArea) continue
    const key = `${fromArea}->${toArea}`
    const bucket = areaEdges.get(key) ?? { labels: [], from: fromArea, to: toArea }
    if (!bucket.labels.includes(edge.label)) bucket.labels.push(edge.label)
    areaEdges.set(key, bucket)
  }
  for (const [key, bucket] of areaEdges) {
    const from = boxById.get(bucket.from)
    const to = boxById.get(bucket.to)
    if (!from || !to) continue
    const sameColumn = from.x === to.x
    const rightward = to.x > from.x
    const side = sameColumn ? (slots[bucket.from][0] === 0 ? "right" : "left") : undefined
    const x1 = from.x + (sameColumn ? (side === "right" ? from.width : 0) : rightward ? from.width : 0)
    const x2 = to.x + (sameColumn ? (side === "right" ? to.width : 0) : rightward ? 0 : to.width)
    const label = bucket.labels[0]
    edges.push({ id: `area:${key}`, x1, y1: from.y + from.height / 2,
      x2, y2: to.y + to.height / 2, label, side, dashed: true,
      labelX: sameColumn ? x1 + (side === "right" ? 52 : -52) : (x1 + x2) / 2,
      labelY: sameColumn ? from.y + from.height / 2 + ((to.y + to.height / 2) - (from.y + from.height / 2)) * 0.3 : undefined,
    })
  }
  return { positions, areaOf, areaBoxes, edges,
    width: columnWidths[0] + columnWidths[1] + 204,
    height: Math.max(...areaBoxes.map((area) => area.y + area.height)) + 28 }
}

export interface OntologyMapProps {
  rules: RulesVersion
  catalog: OntologyCatalog | null
  loading: boolean
  error: unknown
  selectedType: string | null
  onSelectType: (type: string) => void
}

/**
 * 本体地图：首屏展示五个业务区域 + 共同支撑的分区总览（Q2 B：点击区域展开
 * 具体类型）。折叠时概念关系以区域级概览连线呈现；展开后展示类型级关系。
 * 类型目录来自后端 catalog（不按当前数据反推）；无实例的类型保持可见。
 */
export function OntologyMap({ rules, catalog, loading, error, selectedType,
                              onSelectType }: OntologyMapProps) {
  const [openAreas, setOpenAreas] = useState<ReadonlySet<string>>(new Set())
  const [query, setQuery] = useState("")
  const [focusPoint, setFocusPoint] = useState<CanvasFocusPoint | null>(null)
  const focusSeq = useRef(0)
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>())
  const pendingFocus = useRef<string | null>(null)
  // A poll-driven catalog gap (view switch / pending update) must not unmount
  // the canvas and lose pan/zoom; keep rendering the last good directory.
  const lastCatalog = useRef<OntologyCatalog | null>(null)
  if (catalog) lastCatalog.current = catalog
  const effectiveCatalog = catalog ?? lastCatalog.current

  const versionEntry = effectiveCatalog?.versions.find(
    (entry) => rulesOfContractVersion(entry.contract_version) === rules) ?? null
  const registered = versionEntry ? new Set(versionEntry.object_types) : null
  const layout = useMemo(
    () => computeLayout(rules, registered, openAreas),
    [rules, versionEntry, openAreas]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selectedType || !versionEntry?.object_types.includes(selectedType)) return
    const areaId = layout.areaOf.get(selectedType)
    if (!areaId) return
    pendingFocus.current = selectedType
    setOpenAreas((current) => current.has(areaId) ? current : new Set(current).add(areaId))
  }, [selectedType, rules, versionEntry?.contract_version]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const type = pendingFocus.current
    if (!type) return
    const node = nodeRefs.current.get(type)
    const position = layout.positions.get(type)
    if (!node || !position) return
    pendingFocus.current = null
    setFocusPoint({ x: position.x + NODE_W / 2, y: position.y + NODE_H / 2,
      seq: ++focusSeq.current })
    node.focus({ preventScroll: true })
  })

  if (loading && !effectiveCatalog) {
    return <div className="space-y-2 p-4" data-testid="map-skeleton">
      <Skeleton className="h-8 w-1/3" /><Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" /></div>
  }
  if (error && !effectiveCatalog) {
    return <div className="p-4" data-testid="map-error"><Alert variant="destructive">
      <AlertTitle>{errorLabel(error)}</AlertTitle>
      <AlertDescription>
        注册类型目录当前不可用，本体地图无法显示；这不是“没有已注册类型”。
      </AlertDescription>
    </Alert></div>
  }
  if (!effectiveCatalog) return null
  if (!versionEntry || !registered) {
    const available = effectiveCatalog.versions
      .map((entry) => rulesOfContractVersion(entry.contract_version))
      .filter((value): value is RulesVersion => value !== null)
    return <div className="p-4" data-testid="map-version-unavailable"><Alert>
      <AlertTitle>业务规则 {rules} 的注册类型目录不可用</AlertTitle>
      <AlertDescription>
        当前目录提供：{available.length
          ? available.map((value) => RULES_VERSION_LABELS[value]).join("、")
          : "无可识别版本"}。请切换到可用版本，未套用其他版本冒充。
      </AlertDescription>
    </Alert></div>
  }

  const blocks = blocksFor(rules, registered)
  const allTypes = blocks.flatMap((area) => area.types)
  const matches = query.trim()
    ? allTypes.filter((type) =>
        type.toLowerCase().includes(query.trim().toLowerCase())
        || (TYPE_LABELS[type] ?? "").includes(query.trim()))
    : []
  const matchSet = new Set(matches)
  const toggleArea = (areaId: string) => {
    setOpenAreas((current) => {
      const next = new Set(current)
      if (next.has(areaId)) next.delete(areaId)
      else next.add(areaId)
      return next
    })
  }
  const locate = (type: string) => {
    const areaId = layout.areaOf.get(type)
    if (areaId && !openAreas.has(areaId)) {
      // Searching opens the area that holds the match instead of losing it.
      pendingFocus.current = type
      setOpenAreas((current) => new Set(current).add(areaId))
    }
    onSelectType(type)
    const position = layout.positions.get(type)
    if (position) {
      setFocusPoint({ x: position.x + NODE_W / 2, y: position.y + NODE_H / 2,
                      seq: ++focusSeq.current })
      nodeRefs.current.get(type)?.focus()
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="ontology-map">
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card px-3 py-2">
        <Input value={query} onChange={(event) => setQuery(event.target.value)}
               placeholder="搜索类型名称…" className="h-7 w-56" data-testid="map-search"
               aria-label="搜索类型" />
        {query.trim() ? (
          <div className="flex flex-wrap items-center gap-1" data-testid="map-search-matches">
            {matches.length === 0 ? (
              <span className="text-[11px] text-muted-foreground">无匹配类型（目录不随数据增减）</span>
            ) : matches.map((type) => (
              <button key={type} type="button"
                      className="rounded border border-border bg-muted/40 px-1.5 py-0.5 text-[11px] hover:bg-muted"
                      onClick={() => locate(type)}>
                {TYPE_LABELS[type] ?? type}
              </button>
            ))}
          </div>
        ) : null}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {RULES_VERSION_LABELS[rules]} · 区域分组不是执行顺序 · 点击区域展开类型
        </span>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 lg:hidden" data-testid="map-mobile-list">
        {blocks.map((area) => <section key={area.id} className="rounded-md border bg-card p-3">
          <button type="button" className="flex w-full items-center justify-between text-left text-sm font-semibold"
            aria-expanded={openAreas.has(area.id)} onClick={() => toggleArea(area.id)}>
            {area.name}<span className="text-xs text-muted-foreground">{openAreas.has(area.id) ? "收起" : "展开"}</span>
          </button>
          <p className="mt-1 text-xs text-muted-foreground">{area.hint}</p>
          {openAreas.has(area.id) ? <div className="mt-3 flex flex-col gap-2">
            {area.types.map((type) => <button key={type} type="button"
              className="rounded border px-3 py-2 text-left text-sm hover:bg-muted"
              onClick={() => onSelectType(type)}>{TYPE_LABELS[type] ?? type}</button>)}
          </div> : null}
        </section>)}
      </div>
      <div className="hidden min-h-0 flex-1 lg:block">
        <PanZoomCanvas width={layout.width} height={layout.height} edges={layout.edges}
                       focusPoint={focusPoint}
                       emptyHint="先看业务区域与主要关系，点击区域展开对象类型；无实例的类型仍可查看。">
          {layout.areaBoxes.map((area) => (
            <div key={area.id}
                 className={cn("absolute rounded-md border bg-card/60",
                               area.id === "undocumented" ? "border-dashed border-border"
                                 : "border-border/70")}
                 style={{ left: area.x, top: area.y, width: area.width, height: area.height }}
                 data-testid={`map-area-${area.id}`}>
              <button type="button" aria-expanded={area.open}
                      data-testid={`map-area-toggle-${area.id}`}
                      onClick={() => toggleArea(area.id)}
                      className="relative flex w-full flex-col items-start gap-1 rounded-t-md px-3 py-2.5 pr-16 text-left hover:bg-muted/50">
                <span className="text-[14px] font-semibold">{area.name}</span>
                <span className="text-[10.5px] text-muted-foreground">{area.hint}</span>
                <span className="absolute right-3 top-3 text-[11px] text-muted-foreground">
                  {area.typeCount} 类 · {area.open ? "收起" : "展开"}
                </span>
              </button>
              {!area.open ? (
                <div className="px-3 text-[11px] leading-relaxed text-muted-foreground">
                  {area.types.map((type) => TYPE_LABELS[type] ?? type).join("、")}
                </div>
              ) : null}
            </div>
          ))}
          {[...layout.positions.keys()].map((type) => {
            const position = layout.positions.get(type)
            if (!position) return null
            const info = typeInfo(type, rules)
            const selected = selectedType === type
            return (
              <button key={type} type="button"
                      ref={(node) => {
                        if (node) nodeRefs.current.set(type, node)
                        else nodeRefs.current.delete(type)
                      }}
                      data-testid={`map-type-${type}`}
                      aria-pressed={selected}
                      onClick={() => onSelectType(type)}
                      className={cn(
                        "absolute flex flex-col justify-center gap-0.5 rounded-md border bg-background px-2.5 text-left shadow-sm transition-colors",
                        selected ? "border-primary ring-2 ring-primary/30"
                          : matchSet.has(type) ? "border-sky-400 ring-2 ring-sky-200"
                          : "border-border hover:border-foreground/40",
                      )}
                      style={{ left: position.x, top: position.y, width: NODE_W, height: NODE_H }}>
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-[12.5px] font-medium">
                    {TYPE_LABELS[type] ?? type}
                  </span>
                  {info?.marker ? (
                    <Badge variant="outline" className="shrink-0 rounded-sm px-1 text-[9.5px]">
                      {MARKER_LABELS[info.marker]}
                    </Badge>
                  ) : null}
                </span>
                <span className="truncate text-[10px] text-muted-foreground">
                  {type}{info ? "" : " · 暂无业务说明"}
                </span>
              </button>
            )
          })}
        </PanZoomCanvas>
      </div>
    </div>
  )
}
