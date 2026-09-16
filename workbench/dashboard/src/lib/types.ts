/** Typed shapes of the tkos.dashboard/0.1 read contract (subset the UI uses). */

export type Missing = { status: "missing"; reason: string; value?: unknown; field?: string }

export interface ExactRef {
  object_id: string
  revision_id: string
  payload_hash?: string
}

export interface StrategyChoice {
  strategy_id: string
  revision_id: string
  payload_hash: string
  domain_id: string
  domain_name: string | null
  title: string | null
  contract_version: string | null
  record_origin: string | null
  head_recorded_at: string | null
}

export interface Viewer {
  scope_id: string
  principal_id: string
  principal_type: string
  display_name: string
  auth_epoch: number
  assignments: Array<{ domain_id: string; role: string }>
}

export interface GroupAvailability {
  group: string
  available: boolean
  reason: string | null
  historical_available: boolean
  unattached_available: boolean
}

export interface Overview {
  schema_version: string
  read_at: string
  environment: { label: string; synthetic: boolean | null; source?: string }
  viewer: Viewer | null
  strategy_choices: StrategyChoice[]
  selected_strategy_id: string | null
  selection_required: boolean
  groups: GroupAvailability[]
  historical_basis: {
    available: boolean
    reason: string | null
    groups: string[]
    items: Array<{ object_id: string; object_type: string; old_strategy_ref: ExactRef }>
  }
  refresh_interval_seconds: number
  note: string
}

export interface StrategyBasis {
  status: "current" | "historical" | "unattached" | "mixed" | "unrelated" | "unavailable" | "unselected"
  reason: string | null
  selected_strategy_ref: ExactRef | null
  strategy_ref: ExactRef | null
  impact_linked: boolean
  basis_revision: string | null
}

export interface ResponsibilityEntry {
  relation: string
  outcome_id: string | null
  principal: { principal_id: string; display_name: string; principal_type: string; active: boolean } | null
  assignment: {
    assignment_id: string
    role: string
    domain_id: string
    current_assignment_active: boolean
    valid_from: string
    valid_to: string | null
  } | null
  assignment_id: string | null
  appointment: {
    status: "current" | "future" | "expired" | "revoked" | "not_visible" | "not_recorded"
    reason: string | null
    assignments: Array<{ role: string; current?: boolean; future?: boolean; active: boolean;
                         domain_id?: string; valid_to: string | null }>
  }
}

export interface FormalState {
  status: string
  formal: boolean
  authority?: string | null
  applies_to_ref?: ExactRef | null
  lifecycle_status?: string
  note?: string
  human_approved?: boolean
  content_confirmation?: ConfirmationRecord | null
  canonical_ref?: ExactRef | null
  confirmation_record_id?: string | null
  [key: string]: unknown
}

export interface ConfirmationRecord {
  record_id: string
  kind: string
  principal_id: string
  principal: { principal_id: string; display_name: string; principal_type: string; active: boolean } | null
  recorded_at: string
  target_ref: ExactRef
  covered_refs: ExactRef[]
  covers_selected_revision: boolean
  human_confirmation: boolean
  source: string
  content: Record<string, unknown>
  receipt: { receipt_id: string; action_type: string; recorded_at: string } | null
}

export interface ObjectListItem {
  object_id: string
  object_type: string
  domain_id: string
  domain_name: string | null
  title: string | null
  summary?: string | null
  lifecycle_status: string
  object_version: number
  latest_revision_id: string | null
  effective_revision_id: string | null
  created_at: string
  basis: StrategyBasis
  basis_revision_id: string
  formal_state: FormalState
  owner: ResponsibilityEntry | Missing
  dri: ResponsibilityEntry[]
  responsibility?: ResponsibilityEntry[]
  period: { start: string; end: string } | null
  period_source: string | null
  period_status: { status: string; reason: string | null; value?: unknown }
  deadline: string | null
}

export interface ObjectsPage {
  schema_version: string
  read_at: string
  strategy: StrategyChoice | null
  group: string
  basis: string
  items: ObjectListItem[]
  next_cursor: string | null
  loaded_count: number
  has_more: boolean
  hint?: string
}

export interface ResolvedRef extends ExactRef {
  path: string
  object_type: string
  lifecycle_status: string
  title: string | null
  domain_id: string
  payload_hash_matches?: boolean
}

