import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { App } from "@/App"
import { BusinessDefinitions } from "@/components/BusinessDefinitions"
import { catalogItem, catalogPage, detail, methodMapAll, ontologyCatalog, overview } from "./fixtures"

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/dashboard/") })

function renderDefinitions(map = methodMapAll()) {
  const onOpenType = vi.fn()
  render(<BusinessDefinitions methodMap={map} loading={false} error={null}
                               onOpenType={onOpenType} />)
  return { onOpenType }
}

it("exposes all 44 concepts in four categories including unimplemented and reference entries", () => {
  renderDefinitions()
  expect(screen.getByTestId("business-definitions")).toBeInTheDocument()
  for (const category of ["anchor", "reference", "business_artifact", "evidence_runtime_record"]) {
    expect(screen.getByTestId(`definitions-category-${category}`)).toBeInTheDocument()
  }
  // Unimplemented anchors stay visible and selectable as concepts.
  expect(screen.getByTestId("definition-I07")).toHaveTextContent("打法")
  expect(screen.getByTestId("definition-I08")).toHaveTextContent("人机协同工作计划")
  // A To Define reference entry is visible without any runtime object.
  expect(screen.getByTestId("definition-I11")).toHaveTextContent("公司使命")
  expect(screen.getByTestId("definition-I11")).toHaveTextContent("根本目的")
  expect(screen.getByTestId("definition-I07-no-runtime")).toBeInTheDocument()
  expect(screen.getByTestId("definition-I08-no-runtime")).toBeInTheDocument()
  expect(screen.getByTestId("definition-I11-no-runtime")).toBeInTheDocument()
})

it("shows definition/purpose independently from compiled, scope and data statuses", () => {
  renderDefinitions()
  const play = screen.getByTestId("definition-I07")
  expect(play).toHaveTextContent("目的：")
  expect(play).toHaveTextContent("定义：")
  expect(play).toHaveTextContent("赢法")
  // The engineering gap text is not the definition.
  expect(play).not.toHaveTextContent("PDO层级仍开放")
  expect(screen.getByTestId("definition-I07-support"))
    .toHaveTextContent("未覆盖")
  expect(screen.getByTestId("definition-I07-support"))
    .toHaveTextContent("无独立对象类型")
  expect(screen.getByTestId("definition-I07-support"))
    .toHaveTextContent("本 scope 无已启用契约")
  expect(screen.getByTestId("definition-I07-availability"))
    .toHaveTextContent("不适用（无对象类型）")
  // A compiled+scope-enabled concept keeps its data status separate.
  expect(screen.getByTestId("definition-I06-support"))
    .toHaveTextContent("已编译")
  expect(screen.getByTestId("definition-I06-availability"))
    .toHaveTextContent("未查询")
})

it("routes a supported runtime link to the protocol view without inventing one", () => {
  const { onOpenType } = renderDefinitions()
  fireEvent.click(screen.getByTestId("definition-I06-link-Mission"))
  expect(onOpenType).toHaveBeenCalledWith("Mission")
  expect(screen.queryByTestId("definition-I07-link-Mission")).not.toBeInTheDocument()
})

it("states when the definition list is unavailable instead of showing an empty map", () => {
  render(<BusinessDefinitions methodMap={null} loading={false} error={new Error("boom")}
                               onOpenType={vi.fn()} />)
  expect(screen.getByTestId("definitions-error")).toBeInTheDocument()
  expect(screen.queryByTestId("business-definitions")).not.toBeInTheDocument()
})

it("is reachable as a separate top-level view from the protocol map", async () => {
  window.history.replaceState(null, "", "/dashboard/")
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    const body = url.includes("/ontology/method-map") ? methodMapAll()
      : url.includes("/ontology/catalog") ? ontologyCatalog()
      : url.includes("/catalog/objects") ? catalogPage([catalogItem()])
      : url.includes("/overview") ? overview()
      : detail()
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: 200, headers: { "Content-Type": "application/json" } }))
  }))
  render(<App />)
  await screen.findByTestId("ontology-map")
  fireEvent.click(screen.getByTestId("view-tab-definitions"))
  await screen.findByTestId("business-definitions")
  expect(screen.getByTestId("definition-I07")).toBeInTheDocument()
  // Switching back keeps the old protocol view available and separate.
  expect(screen.getByTestId("definitions-view")).toHaveAttribute("aria-hidden", "false")
  fireEvent.click(screen.getByTestId("view-tab-map"))
  await waitFor(() => expect(screen.getByTestId("ontology-map")).toBeInTheDocument())
  expect(screen.getByTestId("definitions-view")).toHaveAttribute("aria-hidden", "true")
})
