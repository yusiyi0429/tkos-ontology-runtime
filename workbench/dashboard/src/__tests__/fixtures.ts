import type { CatalogObjectItem, CatalogObjectsPage, Detail, ObjectListItem, ObjectsPage,
              OntologyCatalog, Overview, ResponsibilityEntry } from "@/lib/types"

export function overview(overrides: Partial<Overview> = {}): Overview {
  return {
    schema_version: "tkos.dashboard/0.1",
    read_at: "2026-09-16T02:00:00+00:00",
    environment: { label: "synthetic", synthetic: true },
    viewer: { scope_id: "s", principal_id: "p-ceo", principal_type: "human",
              display_name: "合成 CEO", auth_epoch: 1, assignments: [] },
    strategy_choices: [{ strategy_id: "s1", revision_id: "s1r1", payload_hash: "a".repeat(64),
                         domain_id: "d1", domain_name: "公司", title: "战略一",
                         contract_version: "tkos.method/0.3", record_origin: "synthetic",
                         head_recorded_at: "2026-09-16T01:00:00+00:00" }],
    selected_strategy_id: "s1",
    selection_required: false,
    groups: [
      { group: "strategy", available: true, reason: null, historical_available: false, unattached_available: false },
      { group: "architecture", available: true, reason: null, historical_available: false, unattached_available: false },
      { group: "ltco", available: true, reason: null, historical_available: true, unattached_available: false },
      { group: "pco", available: true, reason: null, historical_available: true, unattached_available: false },
      { group: "mission", available: true, reason: null, historical_available: true, unattached_available: false },
      { group: "operating", available: true, reason: null, historical_available: false, unattached_available: true },
    ],
    historical_basis: { available: true, reason: null, groups: ["mission"], items: [] },
    refresh_interval_seconds: 5,
    note: "",
    ...overrides,
  }
}

export function missionItem(id = "m1", overrides: Partial<ObjectListItem> = {}): ObjectListItem {
  return {
    object_id: id,
    object_type: "Mission",
    domain_id: "d1",
    domain_name: "公司",
    title: `任务 ${id}`,
    lifecycle_status: "confirmed",
    object_version: 2,
    latest_revision_id: `${id}-r2`,
    effective_revision_id: `${id}-r2`,
    created_at: "2026-09-16T01:00:00+00:00",
    basis: { status: "current", reason: null, selected_strategy_ref: { object_id: "s1", revision_id: "s1r1" },
             strategy_ref: { object_id: "s1", revision_id: "s1r1" }, impact_linked: false, basis_revision: "effective" },
    basis_revision_id: `${id}-r2`,
    formal_state: { status: "confirmed", formal: true },
    owner: { relation: "mission_owner", outcome_id: null,
             principal: { principal_id: "p1", display_name: "责任人甲", principal_type: "human", active: true },
             assignment: null, assignment_id: null,
             appointment: { status: "current", reason: null, assignments: [] } },
    dri: [],
    period: { start: "2026-09-01T00:00:00+00:00", end: "2026-10-01T00:00:00+00:00" },
    period_source: "pco_ref",
    period_status: { status: "available", reason: null },
    deadline: "2026-10-01T00:00:00+00:00",
    ...overrides,
  } as ObjectListItem
}

export function objectsPage(items: ReturnType<typeof missionItem>[], overrides = {}): ObjectsPage {
  return {
    schema_version: "tkos.dashboard/0.1",
    read_at: "2026-09-16T02:00:00+00:00",
    strategy: null,
    group: "mission",
    basis: "current",
    items,
    next_cursor: null,
    loaded_count: items.length,
    has_more: false,
    ...overrides,
  } as ObjectsPage
}

