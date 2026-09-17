/** Chinese business labels.  The UI shows these, never raw API enum text. */

export const GROUP_LABELS: Record<string, string> = {
  strategy: "战略",
  architecture: "责任结构 Architecture",
  ltco: "LTCO 长期目标",
  pco: "PCO 当期目标",
  mission: "Mission 任务",
  operating: "经营运行",
}

export const GROUP_HINTS: Record<string, string> = {
  strategy: "当前正式生效的战略",
  architecture: "战略各责任域的定义与边界",
  ltco: "长期经营目标（周期以记录为准）",
  pco: "当期目标与责任人",
  mission: "承接 PCO 的当前任务",
  operating: "状态、事实、复盘与问题",
}

export const TYPE_LABELS: Record<string, string> = {
  Strategy: "战略",
  StrategicArchitecture: "责任结构",
  LTCO: "LTCO 长期目标",
  PCO: "PCO 当期目标",
  Mission: "Mission 任务",
  OperatingState: "经营状态",
  BusinessFact: "经营事实",
  PeriodReview: "周期复盘",
  OperatingProblem: "经营问题",
  ReviewWindow: "核对窗口",
  CandidateSet: "候选集合",
  StrategicJudgment: "战略判断",
  EvidenceAsset: "原始证据",
  StrategicIssue: "战略议题",
  PotentialIssue: "候选议题",
  StrategicAgreement: "战略协议",
  Signal: "信号",
  ResearchBrief: "研究简报",
  ResearchMemo: "研究备忘",
  ResearchPlan: "研究计划",
  ResearchReport: "研究报告",
  MeetingMinutes: "会议纪要",
  MeetingRound: "会议轮次",
  MethodRun: "方法运行",
  LTCOReviewAdvice: "LTCO 审视建议",
  StrategyUpdateProposal: "战略更新提案",
}

export const VIEW_LABELS: Record<string, string> = {
  map: "本体地图",
  definitions: "业务定义",
  graph: "业务关系图",
}

export const RULES_VERSION_LABELS: Record<string, string> = {
  "0.1": "业务规则 0.1",
  "0.2": "业务规则 0.2",
  "0.3": "业务规则 0.3",
}

export const BASIS_LABELS: Record<string, string> = {
  all: "当前+历史依据",
  current: "当前依据",
  historical: "历史依据",
  mixed: "跨依据",
  unattached: "未关联",
  unrelated: "其他战略域",
  unavailable: "依据不可用",
  unselected: "未选择战略",
}

export const BASIS_REASON_LABELS: Record<string, string> = {
  recorded_other_strategy: "仍引用上一版战略",
  different_strategy_domain: "属于其他独立战略域",
  topic_only_subject: "主题型事实，未关联任务",
  strategy_basis_not_recorded: "未记录战略依据",
  strategy_basis_unavailable: "依据当前不可读",
  pco_basis_unavailable: "PCO 依据当前不可读",
  subject_unavailable: "对象依据当前不可读",
  state_basis_unavailable: "状态依据当前不可读",
  review_targets_not_recorded: "复盘未记录目标",
  review_spans_strategy_basis: "复盘跨多个战略依据",
  type_has_no_strategy_basis: "该类型没有战略依据",
  basis_cycle: "依据引用成环",
  referenced_revision_unavailable: "引用的精确版本当前不可读",
}

export const FORMAL_LABELS: Record<string, string> = {
  effective: "正式生效",
  confirmed: "已确认",
  superseded: "已被取代",
  superseded_confirmed: "历史确认版本",
  recommendation: "建议待确认",
  proposed: "待确认",
  under_review: "核对中",
  candidate: "候选待确认",
  draft: "草稿",
  returned: "已退回",
  recorded: "已记录",
  open: "开放",
  historical: "历史版本",
  transferred: "已移交",
  resolved: "已解决",
  no_further_action: "无需继续处理",
  not_recorded: "未记录",
}

export const RAG_LABELS: Record<string, string> = {
  green: "正常",
  yellow: "关注",
  red: "风险",
  unknown: "未知",
}

export const REVIEW_KIND_LABELS: Record<string, string> = {
  candidate_set_confirmation: "整组目标确认",
  ltco_confirmation: "LTCO 确认",
  strategy_update_confirmation: "战略更新确认",
  architecture_confirmation: "责任结构确认",
  state_confirmation: "经营状态确认",
  problem_closure: "经营问题关闭",
  strategic_agreement_confirmation: "战略协议确认",
  meeting_minutes_confirmation: "纪要确认",
}

export const RELATION_LABELS: Record<string, string> = {
  mission_owner: "任务 Owner",
  owner: "Owner",
  outcome_dri: "成果 DRI",
  outcome_owner: "成果 Owner",
  participant: "参与人",
  responsible: "问题责任人",
  strategy_unit_owner: "责任项 Owner",
}

export const APPOINTMENT_LABELS: Record<string, string> = {
  current: "当前任职",
  future: "任职尚未开始",
  expired: "任职已过期",
  revoked: "任职已撤销",
  not_visible: "任职域当前不可读",
  not_recorded: "未记录任职",
}

