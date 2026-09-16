import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { isMissing } from "@/lib/format"
import type { ObjectListItem } from "@/lib/types"

export interface FilterBarProps {
  items: ObjectListItem[]
  domain: string | null
  periodFrom: string | null
  periodTo: string | null
  owner: string | null
  onChange: (patch: { domain?: string | null; periodFrom?: string | null; periodTo?: string | null;
                      owner?: string | null }) => void
}

interface Option { value: string; label: string }

function domainOptions(items: ObjectListItem[]): Option[] {
  const seen = new Map<string, string>()
  for (const item of items) {
    if (!seen.has(item.domain_id)) seen.set(item.domain_id, item.domain_name ?? item.domain_id.slice(0, 8))
  }
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
}

export function ownerOptions(items: ObjectListItem[]): Option[] {
  const seen = new Map<string, string>()
  for (const item of items) {
    // The filter matches the same recorded responsibility relations the list
    // shows: Owner/Mission Owner/Outcome DRI/Outcome Owner/problem responsible.
    // A participant is not a responsibility relation.
    const entries = item.responsibility
      ?? [item.owner, ...item.dri].filter(
        (entry) => entry && !isMissing(entry) && entry.relation !== "participant")
    for (const entry of entries) {
      if (!entry || isMissing(entry) || !("principal" in entry) || !entry.principal) continue
      if (entry.relation === "participant") continue
      seen.set(entry.principal.principal_id, entry.principal.display_name)
    }
  }
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
}

const ALL = "__all__"

export function FilterBar({ items, domain, periodFrom, periodTo, owner, onChange }: FilterBarProps) {
  const domains = domainOptions(items)
  const owners = ownerOptions(items)
  const active = Boolean(domain || periodFrom || periodTo || owner)
  return (
    <div className="flex flex-wrap items-end gap-3 border-b border-border bg-card/70 px-4 py-2"
         data-testid="filter-bar">
      <div className="space-y-1">
        <Label className="text-[11px] text-muted-foreground">业务域</Label>
        <Select value={domain ?? ALL} onValueChange={(value) => onChange({ domain: value === ALL ? null : value })}>
          <SelectTrigger size="sm" className="w-40" data-testid="filter-domain"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>全部业务域</SelectItem>
            {domains.map((option) => (
              <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-[11px] text-muted-foreground" htmlFor="period-from">周期起（含）</Label>
        <Input id="period-from" type="date" className="h-7 w-36" value={periodFrom ?? ""}
               onChange={(event) => onChange({ periodFrom: event.target.value || null })} />
      </div>
      <div className="space-y-1">
        <Label className="text-[11px] text-muted-foreground" htmlFor="period-to">周期止（含）</Label>
        <Input id="period-to" type="date" className="h-7 w-36" value={periodTo ?? ""}
               onChange={(event) => onChange({ periodTo: event.target.value || null })} />
      </div>
      <div className="space-y-1">
        <Label className="text-[11px] text-muted-foreground">负责人 / DRI</Label>
        <Select value={owner ?? ALL} onValueChange={(value) => onChange({ owner: value === ALL ? null : value })}>
          <SelectTrigger size="sm" className="w-44" data-testid="filter-owner"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>全部责任人</SelectItem>
            {owners.map((option) => (
              <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {active ? (
        <Button size="xs" variant="ghost"
                onClick={() => onChange({ domain: null, periodFrom: null, periodTo: null, owner: null })}>
          清除筛选
        </Button>
      ) : null}
      <p className="w-full text-[10.5px] text-muted-foreground">
        周期按记录的正式周期过滤；Mission 使用其引用的 PCO 版本周期。
      </p>
    </div>
  )
}
