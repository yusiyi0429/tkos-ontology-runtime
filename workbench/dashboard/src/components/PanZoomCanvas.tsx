import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"

/** One labeled connector between two node anchor points (virtual coordinates). */
export interface CanvasEdge {
  id: string
  x1: number
  y1: number
  x2: number
  y2: number
  label: string
  dashed?: boolean
  side?: "left" | "right"
  labelX?: number
  labelY?: number
}

export interface CanvasFocusPoint {
  x: number
  y: number
  /** Bumping the sequence re-centers the same point. */
  seq: number
}

export interface PanZoomCanvasProps {
  /** Virtual (unzoomed) drawing size. */
  width: number
  height: number
  edges: CanvasEdge[]
  /** Absolutely positioned node elements in virtual coordinates. */
  children: React.ReactNode
  focusPoint?: CanvasFocusPoint | null
  emptyHint?: string
  testId?: string
}

const MIN_ZOOM = 0.3
const MAX_ZOOM = 2

function edgePath(edge: CanvasEdge): string {
  if (edge.side) {
    const bend = edge.side === "left" ? -70 : 70
    return `M ${edge.x1} ${edge.y1} C ${edge.x1 + bend} ${edge.y1}, ${edge.x2 + bend} ${edge.y2}, ${edge.x2} ${edge.y2}`
  }
  const direction = edge.x2 >= edge.x1 ? 1 : -1
  const bend = Math.max(40, Math.abs(edge.x2 - edge.x1) / 2) * direction
  return `M ${edge.x1} ${edge.y1} C ${edge.x1 + bend} ${edge.y1}, ${edge.x2 - bend} ${edge.y2}, ${edge.x2} ${edge.y2}`
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  return target instanceof Element
    && Boolean(target.closest("button, a, input, label, select, textarea, [data-no-pan]"))
}

/**
 * Pan/zoom surface for the ontology map and the business relation graph.
 * Nodes stay plain HTML buttons (keyboard focusable); edges are an SVG layer
 * underneath with direction arrows.  Panning starts only on the bare canvas
 * background, and a real drag suppresses the trailing click so node clicks are
 * never hijacked.  Zoom/fit/pan are view-only — no business state changes.
 */
export function PanZoomCanvas({ width, height, edges, children, focusPoint,
                                emptyHint, testId = "pan-zoom-canvas" }: PanZoomCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 24, y: 24 })
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number;
                          panX: number; panY: number; moved: boolean } | null>(null)
  const suppressClickRef = useRef(false)

  const fit = useCallback(() => {
    const box = containerRef.current?.getBoundingClientRect()
    if (!box || box.width === 0 || box.height === 0 || width === 0 || height === 0) {
      setZoom(1)
      setPan({ x: 24, y: 24 })
      return
    }
    const next = Math.max(MIN_ZOOM, Math.min((box.width - 24) / width, (box.height - 24) / height, 1))
    setZoom(next)
    setPan({ x: (box.width - width * next) / 2, y: (box.height - height * next) / 2 })
  }, [width, height])

  const positioned = useRef(false)
  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const fitFirstVisible = () => {
      const box = element.getBoundingClientRect()
      if (positioned.current || box.width === 0 || box.height === 0) return
      fit()
    }
    fitFirstVisible()
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fitFirstVisible)
    observer?.observe(element)
    return () => observer?.disconnect()
  }, [fit])

  const zoomBy = useCallback((delta: number) => {
    positioned.current = true
    setZoom((current) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM,
      Math.round((current + delta) * 100) / 100)))
  }, [])

  useEffect(() => {
    if (!focusPoint) return
    positioned.current = true
    const box = containerRef.current?.getBoundingClientRect()
    if (!box || box.width === 0 || box.height === 0) return
    setPan({ x: box.width / 2 - focusPoint.x * zoom, y: box.height / 2 - focusPoint.y * zoom })
    // Only the focus request sequence drives re-centering.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusPoint?.seq])

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    // Node buttons/links stay fully interactive; panning belongs to the bare
    // background so a click on a node is never captured by the pan layer.
    if (isInteractiveTarget(event.target)) return
    dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
                        panX: pan.x, panY: pan.y, moved: false }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const dx = event.clientX - drag.startX
    const dy = event.clientY - drag.startY
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; positioned.current = true }
    setPan({ x: drag.panX + dx, y: drag.panY + dy })
  }
  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    dragRef.current = null
    if (drag.moved) suppressClickRef.current = true
  }
  const onClickCapture = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!suppressClickRef.current) return
    suppressClickRef.current = false
    event.preventDefault()
    event.stopPropagation()
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-muted/20" data-testid={testId}>
      {emptyHint ? <p className="shrink-0 border-b border-border px-3 py-2 text-[11px] text-muted-foreground">{emptyHint}</p> : null}
      <div ref={containerRef} className="relative min-h-0 flex-1 overflow-hidden" data-testid={`${testId}-viewport`}>
      <div className="absolute inset-0 cursor-grab touch-none active:cursor-grabbing"
           onPointerDown={onPointerDown} onPointerMove={onPointerMove}
           onPointerUp={endDrag} onPointerCancel={endDrag} onClickCapture={onClickCapture}>
        <div style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                      transformOrigin: "0 0", width, height, position: "relative" }}>
          <svg width={width} height={height} className="absolute inset-0" aria-hidden="true">
            <defs>
              <marker id="canvas-arrow" viewBox="0 0 8 8" refX="7" refY="4"
                      markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0.5 L 7.5 4 L 0 7.5 z" fill="#94a3b8" />
              </marker>
              <marker id="canvas-arrow-dashed" viewBox="0 0 8 8" refX="7" refY="4"
                      markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0.5 L 7.5 4 L 0 7.5 z" fill="#b45309" />
              </marker>
            </defs>
            {edges.map((edge) => (
              <g key={edge.id}>
                <path d={edgePath(edge)} fill="none"
                      stroke={edge.dashed ? "#b45309" : "#94a3b8"}
                      strokeWidth={edge.dashed ? 1.2 : 1.5}
                      strokeDasharray={edge.dashed ? "5 4" : undefined}
                      markerEnd={edge.dashed ? "url(#canvas-arrow-dashed)" : "url(#canvas-arrow)"} />
                <text x={edge.labelX ?? (edge.x1 + edge.x2) / 2} y={edge.labelY ?? (edge.y1 + edge.y2) / 2 - 5}
                      textAnchor="middle" fontSize={11} fill="#475569"
                      style={{ paintOrder: "stroke", stroke: "#ffffff", strokeWidth: 3 }}>
                  {edge.label}
                </text>
              </g>
            ))}
          </svg>
          {children}
        </div>
      </div>
      <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded border border-border bg-card p-1 shadow-sm" data-no-pan>
        <Button size="xs" variant="outline" onClick={() => zoomBy(-0.2)} disabled={zoom <= MIN_ZOOM}
                aria-label="缩小">−</Button>
        <span className="w-11 text-center text-[11px] text-muted-foreground" data-testid="zoom-level">
          {Math.round(zoom * 100)}%
        </span>
        <Button size="xs" variant="outline" onClick={() => zoomBy(0.2)} disabled={zoom >= MAX_ZOOM}
                aria-label="放大">＋</Button>
        <Button size="xs" variant="outline" onClick={() => { positioned.current = true; fit() }} data-testid="fit-view">适应视图</Button>
      </div>
      </div>
    </div>
  )
}
