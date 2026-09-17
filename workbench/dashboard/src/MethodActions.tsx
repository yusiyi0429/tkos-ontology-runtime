import { useCallback, useEffect, useRef, useState } from "react"
import { GitPullRequest, RefreshCw } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ApiError } from "@/lib/errors"

export type MethodExactRef = { object_id: string; revision_id: string; payload_hash: string }
export type MethodActionTarget = MethodExactRef & { expected_version: number }
export type MethodOption = { value: string; label: string; ref?: MethodExactRef; record_id?: string;
                             principal_id?: string; assignment_id?: string; personal_agent_id?: string | null;
                             role?: string; domain_id?: string; target_ref?: MethodExactRef }
export type MethodActionOptions = {
  targets?: MethodOption[]; opinions?: MethodOption[]; responsibilities?: MethodOption[]
  assignments?: MethodOption[]; evidences?: MethodOption[]; participants?: MethodOption[]
  state_ref?: MethodExactRef; domain_id?: string
}
export type MethodAction = { action_type: string; label: string; formal_effect: string;
                             allowed: boolean; reason: string | null;
                             target: MethodActionTarget | null;
                             object_domain_id?: string; contract_version: string;
                             options?: MethodActionOptions }
export type MethodCandidateMember = { ref: MethodExactRef; object_type: string; title: string;
                                      payload: Record<string, unknown> }
export type MethodCommitment = { responsibility_object_id: string; principal_id: string;
                                 assignment_id: string; statement: string }
export type MethodTask = { object_id: string; object_type: string; title: string; phase: string;
                           contract_version: string; actions: MethodAction[];
                           payload?: Record<string, unknown>; method_state?: Record<string, unknown>
                           members?: MethodCandidateMember[]; commitments?: MethodCommitment[] }
export type MethodSession = { identity: { principal_id: string; display_name: string }; csrf: string }

const BASE = '/dashboard/api/v1'
const STATUS: Record<string, string> = { issue_confirmed: '议题已确认', draft: '草稿', awaiting_confirmation: '等待全体确认',
  formal: 'Agreement 已正式', proposed: '待最终确认', reviewed: '已复核', returned: '已退回',
  open: '开放', closed: '已关窗', pending: '待承诺', under_review: '核对中', resolved: '候选待激活',
  confirmed: '已确认', generated: '已生成', unknown: '状态未记录', agreement_formal: 'Agreement 已正式',
  reopened: '已重开', candidate: '候选' }

// Only these 0.4 human actions may ever render in the browser.  Agent drafting,
// review and resolution actions stay out even if a projection leaks one.
const BROWSER_ACTIONS = new Set([
  'm1a_set_participants', 'm1a_confirm_agreement', 'm1a_confirm_update',
  'm1b_confirm_ltco', 'm1b_comment', 'm1b_replace_comment', 'm1b_withdraw_comment',
  'm1b_commit_candidate', 'm1b_activate_candidates', 'm1b_reopen_candidates',
  'm1b_reopen_window', 'method_confirm_state', 'method_open_problem',
  'method_revise_problem', 'method_close_problem',
])

const REASON_LABELS: Record<string, string> = {
  human_identity_required: '需要人类身份',
  phase_not_permitted: '当前阶段不允许此动作',
  current_role_not_permitted: '当前任职不允许此动作',
  already_confirmed: '本人已确认该精确版本',
  not_required_participant: '不是该 Agreement 的必要参与人',
  not_window_participant: '不是该评审窗口的参与人',
  not_responsible_owner: '不是该责任的本人',
  not_state_owner: '不是该状态的现任责任人',
  missing_commitment: '尚缺具名 DRI/Owner 本人承诺',
  critical_difference: '存在关键未决分歧，需先重开收敛',
  stale_basis: 'Strategy/Architecture 已更新，候选依据过期，需显式重开',
  member_state_changed: '候选成员版本已变化，需重新收拢',
  commitment_assignment_revoked: '承诺时的任职已撤销，需重开并重新承诺',
  commitment_assignment_mismatch: '承诺任职与责任不符',
}

type Field =
  | { kind: 'text' | 'textarea'; name: string; label: string; required?: boolean; placeholder?: string }
  | { kind: 'select'; name: string; label: string; required?: boolean; options: Array<{ value: string; label: string }> }
  | { kind: 'option'; name: string; label: string; required?: boolean;
      source: 'targets' | 'opinions' | 'responsibilities' | 'assignments'; keepRefAs?: string }
  | { kind: 'evidence'; name: string; label: string }
  | { kind: 'participants'; name: string; label: string }

const EVIDENCE_FIELD: Field = { kind: 'evidence', name: 'evidence_refs', label: '依据（从本人可读候选中选择）' }
const PROBLEM_FIELDS: Field[] = [
  { kind: 'option', name: 'responsible_assignment_id', label: '责任任职（仅本人当前任职）', required: true,
    source: 'assignments' },
  { kind: 'text', name: 'core_question', label: '核心问题', required: true },
  { kind: 'textarea', name: 'statement', label: '问题陈述', required: true },
  { kind: 'textarea', name: 'why_material', label: '为何重要', required: true },
  { kind: 'select', name: 'level', label: '层级', required: true,
    options: [{ value: 'mission', label: 'mission · Mission' }, { value: 'domain', label: 'domain · Business Scope' },
              { value: 'company', label: 'company · 公司' }, { value: 'strategic', label: 'strategic · 战略' }] },
  { kind: 'text', name: 'decision_deadline', label: '决定期限（ISO8601，可空）' },
]

