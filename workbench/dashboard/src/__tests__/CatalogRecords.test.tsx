import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest"
import { CatalogRecords } from "@/components/CatalogRecords"
import { fetchCatalogObjects } from "@/lib/api"
import { ApiError } from "@/lib/errors"
import { catalogItem, catalogPage, responsibilityEntry } from "./fixtures"
vi.mock("@/lib/api", () => ({ fetchCatalogObjects: vi.fn() }))
const read = vi.mocked(fetchCatalogObjects)
beforeEach(() => { read.mockReset() })
afterEach(cleanup)
beforeAll(() => {
  // Radix Select scrolls the active option; jsdom has no layout.
  Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => {})
})
const props = { objectType: "Mission", onOpenRecord: vi.fn(), onAccessDenied: vi.fn() }
it("an initially empty page can announce and accept newly created records", async () => {
  read.mockResolvedValue(catalogPage([]))
  render(<CatalogRecords {...props} />)
  await screen.findByTestId("catalog-empty")
  read.mockResolvedValue(catalogPage([catalogItem()]))
  act(() => window.dispatchEvent(new Event("focus")))
  await screen.findByTestId("catalog-pending")
  expect(screen.queryByTestId("catalog-row-m1")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "查看最新" }))
  await screen.findByTestId("catalog-row-m1")
})
it("an empty successful result also becomes explicitly stale on network failure", async () => {
  read.mockResolvedValue(catalogPage([]))
  render(<CatalogRecords {...props} />)
  await screen.findByTestId("catalog-empty")
  read.mockRejectedValue(new Error("offline"))
  await act(async () => { window.dispatchEvent(new Event("focus")); await Promise.resolve() })
  await screen.findByTestId("catalog-stale")
  expect(screen.getByTestId("catalog-stale")).toHaveTextContent("不代表已刷新")
})
it("accepting a changed first page discards old continuation rows", async () => {
  read.mockResolvedValueOnce(catalogPage([catalogItem("first")], { has_more: true, next_cursor: "next" }))
  read.mockResolvedValueOnce(catalogPage([catalogItem("old-page")]))
  render(<CatalogRecords {...props} />)
  await screen.findByTestId("catalog-row-first")
  fireEvent.click(screen.getByRole("button", { name: "加载更多" }))
  await screen.findByTestId("catalog-row-old-page")
  read.mockResolvedValue(catalogPage([catalogItem("new")]))
  act(() => window.dispatchEvent(new Event("focus")))
  await screen.findByTestId("catalog-pending")
  fireEvent.click(screen.getByRole("button", { name: "查看最新" }))
  await waitFor(() => expect(screen.queryByTestId("catalog-row-old-page")).not.toBeInTheDocument())
  expect(screen.getByTestId("catalog-row-new")).toBeInTheDocument()
})

it("filters the loaded authorized page by search, status and version with honest scope", async () => {
  read.mockResolvedValue(catalogPage([
    catalogItem("m1", { title: "任务甲" }),
    catalogItem("m2", { title: "任务乙", formal_state: { status: "candidate", formal: false } }),
    catalogItem("m3", { title: "任务丙", contract_version: "tkos.method/0.4" }),
  ], { has_more: true, next_cursor: "next" }))
  render(<CatalogRecords {...props} />)
  await screen.findByTestId("catalog-row-m1")
  fireEvent.change(screen.getByTestId("catalog-filter-search"), { target: { value: "乙" } })
  expect(screen.queryByTestId("catalog-row-m1")).not.toBeInTheDocument()
  expect(screen.getByTestId("catalog-row-m2")).toBeInTheDocument()
  // The note never implies a server-wide match while pages remain unloaded.
  expect(screen.getByTestId("catalog-filter-note")).toHaveTextContent("已加载的 3 条")
  expect(screen.getByTestId("catalog-filter-note")).toHaveTextContent("仍有未加载记录")
  fireEvent.change(screen.getByTestId("catalog-filter-search"), { target: { value: "" } })
  fireEvent.click(screen.getByTestId("catalog-filter-status"))
  fireEvent.click(await screen.findByRole("option", { name: "已确认" }))
  expect(screen.queryByTestId("catalog-row-m2")).not.toBeInTheDocument()
  expect(screen.getByTestId("catalog-row-m1")).toBeInTheDocument()
  fireEvent.click(screen.getByTestId("catalog-filter-version"))
  fireEvent.click(await screen.findByRole("option", { name: "业务规则 0.4" }))
  expect(screen.queryByTestId("catalog-row-m1")).not.toBeInTheDocument()
  expect(screen.getByTestId("catalog-row-m3")).toBeInTheDocument()
})

it("filters by an actual recorded owner name without inventing one", async () => {
  read.mockResolvedValue(catalogPage([
    catalogItem("m1", { responsibility: [responsibilityEntry("p-jia", "责任人甲")] }),
    catalogItem("m2", { responsibility: [responsibilityEntry("p-yi", "责任人乙", "outcome_dri")] }),
    catalogItem("m3"),
  ]))
  render(<CatalogRecords {...props} />)
  await screen.findByTestId("catalog-row-m1")
  fireEvent.click(screen.getByTestId("catalog-filter-owner"))
  expect(await screen.findByRole("option", { name: "责任人甲" })).toBeInTheDocument()
  expect(screen.getByRole("option", { name: "责任人乙" })).toBeInTheDocument()
  // Only authorized loaded rows contribute names; nothing is guessed.
  expect(screen.queryByRole("option", { name: "CEO" })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("option", { name: "责任人甲" }))
  expect(screen.getByTestId("catalog-row-m1")).toBeInTheDocument()
  expect(screen.queryByTestId("catalog-row-m2")).not.toBeInTheDocument()
  expect(screen.queryByTestId("catalog-row-m3")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "清除筛选" }))
  expect(screen.getByTestId("catalog-row-m2")).toBeInTheDocument()
})

