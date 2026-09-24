import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { BusinessMainline } from "@/BusinessMainline"
import { fetchDownstream, fetchObjects } from "@/lib/api"
import { ApiError } from "@/lib/errors"
import { missionItem, objectsPage } from "./fixtures"
import type { DownstreamEdge, ObjectListItem } from "@/lib/types"

vi.mock("@/lib/api", () => ({ fetchObjects: vi.fn(), fetchDownstream: vi.fn() }))
const list = vi.mocked(fetchObjects)
const down = vi.mocked(fetchDownstream)
const onOpen = vi.fn()
const onAuthLost = vi.fn()

function item(id: string, type: string, overrides: Partial<ObjectListItem> = {}): ObjectListItem {
  return missionItem(id, { object_type: type, title: `${type} ${id}`, ...overrides })
}

function edge(id: string, revisionId: string, type: string): DownstreamEdge {
  return { object_id: id, object_type: type, domain_id: "d1", title: id,
           ref: { object_id: id, revision_id: revisionId },
           matched_fields: ["parent_ltco_ref"], formal_state: { status: "confirmed", formal: true } }
}

/** One page per lane, keyed by the dashboard group the lane reads. */
function lanes(groups: Record<string, ObjectListItem[]>) {
  list.mockImplementation((query) =>
    Promise.resolve(objectsPage(groups[query.group] ?? [], { group: query.group })))
}

const lane = (name: string) => screen.getByRole("region", { name })

beforeEach(() => {
  list.mockReset(); down.mockReset(); onOpen.mockReset(); onAuthLost.mockReset()
  down.mockResolvedValue({ items: [], next_cursor: null, has_more: false, limit: 25, bounded: 0 })
})
afterEach(cleanup)

it("shows the four lanes and hides candidates behind the formal default", async () => {
  lanes({
    strategy: [item("s1", "Strategy")],
    ltco: [item("l1", "LTCO")],
    pco: [item("p1", "PCO", { formal_state: { status: "candidate", formal: false } })],
    mission: [item("m1", "Mission")],
  })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  await screen.findByTestId("lane-strategy-s1")
  for (const name of ["战略 Strategy", "长期目标 LTCO", "阶段目标 PCO", "任务 Mission"]) {
    expect(lane(name)).toBeInTheDocument()
  }
  // A candidate never fills a lane that has no formal object.
  expect(within(lane("阶段目标 PCO")).getByText("暂无正式对象。")).toBeInTheDocument()
  expect(screen.queryByText("PCO p1")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "候选" }))
  expect(await within(lane("阶段目标 PCO")).findByText("PCO p1")).toBeInTheDocument()
  expect(within(lane("任务 Mission")).getByText("暂无候选对象。")).toBeInTheDocument()
})

it("narrows the PCO lane to the selected LTCO's exact revision", async () => {
  lanes({
    strategy: [], ltco: [item("l1", "LTCO", { basis_revision_id: "l1-r2" })],
    // Same object list; only `keep` is recorded against l1-r2.
    pco: [item("keep", "PCO", { basis_revision_id: "keep-r1" }),
          item("other", "PCO", { basis_revision_id: "other-r1" })],
    mission: [],
  })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  const board = lane("阶段目标 PCO")
  expect(await within(board).findByText("PCO other")).toBeInTheDocument()
  down.mockResolvedValue({ items: [edge("keep", "keep-r1", "PCO"), edge("ignored", "x", "ReviewWindow")],
                           next_cursor: null, has_more: false, limit: 25, bounded: 0 })
  fireEvent.click(screen.getByTestId("lane-ltco-l1"))
  await waitFor(() => expect(within(board).queryByText("PCO other")).not.toBeInTheDocument())
  expect(within(board).getByText("PCO keep")).toBeInTheDocument()
  // The cascade asks for the parent's exact revision, not its object head.
  expect(down).toHaveBeenCalledWith("l1", "l1-r2", null, expect.anything())
  expect(within(board).getByText("已按长期目标「LTCO l1」的精确版本筛选")).toBeInTheDocument()
})

