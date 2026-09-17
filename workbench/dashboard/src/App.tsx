import { useCallback, useEffect, useRef, useState } from "react"
import { AuthLostPanel, ErrorBanner, PendingUpdateBanner, StaleBanner } from "@/components/Banners"
import { BusinessDefinitions } from "@/components/BusinessDefinitions"
import { BusinessGraph } from "@/components/BusinessGraph"
import { CatalogRecords } from "@/components/CatalogRecords"
import { DetailPane } from "@/components/DetailPane"
import { OntologyMap } from "@/components/OntologyMap"
import { TopBar } from "@/components/TopBar"
import { TypeInfoCard } from "@/components/TypeInfoCard"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { fetchDetail, fetchDownstream, fetchMethodMap, fetchObjectReceipts,
         fetchOntologyCatalog, fetchOverview, fetchRevisions } from "@/lib/api"
import { isAbort, isAccessDenial } from "@/lib/errors"
import { useLiveResource } from "@/lib/live"
import { RULES_VERSION_LABELS } from "@/lib/labels"
import { RULES_VERSIONS, rulesOfContractVersion, type RulesVersion } from "@/lib/ontology"
import { DEFAULT_VIEW, mergeView, parseView, serializeView, type ViewState } from "@/lib/urlState"
import type { CatalogObjectItem } from "@/lib/types"