it("applies a legacy owner filter and states when it is not in the loaded rows", async () => {
  read.mockResolvedValue(catalogPage([
    catalogItem("m1", { responsibility: [responsibilityEntry("p-jia", "责任人甲")] }),
  ]))
  const view = render(<CatalogRecords {...props} legacyOwner="p-yi" />)
  await screen.findByTestId("catalog-filter-note")
  // The legacy owner is applied as a filter, and its unmatched state is stated
  // instead of silently showing an unfiltered list.
  expect(screen.queryByTestId("catalog-row-m1")).not.toBeInTheDocument()
  expect(screen.getByTestId("catalog-owner-unmatched")).toHaveTextContent("不在已加载记录中")
  expect(screen.getByTestId("catalog-filter-empty")).toHaveTextContent("没有匹配项")
  expect(screen.getByTestId("catalog-filter-owner")).toHaveTextContent("旧列表责任人未在已加载记录")
  view.unmount()
  read.mockResolvedValue(catalogPage([
    catalogItem("m1", { responsibility: [responsibilityEntry("p-yi", "责任人乙")] }),
  ]))
  render(<CatalogRecords {...props} legacyOwner="p-yi" />)
  await screen.findByTestId("catalog-row-m1")
  expect(screen.getByTestId("catalog-filter-owner")).toHaveTextContent("责任人乙")
  expect(screen.queryByTestId("catalog-owner-unmatched")).not.toBeInTheDocument()
})

it("treats a changed authorized owner projection as an update, keeping options fresh", async () => {
  read.mockResolvedValue(catalogPage([catalogItem("m1", {
    responsibility: [responsibilityEntry("p-jia", "责任人甲")] })]))
  render(<CatalogRecords {...props} />)
  await screen.findByTestId("catalog-row-m1")
  // Same object and revision, but the authorization projection now resolves a
  // different Owner name; it must be offered as an update, not silently swapped.
  read.mockResolvedValue(catalogPage([catalogItem("m1", {
    responsibility: [responsibilityEntry("p-yi", "责任人乙")] })]))
  act(() => window.dispatchEvent(new Event("focus")))
  await screen.findByTestId("catalog-pending")
  fireEvent.click(screen.getByRole("button", { name: "查看最新" }))
  fireEvent.click(screen.getByTestId("catalog-filter-owner"))
  expect(await screen.findByRole("option", { name: "责任人乙" })).toBeInTheDocument()
  expect(screen.queryByRole("option", { name: "责任人甲" })).not.toBeInTheDocument()
})

it("resets filters and aborts a continuation when the type changes", async () => {
  const signals: AbortSignal[] = []
  let release!: (value: ReturnType<typeof catalogPage>) => void
  read.mockResolvedValueOnce(catalogPage([catalogItem("first", {
    responsibility: [responsibilityEntry("p-jia", "责任人甲")] })],
    { has_more: true, next_cursor: "next" }))
  const view = render(<CatalogRecords {...props} />)
  await screen.findByTestId("catalog-row-first")
  read.mockImplementationOnce((_type, _cursor, signal) => {
    if (signal) signals.push(signal)
    return new Promise((resolve) => { release = resolve })
  })
  fireEvent.click(screen.getByRole("button", { name: "加载更多" }))
  fireEvent.change(screen.getByTestId("catalog-filter-search"), { target: { value: "first" } })
  fireEvent.click(screen.getByTestId("catalog-filter-owner"))
  fireEvent.click(await screen.findByRole("option", { name: "责任人甲" }))
  read.mockResolvedValue(catalogPage([catalogItem("second")]))
  view.rerender(<CatalogRecords {...props} objectType="PCO" />)
  await waitFor(() => expect(signals[0]?.aborted).toBe(true))
  await act(async () => { release(catalogPage([catalogItem("stale")])); await Promise.resolve() })
  expect(screen.queryByTestId("catalog-row-stale")).not.toBeInTheDocument()
  expect(screen.getByTestId("catalog-filter-search")).toHaveValue("")
  expect(screen.getByTestId("catalog-filter-owner")).toHaveTextContent("全部责任人")
  expect(await screen.findByTestId("catalog-row-second")).toBeInTheDocument()
})

it("reports an access denial from a continuation instead of keeping rows", async () => {
  const onAccessDenied = vi.fn()
  read.mockResolvedValueOnce(catalogPage([catalogItem("first")], { has_more: true, next_cursor: "next" }))
  render(<CatalogRecords {...props} onAccessDenied={onAccessDenied} />)
  await screen.findByTestId("catalog-row-first")
  read.mockRejectedValueOnce(new ApiError(403, "FORBIDDEN", ""))
  fireEvent.click(screen.getByRole("button", { name: "加载更多" }))
  await waitFor(() => expect(onAccessDenied).toHaveBeenCalled())
})
