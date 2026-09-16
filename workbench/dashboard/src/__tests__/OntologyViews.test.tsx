import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { App } from "@/App"
import { catalogItem, catalogPage, detail, ontologyCatalog, overview } from "./fixtures"
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/dashboard/") })
function setup(query = "") {
  window.history.replaceState(null, "", "/dashboard/" + query)
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    const body = url.includes("/ontology/catalog") ? ontologyCatalog()
      : url.includes("/catalog/objects") ? catalogPage([catalogItem()])
      : url.includes("/overview") ? overview()
      : detail()
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  render(<App />)
}
it("default map links a type to authorized records and an exact-revision graph", async () => {
  setup()
  await screen.findByTestId("map-area-toggle-objectives")
  fireEvent.click(screen.getByTestId("map-area-toggle-objectives"))
  fireEvent.click(screen.getByTestId("map-type-Mission"))
  fireEvent.click(screen.getByTestId("view-type-data"))
  fireEvent.click(await screen.findByTestId("catalog-row-m1"))
  await screen.findByTestId("graph-node-m1-m1-r2")
  expect(window.location.search).toContain("rev=m1-r2")
  fireEvent.click(screen.getByTestId("view-tab-map"))
  expect(screen.getByTestId("map-area-toggle-objectives")).toHaveAttribute("aria-expanded", "true")
})
it("unknown rule versions never display the newest type card as a fallback", async () => {
  setup("?rules=9.9&otype=Mission")
  await screen.findByTestId("rules-invalid")
  await waitFor(() => expect(screen.getByTestId("viewer-name")).toHaveTextContent("合成 CEO"))
  expect(screen.queryByTestId("type-info-card")).not.toBeInTheDocument()
})
it("old rules do not show Architecture through a direct type URL", async () => {
  setup("?rules=0.1&otype=StrategicArchitecture")
  await screen.findByTestId("ontology-map")
  await waitFor(() => expect(window.location.search).not.toContain("otype="))
  expect(screen.queryByTestId("type-info-card")).not.toBeInTheDocument()
})
it("mobile type-to-record navigation closes the old type sheet", async () => {
  const media = vi.spyOn(window, "matchMedia").mockReturnValue({ matches: false, media: "", onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: () => true })
  try {
    setup()
    await screen.findByTestId("map-area-toggle-objectives")
    fireEvent.click(screen.getByTestId("map-area-toggle-objectives"))
    fireEvent.click(screen.getByTestId("map-type-Mission"))
    fireEvent.click(screen.getByTestId("view-type-data"))
    fireEvent.click(await screen.findByTestId("catalog-row-m1"))
    await screen.findByTestId("detail-pane")
    expect(screen.getAllByRole("dialog")).toHaveLength(1)
    expect(screen.queryByTestId("type-info-card")).not.toBeInTheDocument()
  } finally { media.mockRestore() }
})
