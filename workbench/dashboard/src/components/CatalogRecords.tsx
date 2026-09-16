import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchCatalogObjects } from "@/lib/api"
import { errorLabel, isAbort, isAccessDenial } from "@/lib/errors"
import { useLiveResource } from "@/lib/live"
import { TYPE_LABELS, formalBusinessText } from "@/lib/labels"
import { rulesOfContractVersion } from "@/lib/ontology"
import { formatTime } from "@/lib/format"
import type { CatalogObjectItem, CatalogObjectsPage } from "@/lib/types"

export interface CatalogRecordsProps {
  objectType: string
  onOpenRecord: (item: CatalogObjectItem) => void
  onAccessDenied: () => void
}

function pageIdentity(page: CatalogObjectsPage): string {
  return page.items.map((item) =>
    `${item.object_id}:${item.basis_revision_id}:${item.formal_state.status}`).join("|")
}

/**
 * 某一注册类型的真实记录分页列表（catalog/objects，授权目录）。
 * 空结果只说明当前身份与筛选下未读到记录；请求失败与接口不可用单独表达，
 * 不冒充空结果。服务端更新以提示确认方式出现，网络失败保留旧内容并标注过期。
 */
export function CatalogRecords({ objectType, onOpenRecord, onAccessDenied }: CatalogRecordsProps) {
  const [moreItems, setMoreItems] = useState<CatalogObjectItem[]>([])
  const [moreCursor, setMoreCursor] = useState<string | null | undefined>(undefined)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<unknown>(null)
  const epochRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

  const page = useLiveResource({
    key: `catalog-objects#${objectType}`,
    fetcher: (_key, signal) => fetchCatalogObjects(objectType, null, signal),
    identity: pageIdentity,
    onAccessDenied,
  })

  useEffect(() => {
    epochRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setMoreItems([])
    setMoreCursor(undefined)
    setLoadMoreError(null)
    setLoadingMore(false)
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
        已加载 {items.length} 条{hasMore ? "（分页未读完，不表示只有这些）" : ""}
      </div>
      {items.length === 0 ? <p className="p-4 text-[12px] text-muted-foreground" data-testid="catalog-empty">
        当前身份与筛选下未读到「{TYPE_LABELS[objectType] ?? objectType}」的记录；
        这不表示全局没有数据，也不改变该类型在地图中的展示。
      </p> : null}
      <ul className="divide-y divide-border">
        {items.map((item) => {
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
