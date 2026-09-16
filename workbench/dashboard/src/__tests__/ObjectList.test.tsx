import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { ObjectList } from "@/components/ObjectList"
import { missionItem } from "@/__tests__/fixtures"

describe("ObjectList", () => {
  it("marks a still-effective old-Strategy target as historical basis and never reattaches it", () => {
    const item = missionItem("m1", {
      basis: { status: "historical", reason: "recorded_other_strategy", impact_linked: true,
               selected_strategy_ref: { object_id: "s2", revision_id: "s2r1" },
               strategy_ref: { object_id: "s1", revision_id: "s1r1" }, basis_revision: "effective" },
    })
    render(<ObjectList items={[item]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getAllByText("历史依据").length).toBeGreaterThan(0)
    expect(screen.getByTestId("basis-section-历史依据（旧 Strategy）")).toBeInTheDocument()
    expect(screen.getAllByText(/不并入当前战略/).length).toBeGreaterThan(0)
  })

  it("shows the business period of a Mission as coming from its exact PCO reference", () => {
    render(<ObjectList items={[missionItem()]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getByText(/引自 PCO 版本/)).toBeInTheDocument()
    expect(screen.getByText(/周期：2026-09-01 至 2026-10-01/)).toBeInTheDocument()
  })

  it("shows a future appointment distinctly from a revoked one", () => {
    const item = missionItem("m2", {
      owner: { relation: "mission_owner", outcome_id: null,
               principal: { principal_id: "p9", display_name: "未来责任人", principal_type: "human", active: true },
               assignment: null, assignment_id: null,
               appointment: { status: "future", reason: "appointment_not_started", assignments: [] } },
    })
    render(<ObjectList items={[item]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getByText(/任职尚未开始/)).toBeInTheDocument()
  })

  it("uses a neutral historical group subtitle and no effectiveness assumption", () => {
    const view = render(<ObjectList items={[missionItem("m-old", {
      basis: { status: "historical", reason: "recorded_other_strategy", impact_linked: true,
               selected_strategy_ref: { object_id: "s2", revision_id: "s2r1" },
               strategy_ref: { object_id: "s1", revision_id: "s1r1" }, basis_revision: "effective" },
    })]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(view.getByText(/保留旧战略依据，效力以各对象状态为准/)).toBeInTheDocument()
    expect(view.queryByText(/仍生效但基于旧战略/)).not.toBeInTheDocument()
  })

  it("uses the recorded summary as heading when an OperatingState has no title", () => {
    render(<ObjectList items={[missionItem("st1", {
      object_type: "OperatingState", title: null, summary: "证据质量待确认",
    })]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getByText("证据质量待确认")).toBeInTheDocument()
    expect(screen.queryByText("（未记录标题）")).not.toBeInTheDocument()
  })

  it("empty means empty, without sample rows", () => {
    render(<ObjectList items={[]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getByTestId("list-empty")).toHaveTextContent("没有可读对象")
  })
})

describe("list responsibility projection", () => {
  const driEntry = {
    relation: "outcome_dri", outcome_id: "o1",
    principal: { principal_id: "pd", display_name: "DRI 责任人", principal_type: "human", active: true },
    assignment: null, assignment_id: null,
    appointment: { status: "current" as const, reason: null, assignments: [] },
  }
  const responsibleEntry = {
    relation: "responsible", outcome_id: null,
    principal: { principal_id: "pr", display_name: "问题责任人", principal_type: "human", active: true },
    assignment: null, assignment_id: "a1",
    appointment: { status: "current" as const, reason: null, assignments: [] },
  }

  it("shows Outcome DRI when no Owner is recorded", () => {
    render(<ObjectList items={[missionItem("pco1", {
      object_type: "PCO", owner: { status: "missing", reason: "owner_not_recorded" },
      responsibility: [driEntry],
    })]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getByText(/成果 DRI：DRI 责任人/)).toBeInTheDocument()
    expect(screen.queryByText("责任：未记录")).not.toBeInTheDocument()
  })

  it("shows the recorded problem responsible relation", () => {
    render(<ObjectList items={[missionItem("pb1", {
      object_type: "OperatingProblem", owner: { status: "missing", reason: "owner_not_recorded" },
      responsibility: [responsibleEntry],
    })]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getByText(/问题责任人：问题责任人/)).toBeInTheDocument()
    expect(screen.queryByText("责任：未记录")).not.toBeInTheDocument()
  })

  it("falls back to owner+dri when the new projection is absent", () => {
    const item = missionItem("lt1") as unknown as Record<string, unknown>
    delete item.responsibility
    item.owner = { status: "missing", reason: "owner_not_recorded" }
    item.dri = [driEntry]
    render(<ObjectList items={[item as never]} selectedId={null} loading={false} hasMore={false}
                       onSelect={vi.fn()} onLoadMore={vi.fn()} />)
    expect(screen.getByText(/成果 DRI：DRI 责任人/)).toBeInTheDocument()
  })
})
