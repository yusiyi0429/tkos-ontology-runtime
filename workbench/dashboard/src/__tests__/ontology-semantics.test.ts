/** Semantic regression assertions for the curated ontology text.
 *
 * These assertions encode the confirmed business rules (R4) independently of
 * how the module organizes its constants: they check the merged per-version
 * reading that the UI actually shows, not internal tables.
 */
import { describe, expect, it } from "vitest"

import {
  RULES_VERSIONS,
  areasFor,
  mapEdges,
  typeInfo,
  type RulesVersion,
  type TypeInfo,
} from "../lib/ontology"

const ALL_VERSIONS: RulesVersion[] = ["0.1", "0.2", "0.3"]

function text(info: TypeInfo): string {
  return [
    info.definition,
    info.actors,
    info.authority,
    info.embeddedNote ?? "",
    ...info.keyFacts,
    ...info.lifecycle,
    ...info.relations.map((relation) => `${relation.target}:${relation.label}`),
  ].join("\n")
}

function info(type: string, rules: RulesVersion): TypeInfo {
  const found = typeInfo(type, rules)
  if (!found) throw new Error(`missing curated info for ${type} @ ${rules}`)
  return found
}

describe("StrategicJudgment is a product of the confirmed update, not its initiator", () => {
  it("every version: proposal confirmation produces the judgment, never the reverse", () => {
    for (const rules of ALL_VERSIONS) {
      const edges = mapEdges(rules, null)
      expect(
        edges.some((edge) => edge.from === "StrategyUpdateProposal" && edge.to === "StrategicJudgment"),
        `${rules}: proposal → judgment edge`,
      ).toBe(true)
      expect(
        edges.some((edge) => edge.from === "StrategicJudgment" && edge.to === "StrategyUpdateProposal"),
        `${rules}: judgment must not initiate the proposal`,
      ).toBe(false)
      const body = text(info("StrategicJudgment", rules))
      expect(body).toContain("提案")
      expect(body).toContain("同一事务")
      expect(body).not.toContain("CEO 对议题研究结论是否调整战略的判断记录")
    }
  })
})

describe("Signal rules match each version's real actions", () => {
  it("0.1 has no archive/activate disposition and no conversion wording", () => {
    const body = text(info("Signal", "0.1"))
    expect(body).not.toContain("归档")
    expect(body).not.toContain("激活")
    expect(body).not.toContain("转化")
    expect(body).not.toContain("处置动作，不立题")
  })

  it("0.2 conversion derives from the CEO's own confirmation", () => {
    const body = text(info("Signal", "0.2"))
    expect(body).toContain("转化")
    expect(body).toContain("CEO 本人确认")
  })

  it("0.3 names the CEO Agent as the initiation actor, not the CEO person", () => {
    const body = text(info("Signal", "0.3"))
    expect(body).toContain("CEO Agent")
    expect(body).not.toContain("只能来自 CEO 确认")
    expect(body).not.toContain("CEO 本人确认的真实议题")
  })
})

describe("0.1 text never borrows 0.2+ fields or actions", () => {
  it("0.1 StrategicIssue has no business-scope/urgency classification and no direct create", () => {
    const body = text(info("StrategicIssue", "0.1"))
    expect(body).not.toContain("业务范围")
    expect(body).not.toContain("紧急度")
    expect(body).not.toContain("直接创建")
    expect(body).toContain("CEO 本人确认")
  })

  it("0.2 StrategicIssue keeps the classification and the direct-create reason rule", () => {
    const body = text(info("StrategicIssue", "0.2"))
    expect(body).toContain("业务范围")
    expect(body).toContain("紧急度")
    expect(body).toContain("直接创建")
  })

  it("0.1 PotentialIssue only records title/summary and source signals", () => {
    const body = text(info("PotentialIssue", "0.1"))
    expect(body).not.toContain("来源类别")
    expect(body).not.toContain("紧急度")
    expect(body).not.toContain("核心问题")
    expect(body).toContain("来源信号")
  })

  it("0.2 PotentialIssue records the source category", () => {
    expect(text(info("PotentialIssue", "0.2"))).toContain("来源类别")
  })

  it("0.1 ReviewWindow has no feedback deadline wording", () => {
    const body = text(info("ReviewWindow", "0.1"))
    expect(body).not.toContain("截止")
    const later = text(info("ReviewWindow", "0.3"))
    expect(later).toContain("截止")
  })
})

