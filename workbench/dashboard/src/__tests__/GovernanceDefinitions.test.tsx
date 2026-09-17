import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { GovernanceApp } from "@/GovernanceApp"
import { catalogItem, catalogPage, detail, methodMapAll, ontologyCatalog, overview } from "./fixtures"

const identity = { scope_id: "scope", principal_id: "ceo", display_name: "测试 CEO",
                   auth_epoch: 1, assignments: [{ role: "CEO", domain_id: "domain",
                                                  assignment_id: "assignment" }] }
const response = (value: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(value), { status,
    headers: { "Content-Type": "application/json" } }))

function setup(url = "/dashboard/") {
  window.history.replaceState(null, "", url)
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const target = String(input)
    const body = target.includes("/ontology/method-map") ? methodMapAll()
      : target.includes("/ontology/catalog") ? ontologyCatalog()
      : target.includes("/catalog/objects") ? catalogPage([catalogItem()])
      : target.includes("/governance/method/tasks") ? { items: [], next_after: null }
      : target.includes("/governance/tasks") ? { items: [], next_after: null }
      : target.includes("/governance/missions") ? { items: [], next_after: null }
      : target.endsWith("/session") ? { identity, csrf: "csrf" }
      : target.includes("/overview") ? overview()
      : detail()
    return response(body)
  }))
  render(<GovernanceApp />)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/dashboard/") })

it("offers a visible 业务定义 entry from the governance sidebar", async () => {
  setup()
  const entry = await screen.findByRole("button", { name: "业务定义" })
  fireEvent.click(entry)
  await screen.findByTestId("business-definitions")
  expect(screen.getByTestId("definition-I07")).toHaveTextContent("打法")
  expect(window.location.search).toContain("view=definitions")
})

it("restores the definitions route when the page loads with the view parameter", async () => {
  setup("/dashboard/?view=definitions")
  await screen.findByTestId("business-definitions")
  expect(screen.getByTestId("definitions-view")).toHaveAttribute("aria-hidden", "false")
})

it("folds a legacy list route into the embedded explorer object details", async () => {
  setup("/dashboard/?view=list&strategy=s1&object=m1&rev=m1-r2")
  await screen.findByTestId("detail-pane")
  await waitFor(() => expect(window.location.search).toContain("view=graph"))
  expect(window.location.search).toContain("object=m1")
  expect(screen.queryByRole("button", { name: "对象列表" })).not.toBeInTheDocument()
})

it("folds a legacy list type filter into the map's real-data panel", async () => {
  setup("/dashboard/?view=list&object_type=Mission&rules=0.3")
  await screen.findByTestId("type-info-card")
  await screen.findByTestId("catalog-row-m1")
  expect(window.location.search).toContain("view=map")
  expect(window.location.search).toContain("otype=Mission")
  expect(window.location.search).toContain("object_type=Mission")
})
