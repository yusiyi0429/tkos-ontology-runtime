import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"
import { MARKER_LABELS, typeInfo, type RulesVersion } from "@/lib/ontology"
import { RULES_VERSION_LABELS, TYPE_LABELS } from "@/lib/labels"
import type { MethodMap, OntologyCatalog } from "@/lib/types"
import { availabilityLabel, categoryLabel, compiledLabel, contractStatusLabel,
         maturityLabel, supportAssessmentLabel } from "@/lib/methodMapLabels"

function Section({ title, defaultOpen = true, children, testId }: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
  testId?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button type="button"
                className="flex w-full items-center justify-between rounded px-1 py-1 text-left text-[12px] font-semibold hover:bg-muted/50">
          {title}
          <span className="text-[10.5px] text-muted-foreground">{open ? "收起" : "展开"}</span>
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-1 pb-2 pt-0.5" data-testid={testId}>{children}</div>
      </CollapsibleContent>
    </Collapsible>
  )
}

export interface TypeInfoCardProps {
  type: string
  rules: RulesVersion
  catalog: OntologyCatalog | null
  methodMap?: MethodMap | null
  onSelectType: (type: string) => void
  onViewData: (type: string) => void
  onClose: () => void
}

/**
 * 类型业务说明卡：定义、主要信息、关系、生命周期、操作主体、正式效力。
 * 概念说明只读；技术字段在技术溯源页。注册但无整理说明的类型明确标注，不猜。
 */
