import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { DetailPane } from "@/components/DetailPane"
import { formalBusinessText } from "@/lib/labels"
import { detail } from "@/__tests__/fixtures"

afterEach(() => vi.unstubAllGlobals())

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => { resolve = res })
  return { promise, resolve }
}

describe("browser readability fixes", () => {
  it("uses specific business wording for formal states", () => {
    expect(formalBusinessText("Mission", "confirmed", true)).toBe("已确认，待执行承接")
    expect(formalBusinessText("Mission", "under_review", false)).toBe("共同核对中，尚未生效")
    expect(formalBusinessText("Mission", "draft", false)).toBe("草稿，尚未提交核对")
    expect(formalBusinessText("PCO", "candidate", false)).toBe("候选待确认")
    expect(formalBusinessText("LTCO", "draft", false)).not.toContain("六个月")
    expect(formalBusinessText("PeriodReview", "recorded", false)).toContain("Agent 复盘")
    expect(formalBusinessText("OperatingProblem", "transferred", false)).toContain("尚未解决")
  })

  it("shows the confirming person, reason and business action, and opens the receipt", async () => {
    const user = userEvent.setup()
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      receipt: { receipt_id: "rc1", action_type: "m1b_confirm_candidates", actor_id: "p1",
                 auth_epoch: 1, status: "committed", result: {}, object_versions: [],
                 effect_task_ids: [], recorded_at: "2026-09-16T01:40:00+00:00" },
      effects: [] }), { status: 200, headers: { "content-type": "application/json" } })))
    const value = detail()
    value.content_confirmation = {
      records: [{ record_id: "rec1", kind: "candidate_set_confirmation", principal_id: "p1",
                  principal: { principal_id: "p1", display_name: "合成 CEO", principal_type: "human",
                               active: true },
                  recorded_at: "2026-09-16T01:40:00+00:00",
                  target_ref: { object_id: "c1", revision_id: "c1r1" }, covered_refs: [],
                  covers_selected_revision: true, human_confirmation: true,
                  source: "confirmed_candidate_set", content: { reason: "本人确认整组目标" },
                  receipt: { receipt_id: "rc1", action_type: "m1b_confirm_candidates",
                             recorded_at: "2026-09-16T01:40:00+00:00" } }],
      confirmed_for_selected_revision: true, note: "",
    }
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    await user.click(screen.getByRole("tab", { name: "证据与确认" }))
    // The confirmer's name, the reason and a business action label are visible.
    expect(screen.getAllByText("合成 CEO").length).toBeGreaterThan(0)
    expect(screen.getByText("本人确认整组目标")).toBeInTheDocument()
    expect(screen.getAllByText(/动作：整组目标确认/).length).toBeGreaterThan(0)
    // No raw action_type in primary content.
    expect(screen.queryByText(/m1b_confirm_candidates/)).not.toBeInTheDocument()
    await user.click(screen.getAllByRole("button", { name: "查看回执" })[0])
    await waitFor(() => expect(screen.getByText("已提交")).toBeInTheDocument())
    expect(screen.getAllByText("整组目标确认").length).toBeGreaterThan(0)
  })

  it("labels candidate and formal revisions explicitly and dedupes the same revision", async () => {
    const user = userEvent.setup()
    const value = detail()
    value.selected_revision = { ...value.selected_revision, revision_id: "m1-r1", object_version: 1,
                                selection: "requested" }
    value.candidates = {
      same_object_only: true,
      latest: { revision_id: "m1-r3", payload_hash: "d".repeat(64),
                recorded_at: "2026-09-16T02:00:00+00:00", object_version: 3, is_selected: false,
                changed_fields_vs_selected: [{ field_path: "/deliverable", before: "旧交付",
                                               after: "新交付", change: "changed" }] },
      effective: { revision_id: "m1-r2", payload_hash: "b".repeat(64),
                   recorded_at: "2026-09-16T01:30:00+00:00", object_version: 2, is_selected: false },
    }
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    await user.click(screen.getByRole("tab", { name: "版本与候选" }))
    expect(screen.getByText("待确认候选")).toBeInTheDocument()
    expect(screen.getByText("当前正式版本")).toBeInTheDocument()
    expect(screen.getByText("交付物")).toBeInTheDocument()
    expect(screen.getByText(/旧交付/)).toBeInTheDocument()
    expect(screen.queryByText(/\{\"deliverable\"/)).not.toBeInTheDocument()
    // Same revision in latest and effective must appear once as a candidate card.
    expect(screen.getAllByText("当前正式版本").length).toBe(1)
    expect(screen.queryAllByText("当前正式版本（最新）").length).toBe(0)
  })

  it("marks a non-formal state rating and resolves baseline titles", () => {
    const value = detail()
    value.relations.downstream = {
      items: [{ object_id: "st1", object_type: "OperatingState", domain_id: "d1", title: "经营状态",
                ref: { object_id: "st1", revision_id: "st1r1" }, matched_fields: ["subject_ref"],
                formal_state: { status: "recommendation", formal: false },
                state_summary: { subject_ref: { object_id: "m1", revision_id: "m1-r2" },
                                 subject_matches_selected_anchor: true, outcome_id: null,
                                 outcome: null, outcome_status: { status: "missing", reason: "x" },
                                 as_of: "2026-09-16T01:20:00+00:00", rag: "red",
                                 summary: "新建议", data_gaps: [],
                                 baseline_refs: [{ object_id: "m1", revision_id: "m1-r2" }],
                                 evidence_refs: [],
                                 baseline_items: [{ path: "baseline_refs", object_id: "m1",
                                                    revision_id: "m1-r2", object_type: "Mission",
                                                    lifecycle_status: "confirmed", title: "任务 m1",
                                                    domain_id: "d1" }],
                                 evidence_items: [], formal: false, status: "recommendation",
                                 applies_to_ref: null, canonical_ref: null, confirmed_by: null } }],
      next_cursor: null, has_more: false, limit: 25, bounded: 100,
    }
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    expect(screen.getByText(/新建议待确认/)).toBeInTheDocument()
    expect(screen.getByTestId("state-st1")).toHaveTextContent("（非正式）")
    expect(screen.getByRole("button", { name: "任务 m1" })).toBeInTheDocument()
  })

  it("does not repeat direct typed bases as source materials", async () => {
    const user = userEvent.setup()
    const value = detail()
    value.relations.own_basis_refs = [{ path: "pco_ref", object_id: "p1", revision_id: "p1r1",
      object_type: "PCO", lifecycle_status: "draft", title: "当期目标", domain_id: "d1" }]
    value.relations.upstream_refs = []
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    await user.click(screen.getByRole("tab", { name: "依据与关系" }))
    expect(screen.getByText("直接依据（精确引用）")).toBeInTheDocument()
    expect(screen.getByText("没有额外的来源材料。")).toBeInTheDocument()
  })
})

describe("candidate diff readability", () => {
  it("translates known enums and folds model-trace fields", async () => {
    const user = userEvent.setup()
    const value = detail()
    value.selected_revision = { ...value.selected_revision, revision_id: "m1-r1", object_version: 1,
                                selection: "requested" }
    value.candidates = {
      same_object_only: true,
      latest: { revision_id: "m1-r3", payload_hash: "d".repeat(64),
                recorded_at: "2026-09-16T02:00:00+00:00", object_version: 3, is_selected: false,
                changed_fields_vs_selected: [
                  { field_path: "/rag", before: "yellow", after: "green", change: "changed" },
                  { field_path: "/summary", before: "证据质量待确认", after: "证据质量已确认", change: "changed" },
                  { field_path: "/generation_version", before: "controlled-2", after: "controlled-3",
                    change: "changed" },
                ] },
      effective: { revision_id: "m1-r2", payload_hash: "b".repeat(64),
                   recorded_at: "2026-09-16T01:30:00+00:00", object_version: 2, is_selected: false },
    }
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    await user.click(screen.getByRole("tab", { name: "版本与候选" }))
    expect(screen.getByText(/关注/)).toBeInTheDocument()
    expect(screen.getByText(/正常/)).toBeInTheDocument()
    expect(screen.getByText(/证据质量待确认/)).toBeInTheDocument()
    expect(screen.queryByText(/yellow/)).not.toBeInTheDocument()
    expect(screen.queryByText(/controlled-3/)).not.toBeInTheDocument()
    expect(screen.getByText(/另有 1 项技术字段/)).toBeInTheDocument()
  })
})

describe("final browser semantics", () => {
  it("labels a current effective revision without calling it a pending candidate", async () => {
    const user = userEvent.setup()
    const value = detail()
    value.history.items = [
      { revision_id: "m1-r1", object_version: 1, payload_hash: "c".repeat(64),
        recorded_at: "2026-09-16T01:00:00+00:00", is_latest: false, is_effective: false },
      { revision_id: "m1-r2", object_version: 2, payload_hash: "b".repeat(64),
        recorded_at: "2026-09-16T01:30:00+00:00", is_latest: true, is_effective: true },
      { revision_id: "m1-r3", object_version: 3, payload_hash: "d".repeat(64),
        recorded_at: "2026-09-16T02:00:00+00:00", is_latest: true, is_effective: false },
    ]
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    await user.click(screen.getByRole("tab", { name: "版本与候选" }))
    expect(screen.getByText("当前正式")).toBeInTheDocument()
    expect(screen.queryByText("最新候选")).not.toBeInTheDocument()
    expect(screen.getByText("最新版本")).toBeInTheDocument()
  })

  it("does not ask for a state-of-state on an OperatingState's own overview", () => {
    const value = detail()
    value.object = { ...value.object, object_type: "OperatingState", latest_revision_id: "st-r2",
                     effective_revision_id: "st-r2" }
    value.selected_revision = { ...value.selected_revision, revision_id: "st-r2" }
    value.business = { ...value.business, title: null, summary: "证据质量待确认", rag: "yellow" }
    value.evidence = { items: [
      { path: "baseline_refs", field: "baseline_refs", object_id: "m1", revision_id: "m1-r2",
        object_type: "Mission", lifecycle_status: "confirmed", title: "任务 m1", domain_id: "d1" },
      { path: "evidence_refs", field: "evidence_refs", object_id: "ev1", revision_id: "ev1-r1",
        object_type: "EvidenceAsset", lifecycle_status: "stored", title: "原始证据甲", domain_id: "d1" },
    ] }
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    expect(screen.queryByTestId("state-panel")).not.toBeInTheDocument()
    expect(screen.queryByText(/当前精确版本没有已记录的经营状态/)).not.toBeInTheDocument()
    expect(screen.getByTestId("state-own-links")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "任务 m1" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "原始证据甲" })).toBeInTheDocument()
    // The recorded summary is the display heading; no fabricated title.
    expect(screen.getAllByText("证据质量待确认").length).toBeGreaterThan(0)
    expect(screen.queryByText("（未记录标题）")).not.toBeInTheDocument()
  })

  it("keeps a Mission overview's state panel available", () => {
    render(<DetailPane detail={detail()} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    expect(screen.getByTestId("state-panel")).toBeInTheDocument()
  })
})