export function detail(overrides: Partial<Detail> = {}): Detail {
  return {
    schema_version: "tkos.dashboard/0.1",
    read_at: "2026-09-16T02:00:00+00:00",
    environment: { label: "synthetic", synthetic: true },
    viewer: null,
    object: { object_id: "m1", object_type: "Mission", domain_id: "d1", domain_name: "公司",
              lifecycle_status: "confirmed", object_version: 2, created_at: "2026-09-16T01:00:00+00:00",
              latest_revision_id: "m1-r2", effective_revision_id: "m1-r2" },
    protocol: { contract_version: "tkos.method/0.3", interpretation_status: "method_v0_3",
                registration_status: "registered" },
    selected_revision: { revision_id: "m1-r2", object_version: 2, payload_hash: "b".repeat(64),
                         recorded_at: "2026-09-16T01:30:00+00:00", is_latest: true, is_effective: true,
                         selection: "effective" },
    content: { title: "任务 m1" },
    business: {
      title: "任务 m1", statement: null, summary: "摘要", boundary: "无执行授权",
      deliverable: "证据报告", acceptance_criteria: ["原始来源"], period: null,
      period_view: { status: "available", source: "pco_ref",
                     value: { start: "2026-09-01T00:00:00+00:00", end: "2026-10-01T00:00:00+00:00" } },
      deadline: { kind: "hard", value: "2026-10-01T00:00:00+00:00" },
      outcomes: [], units: [],
      supports: [{ outcome_id: "o1", outcome_ref: { object_id: "p1", revision_id: "p1-r1" },
                   contribution: "收集证据", outcome: { outcome_id: "o1", title: "试点证据", criteria: ["三例"] },
                   outcome_status: { status: "available", reason: null } }],
      core_question: null, why_material: null, level: null, rag: null, as_of: null, data_gaps: [],
      findings: [], learnings: [], implications: [], observations: [], recommendation: null,
      metric: null, value: null, unit: null, correction: null,
      subject_outcome: { status: "missing", reason: "no_outcome_recorded" },
      raw: { title: "任务 m1" },
    },
    responsibility: { entries: [], owner: { status: "missing", reason: "owner_not_recorded" },
                      dri: [], note: "" },
    basis: { status: "current", reason: null, selected_strategy_ref: { object_id: "s1", revision_id: "s1r1" },
             strategy_ref: { object_id: "s1", revision_id: "s1r1" }, impact_linked: false,
             basis_revision: "effective" },
    context_note: null,
    relations: { own_basis_refs: [], support_refs: [], upstream_refs: [],
                 downstream: { items: [], next_cursor: null, has_more: false, limit: 25, bounded: 100 } },
    formal_state: { status: "confirmed", formal: true, note: "确认内容在后续建议改变阶段后仍是正式。" },
    content_confirmation: { records: [], confirmed_for_selected_revision: false, note: "" },
    candidates: { same_object_only: true,
                  latest: { revision_id: "m1-r2", payload_hash: "b".repeat(64),
                            recorded_at: "2026-09-16T01:30:00+00:00", object_version: 2, is_selected: true },
                  effective: { revision_id: "m1-r2", payload_hash: "b".repeat(64),
                               recorded_at: "2026-09-16T01:30:00+00:00", object_version: 2, is_selected: true } },
    history: { items: [{ revision_id: "m1-r1", object_version: 1, payload_hash: "c".repeat(64),
                         recorded_at: "2026-09-16T01:00:00+00:00", is_latest: false, is_effective: false },
                       { revision_id: "m1-r2", object_version: 2, payload_hash: "b".repeat(64),
                         recorded_at: "2026-09-16T01:30:00+00:00", is_latest: true, is_effective: true }],
               next_cursor: null },
    evidence: { items: [] },
    receipts: { items: [], next_cursor: null },
    missing: [],
    ...overrides,
  } as Detail
}

const ALL_TYPES_V03 = [
  "Strategy", "StrategicArchitecture", "StrategicAgreement", "StrategicJudgment",
  "StrategyUpdateProposal", "LTCO", "PCO", "Mission", "LTCOReviewAdvice",
  "ReviewWindow", "CandidateSet", "OperatingState", "BusinessFact", "PeriodReview",
  "OperatingProblem", "Signal", "PotentialIssue", "StrategicIssue", "ResearchBrief",
  "ResearchMemo", "ResearchPlan", "ResearchReport", "MeetingMinutes", "MeetingRound",
  "EvidenceAsset", "MethodRun",
]

export function ontologyCatalog(overrides: Partial<OntologyCatalog> = {}): OntologyCatalog {
  const v02 = ALL_TYPES_V03.filter((type) =>
    !["StrategicArchitecture", "OperatingState", "OperatingProblem"].includes(type))
  const v01 = v02.filter((type) => type !== "ResearchBrief")
  const v04 = ["StrategicIssue", "StrategicAgreement", "Strategy", "StrategicArchitecture",
    "StrategyUpdateProposal", "LTCO", "PCO", "Mission", "ReviewWindow", "CandidateSet",
    "OperatingState", "OperatingProblem", "PeriodReview", "MethodRun", "EvidenceAsset"]
  return {
    schema_version: "tkos.dashboard/0.1",
    read_at: "2026-09-16T02:00:00+00:00",
    versions: [
      { contract_version: "tkos.method/0.4", object_types: v04 },
      { contract_version: "tkos.method/0.3", object_types: [...ALL_TYPES_V03] },
      { contract_version: "tkos.method/0.2", object_types: v02 },
      { contract_version: "tkos.method/0.1", object_types: v01 },
    ],
    types: Object.fromEntries(ALL_TYPES_V03.map((type) => [type, { group: null, listable: true }])),
    note: "",
    ...overrides,
  }
}

