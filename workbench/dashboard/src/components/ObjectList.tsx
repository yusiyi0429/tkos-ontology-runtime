import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { errorLabel } from "@/lib/errors"
import {
  APPOINTMENT_LABELS, BASIS_LABELS, BASIS_REASON_LABELS, BASIS_SECTIONS,
  FORMAL_LABELS, RELATION_LABELS, TYPE_LABELS,
} from "@/lib/labels"
import { formatDay, formatPeriod, isMissing } from "@/lib/format"
import type { ObjectListItem, ResponsibilityEntry } from "@/lib/types"

export interface ObjectListProps {
  items: ObjectListItem[]
  selectedId: string | null
  loading: boolean
  hasMore: boolean
  basis?: string
  loadMoreError?: unknown
  onSelect: (item: ObjectListItem) => void
  onLoadMore: () => void
}

function FormalBadge({ item }: { item: ObjectListItem }) {
  const label = FORMAL_LABELS[item.formal_state.status] ?? "状态未记录"
  const tone = item.formal_state.formal
    ? "border-emerald-300 bg-emerald-50 text-emerald-800"
    : item.formal_state.status === "transferred"
      ? "border-amber-300 bg-amber-50 text-amber-900"
      : "border-border bg-muted text-muted-foreground"
  return <Badge variant="outline" className={`rounded-sm text-[10.5px] ${tone}`}>{label}</Badge>
}

function BasisBadge({ item }: { item: ObjectListItem }) {
  const status = item.basis.status
  const tone = status === "historical" || status === "mixed"
    ? "border-amber-300 bg-amber-50 text-amber-900"
    : status === "current"
      ? "border-emerald-200 bg-emerald-50/60 text-emerald-800"
      : "border-border bg-muted text-muted-foreground"
  return (
    <Badge variant="outline" data-basis={status} className={`rounded-sm text-[10.5px] ${tone}`}>
      {BASIS_LABELS[status] ?? "依据未记录"}
    </Badge>
  )
}

function ObjectRows({ items, selectedId, onSelect }: {
  items: ObjectListItem[]
  selectedId: string | null
  onSelect: (item: ObjectListItem) => void
}) {
  return (
    <ul className="divide-y divide-border">
      {items.map((item) => {
        const responsibility: ResponsibilityEntry[] = (item.responsibility
          ?? [item.owner, ...item.dri]).filter(
            (entry): entry is ResponsibilityEntry =>
              Boolean(entry) && !isMissing(entry) && entry.relation !== "participant")
        const active = item.object_id === selectedId
        return (
          <li key={`${item.object_id}:${item.basis_revision_id}`}>
            <button type="button" data-testid={`object-row-${item.object_id}`}
                    aria-current={active ? "true" : undefined}
                    onClick={() => onSelect(item)}
                    className={`w-full px-3 py-2.5 text-left transition-colors ${
                      active ? "bg-accent" : "hover:bg-muted/60"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[10.5px] text-muted-foreground">
                  {TYPE_LABELS[item.object_type] ?? item.object_type}
                </span>
                <FormalBadge item={item} />
                <BasisBadge item={item} />
              </div>
              <div className="mt-0.5 truncate text-[13px] font-medium text-foreground">
                {item.title ?? item.summary ?? "（未记录标题）"}
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                <span>周期：{item.period ? formatPeriod(item.period) : "未记录"}</span>
                {item.period && item.period_source === "pco_ref" ? <span>（引自 PCO 版本）</span> : null}
                {item.deadline ? <span>期限：{formatDay(item.deadline)}</span> : null}
                {responsibility.length ? responsibility.map((entry, index) => (
                  entry.principal ? (
                    <span key={`${entry.relation}-${entry.outcome_id ?? ""}-${index}`}>
                      {RELATION_LABELS[entry.relation] ?? "责任"}：{entry.principal.display_name}
                      {entry.appointment.status !== "current"
                        ? `（${APPOINTMENT_LABELS[entry.appointment.status] ?? "任职状态未记录"}）` : ""}
                    </span>
                  ) : null
                )) : <span>责任：未记录</span>}
              </div>
              {item.basis.status !== "current" && item.basis.reason ? (
                <div className="mt-0.5 text-[10.5px] text-amber-800">
                  {BASIS_REASON_LABELS[item.basis.reason] ?? "记录依据"}；保留其记录的精确战略版本，不并入当前战略。
                </div>
              ) : null}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export function ObjectList({ items, selectedId, loading, hasMore, basis = "all", loadMoreError,
                             onSelect, onLoadMore }: ObjectListProps) {
  if (loading && items.length === 0) {
    return (
      <div className="space-y-2 p-3" data-testid="list-skeleton">
        {[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-16 w-full" />)}
      </div>
    )
  }
  if (items.length === 0) {
    return (
      <div className="p-6 text-sm text-muted-foreground" data-testid="list-empty">
        当前分组与筛选下没有可读对象。
      </div>
    )
  }
  const sections = basis === "all"
    ? BASIS_SECTIONS
        .map((section) => ({ ...section,
                             items: items.filter((item) => section.statuses.includes(item.basis.status)) }))
        .filter((section) => section.items.length > 0)
    : null
  return (
    <div className="flex flex-col" data-testid="object-list">
      <div className="border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        已加载 {items.length} 条
      </div>
      {sections ? sections.map((section) => (
        <section key={section.title} data-testid={`basis-section-${section.title}`}
                 className="border-b border-border last:border-b-0">
          <header className="flex flex-wrap items-baseline gap-x-2 bg-muted/40 px-3 py-1.5">
            <h2 className="text-[12px] font-semibold text-foreground">{section.title}</h2>
            <span className="text-[10.5px] text-muted-foreground">{section.hint}</span>
            <span className="ml-auto text-[10.5px] text-muted-foreground">{section.items.length} 条</span>
          </header>
          <ObjectRows items={section.items} selectedId={selectedId} onSelect={onSelect} />
        </section>
      )) : <ObjectRows items={items} selectedId={selectedId} onSelect={onSelect} />}
      {hasMore ? (
        <div className="space-y-1 p-3">
          <Button variant="outline" size="sm" className="w-full" onClick={onLoadMore}
                  disabled={loading}>
            {loading ? "加载中…" : "加载更多"}
          </Button>
          {loadMoreError ? (
            <p className="text-[11px] text-destructive" data-testid="load-more-error">
              {errorLabel(loadMoreError)} 请重试；已加载内容保持不变。
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
