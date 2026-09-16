import { afterEach, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { OntologyMap } from "@/components/OntologyMap"
import type { OntologyCatalog } from "@/lib/types"
afterEach(cleanup)
const common = ["Strategy", "LTCO", "PCO", "Mission", "ReviewWindow", "CandidateSet", "PeriodReview", "StrategicIssue", "EvidenceAsset"]
const catalog: OntologyCatalog = { schema_version: "tkos.dashboard/0.1", read_at: "2026-09-16T00:00:00Z", versions: [{ contract_version: "tkos.method/0.3", object_types: [...common, "StrategicArchitecture", "OperatingState"] }, { contract_version: "tkos.method/0.1", object_types: common }], types: {}, note: "Registered directory without instances" }
it("starts with collapsed areas and keeps concepts that have no instances", () => {
  render(<OntologyMap rules="0.3" catalog={catalog} loading={false} error={null} selectedType={null} onSelectType={vi.fn()} />)
  const toggles = screen.getAllByTestId(/^map-area-toggle-/)
  expect(toggles).toHaveLength(6)
  toggles.forEach((button) => expect(button).toHaveAttribute("aria-expanded", "false"))
  fireEvent.click(screen.getByTestId("map-area-toggle-strategy-structure"))
  expect(screen.getByTestId("map-type-StrategicArchitecture")).toBeInTheDocument()
})
it("search opens the containing area and selects its exact type", () => {
  const select = vi.fn()
  render(<OntologyMap rules="0.3" catalog={catalog} loading={false} error={null} selectedType={null} onSelectType={select} />)
  fireEvent.change(screen.getByTestId("map-search"), { target: { value: "Mission" } })
  fireEvent.click(screen.getByTestId("map-search-matches").querySelector("button")!)
  expect(select).toHaveBeenCalledWith("Mission")
  expect(screen.getByTestId("map-area-toggle-objectives")).toHaveAttribute("aria-expanded", "true")
  expect(screen.getByTestId("map-type-Mission")).toHaveFocus()
})
it("external selection expands its area, while old rules omit absent types", () => {
  const props = { catalog, loading: false, error: null, selectedType: "StrategicArchitecture", onSelectType: vi.fn() }
  const { rerender } = render(<OntologyMap {...props} rules="0.3" />)
  expect(screen.getByTestId("map-type-StrategicArchitecture")).toBeInTheDocument()
  rerender(<OntologyMap {...props} rules="0.1" />)
  expect(screen.queryByTestId("map-type-StrategicArchitecture")).not.toBeInTheDocument()
})
it("a catalogue error is not an empty ontology", () => {
  render(<OntologyMap rules="0.3" catalog={null} loading={false} error={new Error("offline")} selectedType={null} onSelectType={vi.fn()} />)
  expect(screen.getByTestId("map-error")).toHaveTextContent("这不是")
  expect(screen.queryByTestId("map-area-strategy-structure")).not.toBeInTheDocument()
})