export function catalogItem(id = "m1", overrides: Partial<CatalogObjectItem> = {}): CatalogObjectItem {
  return {
    object_id: id,
    object_type: "Mission",
    domain_id: "d1",
    domain_name: "公司",
    title: `任务 ${id}`,
    lifecycle_status: "confirmed",
    object_version: 2,
    latest_revision_id: `${id}-r2`,
    effective_revision_id: `${id}-r2`,
    created_at: "2026-09-16T01:00:00+00:00",
    basis_revision_id: `${id}-r2`,
    contract_version: "tkos.method/0.3",
    formal_state: { status: "confirmed", formal: true },
    responsibility: [],
    ...overrides,
  }
}

/** One authorized responsibility relation for catalog rows (participants are
 *  never part of the catalog projection; callers can still pass the relation). */
export function responsibilityEntry(principalId: string, displayName: string,
                                    relation = "mission_owner"): ResponsibilityEntry {
  return {
    relation,
    outcome_id: null,
    principal: { principal_id: principalId, display_name: displayName,
                 principal_type: "human", active: true },
    assignment: null,
    assignment_id: null,
    appointment: { status: "current", reason: null, assignments: [] },
  }
}

export function catalogPage(items: CatalogObjectItem[], overrides = {}): CatalogObjectsPage {
  return {
    schema_version: "tkos.dashboard/0.1",
    read_at: "2026-09-16T02:00:00+00:00",
    object_type: "Mission",
    items,
    next_cursor: null,
    loaded_count: items.length,
    has_more: false,
    ...overrides,
  } as CatalogObjectsPage
}

export function methodMapEntry(overrides: Partial<import("@/lib/types").MethodMapEntry> = {}) {
  return {
    id: "I06",
    business_category: "anchor",
    ssot_name: "Mission",
    business_name: "任务",
    business_maturity: "defined",
    purpose: "把 PCO 转成少数可由一个 Mission Owner 独立承担结果责任的必要结果单元。",
    definition: "从 PCO 的结果要求出发，明确必须完成什么、由谁负责、什么证据算完成。",
    definition_source: { document: "anchors-normalized.json", index: 5 },
    source_ref: { source_id: "B01", table_id: "tbl7EEJUYN3dqpTt", table_name: "02_Anchor",
                  record_ref: "rec28cqIwUbDZm", source_locator: "" },
    runtime_correspondence: "Mission.supports / primary_scope_id",
    runtime_object_types: ["Mission"],
    runtime_support_assessment: "pending_business_close",
    calibration_conclusion: "待业务收口",
    gap: "PDO层级仍开放",
    next_step: "对账上游关系",
    instance_verification: "未核验企业真实实例",
    review_status: "待评审",
    planned_contracts: ["tkos.method/0.4"],
    runtime_support: { assessment: "pending_business_close", compiled: "compiled",
                       compiled_contract_versions: ["tkos.method/0.3"],
                       scope_enabled_contract_versions: ["tkos.method/0.3"],
                       links: [{ object_type: "Mission", contract_versions: ["tkos.method/0.3"],
                                 catalog_path: "/catalog/objects?object_type=Mission" }] },
    runtime_links: [{ object_type: "Mission", contract_versions: ["tkos.method/0.3"],
                      catalog_path: "/catalog/objects?object_type=Mission" }],
    authorized_read_availability: { status: "not_queried", note: "" },
    ...overrides,
  }
}

