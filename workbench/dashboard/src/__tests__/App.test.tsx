import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { App } from "@/App"
import { detail, missionItem, objectsPage, overview } from "@/__tests__/fixtures"

interface Deferred<T> { promise: Promise<T>; resolve: (value: T) => void }
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => { resolve = res })
  return { promise, resolve }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" } })
}

function denial(): Response {
  return jsonResponse({ error: { code: "FORBIDDEN",
                                 message: "Current authority does not permit this operation." } }, 403)
}

// The legacy list/detail flows pinned by this file run in the list view; the
// ontology map is the default view and is covered by OntologyViews.test.tsx.
beforeEach(() => {
  window.history.replaceState(null, "", "/dashboard/?view=list")
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, "", "/dashboard/")
})

describe("App", () => {
  it("loads overview, auto-selects the single Strategy and opens a listed object", async () => {
    const user = userEvent.setup()
    const calls: string[] = []
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")])))
      if (url.includes("/objects/")) return Promise.resolve(jsonResponse(detail()))
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("viewer-name")).toHaveTextContent("合成 CEO"))
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    await user.click(screen.getByTestId("object-row-m1"))
    await waitFor(() => expect(screen.getByTestId("detail-pane")).toBeInTheDocument())
    expect(window.location.search).toContain("object=m1")
  })

  it("invalidates protected projections when the authorization version changes", async () => {
    const user = userEvent.setup()
    let epoch = 1
    let items = [missionItem("m1")]
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview({
        viewer: { scope_id: "s", principal_id: "p-ceo", principal_type: "human",
                  display_name: "合成 CEO", auth_epoch: epoch, assignments: [] } })))
      if (url.includes("/objects?")) return Promise.resolve(jsonResponse(objectsPage(items)))
      if (url.includes("/objects/")) return Promise.resolve(jsonResponse(detail()))
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    // A revocation bumps the authorization epoch and removes the row.
    epoch = 2
    items = []
    await user.click(screen.getByRole("button", { name: "立即刷新" }))
    await waitFor(() => expect(screen.queryByTestId("object-row-m1")).not.toBeInTheDocument())
    expect(screen.queryByTestId("pending-banner")).not.toBeInTheDocument()
    expect(screen.getByTestId("list-empty")).toBeInTheDocument()
  })

  it("clears protected content on 403 and an in-flight list response cannot restore it", async () => {
    const user = userEvent.setup()
    const lateList = deferred<Response>()
    let armRefresh = false
    let detailCalls = 0
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        if (armRefresh) {
          armRefresh = false
          return lateList.promise // an in-flight refresh that ignores its abort
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")])))
      }
      if (url.includes("/objects/")) {
        detailCalls += 1
        return Promise.resolve(denial()) // exact-object read is no longer authorized
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armRefresh = true
    await user.click(screen.getByRole("button", { name: "立即刷新" }))
    await user.click(screen.getByTestId("object-row-m1"))
    await waitFor(() => expect(detailCalls).toBeGreaterThan(0))
    await waitFor(() => expect(screen.getByTestId("auth-lost")).toBeInTheDocument())
    // The private header/overview is cleared as well, not just the list.
    await waitFor(() => expect(screen.getByTestId("viewer-name")).toHaveTextContent("未加载"))
    await act(async () => {
      lateList.resolve(jsonResponse(objectsPage([missionItem("m1")])))
      await lateList.promise
    })
    expect(screen.queryByTestId("object-row-m1")).not.toBeInTheDocument()
    expect(screen.getByTestId("auth-lost")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "加载更多版本" })).not.toBeInTheDocument()
  })

  it("keeps stale data and shows a Chinese recovery message for a storage 503", async () => {
    const user = userEvent.setup()
    let armFailure = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        if (armFailure) {
          armFailure = false
          return Promise.resolve(jsonResponse({ error: { code: "EVIDENCE_UNAVAILABLE",
            message: "Evidence storage configuration is unavailable" } }, 503))
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")])))
      }
      if (url.includes("/objects/")) return Promise.resolve(jsonResponse(detail()))
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armFailure = true
    await user.click(screen.getByRole("button", { name: "立即刷新" }))
    await waitFor(() => expect(screen.getByTestId("error-banner")).toBeInTheDocument())
    expect(screen.getByTestId("error-banner")).toHaveTextContent("证据存储暂不可用")
    expect(screen.getByTestId("error-banner")).not.toHaveTextContent("Evidence storage")
    expect(screen.getByTestId("stale-banner")).toBeInTheDocument()
  })
})