const FORMS: Record<string, Field[]> = {
  m1a_confirm_agreement: [{ kind: 'textarea', name: 'statement', label: '确认说明', required: true }],
  m1a_confirm_update: [{ kind: 'textarea', name: 'statement', label: '最终确认说明', required: true }],
  m1b_confirm_ltco: [{ kind: 'textarea', name: 'statement', label: '确认说明', required: true }],
  m1b_activate_candidates: [
    { kind: 'textarea', name: 'statement', label: '整组确认说明', required: true },
    { kind: 'textarea', name: 'notes', label: '非阻塞说明（每行一条，可空）' }],
  m1b_reopen_candidates: [{ kind: 'textarea', name: 'reason', label: '退回理由', required: true },
                          { kind: 'text', name: 'title', label: '新窗口名称', required: true }],
  m1b_reopen_window: [{ kind: 'textarea', name: 'reason', label: '重开理由', required: true },
                      { kind: 'text', name: 'title', label: '新窗口名称', required: true }],
  method_confirm_state: [{ kind: 'textarea', name: 'reason', label: '确认理由', required: true },
                         { kind: 'textarea', name: 'summary', label: '修正说明（与评级同时填写）' },
                         { kind: 'select', name: 'rag', label: '修正评级（与说明同时填写）',
                           options: [{ value: 'unknown', label: 'unknown · 无证据即缺口' },
                                     { value: 'green', label: 'green' }, { value: 'yellow', label: 'yellow' },
                                     { value: 'red', label: 'red' }] }],
  method_open_problem: [...PROBLEM_FIELDS, EVIDENCE_FIELD],
  method_revise_problem: PROBLEM_FIELDS,
  method_close_problem: [
    { kind: 'select', name: 'disposition', label: '处置', required: true,
      options: [{ value: 'resolved', label: 'resolved' }, { value: 'no_further_action', label: 'no_further_action' }] },
    { kind: 'textarea', name: 'reason', label: '关闭理由', required: true },
    EVIDENCE_FIELD],
  m1b_commit_candidate: [
    { kind: 'option', name: 'responsibility_ref', label: '本人责任（只列本人 DRI/Owner 责任）', required: true,
      source: 'responsibilities', keepRefAs: 'responsibility' },
    { kind: 'textarea', name: 'statement', label: '本人承诺说明', required: true }],
  m1a_set_participants: [{ kind: 'participants', name: 'participants', label: '必要参与人（含 CEO，来自当前任职）' }],
  m1b_comment: [
    { kind: 'option', name: 'target_ref', label: '评论对象（窗口冻结的确切目标）', required: true,
      source: 'targets', keepRefAs: 'target' },
    { kind: 'textarea', name: 'content', label: '意见内容', required: true }],
  m1b_replace_comment: [
    { kind: 'option', name: 'replaces_record_id', label: '被替代的本人有效意见', required: true,
      source: 'opinions', keepRefAs: 'target' },
    { kind: 'textarea', name: 'content', label: '意见内容', required: true }],
  m1b_withdraw_comment: [
    { kind: 'option', name: 'review_record_id', label: '本人有效意见', required: true, source: 'opinions' },
    { kind: 'textarea', name: 'reason', label: '撤回理由', required: true }],
}

function text(values: Record<string, unknown>, name: string): string {
  const value = values[name]
  return typeof value === 'string' ? value : ''
}
function rows(values: Record<string, unknown>, name: string): Array<Record<string, string>> {
  const value = values[name]
  return Array.isArray(value) ? value as Array<Record<string, string>> : []
}
function optionList(values: Record<string, unknown>, name: string): MethodOption[] {
  const value = values[`${name}__options`]
  return Array.isArray(value) ? value as MethodOption[] : []
}
function selectedOption(values: Record<string, unknown>, name: string): MethodOption | undefined {
  const value = values[`${name}__option`]
  return value && typeof value === 'object' ? value as MethodOption : undefined
}
function refFrom(values: Record<string, unknown>, prefix: string): MethodExactRef {
  return { object_id: text(values, `${prefix}_object_id`), revision_id: text(values, `${prefix}_revision_id`),
           payload_hash: text(values, `${prefix}_payload_hash`) }
}
function exactRefs(values: Record<string, unknown>, name: string): MethodExactRef[] {
  const value = values[name]
  if (!Array.isArray(value)) return []
  return (value as MethodExactRef[]).filter((ref) => ref?.object_id && ref?.revision_id && ref?.payload_hash)
}
function applyRef(values: Record<string, unknown>, prefix: string, ref: MethodExactRef): Record<string, unknown> {
  return { ...values, [`${prefix}_object_id`]: ref.object_id, [`${prefix}_revision_id`]: ref.revision_id,
           [`${prefix}_payload_hash`]: ref.payload_hash }
}

