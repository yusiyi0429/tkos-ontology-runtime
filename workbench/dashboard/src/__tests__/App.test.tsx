import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { App } from "@/App"
import { catalogItem, catalogPage, detail, methodMap, ontologyCatalog, overview,
         responsibilityEntry } from "@/__tests__/fixtures"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" } })
}

function denial(): Response {
  return jsonResponse({ error: { code: "FORBIDDEN",
                                 message: "Current authority does not permit this operation." } }, 403)
}

type Api = {
  overview?: () => Response | Promise<Response>
  catalog?: () => Response | Promise<Response>
  records?: () => Response | Promise<Response>
  detail?: () => Response | Promise<Response>
}

/** Stubs the authorized dashboard reads with synthetic fixtures. */
function stubApi(api: Api = {}) {
  const fetcher = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes("/ontology/catalog")) {
      return Promise.resolve(api.catalog?.() ?? jsonResponse(ontologyCatalog()))
    }
    if (url.includes("/ontology/method-map")) return Promise.resolve(jsonResponse(methodMap()))
    if (url.includes("/catalog/objects")) {
      return Promise.resolve(api.records?.() ?? jsonResponse(catalogPage([catalogItem()])))
    }
    if (url.includes("/overview")) return Promise.resolve(api.overview?.() ?? jsonResponse(overview()))
    if (url.includes("/objects/")) return Promise.resolve(api.detail?.() ?? jsonResponse(detail()))
    return Promise.resolve(jsonResponse({}, 404))
  })
  vi.stubGlobal("fetch", fetcher)
  return fetcher
}

beforeEach(() => {
  window.history.replaceState(null, "", "/dashboard/?view=map&rules=0.4")
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/dashboard/")
})

describe("read-only explorer navigation", () => {
  it("offers only map, definitions and graph; the old list entry is gone", async () => {
    stubApi()
    render(<App />)
    await screen.findByTestId("view-tabs")
    for (const name of ["map", "definitions", "graph"]) {
      expect(screen.getByTestId(`view-tab-${name}`)).toBeInTheDocument()
    }
    expect(screen.queryByTestId("view-tab-list")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "列表" })).not.toBeInTheDocument()
  })
})

