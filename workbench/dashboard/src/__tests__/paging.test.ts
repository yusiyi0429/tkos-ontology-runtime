import { describe, expect, it, vi } from "vitest"
import { PAGE_SIZE, pageQuery, readWindow, type CursorPage } from "@/lib/paging"

/** A cursor list over `count` ids with the session readers' limit/after semantics. */
function list(count: number) {
  const all = Array.from({ length: count }, (_, index) => `id-${String(index).padStart(3, "0")}`)
  return vi.fn(async (after: string | null, limit: number): Promise<CursorPage<string>> => {
    const start = after ? all.indexOf(after) + 1 : 0
    const items = all.slice(start, start + limit)
    return { items, next_after: start + limit < all.length ? items[items.length - 1] : null }
  })
}

describe("readWindow", () => {
  it("reads one page by default and keeps the cursor for 「加载更多」", async () => {
    const read = list(60)
    const page = await readWindow(read, 0)
    expect(page.items).toHaveLength(PAGE_SIZE)
    expect(page.next_after).toBe("id-024")
    expect(read).toHaveBeenCalledTimes(1)
  })

  it("covers every loaded item on refresh, across the 100-item request cap", async () => {
    const read = list(260)
    const page = await readWindow(read, 150)
    expect(page.items).toHaveLength(150)
    expect(page.items[149]).toBe("id-149")
    expect(page.next_after).toBe("id-149")
    expect(read.mock.calls.map(([, limit]) => limit)).toEqual([100, 50])
  })

  it("stops at the end of the list", async () => {
    const page = await readWindow(list(30), 75)
    expect(page.items).toHaveLength(30)
    expect(page.next_after).toBeNull()
  })

  it("builds the session list query", () => {
    expect(pageQuery(25, null)).toBe("?limit=25")
    expect(pageQuery(50, "a b")).toBe("?limit=50&after=a%20b")
  })
})
