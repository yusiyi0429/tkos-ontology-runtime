import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { GROUP_HINTS, GROUP_LABELS } from "@/lib/labels"
import type { Overview } from "@/lib/types"

export interface SideNavProps {
  overview: Overview | null
  group: string
  basis: string
  onSelect: (group: string, basis: string) => void
}

export function SideNav({ overview, group, basis, onSelect }: SideNavProps) {
  const groups = overview?.groups ?? []
  const historical = overview?.historical_basis
  return (
    <nav className="flex h-full w-full flex-col bg-nav text-nav-foreground" data-testid="side-nav">
      <div className="px-3 pt-3 pb-1 text-[11px] tracking-wide text-nav-foreground/70">
        正式关系层级（非执行进度）
      </div>
      <ul className="flex-1 space-y-0.5 px-2 py-1">
        {groups.map((entry) => {
          const active = group === entry.group && (basis === "all" || basis === "current")
          return (
            <li key={entry.group}>
              <button type="button"
                      data-testid={`nav-${entry.group}`}
                      aria-current={active ? "page" : undefined}
                      onClick={() => onSelect(entry.group, "all")}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-[13px] transition-colors",
                        active ? "bg-white/15 text-white" : "text-nav-foreground/85 hover:bg-white/10",
                      )}>
                <span className="min-w-0">
                  <span className="block truncate">{GROUP_LABELS[entry.group] ?? entry.group}</span>
                  <span className="block truncate text-[10.5px] text-nav-foreground/60">
                    {GROUP_HINTS[entry.group] ?? ""}
                  </span>
                </span>
                {!entry.available && !entry.historical_available ? (
                  <Badge variant="outline"
                         className="shrink-0 border-white/25 bg-transparent text-[10px] text-nav-foreground/70">
                    未记录
                  </Badge>
                ) : null}
              </button>
              {entry.historical_available ? (
                <button type="button"
                        data-testid={`nav-${entry.group}-historical`}
                        onClick={() => onSelect(entry.group, "historical")}
                        className={cn(
                          "mt-0.5 ml-2 flex w-[calc(100%-0.5rem)] items-center gap-1.5 rounded-md px-2 py-1 text-left text-[11.5px]",
                          group === entry.group && basis === "historical"
                            ? "bg-white/15 text-white" : "text-nav-foreground/70 hover:bg-white/10",
                        )}>
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-300" />
                  历史依据（旧 Strategy）
                </button>
              ) : null}
              {entry.group === "operating" && entry.unattached_available ? (
                <button type="button"
                        data-testid="nav-operating-unattached"
                        onClick={() => onSelect(entry.group, "unattached")}
                        className={cn(
                          "mt-0.5 ml-2 flex w-[calc(100%-0.5rem)] items-center gap-1.5 rounded-md px-2 py-1 text-left text-[11.5px]",
                          group === entry.group && basis === "unattached"
                            ? "bg-white/15 text-white" : "text-nav-foreground/70 hover:bg-white/10",
                        )}>
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-slate-300" />
                  未关联事实（主题型）
                </button>
              ) : null}
            </li>
          )
        })}
      </ul>
      <div className="border-t border-white/10 px-3 py-2 text-[10.5px] leading-relaxed text-nav-foreground/60">
        {historical?.available
          ? `历史依据：${historical.groups.length} 组（单列，不并入当前战略）`
          : "历史依据：无记录"}
        {groups.filter((entry) => !entry.available).length
          ? ` · 未记录：${groups.filter((entry) => !entry.available)
              .map((entry) => GROUP_LABELS[entry.group] ?? entry.group).join("、")}`
          : ""}
      </div>
    </nav>
  )
}
