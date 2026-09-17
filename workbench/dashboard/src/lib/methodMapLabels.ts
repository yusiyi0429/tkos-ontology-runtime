/** Shared display labels for the Method→Runtime business-definition map. */

export const CATEGORY_LABELS: Record<string, string> = {
  anchor: "Anchor",
  reference: "Reference",
  business_artifact: "Business Artifact",
  evidence_runtime_record: "Evidence / Runtime Record",
}

export const CATEGORY_ORDER = [
  "anchor", "reference", "business_artifact", "evidence_runtime_record",
] as const

export const MATURITY_LABELS: Record<string, string> = {
  defined: "Defined",
  partial: "Partial",
  to_define: "To Define",
  open_classification: "Open Classification",
  defined_for_m1a: "Defined for M1-A",
}

export const SUPPORT_ASSESSMENT_LABELS: Record<string, string> = {
  reusable_partial: "可复用（部分）",
  pending_business_close: "待业务收口",
  requires_contract_change: "需契约调整",
  not_implemented: "未覆盖",
  unknown: "未知",
}

export const COMPILED_LABELS: Record<string, string> = {
  compiled: "已编译",
  partially_compiled: "部分编译",
  not_compiled: "未编译",
  no_runtime_object_type: "无独立对象类型",
}

export const CONTRACT_STATUS_LABELS: Record<string, string> = {
  enabled_in_scope: "本 scope 已启用",
  compiled_not_enabled_in_scope: "已编译，本 scope 未启用",
  documented_not_compiled: "仅文档，未编译",
}

export const AVAILABILITY_LABELS: Record<string, string> = {
  not_queried: "未查询（不代表无数据）",
  visible: "当前身份可见至少一条",
  none_visible: "当前身份未读到",
  partially_visible: "部分对象类型可见（其余未读到）",
  partial_failed: "部分读取失败",
  not_implemented: "未编译／未启用",
  not_applicable: "不适用（无对象类型）",
  failed: "读取失败",
  unknown: "状态未知",
}

export function categoryLabel(value: string): string {
  return CATEGORY_LABELS[value] ?? value
}

export function maturityLabel(value: string): string {
  return MATURITY_LABELS[value] ?? value
}

export function supportAssessmentLabel(value: string): string {
  return SUPPORT_ASSESSMENT_LABELS[value] ?? value
}

export function compiledLabel(value: string): string {
  return COMPILED_LABELS[value] ?? value
}

export function contractStatusLabel(value: string | undefined): string {
  return CONTRACT_STATUS_LABELS[value ?? ""] ?? "未启用"
}

export function availabilityLabel(value: string | undefined): string {
  return AVAILABILITY_LABELS[value ?? ""] ?? value ?? ""
}
