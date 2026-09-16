import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { CatalogRecords } from "@/components/CatalogRecords"
import { fetchCatalogObjects } from "@/lib/api"
import { catalogItem, catalogPage } from "./fixtures"
vi.mock("@/lib/api", () => ({ fetchCatalogObjects: vi.fn() }))
const read = vi.mocked(fetchCatalogObjects)
beforeEach(() => { read.mockReset() })
afterEach(cleanup)
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
