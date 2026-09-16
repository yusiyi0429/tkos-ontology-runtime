import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AuthLostPanel, ErrorBanner, PendingUpdateBanner, StaleBanner } from "@/components/Banners"
import { BusinessGraph } from "@/components/BusinessGraph"
import { CatalogRecords } from "@/components/CatalogRecords"
import { DetailPane } from "@/components/DetailPane"
import { FilterBar } from "@/components/FilterBar"
import { ObjectList } from "@/components/ObjectList"
import { OntologyMap } from "@/components/OntologyMap"
import { SideNav } from "@/components/SideNav"
import { TopBar } from "@/components/TopBar"
import { TypeInfoCard } from "@/components/TypeInfoCard"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { fetchDetail, fetchDownstream, fetchObjectReceipts, fetchObjects, fetchOntologyCatalog,
         fetchOverview, fetchRevisions, objectPath } from "@/lib/api"
import { isAbort, isAccessDenial } from "@/lib/errors"
import { useLiveResource } from "@/lib/live"
import { GROUP_LABELS, RULES_VERSION_LABELS } from "@/lib/labels"
import { RULES_VERSIONS, rulesOfContractVersion, type RulesVersion } from "@/lib/ontology"
import { DEFAULT_VIEW, mergeView, parseView, serializeView, type ViewState } from "@/lib/urlState"
import type { CatalogObjectItem, ObjectListItem, ObjectsPage } from "@/lib/types"

const VIEW_NAMES = ["map", "graph", "list"]

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia(query).matches : true)
  useEffect(() => {
    const media = window.matchMedia(query)
    const listener = () => setMatches(media.matches)
    media.addEventListener("change", listener)
    return () => media.removeEventListener("change", listener)
  }, [query])
  return matches
}

function useUrlView(): [ViewState, (patch: Partial<ViewState>, replace?: boolean) => void] {
  const [view, setView] = useState<ViewState>(() =>
    typeof window === "undefined" ? { ...DEFAULT_VIEW } : parseView(window.location.search))
  useEffect(() => {
    const onPop = () => setView(parseView(window.location.search))
    window.addEventListener("popstate", onPop)
    return () => window.removeEventListener("popstate", onPop)
  }, [])
  const navigate = useCallback((patch: Partial<ViewState>, replace = false) => {
    setView((current) => {
      const next = mergeView(current, patch)
      const url = `${window.location.pathname}${serializeView(next)}`
      if (replace) window.history.replaceState(null, "", url)
      else window.history.pushState(null, "", url)
      return next
    })
  }, [])
  return [view, navigate]
}

function objectsIdentity(page: ObjectsPage): string {
  return page.items.map((item) =>
    `${item.object_id}:${item.basis_revision_id}:${item.formal_state.status}`).join("|")
}