describe("actor and efficacy wording follows the real actions", () => {
  it("0.3 issue initiation is the CEO Agent; 0.1/0.2 is the CEO person", () => {
    expect(info("StrategicIssue", "0.3").actors).toContain("CEO Agent")
    expect(info("StrategicIssue", "0.1").actors).toContain("CEO 本人")
    expect(info("StrategicIssue", "0.2").actors).toContain("CEO 本人")
    expect(info("PotentialIssue", "0.3").actors).toContain("CEO Agent")
  })

  it("MethodRun autonomous intake is a 0.3 capability only", () => {
    expect(text(info("MethodRun", "0.3"))).toContain("CEO Agent")
    for (const rules of ["0.1", "0.2"] as RulesVersion[]) {
      expect(text(info("MethodRun", rules))).not.toContain("自主")
      expect(info("MethodRun", rules).actors).toContain("CEO 本人创建")
      expect(info("MethodRun", rules).actors).not.toContain("CEO Agent 创建")
    }
  })

  it("LTCOReviewAdvice is produced by the Co-agent", () => {
    for (const rules of ALL_VERSIONS) {
      expect(info("LTCOReviewAdvice", rules).actors).toContain("Co-agent")
      expect(info("LTCOReviewAdvice", rules).actors).not.toContain("CEO Agent")
    }
  })

  it("BusinessFact never claims a record proves truth or Outcome attainment", () => {
    for (const rules of ALL_VERSIONS) {
      const authority = info("BusinessFact", rules).authority
      expect(authority).not.toContain("已记录即成立")
      expect(authority).toContain("不证明事实为真")
      expect(authority).toContain("不代表任何 Outcome 达成")
    }
  })

  it("OperatingState keeps the old formal state effective while a new recommendation is unconfirmed", () => {
    expect(info("OperatingState", "0.3").lifecycle.join("\n")).toContain("旧正式状态继续有效")
  })
})

describe("confirmed core relations are present per version", () => {
  it("PeriodReview → PotentialIssue exists only where the candidate pool accepts review findings", () => {
    const has = (rules: RulesVersion) =>
      mapEdges(rules, null).some((edge) => edge.from === "PeriodReview" && edge.to === "PotentialIssue")
    expect(has("0.1")).toBe(false)
    expect(has("0.2")).toBe(true)
    expect(has("0.3")).toBe(true)
  })

  it("0.3 adds OperatingState → LTCO and StrategicArchitecture → Mission", () => {
    const edges = mapEdges("0.3", null)
    expect(edges.some((edge) => edge.from === "OperatingState" && edge.to === "LTCO")).toBe(true)
    expect(edges.some((edge) => edge.from === "StrategicArchitecture" && edge.to === "Mission")).toBe(true)
  })

  it("edges always connect types curated for that version", () => {
    for (const rules of ALL_VERSIONS) {
      const curated = new Set(areasFor(rules, null).flatMap((area) => area.types))
      for (const edge of mapEdges(rules, null)) {
        expect(curated.has(edge.from), `${rules}: ${edge.from}`).toBe(true)
        expect(curated.has(edge.to), `${rules}: ${edge.to}`).toBe(true)
      }
    }
  })
})

describe("business-language boundary (R4)", () => {
  const FORBIDDEN = [
    "effective", "canonical", "pco_ref", "hard_deadline", "previous_state_ref",
    "formal_effect", "origin=", "business_scope", "urgency=", "architecture_ref",
    "converted", "unit_outcomes", "signal_refs", "source_refs", "state_refs",
    "primary_scope_id",
  ]

  it("curated text never requires reading API field names", () => {
    expect(RULES_VERSIONS).toEqual(expect.arrayContaining(ALL_VERSIONS))
    for (const rules of ALL_VERSIONS) {
      for (const area of areasFor(rules, null)) {
        for (const type of area.types) {
          const body = text(info(type, rules))
          for (const token of FORBIDDEN) {
            expect(body.includes(token), `${rules} ${type} contains "${token}"`).toBe(false)
          }
        }
      }
    }
  })
})
