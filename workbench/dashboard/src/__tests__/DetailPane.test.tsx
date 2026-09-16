import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"
import { DetailPane } from "@/components/DetailPane"
import { detail } from "@/__tests__/fixtures"

describe("DetailPane", () => {
  it("shows readable Outcome name and criteria behind supports[].outcome_ref", () => {
    render(<DetailPane detail={detail()} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    expect(screen.getAllByText("试点证据").length).toBeGreaterThan(0)
    expect(screen.getAllByText(/三例/).length).toBeGreaterThan(0)
    expect(screen.getByText(/承接贡献：收集证据/)).toBeInTheDocument()
    expect(screen.queryByText("assignment")).not.toBeInTheDocument()
  })

  it("states an unreadable outcome explicitly instead of inventing a name", () => {
    const value = detail()
    value.business.supports = [{ outcome_id: "o9", outcome_ref: { object_id: "p9", revision_id: "r9" },
                                 contribution: "未读贡献", outcome: null,
                                 outcome_status: { status: "missing", reason: "outcome_revision_unavailable" } }]
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    expect(screen.getByText(/Outcome 依据当前不可读/)).toBeInTheDocument()
  })

  it("separates Mission content confirmation from the formal OperatingState panel", () => {
    const value = detail({
      content_confirmation: {
        records: [{ record_id: "rec1", kind: "candidate_set_confirmation", principal_id: "p1",
                    principal: { principal_id: "p1", display_name: "合成 CEO", principal_type: "human",
                                 active: true },
                    recorded_at: "2026-09-16T01:40:00+00:00",
                    target_ref: { object_id: "c1", revision_id: "c1r1" }, covered_refs: [],
                    covers_selected_revision: true, human_confirmation: true,
                    source: "confirmed_candidate_set", content: { reason: "CEO 确认整组目标" },
                    receipt: { receipt_id: "rc1", action_type: "m1b_confirm_candidates",
                               recorded_at: "2026-09-16T01:40:00+00:00" } }],
        confirmed_for_selected_revision: true, note: "",
      },
      relations: {
        ...detail().relations,
        downstream: { items: [{ object_id: "st1", object_type: "OperatingState", domain_id: "d1",
            title: "经营状态", ref: { object_id: "st1", revision_id: "st1r1" }, matched_fields: ["subject_ref"],
            formal_state: { status: "confirmed", formal: true },
            state_summary: { subject_ref: { object_id: "m1", revision_id: "m1-r2" },
                             subject_matches_selected_anchor: true, outcome_id: null, outcome: null,
                             outcome_status: { status: "missing", reason: "no_outcome_recorded" },
                             as_of: "2026-09-16T01:20:00+00:00", rag: "yellow",
                             summary: "证据质量待确认", data_gaps: ["缺少第二来源"],
                             baseline_refs: [{ object_id: "m1", revision_id: "m1-r2" }], evidence_refs: [],
                             formal: true, status: "confirmed",
                             applies_to_ref: { object_id: "st1", revision_id: "st1r1" },
                             canonical_ref: { object_id: "st1", revision_id: "st1r1" }, confirmed_by: "p1" } }],
          next_cursor: null, has_more: false, limit: 25, bounded: 100 },
      },
    })
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    // Mission content confirmation is its own panel.
    expect(screen.getByText("内容确认（该对象精确版本）")).toBeInTheDocument()
    expect(screen.getByText("CEO 确认整组目标")).toBeInTheDocument()
    // The formal OperatingState RAG/as_of/baseline panel is separate and exact.
    expect(screen.getByText(/正式经营状态/)).toBeInTheDocument()
    expect(screen.getByText("关注")).toBeInTheDocument()
    expect(screen.getByText(/缺少第二来源/)).toBeInTheDocument()
  })

  it("shows the matched responsible person for an Outcome instead of a raw id", () => {
    const value = detail()
    value.responsibility.entries = [{ relation: "outcome_dri", outcome_id: "o1",
      principal: { principal_id: "p1", display_name: "责任人甲", principal_type: "human", active: true },
      assignment: { assignment_id: "a1", role: "DOMAIN_DRI", domain_id: "d1",
                    current_assignment_active: true, valid_from: "2026-09-01T00:00:00+00:00", valid_to: null },
      assignment_id: "a1",
      appointment: { status: "future", reason: "appointment_not_started", assignments: [] } }]
    value.business.supports = [{ outcome_id: "o1", outcome_ref: { object_id: "p1", revision_id: "p1-r1" },
      contribution: "收集证据", outcome: { outcome_id: "o1", title: "试点证据", criteria: ["三例"] },
      outcome_status: { status: "available", reason: null }, dri_principal_id: "p1" }]
    value.business.outcomes = [{ outcome_id: "o1", unit_id: "u1", title: "试点证据",
      result_statement: "三例试点", criteria: ["三例"], dri_principal_id: "p1" }]
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    expect(screen.getAllByText(/责任人甲/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/任职尚未开始/).length).toBeGreaterThan(0)
    expect(screen.queryByText("p1")).not.toBeInTheDocument()
  })

  it("keeps raw JSON and exact ids behind the technical traceability tab only", async () => {
    render(<DetailPane detail={detail()} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole("tab", { name: "技术溯源" }))
    await user.click(screen.getByRole("button", { name: /展开精确引用/ }))
    expect(screen.getByTestId("exact-refs")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /展开原始 payload/ }))
    expect(screen.getByTestId("raw-payload")).toHaveTextContent('"title": "任务 m1"')
  })

  it("renders a mapped business error, not raw API text", () => {
    render(<DetailPane detail={detail()} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    expect(screen.queryByText("The requested record is unavailable.")).not.toBeInTheDocument()
  })
})

describe("evidence download", () => {
  it("uses a same-origin download anchor without opening a blank tab", async () => {
    const user = userEvent.setup()
    const value = detail()
    value.evidence = { items: [
      { path: "evidence_refs", field: "evidence_refs", object_id: "ev1", revision_id: "ev1-r1",
        object_type: "EvidenceAsset", lifecycle_status: "stored", title: "原始证据甲",
        domain_id: "d1", download: { available: true, object_id: "ev1", revision_id: "ev1-r1" } },
    ] }
    render(<DetailPane detail={value} loading={false} error={null}
                       onOpenObject={vi.fn()} onSelectRevision={vi.fn()} />)
    await user.click(screen.getByRole("tab", { name: "证据与确认" }))
    const link = screen.getByRole("link", { name: "下载原始证据" })
    expect(link).toHaveAttribute("download")
    expect(link).not.toHaveAttribute("target")
    expect(link).not.toHaveAttribute("rel")
    expect(link).toHaveAttribute("href", "/dashboard/api/v1/evidence-assets/ev1/revisions/ev1-r1")
  })
})