describe("ReceiptDetail denial handling", () => {
  function receiptDetail() {
    const value = detail()
    value.content_confirmation = {
      records: [{ record_id: "rec1", kind: "candidate_set_confirmation", principal_id: "p1",
                  principal: { principal_id: "p1", display_name: "合成 CEO", principal_type: "human",
                               active: true },
                  recorded_at: "2026-09-16T01:40:00+00:00",
                  target_ref: { object_id: "c1", revision_id: "c1r1" }, covered_refs: [],
                  covers_selected_revision: true, human_confirmation: true,
                  source: "confirmed_candidate_set", content: { reason: "确认" },
                  receipt: { receipt_id: "rc1", action_type: "m1b_confirm_candidates",
                             recorded_at: "2026-09-16T01:40:00+00:00" } }],
      confirmed_for_selected_revision: true, note: "",
    }
    return value
  }

  it("propagates a 403 receipt denial to the app access handler", async () => {
    const user = userEvent.setup()
    const onAccessDenied = vi.fn()
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      error: { code: "FORBIDDEN", message: "Current authority does not permit this operation." } }),
      { status: 403, headers: { "content-type": "application/json" } })))
    render(<DetailPane detail={receiptDetail()} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()}
                       onAccessDenied={onAccessDenied} />)
    await user.click(screen.getByRole("tab", { name: "证据与确认" }))
    await user.click(screen.getAllByRole("button", { name: "查看回执" })[0])
    await waitFor(() => expect(onAccessDenied).toHaveBeenCalledTimes(1))
    // The raw server text is never rendered as content.
    expect(screen.queryByText(/Current authority/)).not.toBeInTheDocument()
  })

  it("ignores a stale receipt denial after the receipt id changes", async () => {
    const user = userEvent.setup()
    const onAccessDenied = vi.fn()
    const stale = deferred<Response>()
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/action-receipts/rc1")) return stale.promise
      return Promise.resolve(new Response(JSON.stringify({ receipt: { receipt_id: "rc2",
        action_type: "m1b_confirm_ltco", recorded_at: "2026-09-16T01:45:00+00:00",
        status: "committed", object_versions: [] } }),
        { status: 200, headers: { "content-type": "application/json" } }))
    }))
    const value = receiptDetail()
    const { rerender } = render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()}
                       onAccessDenied={onAccessDenied} />)
    await user.click(screen.getByRole("tab", { name: "证据与确认" }))
    await user.click(screen.getAllByRole("button", { name: "查看回执" })[0])
    // Same object, different receipt: the stale denial must be ignored.
    const changed = receiptDetail()
    changed.content_confirmation.records[0].receipt = { receipt_id: "rc2",
      action_type: "m1b_confirm_ltco", recorded_at: "2026-09-16T01:45:00+00:00" }
    rerender(<DetailPane detail={changed} loading={false} error={null}
                         onOpenObject={vi.fn()} onSelectRevision={vi.fn()}
                         onAccessDenied={onAccessDenied} />)
    await act(async () => {
      stale.resolve(new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "x" } }),
        { status: 404, headers: { "content-type": "application/json" } }))
      await stale.promise
    })
    await waitFor(() => expect(onAccessDenied).not.toHaveBeenCalled())
  })
})
