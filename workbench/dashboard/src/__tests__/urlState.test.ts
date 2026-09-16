import { describe, expect, it } from "vitest"
import { DEFAULT_VIEW, mergeView, parseView, serializeView } from "@/lib/urlState"

describe("URL view state", () => {
  it("round-trips strategy/object/revision/filters", () => {
    const view = parseView("?strategy=s1&object=o1&rev=r1&group=mission&basis=historical"
      + "&object_type=Mission&domain=d1&period_from=2026-09-01&period_to=2026-09-30&owner=p1")
    expect(view).toMatchObject({ strategy: "s1", object: "o1", rev: "r1", group: "mission",
                                 basis: "historical", objectType: "Mission", domain: "d1",
                                 periodFrom: "2026-09-01", periodTo: "2026-09-30", owner: "p1" })
    expect(serializeView(view)).toContain("rev=r1")
    expect(serializeView({ ...DEFAULT_VIEW })).toBe("?group=strategy&basis=all")
  })

  it("drops a pinned historical revision when the object or strategy changes", () => {
    const current = { ...DEFAULT_VIEW, strategy: "s1", object: "o1", rev: "r1" }
    expect(mergeView(current, { object: "o2" }).rev).toBeNull()
    expect(mergeView(current, { object: "o2", rev: "r9" }).rev).toBe("r9")
    expect(mergeView(current, { strategy: "s2" }).rev).toBeNull()
    expect(mergeView(current, { group: "pco" }).rev).toBe("r1")
  })
})