describe("legacy object-list routes", () => {
  it("opens the exact object's details and rewrites the list route", async () => {
    window.history.replaceState(null, "",
      "/dashboard/?view=list&strategy=s1&object=m1&rev=m1-r2&group=mission&owner=p1")
    stubApi()
    render(<App />)
    await screen.findByTestId("detail-pane")
    expect(within(screen.getByTestId("detail-pane")).getAllByText("任务 m1").length)
      .toBeGreaterThan(0)
    await waitFor(() => expect(window.location.search).toContain("view=graph"))
    expect(window.location.search).toContain("object=m1")
    expect(window.location.search).toContain("rev=m1-r2")
    // The legacy list filter references survive the normalization.
    expect(window.location.search).toContain("group=mission")
    expect(window.location.search).toContain("owner=p1")
  })

  it("folds a list type filter into the map records and preserves every filter", async () => {
    window.history.replaceState(null, "",
      "/dashboard/?view=list&object_type=Mission&owner=p1&period_from=2026-09-01&domain=d1")
    stubApi({ records: () => jsonResponse(catalogPage([
      catalogItem("m1", { responsibility: [responsibilityEntry("p1", "责任人甲")] }),
      catalogItem("m2", { responsibility: [responsibilityEntry("p2", "责任人乙")] }),
    ])) })
    render(<App />)
    await screen.findByTestId("type-info-card")
    await screen.findByTestId("catalog-row-m1")
    // The legacy owner filter is applied to the embedded records list.
    expect(screen.queryByTestId("catalog-row-m2")).not.toBeInTheDocument()
    expect(screen.getByTestId("catalog-filter-owner")).toHaveTextContent("责任人甲")
    // Filters this type-scoped authorized read cannot apply are stated visibly.
    expect(screen.getByTestId("legacy-filter-note")).toHaveTextContent("业务域")
    expect(screen.getByTestId("legacy-filter-note")).toHaveTextContent("周期")
    expect(window.location.search).toContain("view=map")
    expect(window.location.search).toContain("otype=Mission")
    expect(window.location.search).toContain("object_type=Mission")
    expect(window.location.search).toContain("owner=p1")
    expect(window.location.search).toContain("period_from=2026-09-01")
    expect(window.location.search).toContain("domain=d1")
  })

  it("keeps the pinned revision when the default strategy is adopted after load", async () => {
    const calls: string[] = []
    let releaseOverview!: (value: Response) => void
    const overviewGate = new Promise<Response>((resolve) => { releaseOverview = resolve })
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url.includes("/overview")) return overviewGate
      if (url.includes("/ontology/catalog")) return Promise.resolve(jsonResponse(ontologyCatalog()))
      if (url.includes("/ontology/method-map")) return Promise.resolve(jsonResponse(methodMap()))
      if (url.includes("/downstream")) {
        return Promise.resolve(jsonResponse({ items: [], next_cursor: null, has_more: false,
                                              limit: 25, bounded: 100 }))
      }
      if (url.includes("/objects/")) return Promise.resolve(jsonResponse(detail()))
      return Promise.resolve(jsonResponse({}, 404))
    }))
    window.history.replaceState(null, "", "/dashboard/?view=list&object=m1&rev=m1-r1")
    render(<App />)
    // The legacy route normalizes immediately with the exact revision intact and
    // no strategy yet; the overview default arrives afterwards.
    await screen.findByTestId("detail-pane")
    await waitFor(() => expect(window.location.search).toContain("view=graph"))
    expect(window.location.search).toContain("object=m1")
    expect(window.location.search).toContain("rev=m1-r1")
    expect(window.location.search).not.toContain("strategy=")
    await act(async () => {
      releaseOverview(jsonResponse(overview()))
      await overviewGate
    })
    // Initial default adoption is not a user strategy switch: the pinned
    // revision survives in the URL and the detail read keeps the exact revision.
    await waitFor(() => expect(window.location.search).toContain("strategy=s1"))
    expect(window.location.search).toContain("rev=m1-r1")
    await waitFor(() => {
      const reads = calls.filter((url) => url.includes("/objects/m1"))
      expect(reads.length).toBeGreaterThan(0)
      expect(reads[reads.length - 1]).toContain("revision_id=m1-r1")
      expect(reads[reads.length - 1]).toContain("strategy_id=s1")
    })
    // An explicit refresh still reads the same exact pinned revision.
    await userEvent.setup().click(screen.getByRole("button", { name: "立即刷新" }))
    expect(window.location.search).toContain("rev=m1-r1")
    await waitFor(() => {
      const reads = calls.filter((url) => url.includes("/objects/m1"))
      expect(reads[reads.length - 1]).toContain("revision_id=m1-r1")
    })
  })

  it("normalizes a bare list route to the ontology map", async () => {
    window.history.replaceState(null, "", "/dashboard/?view=list")
    stubApi()
    render(<App />)
    await screen.findByTestId("ontology-map")
    expect(screen.getByTestId("view-tab-map")).toHaveAttribute("aria-current", "page")
    await waitFor(() => expect(window.location.search).toContain("view=map"))
    expect(window.location.search).not.toContain("view=list")
  })
})