export function paramsForMethodAction(actionType: string, values: Record<string, unknown>): Record<string, unknown> {
  switch (actionType) {
    case 'm1a_confirm_agreement':
    case 'm1a_confirm_update':
    case 'm1b_confirm_ltco':
      return { statement: text(values, 'statement') }
    case 'm1b_activate_candidates':
      return { statement: text(values, 'statement'),
               notes: text(values, 'notes').split('\n').map((line) => line.trim()).filter(Boolean) }
    case 'm1b_reopen_candidates':
    case 'm1b_reopen_window':
      return { reason: text(values, 'reason'), title: text(values, 'title') }
    case 'method_confirm_state': {
      const params: Record<string, unknown> = { reason: text(values, 'reason') }
      if (text(values, 'rag')) { params.summary = text(values, 'summary'); params.rag = text(values, 'rag') }
      return params
    }
    case 'method_open_problem':
    case 'method_revise_problem': {
      const payload: Record<string, unknown> = {
        state_ref: refFrom(values, 'state'),
        core_question: text(values, 'core_question'), statement: text(values, 'statement'),
        why_material: text(values, 'why_material'), level: text(values, 'level'),
        responsible_assignment_id: text(values, 'responsible_assignment_id'),
        evidence_refs: exactRefs(values, 'evidence_refs'),
      }
      if (text(values, 'decision_deadline')) payload.decision_deadline = text(values, 'decision_deadline')
      return actionType === 'method_open_problem'
        ? { domain_id: text(values, 'domain_id'), payload }
        : { payload }
    }
    case 'method_close_problem':
      return { disposition: text(values, 'disposition'), reason: text(values, 'reason'),
               evidence_refs: exactRefs(values, 'evidence_refs') }
    case 'm1b_commit_candidate':
      return { responsibility_ref: refFrom(values, 'responsibility'), statement: text(values, 'statement') }
    case 'm1a_set_participants':
      return { participants: rows(values, 'participants')
        .filter((row) => row.principal_id && row.assignment_id)
        .map((row) => ({ principal_id: row.principal_id, assignment_id: row.assignment_id,
                         personal_agent_id: row.personal_agent_id || null,
                         research: row.research === 'true' })) }
    case 'm1b_comment':
      return { target_ref: refFrom(values, 'target'), content: text(values, 'content') }
    case 'm1b_replace_comment':
      return { target_ref: refFrom(values, 'target'), content: text(values, 'content'),
               replaces_record_id: text(values, 'replaces_record_id') }
    case 'm1b_withdraw_comment':
      return { review_record_id: text(values, 'review_record_id'), reason: text(values, 'reason') }
    default:
      return {}
  }
}

function envelopeReason(action: MethodAction, values: Record<string, unknown>): string {
  // ActionRequest.reason requires at least 5 characters; a 4-character action
  // label alone (e.g. “发表意见”) is a 422.  Prefer the real human input.
  const candidates = [text(values, 'statement'), text(values, 'reason'), text(values, 'content'), action.label]
  return candidates.find((value) => value.trim().length >= 5) ?? `${action.label}：本人工作台提交`
}

export function methodEnvelope(action: MethodAction, values: Record<string, unknown>, idempotencyKey: string) {
  const envelope: Record<string, unknown> = {
    action_type: action.action_type,
    contract_version: action.contract_version,
    expected_versions: [],
    idempotency_key: idempotencyKey,
    reason: envelopeReason(action, values),
    params: paramsForMethodAction(action.action_type, values),
  }
  if (action.target) {
    // ActionTarget is RevisionRef + expected_version (extra fields forbidden);
    // the exact payload hash lives only in params ExactRefs, never in target.
    envelope.target = { object_id: action.target.object_id, revision_id: action.target.revision_id,
                        expected_version: action.target.expected_version }
  } else {
    // ActionRequest.target is required-but-nullable; omitting the key is a 422.
    envelope.target = null
  }
  return envelope
}

function refLine(ref: unknown): string {
  const value = ref as MethodExactRef | undefined
  return value?.object_id ? `${value.object_id.slice(0, 8)}… · ${value.revision_id?.slice(0, 8) ?? ''}` : ''
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}
function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}
function shortId(value: unknown): string {
  return typeof value === 'string' && value ? `${value.slice(0, 8)}…` : ''
}
function periodLabel(record: Record<string, unknown>): string {
  const start = asText(record.start)
  const end = asText(record.end)
  return start || end ? `${start || '…'} → ${end || '…'}` : '—'
}
function joinList(value: unknown, separator = '；'): string {
  const items = asList(value).map((item) => asText(item)).filter(Boolean)
  return items.length ? items.join(separator) : '—'
}
function refsLine(value: unknown): string {
  const refs = asList(value).map(refLine).filter(Boolean)
  return refs.length ? refs.join('；') : '—'
}

function PcoFacts({ payload }: { payload: Record<string, unknown> }) {
  return (
    <div className="mt-1 space-y-0.5" data-testid="method-pco-facts">
      <p><strong>主业务 Scope：</strong>{asText(payload.primary_scope_id) || '—'} · <strong>期限：</strong>{periodLabel(asRecord(payload.period))}</p>
      <p><strong>结果：</strong>{asText(payload.result_statement) || '—'}</p>
      <p><strong>标准：</strong>{joinList(payload.criteria)}</p>
      <p><strong>确切父级 LTCO：</strong>{refLine(payload.parent_ltco_ref)}</p>
      <p><strong>当前现实：</strong>{asText(payload.current_reality) || '—'}</p>
      <p><strong>预期长期推进：</strong>{asText(payload.expected_lt_advance) || '—'}</p>
      <p><strong>为什么：</strong>{asText(payload.why) || '—'}</p>
    </div>
  )
}

