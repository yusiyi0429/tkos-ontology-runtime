import { afterEach, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { PanZoomCanvas } from "@/components/PanZoomCanvas"
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
function box(width: number, height: number) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: width, bottom: height, width, height, toJSON: () => ({}) })
}
it("fits the viewport once, then preserves user zoom across data updates", () => {
  box(800, 600)
  const { rerender } = render(<PanZoomCanvas width={1000} height={800} edges={[]}>节点</PanZoomCanvas>)
  expect(screen.getByTestId("zoom-level")).toHaveTextContent("72%")
  fireEvent.click(screen.getByRole("button", { name: "放大" }))
  rerender(<PanZoomCanvas width={1200} height={900} edges={[]}>节点</PanZoomCanvas>)
  expect(screen.getByTestId("zoom-level")).toHaveTextContent("92%")
  fireEvent.click(screen.getByTestId("fit-view"))
  expect(screen.getByTestId("zoom-level")).toHaveTextContent("64%")
})
it("clamped fit centers with the actual drawn scale", () => {
  box(100, 100)
  const { container } = render(<PanZoomCanvas width={1000} height={1000} edges={[]}>节点</PanZoomCanvas>)
  expect(screen.getByTestId("zoom-level")).toHaveTextContent("30%")
  expect(container.querySelector('[style*="transform:"]')).toHaveStyle({ transform: "translate(-100px, -100px) scale(0.3)" })
})
it("a node pointer is not captured for panning", () => {
  vi.stubGlobal("PointerEvent", MouseEvent)
  const capture = vi.fn(), click = vi.fn()
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", { value: capture, configurable: true })
  render(<PanZoomCanvas width={300} height={200} edges={[]}><button onClick={click}>节点</button></PanZoomCanvas>)
  fireEvent.pointerDown(screen.getByRole("button", { name: "节点" }), { button: 0 })
  fireEvent.click(screen.getByRole("button", { name: "节点" }))
  expect(capture).not.toHaveBeenCalled()
  expect(click).toHaveBeenCalledOnce()
  delete (HTMLElement.prototype as Partial<HTMLElement>).setPointerCapture
})
it("fits newly arriving graph nodes before the reader chooses a position", () => {
  box(800, 600)
  const { rerender } = render(<PanZoomCanvas width={300} height={200} edges={[]}>根节点</PanZoomCanvas>)
  expect(screen.getByTestId("zoom-level")).toHaveTextContent("100%")
  rerender(<PanZoomCanvas width={1000} height={800} edges={[]}>完整目标主干</PanZoomCanvas>)
  expect(screen.getByTestId("zoom-level")).toHaveTextContent("72%")
})
