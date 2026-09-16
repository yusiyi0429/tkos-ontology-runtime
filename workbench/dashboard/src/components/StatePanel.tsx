import { Badge } from "@/components/ui/badge"
import { RAG_LABELS, REVIEW_KIND_LABELS, actionLabel, formalBusinessText } from "@/lib/labels"
import { formatTime } from "@/lib/format"
import type { ConfirmationRecord, DownstreamEdge, ExactRef, FormalState, ResolvedRef } from "@/lib/types"

function RagBadge({ rag, formal }: { rag: string | null | undefined; formal: boolean }) {
  const value = rag ?? "unknown"
  const classes: Record<string, string> = {
    green: "border-emerald-300 bg-emerald-50 text-emerald-800",
    yellow: "border-amber-300 bg-amber-50 text-amber-900",
    red: "border-red-300 bg-red-50 text-red-800",
    unknown: "border-border bg-muted text-muted-foreground",
  }
  return (
    <Badge variant="outline"
           data-formal={formal ? "true" : "false"}
           className={`rounded-sm ${formal ? classes[value] ?? classes.unknown
             : "border-border bg-muted/60 text-muted-foreground"}`}>
      {formal ? RAG_LABELS[value] ?? value : `${RAG_LABELS[value] ?? value}（非正式）`}
    </Badge>
  )
}

function RefList({ items, refs, onOpen }: {
  items?: ResolvedRef[]
  refs: ExactRef[]
  onOpen: (objectId: string, revisionId?: string) => void
}) {
  if (items && items.length) {
    return (
      <ul className="space-y-0.5">
        {items.map((item) => (
          <li key={`${item.object_id}:${item.revision_id}`}>
            <button type="button" className="text-left text-primary underline-offset-2 hover:underline"
                    onClick={() => onOpen(item.object_id, item.revision_id)}>
              {item.title ?? "查看对象"}
            </button>
          </li>
        ))}
      </ul>
    )
  }
  if (!refs.length) return <span className="text-muted-foreground">未记录</span>
  return <span className="text-muted-foreground">记录中的引用当前不可读（不显示标识）</span>
}

export function StatePanel({ confirmation, states, onOpenObject }: {
  formalState?: FormalState
  confirmation: { records: ConfirmationRecord[]; confirmed_for_selected_revision: boolean }
  states: DownstreamEdge[]
  onOpenObject: (objectId: string, revisionId?: string) => void
}) {
  const relevantStates = states.filter((edge) => edge.state_summary)
  const covering = confirmation.records.filter((record) => record.covers_selected_revision)
  return (
    <section className="space-y-3" data-testid="state-panel">
      <div className="rounded-md border border-border bg-card p-3">
        <h3 className="text-[12px] font-semibold text-foreground">内容确认（该对象精确版本）</h3>
        <p className="mt-1 text-[11.5px] text-muted-foreground">
          {confirmation.confirmed_for_selected_revision
            ? "该精确版本有已记录的人类确认。"
            : "该精确版本尚无覆盖它的人工确认。"}
        </p>
        <ul className="mt-2 space-y-1.5">
          {covering.map((record) => (
            <li key={record.record_id} className="rounded border border-border/70 bg-muted/30 px-2 py-1.5">
              <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
                <span className="font-medium">{REVIEW_KIND_LABELS[record.kind] ?? "人工确认"}</span>
                <span className="text-muted-foreground">
                  {record.principal?.display_name ?? "确认人未记录"}
                </span>
                <span className="text-muted-foreground">{formatTime(record.recorded_at)}</span>
                {record.receipt ? (
                  <span className="text-muted-foreground">动作：{actionLabel(record.receipt.action_type)}</span>
                ) : null}
              </div>
              {"reason" in record.content && record.content.reason ? (
                <p className="mt-0.5 text-[11.5px] text-foreground/90">{String(record.content.reason)}</p>
              ) : null}
            </li>
          ))}
          {covering.length === 0 ? (
            <li className="text-[11.5px] text-muted-foreground">无覆盖该版本的确认记录。</li>
          ) : null}
        </ul>
      </div>
      <div className="rounded-md border border-border bg-card p-3">
        <h3 className="text-[12px] font-semibold text-foreground">
          正式经营状态（按精确 Mission/Outcome 版本）
        </h3>
        {relevantStates.length === 0 ? (
          <p className="mt-2 text-[11.5px] text-muted-foreground" data-testid="no-state">
            当前精确版本没有已记录的经营状态。
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {relevantStates.map((edge) => {
              const summary = edge.state_summary!
              return (
                <li key={`${edge.object_id}:${edge.ref.revision_id}`}
                    className="rounded border border-border/70 bg-muted/30 px-2 py-2"
                    data-testid={`state-${edge.object_id}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <RagBadge rag={summary.rag} formal={summary.formal} />
                    <span className="text-[11.5px] text-muted-foreground">
                      观察时点 {formatTime(summary.as_of)}
                    </span>
                    <span className="text-[11.5px] text-foreground/80">
                      {formalBusinessText("OperatingState", summary.status, summary.formal)}
                    </span>
                    {summary.outcome ? (
                      <button type="button" className="text-[11.5px] text-primary underline-offset-2 hover:underline"
                              onClick={() => onOpenObject(edge.object_id, edge.ref.revision_id)}>
                        Outcome：{String(summary.outcome.title ?? summary.outcome_id)}
                      </button>
                    ) : summary.outcome_id ? (
                      <span className="text-[11.5px] text-muted-foreground">Outcome：{summary.outcome_id}</span>
                    ) : null}
                  </div>
                  {summary.summary ? <p className="mt-1 text-[12px] text-foreground/90">{summary.summary}</p> : null}
                  <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                    <dt>基准</dt>
                    <dd><RefList items={summary.baseline_items} refs={summary.baseline_refs} onOpen={onOpenObject} /></dd>
                    <dt>证据</dt>
                    <dd><RefList items={summary.evidence_items} refs={summary.evidence_refs} onOpen={onOpenObject} /></dd>
                    {summary.data_gaps.length ? (
                      <><dt>数据缺口</dt><dd>{summary.data_gaps.join("；")}</dd></>
                    ) : null}
                  </dl>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}
