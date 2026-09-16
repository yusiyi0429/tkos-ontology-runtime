import { describe, expect, it } from "vitest"
import { ownerOptions } from "@/components/FilterBar"
import { missionItem } from "@/__tests__/fixtures"

describe("owner filter options", () => {
  it("uses recorded responsibility relations and excludes participants", () => {
    const item = missionItem("pco1", {
      owner: { status: "missing", reason: "owner_not_recorded" },
      dri: [],
      responsibility: [
        { relation: "outcome_dri", outcome_id: "o1",
          principal: { principal_id: "pd", display_name: "DRI 责任人", principal_type: "human",
                       active: true },
          assignment: null, assignment_id: null,
          appointment: { status: "current", reason: null, assignments: [] } },
        { relation: "responsible", outcome_id: null,
          principal: { principal_id: "pr", display_name: "问题责任人", principal_type: "human",
                       active: true },
          assignment: null, assignment_id: "a1",
          appointment: { status: "current", reason: null, assignments: [] } },
        { relation: "participant", outcome_id: null,
          principal: { principal_id: "pp", display_name: "参与人", principal_type: "human",
                       active: true },
          assignment: null, assignment_id: null,
          appointment: { status: "current", reason: null, assignments: [] } },
      ],
    })
    expect(ownerOptions([item])).toEqual([
      { value: "pd", label: "DRI 责任人" },
      { value: "pr", label: "问题责任人" },
    ])
  })
})
