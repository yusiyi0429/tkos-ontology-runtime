import { useEffect, useRef, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { StatePanel } from "@/components/StatePanel"
import { TechnicalTrace } from "@/components/TechnicalTrace"
import { evidenceUrl, getJson } from "@/lib/api"
import { errorLabel, isAbort, isAccessDenial } from "@/lib/errors"
import {
  APPOINTMENT_LABELS, BASIS_LABELS, BASIS_REASON_LABELS, DEADLINE_LABELS, RAG_LABELS,
  RELATION_LABELS, REVIEW_KIND_LABELS, ROLE_LABELS, TYPE_LABELS,
  actionLabel, businessFieldLabel, candidateLabel, formalBusinessText, referenceLabel,
} from "@/lib/labels"
import { formatDay, formatPeriod, formatTime, formatValue, isMissing } from "@/lib/format"
import type { Detail, DownstreamEdge, DownstreamPage, ExactRef } from "@/lib/types"

function RefLine({ label, refValue, title, onOpen }: {
  label: string
  refValue: ExactRef | null | undefined
  title?: string | null
  onOpen?: (objectId: string, revisionId?: string) => void
}) {
  if (!refValue) return null
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 text-[12px]">
      <span className="text-muted-foreground">{referenceLabel(label)}</span>
      {onOpen ? (
        <button type="button" className="text-left font-medium text-primary underline-offset-2 hover:underline"
                onClick={() => onOpen(refValue.object_id, refValue.revision_id)}>
          {title ?? "查看对象"}
        </button>
      ) : <span className="font-medium">{title ?? "对象"}</span>}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[12.5px]">
      <div className="text-muted-foreground">{label}</div>
      <div className="min-w-0 break-words text-foreground">{children}</div>
    </div>
  )
}

const ENUM_VALUE_LABELS: Record<string, string> = {
  green: "正常", yellow: "关注", red: "风险", unknown: "未知",
  draft: "草稿", under_review: "核对中", confirmed: "已确认", candidate: "候选待确认",
  open: "开放", transferred: "已移交", resolved: "已解决", no_further_action: "无需继续处理",
}

// Identifier/model-trace fields stay folded under 技术溯源; they are not
// business edits a reader compares.
const TECHNICAL_DIFF_FIELDS = new Set(["generation_version", "review_id", "fact_id"])

function diffValue(value: unknown): string {
  if (typeof value === "string" && value in ENUM_VALUE_LABELS) return ENUM_VALUE_LABELS[value]
  if (Array.isArray(value)) return value.map(diffValue).join("；")
  if (value && typeof value === "object") return "已更新（原文见技术溯源）"
  return formatValue(value)
}