export const DEADLINE_LABELS: Record<string, string> = {
  hard: "硬期限",
  decision: "决策期限",
  feedback: "评论截止",
}

export const ROLE_LABELS: Record<string, string> = {
  CEO: "公司 CEO",
  DOMAIN_DRI: "业务域 DRI",
  MISSION_DRI: "任务 DRI",
  VERIFIER: "独立验收人",
  IC: "执行负责人（IC）",
  AGENT: "Agent",
  CEO_AGENT: "CEO Agent",
  CO_AGENT: "协同 Agent",
  PERSONAL_AGENT: "个人 Agent",
}

export const REFERENCE_FIELD_LABELS: Record<string, string> = {
  strategy_ref: "战略依据",
  architecture_ref: "责任结构",
  pco_ref: "PCO 目标",
  ltco_ref: "LTCO 目标",
  subject_ref: "目标对象",
  state_ref: "经营状态",
  state_refs: "经营状态",
  target_refs: "复盘目标",
  fact_refs: "经营事实",
  corrects_ref: "更正的原始事实",
  source_ref: "来源证据",
  source_refs: "来源材料",
  evidence_refs: "证据",
  baseline_refs: "状态基准",
  material_refs: "材料",
  supports: "承接关系",
  outcome_ref: "Outcome",
  window_ref: "核对窗口",
  previous_window_ref: "上一核对窗口",
  issue_ref: "所属战略议题", potential_issue_ref: "来源候选议题",
  memo_ref: "研究澄清依据", plan_ref: "研究计划依据", report_ref: "研究报告依据",
  brief_ref: "研究简报依据", meeting_ref: "所属会议", minutes_ref: "会议纪要依据",
  agreement_ref: "战略共识依据", source_agreement_ref: "战略共识来源",
  source_proposal_ref: "更新提案来源", advice_ref: "目标审视建议",
  period_review_ref: "周期复盘依据", direct_source_refs: "直接来源", signal_refs: "来源信号",
}