function MissionFacts({ payload }: { payload: Record<string, unknown> }) {
  return (
    <div className="mt-1 space-y-0.5" data-testid="method-mission-facts">
      <p><strong>Owner：</strong>{shortId(payload.owner_principal_id) || '—'} · <strong>主 Scope：</strong>{asText(payload.primary_scope_id) || '—'} · <strong>期限：</strong>{periodLabel(asRecord(payload.period))}</p>
      <p><strong>父级 PCO：</strong>{refLine(payload.parent_pco_ref)}</p>
      <p><strong>为什么：</strong>{asText(payload.why) || '—'}</p>
      <p><strong>要求：</strong>{joinList(payload.requirements)}</p>
      <p><strong>标准：</strong>{joinList(payload.criteria)}</p>
      <p><strong>依据：</strong>{refsLine(payload.evidence_refs)}</p>
    </div>
  )
}

function LtcoFacts({ payload }: { payload: Record<string, unknown> }) {
  return (
    <div className="mt-1 space-y-0.5" data-testid="method-ltco-facts">
      <p><strong>主业务 Scope：</strong>{asText(payload.primary_scope_id) || '—'} · <strong>期限：</strong>{periodLabel(asRecord(payload.period))}</p>
      <p><strong>结果：</strong>{asText(payload.result_statement) || '—'}</p>
      <p><strong>标准：</strong>{joinList(payload.criteria)}</p>
      <p><strong>边界：</strong>{asText(payload.boundary) || '—'} · <strong>horizon：</strong>{asText(payload.horizon) || '—'}</p>
      <p><strong>为什么：</strong>{asText(payload.why) || '—'}</p>
      <p><strong>依据：</strong>{refsLine(payload.baseline_refs)}</p>
    </div>
  )
}

function StrategyFacts({ payload }: { payload: Record<string, unknown> }) {
  const capabilities = asList(payload.required_capabilities).map(asRecord)
  return (
    <div className="mt-1 space-y-0.5" data-testid="method-strategy-facts">
      <p><strong>Strategy：</strong>{asText(payload.title) || '—'} — {asText(payload.statement) || '—'}</p>
      {capabilities.length > 0 && (
        <ul className="list-disc pl-4">
          {capabilities.map((capability) => (
            <li key={asText(capability.capability_id)}>
              <strong>{asText(capability.name)}</strong>（{asText(capability.capability_id)}）· 主 Domain {asText(capability.primary_domain_id) || '—'}
              {' · '}{asText(capability.definition)}
              {asList(capability.analysis_fields).length > 0 ? ` · 分析字段：${joinList(capability.analysis_fields, '、')}` : ''}
            </li>
          ))}
        </ul>
      )}
      {capabilities.length === 0 && <p><strong>Required Capability：</strong>无</p>}
    </div>
  )
}

function ArchitectureFacts({ payload }: { payload: Record<string, unknown> }) {
  const battlefields = asList(payload.battlefields).map(asRecord)
  const domains = asList(payload.domains).map(asRecord)
  return (
    <div className="mt-1 space-y-0.5" data-testid="method-architecture-facts">
      <p><strong>Architecture：</strong>{asText(payload.title) || '—'}</p>
      <p><strong>Battlefield：</strong></p>
      <ul className="list-disc pl-4">
        {battlefields.map((unit) => (
          <li key={asText(unit.unit_id)}>
            <strong>{asText(unit.name)}</strong>（{asText(unit.unit_id)}）· {asText(unit.definition)}
            {' · 战略依据：'}{joinList(unit.strategic_basis)} · 边界：{asText(unit.boundary) || '—'}
            {unit.auth_domain_id ? ` · 授权域 ${shortId(unit.auth_domain_id)}` : ''}
            {unit.current_dri_principal_id ? ` · 当前责任人 ${shortId(unit.current_dri_principal_id)}` : ''}
          </li>
        ))}
      </ul>
      <p><strong>Domain：</strong></p>
      <ul className="list-disc pl-4">
        {domains.map((unit) => (
          <li key={asText(unit.unit_id)}>
            <strong>{asText(unit.name)}</strong>（{asText(unit.unit_id)}）· 责任：{asText(unit.responsibility) || '—'}
            {' · '}{asText(unit.definition)}
            {' · 授权域映射：'}{shortId(unit.auth_domain_id) || '未映射'}
            {' · 当前责任人：'}{shortId(unit.current_dri_principal_id) || '未记录'}
          </li>
        ))}
      </ul>
    </div>
  )
}

const DISPOSITION_LABELS: Record<string, string> = {
  adopted: '采纳', partially_adopted: '部分采纳', not_adopted: '未采纳', unresolved: '未决',
}

function ConfirmRoster({ participants, confirmed }: { participants: Array<Record<string, unknown>>;
                                                      confirmed: string[] }) {
  return (
    <ul className="list-disc pl-4">
      {participants.map((person) => {
        const principal = String(person.principal_id ?? '')
        return <li key={principal}>{principal.slice(0, 8)}… · {confirmed.includes(principal) ? '已确认' : '待确认'}</li>
      })}
    </ul>
  )
}

