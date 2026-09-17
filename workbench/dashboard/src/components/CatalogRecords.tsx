import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchCatalogObjects } from "@/lib/api"
import { errorLabel, isAbort, isAccessDenial } from "@/lib/errors"
import { useLiveResource } from "@/lib/live"
import { FORMAL_LABELS, TYPE_LABELS, formalBusinessText } from "@/lib/labels"
import { rulesOfContractVersion } from "@/lib/ontology"
import { formatTime } from "@/lib/format"
import type { CatalogObjectItem, CatalogObjectsPage } from "@/lib/types"

const ALL = "__all__"

export interface CatalogRecordsProps {
  objectType: string
  onOpenRecord: (item: CatalogObjectItem) => void
  onAccessDenied: () => void
  /** A legacy ?view=list owner filter kept in the URL: applied to the loaded
   *  rows when its principal appears there; otherwise it is surfaced as an
   *  unmatched legacy filter instead of pretending it was applied. */
  legacyOwner?: string | null
}

function pageIdentity(page: CatalogObjectsPage): string {
  // The authorization projection (title/summary/formal state/responsibility)
  // belongs to the page identity: the same revision can surface different
  // authorized Owner names, and the UI must offer that as an update instead of
  // keeping stale rows and stale owner options.
  const responsibility = (item: CatalogObjectItem) => (item.responsibility ?? []).map(
    (entry) => `${entry.relation}:${entry.outcome_id ?? ""}`
      + `:${entry.principal?.principal_id ?? ""}:${entry.principal?.display_name ?? ""}`
      + `:${entry.appointment?.status ?? ""}`).join(",")
  return page.items.map((item) =>
    `${item.object_id}:${item.basis_revision_id}:${item.formal_state.status}`
    + `:${item.formal_state.formal}:${item.title ?? ""}:${item.summary ?? ""}`
    + `:${responsibility(item)}`).join("|")
}

interface OwnerOption { value: string; label: string }

/**
 * Owner options come only from the authorized responsibility projection of the
 * loaded rows: the same recorded relations the group list filters by, with
 * participants excluded.  A missing name is never replaced by a guess.
 */
export function catalogOwnerOptions(items: CatalogObjectItem[]): OwnerOption[] {
  const seen = new Map<string, string>()
  for (const item of items) {
    for (const entry of item.responsibility ?? []) {
      const principal = entry.principal
      if (!principal || entry.relation === "participant") continue
      if (!principal.principal_id || !principal.display_name) continue
      if (!seen.has(principal.principal_id)) {
        seen.set(principal.principal_id, principal.display_name)
      }
    }
  }
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
}

/**
 * 某一注册类型的真实记录分页列表（catalog/objects，授权目录）。
 * 空结果只说明当前身份与筛选下未读到记录；请求失败与接口不可用单独表达，
 * 不冒充空结果。服务端更新以提示确认方式出现，网络失败保留旧内容并标注过期。
 *
 * 搜索/责任人/状态/版本筛选只作用于已加载的授权分页（服务端目录接口本就没有
 * 这些参数，不伪造）；责任人选项来自已加载记录的授权责任投影，未知责任人不补造。
 */