function diffFieldKey(path: string): string {
  const parts = path.replace(/^\//, "").split("/").filter((part) => part && !/^\d+$/.test(part))
  return parts[parts.length - 1] ?? path
}

function Baseline({ detail }: { detail: Detail }) {
  const business = detail.business
  return (
    <div className="space-y-1.5">
      <Field label="标题">{business.title ?? "未记录"}</Field>
      {business.statement ? <Field label="内容">{business.statement}</Field> : null}
      {business.summary ? <Field label="摘要">{business.summary}</Field> : null}
      {business.deliverable ? <Field label="交付物">{business.deliverable}</Field> : null}
      {business.boundary ? <Field label="边界">{business.boundary}</Field> : null}
      {business.acceptance_criteria.length ? (
        <Field label="验收标准"><ul className="list-disc space-y-0.5 pl-4">
          {business.acceptance_criteria.map((item, index) => <li key={index}>{item}</li>)}
        </ul></Field>
      ) : null}
      {business.period_view?.status === "available" ? (
        <Field label="业务周期">
          {formatPeriod((business.period_view.value as { start: string; end: string } | undefined) ?? business.period)}
          {business.period_view.source === "pco_ref"
            ? <span className="ml-1 text-[11px] text-muted-foreground">（引自 PCO 版本）</span> : null}
        </Field>
      ) : business.period ? <Field label="业务周期">{formatPeriod(business.period)}</Field> : null}
      {business.deadline ? (
        <Field label={DEADLINE_LABELS[business.deadline.kind] ?? "期限"}>{formatDay(business.deadline.value)}</Field>
      ) : null}
      {business.rag ? <Field label="状态评级">{RAG_LABELS[business.rag] ?? business.rag}</Field> : null}
      {business.as_of ? <Field label="观察时点">{formatTime(business.as_of)}</Field> : null}
      {business.core_question ? <Field label="核心问题">{business.core_question}</Field> : null}
      {business.why_material ? <Field label="为何重要">{business.why_material}</Field> : null}
      {business.level ? <Field label="层级">{business.level}</Field> : null}
      {business.metric ? <Field label="指标">{business.metric}：{formatValue(business.value)}{business.unit ? ` ${business.unit}` : ""}</Field> : null}
      {business.observations?.length ? (
        <Field label="观察"><ul className="list-disc space-y-0.5 pl-4">
          {business.observations.map((item, index) => <li key={index}>{item}</li>)}</ul></Field>
      ) : null}
      {business.findings.length ? (
        <Field label="发现"><ul className="list-disc space-y-0.5 pl-4">
          {business.findings.map((item, index) => <li key={index}>{item}</li>)}</ul></Field>
      ) : null}
      {business.learnings.length ? (
        <Field label="学习"><ul className="list-disc space-y-0.5 pl-4">
          {business.learnings.map((item, index) => <li key={index}>{item}</li>)}</ul></Field>
      ) : null}
      {business.implications.length ? (
        <Field label="影响"><ul className="list-disc space-y-0.5 pl-4">
          {business.implications.map((item, index) => <li key={index}>{item}</li>)}</ul></Field>
      ) : null}
      {business.data_gaps.length ? (
        <Field label="数据缺口"><ul className="list-disc space-y-0.5 pl-4">
          {business.data_gaps.map((item, index) => <li key={index}>{item}</li>)}</ul></Field>
      ) : null}
      {business.correction ? (
        <Field label="纠错">
          更正“{business.correction.corrects_ref.object_id === detail.object.object_id ? "原始事实" : "原始事实"}”：
          {business.correction.correction_reason}
        </Field>
      ) : null}
      {business.subject_outcome?.status === "available" && business.subject_outcome.value ? (
        <Field label="关联 Outcome">
          {String(business.subject_outcome.value.outcome.title ?? business.subject_outcome.value.outcome_id)}
          {Array.isArray(business.subject_outcome.value.outcome.criteria)
            ? <span className="ml-1 text-[11px] text-muted-foreground">
                （标准：{(business.subject_outcome.value.outcome.criteria as string[]).join("；")}）
              </span> : null}
        </Field>
      ) : null}
    </div>
  )
}

function outcomeResponsible(detail: Detail, outcomeId: string | null | undefined,
                            principalId: string | null | undefined) {
  if (!principalId) return "未记录责任"
  const entry = detail.responsibility.entries.find((candidate) =>
    candidate.relation === "outcome_dri" && candidate.outcome_id === outcomeId
    && candidate.principal?.principal_id === principalId)
  if (!entry?.principal) return "未记录责任"
  const suffix = entry.appointment.status === "current" ? ""
    : `（${APPOINTMENT_LABELS[entry.appointment.status] ?? "任职状态未记录"}）`
  return `${entry.principal.display_name}${suffix}`
}

function Outcomes({ detail, onOpenObject }: {
  detail: Detail
  onOpenObject: (objectId: string, revisionId?: string) => void
}) {
  if (!detail.business.outcomes.length && !detail.business.supports.length) return null
  const units = new Map<string, string>()
  for (const unit of detail.business.units) {
    if (typeof unit.unit_id === "string") units.set(unit.unit_id, String(unit.name ?? unit.unit_id))
  }
  return (
    <section className="mt-3 space-y-2" data-testid="outcomes">
      <h3 className="text-[12px] font-semibold">目标与承接的 Outcome</h3>
      {detail.business.outcomes.map((outcome) => (
        <div key={String(outcome.outcome_id)}
             className="space-y-1 rounded border border-border/70 bg-card px-2.5 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[12.5px] font-medium">{outcome.title ?? outcome.outcome_id}</span>
            {outcome.unit_id ? (
              <span className="text-[11px] text-muted-foreground">
                责任项：{units.get(outcome.unit_id) ?? outcome.unit_id}
              </span>
            ) : null}
            <span className="text-[11px] text-muted-foreground">
              责任人：{outcomeResponsible(detail, outcome.outcome_id,
                                         outcome.dri_principal_id ?? outcome.owner_principal_id)}
            </span>
          </div>
          <p className="text-[12px] text-foreground/90">{outcome.result_statement ?? "未记录结果"}</p>
          <p className="text-[11px] text-muted-foreground">
            标准：{(outcome.criteria ?? []).join("；") || "未记录"}
          </p>
        </div>
      ))}
      {detail.business.supports.map((support, index) => (
        <div key={`${support.outcome_id}-${index}`}
             className="space-y-1 rounded border border-border/70 bg-card px-2.5 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[12.5px] font-medium">
              {String(support.outcome?.title ?? support.outcome_id ?? "未记录")}
            </span>
            <span className="text-[11px] text-muted-foreground">承接贡献：{support.contribution ?? "未记录"}</span>
          </div>
          {support.outcome_status?.status === "available" ? (
            <>
              <p className="text-[12px] text-foreground/90">{String(support.outcome?.result_statement ?? "未记录结果")}</p>
              <p className="text-[11px] text-muted-foreground">
                标准：{(support.outcome?.criteria as string[] | undefined)?.join("；") ?? "未记录"}
              </p>
              {support.outcome_ref ? (
                <button type="button" className="text-[11px] text-primary underline-offset-2 hover:underline"
                        onClick={() => onOpenObject(support.outcome_ref!.object_id,
                                                    support.outcome_ref!.revision_id)}>
                  查看该 Outcome 所在的 PCO 版本
                </button>
              ) : null}
            </>
          ) : <p className="text-[11px] text-muted-foreground">Outcome 依据当前不可读。</p>}
        </div>
      ))}
    </section>
  )
}

function ReceiptDetail({ receiptId, onAccessDenied }: {
  receiptId: string
  onAccessDenied?: () => void
}) {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [open, setOpen] = useState(false)
  const epochRef = useRef(0)
  // A different receipt never reuses the previous detail or error.
  useEffect(() => {
    epochRef.current += 1
    setData(null)
    setError(null)
  }, [receiptId])
  useEffect(() => {
    if (!open || data) return
    const epoch = ++epochRef.current
    const controller = new AbortController()
    getJson<Record<string, unknown>>(`/action-receipts/${encodeURIComponent(receiptId)}`, controller.signal)
      .then((value) => { if (epoch === epochRef.current) setData(value) })
      .catch((reason) => {
        if (epoch !== epochRef.current || isAbort(reason)) return
        if (isAccessDenial(reason)) {
          // Same protection as the main and manual loaders: losing authority
          // clears protected App state immediately, not on the next poll.
          onAccessDenied?.()
          return
        }
        setError(reason)
      })
    return () => controller.abort()
  }, [open, data, receiptId, onAccessDenied])
  const receipt = (data?.receipt ?? null) as Record<string, unknown> | null
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button size="xs" variant="outline" data-testid={`receipt-${receiptId}`}>查看回执</Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        {error ? <p className="mt-1 text-[11px] text-destructive">{errorLabel(error)}</p> : null}
        {receipt ? (
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 rounded border border-border/60 bg-muted/30 px-2 py-1.5 text-[11px]">
            <dt className="text-muted-foreground">动作</dt>
            <dd>{actionLabel(String(receipt.action_type))}</dd>
            <dt className="text-muted-foreground">记录时间</dt>
            <dd>{formatTime(String(receipt.recorded_at))}</dd>
            <dt className="text-muted-foreground">状态</dt>
            <dd>{receipt.status === "committed" ? "已提交" : String(receipt.status)}</dd>
            <dt className="text-muted-foreground">涉及对象版本</dt>
            <dd>{Array.isArray(receipt.object_versions) ? receipt.object_versions.length : 0} 个</dd>
          </dl>
        ) : !error ? <Skeleton className="mt-1 h-12 w-full" /> : null}
      </CollapsibleContent>
    </Collapsible>
  )
}

