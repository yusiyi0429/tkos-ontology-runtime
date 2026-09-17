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
  contractVersionOf,
  mapEdges,
  rulesOfContractVersion,
  typeInfo,
  undocumentedTypes,
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

describe("tkos.method/0.4 map reading is real and version-scoped", () => {
  const V04 = ["StrategicIssue", "StrategicAgreement", "Strategy", "StrategicArchitecture",
    "StrategyUpdateProposal", "LTCO", "PCO", "Mission", "ReviewWindow", "CandidateSet",
    "OperatingState", "OperatingProblem", "PeriodReview", "MethodRun", "EvidenceAsset"]

  it("exposes 0.4 while 0.1-0.3 keep their original readings", () => {
    expect(rulesOfContractVersion("tkos.method/0.4")).toBe("0.4")
    expect(contractVersionOf("0.4")).toBe("tkos.method/0.4")
    expect(RULES_VERSIONS[0]).toBe("0.4")
    expect(RULES_VERSIONS).toEqual(expect.arrayContaining(ALL_VERSIONS))
    for (const rules of ALL_VERSIONS) {
      expect(text(info("StrategicAgreement", rules))).not.toContain("全体当前人类")
      expect(text(info("CandidateSet", rules))).not.toContain("本人责任承诺")
      expect(text(info("Mission", rules))).not.toContain("唯一 Owner")
      expect(info("Signal", rules)).not.toBeNull()
    }
  })

  it("0.4 noPotentialIssue: direct Agent issue with no candidate-pool gate", () => {
    const body = text(info("StrategicIssue", "0.4"))
    expect(body).toContain("直接创建")
    expect(info("StrategicIssue", "0.4").relations.map((relation) => relation.target))
      .not.toContain("PotentialIssue")
    expect(mapEdges("0.4", null).some((edge) => edge.from === "PotentialIssue" || edge.to === "PotentialIssue"))
      .toBe(false)
  })

  it("0.4 all-signer Agreement: CEO mandatory, exact version, no meeting chain", () => {
    const agreement = info("StrategicAgreement", "0.4")
    expect(text(agreement)).toContain("全体")
    expect(text(agreement)).toContain("包括 CEO 本人")
    expect(agreement.relations.map((relation) => relation.target)).not.toContain("MeetingMinutes")
    expect(text(agreement)).toContain("失效")
  })

  it("0.4 scope results: Domain and Battlefield, PCO parent, Mission Owner", () => {
    expect(text(info("LTCO", "0.4"))).toContain("Battlefield")
    expect(text(info("LTCO", "0.4"))).toContain("Domain")
    expect(text(info("PCO", "0.4"))).toContain("Scope")
    expect(text(info("PCO", "0.4"))).toContain("责任人")
    expect(text(info("PCO", "0.4"))).toContain("确切父级")
    expect(text(info("Mission", "0.4"))).toContain("Owner")
    expect(info("Mission", "0.4").relations.map((relation) => relation.target))
      .toEqual(expect.arrayContaining(["PCO", "OperatingState"]))
  })

  it("0.4 commitments and whole-set CEO activation are explicit", () => {
    const candidate = text(info("CandidateSet", "0.4"))
    expect(candidate).toContain("承诺")
    expect(candidate).toContain("整组")
    expect(candidate).toContain("关键未决")
    const window = text(info("ReviewWindow", "0.4"))
    expect(window).not.toContain("截止")
    expect(window).toContain("恰好覆盖")
  })

  it("0.4 State responsibility, Unknown rule and PeriodReview basis", () => {
    const state = info("OperatingState", "0.4")
    expect(state.relations.map((relation) => relation.target))
      .toEqual(expect.arrayContaining(["LTCO", "PCO", "Mission"]))
    expect(text(state)).toContain("未知")
    expect(text(state)).toContain("当前 CEO")
    expect(text(state)).toContain("Scope 责任人")
    expect(text(state)).toContain("Owner")
    expect(text(info("PeriodReview", "0.4"))).toContain("正式经营状态")
    expect(info("PeriodReview", "0.4").relations.map((relation) => relation.target))
      .toContain("OperatingState")
    expect(mapEdges("0.4", null).some((edge) => edge.from === "PeriodReview" && edge.to === "PotentialIssue"))
      .toBe(false)
  })

  it("the catalog filters 0.4 areas and unknown old-only types stay unlabeled", () => {
    const curated = new Set(areasFor("0.4", null).flatMap((area) => area.types))
    for (const type of V04) expect(curated.has(type), type).toBe(true)
    expect(undocumentedTypes("0.4", new Set(V04))).toEqual([])
    const registered = new Set(["StrategicIssue", "Signal", "PotentialIssue", "BusinessFact"])
    const filtered = areasFor("0.4", registered).flatMap((area) => area.types)
    expect(filtered.every((type) => registered.has(type))).toBe(true)
    expect(filtered).toContain("StrategicIssue")
    expect(typeInfo("Signal", "0.4")).toBeNull()
    expect(typeInfo("PotentialIssue", "0.4")).toBeNull()
    expect(typeInfo("StrategicIssue", "0.4")).not.toBeNull()
  })

  it("0.4 graph edges connect only 0.4 types and drop removed concepts", () => {
    const curated = new Set(areasFor("0.4", null).flatMap((area) => area.types))
    const edges = mapEdges("0.4", null)
    expect(edges.some((edge) => edge.from === "StrategicIssue" && edge.to === "StrategicAgreement")).toBe(true)
    expect(edges.some((edge) => edge.from === "ReviewWindow" && edge.to === "LTCO")).toBe(true)
    expect(edges.some((edge) => edge.from === "Strategy" && edge.to === "StrategicArchitecture")).toBe(true)
    expect(edges.some((edge) => edge.from === "OperatingProblem" && edge.to === "StrategicIssue")).toBe(true)
    for (const gone of ["Signal", "PotentialIssue", "ResearchPlan", "ResearchBrief", "MeetingRound",
                        "MeetingMinutes", "BusinessFact", "StrategicJudgment", "LTCOReviewAdvice",
                        "ResearchMemo", "ResearchReport"]) {
      expect(edges.some((edge) => edge.from === gone || edge.to === gone), gone).toBe(false)
    }
    for (const edge of edges) {
      expect(curated.has(edge.from), edge.from).toBe(true)
      expect(curated.has(edge.to), edge.to).toBe(true)
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
    for (const rules of [...ALL_VERSIONS, "0.4"] as RulesVersion[]) {
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