export function CatalogRecords({ objectType, onOpenRecord, onAccessDenied,
                               legacyOwner = null }: CatalogRecordsProps) {
  const [moreItems, setMoreItems] = useState<CatalogObjectItem[]>([])
  const [moreCursor, setMoreCursor] = useState<string | null | undefined>(undefined)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<unknown>(null)
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState(ALL)
  const [version, setVersion] = useState(ALL)
  const [owner, setOwner] = useState<string>(legacyOwner ?? ALL)
  const epochRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const currentType = useRef(objectType)

  const page = useLiveResource({
    key: `catalog-objects#${objectType}`,
    fetcher: (_key, signal) => fetchCatalogObjects(objectType, null, signal),
    identity: pageIdentity,
    onAccessDenied,
  })

  useEffect(() => {
    if (currentType.current === objectType) return
    currentType.current = objectType
    epochRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setMoreItems([])
    setMoreCursor(undefined)
    setLoadMoreError(null)
    setLoadingMore(false)
    // Filters describe the previously selected type; never carry them over.
    setSearch("")
    setStatus(ALL)
    setVersion(ALL)
    setOwner(ALL)
  }, [objectType])

  useEffect(() => () => {
    epochRef.current += 1
    abortRef.current?.abort()
  }, [])

  const loadMore = useCallback(async () => {
    const cursor = moreCursor === undefined ? page.data?.next_cursor : moreCursor
    if (!cursor || loadingMore) return
    const epoch = ++epochRef.current
    setLoadingMore(true)
    setLoadMoreError(null)
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const next = await fetchCatalogObjects(objectType, cursor, controller.signal)
      if (epoch !== epochRef.current) return
      setMoreItems((current) => {
        const seen = new Set(current.map((item) => item.object_id))
        return [...current, ...next.items.filter((item) => !seen.has(item.object_id))]
      })
      setMoreCursor(next.next_cursor) // null is a real exhausted state
    } catch (error) {
      if (epoch !== epochRef.current || isAbort(error)) return
      if (isAccessDenial(error)) {
        onAccessDenied()
        return
      }
      setLoadMoreError(error)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      if (epoch === epochRef.current) setLoadingMore(false)
    }
  }, [moreCursor, page.data?.next_cursor, loadingMore, objectType, onAccessDenied])

  const items = useMemo(() => {
    const first = page.data?.items ?? []
    const seen = new Set(first.map((item) => item.object_id))
    return [...first, ...moreItems.filter((item) => !seen.has(item.object_id))]
  }, [page.data, moreItems])

  const hasMore = moreCursor === null ? false : Boolean(moreCursor ?? page.data?.next_cursor)

  const needle = search.trim().toLowerCase()
  // Client-side filters only see what this identity has already been authorized
  // to page; the notes below say so instead of implying a server-wide match.
  const filtered = useMemo(() => items.filter((item) => {
    if (status !== ALL && item.formal_state.status !== status) return false
    if (version !== ALL && (item.contract_version ?? "") !== version) return false
    if (owner !== ALL && !(item.responsibility ?? []).some((entry) =>
        entry.relation !== "participant" && entry.principal?.principal_id === owner)) return false
    if (needle && !`${item.title ?? ""} ${item.summary ?? ""} ${item.object_id}`
        .toLowerCase().includes(needle)) return false
    return true
  }), [items, status, version, owner, needle])
  const ownerOptions = useMemo(() => catalogOwnerOptions(items), [items])
  const ownerKnown = owner === ALL || ownerOptions.some((option) => option.value === owner)
  const statusOptions = useMemo(() => {
    const seen = new Map<string, string>()
    for (const item of items) {
      if (!seen.has(item.formal_state.status)) {
        seen.set(item.formal_state.status,
                 FORMAL_LABELS[item.formal_state.status] ?? item.formal_state.status)
      }
    }
    return [...seen.entries()].map(([value, label]) => ({ value, label }))
  }, [items])
  const versionOptions = useMemo(() => {
    const seen = new Map<string, string>()
    for (const item of items) {
      if (!item.contract_version) continue
      const rules = rulesOfContractVersion(item.contract_version)
      seen.set(item.contract_version, rules ? `业务规则 ${rules}` : item.contract_version)
    }
    return [...seen.entries()].map(([value, label]) => ({ value, label }))
  }, [items])
  const filtering = Boolean(needle) || status !== ALL || version !== ALL || owner !== ALL

  if ((page.loading || (!page.data && !page.error)) && items.length === 0) {
    return <div className="space-y-2 p-3" data-testid="catalog-skeleton">
      {[0, 1, 2].map((key) => <Skeleton key={key} className="h-14 w-full" />)}</div>
  }
  if (page.error && !page.data) {
    return (
      <div className="space-y-2 p-3" data-testid="catalog-error">
        <p className="text-[12px] text-destructive">{errorLabel(page.error)}</p>
        <p className="text-[11px] text-muted-foreground">读取失败不等于没有记录；请重试。</p>
        <Button size="xs" variant="outline" onClick={() => page.refresh()}>重试</Button>
      </div>
    )
  }
  return (
    <div data-testid="catalog-records">
      {page.pending ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-sky-300 bg-sky-50 px-3 py-1.5 text-[11px] text-sky-900"
             data-testid="catalog-pending">
          <span>服务端记录已更新；当前仍显示你正在阅读的列表。</span>
          <Button size="xs" variant="outline" onClick={() => {
            epochRef.current += 1
            abortRef.current?.abort()
            setMoreItems([])
            setMoreCursor(undefined)
            setLoadingMore(false)
            setLoadMoreError(null)
            page.acceptPending()
          }}>查看最新</Button>
          <Button size="xs" variant="ghost" onClick={() => page.dismissPending()}>保持当前</Button>
        </div>
      ) : null}
      {page.stale ? (
        <div className="border-b border-amber-300 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-900"
             data-testid="catalog-stale">
          {errorLabel(page.error)}；当前显示的是上次成功读取的内容，不代表已刷新。
        </div>
      ) : null}
      <div className="border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        {filtering
          ? `筛选后 ${filtered.length} 条（已加载 ${items.length} 条${hasMore ? "，分页未读完" : ""}）`
          : `已加载 ${items.length} 条${hasMore ? "（分页未读完，不表示只有这些）" : ""}`}
      </div>
      <div className="border-b border-border bg-card/60 px-3 py-2" data-testid="catalog-filters">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1 space-y-0.5">
            <Label htmlFor="catalog-search" className="text-[10.5px] text-muted-foreground">搜索</Label>
            <Input id="catalog-search" type="search" className="h-7"
                   aria-label="搜索已加载记录" placeholder="标题、摘要或对象编号"
                   value={search} onChange={(event) => setSearch(event.target.value)}
                   data-testid="catalog-filter-search" />
          </div>
          <div className="space-y-0.5">
            <Label className="text-[10.5px] text-muted-foreground">责任人</Label>
            <Select value={owner} onValueChange={setOwner}>
              <SelectTrigger size="sm" className="w-36" data-testid="catalog-filter-owner">
                <SelectValue placeholder={ownerKnown ? "全部责任人" : "旧筛选责任人"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>全部责任人</SelectItem>
                {!ownerKnown && owner !== ALL ? (
                  <SelectItem value={owner}>旧列表责任人未在已加载记录</SelectItem>
                ) : null}
                {ownerOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-0.5">
            <Label className="text-[10.5px] text-muted-foreground">状态</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger size="sm" className="w-32" data-testid="catalog-filter-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>全部状态</SelectItem>
                {statusOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-0.5">
            <Label className="text-[10.5px] text-muted-foreground">业务规则版本</Label>
            <Select value={version} onValueChange={setVersion}>
              <SelectTrigger size="sm" className="w-40" data-testid="catalog-filter-version">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>全部版本</SelectItem>
                {versionOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {filtering ? (
            <Button size="xs" variant="ghost"
                    onClick={() => { setSearch(""); setStatus(ALL); setVersion(ALL); setOwner(ALL) }}>
              清除筛选
            </Button>
          ) : null}
        </div>
        <p className="mt-1 text-[10.5px] text-muted-foreground" data-testid="catalog-filter-note">
          责任人选项只来自已授权的已加载记录，未知责任人不补造。
          {filtering ? ` 搜索/责任人/状态/版本仅作用于已加载的 ${items.length} 条${
            hasMore ? "；仍有未加载记录，继续加载后同样适用。" : "（已到末页，不表示全局总数）。"}` : ""}
        </p>
        {legacyOwner && owner === legacyOwner && !ownerKnown ? (
          <p className="mt-1 text-[10.5px] text-amber-800" data-testid="catalog-owner-unmatched">
            旧列表链接指定的责任人不在已加载记录中；这不代表服务端没有匹配记录。
          </p>
        ) : null}
      </div>
      {items.length === 0 ? <p className="p-4 text-[12px] text-muted-foreground" data-testid="catalog-empty">
        当前身份与筛选下未读到「{TYPE_LABELS[objectType] ?? objectType}」的记录；
        这不表示全局没有数据，也不改变该类型在地图中的展示。
      </p> : null}
      {items.length > 0 && filtered.length === 0 ? (
        <p className="p-4 text-[12px] text-muted-foreground" data-testid="catalog-filter-empty">
          当前筛选在已加载记录中没有匹配项{hasMore ? "；仍有未加载记录，继续加载后再筛选" : "（当前授权读取已到末页）"}。
        </p>
      ) : null}
      <ul className="divide-y divide-border">
        {filtered.map((item) => {
          const rules = rulesOfContractVersion(item.contract_version)
          return (
            <li key={`${item.object_id}:${item.basis_revision_id}`}>
              <button type="button" data-testid={`catalog-row-${item.object_id}`}
                      className="w-full px-3 py-2 text-left transition-colors hover:bg-muted/60"
                      onClick={() => onOpenRecord(item)}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-[12.5px] font-medium">
                    {item.title ?? item.summary ?? "（未记录标题）"}
                  </span>
                  <Badge variant="outline"
                         className={`rounded-sm text-[10px] ${item.formal_state.formal
                           ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                           : "border-border bg-muted text-muted-foreground"}`}>
                    {formalBusinessText(item.object_type, item.formal_state.status,
                                        item.formal_state.formal)}
                  </Badge>
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 text-[10.5px] text-muted-foreground">
                  <span>当前内容第 {item.object_version} 版</span>
                  <span>对象创建于 {formatTime(item.created_at)}</span>
                  <span>{rules ? `业务规则 ${rules}` : "规则版本未识别"}</span>
                </div>
              </button>
            </li>
          )
        })}
      </ul>
      {hasMore ? (
        <div className="space-y-1 p-3">
          <Button variant="outline" size="sm" className="w-full" onClick={() => void loadMore()}
                  disabled={loadingMore}>
            {loadingMore ? "加载中…" : "加载更多"}
          </Button>
          {loadMoreError ? (
            <p className="text-[11px] text-destructive" data-testid="catalog-load-more-error">
              {errorLabel(loadMoreError)} 请重试；已加载内容保持不变。
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