const VIEW_NAMES = ["map", "definitions", "graph"]

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
  // Legacy ?view=list links are normalized once so the address bar matches the
  // rendered ontology map (or object details).  serializeView keeps the
  // legacy object and filter parameters instead of dropping them.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("view") !== "list") return
    window.history.replaceState(null, "",
      `${window.location.pathname}${serializeView(parseView(window.location.search))}`)
  }, [])
  useEffect(() => {
    const onPop = () => {
      const parsed = parseView(window.location.search)
      if (new URLSearchParams(window.location.search).get("view") === "list") {
        window.history.replaceState(null, "",
          `${window.location.pathname}${serializeView(parsed)}`)
      }
      setView(parsed)
    }
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

export function App({ embedded = false }: { embedded?: boolean } = {}) {
  const [view, navigate] = useUrlView()
  const [accessLost, setAccessLost] = useState(false)
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
  const downstreamEpoch = useRef(0)
  const historyEpoch = useRef(0)
  const receiptsEpoch = useRef(0)
  const downstreamAbortRef = useRef<AbortController | null>(null)
  const historyAbortRef = useRef<AbortController | null>(null)
  const receiptsAbortRef = useRef<AbortController | null>(null)
  const desktop = useMediaQuery("(min-width: 1024px)")
  const activeView = view.view === "list"
    ? (view.object ? "graph" : "map")
    : VIEW_NAMES.includes(view.view) ? view.view : "map"
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
    resetDetailLoaders()
  }, [resetDetailLoaders])

  useEffect(() => () => {
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
  // stay explicit (never merged) via the URL.  The initial default adoption
  // passes the pinned revision through explicitly: mergeView invalidates a
  // revision on a user-driven strategy change, but adopting the only readable
  // strategy must keep an exact deep-linked object revision intact.
  useEffect(() => {
    const data = overview.data
    if (!data || view.strategy || accessLost) return
    if (!data.selection_required && data.selected_strategy_id) {
      navigate({ strategy: data.selected_strategy_id, rev: view.rev }, true)
    }
  }, [overview.data, view.strategy, view.rev, navigate, accessLost])

  const catalog = useLiveResource({
    key: `ontology-catalog#${generation}`,
    fetcher: (_key, signal) => fetchOntologyCatalog(signal),
    identity: (data) => data.versions.map(
      (entry) => `${entry.contract_version}:${entry.object_types.join(",")}`).join("|"),
    enabled: !accessLost && (visitedViews.has("map") || visitedViews.has("definitions")),
    onAccessDenied,
  })

  const methodMap = useLiveResource({
    key: `method-map#${generation}`,
    fetcher: (_key, signal) => fetchMethodMap(signal),
    identity: (data) => `${data.source_snapshot?.snapshot_sha256 ?? ""}`
      + `|${(data.runtime_implementation?.scope_enabled_contract_versions ?? []).join(",")}`
      + `|${(data.entries ?? []).map((entry) => entry.id).join(",")}`,
    enabled: !accessLost && (visitedViews.has("map") || visitedViews.has("definitions")),
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

  // A legacy object-list deep link carries object_type; show that type's real
  // records inside the map instead of dropping the filter reference when the
  // route is normalized.
  useEffect(() => {
    if (activeView === "map" && shownType && view.objectType === shownType) {
      setRecordsFor(shownType)
    }
  }, [activeView, shownType, view.objectType])

  // Legacy list filters that this type-scoped authorized read cannot apply are
  // stated instead of silently pretending they were honored.  Owner is applied
  // by the records panel when it appears in the loaded authorized rows.
  const legacyFilters = [
    view.domain ? "业务域" : null,
    view.periodFrom || view.periodTo ? "周期" : null,
  ].filter((label): label is string => label !== null)
  const legacyFilterNotice = legacyFilters.length ? (
    <p className="border-b border-border bg-amber-50 px-3 py-1.5 text-[10.5px] text-amber-900"
       data-testid="legacy-filter-note">
      {`旧列表链接保留的${legacyFilters.join("、")}筛选未应用；此面板按对象类型读取已授权记录。`}
    </p>
  ) : null

  const strategyId = view.strategy ?? overview.data?.selected_strategy_id ?? null
  const authEpochRef = useRef<string | null>(null)

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

  const resetDetail = detail.reset

  useEffect(() => {
    const observed = overview.latest ?? overview.data
    const identity = observed?.viewer
      ? `${observed.viewer.scope_id}:${observed.viewer.principal_id}:${observed.viewer.auth_epoch}`
      : null
    if (identity === null) return
    if (authEpochRef.current !== null && identity !== authEpochRef.current) {
      // Authorization version changed (e.g. a revocation bumped auth_epoch):
      // every protected projection is invalidated before any reload, so no
      // protected record or object can remain behind an update prompt.
      resetDetailLoaders()
      resetDetail()
      setGeneration((value) => value + 1)
    }
    authEpochRef.current = identity
  }, [overview.latest, overview.data, resetDetail, resetDetailLoaders])

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

  const openObject = useCallback((objectId: string, revisionId?: string) => {
    navigate({ object: objectId, rev: revisionId ?? null })
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
    detail.acceptPending()
    catalog.acceptPending()
    methodMap.acceptPending()
  }, [overview, detail, catalog, methodMap])
  const dismissAll = useCallback(() => {
    overview.dismissPending()
    detail.dismissPending()
    catalog.dismissPending()
    methodMap.dismissPending()
  }, [overview, detail, catalog, methodMap])

  const pending = Boolean(overview.pending || detail.pending || catalog.pending
    || methodMap.pending)
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
    <div className={`flex min-h-0 flex-col bg-background text-foreground ${embedded ? "h-full" : "h-screen"}`}>
      <TopBar embedded={embedded} overview={overview.data} strategyId={strategyId}
              onStrategyChange={(next) => navigate({ strategy: next, object: null, rev: null })}
              onRefresh={() => { overview.refresh(); detail.refresh(); catalog.refresh(); methodMap.refresh() }}
              refreshing={overview.loading || detail.loading || catalog.loading || methodMap.loading}
              view={activeView}
              onViewChange={(next) => navigate({ view: next })}
              rules={rulesValid ? view.rules : "0.3"}
              onRulesChange={(next) => navigate({ rules: next })} />
      {detail.stale || catalog.stale
        ? <StaleBanner updatedAt={detail.updatedAt ?? catalog.updatedAt} /> : null}
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
      <div className="flex min-h-0 flex-1">
        {visitedViews.has("definitions") ? (
          <main className={activeView === "definitions" ? "flex min-w-0 flex-1" : "hidden"}
                aria-hidden={activeView !== "definitions"} data-testid="definitions-view">
            {accessLost ? <AuthLostPanel /> : (
              <BusinessDefinitions methodMap={methodMap.data} loading={methodMap.loading}
                                   error={methodMap.error}
                                   onOpenType={(type) => { setRulesNotice(false)
                                     navigate({ view: "map", otype: type, object: null, rev: null }) }} />
            )}
          </main>
        ) : null}
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
                                    methodMap={methodMap.data}
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
                        {legacyFilterNotice}
                        <CatalogRecords key={`${shownType}#${generation}`}
                                        objectType={shownType}
                                        legacyOwner={view.owner}
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
                                      methodMap={methodMap.data}
                                      onSelectType={selectType}
                                      onViewData={(type) => setRecordsFor(type)}
                                      onClose={() => navigate({ otype: null })} />
                      </div>
                      {recordsFor === shownType ? (
                        <div className="max-h-[45%] min-h-0 overflow-auto border-t border-border">
                          {legacyFilterNotice}
                          <CatalogRecords key={`${shownType}#${generation}`}
                                          objectType={shownType}
                                          legacyOwner={view.owner}
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
    </div>
  )
}
