import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { TypeInfoCard } from "@/components/TypeInfoCard"
import { methodMap, methodMapEntry, ontologyCatalog } from "./fixtures"

afterEach(() => { cleanup() })

function renderCard(type = "Mission", map: unknown = methodMap()) {
  const onSelectType = vi.fn()
  render(<TypeInfoCard type={type} rules="0.3" catalog={ontologyCatalog()}
                       methodMap={map as never}
                       onSelectType={onSelectType}
                       onViewData={vi.fn()} onClose={vi.fn()} />)
  return { onSelectType }
}

it("shows source, classification, maturity and implementation support", () => {
  renderCard()
  const entry = screen.getByTestId("method-map-entry-I06")
  expect(entry).toHaveTextContent("Mission")
  expect(entry).toHaveTextContent("Anchor")
  expect(entry).toHaveTextContent("Defined")
  expect(entry).toHaveTextContent("来源 B01")
  expect(entry).toHaveTextContent("待业务收口")
  expect(entry).toHaveTextContent("已编译")
  expect(entry).toHaveTextContent("本 scope 启用：tkos.method/0.3")
})

it("marks a documented-but-uncompiled contract as not enabled", () => {
  renderCard()
  expect(screen.getByTestId("method-map-entry-I06"))
    .toHaveTextContent("tkos.method/0.4（仅文档，未编译）")
})

it("offers a runtime data link only for a scope-enabled object type", () => {
  const { onSelectType } = renderCard()
  const entry = screen.getByTestId("method-map-entry-I06")
  fireEvent.click(within(entry).getByRole("button", { name: "Mission 任务" }))
  expect(onSelectType).toHaveBeenCalledWith("Mission")
})

it("never shows a runtime data link for an uncompiled or scope-disabled type", () => {
  const entry = methodMapEntry({
    id: "I03",
    ssot_name: "Strategic Architecture",
    runtime_object_types: ["StrategicArchitecture"],
    planned_contracts: ["tkos.method/0.4"],
    runtime_support: { assessment: "requires_contract_change", compiled: "compiled",
                       compiled_contract_versions: ["tkos.method/0.3"],
                       scope_enabled_contract_versions: [],
                       links: [] },
    runtime_links: [],
    authorized_read_availability: { status: "not_implemented", note: "" },
  })
  renderCard("StrategicArchitecture", methodMap({ entries: [entry] }))
  const card = screen.getByTestId("method-map-entry-I03")
  expect(card).toHaveTextContent("本 scope 无已启用契约")
  expect(card).toHaveTextContent("未编译／未启用")
  expect(card).not.toHaveTextContent("已支持对象")
})

it("states when the method map is unavailable instead of implying empty mapping", () => {
  renderCard("Mission", null)
  fireEvent.click(screen.getByText("方法定义对照"))
  expect(screen.getByTestId("method-map-unavailable")).toBeInTheDocument()
})
