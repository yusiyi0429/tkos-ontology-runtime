import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { errorLabel } from "@/lib/errors"
import { TYPE_LABELS } from "@/lib/labels"
import { CATEGORY_ORDER, availabilityLabel, categoryLabel, compiledLabel,
         contractStatusLabel, maturityLabel, supportAssessmentLabel } from "@/lib/methodMapLabels"
import type { MethodMap, MethodMapEntry } from "@/lib/types"

export interface BusinessDefinitionsProps {
  methodMap: MethodMap | null
  loading: boolean
  error: unknown
  onOpenType: (type: string) => void
}

function EntryCard({ entry, methodMap, onOpenType }: {
  entry: MethodMapEntry
  methodMap: MethodMap
  onOpenType: (type: string) => void
}) {
  const support = entry.runtime_support ?? {
    compiled: "unknown", scope_enabled_contract_versions: [], links: [],
  }
  const documented = methodMap.method_definition_map?.documented_contracts ?? []
  const enabled = support.scope_enabled_contract_versions ?? []
  return (
    <article data-testid={`definition-${entry.id}`}
             className="flex flex-col gap-1.5 rounded-md border border-border bg-card px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[13px] font-semibold">
          {entry.id} · {entry.business_name || entry.ssot_name}
        </span>
        <Badge variant="secondary" className="rounded-sm text-[9.5px]">
          {maturityLabel(entry.business_maturity)}
        </Badge>
      </div>
      <div className="text-[10.5px] text-muted-foreground">
        {entry.ssot_name}
      </div>
      <p className="text-[12px] leading-relaxed">
        <span className="font-medium">目的：</span>{entry.purpose || "未记录"}
      </p>
      <p className="text-[12px] leading-relaxed text-muted-foreground">
        <span className="font-medium text-foreground">定义：</span>{entry.definition || "未记录"}
      </p>
      <div className="mt-0.5 grid gap-0.5 text-[10.5px] text-muted-foreground">
        <div data-testid={`definition-${entry.id}-support`}>
          实现支持：{supportAssessmentLabel(entry.runtime_support_assessment)}
          {" · "}{compiledLabel(support.compiled)}
          {enabled.length ? ` · 本 scope 启用：${enabled.join("、")}` : " · 本 scope 无已启用契约"}
        </div>
        <div data-testid={`definition-${entry.id}-availability`}>
          授权数据：{availabilityLabel(entry.authorized_read_availability?.status)}
        </div>
        <div>
          来源：{entry.source_ref?.source_id}
          {entry.source_ref?.table_name ? ` · ${entry.source_ref.table_name}` : ""}
          {entry.source_ref?.record_ref ? ` · ${entry.source_ref.record_ref}` : ""}
        </div>
        {(entry.planned_contracts ?? []).length ? (
          <div>
            涉及文档契约：{entry.planned_contracts.map((version) => {
              const status = documented.find(
                (item) => item.contract_version === version)?.support_status
              return `${version}（${contractStatusLabel(status)}）`
            }).join("、")}
          </div>
        ) : null}
        {entry.gap ? <div>差异／边界：{entry.gap}</div> : null}
        {entry.next_step ? <div>下一步：{entry.next_step}</div> : null}
        <div>业务实例核验：{entry.instance_verification}</div>
      </div>
      <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[10.5px]">
        <span className="text-muted-foreground">运行时对象：</span>
        {(support.links ?? []).length ? support.links.map((link) => (
          <button key={link.object_type} type="button"
                  data-testid={`definition-${entry.id}-link-${link.object_type}`}
                  className="text-primary underline-offset-2 hover:underline"
                  onClick={() => onOpenType(link.object_type)}>
            {TYPE_LABELS[link.object_type] ?? link.object_type}
          </button>
        )) : (
          <span data-testid={`definition-${entry.id}-no-runtime`}
                className="text-muted-foreground">
            未提供已支持的运行时对象类型（概念仍保留）
          </span>
        )}
      </div>
    </article>
  )
}

/**
 * 业务定义视图：44 项工作文档概念，按四类业务身份分组。它独立于实际实例与
 * 运行时注册表：未编译、未启用或无实例的概念仍然全部显示，不用空列表代替。
 * 旧的按对象类型组织的本体地图与协议视图保持独立。
 */
export function BusinessDefinitions({ methodMap, loading, error, onOpenType }: BusinessDefinitionsProps) {
  if (loading && !methodMap) {
    return <div className="space-y-2 p-4" data-testid="definitions-skeleton">
      <Skeleton className="h-8 w-1/3" /><Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" /></div>
  }
  if (error && !methodMap) {
    return <div className="p-4" data-testid="definitions-error"><Alert variant="destructive">
      <AlertTitle>{errorLabel(error)}</AlertTitle>
      <AlertDescription>
        业务定义清单当前不可用；这不是“没有业务概念”，也不影响既有协议视图。
      </AlertDescription>
    </Alert></div>
  }
  if (!methodMap) return null
  const entries = methodMap.entries ?? []
  const counts = methodMap.inventory_counts?.by_business_category ?? {}
  return (
    <div className="min-h-0 flex-1 overflow-auto p-3" data-testid="business-definitions">
      <div className="mb-3 rounded-md border border-border bg-muted/30 px-3 py-2 text-[11.5px]">
        <div className="font-medium">
          业务定义（{methodMap.source_snapshot?.entry_count ?? entries.length} 项）·
          快照 {methodMap.source_snapshot?.checked_date ?? "未标注"}
        </div>
        <div className="mt-0.5 text-muted-foreground">
          本视图按工作文档概念组织，独立于实际实例与运行时注册表；未编译、未启用或
          无实例的概念仍然显示。实现支持、本 scope 启用状态与授权数据可得性分别标注，
          不互相替代。<strong>差异／边界与下一步是 2026-09-17 工作文档快照的校准时结论（旧基线）</strong>，不是已实现的 0.4 现状；实现支持与 scope 启用状态以当前运行时注册表为准。旧的按对象类型组织的本体地图与协议视图保持独立。
        </div>
      </div>
      {CATEGORY_ORDER.map((category) => {
        const items = entries.filter((entry) => entry.business_category === category)
        if (!items.length) return null
        const declared = counts[category]
        return (
          <section key={category} className="mb-5" data-testid={`definitions-category-${category}`}>
            <header className="mb-2 flex flex-wrap items-baseline gap-2 border-b border-border pb-1">
              <h2 className="text-[13px] font-semibold">{categoryLabel(category)}</h2>
              <span className="text-[11px] text-muted-foreground">
                {items.length} 项{declared !== undefined ? `（清单标注 ${declared}）` : ""}
              </span>
            </header>
            <div className="grid gap-2 lg:grid-cols-2">
              {items.map((entry) => (
                <EntryCard key={entry.id} entry={entry} methodMap={methodMap}
                           onOpenType={onOpenType} />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