it("a failed edge read shows an error with a retry instead of reading forever", async () => {
  lanes({
    strategy: [], ltco: [item("l1", "LTCO", { basis_revision_id: "l1-r2" })],
    pco: [item("keep", "PCO", { basis_revision_id: "keep-r1" }),
          item("other", "PCO", { basis_revision_id: "other-r1" })],
    mission: [],
  })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  const board = lane("阶段目标 PCO")
  expect(await within(board).findByText("PCO other")).toBeInTheDocument()
  down.mockRejectedValueOnce(new TypeError("network"))
  fireEvent.click(screen.getByTestId("lane-ltco-l1"))
  expect(await within(board).findByTestId("lane-pco-edge-error")).toBeInTheDocument()
  expect(within(board).queryByText("正在读取…")).not.toBeInTheDocument()
  // Without the edges the lane cannot be narrowed exactly, so it shows no cards.
  expect(within(board).queryByText("PCO other")).not.toBeInTheDocument()
  down.mockResolvedValue({ items: [edge("keep", "keep-r1", "PCO")],
                           next_cursor: null, has_more: false, limit: 25, bounded: 0 })
  fireEvent.click(within(board).getByRole("button", { name: "重试" }))
  expect(await within(board).findByText("PCO keep")).toBeInTheDocument()
  expect(within(board).queryByText("PCO other")).not.toBeInTheDocument()
  expect(within(board).queryByTestId("lane-pco-edge-error")).not.toBeInTheDocument()
  expect(onAuthLost).not.toHaveBeenCalled()
})

it("reselecting an upstream card releases its downstream filter", async () => {
  lanes({ strategy: [], ltco: [item("l1", "LTCO")],
          pco: [item("keep", "PCO", { basis_revision_id: "keep-r1" }), item("other", "PCO")], mission: [] })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  await within(lane("长期目标 LTCO")).findByText("LTCO l1")
  down.mockResolvedValue({ items: [edge("keep", "keep-r1", "PCO")],
                           next_cursor: null, has_more: false, limit: 25, bounded: 0 })
  const select = screen.getByTestId("lane-ltco-l1")
  fireEvent.click(select)
  await waitFor(() => expect(within(lane("阶段目标 PCO")).queryByText("PCO other")).not.toBeInTheDocument())
  fireEvent.click(select)
  expect(await within(lane("阶段目标 PCO")).findByText("PCO other")).toBeInTheDocument()
})

it("says the Mission lane needs a PCO instead of pretending an LTCO narrowed it", async () => {
  lanes({ strategy: [], ltco: [item("l1", "LTCO")], pco: [], mission: [item("m1", "Mission")] })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  await within(lane("长期目标 LTCO")).findByText("LTCO l1")
  fireEvent.click(screen.getByTestId("lane-ltco-l1"))
  const board = lane("任务 Mission")
  expect(await within(board).findByText("任务只记录所属阶段目标；选择一个阶段目标可继续收窄")).toBeInTheDocument()
  // The Mission stays visible: the view narrows nothing it cannot narrow exactly.
  expect(within(board).getByText("Mission m1")).toBeInTheDocument()
})

it("opens a card at the exact revision the lane showed", async () => {
  lanes({ strategy: [], ltco: [], pco: [], mission: [item("m1", "Mission", { basis_revision_id: "m1-r7" })] })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  fireEvent.click(await screen.findByTestId("lane-mission-m1"))
  expect(onOpen).toHaveBeenCalledWith("m1", "m1-r7")
})

it("a 403 on one lane is that lane's message; only a 401 ends the session", async () => {
  lanes({ strategy: [], ltco: [], pco: [item("p1", "PCO")], mission: [] })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  expect(await within(lane("阶段目标 PCO")).findByText("PCO p1")).toBeInTheDocument()
  list.mockImplementation((query) => query.group === "pco"
    ? Promise.reject(new ApiError(403, "FORBIDDEN", ""))
    : Promise.resolve(objectsPage([], { group: query.group })))
  fireEvent.click(screen.getByRole("button", { name: "刷新" }))
  expect(await within(lane("阶段目标 PCO")).findByText("当前身份无权读取该内容")).toBeInTheDocument()
  // The card this identity can no longer read is dropped, not kept as stale content.
  expect(within(lane("阶段目标 PCO")).queryByText("PCO p1")).not.toBeInTheDocument()
  expect(onAuthLost).not.toHaveBeenCalled()
  list.mockImplementation(() => Promise.reject(new ApiError(401, "UNAUTHENTICATED", "")))
  fireEvent.click(screen.getByRole("button", { name: "刷新" }))
  await waitFor(() => expect(onAuthLost).toHaveBeenCalled())
})

it("reads the historical basis from the server rather than filtering it client-side", async () => {
  lanes({ strategy: [], ltco: [item("l1", "LTCO")], pco: [], mission: [] })
  render(<BusinessMainline onOpen={onOpen} onAuthLost={onAuthLost} />)
  await within(lane("长期目标 LTCO")).findByText("LTCO l1")
  list.mockClear()
  fireEvent.click(screen.getByRole("button", { name: "历史依据" }))
  await waitFor(() => expect(list).toHaveBeenCalledWith(
    expect.objectContaining({ group: "ltco", basis: "historical" }), expect.anything()))
  // The Strategy lane is the picker for the others and always lists them all.
  expect(list).toHaveBeenCalledWith(
    expect.objectContaining({ group: "strategy", basis: "all" }), expect.anything())
})