export function TypeInfoCard({ type, rules, catalog, methodMap, onSelectType, onViewData,
                               onClose }: TypeInfoCardProps) {
  const info = typeInfo(type, rules)
  const meta = catalog?.types[type]
  const listable = meta?.listable !== false
  const mapped = (methodMap?.entries ?? []).filter(
    (entry) => (entry.runtime_object_types ?? []).includes(type))
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="type-info-card">
      <div className="border-b border-border bg-card px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="rounded-sm text-[10.5px]">对象类型</Badge>
          <span className="text-[13px] font-semibold">{TYPE_LABELS[type] ?? type}</span>
          <span className="text-[10.5px] text-muted-foreground">{type}</span>
          {info?.marker ? (
            <Badge variant="outline" className="rounded-sm text-[10px]">
              {MARKER_LABELS[info.marker]}
            </Badge>
          ) : null}
          <Button size="xs" variant="ghost" className="ml-auto" onClick={onClose}>关闭</Button>
        </div>
        <div className="mt-1 text-[11px] text-muted-foreground">
          {RULES_VERSION_LABELS[rules]} · 概念说明不是执行按钮
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-auto p-3">
        {info ? (
          <>
            <Section title="定义" testId="type-definition">
              <p className="text-[12.5px] leading-relaxed">{info.definition}</p>
              {info.embeddedNote ? (
                <p className="mt-1 rounded border border-border/60 bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground">
                  {info.embeddedNote}
                </p>
              ) : null}
            </Section>
            <Separator />
            <Section title="主要业务信息" defaultOpen={info.keyFacts.length > 0}>
              {info.keyFacts.length ? (
                <ul className="list-disc space-y-1 pl-4 text-[12px]">
                  {info.keyFacts.map((fact, index) => <li key={index}>{fact}</li>)}
                </ul>
              ) : <p className="text-[11.5px] text-muted-foreground">无额外要点。</p>}
            </Section>
            <Separator />
            <Section title="对象关系" testId="type-relations">
              {info.relations.length ? (
                <ul className="space-y-1">
                  {info.relations.map((relation) => (
                    <li key={`${relation.target}:${relation.label}`}
                        className="flex flex-wrap items-baseline gap-x-2 text-[12px]">
                      <span className="text-muted-foreground">{relation.label}</span>
                      <button type="button"
                              className="text-left font-medium text-primary underline-offset-2 hover:underline"
                              onClick={() => onSelectType(relation.target)}>
                        {TYPE_LABELS[relation.target] ?? relation.target}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : <p className="text-[11.5px] text-muted-foreground">未记录类型间关系。</p>}
            </Section>
            <Separator />
            <Section title="生命周期" defaultOpen={false}>
              <ol className="list-decimal space-y-0.5 pl-4 text-[12px]">
                {info.lifecycle.map((step, index) => <li key={index}>{step}</li>)}
              </ol>
            </Section>
            <Separator />
            <Section title="操作主体" defaultOpen={false}>
              <p className="text-[12px] leading-relaxed">{info.actors}</p>
            </Section>
            <Separator />
            <Section title="正式效力" testId="type-authority">
              <p className="text-[12px] leading-relaxed">{info.authority}</p>
            </Section>
          </>
        ) : (
          <p className="rounded border border-border/60 bg-muted/40 px-2.5 py-2 text-[12px] text-muted-foreground"
             data-testid="type-undocumented">
            该类型已在业务规则 {rules} 注册，暂无整理的业务说明；以下为真实数据入口，不以猜测文本代替。
          </p>
        )}
        <Separator />
        <Section title="方法定义对照" defaultOpen={mapped.length > 0} testId="type-method-map">
          {!methodMap ? (
            <p className="text-[11.5px] text-muted-foreground" data-testid="method-map-unavailable">
              方法定义对照当前不可用；这不改变该类型的业务规则与数据读取。
            </p>
          ) : mapped.length === 0 ? (
            <p className="text-[11.5px] text-muted-foreground" data-testid="method-map-empty">
              该类型未出现在 44 项工作文档对照清单中；空映射不是“无此概念”。
            </p>
          ) : (
            <div className="space-y-2">
              {mapped.map((entry) => {
                const documented = methodMap.method_definition_map?.documented_contracts ?? []
                const support = entry.runtime_support ?? { compiled: "unknown",
                  scope_enabled_contract_versions: [], links: [] }
                return (
                  <div key={entry.id} data-testid={`method-map-entry-${entry.id}`}
                       className="rounded border border-border/60 px-2 py-1.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[11.5px] font-medium">{entry.id} · {entry.ssot_name}</span>
                      <Badge variant="outline" className="rounded-sm text-[9.5px]">
                        {categoryLabel(entry.business_category)}
                      </Badge>
                      <Badge variant="secondary" className="rounded-sm text-[9.5px]">
                        {maturityLabel(entry.business_maturity)}
                      </Badge>
                    </div>
                    <p className="mt-1 text-[10.5px] text-muted-foreground">
                      来源 {entry.source_ref?.source_id}
                      {entry.source_ref?.table_name ? ` · ${entry.source_ref.table_name}` : ""}
                      {entry.source_ref?.record_ref ? ` · ${entry.source_ref.record_ref}` : ""}
                    </p>
                    <p className="mt-1 text-[11px]">
                      实现支持：{supportAssessmentLabel(entry.runtime_support_assessment)}
                      {" · "}{compiledLabel(support.compiled)}
                      {(support.scope_enabled_contract_versions ?? []).length
                        ? ` · 本 scope 启用：${support.scope_enabled_contract_versions.join("、")}`
                        : " · 本 scope 无已启用契约"}
                    </p>
                    {(entry.planned_contracts ?? []).length ? (
                      <p className="mt-1 text-[10.5px] text-muted-foreground">
                        涉及文档契约：{entry.planned_contracts.map((version) => {
                          const status = documented.find(
                            (item) => item.contract_version === version)?.support_status
                          return `${version}（${contractStatusLabel(status)}）`
                        }).join("、")}
                      </p>
                    ) : null}
                    {(support.links ?? []).length ? (
                      <p className="mt-1 flex flex-wrap items-center gap-1 text-[10.5px]">
                        已支持对象：
                        {support.links.map((link) => (
                          <button key={link.object_type} type="button"
                                  className="text-primary underline-offset-2 hover:underline"
                                  onClick={() => onSelectType(link.object_type)}>
                            {TYPE_LABELS[link.object_type] ?? link.object_type}
                          </button>
                        ))}
                      </p>
                    ) : null}
                    <p className="mt-1 text-[10.5px] text-muted-foreground">
                      授权数据：{availabilityLabel(entry.authorized_read_availability?.status)}
                    </p>
                    {entry.gap ? (
                      <p className="mt-1 text-[10.5px] text-muted-foreground">差异／边界：{entry.gap}</p>
                    ) : null}
                  </div>
                )
              })}
            </div>
          )}
        </Section>
        <Separator />
        <div className="px-1 pt-1">
          {listable ? (
            <Button size="sm" variant="outline" onClick={() => onViewData(type)}
                    data-testid="view-type-data">
              查看实际数据
            </Button>
          ) : (
            <p className="text-[11.5px] text-muted-foreground" data-testid="type-not-listable">
              该类型当前不支持目录列表查询。
            </p>
          )}
          <p className="mt-1 text-[10.5px] text-muted-foreground">
            实际记录按当前身份与授权读取；空结果不表示全局无数据。
          </p>
        </div>
      </div>
    </div>
  )
}