export function App() {
  const [view, navigate] = useUrlView()
  const [accessLost, setAccessLost] = useState(false)
  const [moreItems, setMoreItems] = useState<ObjectListItem[]>([])
  // undefined = use the first page cursor, null = explicitly exhausted, string = continuation
  const [moreCursor, setMoreCursor] = useState<string | null | undefined>(undefined)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<unknown>(null)
  const [downstreamMore, setDownstreamMore] = useState<import("@/lib/types").DownstreamEdge[]>([])
  const [downstreamCursor, setDownstreamCursor] = useState<string | null | undefined>(undefined)
  const [downstreamLoading, setDownstreamLoading] = useState(false)
  const [downstreamError, setDownstreamError] = useState<unknown>(null)
  const [historyMore, setHistoryMore] = useState<import("@/lib/types").HistoryRevision[]>([])
  const [historyCursor, setHistoryCursor] = useState<string | null | undefined>(undefined)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<unknown>(null)
  const [receiptsMore, setReceiptsMore] = useState<Array<Record<string, unknown>>>([])
  const [receiptsCursor, setReceiptsCursor] = useState<string | null | undefined>(undefined)
  const [receiptsLoading, setReceiptsLoading] = useState(false)
  const [receiptsError, setReceiptsError] = useState<unknown>(null)
  const [generation, setGeneration] = useState(0)
  const loadMoreEpoch = useRef(0)
  const downstreamEpoch = useRef(0)
  const historyEpoch = useRef(0)
  const receiptsEpoch = useRef(0)
  const objectsAbortRef = useRef<AbortController | null>(null)
  const downstreamAbortRef = useRef<AbortController | null>(null)
  const historyAbortRef = useRef<AbortController | null>(null)
  const receiptsAbortRef = useRef<AbortController | null>(null)
  const desktop = useMediaQuery("(min-width: 1024px)")
  const activeView = VIEW_NAMES.includes(view.view) ? view.view : "map"
  const rulesValid = (RULES_VERSIONS as string[]).includes(view.rules)
  const [recordsFor, setRecordsFor] = useState<string | null>(null)
  const [rulesNotice, setRulesNotice] = useState(false)
  // Map/graph subtrees stay mounted once visited so pan/zoom/expansions and
  // reading position survive plain view switches (F3); they only reset on
  // identity/authorization/strategy boundaries.
  const [visitedViews, setVisitedViews] = useState<ReadonlySet<string>>(() => new Set([activeView]))
  useEffect(() => {
    setVisitedViews((current) => current.has(activeView) ? current : new Set(current).add(activeView))
  }, [activeView])

  const resetObjectLoaders = useCallback(() => {
    loadMoreEpoch.current += 1
    objectsAbortRef.current?.abort()
    objectsAbortRef.current = null
    setMoreItems([])
    setMoreCursor(undefined)
    setLoadMoreError(null)
    setLoadingMore(false)
  }, [])

  const resetDetailLoaders = useCallback(() => {
    downstreamEpoch.current += 1
    downstreamAbortRef.current?.abort()
    downstreamAbortRef.current = null
    setDownstreamMore([])
    setDownstreamCursor(undefined)
    setDownstreamError(null)
    setDownstreamLoading(false)
    historyEpoch.current += 1
    historyAbortRef.current?.abort()
    historyAbortRef.current = null
    setHistoryMore([])
    setHistoryCursor(undefined)
    setHistoryError(null)
    setHistoryLoading(false)
    receiptsEpoch.current += 1
    receiptsAbortRef.current?.abort()
    receiptsAbortRef.current = null
    setReceiptsMore([])
    setReceiptsCursor(undefined)
    setReceiptsError(null)
    setReceiptsLoading(false)
  }, [])

  const onAccessDenied = useCallback(() => {
    // Clear every protected projection, including the overview/private header
    // (via disabled hooks) and all manual load-more state, and abort in-flight
    // manual requests so a late response can never repopulate the page.
    setAccessLost(true)
    resetObjectLoaders()
    resetDetailLoaders()
  }, [resetObjectLoaders, resetDetailLoaders])

  useEffect(() => () => {
    objectsAbortRef.current?.abort()
    downstreamAbortRef.current?.abort()
    historyAbortRef.current?.abort()
    receiptsAbortRef.current?.abort()
  }, [])

  const overview = useLiveResource({
    key: `overview#${view.strategy ?? ""}`,
    fetcher: (_key, signal) => fetchOverview(view.strategy, signal),
    identity: (data) => `${data.viewer?.principal_id ?? "none"}|${data.selected_strategy_id ?? "none"}`
      + `|${data.groups.map((group) => `${group.group}:${group.available}:${group.historical_available}`).join(",")}`
      + `|${data.historical_basis.groups.join(",")}`,
    enabled: !accessLost,
    onAccessDenied,
  })

  // Exactly one readable Strategy is selected automatically; multiple choices
  // stay explicit (never merged) via the URL.
  useEffect(() => {
    const data = overview.data
    if (!data || view.strategy || accessLost) return
    if (!data.selection_required && data.selected_strategy_id) {
      navigate({ strategy: data.selected_strategy_id }, true)
    }
  }, [overview.data, view.strategy, navigate, accessLost])

  const catalog = useLiveResource({
    key: `ontology-catalog#${generation}`,
    fetcher: (_key, signal) => fetchOntologyCatalog(signal),
    identity: (data) => data.versions.map(
      (entry) => `${entry.contract_version}:${entry.object_types.join(",")}`).join("|"),
    enabled: !accessLost && visitedViews.has("map"),
    onAccessDenied,
  })

  // A type selection is scoped to its rule version: after switching versions or
  // loading a directory where the type is not registered, clear the selection
  // instead of showing it under rules it does not belong to.
  useEffect(() => {
    const data = catalog.data
    if (!data || !view.otype || !rulesValid) return
    const entry = data.versions.find(
      (version) => rulesOfContractVersion(version.contract_version) === view.rules)
    if (entry && !entry.object_types.includes(view.otype)) {
      navigate({ otype: null }, true)
    }
  }, [catalog.data, view.rules, view.otype, rulesValid, navigate])

  const shownType = rulesValid && catalog.data?.versions.some((version) =>
    rulesOfContractVersion(version.contract_version) === view.rules
    && version.object_types.includes(view.otype ?? "")) ? view.otype : null

  const strategyId = view.strategy ?? overview.data?.selected_strategy_id ?? null
  const authEpochRef = useRef<string | null>(null)

  const objectsKey = accessLost || !strategyId || activeView !== "list" ? null : objectPath({
    group: view.group,
    basis: view.basis,
    strategyId,
    objectType: view.objectType,
    domainId: view.domain,
    periodFrom: view.periodFrom ? `${view.periodFrom}T00:00:00+00:00` : null,
    periodTo: view.periodTo ? `${view.periodTo}T23:59:59+00:00` : null,
    ownerId: view.owner,
    limit: 25,
  }) + `#${view.strategy ?? ""}#${generation}`

  const objects = useLiveResource({
    key: objectsKey,
    fetcher: (_key, signal) => fetchObjects({
      group: view.group,
      basis: view.basis,
      strategyId,
      objectType: view.objectType,
      domainId: view.domain,
      periodFrom: view.periodFrom ? `${view.periodFrom}T00:00:00+00:00` : null,
      periodTo: view.periodTo ? `${view.periodTo}T23:59:59+00:00` : null,
      ownerId: view.owner,
      limit: 25,
    }, signal),
    identity: objectsIdentity,
    enabled: !accessLost,
    onAccessDenied,
  })

  useEffect(() => {
    resetObjectLoaders()
  }, [objectsKey, resetObjectLoaders])

  const detailKey = accessLost || !view.object
    ? null
    : `${view.object}#${view.rev ?? "effective"}#${strategyId ?? ""}#${generation}`
  const detail = useLiveResource({
    key: detailKey,
    fetcher: (_key, signal) => fetchDetail(view.object as string, view.rev, strategyId, signal),
    identity: (data) =>
      `${data.selected_revision.revision_id}:${data.selected_revision.payload_hash}`
      + `:${data.formal_state.status}:${data.content_confirmation.confirmed_for_selected_revision}`,
    enabled: !accessLost,
    onAccessDenied,
  })
  useEffect(() => {
    // Invalidate manual paging on the request key itself (view/strategy/object/
    // revision/authorization generation), not one render later via response data.
    resetDetailLoaders()
  }, [detailKey, resetDetailLoaders])

  const resetObjects = objects.reset
  const resetDetail = detail.reset

  useEffect(() => {
    const observed = overview.latest ?? overview.data
    const identity = observed?.viewer
      ? `${observed.viewer.scope_id}:${observed.viewer.principal_id}:${observed.viewer.auth_epoch}`
      : null
    if (identity === null) return
    if (authEpochRef.current !== null && identity !== authEpochRef.current) {
      // Authorization version changed (e.g. a revocation bumped auth_epoch):
      // every protected projection is invalidated before any reload, so a
      // removed list row or object cannot remain behind an update prompt.
      resetObjectLoaders()
      resetDetailLoaders()
      resetObjects()
      resetDetail()
      setGeneration((value) => value + 1)
    }
    authEpochRef.current = identity
  }, [overview.latest, overview.data, resetObjects, resetDetail,
      resetObjectLoaders, resetDetailLoaders])

  const loadMoreDownstream = useCallback(async () => {
    const data = detail.data
    if (!data || downstreamLoading || accessLost) return
    const cursor = downstreamCursor === undefined
      ? data.relations.downstream.next_cursor : downstreamCursor
    if (!cursor) return
    const epoch = ++downstreamEpoch.current
    setDownstreamLoading(true)
    setDownstreamError(null)
    downstreamAbortRef.current?.abort()
    const controller = new AbortController()
    downstreamAbortRef.current = controller
    try {
      const page = await fetchDownstream(data.object.object_id,
                                         data.selected_revision.revision_id, cursor,
                                         controller.signal)
      if (epoch !== downstreamEpoch.current) return
      setDownstreamMore((current) => {
        const seen = new Set(current.map((edge) => `${edge.object_id}:${edge.ref.revision_id}`))
        return [...current, ...page.items.filter(
          (edge) => !seen.has(`${edge.object_id}:${edge.ref.revision_id}`))]
      })
      setDownstreamCursor(page.next_cursor)
    } catch (error) {
      if (epoch !== downstreamEpoch.current) return
      if (isAbort(error)) return
      if (isAccessDenial(error)) {
        onAccessDenied()
        return
      }
      setDownstreamError(error)
    } finally {
      if (downstreamAbortRef.current === controller) downstreamAbortRef.current = null
      if (epoch === downstreamEpoch.current) setDownstreamLoading(false)
    }
  }, [detail.data, downstreamCursor, downstreamLoading, accessLost, onAccessDenied])

  const loadMoreHistory = useCallback(async () => {
    const data = detail.data
    if (!data || historyLoading || accessLost) return
    const cursor = historyCursor === undefined ? data.history.next_cursor : historyCursor
    if (!cursor) return
    const epoch = ++historyEpoch.current
    setHistoryLoading(true)
    setHistoryError(null)
    historyAbortRef.current?.abort()
    const controller = new AbortController()
    historyAbortRef.current = controller
    try {
      const page = await fetchRevisions(data.object.object_id, cursor, controller.signal)
      if (epoch !== historyEpoch.current) return
      setHistoryMore((current) => {
        const seen = new Set(current.map((item) => item.revision_id))
        return [...current, ...page.items.filter((item) => !seen.has(item.revision_id))]
      })
      setHistoryCursor(page.next_cursor)
    } catch (error) {
      if (epoch !== historyEpoch.current || isAbort(error)) return
      if (isAccessDenial(error)) { onAccessDenied(); return }
      setHistoryError(error)
    } finally {
      if (historyAbortRef.current === controller) historyAbortRef.current = null
      if (epoch === historyEpoch.current) setHistoryLoading(false)
    }
  }, [detail.data, historyCursor, historyLoading, accessLost, onAccessDenied])

  const loadMoreReceipts = useCallback(async () => {
    const data = detail.data
    if (!data || receiptsLoading || accessLost) return
    const cursor = receiptsCursor === undefined ? data.receipts.next_cursor : receiptsCursor
    if (!cursor) return
    const epoch = ++receiptsEpoch.current
    setReceiptsLoading(true)
    setReceiptsError(null)
    receiptsAbortRef.current?.abort()
    const controller = new AbortController()
    receiptsAbortRef.current = controller
    try {
      const page = await fetchObjectReceipts(data.object.object_id, cursor, controller.signal)
      if (epoch !== receiptsEpoch.current) return
      setReceiptsMore((current) => {
        const seen = new Set(current.map((item) => String(item.receipt_id)))
        return [...current, ...page.items.filter((item) => !seen.has(String(item.receipt_id)))]
      })
      setReceiptsCursor(page.next_cursor)
    } catch (error) {
      if (epoch !== receiptsEpoch.current || isAbort(error)) return
      if (isAccessDenial(error)) { onAccessDenied(); return }
      setReceiptsError(error)
    } finally {
      if (receiptsAbortRef.current === controller) receiptsAbortRef.current = null
      if (epoch === receiptsEpoch.current) setReceiptsLoading(false)
    }
  }, [detail.data, receiptsCursor, receiptsLoading, accessLost, onAccessDenied])

  const loadMore = useCallback(async () => {
    const cursor = moreCursor === undefined ? objects.data?.next_cursor : moreCursor
    if (!cursor || loadingMore || accessLost) return
    const epoch = ++loadMoreEpoch.current
    setLoadingMore(true)
    setLoadMoreError(null)
    objectsAbortRef.current?.abort()
    const controller = new AbortController()
    objectsAbortRef.current = controller
    try {
      const page = await fetchObjects({
        group: view.group, basis: view.basis, strategyId, objectType: view.objectType,
        domainId: view.domain,
        periodFrom: view.periodFrom ? `${view.periodFrom}T00:00:00+00:00` : null,
        periodTo: view.periodTo ? `${view.periodTo}T23:59:59+00:00` : null,
        ownerId: view.owner, limit: 25, cursor,
      }, controller.signal)
      if (epoch !== loadMoreEpoch.current) return // obsolete after filters/object/access changed
      setMoreItems((current) => {
        const seen = new Set(current.map((item) => item.object_id))
        return [...current, ...page.items.filter((item) => !seen.has(item.object_id))]
      })
      // null is a real exhausted state and must not resurrect the first cursor.
      setMoreCursor(page.next_cursor)
    } catch (error) {
      if (epoch !== loadMoreEpoch.current) return
      if (isAbort(error)) return
      if (isAccessDenial(error)) {
        onAccessDenied()
        return
      }
      setLoadMoreError(error)
    } finally {
      if (objectsAbortRef.current === controller) objectsAbortRef.current = null
      if (epoch === loadMoreEpoch.current) setLoadingMore(false)
    }
  }, [moreCursor, objects.data?.next_cursor, loadingMore, accessLost, onAccessDenied, view.group,
      view.basis, strategyId, view.objectType, view.domain, view.periodFrom, view.periodTo, view.owner])

  const items = useMemo(() => {
    const first = objects.data?.items ?? []
    const seen = new Set(first.map((item) => item.object_id))
    return [...first, ...moreItems.filter((item) => !seen.has(item.object_id))]
  }, [objects.data, moreItems])

  const openObject = useCallback((objectId: string, revisionId?: string) => {
    navigate({ object: objectId, rev: revisionId ?? null })
  }, [navigate])

  const selectGroup = useCallback((group: string, basis: string) => {
    navigate({ group, basis, object: null, rev: null, objectType: null })
  }, [navigate])

  const selectType = useCallback((type: string) => {
    setRecordsFor(null)
    navigate({ otype: type })
  }, [navigate])

  // Formal-first: open the effective revision when one is recorded; otherwise
  // the backend-selected content version.  The revision stays pinned in the URL.
  const openCatalogRecord = useCallback((item: CatalogObjectItem) => {
    navigate({ view: "graph", object: item.object_id,
               rev: item.effective_revision_id ?? item.basis_revision_id ?? item.latest_revision_id })
  }, [navigate])

  // Rule lookup for a real object always follows its recorded contract version;
  // an unrecognized version is surfaced, never silently mapped to the latest.
  const showTypeRules = useCallback((objectType: string, contractVersion: unknown) => {
    const rules = typeof contractVersion === "string"
      ? rulesOfContractVersion(contractVersion) : null
    if (!rules) {
      setRulesNotice(true)
      return
    }
    setRulesNotice(false)
    navigate({ view: "map", rules, otype: objectType, object: null, rev: null })
  }, [navigate])

  const acceptAll = useCallback(() => {
    overview.acceptPending()
    objects.acceptPending()
    detail.acceptPending()
    catalog.acceptPending()
  }, [overview, objects, detail, catalog])
  const dismissAll = useCallback(() => {
    overview.dismissPending()
    objects.dismissPending()
    detail.dismissPending()
    catalog.dismissPending()
  }, [overview, objects, detail, catalog])

  const pending = Boolean(overview.pending || objects.pending || detail.pending || catalog.pending)
  const groupEntry = overview.data?.groups.find((entry) => entry.group === view.group)

  const detailMoreEdges = downstreamMore
  const detailHasMore = downstreamCursor === null
    ? false
    : Boolean(downstreamCursor ?? detail.data?.relations.downstream.next_cursor)
  const historyHasMore = historyCursor === null
    ? false
    : Boolean(historyCursor ?? detail.data?.history.next_cursor)
  const receiptsHasMore = receiptsCursor === null
    ? false
    : Boolean(receiptsCursor ?? detail.data?.receipts.next_cursor)
  const detailBody = accessLost ? <AuthLostPanel /> : (
    <DetailPane detail={detail.data} loading={detail.loading} error={detail.error}
                onOpenObject={openObject}
                onSelectRevision={(revisionId) => navigate({ rev: revisionId })}
                downstreamMore={detailMoreEdges}
                downstreamHasMore={detailHasMore}
                downstreamLoading={downstreamLoading}
                downstreamError={downstreamError}
                onLoadMoreDownstream={() => void loadMoreDownstream()}
                historyMore={historyMore}
                historyHasMore={historyHasMore}
                historyLoading={historyLoading}
                historyError={historyError}
                onLoadMoreHistory={() => void loadMoreHistory()}
                receiptsMore={receiptsMore}
                receiptsHasMore={receiptsHasMore}
                receiptsLoading={receiptsLoading}
                receiptsError={receiptsError}
                onLoadMoreReceipts={() => void loadMoreReceipts()}
                onAccessDenied={onAccessDenied}
                onShowTypeRules={showTypeRules} />
  )

  return (
    <div className="flex h-screen min-h-0 flex-col bg-background text-foreground">
      <TopBar overview={overview.data} strategyId={strategyId}
              onStrategyChange={(next) => navigate({ strategy: next, object: null, rev: null })}
              onRefresh={() => { overview.refresh(); objects.refresh(); detail.refresh(); catalog.refresh() }}
              refreshing={overview.loading || objects.loading || detail.loading || catalog.loading}
              view={activeView}
              onViewChange={(next) => navigate({ view: next })}
              rules={rulesValid ? view.rules : "0.3"}
              onRulesChange={(next) => navigate({ rules: next })} />
      {objects.stale || detail.stale || catalog.stale
        ? <StaleBanner updatedAt={objects.updatedAt ?? detail.updatedAt ?? catalog.updatedAt} /> : null}
      {pending ? <PendingUpdateBanner onAccept={acceptAll} onDismiss={dismissAll} /> : null}
      {rulesNotice ? (
        <Alert variant="destructive" data-testid="rules-notice">
          <AlertTitle>该对象绑定的业务规则版本未被识别</AlertTitle>
          <AlertDescription className="flex items-center gap-2">
            未套用最新规则；请在本体地图手动选择业务规则版本查看。
            <Button size="xs" variant="outline" onClick={() => setRulesNotice(false)}>知道了</Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {activeView !== "list" ? (
        <div className="flex min-h-0 flex-1">
          {visitedViews.has("map") ? (
            <main className={activeView === "map" ? "flex min-w-0 flex-1" : "hidden"}
                  aria-hidden={activeView !== "map"}>
              <section className="min-w-0 flex-1">
                {accessLost ? <AuthLostPanel /> : !rulesValid ? (
                  <div className="p-4" data-testid="rules-invalid">
                    <Alert>
                      <AlertTitle>未知的业务规则版本</AlertTitle>
                      <AlertDescription className="flex flex-wrap items-center gap-2">
                        请选择一个有效的版本：
                        {RULES_VERSIONS.map((version) => (
                          <Button key={version} size="xs" variant="outline"
                                  onClick={() => navigate({ rules: version })}>
                            {RULES_VERSION_LABELS[version]}
                          </Button>
                        ))}
                      </AlertDescription>
                    </Alert>
                  </div>
                ) : (
                  <OntologyMap rules={view.rules as RulesVersion}
                               catalog={catalog.data} loading={catalog.loading}
                               error={catalog.error} selectedType={shownType}
                               onSelectType={selectType} />
                )}
              </section>
              {desktop && shownType ? (
                <aside className="flex w-[400px] shrink-0 flex-col border-l border-border">
                  {shownType ? (
                    <>
                      <div className="min-h-0 flex-1">
                        <TypeInfoCard type={shownType}
                                      rules={view.rules as RulesVersion}
                                      catalog={catalog.data}
                                      onSelectType={selectType}
                                      onViewData={(type) => setRecordsFor(type)}
                                      onClose={() => navigate({ otype: null })} />
                      </div>
                      {recordsFor === shownType ? (
                        <div className="max-h-[45%] min-h-0 overflow-auto border-t border-border"
                             data-testid="type-records">
                          <div className="border-b border-border bg-muted/40 px-3 py-1.5 text-[11px] text-muted-foreground">
                            实际记录（当前身份授权范围）
                          </div>
                          <CatalogRecords key={`${shownType}#${generation}`}
                                          objectType={shownType}
                                          onOpenRecord={openCatalogRecord}
                                          onAccessDenied={onAccessDenied} />
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <div className="p-6 text-sm text-muted-foreground" data-testid="type-empty">
                      在地图中选择一个对象类型，查看其业务说明与实际数据入口。
                    </div>
                  )}
                </aside>
              ) : (
                <Sheet open={activeView === "map" && Boolean(shownType)}
                       onOpenChange={(open) => { if (!open) navigate({ otype: null }) }}>
                  <SheetContent side="right" showCloseButton={false}
                                className="flex w-full max-w-none flex-col p-0 data-[side=right]:w-full data-[side=right]:max-w-none sm:max-w-xl">
                    <SheetHeader className="sr-only"><SheetTitle>类型说明</SheetTitle></SheetHeader>
                    {shownType ? (
                      <>
                        <div className="min-h-0 flex-1">
                          <TypeInfoCard type={shownType}
                                        rules={view.rules as RulesVersion}
                                        catalog={catalog.data}
                                        onSelectType={selectType}
                                        onViewData={(type) => setRecordsFor(type)}
                                        onClose={() => navigate({ otype: null })} />
                        </div>
                        {recordsFor === shownType ? (
                          <div className="max-h-[45%] min-h-0 overflow-auto border-t border-border">
                            <CatalogRecords key={`${shownType}#${generation}`}
                                            objectType={shownType}
                                            onOpenRecord={openCatalogRecord}
                                            onAccessDenied={onAccessDenied} />
                          </div>
                        ) : null}
                      </>
                    ) : null}
                  </SheetContent>
                </Sheet>
              )}
            </main>
          ) : null}
          {visitedViews.has("graph") ? (
            <main className={activeView === "graph" ? "flex min-w-0 flex-1" : "hidden"}
                  aria-hidden={activeView !== "graph"}>
              <section className="min-w-0 flex-1">
                {accessLost ? <AuthLostPanel /> : (
                  <BusinessGraph
                    strategyId={strategyId}
                    entryFocus={view.object
                      ? { objectId: view.object, revisionId: view.rev, label: view.object }
                      : (() => {
                          const choice = overview.data?.strategy_choices.find(
                            (entry) => entry.strategy_id === strategyId)
                          return choice
                            ? { objectId: choice.strategy_id, revisionId: choice.revision_id,
                                label: choice.title ?? "当前战略" }
                            : null
                        })()}
                    entryKey={`graph#${strategyId ?? ""}#${generation}`}
                    active={activeView === "graph" && !accessLost}
                    selectedObject={view.object}
                    selectedRevision={view.rev}
                    onOpenObject={openObject}
                    onAccessDenied={onAccessDenied} />
                )}
              </section>
              {desktop && view.object ? (
                <section className="w-[420px] shrink-0 border-l border-border bg-muted/30">
                  <div className="h-full min-h-0 overflow-hidden bg-background">
                    <ErrorBanner error={detail.error} updatedAt={detail.updatedAt} />
                    {view.object ? detailBody : (
                      <div className="p-6 text-sm text-muted-foreground" data-testid="graph-detail-empty">
                        点击关系图中的对象名称查看其正式内容、依据与确认证据。
                      </div>
                    )}
                  </div>
                </section>
              ) : (
                <Sheet open={activeView === "graph" && !desktop && Boolean(view.object)}
                       onOpenChange={(open) => { if (!open) navigate({ object: null, rev: null }) }}>
                  <SheetContent side="right" showCloseButton={false}
                                className="flex w-full max-w-none flex-col p-0 data-[side=right]:w-full data-[side=right]:max-w-none sm:max-w-2xl">
                    <SheetHeader className="flex flex-row items-center justify-between gap-2 border-b border-border px-4 py-2.5">
                      <SheetTitle className="text-[13px]">对象详情</SheetTitle>
                      <SheetClose asChild>
                        <Button size="xs" variant="outline">关闭详情</Button>
                      </SheetClose>
                    </SheetHeader>
                    <div className="min-h-0 flex-1 overflow-hidden">
                      <ErrorBanner error={detail.error} updatedAt={detail.updatedAt} />
                      {detailBody}
                    </div>
                  </SheetContent>
                </Sheet>
              )}
            </main>
          ) : null}
        </div>
      ) : (
      <div className="flex min-h-0 flex-1">
        {desktop ? (
          <aside className="w-60 shrink-0 border-r border-border">
            <SideNav overview={overview.data} group={view.group} basis={view.basis} onSelect={selectGroup} />
          </aside>
        ) : null}
        <main className="flex min-w-0 flex-1">
          <section className={`flex min-h-0 flex-col border-r border-border bg-card ${
            desktop ? "w-[380px] shrink-0" : "w-full"}`}>
            <div className="flex items-center gap-2 border-b border-border px-3 py-2">
              {!desktop ? (
                <Sheet>
                  <SheetTrigger asChild>
                    <Button size="xs" variant="outline">导航</Button>
                  </SheetTrigger>
                  <SheetContent side="left" className="w-72 bg-nav p-0">
                    <SheetHeader className="sr-only"><SheetTitle>导航</SheetTitle></SheetHeader>
                    <SideNav overview={overview.data} group={view.group} basis={view.basis}
                             onSelect={(group, basis) => { selectGroup(group, basis) }} />
                  </SheetContent>
                </Sheet>
              ) : null}
              <div className="min-w-0">
                <div className="truncate text-[13px] font-semibold">
                  {GROUP_LABELS[view.group] ?? view.group}
                  {view.basis === "all" || view.basis === "current" ? " · 当前与历史依据" : ""}
                  {view.basis === "historical" ? " · 仅历史依据" : ""}
                  {view.basis === "unattached" ? " · 未关联" : ""}
                </div>
                <div className="text-[10.5px] text-muted-foreground">
                  {groupEntry?.available === false && groupEntry?.historical_available === false
                    ? "当前战略未记录该分组" : "正式层级视图"}
                </div>
              </div>
            </div>
            <FilterBar items={items} domain={view.domain} periodFrom={view.periodFrom}
                       periodTo={view.periodTo} owner={view.owner}
                       onChange={(patch) => navigate({ ...patch, object: null, rev: null })} />
            <ErrorBanner error={objects.error} updatedAt={objects.updatedAt} />
            <div className="min-h-0 flex-1 overflow-auto">
              <ObjectList items={items} selectedId={view.object} loading={objects.loading}
                          hasMore={moreCursor === null ? false
                            : Boolean(moreCursor ?? objects.data?.next_cursor)}
                          loadMoreError={loadMoreError}
                          onSelect={(item) => openObject(item.object_id)}
                          onLoadMore={() => void loadMore()} />
            </div>
          </section>
          {desktop ? (
            <section className="min-w-0 flex-1 bg-muted/30">
              <div className="h-full min-h-0 overflow-hidden bg-background">
                <ErrorBanner error={detail.error} updatedAt={detail.updatedAt} />
                {detailBody}
              </div>
            </section>
          ) : (
            <Sheet open={Boolean(view.object)} onOpenChange={(open) => { if (!open) navigate({ object: null, rev: null }) }}>
              <SheetContent side="right" showCloseButton={false}
                            className="flex w-full max-w-none flex-col p-0 data-[side=right]:w-full data-[side=right]:max-w-none sm:max-w-2xl">
                <SheetHeader className="flex flex-row items-center justify-between gap-2 border-b border-border px-4 py-2.5">
                  <SheetTitle className="text-[13px]">对象详情</SheetTitle>
                  <SheetClose asChild>
                    <Button size="xs" variant="outline">关闭详情</Button>
                  </SheetClose>
                </SheetHeader>
                <div className="min-h-0 flex-1 overflow-hidden">
                  <ErrorBanner error={detail.error} updatedAt={detail.updatedAt} />
                  {detailBody}
                </div>
              </SheetContent>
            </Sheet>
          )}
        </main>
      </div>
      )}
    </div>
  )
}