function Responsibility({ detail }: { detail: Detail }) {
  return (
    <section className="mt-3 space-y-1.5" data-testid="responsibility">
      <h3 className="text-[12px] font-semibold">实际责任与任职状态</h3>
      {detail.responsibility.entries.length === 0 ? (
        <p className="text-[11.5px] text-muted-foreground">未记录责任人。Owner 不等于 DRI。</p>
      ) : (
        <ul className="space-y-1.5">
          {detail.responsibility.entries.map((entry, index) => {
            const currentRoles = entry.appointment.assignments
              .filter((item) => item.current)
              .map((item) => ROLE_LABELS[item.role] ?? item.role)
            return (
              <li key={`${entry.relation}-${entry.outcome_id ?? ""}-${index}`}
                  className="rounded border border-border/70 bg-muted/30 px-2 py-1.5 text-[12px]">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className="rounded-sm text-[10.5px]">
                    {RELATION_LABELS[entry.relation] ?? "责任关系"}
                  </Badge>
                  <span className="font-medium">{entry.principal?.display_name ?? "未记录"}</span>
                  {entry.appointment.status === "current" && currentRoles.length ? (
                    <span className="text-muted-foreground">{currentRoles.join(" / ")}</span>
                  ) : entry.assignment ? (
                    <span className="text-muted-foreground">
                      {ROLE_LABELS[entry.assignment.role] ?? "已记录岗位"}
                    </span>
                  ) : null}
                  <span className={`text-[11px] ${entry.appointment.status === "current"
                    ? "text-emerald-700" : "text-amber-800"}`}>
                    {APPOINTMENT_LABELS[entry.appointment.status] ?? "任职状态未记录"}
                  </span>
                  {entry.outcome_id ? (
                    <span className="text-[11px] text-muted-foreground">
                      Outcome：{detail.business.outcomes.find(
                        (outcome) => outcome.outcome_id === entry.outcome_id)?.title ?? entry.outcome_id}
                    </span>
                  ) : null}
                </div>
                {entry.appointment.status !== "current" && entry.appointment.assignments.length ? (
                  <div className="mt-0.5 text-[10.5px] text-muted-foreground">
                    记录任职：{entry.appointment.assignments.map((item) =>
                      `${ROLE_LABELS[item.role] ?? "已记录岗位"}（${item.current ? "当前"
                        : item.future ? "尚未开始" : item.active ? "已过期" : "已撤销"}）`).join("、")}
                  </div>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function DownstreamList({ page, onOpen, onLoadMore, loadingMore, loadError }: {
  page: DownstreamPage
  onOpen: (objectId: string, revisionId?: string) => void
  onLoadMore: () => void
  loadingMore: boolean
  loadError?: unknown
}) {
  if (page.items.length === 0) {
    return <p className="text-[11.5px] text-muted-foreground" data-testid="downstream-empty">
      没有记录在案的下一层对象。
    </p>
  }
  return (
    <div className="space-y-1.5" data-testid="downstream">
      {page.items.map((edge) => (
        <button key={`${edge.object_id}:${edge.ref.revision_id}`} type="button"
                className="w-full rounded border border-border/70 bg-card px-2 py-1.5 text-left hover:bg-muted/40"
                onClick={() => onOpen(edge.object_id, edge.ref.revision_id)}>
          <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
            <span className="text-muted-foreground">{TYPE_LABELS[edge.object_type] ?? edge.object_type}</span>
            <span className="font-medium">{edge.title ?? edge.summary ?? "未命名对象"}</span>
            {edge.formal_state.formal ? <Badge variant="outline" className="rounded-sm text-[10px]">正式生效</Badge> : null}
            <span className="text-[10.5px] text-muted-foreground">
              依据：{edge.matched_fields.map(referenceLabel).join("、")}
            </span>
          </div>
        </button>
      ))}
      {page.has_more ? (
        <div className="space-y-1">
          <Button variant="outline" size="xs" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? "加载中…" : "加载更多下一层对象"}
          </Button>
          {loadError ? (
            <p className="text-[11px] text-destructive" data-testid="downstream-error">
              {errorLabel(loadError)} 请重试。
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

const STATE_TARGET_TYPES = new Set(["Mission", "LTCO", "PCO"])

function StateOwnLinks({ detail, onOpenObject }: {
  detail: Detail
  onOpenObject: (objectId: string, revisionId?: string) => void
}) {
  const baseline = detail.evidence.items.filter((item) => item.field === "baseline_refs")
  const evidence = detail.evidence.items.filter((item) => item.field === "evidence_refs")
  if (!baseline.length && !evidence.length) return null
  const render = (items: typeof baseline) => (
    <ul className="space-y-0.5">
      {items.map((item) => (
        <li key={`${item.object_id}:${item.revision_id}`}>
          <button type="button" className="text-left text-primary underline-offset-2 hover:underline"
                  onClick={() => onOpenObject(item.object_id, item.revision_id)}>
            {item.title ?? "查看对象"}
          </button>
        </li>
      ))}
    </ul>
  )
  return (
    <section className="mt-3 space-y-1.5" data-testid="state-own-links">
      <h3 className="text-[12px] font-semibold">状态基准与证据（精确版本）</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
        <dt className="text-muted-foreground">基准</dt>
        <dd>{baseline.length ? render(baseline) : <span className="text-muted-foreground">未记录</span>}</dd>
        <dt className="text-muted-foreground">证据</dt>
        <dd>{evidence.length ? render(evidence) : <span className="text-muted-foreground">未记录</span>}</dd>
      </dl>
    </section>
  )
}

export interface DetailPaneProps {
  detail: Detail | null
  loading: boolean
  error: unknown
  onOpenObject: (objectId: string, revisionId?: string) => void
  onSelectRevision: (revisionId: string) => void
  downstreamMore?: DownstreamEdge[]
  downstreamHasMore?: boolean
  downstreamLoading?: boolean
  downstreamError?: unknown
  onLoadMoreDownstream?: () => void
  historyMore?: import("@/lib/types").HistoryRevision[]
  historyHasMore?: boolean
  historyLoading?: boolean
  historyError?: unknown
  onLoadMoreHistory?: () => void
  receiptsMore?: Array<Record<string, unknown>>
  receiptsHasMore?: boolean
  receiptsLoading?: boolean
  receiptsError?: unknown
  onLoadMoreReceipts?: () => void
  onAccessDenied?: () => void
  onShowTypeRules?: (objectType: string, contractVersion: unknown) => void
}

export function DetailPane({ detail, loading, error, onOpenObject, onSelectRevision,
                            downstreamMore = [], downstreamHasMore = false,
                            downstreamLoading = false, downstreamError = null,
                            onLoadMoreDownstream, historyMore = [], historyHasMore = false,
                            historyLoading = false, historyError = null, onLoadMoreHistory,
                            receiptsMore = [], receiptsHasMore = false, receiptsLoading = false,
                            receiptsError = null, onLoadMoreReceipts,
                            onAccessDenied, onShowTypeRules }: DetailPaneProps) {
  if (!detail && loading) {
    return <div className="space-y-2 p-4" data-testid="detail-skeleton"><Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-24 w-full" /><Skeleton className="h-24 w-full" /></div>
  }
  if (!detail) {
    return <div className="p-6 text-sm text-muted-foreground" data-testid="detail-empty">
      从左侧列表选择一个对象查看其正式内容、依据与确认证据。
    </div>
  }
  const downstreamPage: DownstreamPage = {
    ...detail.relations.downstream,
    items: [...detail.relations.downstream.items, ...downstreamMore],
    has_more: downstreamHasMore,
  }
  const states = downstreamPage.items.filter((edge) => edge.object_type === "OperatingState")
  const historyItems = [...detail.history.items, ...historyMore]
  const receipts = [...detail.receipts.items, ...receiptsMore]
  const effectiveId = isMissing(detail.candidates.effective)
    ? null : detail.candidates.effective.revision_id
  const latestId = isMissing(detail.candidates.latest) ? null : detail.candidates.latest.revision_id
  const candidateByRevision = new Map<string, {
    revision_id: string; recorded_at: string; object_version: number;
    changed_fields_vs_selected?: Array<{ field_path: string; before: unknown; after: unknown; change: string }>
  }>()
  for (const candidate of [detail.candidates.effective, detail.candidates.latest]) {
    if (isMissing(candidate) || candidate.is_selected) continue
    candidateByRevision.set(candidate.revision_id, candidate)
  }
  const candidates = [...candidateByRevision.values()]
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="detail-pane">
      <div className="border-b border-border bg-card px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="rounded-sm text-[10.5px]">
            {TYPE_LABELS[detail.object.object_type] ?? detail.object.object_type}
          </Badge>
          <span className="text-[13px] font-semibold">
            {detail.business.title ?? detail.business.summary ?? "（未记录标题）"}
          </span>
          <span className="text-[11px] text-muted-foreground">业务域：{detail.object.domain_name ?? "未记录"}</span>
          <span className="text-[10.5px] text-muted-foreground">
            第 {String(detail.selected_revision.object_version)} 版
            {detail.selected_revision.selection === "requested" ? "（指定/历史版本）" : ""}
          </span>
          {onShowTypeRules ? (
            <Button size="xs" variant="outline" className="ml-auto" data-testid="show-type-rules"
                    onClick={() => onShowTypeRules(detail.object.object_type,
                                                   detail.protocol.contract_version)}>
              查看该类型业务规则
            </Button>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <span>正式状态：{formalBusinessText(detail.object.object_type, detail.formal_state.status,
                                             detail.formal_state.formal)}</span>
          <span>依据：{BASIS_LABELS[detail.basis.status] ?? "未记录"}</span>
          {detail.basis.reason ? <span>（{BASIS_REASON_LABELS[detail.basis.reason] ?? "依据说明未记录"}）</span> : null}
          {detail.context_note === "multiple_current_strategies_readable"
            ? <span className="text-amber-800">存在多个独立战略，请先选择战略</span> : null}
        </div>
      </div>
      <Tabs defaultValue="overview" className="flex min-h-0 flex-1 flex-col">
        <div className="border-b border-border bg-card px-3">
          <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1 bg-transparent p-0 py-1">
            <TabsTrigger value="overview" className="whitespace-nowrap">概览</TabsTrigger>
            <TabsTrigger value="relations" className="whitespace-nowrap">依据与关系</TabsTrigger>
            <TabsTrigger value="versions" className="whitespace-nowrap">版本与候选</TabsTrigger>
            <TabsTrigger value="evidence" className="whitespace-nowrap">证据与确认</TabsTrigger>
            <TabsTrigger value="technical" className="whitespace-nowrap">技术溯源</TabsTrigger>
          </TabsList>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          <TabsContent value="overview" className="m-0 space-y-3">
            <Baseline detail={detail} />
            <Outcomes detail={detail} onOpenObject={onOpenObject} />
            <Responsibility detail={detail} />
            {STATE_TARGET_TYPES.has(detail.object.object_type) ? (
              <StatePanel formalState={detail.formal_state} confirmation={detail.content_confirmation}
                          states={states} onOpenObject={onOpenObject} />
            ) : detail.object.object_type === "OperatingState" ? (
              <StateOwnLinks detail={detail} onOpenObject={onOpenObject} />
            ) : null}
          </TabsContent>
          <TabsContent value="relations" className="m-0 space-y-4">
            <section>
              <h3 className="text-[12px] font-semibold">直接依据（精确引用）</h3>
              <div className="mt-1 space-y-1">
                {detail.relations.own_basis_refs.length === 0
                  ? <p className="text-[11.5px] text-muted-foreground">未记录直接依据。</p>
                  : detail.relations.own_basis_refs.map((ref) => (
                    <RefLine key={`${ref.path}:${ref.object_id}:${ref.revision_id}`} label={ref.path}
                             refValue={ref} title={ref.title} onOpen={onOpenObject} />
                  ))}
              </div>
            </section>
            <section>
              <h3 className="text-[12px] font-semibold">记录中的来源材料</h3>
              {detail.relations.upstream_refs.length === 0 ? (
                <p className="mt-1 text-[11.5px] text-muted-foreground">没有额外的来源材料。</p>
              ) : (
                <div className="mt-1 max-h-72 space-y-1 overflow-y-auto pr-1">
                  {detail.relations.upstream_refs.map((ref) => (
                    <RefLine key={`${ref.path}:${ref.object_id}:${ref.revision_id}`} label={ref.path}
                             refValue={ref} title={ref.title} onOpen={onOpenObject} />
                  ))}
                </div>
              )}
            </section>
            <section>
              <h3 className="text-[12px] font-semibold">下一层对象（按精确版本）</h3>
              <div className="mt-1">
                <DownstreamList page={downstreamPage} onOpen={onOpenObject}
                                onLoadMore={() => onLoadMoreDownstream?.()}
                                loadingMore={downstreamLoading} loadError={downstreamError} />
              </div>
            </section>
          </TabsContent>
          <TabsContent value="versions" className="m-0 space-y-4">
            <section>
              <h3 className="text-[12px] font-semibold">历史版本（阅读时保留你选择的精确版本）</h3>
              <ul className="mt-1 space-y-1" data-testid="history-list">
                {historyItems.map((item) => (
                  <li key={item.revision_id}
                      className="flex flex-wrap items-center gap-2 rounded border border-border/70 bg-card px-2 py-1.5 text-[12px]">
                    <span>第 {item.object_version} 版</span>
                    <span className="text-muted-foreground">{formatTime(item.recorded_at)}</span>
                    {item.is_effective ? (
                      <Badge variant="outline" className="rounded-sm text-[10px]">当前正式</Badge>
                    ) : item.is_latest ? (
                      <Badge variant="outline" className="rounded-sm text-[10px]">最新版本</Badge>
                    ) : null}
                    <Button size="xs" variant="outline" onClick={() => onSelectRevision(item.revision_id)}
                            disabled={item.revision_id === detail.selected_revision.revision_id}
                            className="ml-auto">
                      查看此版本
                    </Button>
                  </li>
                ))}
              </ul>
              {historyHasMore ? (
                <div className="mt-1 space-y-1">
                  <Button size="xs" variant="outline" onClick={() => onLoadMoreHistory?.()}
                          disabled={historyLoading}>
                    {historyLoading ? "加载中…" : "加载更多版本"}
                  </Button>
                  {historyError ? (
                    <p className="text-[11px] text-destructive" data-testid="history-error">
                      {errorLabel(historyError)} 已加载版本保持不变。
                    </p>
                  ) : null}
                </div>
              ) : null}
            </section>
            <section>
              <h3 className="text-[12px] font-semibold">版本对比（同一对象精确版本）</h3>
              {candidates.length === 0 ? (
                <p className="mt-1 text-[11.5px] text-muted-foreground" data-testid="no-candidates">
                  当前选中的就是最新且生效的版本，没有其他候选。
                </p>
              ) : (
                <ul className="mt-1 space-y-2">
                  {candidates.map((candidate) => {
                    const classification = candidateLabel(candidate, effectiveId, latestId)
                    return (
                      <li key={candidate.revision_id}
                          className="rounded border border-border/70 bg-card px-2.5 py-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant="outline" className={`rounded-sm text-[10.5px] ${classification.tone}`}>
                            {classification.label}
                          </Badge>
                          <span className="text-[12px]">第 {candidate.object_version} 版</span>
                          <span className="text-[11px] text-muted-foreground">
                            记录于 {formatTime(candidate.recorded_at)}
                          </span>
                          <Button size="xs" variant="ghost" className="ml-auto"
                                  onClick={() => onSelectRevision(candidate.revision_id)}>
                            查看此版本
                          </Button>
                        </div>
                        {(() => {
                          const changes = candidate.changed_fields_vs_selected ?? []
                          const businessChanges = changes.filter(
                            (change) => !TECHNICAL_DIFF_FIELDS.has(diffFieldKey(change.field_path)))
                          const technicalCount = changes.length - businessChanges.length
                          if (!changes.length) {
                            return <p className="mt-1 text-[11px] text-muted-foreground">没有可比较的字段差异。</p>
                          }
                          return (
                            <>
                              {businessChanges.length ? (
                                <ul className="mt-1 space-y-0.5 text-[11.5px]">
                                  {businessChanges.map((change) => (
                                    <li key={change.field_path} className="break-words">
                                      <span className="font-medium">{businessFieldLabel(change.field_path)}</span>
                                      {"："}
                                      <span className="text-muted-foreground">{diffValue(change.before)}</span>
                                      {" → "}
                                      <span>{diffValue(change.after)}</span>
                                    </li>
                                  ))}
                                </ul>
                              ) : null}
                              {technicalCount ? (
                                <p className="mt-1 text-[11px] text-muted-foreground">
                                  另有 {technicalCount} 项技术字段（生成版本等）变更，见技术溯源。
                                </p>
                              ) : null}
                            </>
                          )
                        })()}
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>
          </TabsContent>
          <TabsContent value="evidence" className="m-0 space-y-4">
            <section>
              <h3 className="text-[12px] font-semibold">证据与基准（精确版本）</h3>
              {detail.evidence.items.length === 0 ? (
                <p className="mt-1 text-[11.5px] text-muted-foreground">未记录证据引用。</p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {detail.evidence.items.map((item) => (
                    <li key={`${item.object_id}:${item.revision_id}:${item.field ?? ""}`}
                        className="flex flex-wrap items-center gap-2 rounded border border-border/70 bg-card px-2 py-1.5 text-[12px]">
                      <span className="text-muted-foreground">{referenceLabel(item.field ?? "")}</span>
                      <span className="font-medium">{item.title ?? "已记录对象"}</span>
                      {item.download?.available ? (
                        // Same-origin anchor with ``download`` (no target=_blank) so the
                        // browser download event is observable while the page stays put.
                        <a className="text-[11px] text-primary underline-offset-2 hover:underline"
                           href={evidenceUrl(item.object_id, item.revision_id)} download>
                          下载原始证据
                        </a>
                      ) : (
                        <button type="button" className="text-[11px] text-primary underline-offset-2 hover:underline"
                                onClick={() => onOpenObject(item.object_id, item.revision_id)}>
                          查看对象
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section>
              <h3 className="text-[12px] font-semibold">人工确认记录</h3>
              {detail.content_confirmation.records.length === 0 ? (
                <p className="mt-1 text-[11.5px] text-muted-foreground">未记录人工确认。</p>
              ) : (
                <ul className="mt-1 space-y-1.5">
                  {detail.content_confirmation.records.map((record) => (
                    <li key={record.record_id} className="rounded border border-border/70 bg-card px-2 py-1.5 text-[12px]">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline" className="rounded-sm text-[10.5px]">
                          {REVIEW_KIND_LABELS[record.kind] ?? "人工确认"}
                        </Badge>
                        <span className="font-medium">{record.principal?.display_name ?? "确认人未记录"}</span>
                        <span className="text-muted-foreground">{formatTime(record.recorded_at)}</span>
                        <span className={record.covers_selected_revision ? "text-emerald-700" : "text-muted-foreground"}>
                          {record.covers_selected_revision ? "覆盖当前精确版本" : "不覆盖当前版本"}
                        </span>
                        {record.receipt ? (
                          <>
                            <span className="text-muted-foreground">动作：{actionLabel(record.receipt.action_type)}</span>
                            <ReceiptDetail receiptId={record.receipt.receipt_id}
                                           onAccessDenied={onAccessDenied} />
                          </>
                        ) : null}
                      </div>
                      {"reason" in record.content && record.content.reason ? (
                        <p className="mt-0.5 text-[11.5px] text-muted-foreground">{String(record.content.reason)}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section>
              <h3 className="text-[12px] font-semibold">动作回执（与对象相关）</h3>
              {receipts.length === 0 ? (
                <p className="mt-1 text-[11.5px] text-muted-foreground">未记录相关回执。</p>
              ) : (
                <ul className="mt-1 space-y-1" data-testid="receipt-list">
                  {receipts.map((receipt) => (
                    <li key={String(receipt.receipt_id)}
                        className="flex flex-wrap items-center gap-2 rounded border border-border/70 bg-card px-2 py-1.5 text-[12px]">
                      <span className="font-medium">{actionLabel(String(receipt.action_type))}</span>
                      <span className="text-muted-foreground">{formatTime(String(receipt.recorded_at))}</span>
                      <ReceiptDetail receiptId={String(receipt.receipt_id)}
                                     onAccessDenied={onAccessDenied} />
                    </li>
                  ))}
                </ul>
              )}
              {receiptsHasMore ? (
                <div className="mt-1 space-y-1">
                  <Button size="xs" variant="outline" onClick={() => onLoadMoreReceipts?.()}
                          disabled={receiptsLoading}>
                    {receiptsLoading ? "加载中…" : "加载更多回执"}
                  </Button>
                  {receiptsError ? (
                    <p className="text-[11px] text-destructive" data-testid="receipts-error">
                      {errorLabel(receiptsError)} 已加载回执保持不变。
                    </p>
                  ) : null}
                </div>
              ) : null}
            </section>
          </TabsContent>
          <TabsContent value="technical" className="m-0">
            <TechnicalTrace detail={detail} error={error} />
          </TabsContent>
        </div>
      </Tabs>
      <Separator />
      <p className="px-4 py-1.5 text-[10.5px] text-muted-foreground">只读浏览，不产生业务写入。</p>
    </div>
  )
}