describe("App load-more and authorization observation", () => {
  it("ignores a delayed load-more page after filters change", async () => {
    const user = userEvent.setup()
    const latePage = deferred<Response>()
    let armed = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        if (armed && url.includes("cursor=c1")) {
          armed = false
          return latePage.promise
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")],
          { next_cursor: "c1", has_more: true })))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armed = true
    await user.click(screen.getByRole("button", { name: "加载更多" }))
    // The filter changes while the continuation is in flight.
    fireEvent.change(document.getElementById("period-from") as HTMLInputElement,
                     { target: { value: "2026-09-01" } })
    await act(async () => {
      latePage.resolve(jsonResponse(objectsPage([missionItem("m9")], { next_cursor: null })))
      await latePage.promise
    })
    expect(screen.queryByTestId("object-row-m9")).not.toBeInTheDocument()
  })

  it("treats a final page as exhausted and never resurrects the first cursor", async () => {
    const user = userEvent.setup()
    let calls = 0
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        calls += 1
        if (url.includes("cursor=c1")) {
          return Promise.resolve(jsonResponse(objectsPage([missionItem("m2")], { next_cursor: null })))
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")],
          { next_cursor: "c1", has_more: true })))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    await user.click(screen.getByRole("button", { name: "加载更多" }))
    await waitFor(() => expect(screen.getByTestId("object-row-m2")).toBeInTheDocument())
    expect(screen.queryByRole("button", { name: "加载更多" })).not.toBeInTheDocument()
    expect(calls).toBeGreaterThanOrEqual(2)
  })

  it("shows a retry hint when load-more fails without clearing loaded rows", async () => {
    const user = userEvent.setup()
    let armed = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        if (armed) {
          armed = false
          return Promise.resolve(jsonResponse({ error: { code: "EVIDENCE_UNAVAILABLE",
            message: "storage" } }, 503))
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")],
          { next_cursor: "c1", has_more: true })))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armed = true
    await user.click(screen.getByRole("button", { name: "加载更多" }))
    await waitFor(() => expect(screen.getByTestId("load-more-error")).toBeInTheDocument())
    expect(screen.getByTestId("object-row-m1")).toBeInTheDocument()
  })

  it("clears the list when a load-more is denied (403)", async () => {
    const user = userEvent.setup()
    let armed = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        if (armed) {
          armed = false
          return Promise.resolve(denial())
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")],
          { next_cursor: "c1", has_more: true })))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armed = true
    await user.click(screen.getByRole("button", { name: "加载更多" }))
    await waitFor(() => expect(screen.getByTestId("auth-lost")).toBeInTheDocument())
    expect(screen.queryByTestId("object-row-m1")).not.toBeInTheDocument()
  })

  it("invalidates protected projections from a pending overview response", async () => {
    const user = userEvent.setup()
    let phase = 1
    let items = [missionItem("m1")]
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) {
        const groups = overview().groups.map((group) =>
          group.group === "ltco" ? { ...group, available: phase === 1 } : group)
        return Promise.resolve(jsonResponse(overview({
          groups,
          viewer: { scope_id: "s", principal_id: "p-ceo", principal_type: "human",
                    display_name: "合成 CEO", auth_epoch: phase, assignments: [] } })))
      }
      if (url.includes("/objects?")) return Promise.resolve(jsonResponse(objectsPage(items)))
      if (url.includes("/objects/")) return Promise.resolve(jsonResponse(detail()))
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    // A revocation changes both authorization identity and group availability;
    // the overview response is offered as a pending update, but the protected
    // list must already be invalidated from the observed successful payload.
    phase = 2
    items = []
    await user.click(screen.getByRole("button", { name: "立即刷新" }))
    await waitFor(() => expect(screen.queryByTestId("object-row-m1")).not.toBeInTheDocument())
    expect(screen.getByTestId("list-empty")).toBeInTheDocument()
  })
})