describe("authorization boundaries", () => {
  it("invalidates protected records when the authorization version changes", async () => {
    const user = userEvent.setup()
    let releaseLate!: () => void
    const gate = new Promise<void>((resolve) => { releaseLate = resolve })
    let epoch = 1
    let armed = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview({
        viewer: { scope_id: "s", principal_id: "p-ceo", principal_type: "human",
                  display_name: "合成 CEO", auth_epoch: epoch, assignments: [] } })))
      if (url.includes("/ontology/catalog")) return Promise.resolve(jsonResponse(ontologyCatalog()))
      if (url.includes("/ontology/method-map")) return Promise.resolve(jsonResponse(methodMap()))
      if (url.includes("/catalog/objects")) {
        // Each reload gets its own Response; a shared body would be read twice.
        return armed ? gate.then(() => jsonResponse(catalogPage([catalogItem()])))
          : Promise.resolve(jsonResponse(catalogPage([catalogItem()])))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    window.history.replaceState(null, "",
      "/dashboard/?view=map&otype=Mission&object_type=Mission")
    render(<App />)
    await screen.findByTestId("catalog-row-m1")
    epoch = 2
    armed = true
    await user.click(screen.getByRole("button", { name: "立即刷新" }))
    // The old protected projection is cleared before the reload resolves.
    await waitFor(() => expect(screen.queryByTestId("catalog-row-m1")).not.toBeInTheDocument())
    await act(async () => {
      releaseLate()
      await gate
    })
    expect(await screen.findByTestId("catalog-row-m1")).toBeInTheDocument()
  })

  it("clears protected records on 403 and a late response cannot restore them", async () => {
    const user = userEvent.setup()
    let releaseLate!: () => void
    const gate = new Promise<void>((resolve) => { releaseLate = resolve })
    let armed = false
    let denyOverview = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) {
        return Promise.resolve(denyOverview
          ? denial() : jsonResponse(overview()))
      }
      if (url.includes("/ontology/catalog")) return Promise.resolve(jsonResponse(ontologyCatalog()))
      if (url.includes("/ontology/method-map")) return Promise.resolve(jsonResponse(methodMap()))
      if (url.includes("/catalog/objects")) {
        return armed ? gate.then(() => jsonResponse(catalogPage([catalogItem()])))
          : Promise.resolve(jsonResponse(catalogPage([catalogItem()])))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    window.history.replaceState(null, "",
      "/dashboard/?view=map&otype=Mission&object_type=Mission")
    render(<App />)
    await screen.findByTestId("catalog-row-m1")
    // An in-flight records refresh that will ignore its abort.
    armed = true
    await act(async () => { window.dispatchEvent(new Event("focus")) })
    denyOverview = true
    await user.click(screen.getByRole("button", { name: "立即刷新" }))
    await screen.findByTestId("auth-lost")
    await waitFor(() => expect(screen.queryByTestId("catalog-row-m1")).not.toBeInTheDocument())
    await act(async () => {
      releaseLate()
      await gate
    })
    expect(screen.queryByTestId("catalog-row-m1")).not.toBeInTheDocument()
    expect(screen.getByTestId("auth-lost")).toBeInTheDocument()
  })
})

describe("detail paging", () => {
  it("clears history and receipt pagination when the exact revision changes", async () => {
    const user = userEvent.setup()
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/downstream")) {
        return Promise.resolve(jsonResponse({ items: [], next_cursor: null, has_more: false,
                                              limit: 25, bounded: 100 }))
      }
      if (url.includes("/revisions")) {
        return Promise.resolve(jsonResponse({ items: [], next_cursor: null }))
      }
      if (url.includes("/receipts")) {
        return Promise.resolve(jsonResponse({ items: [], next_cursor: null }))
      }
      if (url.includes("/objects/")) {
        const value = detail()
        value.history.next_cursor = "h1"
        value.receipts = { items: [{ receipt_id: "old-r", action_type: "m1b_confirm_ltco",
                                     recorded_at: "2026-09-16T01:00:00+00:00" }],
                           next_cursor: "p1" }
        return Promise.resolve(jsonResponse(value))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    window.history.replaceState(null, "", "/dashboard/?view=graph&object=m1")
    render(<App />)
    await screen.findByTestId("detail-pane")
    await user.click(screen.getByRole("tab", { name: "版本与候选" }))
    expect(screen.getByRole("button", { name: "加载更多版本" })).toBeInTheDocument()
    await user.click(screen.getByRole("tab", { name: "证据与确认" }))
    expect(screen.getByRole("button", { name: "加载更多回执" })).toBeInTheDocument()
    // Select the historical revision: both manual cursors must reset.
    await user.click(screen.getByRole("tab", { name: "版本与候选" }))
    await user.click(screen.getAllByRole("button", { name: "查看此版本" })[0])
    await waitFor(() => expect(window.location.search).toContain("rev=m1-r1"))
    await waitFor(() => expect(screen.queryByRole("button", { name: "加载更多版本" })).not.toBeInTheDocument())
  })
})
