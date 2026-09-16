import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { relativeToNow } from "@/lib/format"
import type { Overview } from "@/lib/types"

export interface TopBarProps {
  overview: Overview | null
  strategyId: string | null
  onStrategyChange: (strategyId: string) => void
  onRefresh: () => void
  refreshing: boolean
}

export function TopBar({ overview, strategyId, onStrategyChange, onRefresh, refreshing }: TopBarProps) {
  const choices = overview?.strategy_choices ?? []
  const selected = choices.find((choice) => choice.strategy_id === strategyId) ?? choices[0] ?? null
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-card px-4 py-2.5">
      <div className="flex items-baseline gap-2">
        <h1 className="text-sm font-semibold tracking-tight">Runtime 经营看板</h1>
        <span className="text-[11px] text-muted-foreground">本机只读</span>
      </div>
      <span className="flex items-center gap-1.5">
        <Badge variant="secondary" data-testid="environment-label" className="rounded-sm">
          {overview?.environment?.label || "环境未标注"}
        </Badge>
        {overview?.environment?.synthetic ? (
          <Badge variant="outline" data-testid="synthetic-marker" className="rounded-sm text-[10.5px]">
            合成数据
          </Badge>
        ) : null}
      </span>
      <Separator orientation="vertical" className="hidden h-5 sm:block" />
      <div className="min-w-0 text-xs text-muted-foreground">
        查看身份：
        <span className="ml-1 font-medium text-foreground" data-testid="viewer-name">
          {overview?.viewer?.display_name ?? "未加载"}
        </span>
        {overview?.viewer ? (
          <span className="ml-1 text-[11px]">（{overview.viewer.principal_type === "human" ? "人类" : "Agent"}）</span>
        ) : null}
      </div>
      {choices.length > 0 ? (
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {choices.length > 1 ? "选择战略（多个独立战略域，不合并）" : "当前正式战略"}
          </span>
          <Select value={selected?.strategy_id ?? undefined} onValueChange={onStrategyChange}
                  disabled={choices.length <= 1}>
            <SelectTrigger size="sm" className="min-w-52" data-testid="strategy-select">
              <SelectValue placeholder="选择战略" />
            </SelectTrigger>
            <SelectContent>
              {choices.map((choice) => (
                <SelectItem key={choice.strategy_id} value={choice.strategy_id}>
                  {choice.title ?? "未命名战略"}{choice.domain_name ? ` · ${choice.domain_name}` : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <span className="text-xs text-amber-700" data-testid="no-strategy">当前身份没有可读的正式战略</span>
      )}
      <div className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
        <span data-testid="updated-at">更新：{relativeToNow(overview?.read_at ?? null) || "未读取"}</span>
        <Button size="xs" variant="outline" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? "刷新中…" : "立即刷新"}
        </Button>
      </div>
    </header>
  )
}