describe("App same-screen basis groups", () => {
  it("shows current-basis and historical-basis rows together by default", async () => {
    const historical = missionItem("m-old", {
      title: "旧战略下的正式任务",
      basis: { status: "historical", reason: "recorded_other_strategy", impact_linked: true,
               selected_strategy_ref: { object_id: "s2", revision_id: "s2r1" },
               strategy_ref: { object_id: "s1", revision_id: "s1r1" }, basis_revision: "effective" },
    })
    const current = missionItem("m-new", { title: "当前战略下的任务" })
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        return Promise.resolve(jsonResponse(objectsPage([historical, current])))
      }
      if (url.includes("/objects/")) return Promise.resolve(jsonResponse(detail()))
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m-old")).toBeInTheDocument())
    // Both groups are on the same screen, each with its own label.
    expect(screen.getByTestId("basis-section-当前依据")).toBeInTheDocument()
    expect(screen.getByTestId("basis-section-历史依据（旧 Strategy）")).toBeInTheDocument()
    expect(screen.getByTestId("object-row-m-new")).toBeInTheDocument()
    expect(window.location.search).toContain("basis=all")
  })
})

describe("App manual loader lifecycle", () => {
  it("aborts an in-flight load-more on filter change and resets its loading state", async () => {
    const user = userEvent.setup()
    const signals: AbortSignal[] = []
    const latePage = deferred<Response>()
    let armed = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        if (armed && url.includes("cursor=c1")) {
          armed = false
          if (init?.signal) signals.push(init.signal)
          return latePage.promise
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")],
          { next_cursor: "c1", has_more: true })))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armed = true
    await user.click(screen.getByRole("button", { name: "加载更多" }))
    fireEvent.change(document.getElementById("period-from") as HTMLInputElement,
                     { target: { value: "2026-09-01" } })
    await waitFor(() => expect(signals[0]?.aborted).toBe(true))
    // The loading flag is reset, so the control is usable again.
    await waitFor(() => expect(screen.getByRole("button", { name: "加载更多" })).not.toBeDisabled())
    await act(async () => {
      latePage.resolve(jsonResponse(objectsPage([missionItem("m9")], { next_cursor: null })))
      await latePage.promise
    })
    expect(screen.queryByTestId("object-row-m9")).not.toBeInTheDocument()
  })

  it("ignores a stale 403 from a superseded load-more instead of clearing the new view", async () => {
    const user = userEvent.setup()
    const lateDenial = deferred<Response>()
    let armed = false
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) {
        if (armed && url.includes("cursor=c1")) {
          armed = false
          return lateDenial.promise
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")],
          { next_cursor: "c1", has_more: true })))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armed = true
    await user.click(screen.getByRole("button", { name: "加载更多" }))
    fireEvent.change(document.getElementById("period-from") as HTMLInputElement,
                     { target: { value: "2026-09-01" } })
    await act(async () => {
      lateDenial.resolve(denial())
      await lateDenial.promise
    })
    expect(screen.queryByTestId("auth-lost")).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
  })

  it("aborts manual paging when the authorization identity changes", async () => {
    const user = userEvent.setup()
    const signals: AbortSignal[] = []
    const latePage = deferred<Response>()
    let armed = false
    let epoch = 1
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview({
        viewer: { scope_id: "s", principal_id: "p-ceo", principal_type: "human",
                  display_name: "合成 CEO", auth_epoch: epoch, assignments: [] } })))
      if (url.includes("/objects?")) {
        if (armed && url.includes("cursor=c1")) {
          armed = false
          if (init?.signal) signals.push(init.signal)
          return latePage.promise
        }
        return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")],
          { next_cursor: "c1", has_more: true })))
      }
      return Promise.resolve(jsonResponse({}, 404))
    }))
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    armed = true
    await user.click(screen.getByRole("button", { name: "加载更多" }))
    epoch = 2 // a revocation bumped auth_epoch
    await user.click(screen.getByRole("button", { name: "立即刷新" }))
    await waitFor(() => expect(signals[0]?.aborted).toBe(true))
    await act(async () => {
      latePage.resolve(jsonResponse(objectsPage([missionItem("m9")], { next_cursor: null })))
      await latePage.promise
    })
    expect(screen.queryByTestId("object-row-m9")).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole("button", { name: "加载更多" })).not.toBeDisabled())
  })

  it("clears history and receipt pagination when the exact revision changes", async () => {
    const user = userEvent.setup()
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(overview()))
      if (url.includes("/objects?")) return Promise.resolve(jsonResponse(objectsPage([missionItem("m1")])))
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
    render(<App />)
    await waitFor(() => expect(screen.getByTestId("object-row-m1")).toBeInTheDocument())
    await user.click(screen.getByTestId("object-row-m1"))
    await waitFor(() => expect(screen.getByTestId("detail-pane")).toBeInTheDocument())
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
