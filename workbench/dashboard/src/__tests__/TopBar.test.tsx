import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeAll, expect, it, vi } from "vitest"
import { TopBar } from "@/components/TopBar"

afterEach(() => { cleanup() })
beforeAll(() => {
  // Radix Select scrolls the active option; jsdom has no layout.
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => {})
})

function renderBar(rules: string) {
  const onRulesChange = vi.fn()
  render(<TopBar overview={null} strategyId={null} onStrategyChange={vi.fn()}
                  onRefresh={vi.fn()} refreshing={false} view="map"
                  onViewChange={vi.fn()} rules={rules} onRulesChange={onRulesChange} />)
  return { onRulesChange }
}

it("shows the current 0.4 rule version instead of a blank select", () => {
  renderBar("0.4")
  expect(screen.getByTestId("rules-select")).toHaveTextContent("业务规则 0.4")
})

it("keeps the old 0.1-0.3 choices selectable", async () => {
  const { onRulesChange } = renderBar("0.3")
  expect(screen.getByTestId("rules-select")).toHaveTextContent("业务规则 0.3")
  fireEvent.click(screen.getByTestId("rules-select"))
  for (const label of ["业务规则 0.4", "业务规则 0.3", "业务规则 0.2", "业务规则 0.1"]) {
    expect(await screen.findByRole("option", { name: label })).toBeInTheDocument()
  }
  fireEvent.click(screen.getByRole("option", { name: "业务规则 0.4" }))
  expect(onRulesChange).toHaveBeenCalledWith("0.4")
})

it("has no independent object-list entry in the read-only explorer navigation", () => {
  renderBar("0.4")
  expect(screen.getByTestId("view-tab-map")).toBeInTheDocument()
  expect(screen.getByTestId("view-tab-definitions")).toBeInTheDocument()
  expect(screen.getByTestId("view-tab-graph")).toBeInTheDocument()
  expect(screen.queryByTestId("view-tab-list")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "列表" })).not.toBeInTheDocument()
})