export function MethodContentPreview({ task }: { task: MethodTask }) {
  const payload = (task.payload ?? {}) as Record<string, unknown>
  const state = (task.method_state ?? {}) as Record<string, unknown>
  if (task.object_type === 'StrategicAgreement') {
    const participants = (payload.participants as Array<Record<string, unknown>>) ?? []
    const confirmed = (state.confirmed_principal_ids as string[]) ?? []
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>Agreement 正文：</strong>{String(payload.statement ?? '')}</p>
        <p className="mt-1"><strong>议题：</strong>{refLine(payload.issue_ref)}</p>
        <p className="mt-1"><strong>签署人：</strong></p>
        <ConfirmRoster participants={participants} confirmed={confirmed} />
        <p className="mt-1"><strong>所附证据：</strong>{(payload.evidence_refs as unknown[] ?? []).map(refLine).join('；') || '无'}</p>
      </div>
    )
  }
  if (task.object_type === 'StrategyUpdateProposal') {
    const change = asRecord(payload.change)
    const strategy = change.strategy ? asRecord(change.strategy) : null
    const architecture = change.architecture ? asRecord(change.architecture) : null
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>提案理由：</strong>{asText(payload.rationale) || '—'}</p>
        <p className="mt-1"><strong>变更类型：</strong>
          {[strategy ? 'Strategy' : '', architecture ? 'Architecture' : ''].filter(Boolean).join(' + ') || '无实际变更对象'}</p>
        <p className="mt-1"><strong>保留与变更基准：</strong>
          {strategy
            ? `变更 Strategy（基准 ${refLine(change.strategy_target_ref) || '无（初始）'}）`
            : `保留 Strategy ${refLine(change.strategy_target_ref)}`}
          {' · '}
          {architecture
            ? `变更 Architecture（基准 ${refLine(change.architecture_target_ref) || '无（初始）'}）`
            : `保留 Architecture ${refLine(change.architecture_target_ref)}`}</p>
        <p className="mt-1"><strong>适用性理由：</strong>{asText(change.applicability_rationale) || '—'}</p>
        {strategy ? <StrategyFacts payload={strategy} /> : null}
        {architecture ? <ArchitectureFacts payload={architecture} /> : null}
      </div>
    )
  }
  if (task.object_type === 'CandidateSet') {
    const differences = asList(payload.unresolved_differences).map(asRecord)
    const dispositions = asList(payload.dispositions).map(asRecord)
    const commitments = task.commitments ?? []
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>候选成员（完整集合）：</strong></p>
        <ul className="list-disc space-y-1 pl-4">
          {(task.members ?? []).map((member) => (
            <li key={member.ref.object_id}>
              <p><strong>{member.object_type} · {member.title}</strong> <span>{refLine(member.ref)}</span></p>
              {member.object_type === 'PCO' ? <PcoFacts payload={member.payload} /> : null}
              {member.object_type === 'Mission' ? <MissionFacts payload={member.payload} /> : null}
            </li>
          ))}
        </ul>
        <p className="mt-1"><strong>本人责任承诺（{commitments.length} / {(task.members ?? []).length}）：</strong></p>
        {commitments.length === 0 ? <p>尚未有具名责任人承诺</p> : (
          <ul className="list-disc pl-4">
            {commitments.map((item) => (
              <li key={item.responsibility_object_id}>{shortId(item.principal_id)} · {item.statement}</li>
            ))}
          </ul>
        )}
        <p className="mt-1"><strong>逐条意见处置：</strong></p>
        {dispositions.length === 0 ? <p>本次窗口没有冻结意见</p> : (
          <ul className="list-disc pl-4">
            {dispositions.map((item) => (
              <li key={asText(item.review_record_id)}>
                意见 {shortId(item.review_record_id)} · {DISPOSITION_LABELS[asText(item.decision)] ?? asText(item.decision)} · {asText(item.rationale)}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1"><strong>未决差异：</strong></p>
        {differences.length === 0 ? <p>无</p> : (
          <ul className="list-disc pl-4">
            {differences.map((item, index) => (
              <li key={index} className={item.critical ? 'text-destructive' : ''}>
                {item.critical ? '关键 · ' : ''}{asText(item.topic)}：{asText(item.statement)}
              </li>
            ))}
          </ul>
        )}
        {differences.some((item) => item.critical)
          ? <p className="mt-1 text-destructive">存在关键未决分歧：本组不能激活，需先重开收敛。</p> : null}
        {asList(payload.notes).length > 0 ? (
          <p className="mt-1"><strong>非阻塞说明：</strong>{joinList(payload.notes)}</p>
        ) : null}
        <p className="mt-1"><strong>处置说明：</strong>{asText(payload.summary) || '—'}</p>
      </div>
    )
  }
  if (task.object_type === 'ReviewWindow') {
    const pco = (payload.pco_refs as unknown[]) ?? []
    const missions = (payload.mission_refs as unknown[]) ?? []
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>冻结目标：</strong>PCO {pco.length} · Mission {missions.length}（{String(payload.period ? JSON.stringify(payload.period) : '')}）</p>
        <p className="mt-1"><strong>LTCO 集合：</strong>{(payload.ltco_refs as unknown[] ?? []).map(refLine).join('；')}</p>
      </div>
    )
  }
  if (task.object_type === 'OperatingState') {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>推荐：</strong>{String(payload.summary ?? '')} · {String(payload.rag ?? '')}</p>
        <p className="mt-1"><strong>依据：</strong>{(payload.baseline_refs as unknown[] ?? []).map(refLine).join('；')}</p>
        <p className="mt-1"><strong>证据：</strong>{(payload.evidence_refs as unknown[] ?? []).map(refLine).join('；') || '无'}</p>
        <p className="mt-1"><strong>证据缺口：</strong>{(payload.data_gaps as string[] ?? []).join('；') || '无'}</p>
      </div>
    )
  }
  if (task.object_type === 'OperatingProblem') {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>问题：</strong>{String(payload.core_question ?? '')} · {String(payload.level ?? '')}</p>
        <p className="mt-1"><strong>依据 State：</strong>{refLine(payload.state_ref)} · 责任任职 {String(payload.responsible_assignment_id ?? '').slice(0, 8)}…</p>
      </div>
    )
  }
  if (task.object_type === 'LTCO') {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>LTCO：</strong>{asText(payload.title) || task.title}</p>
        <LtcoFacts payload={payload} />
      </div>
    )
  }
  if (task.object_type === 'PCO') {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>PCO：</strong>{asText(payload.title) || task.title}</p>
        <PcoFacts payload={payload} />
      </div>
    )
  }
  if (task.object_type === 'Mission') {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>Mission：</strong>{asText(payload.title) || task.title}</p>
        <MissionFacts payload={payload} />
      </div>
    )
  }
  if (task.object_type === 'StrategicIssue') {
    return (
      <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
        <p><strong>议题：</strong>{asText(payload.title) || task.title}</p>
        <p className="mt-1"><strong>核心问题：</strong>{asText(payload.core_question) || '—'} · <strong>分类：</strong>{asText(payload.business_scope) || '—'} / {asText(payload.urgency) || '—'}</p>
        <p><strong>摘要：</strong>{asText(payload.summary) || '—'}</p>
        <p><strong>当前依据：</strong>Strategy {refLine(payload.strategy_ref) || '无'} · Architecture {refLine(payload.architecture_ref) || '无'}</p>
        <p><strong>来源：</strong>{refsLine(payload.source_refs)}</p>
      </div>
    )
  }
  const title = String(payload.title ?? task.title)
  return (
    <div className="rounded-md border border-border bg-muted/30 p-2 text-xs" data-testid={`method-content-${task.object_id}`}>
      <p><strong>{task.object_type}：</strong>{title}</p>
      {payload.result_statement ? <p className="mt-1">{String(payload.result_statement)}</p> : null}
      {payload.statement && task.object_type !== 'StrategicAgreement' ? <p className="mt-1">{String(payload.statement)}</p> : null}
    </div>
  )
}