export function methodMap(overrides: Partial<import("@/lib/types").MethodMap> = {}): import("@/lib/types").MethodMap {
  return {
    schema_version: "tkos.dashboard/0.1",
    read_at: "2026-09-17T02:00:00+00:00",
    source_snapshot: { checked_date: "2026-09-17", scope: "read-only", snapshot_sha256: "a".repeat(64),
                       entry_count: 1, sources: [], note: "" },
    method_definition_map: { role: "documented_business_map",
      documented_contracts: [{ contract_version: "tkos.method/0.4",
                               support_status: "documented_not_compiled" },
                             { contract_version: "tkos.workspace/0.2",
                               support_status: "documented_not_compiled" }],
      note: "" },
    runtime_implementation: { compiled_contract_versions: ["tkos.method/0.3"],
      scope_enabled_contract_versions: ["tkos.method/0.3"],
      scope_registered_contract_versions: ["tkos.method/0.3"],
      documented_contract_states: { "tkos.method/0.4": "documented_not_compiled",
                                    "tkos.workspace/0.2": "documented_not_compiled" },
      note: "" },
    availability: { mode: "none", queried_object_types: 0, note: "" },
    inventory_counts: { by_business_category: { anchor: 10 }, by_runtime_support_assessment: {}, note: "" },
    entries: [methodMapEntry()],
    notes: [],
    ...overrides,
  }
}

const UNIMPLEMENTED = {
  runtime_support_assessment: "not_implemented",
  gap: "0.3 未交付该对象；按当前资料标注范围",
  runtime_object_types: [] as string[],
  runtime_support: { assessment: "not_implemented", compiled: "no_runtime_object_type",
                     compiled_contract_versions: [] as string[],
                     scope_enabled_contract_versions: [] as string[], links: [] },
  runtime_links: [] as Array<{ object_type: string; contract_versions: string[]; catalog_path: string }>,
  planned_contracts: [] as string[],
  authorized_read_availability: { status: "not_applicable", note: "" },
}

/** All four business categories with unimplemented + reference concepts. */
export function methodMapAll(): import("@/lib/types").MethodMap {
  const anchor = methodMapEntry()
  const play = methodMapEntry({ id: "I07", ssot_name: "Play", business_name: "打法",
    business_category: "anchor", business_maturity: "defined",
    purpose: "为一个已确认 Mission 选择一套有取舍、有因果逻辑、可被对齐和验证的赢法。",
    definition: "从 Mission 的结果要求、关键约束和 Evidence 出发，形成可验证的赢法。",
    definition_source: { document: "anchors-normalized.json", index: 6 }, ...UNIMPLEMENTED })
  const plan = methodMapEntry({ id: "I08", ssot_name: "Human + AI Plan", business_name: "人机协同工作计划",
    business_category: "anchor", business_maturity: "defined",
    purpose: "把 Play 变成真实可运行的人 + Agent 工作系统，明确必须发生的工作与分工。",
    definition: "从 Play 的 Key Moves、Quality Bar 和 Milestones 出发，明确人机分工与交接。",
    definition_source: { document: "anchors-normalized.json", index: 7 }, ...UNIMPLEMENTED })
  const missionRef = methodMapEntry({ id: "I11", ssot_name: "Company Mission", business_name: "公司使命",
    business_category: "reference", business_maturity: "to_define",
    purpose: "长期定义公司存在的根本目的，约束 Strategy 与重大经营选择。",
    definition: "待定义：Mission 的正式内容边界、与 Vision / Strategy 的关系。",
    definition_source: { document: "references-normalized.json", index: 0 }, ...UNIMPLEMENTED })
  const artifact = methodMapEntry({ id: "I19", ssot_name: "Company Period Review", business_name: "公司周期复盘报告",
    business_category: "business_artifact", business_maturity: "defined",
    purpose: "在下一周期经营承诺形成前，让 CEO 快速看清本周期经营结果、原因与含义。",
    definition: "调用最新经营状态与 Canonical RAG，对照本周期 PCO 识别结果与差距。",
    definition_source: { document: "artifacts-normalized.json", index: 0 } })
  const record = methodMapEntry({ id: "I30", ssot_name: "Actual Result", business_name: "实际结果",
    business_category: "evidence_runtime_record", business_maturity: "partial",
    purpose: "说明实际发生的经营结果。",
    definition: "Source / As-of / Metric or result definition / Related Anchor / Traceability",
    definition_source: { document: "records-normalized.json", index: 0 } })
  return methodMap({
    entries: [anchor, play, plan, missionRef, artifact, record],
    source_snapshot: { checked_date: "2026-09-17", scope: "read-only", snapshot_sha256: "b".repeat(64),
                       entry_count: 44, sources: [], note: "" },
    inventory_counts: { by_business_category: { anchor: 10, reference: 8, business_artifact: 11,
                                                evidence_runtime_record: 15 },
                        by_runtime_support_assessment: {}, note: "" },
  })
}
