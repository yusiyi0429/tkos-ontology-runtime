import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { MethodActions, methodEnvelope, paramsForMethodAction } from "@/MethodActions"

const BASE = "/dashboard/api/v1"

const session = { identity: { principal_id: "p1", display_name: "合成 CEO" }, csrf: "c" }

function action(overrides: Record<string, unknown> = {}) {
  return {
    action_type: "m1a_confirm_agreement",
    label: "确认 Agreement（本人）",
    formal_effect: "agreement_confirmation_record",
    allowed: true,
    reason: null,
    target: { object_id: "o1", revision_id: "r1", payload_hash: "a".repeat(64), expected_version: 4 },
    contract_version: "tkos.method/0.4",
    ...overrides,
  }
}

function defaultTask(overrides: Record<string, unknown> = {}) {
  return { object_id: "o1", object_type: "StrategicAgreement", title: "战略议题 1 · Agreement",
           phase: "awaiting_confirmation", contract_version: "tkos.method/0.4",
           actions: [action()], ...overrides }
}

function setup(items = [defaultTask()]) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    expect(url.startsWith(`${BASE}/governance/method/tasks`)).toBe(true)
    return Promise.resolve(new Response(JSON.stringify({ items, next_after: null }),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  const prepare = vi.fn(async (_body: Record<string, unknown>) => undefined)
  const onError = vi.fn()
  const onExplore = vi.fn()
  render(<MethodActions session={session} prepare={prepare} onError={onError} onExplore={onExplore} />)
  return { prepare, onError, onExplore }
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it("lists pending 0.4 confirmations and previews a strict envelope", async () => {
  const { prepare } = setup()
  await screen.findByTestId("method-task-o1")
  fireEvent.click(screen.getByTestId("method-action-o1-m1a_confirm_agreement"))
  fireEvent.change(await screen.findByLabelText("确认说明"), { target: { value: "本人确认该版本" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  const envelope = prepare.mock.calls[0][0]
  expect(envelope.action_type).toBe("m1a_confirm_agreement")
  expect(envelope.contract_version).toBe("tkos.method/0.4")
  expect(envelope.target).toEqual({ object_id: "o1", revision_id: "r1", expected_version: 4 })
  expect(Object.keys(envelope.target as Record<string, unknown>)).not.toContain("payload_hash")
  expect(envelope.params).toEqual({ statement: "本人确认该版本" })
  expect(String(envelope.idempotency_key).length).toBeGreaterThanOrEqual(16)
})

it("keeps an optional state correction pair explicit", () => {
  const base = { ...action({ action_type: "method_confirm_state" }) }
  const plain = methodEnvelope(base, { reason: "复核依据" }, "k".repeat(16))
  expect(plain.params).toEqual({ reason: "复核依据" })
  const corrected = methodEnvelope(base, { reason: "复核依据", summary: "更新说明", rag: "yellow" }, "k".repeat(16))
  expect(corrected.params).toEqual({ reason: "复核依据", summary: "更新说明", rag: "yellow" })
})

it("splits non-blocking notes for whole-set activation", () => {
  const base = action({ action_type: "m1b_activate_candidates" })
  const envelope = methodEnvelope(base, { statement: "整组确认", notes: "说明一\n\n说明二 " }, "k".repeat(16))
  expect(envelope.params).toEqual({ statement: "整组确认", notes: ["说明一", "说明二"] })
})

it("never sends a reason shorter than the ActionRequest minimum", () => {
  const comment = action({ action_type: "m1b_comment", label: "发表意见" })
  const envelope = methodEnvelope(comment, {
    target_object_id: "o1", target_revision_id: "r1", target_payload_hash: "a".repeat(64),
    content: "短" }, "k".repeat(16))
  expect(String(envelope.reason).trim().length).toBeGreaterThanOrEqual(5)
  expect(String(envelope.reason)).toContain("发表意见")
})

it("derives the replacement target from an opinion option that only exposes target_ref", async () => {
  const target = { object_id: "pco9", revision_id: "rev9", payload_hash: "9".repeat(64) }
  const replace = action({ action_type: "m1b_replace_comment", label: "替代本人意见",
    options: { opinions: [{ value: "rec-9", label: "旧意见", target_ref: target }] } })
  const { prepare } = setup([defaultTask({ object_type: "ReviewWindow", phase: "open", actions: [replace] })])
  await screen.findByTestId("method-task-o1")
  fireEvent.click(screen.getByTestId("method-action-o1-m1b_replace_comment"))
  await screen.findByTestId("method-form-m1b_replace_comment")
  fireEvent.change(screen.getByLabelText("被替代的本人有效意见"), { target: { value: "rec-9" } })
  fireEvent.change(screen.getByLabelText("意见内容"), { target: { value: "替代后的意见内容" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  const envelope = prepare.mock.calls[0][0] as { params: Record<string, unknown> }
  expect(envelope.params.target_ref).toEqual(target)
  expect(envelope.params.replaces_record_id).toBe("rec-9")
})

it("renders a strict form for a verified human action", async () => {
  const commit = action({ action_type: "m1b_commit_candidate", label: "提交本人责任承诺" })
  const { prepare, onExplore } = setup([defaultTask({ actions: [commit] })])
  await screen.findByTestId("method-task-o1")
  fireEvent.click(screen.getByTestId("method-action-o1-m1b_commit_candidate"))
  expect(await screen.findByTestId("method-form-m1b_commit_candidate")).toBeInTheDocument()
  expect(onExplore).not.toHaveBeenCalled()
  expect(prepare).not.toHaveBeenCalled()
})

it("never renders an Agent-only action, even if a projection leaks one", async () => {
  const unknown = action({ action_type: "m1a_transfer_problem", label: "战略移交（Agent）" })
  const { prepare, onExplore } = setup([defaultTask({ actions: [unknown] })])
  await screen.findByTestId("method-task-o1")
  expect(screen.queryByTestId("method-action-o1-m1a_transfer_problem")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /战略移交/ })).not.toBeInTheDocument()
  expect(onExplore).not.toHaveBeenCalled()
  expect(prepare).not.toHaveBeenCalled()
})

it("shows a complete Agreement roster with confirmation state before reconfirming", async () => {
  const agreement = defaultTask({ payload: { statement: "共同结论正文",
    issue_ref: { object_id: "i1", revision_id: "ir1", payload_hash: "c".repeat(64) },
    participants: [{ principal_id: "p1", assignment_id: "a1", personal_agent_id: null },
                   { principal_id: "p2", assignment_id: "a2", personal_agent_id: "g2" }],
    evidence_refs: [{ object_id: "e1", revision_id: "er1", payload_hash: "d".repeat(64) }] },
    method_state: { phase: "awaiting_confirmation", confirmed_principal_ids: ["p1"] } })
  setup([agreement])
  await screen.findByTestId("method-task-o1")
  const preview = screen.getByTestId("method-content-o1")
  expect(preview).toHaveTextContent("共同结论正文")
  expect(preview).toHaveTextContent("已确认")
  expect(preview).toHaveTextContent("待确认")
})

it("shows a blocked activation with the concrete missing reason and no submit path", async () => {
  const blocked = action({ action_type: "m1b_activate_candidates", label: "整组激活候选集合",
                            allowed: false, reason: "missing_commitment" })
  const { prepare } = setup([defaultTask({ object_type: "CandidateSet", phase: "pending",
    payload: { title: "候选", summary: "整组说明",
               unresolved_differences: [{ topic: "证据", statement: "不足", critical: true }] },
    members: [{ ref: { object_id: "m1", revision_id: "mr1", payload_hash: "a".repeat(64) },
                object_type: "PCO", title: "周期结果", payload: {} }],
    commitments: [], actions: [blocked] })])
  await screen.findByTestId("method-task-o1")
  expect(screen.getByTestId("method-blocked-m1b_activate_candidates")).toHaveTextContent("尚缺具名 DRI/Owner 本人承诺")
  const button = screen.getByTestId("method-action-o1-m1b_activate_candidates")
  expect(button).toBeDisabled()
  fireEvent.click(button)
  expect(prepare).not.toHaveBeenCalled()
  expect(screen.getByTestId("method-content-o1")).toHaveTextContent("关键未决分歧")
})

it("derives an exact comment target from authorized options without hash inputs", async () => {
  const comment = action({ action_type: "m1b_comment", label: "发表意见",
    options: { targets: [{ value: "o2:r2", label: "PCO · 周期结果",
                           ref: { object_id: "o2", revision_id: "r2", payload_hash: "e".repeat(64) } }] } })
  const { prepare } = setup([defaultTask({ object_type: "ReviewWindow", phase: "open", actions: [comment] })])
  await screen.findByTestId("method-task-o1")
  fireEvent.click(screen.getByTestId("method-action-o1-m1b_comment"))
  await screen.findByTestId("method-form-m1b_comment")
  expect(screen.queryByLabelText(/payload_hash/)).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/object_id/)).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText("评论对象（窗口冻结的确切目标）"), { target: { value: "o2:r2" } })
  fireEvent.change(screen.getByLabelText("意见内容"), { target: { value: "保留但补充证据" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  expect(prepare.mock.calls[0][0].params).toEqual({
    target_ref: { object_id: "o2", revision_id: "r2", payload_hash: "e".repeat(64) },
    content: "保留但补充证据" })
})

it("derives the caller's own responsibility for a commitment", async () => {
  const commit = action({ action_type: "m1b_commit_candidate", label: "提交本人责任承诺",
    options: { responsibilities: [{ value: "p1:pr1", label: "PCO · 本人责任",
                                    ref: { object_id: "p1", revision_id: "pr1", payload_hash: "f".repeat(64) } }] } })
  const { prepare } = setup([defaultTask({ object_type: "CandidateSet", phase: "pending", actions: [commit] })])
  await screen.findByTestId("method-task-o1")
  fireEvent.click(screen.getByTestId("method-action-o1-m1b_commit_candidate"))
  await screen.findByTestId("method-form-m1b_commit_candidate")
  fireEvent.change(screen.getByLabelText("本人责任（只列本人 DRI/Owner 责任）"), { target: { value: "p1:pr1" } })
  fireEvent.change(screen.getByLabelText("本人承诺说明"), { target: { value: "本人承担" } })
  fireEvent.click(screen.getByRole("button", { name: "准备并预览" }))
  await waitFor(() => expect(prepare).toHaveBeenCalledTimes(1))
  const envelope = prepare.mock.calls[0][0] as { params: { responsibility_ref: Record<string, string> } }
  expect(envelope.params.responsibility_ref).toEqual(
    { object_id: "p1", revision_id: "pr1", payload_hash: "f".repeat(64) })
})

it("offers Unknown as an explicit human state correction", async () => {
  const confirm = action({ action_type: "method_confirm_state", label: "确认正式经营状态" })
  setup([defaultTask({ object_type: "OperatingState", phase: "proposed",
    payload: { summary: "推荐", rag: "unknown", baseline_refs: [], evidence_refs: [], data_gaps: ["缺口"] },
    actions: [confirm] })])
  await screen.findByTestId("method-task-o1")
  fireEvent.click(screen.getByTestId("method-action-o1-method_confirm_state"))
  const select = await screen.findByLabelText("修正评级（与说明同时填写）")
  expect(select).toHaveTextContent("unknown")
  expect(screen.getAllByTestId("method-content-o1")[0]).toHaveTextContent("证据缺口")
})

it("renders full PCO and Mission business fields for candidate review", async () => {
  const pco = { object_id: "pco1", revision_id: "pr1", payload_hash: "a".repeat(64) }
  const mission = { object_id: "mission1", revision_id: "mr1", payload_hash: "b".repeat(64) }
  const blocked = action({ action_type: "m1b_activate_candidates", label: "整组激活候选集合",
                            allowed: false, reason: "missing_commitment" })
  setup([defaultTask({ object_type: "CandidateSet", phase: "pending", actions: [blocked],
    payload: { title: "候选集合", summary: "保留双结果",
      dispositions: [{ review_record_id: "record-12345678", decision: "adopted", rationale: "采纳该意见" }],
      unresolved_differences: [{ topic: "证据充分性", statement: "尚缺独立来源", critical: true }],
      notes: ["非阻塞备注"] },
    members: [
      { ref: pco, object_type: "PCO", title: "周期结果 A", payload: {
        title: "周期结果 A", primary_scope_id: "scope-a",
        period: { start: "2026-01-01T00:00:00Z", end: "2026-02-01T00:00:00Z" },
        parent_ltco_ref: { object_id: "ltco1", revision_id: "lr1", payload_hash: "c".repeat(64) },
        architecture_ref: { object_id: "arch1", revision_id: "ar1", payload_hash: "d".repeat(64) },
        strategy_ref: { object_id: "st1", revision_id: "sr1", payload_hash: "e".repeat(64) },
        current_reality: "当前现实描述", result_statement: "周期结果描述",
        criteria: ["标准一", "标准二"], expected_lt_advance: "推进长期结果", why: "周期必要性" } },
      { ref: mission, object_type: "Mission", title: "必要结果单元 M", payload: {
        title: "必要结果单元 M", owner_principal_id: "owner-12345678", primary_scope_id: "scope-a",
        parent_pco_ref: pco, why: "任务必要性", requirements: ["要求一"], criteria: ["验收标准"],
        evidence_refs: [{ object_id: "ev1", revision_id: "evr1", payload_hash: "f".repeat(64) }],
        period: { start: "2026-01-05T00:00:00Z", end: "2026-01-20T00:00:00Z" } } },
    ],
    commitments: [{ responsibility_object_id: "pco1", principal_id: "owner-12345678",
                    assignment_id: "as1", statement: "本人承诺该责任" }] })])
  await screen.findByTestId("method-task-o1")
  const preview = screen.getByTestId("method-content-o1")
  expect(preview).toHaveTextContent("当前现实描述")
  expect(preview).toHaveTextContent("标准一；标准二")
  expect(preview).toHaveTextContent("推进长期结果")
  expect(preview).toHaveTextContent("周期必要性")
  expect(preview).toHaveTextContent("owner-12")
  expect(preview).toHaveTextContent("任务必要性")
  expect(preview).toHaveTextContent("要求一")
  expect(preview).toHaveTextContent("验收标准")
  expect(preview).toHaveTextContent("本人承诺该责任")
  expect(preview).toHaveTextContent("采纳")
  expect(preview).toHaveTextContent("采纳该意见")
  expect(preview).toHaveTextContent("证据充分性")
  expect(preview).toHaveTextContent("尚缺独立来源")
  expect(preview).toHaveTextContent("非阻塞备注")
  expect(preview.textContent).not.toContain('"object_id"')
  expect(preview.textContent).not.toContain('{"')
})

it("renders the full proposal change, domain mapping and retained basis", async () => {
  const retainedStrategy = { object_id: "st-old", revision_id: "sr-old", payload_hash: "1".repeat(64) }
  const retainedArchitecture = { object_id: "arch-old", revision_id: "ar-old", payload_hash: "2".repeat(64) }
  const change = {
    strategy_target_ref: retainedStrategy, architecture_target_ref: retainedArchitecture,
    applicability_rationale: "适用性理由：保留对象仍有效",
    strategy: { title: "新战略", statement: "战略陈述", required_capabilities: [
      { capability_id: "cap-1", name: "需求感知", definition: "感知需求变化",
        primary_domain_id: "scope-a", analysis_fields: ["demand", "quality"] }] },
    architecture: { title: "新责任结构", battlefields: [
      { unit_id: "bf-1", name: "价值场域", definition: "价值定义", strategic_basis: ["依据一"],
        boundary: "边界", interfaces: [], auth_domain_id: "auth-1",
        current_dri_principal_id: "dri-1" }],
      domains: [{ unit_id: "scope-a", name: "能力域", definition: "长期能力",
                  responsibility: "主要责任", auth_domain_id: "auth-2",
                  current_dri_principal_id: "dri-2" }] },
  }
  setup([defaultTask({ object_type: "StrategyUpdateProposal", phase: "reviewed",
    payload: { title: "提案", rationale: "依据正式共识", change },
    actions: [action({ action_type: "m1a_confirm_update", label: "最终确认正式更新" })] })])
  const preview = await screen.findByTestId("method-content-o1")
  expect(preview).toHaveTextContent("需求感知")
  expect(preview).toHaveTextContent("感知需求变化")
  expect(preview).toHaveTextContent("分析字段：demand、quality")
  expect(preview).toHaveTextContent("价值场域")
  expect(preview).toHaveTextContent("战略依据：依据一")
  expect(preview).toHaveTextContent("能力域")
  expect(preview).toHaveTextContent("主要责任")
  expect(preview).toHaveTextContent("授权域映射")
  expect(preview).toHaveTextContent("当前责任人")
  expect(preview).toHaveTextContent("适用性理由")
  expect(preview).toHaveTextContent("变更 Strategy（基准 st-old")
  expect(preview).toHaveTextContent("变更 Architecture（基准 arch-old")
  expect(preview.textContent).not.toContain('"object_id"')
  expect(preview.textContent).not.toContain('{"')
})

it("shows a retained object's exact basis when only the counterpart changes", async () => {
  const retainedStrategy = { object_id: "st-retained", revision_id: "sr-retained", payload_hash: "3".repeat(64) }
  const change = {
    strategy_target_ref: retainedStrategy, architecture_target_ref: null,
    strategy: null,
    applicability_rationale: "Strategy 保留原确切版本",
    architecture: { title: "仅变更责任结构", battlefields: [
      { unit_id: "bf-1", name: "场域", definition: "定义", strategic_basis: ["依据"],
        boundary: "边界", interfaces: [] }],
      domains: [{ unit_id: "dom-1", name: "域", definition: "定义",
                  responsibility: "责任", auth_domain_id: "auth-x" }] },
  }
  setup([defaultTask({ object_type: "StrategyUpdateProposal", phase: "reviewed",
    payload: { title: "仅责任结构提案", rationale: "共识", change } })])
  const preview = await screen.findByTestId("method-content-o1")
  expect(preview).toHaveTextContent("保留 Strategy st-retai")
  expect(preview).toHaveTextContent("Strategy 保留原确切版本")
  expect(preview).not.toHaveTextContent("Required Capability")
})

it("shows an empty state instead of a blank privileged console", async () => {
  setup([])
  await screen.findByTestId("method-actions-empty")
  expect(screen.queryByRole("form")).not.toBeInTheDocument()
})

it("never implies empty when the method-task read failed", async () => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(
    new Response(JSON.stringify({ error: { code: "UNAVAILABLE" } }), { status: 500 }))))
  render(<MethodActions session={session} prepare={vi.fn()} onError={vi.fn()} onExplore={vi.fn()} />)
  await screen.findByTestId("method-actions-error")
  expect(screen.queryByTestId("method-actions-empty")).not.toBeInTheDocument()
})

it("builds strict participant nomination rows", () => {
  const params = paramsForMethodAction("m1a_set_participants", {
    participants: [
      { principal_id: "p1", assignment_id: "a1", personal_agent_id: "", research: "true" },
      { principal_id: "p2", assignment_id: "a2", personal_agent_id: "g2", research: "" },
      { principal_id: "", assignment_id: "", personal_agent_id: "", research: "" },
    ],
  }) as { participants: Array<Record<string, unknown>> }
  expect(params.participants).toEqual([
    { principal_id: "p1", assignment_id: "a1", personal_agent_id: null, research: true },
    { principal_id: "p2", assignment_id: "a2", personal_agent_id: "g2", research: false },
  ])
})

it("builds own-responsibility commitment and problem close payloads", () => {
  const commit = methodEnvelope(
    { ...action({ action_type: "m1b_commit_candidate" }) },
    { responsibility_object_id: "o1", responsibility_revision_id: "r1",
      responsibility_payload_hash: "a".repeat(64), statement: "本人承担" }, "k".repeat(16))
  expect(commit.params).toEqual({
    responsibility_ref: { object_id: "o1", revision_id: "r1", payload_hash: "a".repeat(64) },
    statement: "本人承担" })
  const close = methodEnvelope(
    { ...action({ action_type: "method_close_problem" }) },
    { disposition: "resolved", reason: "已处理",
      evidence_refs: [{ object_id: "e1", revision_id: "er1", payload_hash: "b".repeat(64) }] }, "k".repeat(16))
  expect(close.params).toEqual({
    disposition: "resolved", reason: "已处理",
    evidence_refs: [{ object_id: "e1", revision_id: "er1", payload_hash: "b".repeat(64) }] })
})

it("omits the target for a targetless problem open and keeps the exact state ref", () => {
  const task = { ...defaultTask(), object_type: "OperatingState" }
  const problem = action({ action_type: "method_open_problem", target: null, object_domain_id: "d9" })
  const envelope = methodEnvelope(problem, {
    domain_id: "d9", state_object_id: "s1", state_revision_id: "sr1", state_payload_hash: "c".repeat(64),
    core_question: "为什么偏差?", statement: "偏差持续", why_material: "影响周期结果",
    level: "domain", responsible_assignment_id: "as1", evidence_refs: [] }, "k".repeat(16))
  expect(envelope.target).toBeNull()
  expect(envelope.params).toEqual({
    domain_id: "d9",
    payload: { state_ref: { object_id: "s1", revision_id: "sr1", payload_hash: "c".repeat(64) },
               core_question: "为什么偏差?", statement: "偏差持续", why_material: "影响周期结果",
               level: "domain", responsible_assignment_id: "as1", evidence_refs: [] } })
  void task
})