export function referenceLabel(path: string): string {
  const normalized = path.replace(/^\//, "").replace(/\/\d+\//g, "/")
  const last = normalized.split("/").filter(Boolean).pop() ?? path
  return REFERENCE_FIELD_LABELS[last] ?? "记录引用"
}

export const ACTION_LABELS: Record<string, string> = {
  m1b_confirm_candidates: "整组目标确认",
  m1b_confirm_ltco: "长期目标确认",
  m1b_propose_ltco: "长期目标提出",
  m1b_revise_ltco: "长期目标修订",
  m1b_return_ltco: "长期目标退回",
  m1b_draft_pco: "当期目标拟定",
  m1b_revise_pco: "当期目标修订",
  m1b_draft_mission: "任务拟定",
  m1b_revise_mission: "任务修订",
  m1b_open_window: "发起共同核对",
  m1b_comment: "核对意见",
  m1b_withdraw_comment: "撤回意见",
  m1b_close_window: "结束核对",
  m1b_resolve_window: "形成候选结论",
  m1b_reopen_window: "重开核对",
  m1b_reopen_candidates: "重新确认依据",
  m1b_record_fact: "记录经营事实",
  m1b_correct_fact: "更正经营事实",
  m1b_generate_review: "生成周期复盘",
  m1b_regenerate_review: "重新生成周期复盘",
  m1b_advise_ltco: "长期目标建议",
  m1a_confirm_update: "战略更新确认",
  m1a_propose_update: "战略更新提出",
  m1a_review_update: "战略更新影响审查",
  m1a_confirm_agreement: "战略协议确认",
  m1a_confirm_strategic_issue: "战略议题立项",
  m1a_open_potential_issue: "候选议题提出",
  m1a_record_signal: "信号记录",
  m1a_publish_report: "研究报告发布",
  m1a_submit_report: "研究报告提交",
  m1a_publish_memo: "备忘发布",
  m1a_open_meeting: "会议召开",
  m1a_publish_minutes: "纪要发布",
  method_propose_state: "经营状态建议",
  method_confirm_state: "经营状态确认",
  method_open_problem: "经营问题开启",
  method_revise_problem: "经营问题修订",
  method_close_problem: "经营问题关闭",
  method_propose_architecture: "责任结构提出",
  method_revise_architecture: "责任结构修订",
  method_confirm_architecture: "责任结构确认",
  upload_evidence: "上传原始证据",
  workspace_create: "工作面创建",
}

export function actionLabel(action: string | null | undefined): string {
  if (!action) return "未记录动作"
  return ACTION_LABELS[action] ?? "已记录动作"
}

const FIELD_LABELS: Record<string, string> = {
  title: "标题", statement: "陈述", summary: "摘要", description: "说明", analysis: "分析",
  deliverable: "交付物", boundary: "边界", acceptance_criteria: "验收标准",
  hard_deadline: "硬期限", decision_deadline: "决策期限", feedback_deadline: "评论截止",
  period: "周期", start: "开始", end: "结束", rag: "状态评级", as_of: "观察时点",
  data_gaps: "数据缺口", findings: "发现", learnings: "学习", implications: "影响",
  observations: "观察", core_question: "核心问题", why_material: "为何重要", level: "层级",
  metric: "指标", value: "取值", unit: "单位", outcomes: "目标结果", unit_outcomes: "目标结果",
  units: "责任定义项", supports: "承接关系", contribution: "贡献", criteria: "标准",
  result_statement: "结果陈述", owner_principal_id: "Owner", dri_principal_id: "DRI",
  participants: "参与人", primary_scope_id: "主责任项", name: "名称", definition: "定义",
  strategic_basis: "战略依据", interfaces: "接口", unit_type: "定义项类型", unit_id: "定义项",
  strategy_ref: "战略依据", architecture_ref: "责任结构", pco_ref: "PCO 目标",
  ltco_ref: "LTCO 目标", subject_ref: "目标对象", state_ref: "经营状态",
  source_ref: "来源证据", corrects_ref: "更正的原始事实", correction_reason: "更正原因",
  generation_version: "生成版本", review_id: "复盘编号", fact_id: "事实编号",
}

export function businessFieldLabel(path: string): string {
  const clean = path.replace(/^\//, "")
  const parts = clean.split("/").filter((part) => part && !/^\d+$/.test(part))
  const last = parts[parts.length - 1] ?? clean
  const parent = parts[parts.length - 2]
  const label = FIELD_LABELS[last] ?? FIELD_LABELS[parent] ?? "字段"
  return parts.length > 1 ? `${label} · ${parts.slice(0, -1).map((p) => FIELD_LABELS[p] ?? p).join(" ")}` : label
}

/** Business wording for the formal state of one exact revision. */
export function formalBusinessText(objectType: string, status: string, formal: boolean): string {
  if (objectType === "Strategy") {
    return formal ? "正式生效" : status === "superseded" ? "已被新版本取代" : "尚未生效"
  }
  if (objectType === "StrategicArchitecture") {
    return formal ? "已确认，责任结构生效" : status === "proposed" ? "提出待确认" : "尚未生效"
  }
  if (objectType === "LTCO") {
    if (formal) return "已确认，长期目标生效"
    return { draft: "草稿，尚未提交确认", returned: "已退回修订", under_review: "共同核对中，尚未生效",
             candidate: "候选待确认" }[status] ?? "尚未生效"
  }
  if (objectType === "PCO") {
    if (formal) return "已确认，当期目标生效"
    return { draft: "草稿，尚未提交核对", under_review: "共同核对中，尚未生效",
             candidate: "候选待确认" }[status] ?? "尚未生效"
  }
  if (objectType === "Mission") {
    if (formal) return "已确认，待执行承接"
    return { draft: "草稿，尚未提交核对", under_review: "共同核对中，尚未生效",
             candidate: "候选待确认" }[status] ?? "尚未生效"
  }
  if (objectType === "OperatingState") {
    if (formal) return "已确认的正式状态"
    return { recommendation: "新建议待确认", superseded_confirmed: "历史确认版本，已被新状态取代",
             proposed: "建议待确认" }[status] ?? "尚未确认"
  }
  if (objectType === "BusinessFact") return "已记录"
  if (objectType === "PeriodReview") return "Agent 复盘（无需人工审批）"
  if (objectType === "OperatingProblem") {
    return { open: "开放跟踪中", transferred: "已移交战略议题（尚未解决）",
             resolved: "已解决", no_further_action: "无需继续处理",
             historical: "历史版本" }[status] ?? "状态未记录"
  }
  return FORMAL_LABELS[status] ?? "状态未记录"
}

export function candidateLabel(candidate: { revision_id: string }, effectiveId: string | null,
                               latestId: string | null): { label: string; tone: string } {
  if (latestId && candidate.revision_id === latestId && effectiveId
      && candidate.revision_id !== effectiveId) {
    return { label: "待确认候选", tone: "border-sky-300 bg-sky-50 text-sky-900" }
  }
  if (effectiveId && candidate.revision_id === effectiveId) {
    return { label: candidate.revision_id === latestId ? "当前正式版本（最新）" : "当前正式版本",
             tone: "border-emerald-300 bg-emerald-50 text-emerald-800" }
  }
  if (!effectiveId) return { label: "草稿版本", tone: "border-border bg-muted text-muted-foreground" }
  return { label: "历史版本", tone: "border-amber-300 bg-amber-50 text-amber-900" }
}

export const BASIS_SECTIONS: Array<{ statuses: string[]; title: string; hint: string }> = [
  { statuses: ["current"], title: "当前依据", hint: "与所选战略精确匹配" },
  { statuses: ["historical", "mixed"], title: "历史依据（旧 Strategy）",
    hint: "保留旧战略依据，效力以各对象状态为准" },
  { statuses: ["unattached"], title: "未关联（主题型事实）", hint: "未记录任务关联" },
  { statuses: ["unavailable"], title: "依据当前不可读", hint: "不推断、不补造" },
]