export function MethodActions({ session, prepare, onError, onExplore }: {
  session: MethodSession
  prepare: (body: Record<string, unknown>) => Promise<void>
  onError: (error: unknown) => void
  onExplore: (objectId: string) => void
}) {
  const [tasks, setTasks] = useState<MethodTask[]>([])
  const [nextAfter, setNextAfter] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [openAction, setOpenAction] = useState<{ task: MethodTask; action: MethodAction } | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState(false)
  const mounted = useRef(true)
  const epoch = useRef(0)
  const controller = useRef<AbortController | null>(null)

  const load = useCallback(async () => {
    const generation = ++epoch.current
    controller.current?.abort()
    const current = new AbortController()
    controller.current = current
    try {
      const response = await fetch(`${BASE}/governance/method/tasks`, {
        credentials: 'same-origin', cache: 'no-store', signal: current.signal,
        headers: { Accept: 'application/json' } })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new ApiError(response.status, data.error?.code ?? 'UNAVAILABLE', '')
      }
      const page = await response.json() as { items: MethodTask[]; next_after: string | null }
      if (mounted.current && generation === epoch.current) {
        setTasks(page.items.map((task) => ({ ...task,
          actions: (task.actions ?? []).filter((action) => BROWSER_ACTIONS.has(action.action_type)) })))
        setNextAfter(page.next_after); setLoading(false); setFailed(false)
      }
    } catch (error) {
      if (!mounted.current || generation !== epoch.current) return
      if (error instanceof DOMException && error.name === 'AbortError') return
      onError(error); setLoading(false); setFailed(true)
    }
  }, [onError])

  useEffect(() => {
    mounted.current = true
    void load()
    const timer = setInterval(() => { if (!document.hidden) void load() }, 5000)
    window.addEventListener('focus', load)
    return () => { mounted.current = false; clearInterval(timer); epoch.current++
      controller.current?.abort(); window.removeEventListener('focus', load) }
  }, [load])

  const start = (task: MethodTask, action: MethodAction) => {
    setOpenAction({ task, action })
    const options = action.options ?? {}
    const initial: Record<string, unknown> = {}
    if (action.object_domain_id) initial.domain_id = action.object_domain_id
    if (options.domain_id) initial.domain_id = options.domain_id
    if (options.state_ref) {
      Object.assign(initial, applyRef({}, 'state', options.state_ref))
    }
    for (const field of FORMS[action.action_type] ?? []) {
      if (field.kind === 'option') {
        initial[`${field.name}__options`] = (options as Record<string, unknown>)[field.source] ?? []
      } else if (field.kind === 'evidence') {
        initial[`${field.name}__options`] = options.evidences ?? []
      } else if (field.kind === 'participants') {
        initial[`${field.name}__options`] = options.participants ?? []
      }
    }
    setValues(initial)
  }
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!openAction || busy) return
    setBusy(true)
    try {
      await prepare(methodEnvelope(openAction.action, values, crypto.randomUUID()))
      setOpenAction(null)
    } catch (error) {
      onError(error)
    } finally { setBusy(false) }
  }
  const setSimple = (name: string, value: unknown) =>
    setValues((current) => ({ ...current, [name]: value }))
  const chooseOption = (field: Extract<Field, { kind: 'option' }>, option: MethodOption) => {
    setValues((current) => {
      let next = { ...current, [field.name]: option.value, [`${field.name}__option`]: option }
      const ref = option.ref ?? option.target_ref
      if (field.keepRefAs && ref) next = applyRef(next, field.keepRefAs, ref)
      if (field.name === 'responsible_assignment_id') next.responsible_assignment_id = option.assignment_id ?? option.value
      return next
    })
  }
  const toggleEvidence = (field: Field, option: MethodOption) =>
    setValues((current) => {
      const selected = exactRefs(current, field.name)
      const exists = option.ref && selected.some((ref) => ref.object_id === option.ref!.object_id
        && ref.revision_id === option.ref!.revision_id)
      return { ...current, [field.name]: exists || !option.ref ? selected
        : [...selected, option.ref] }
    })
  const setRow = (name: string, index: number, field: string, value: string) =>
    setValues((current) => {
      const list = [...rows(current, name)]
      list[index] = { ...list[index], [field]: value }
      return { ...current, [name]: list }
    })
  const addParticipant = (field: Field) =>
    setValues((current) => ({ ...current, [field.name]: [...rows(current, field.name), {}] }))
  const removeRow = (field: Field, index: number) =>
    setValues((current) => ({ ...current, [field.name]: rows(current, field.name).filter((_row, i) => i !== index) }))
  const loadMore = async () => {
    if (!nextAfter) return
    try {
      const response = await fetch(`${BASE}/governance/method/tasks?after=${nextAfter}`, {
        credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      if (!response.ok) throw new ApiError(response.status, 'UNAVAILABLE', '')
      const page = await response.json() as { items: MethodTask[]; next_after: string | null }
      setTasks((current) => [...current, ...page.items.map((task) => ({ ...task,
        actions: (task.actions ?? []).filter((action) => BROWSER_ACTIONS.has(action.action_type)) }))])
      setNextAfter(page.next_after)
    } catch (error) { onError(error) }
  }

  const renderField = (field: Field) => {
    if (field.kind === 'option') {
      const options = optionList(values, field.name)
      const missing = options.length === 0
      return (
        <div key={field.name}>
          <Label htmlFor={`method-${field.name}`}>{field.label}</Label>
          <select id={`method-${field.name}`} required={field.required} disabled={missing}
                  className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                  value={selectedOption(values, field.name)?.value ?? ''}
                  onChange={(event) => {
                    const option = options.find((item) => item.value === event.target.value)
                    if (option) chooseOption(field, option)
                  }}>
            <option value="">{missing ? '当前没有可选的授权对象' : '请选择'}</option>
            {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          {missing && <p className="text-[11px] text-muted-foreground">无可选项时不接受手工 ID/hash；请先在对象详情完成前置步骤。</p>}
        </div>
      )
    }
    if (field.kind === 'evidence') {
      const options = optionList(values, field.name)
      const selected = exactRefs(values, field.name)
      return (
        <div key={field.name}>
          <Label>{field.label}</Label>
          <div className="space-y-1" data-testid={`method-evidence-${field.name}`}>
            {options.length === 0 && <p className="text-[11px] text-muted-foreground">没有可选的确切依据。</p>}
            {options.map((option) => (
              <label key={option.value} className="flex items-center gap-2 text-xs">
                <input type="checkbox"
                       checked={!!option.ref && selected.some((ref) => ref.object_id === option.ref!.object_id
                         && ref.revision_id === option.ref!.revision_id)}
                       onChange={() => toggleEvidence(field, option)} />
                {option.label}
              </label>
            ))}
          </div>
        </div>
      )
    }
    if (field.kind === 'participants') {
      const options = optionList(values, field.name)
      return (
        <div className="space-y-2" data-testid={`method-rows-${field.name}`}>
          {rows(values, field.name).map((row, index) => (
            <div key={index} className="flex flex-wrap items-end gap-2">
              <div className="min-w-[14rem] flex-1">
                <Label className="text-[11px] text-muted-foreground">当前人类参与人</Label>
                <select className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                        value={row.principal_id ?? ''}
                        onChange={(event) => {
                          const option = options.find((item) => item.principal_id === event.target.value)
                          if (!option) return
                          setValues((current) => {
                            const list = [...rows(current, field.name)]
                            list[index] = { principal_id: option.principal_id ?? '', assignment_id: option.assignment_id ?? '',
                                            personal_agent_id: option.personal_agent_id ?? '',
                                            research: list[index]?.research ?? '' }
                            return { ...current, [field.name]: list }
                          })
                        }}>
                  <option value="">请选择</option>
                  {options.map((option) => <option key={option.value} value={option.principal_id}>{option.label}</option>)}
                </select>
              </div>
              <label className="flex items-center gap-1 text-xs">
                <input type="checkbox" checked={row.research === 'true'}
                       onChange={(event) => setRow(field.name, index, 'research', event.target.checked ? 'true' : '')} />
                研究责任
              </label>
              <Button type="button" size="sm" variant="ghost" onClick={() => removeRow(field, index)}>删除</Button>
            </div>
          ))}
          <Button type="button" size="sm" variant="outline" onClick={() => addParticipant(field)}>添加参与人</Button>
        </div>
      )
    }
    if (field.kind === 'select') {
      return (
        <div key={field.name}>
          <Label htmlFor={`method-${field.name}`}>{field.label}</Label>
          <select id={`method-${field.name}`} required={field.required}
                  className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                  value={text(values, field.name)}
                  onChange={(event) => setSimple(field.name, event.target.value)}>
            <option value="">请选择</option>
            {field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
      )
    }
    if (field.kind === 'textarea') {
      return (
        <div key={field.name}>
          <Label htmlFor={`method-${field.name}`}>{field.label}</Label>
          <Textarea id={`method-${field.name}`} required={field.required} placeholder={field.placeholder}
                    value={text(values, field.name)}
                    onChange={(event) => setSimple(field.name, event.target.value)} />
        </div>
      )
    }
    return (
      <div key={field.name}>
        <Label htmlFor={`method-${field.name}`}>{field.label}</Label>
        <Input id={`method-${field.name}`} required={field.required} placeholder={field.placeholder}
               value={text(values, field.name)}
               onChange={(event) => setSimple(field.name, event.target.value)} />
      </div>
    )
  }

  return (
    <div className="space-y-4" data-testid="method-actions">
      <div className="flex items-center justify-between">
        <div>
          <p className="gov-eyebrow">Agreement · 承诺 · 状态 / METHOD 0.4</p>
          <h2 className="text-xl font-semibold">需要本人确认或承诺的事项</h2>
          <p className="text-sm text-muted-foreground">
            只显示当前身份被列为必要参与人、且属于本人责任的事项；确切引用一律从本人可读对象中派生，
            不接受手工 ID/hash；提交前一律预览，核心在校验时重新检查当前任职。
            <span className="ml-1">当前身份：{session.identity.display_name}。</span>
          </p>
        </div>
        <Button variant="outline" onClick={() => void load()}><RefreshCw size={15} />刷新</Button>
      </div>
      {loading && <p>正在读取…</p>}
      {!loading && failed && (
        <Alert>
          <AlertDescription data-testid="method-actions-error">
            方法事项读取失败；这不是“没有事项”。请刷新或重新登录后再判断。
          </AlertDescription>
        </Alert>
      )}
      {!loading && !failed && tasks.length === 0 && (
        <div className="gov-empty" data-testid="method-actions-empty">
          <GitPullRequest size={30} strokeWidth={1.4} />
          <h3>当前没有需要你确认的 0.4 事项</h3>
          <p>Agreement、承诺或正式状态需要你参与时，会显示在这里；起草与复核由 Agent 完成。</p>
        </div>
      )}
      {tasks.map((task) => (
        <section key={task.object_id} className="gov-task-card" data-testid={`method-task-${task.object_id}`}>
          <div className="gov-task-symbol"><GitPullRequest size={22} /></div>
          <div className="gov-task-copy">
            <div className="gov-card-kicker">
              <span>{STATUS[task.phase] ?? task.phase}</span>
              <small>{task.contract_version}</small>
            </div>
            <h3>{task.title}</h3>
            <MethodContentPreview task={task} />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {task.actions.map((action) => BROWSER_ACTIONS.has(action.action_type) && (
                FORMS[action.action_type] ? (
                  <span key={action.action_type} className="inline-flex items-center gap-2">
                    <Button size="sm" variant={action.allowed ? 'outline' : 'ghost'} disabled={!action.allowed}
                            onClick={() => action.allowed && start(task, action)}
                            data-testid={`method-action-${task.object_id}-${action.action_type}`}>
                      {action.label}
                    </Button>
                    {!action.allowed && (
                      <span className="text-[11px] text-muted-foreground" data-testid={`method-blocked-${action.action_type}`}>
                        {REASON_LABELS[action.reason ?? ''] ?? action.reason}
                      </span>
                    )}
                  </span>
                ) : (
                  <Button key={action.action_type} size="sm" variant="ghost"
                          onClick={() => onExplore(task.object_id)}
                          data-testid={`method-action-${task.object_id}-${action.action_type}`}>
                    在对象详情中查看：{action.label}
                  </Button>
                )
              ))}
            </div>
            <p className="mt-1 text-[10.5px] text-muted-foreground">
              正式效力：{task.actions.map((action) => action.formal_effect).join('；')}
            </p>
          </div>
        </section>
      ))}
      {nextAfter && <Button variant="outline" onClick={() => void loadMore()}>加载更多</Button>}
      {openAction && (
        <form className="space-y-3 border-t pt-4" onSubmit={submit}
              data-testid={`method-form-${openAction.action.action_type}`}>
          <h4 className="font-semibold">{openAction.action.label}</h4>
          <MethodContentPreview task={openAction.task} />
          <p className="text-xs text-muted-foreground">
            对象：{openAction.task.title}
            {openAction.action.target ? '（确切版本已锁定；版本变化将拒绝提交）' : '（无对象目标；参数自带 exact 引用）'}
          </p>
          {(FORMS[openAction.action.action_type] ?? []).map((field) => renderField(field))}
          <p className="text-xs text-muted-foreground">
            下一步是预览：确认对象、版本与输入后再提交；本表单不直接写业务。
          </p>
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>准备并预览</Button>
            <Button type="button" variant="ghost" onClick={() => setOpenAction(null)}>取消</Button>
          </div>
        </form>
      )}
    </div>
  )
}
