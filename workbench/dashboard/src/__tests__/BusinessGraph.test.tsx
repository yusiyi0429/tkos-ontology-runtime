import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { BusinessGraph } from "@/components/BusinessGraph"
import { fetchDetail } from "@/lib/api"
import { ApiError } from "@/lib/errors"
import { detail } from "./fixtures"
import type { DownstreamEdge } from "@/lib/types"
vi.mock("@/lib/api", () => ({ fetchDetail: vi.fn(), fetchDownstream: vi.fn() }))
const read = vi.mocked(fetchDetail)
const props = { strategyId: "s1", entryFocus: { objectId: "m1", revisionId: "m1-r2", label: "入口" }, entryKey: "first", active: true, selectedObject: null, selectedRevision: null, onOpenObject: vi.fn(), onAccessDenied: vi.fn() }
function edge(id: string, type = "PeriodReview", status = "agent_analysis"): DownstreamEdge {
  return { object_id: id, object_type: type, domain_id: "d1", title: id, ref: { object_id: id, revision_id: id + "r" }, matched_fields: ["target_refs"], formal_state: { status, formal: false } }
}
function root(edges: DownstreamEdge[] = []) {
  const d = detail()
  d.relations.downstream.items = edges
  return d
}
beforeEach(() => { read.mockReset(); props.onAccessDenied.mockReset() })
afterEach(cleanup)
it("keeps historical exact basis and analysis materials, with candidate opt-in", async () => {
  const d = root([edge("review"), edge("candidate", "OperatingState", "recommendation")])
  d.basis = { ...d.basis, status: "historical", strategy_ref: { object_id: "old", revision_id: "old-r" } }
  d.relations.own_basis_refs = [{ object_id: "old", revision_id: "old-r", path: "strategy_ref", object_type: "Strategy", domain_id: "d1", title: "旧战略", lifecycle_status: "superseded" }]
  read.mockResolvedValue(d)
  render(<BusinessGraph {...props} />)
  await screen.findByTestId("graph-node-review-reviewr")
  expect(screen.getByTestId("graph-node-old-old-r")).toBeInTheDocument()
  expect(screen.queryByTestId("graph-node-candidate-candidater")).not.toBeInTheDocument()
  expect(screen.getByTestId("graph-node-m1-m1-r2")).toHaveTextContent("沿用历史依据")
  fireEvent.click(screen.getByTestId("toggle-non-formal"))
  expect(screen.getByTestId("graph-node-candidate-candidater")).toBeInTheDocument()
})
it("refresh replaces vanished adjacency and updates remaining titles", async () => {
  read.mockResolvedValue(root([edge("keep"), edge("gone")]))
  render(<BusinessGraph {...props} />)
  await screen.findByTestId("graph-node-gone-goner")
  read.mockResolvedValue(root([{ ...edge("keep"), title: "新标题" }]))
  act(() => window.dispatchEvent(new Event("focus")))
  await waitFor(() => expect(screen.queryByTestId("graph-node-gone-goner")).not.toBeInTheDocument())
  expect(screen.getByTestId("graph-node-keep-keepr")).toHaveTextContent("新标题")
})
it("initial failure waits for an explicit retry instead of looping", async () => {
  read.mockRejectedValueOnce(new Error("offline"))
  render(<BusinessGraph {...props} />)
  await screen.findByTestId("graph-error-m1-m1-r2")
  expect(read).toHaveBeenCalledTimes(1)
  read.mockResolvedValue(root())
  fireEvent.click(screen.getByTestId("graph-retry-m1-m1-r2"))
  await waitFor(() => expect(screen.queryByTestId("graph-error-m1-m1-r2")).not.toBeInTheDocument())
  expect(read).toHaveBeenCalledTimes(2)
})
it("reset ignores a late old response and rebuilds the unchanged root", async () => {
  let resolve!: (d: ReturnType<typeof detail>) => void
  read.mockImplementationOnce(() => new Promise((r) => { resolve = r }))
  const { rerender } = render(<BusinessGraph {...props} />)
  await waitFor(() => expect(read).toHaveBeenCalledOnce())
  read.mockResolvedValue(root([edge("current")]))
  rerender(<BusinessGraph {...props} entryKey="new-authority" />)
  await screen.findByTestId("graph-node-current-currentr")
  await act(async () => resolve(root([edge("late-secret")])) )
  expect(screen.queryByTestId("graph-node-late-secret-late-secretr")).not.toBeInTheDocument()
})
it("refresh distinguishes network staleness from an authority denial", async () => {
  read.mockResolvedValue(root())
  render(<BusinessGraph {...props} />)
  await waitFor(() => expect(screen.getByTestId("graph-expand-m1-m1-r2")).toHaveTextContent("已展开"))
  read.mockRejectedValue(new Error("offline"))
  act(() => window.dispatchEvent(new Event("focus")))
  await screen.findByTestId("graph-stale")
  expect(props.onAccessDenied).not.toHaveBeenCalled()
  read.mockRejectedValue(new ApiError(403, "FORBIDDEN", "denied"))
  act(() => window.dispatchEvent(new Event("focus")))
  await waitFor(() => expect(props.onAccessDenied).toHaveBeenCalledOnce())
})
