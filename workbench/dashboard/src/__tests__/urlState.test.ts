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
    expect(serializeView({ ...DEFAULT_VIEW })).toBe("?view=map&group=strategy&basis=all&rules=0.4")
  })

  it("keeps 0.1-0.4 rule versions and surfaces unknown ones honestly", () => {
    for (const rules of ["0.1", "0.2", "0.3", "0.4"]) {
      expect(parseView(`?rules=${rules}`).rules).toBe(rules)
    }
    expect(parseView("?rules=9.9").rules).toBe("9.9")
    expect(DEFAULT_VIEW.rules).toBe("0.4")
  })

  it("drops a pinned historical revision when the object or strategy changes", () => {
    const current = { ...DEFAULT_VIEW, strategy: "s1", object: "o1", rev: "r1" }
    expect(mergeView(current, { object: "o2" }).rev).toBeNull()
    expect(mergeView(current, { object: "o2", rev: "r9" }).rev).toBe("r9")
    expect(mergeView(current, { strategy: "s2" }).rev).toBeNull()
    expect(mergeView(current, { group: "pco" }).rev).toBe("r1")
  })

  it("normalizes legacy list routes into the map or the exact object detail", () => {
    const listed = parseView("?view=list&object_type=Mission&owner=p1&domain=d1")
    expect(listed.view).toBe("map")
    expect(listed.otype).toBe("Mission")
    expect(listed.objectType).toBe("Mission")
    expect(listed.owner).toBe("p1")
    expect(listed.domain).toBe("d1")
    expect(serializeView(listed)).not.toContain("view=list")
    const exact = parseView("?view=list&object=o1&rev=r1")
    expect(exact.view).toBe("graph")
    expect(exact.object).toBe("o1")
    expect(exact.rev).toBe("r1")
    // An explicit map type is never overridden by a legacy filter reference.
    expect(parseView("?view=map&otype=PCO&object_type=Mission").otype).toBe("PCO")
  })
})