export interface DownstreamEdge {
  object_id: string
  object_type: string
  domain_id: string
  title: string | null
  summary?: string | null
  ref: ExactRef
  matched_fields: string[]
  formal_state: FormalState
  state_summary?: {
    subject_ref: ExactRef | null
    subject_matches_selected_anchor: boolean
    outcome_id: string | null
    outcome: Record<string, unknown> | null
    outcome_status: { status: string; reason: string | null }
    as_of: string | null
    rag: string | null
    summary: string | null
    data_gaps: string[]
    baseline_refs: ExactRef[]
    evidence_refs: ExactRef[]
    baseline_items?: ResolvedRef[]
    evidence_items?: ResolvedRef[]
    formal: boolean
    status: string
    applies_to_ref: ExactRef | null
    canonical_ref: ExactRef | null
    confirmed_by: string | null
  }
}

export interface DownstreamPage {
  items: DownstreamEdge[]
  next_cursor: string | null
  has_more: boolean
  limit: number
  bounded: number
}

export interface OutcomeView {
  outcome_id: string | null
  unit_id?: string
  title?: string
  result_statement?: string
  criteria?: string[]
  dri_principal_id?: string
  owner_principal_id?: string
  outcome?: Record<string, unknown> | null
  outcome_status?: { status: string; reason: string | null }
  contribution?: string
  outcome_ref?: ExactRef
}

export interface Detail {
  schema_version: string
  read_at: string
  environment: { label: string; synthetic: boolean | null }
  viewer: Viewer | null
  object: {
    object_id: string
    object_type: string
    domain_id: string
    domain_name: string | null
    lifecycle_status: string
    object_version: number
    created_at: string
    latest_revision_id: string | null
    effective_revision_id: string | null
  }
  protocol: Record<string, unknown>
  selected_revision: {
    revision_id: string
    object_version: number
    payload_hash: string
    recorded_at: string
    is_latest: boolean
    is_effective: boolean
    selection: "requested" | "effective" | "latest"
  }
  content: Record<string, unknown>
  business: {
    title: string | null
    statement: string | null
    summary: string | null
    boundary: string | null
    deliverable: string | null
    acceptance_criteria: string[]
    period: { start: string; end: string } | null
    period_view: { status: string; reason?: string | null; source?: string; value?: unknown }
    deadline: { kind: string; value: string } | null
    outcomes: OutcomeView[]
    units: Array<Record<string, unknown>>
    supports: OutcomeView[]
    core_question: string | null
    why_material: string | null
    level: string | null
    rag: string | null
    as_of: string | null
    data_gaps: string[]
    findings: string[]
    learnings: string[]
    implications: string[]
    observations: string[]
    recommendation: string | null
    metric: string | null
    value: unknown
    unit: string | null
    correction: { corrects_ref: ExactRef; correction_reason: string } | null
    subject_outcome: { status: string; reason?: string | null; value?: { outcome_id: string; outcome: Record<string, unknown> } }
    raw: Record<string, unknown>
  }
  responsibility: {
    entries: ResponsibilityEntry[]
    owner: ResponsibilityEntry | Missing
    dri: ResponsibilityEntry[]
    note: string
  }
  basis: StrategyBasis
  context_note: string | null
  relations: {
    own_basis_refs: ResolvedRef[]
    support_refs: ResolvedRef[]
    upstream_refs: ResolvedRef[]
    downstream: DownstreamPage
  }
  formal_state: FormalState
  content_confirmation: {
    records: ConfirmationRecord[]
    confirmed_for_selected_revision: boolean
    note: string
  }
  candidates: {
    same_object_only: boolean
    latest: CandidateRevision | Missing
    effective: CandidateRevision | Missing
  }
  history: { items: HistoryRevision[]; next_cursor: string | null }
  evidence: { items: Array<ResolvedRef & { field?: string;
    download?: { available: boolean; object_id?: string; revision_id?: string } }> }
  receipts: { items: Array<Record<string, unknown>>; next_cursor: string | null }
  missing: Array<{ field: string; status: string; reason: string; value?: unknown }>
}

export interface CandidateRevision {
  revision_id: string
  payload_hash: string
  recorded_at: string
  object_version: number
  is_selected: boolean
  changed_fields_vs_selected?: Array<{ field_path: string; before: unknown; after: unknown; change: string }>
}

export interface HistoryRevision {
  revision_id: string
  object_version: number
  payload_hash: string
  recorded_at: string
  is_latest: boolean
  is_effective: boolean
}
