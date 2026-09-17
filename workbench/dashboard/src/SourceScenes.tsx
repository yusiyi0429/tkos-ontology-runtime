import { useCallback, useEffect, useRef, useState } from "react"
import { RefreshCw } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ApiError } from "@/lib/errors"

export type SourceSession = { identity: { principal_id: string; display_name: string }; csrf: string }
type Payload = Record<string, unknown>
export type SourceSceneSummary = { scene_id: string; scene_type: string; external_id: string;
  title: string; owner_principal_id: string; participant_principal_ids: string[];
  version: number; created_at: string }
type VersionEntry = { event_id: string; version_seq: number; payload_hash: string; fingerprint: string;
  media_type: string; acquired_at: string; origin_label?: string | null; status: string;
  correction_of?: string | null;
  segments?: Array<{ speaker?: string | null; text: string; occurred_at?: string | null }> | null;
  shares?: Array<{ share_event_id: string; version_event_id: string; share_to_principal_id: string;
                   active: boolean; note?: string | null }> }
type SourceEntry = { source_id: string; system: string; external_id: string; title: string;
  media_type: string; acquired_at: string; origin_label?: string | null; status: string;
  owner_principal_id: string; access: string; versions: VersionEntry[] }
type RunEntry = { event_id: string; agent_principal_id?: string; status: string; withheld?: boolean;
  purpose?: string; error?: string | null; output_refs?: unknown[] }
type DraftEntry = { event_id: string; status: string; title?: string | null; items?: Array<Payload>;
  created_by_principal_id?: string; withheld?: boolean }
type DecisionEntry = { event_id: string; draft_event_id: string; item_index: number;
  decision: string; note?: string | null; principal_id?: string }
export type SourceSceneView = {
  scene_id: string; version: number; formal_effect: string; principal_names?: Record<string, string>;
  scene: { scene_type: string; external_id: string; title: string; owner_principal_id: string;
           participant_principal_ids: string[]; agent_bindings: Array<{ agent_principal_id: string;
           owner_principal_id: string }> };
  sources: SourceEntry[]; runs: RunEntry[]; drafts: DraftEntry[]; decisions: DecisionEntry[];
  operations: Payload[] }

const BASE = '/dashboard/api/v1'
const SCENE_TYPES = ['meeting', 'document', 'selected_conversation'] as const
const DECISIONS = ['accepted', 'rejected', 'noted'] as const
const OPERATION_LABELS: Record<string, string> = {
  scene_create: '建立独立来源场景', source_add: '登记来源', source_version: '登记新版本',
  source_correct: '更正来源', source_withdraw: '撤回来源', source_share: '分享精确版本',
  source_unshare: '取消分享', followup_draft: '提交跟进草稿', draft_decision: '本人处理草稿条目',
  context_create: '生成 Context 快照',
}
const ITEM_KIND_LABELS: Record<string, string> = {
  fact: '事实', request: '请求', suggestion: '建议', accepted: '已接受',
}
const ITEM_KINDS = ['fact', 'request', 'suggestion', 'accepted'] as const

function err(error: unknown) {
  if (error instanceof ApiError) return `操作未完成（${error.code}）`
  return '服务暂时不可用；已发送的提交请先核对结果。'
}

export function sceneEnvelope(sceneId: string, expectedVersion: number, event: Payload, key: string) {
  return { contract_version: 'tkos.workspace/0.2', scene_id: sceneId,
           expected_version: expectedVersion, idempotency_key: key, event }
}

export function contextEnvelope(sceneId: string, purpose: string, items: Array<Payload>, key: string) {
  return { contract_version: 'tkos.workspace/0.2', scene_id: sceneId,
           idempotency_key: key, purpose, items }
}

export function eventFor(kind: string, values: Payload): Payload {
  const str = (name: string) => (typeof values[name] === 'string' ? values[name] as string : '')
  const rows = (name: string) => Array.isArray(values[name]) ? values[name] as Array<Payload> : []
  switch (kind) {
    case 'scene_create':
      return { kind, scene_type: str('scene_type'), external_id: str('external_id'), title: str('title'),
               owner_principal_id: str('owner_principal_id'),
               participant_principal_ids: rows('participants').map((row) => String(row.principal_id ?? '')).filter(Boolean),
               agent_bindings: rows('agent_bindings')
                 .filter((row) => row.agent_principal_id && row.owner_principal_id)
                 .map((row) => ({ agent_principal_id: String(row.agent_principal_id),
                                  owner_principal_id: String(row.owner_principal_id) })) }
    case 'source_add':
      return { kind, system: str('system'), external_id: str('external_id'), title: str('title'),
               media_type: str('media_type'), acquired_at: str('acquired_at'),
               ...(str('origin_label') ? { origin_label: str('origin_label') } : {}),
               sensitivity: 'private' }
    case 'source_version':
    case 'source_correct': {
      const event: Payload = { kind, source_id: str('source_id'), media_type: str('media_type'),
                               acquired_at: str('acquired_at'),
                               segments: rows('segments').filter((row) => row.text)
                                 .map((row) => ({ text: String(row.text),
                                                  ...(row.speaker ? { speaker: String(row.speaker) } : {}),
                                                  ...(row.occurred_at ? { occurred_at: String(row.occurred_at) } : {}) })) }
      if (kind === 'source_version') event.fingerprint = str('fingerprint')
      else { event.corrects_event_id = str('corrects_event_id'); event.reason = str('reason') }
      if (str('evidence_object_id') && str('evidence_revision_id') && str('evidence_payload_hash')) {
        event.evidence_ref = { object_id: str('evidence_object_id'),
                               revision_id: str('evidence_revision_id'),
                               payload_hash: str('evidence_payload_hash') }
      }
      return event
    }
    case 'source_withdraw':
      return { kind, source_id: str('source_id'), reason: str('reason'),
               ...(str('version_event_id') ? { version_event_id: str('version_event_id') } : {}) }
    case 'source_share':
      return { kind, source_id: str('source_id'), version_event_id: str('version_event_id'),
               payload_hash: str('payload_hash'), share_to_principal_id: str('share_to_principal_id'),
               ...(str('note') ? { note: str('note') } : {}) }
    case 'source_unshare':
      return { kind, share_event_id: str('share_event_id'), reason: str('reason') }
    case 'followup_draft':
      return { kind, title: str('title'),
               ...(str('run_event_id') ? { run_event_id: str('run_event_id') } : {}),
               items: rows('items').filter((row) => row.text).map((row) => {
                 const nested = Array.isArray(row.citations) ? row.citations as Array<Payload> : []
                 const citations = nested.length ? nested : (row.source_id ? [row] : [])
                 return { item_kind: String(row.item_kind ?? 'fact'), text: String(row.text),
                   citations: citations
                     .filter((c) => c.source_id && c.version_event_id && c.payload_hash && c.quote)
                     .map((c) => ({ source_id: String(c.source_id), version_event_id: String(c.version_event_id),
                                    payload_hash: String(c.payload_hash),
                                    segment_index: Number(c.segment_index ?? 0), quote: String(c.quote) })) } }) }
    case 'draft_decision':
      return { kind, draft_event_id: str('draft_event_id'), item_index: Number(str('item_index') || 0),
               decision: str('decision'), ...(str('note') ? { note: str('note') } : {}) }
    default:
      return { kind }
  }
}

type SField = { name: string; label: string
                type: 'text' | 'textarea' | 'select' | 'rows' | 'number' | 'datetime' | 'fingerprint'
                required?: boolean; options?: readonly string[]; rowFields?: Array<{ name: string; label: string }> }

const SEGMENTS: SField = { name: 'segments', label: '分段（可定位的原文）', type: 'rows',
  rowFields: [{ name: 'speaker', label: 'speaker（可空）' }, { name: 'text', label: '文本' },
              { name: 'occurred_at', label: 'occurred_at（可空 ISO）' }] }
const MEDIA_TYPES = ['text/plain', 'text/markdown', 'application/pdf', 'audio/mpeg',
                     'video/mp4', 'application/octet-stream'] as const
const EVIDENCE_OPTIONAL: SField[] = [
  { name: 'evidence_object_id', label: 'EvidenceAsset object_id（原本已共享时才填）', type: 'text' },
  { name: 'evidence_revision_id', label: 'EvidenceAsset revision_id', type: 'text' },
  { name: 'evidence_payload_hash', label: 'EvidenceAsset payload_hash', type: 'text' }]

export const SCENE_FORMS: Record<string, SField[]> = {
  scene_create: [{ name: 'scene_type', label: '场景类型', type: 'select', required: true, options: SCENE_TYPES },
                 { name: 'external_id', label: '外部标识（scope 内唯一）', type: 'text', required: true },
                 { name: 'title', label: '名称', type: 'text', required: true },
                 { name: 'participants', label: '参与人 principal_id（未提供姓名→身份目录，不做猜测）', type: 'rows',
                   rowFields: [{ name: 'principal_id', label: 'principal_id' }] },
                 { name: 'agent_bindings', label: 'Agent 绑定（可选；需当前绑定关系）', type: 'rows',
                   rowFields: [{ name: 'agent_principal_id', label: 'agent_principal_id' },
                               { name: 'owner_principal_id', label: 'owner_principal_id' }] }],
  source_add: [{ name: 'system', label: '来源系统', type: 'text', required: true },
               { name: 'external_id', label: '来源 ID', type: 'text', required: true },
               { name: 'title', label: '标题', type: 'text', required: true },
               { name: 'media_type', label: '内容类型', type: 'select', required: true, options: MEDIA_TYPES },
               { name: 'acquired_at', label: '获取时间', type: 'datetime', required: true },
               { name: 'origin_label', label: '来源说明（可空）', type: 'text' }],
  source_version: [{ name: 'source_id', label: '来源', type: 'select', required: true },
                   { name: 'fingerprint', label: '内容指纹', type: 'fingerprint', required: true },
                   { name: 'media_type', label: '内容类型', type: 'select', required: true, options: MEDIA_TYPES },
                   { name: 'acquired_at', label: '获取时间', type: 'datetime', required: true },
                   SEGMENTS, ...EVIDENCE_OPTIONAL],
  source_correct: [{ name: 'source_id', label: '来源', type: 'select', required: true },
                   { name: 'corrects_event_id', label: '被更正版本（选择来源版本）', type: 'select', required: true },
                   { name: 'reason', label: '更正理由', type: 'textarea', required: true },
                   { name: 'fingerprint', label: '内容指纹', type: 'fingerprint', required: true },
                   { name: 'media_type', label: '内容类型', type: 'select', required: true, options: MEDIA_TYPES },
                   { name: 'acquired_at', label: '获取时间', type: 'datetime', required: true },
                   SEGMENTS, ...EVIDENCE_OPTIONAL],
  source_withdraw: [{ name: 'source_id', label: '来源', type: 'select', required: true },
                    { name: 'version_event_id', label: '仅撤回该版本（可空＝整源）', type: 'select' },
                    { name: 'reason', label: '撤回理由', type: 'textarea', required: true }],
  source_share: [{ name: 'source_id', label: '来源', type: 'select', required: true },
                 { name: 'version_event_id', label: '精确版本', type: 'select', required: true },
                 { name: 'share_to_principal_id', label: '授权给（场景人类成员）', type: 'select', required: true },
                 { name: 'note', label: '说明（可空）', type: 'text' }],
  source_unshare: [{ name: 'share_event_id', label: '分享记录', type: 'select', required: true },
                   { name: 'reason', label: '取消理由', type: 'textarea', required: true }],
  followup_draft: [{ name: 'title', label: '草稿标题', type: 'text', required: true },
                   { name: 'run_event_id', label: '关联运行（可空）', type: 'select' },
                   { name: 'items', label: '条目（每条须引用确切片段）', type: 'rows', rowFields: [] }],
  draft_decision: [{ name: 'draft_event_id', label: '草稿', type: 'select', required: true },
                   { name: 'item_index', label: '条目序号', type: 'number', required: true },
                   { name: 'decision', label: '本人决定', type: 'select', required: true, options: DECISIONS },
                   { name: 'note', label: '说明（可空）', type: 'text' }],
}

export async function sha256Hex(data: ArrayBuffer | Uint8Array): Promise<string> {
  const buffer = data instanceof Uint8Array
    ? data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer : data
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

/** Derive references the server requires from the selected authorized data. */
export function resolveEvent(kind: string, values: Payload, detail: SourceSceneView | null): Payload {
  const event = eventFor(kind, values) as Payload
  if (typeof event.acquired_at === 'string' && event.acquired_at) {
    event.acquired_at = new Date(event.acquired_at).toISOString()
  }
  const versions = (detail?.sources ?? []).flatMap((source) =>
    source.versions.map((version) => ({ ...version, source_id: source.source_id })))
  if (kind === 'source_share' && typeof event.version_event_id === 'string') {
    const version = versions.find((item) => item.event_id === event.version_event_id)
    if (version) event.payload_hash = version.payload_hash
  }
  if (kind === 'source_version' || kind === 'source_correct') {
    event.fingerprint = String(values.fingerprint ?? '')
  }
  return event
}

export function SourceScenes({ session, prepare, onError }: {
  session: SourceSession
  prepare: (body: Payload) => Promise<void>
  onError: (error: unknown) => void
}) {
  const [scenes, setScenes] = useState<SourceSceneSummary[]>([])
  const [nextAfter, setNextAfter] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<SourceSceneView | null>(null)
  const [stale, setStale] = useState(false)
  const [operation, setOperation] = useState<string | null>(null)
  const [values, setValues] = useState<Payload>({})
  const [busy, setBusy] = useState(false)
  const [contextId, setContextId] = useState('')
  const [context, setContext] = useState<Payload | null>(null)
  const [failure, setFailure] = useState('')
  const [listFailed, setListFailed] = useState(false)
  const mounted = useRef(true)
  const sceneEpoch = useRef(0)
  const detailAbort = useRef<AbortController | null>(null)
  const contextEpoch = useRef(0)
  const contextAbort = useRef<AbortController | null>(null)
  const contextIdRef = useRef('')
  const selectedRef = useRef<string | null>(null)

  const loadList = useCallback(async () => {
    try {
      const response = await fetch(`${BASE}/governance/sources`, { credentials: 'same-origin',
        cache: 'no-store', headers: { Accept: 'application/json' } })
      if (!response.ok) throw new ApiError(response.status, 'UNAVAILABLE', '')
      const page = await response.json() as { items: SourceSceneSummary[]; next_after: string | null }
      if (mounted.current) { setScenes(page.items); setNextAfter(page.next_after); setFailure(''); setListFailed(false) }
    } catch (error) { if (mounted.current) { setFailure(err(error)); setListFailed(true); onError(error) } }
  }, [onError])

  const loadDetail = useCallback(async (sceneId: string) => {
    // Per-scene generation + abort: a late response for a previous scene can
    // never replace the newly selected scene's data.
    const generation = ++sceneEpoch.current
    detailAbort.current?.abort()
    const controller = new AbortController()
    detailAbort.current = controller
    try {
      const response = await fetch(`${BASE}/governance/sources/${encodeURIComponent(sceneId)}`,
        { credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
          headers: { Accept: 'application/json' } })
      if (!response.ok) throw new ApiError(response.status, 'UNAVAILABLE', '')
      const value = await response.json() as SourceSceneView
      if (mounted.current && generation === sceneEpoch.current && selectedRef.current === sceneId) {
        setDetail(value); setStale(false)
      }
    } catch (error) {
      if (!mounted.current || generation !== sceneEpoch.current) return
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (selectedRef.current !== sceneId) return
      setStale(true)
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) setDetail(null)
      onError(error)
    }
  }, [onError])

  const loadContext = useCallback(async (sceneId: string, contextKey: string) => {
    if (!contextKey) return
    const generation = ++contextEpoch.current
    contextAbort.current?.abort()
    const controller = new AbortController()
    contextAbort.current = controller
    try {
      const response = await fetch(`${BASE}/governance/sources/contexts/${encodeURIComponent(contextKey)}`,
        { credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
          headers: { Accept: 'application/json' } })
      if (!response.ok) throw new ApiError(response.status, 'UNAVAILABLE', '')
      const value = await response.json() as Payload
      if (mounted.current && generation === contextEpoch.current && selectedRef.current === sceneId) {
        setContext(value)
      }
    } catch (error) {
      if (!mounted.current || generation !== contextEpoch.current) return
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (selectedRef.current !== sceneId) return
      setContext(null); onError(error)
    }
  }, [onError])

  // No contextId state dependency: the timer/refresh lifecycle stays stable
  // while the current key is read from a ref, so a new snapshot id can never
  // be aborted by an effect re-run before its read starts.
  const refreshAll = useCallback((contextHint?: string) => {
    void loadList()
    const sceneId = selectedRef.current
    if (sceneId) {
      void loadDetail(sceneId)
      const key = contextHint || contextIdRef.current
      if (key) { contextIdRef.current = key; setContextId(key); void loadContext(sceneId, key) }
    }
  }, [loadList, loadDetail, loadContext])

  useEffect(() => {
    mounted.current = true
    const onFocus = () => refreshAll()
    const onCommitted = (event: Event) => refreshAll((event as CustomEvent).detail?.context_id)
    // A newly known snapshot id must actually trigger a read even when it
    // arrives while the component keeps re-rendering.
    void loadList()
    const timer = setInterval(() => { if (!document.hidden) refreshAll() }, 5000)
    window.addEventListener('focus', onFocus)
    window.addEventListener('governance-committed', onCommitted)
    return () => {
      mounted.current = false
      clearInterval(timer)
      window.removeEventListener('focus', onFocus)
      window.removeEventListener('governance-committed', onCommitted)
      detailAbort.current?.abort()
      contextAbort.current?.abort()
    }
  }, [refreshAll, loadList])

  useEffect(() => {
    if (selectedRef.current && contextId) {
      contextIdRef.current = contextId
      void loadContext(selectedRef.current, contextId)
    }
  }, [contextId, loadContext])

  const open = (sceneId: string) => {
    selectedRef.current = sceneId
    sceneEpoch.current++
    detailAbort.current?.abort()
    setSelected(sceneId); setDetail(null); setStale(false); setOperation(null); setValues({})
    contextIdRef.current = ''; setContext(null); setContextId(''); setFailure('')
    void loadDetail(sceneId)
  }
  const localNow = () => {
    const now = new Date()
    const pad = (value: number) => String(value).padStart(2, '0')
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
  }
  const start = (kind: string) => {
    setOperation(kind)
    setValues(kind === 'scene_create'
      ? { owner_principal_id: session.identity.principal_id }
      : kind === 'source_version' || kind === 'source_correct' || kind === 'source_add'
        ? { acquired_at: localNow() } : {})
  }
  const setSimple = (name: string, value: unknown) => setValues((current) => ({ ...current, [name]: value }))
  const rowList = (name: string) => Array.isArray(values[name]) ? values[name] as Array<Payload> : []
  const setRow = (name: string, index: number, field: string, value: string) => setValues((current) => {
    const list = [...rowList(name)]
    list[index] = { ...list[index], [field]: value }
    return { ...current, [name]: list }
  })
  const patchRow = (name: string, index: number, patch: Payload) => setValues((current) => {
    const list = [...rowList(name)]
    list[index] = { ...list[index], ...patch }
    return { ...current, [name]: list }
  })
  const nameOf = (principalId: string) => detail?.principal_names?.[principalId] ?? ''
  const contextVersionOptions = (detail?.sources ?? []).flatMap((source) => source.versions
    .filter((version) => version.status === 'available')
    .map((version) => ({ value: `${source.source_id}|${version.event_id}|${version.payload_hash}`,
      sourceId: source.source_id, eventId: version.event_id, payloadHash: version.payload_hash,
      label: `${source.title} · v${version.version_seq} · ${version.event_id.slice(0, 8)}` })))
  const segmentOptions = (detail?.sources ?? []).flatMap((source) => source.versions.flatMap((version) =>
    (version.segments ?? []).map((segment, index) => ({
      value: `${source.source_id}|${version.event_id}|${version.payload_hash}|${index}`,
      label: `${source.title} · v${version.version_seq} · [${index}] ${segment.text.slice(0, 40)}`,
      quote: segment.text,
    }))))
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!operation || busy) return
    setBusy(true)
    try {
      const key = crypto.randomUUID()
      const body = operation === 'context_create'
        ? contextEnvelope(selected as string, String(values.purpose ?? ''),
            rowList('items').map((row) => ({ source_id: String(row.source_id ?? ''),
              version_event_id: String(row.version_event_id ?? ''), payload_hash: String(row.payload_hash ?? '') })),
            key)
        : sceneEnvelope(operation === 'scene_create' ? crypto.randomUUID() : (selected as string),
            operation === 'scene_create' ? 0 : (detail?.version ?? 0), resolveEvent(operation, values, detail), key)
      await prepare(body)
      setOperation(null); setValues({})
      void loadList()
      if (selected) { void loadDetail(selected); void loadContext(selected, contextId) }
    } catch (error) { onError(error) } finally { setBusy(false) }
  }
  const optionsFor = (field: SField): Array<{ value: string; label: string }> => {
    const sources = detail?.sources ?? []
    const versions = sources.flatMap((source) => source.versions.map((version) => ({ source, version })))
    switch (field.name) {
      case 'source_id': return sources.map((source) => ({ value: source.source_id,
        label: `${source.title}（${source.status}）` }))
      case 'version_event_id': return versions
        .filter(({ source }) => !values.source_id || source.source_id === values.source_id)
        .map(({ source, version }) => ({ value: version.event_id,
          label: `${source.title} · v${version.version_seq} · ${version.event_id.slice(0, 8)} · ${version.status === 'available' ? '可读' : '不可读'}` }))
      case 'corrects_event_id': return versions
        .filter(({ source }) => !values.source_id || source.source_id === values.source_id)
        .map(({ source, version }) => ({ value: version.event_id,
          label: `${source.title} · v${version.version_seq} · ${version.status === 'available' ? '可读' : '不可读'}` }))
      case 'share_event_id': return sources.flatMap((source) => (source.versions ?? [])
        .flatMap((version) => (version.shares ?? []).map((share) => ({ value: share.share_event_id,
          label: `${source.title} v${version.version_seq} → ${share.share_to_principal_id.slice(0, 8)}${share.active ? '' : '（已失效）'}` }))))
      case 'share_to_principal_id': return (detail?.scene.participant_principal_ids ?? [])
        .map((id) => ({ value: id, label: nameOf(id) ? `${nameOf(id)}（${id.slice(0, 8)}）` : `未记录姓名 · ${id.slice(0, 8)}` }))
      case 'draft_event_id': return (detail?.drafts ?? []).map((draft) => ({ value: draft.event_id,
        label: `${draft.title ?? draft.event_id.slice(0, 8)}（${draft.status}）` }))
      case 'run_event_id': return (detail?.runs ?? []).map((run) => ({ value: run.event_id,
        label: `${run.event_id.slice(0, 8)} · ${run.status}${run.withheld ? ' · 元数据' : ''}` }))
      case 'evidence_object_id':
      case 'evidence_revision_id':
      case 'evidence_payload_hash':
        return []
      default: return []
    }
  }

  return (
    <div className="space-y-5" data-testid="source-scenes">
      <div className="flex items-center justify-between">
        <div>
          <p className="gov-eyebrow">独立来源协作 / WORKSPACE 0.2</p>
          <h2 className="text-xl font-semibold">我的独立来源场景</h2>
          <p className="text-sm text-muted-foreground">
            无业务锚点的会议／文档／本人选定的对话；不产生正式经营对象或效力。
            本机授权不等于外部系统 ACL 同步。
          </p>
        </div>
        <Button variant="outline" onClick={() => refreshAll()}>
          <RefreshCw size={15} />刷新</Button>
      </div>
      {failure && <Alert><AlertDescription>{failure}</AlertDescription></Alert>}
      {!selected && <div className="space-y-3">
        <Button onClick={() => start('scene_create')} data-testid="source-create-open">建立独立来源场景</Button>
        {scenes.length === 0 && !listFailed && <p className="text-sm text-muted-foreground" data-testid="source-empty">
          当前身份还没有可见的独立来源场景；空列表不是“不可读”。</p>}
        {scenes.length === 0 && listFailed && <p className="text-sm text-muted-foreground" data-testid="source-list-failed">
          来源场景列表读取失败；这不是“没有场景”，请刷新或重新登录后再判断。</p>}
        {scenes.map((scene) => <button key={scene.scene_id} type="button" data-testid={`source-scene-${scene.scene_id}`}
          className="block w-full rounded-md border border-border bg-card px-3 py-2 text-left hover:bg-muted/40"
          onClick={() => open(scene.scene_id)}>
          <span className="text-sm font-medium">{scene.title}</span>
          <span className="ml-2 text-[11px] text-muted-foreground">{scene.scene_type} · v{scene.version}</span>
        </button>)}
        {nextAfter && <Button variant="outline" onClick={async () => {
          const response = await fetch(`${BASE}/governance/sources?after=${nextAfter}`, { credentials: 'same-origin', cache: 'no-store' })
          if (response.ok) { const page = await response.json() as { items: SourceSceneSummary[]; next_after: string | null }
            setScenes((current) => [...current, ...page.items]); setNextAfter(page.next_after) } }}>加载更多</Button>}
      </div>}
      {selected && <div className="space-y-4">
        <Button variant="ghost" onClick={() => { setSelected(null); setDetail(null); setOperation(null) }}>← 返回场景列表</Button>
        {stale && <Alert><AlertDescription>场景数据暂未刷新，提交已暂停。</AlertDescription></Alert>}
        {detail && <>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold">{detail.scene.title}</h3>
            <Badge variant="secondary">{detail.scene.scene_type}</Badge>
            <Badge variant="outline">{detail.formal_effect === 'none' ? '无正式效力' : detail.formal_effect}</Badge>
            <span className="text-[11px] text-muted-foreground">v{detail.version}</span>
          </div>
          <p className="text-[11px] text-muted-foreground" data-testid="source-members">
            拥有者 {detail.scene.owner_principal_id.slice(0, 8)} · 参与人
            {(detail.scene.participant_principal_ids ?? []).map((id) => ` ${id.slice(0, 8)}`).join('') || ' 无'}
            {detail.scene.agent_bindings?.length ? ` · Agent 绑定 ${detail.scene.agent_bindings.length}` : ''}
          </p>
          <div className="flex flex-wrap gap-2">
            {['source_add', 'source_version', 'source_correct', 'source_withdraw', 'source_share',
              'source_unshare', 'followup_draft', 'draft_decision'].map((kind) => {
              const hint = (detail.operations ?? []).find((operation) => operation.kind === kind)
              const reason = hint && !hint.allowed ? String(hint.reason ?? 'not_permitted') : null
              return (
              <Button key={kind} size="sm" variant="outline" onClick={() => start(kind)}
                      disabled={stale || Boolean(reason)}
                      title={reason ? `当前角色不可用（${reason}），提交时会重新校验` : undefined}
                      data-testid={`source-action-${kind}`}>
                {kind === 'source_add' ? '登记来源' : kind === 'source_version' ? '登记新版本'
                  : kind === 'source_correct' ? '更正来源' : kind === 'source_withdraw' ? '撤回来源'
                  : kind === 'source_share' ? '分享精确版本' : kind === 'source_unshare' ? '取消分享'
                  : kind === 'followup_draft' ? '提交跟进草稿' : '本人处理草稿条目'}
              </Button>)})}
            <Button size="sm" variant="outline" disabled={stale} onClick={() => start('context_create')}
                    data-testid="source-action-context_create">生成 Context 快照</Button>
          </div>
          <section className="space-y-2" data-testid="source-list">
            <h4 className="text-sm font-semibold">来源与版本</h4>
            {detail.sources.length === 0 && <p className="text-sm text-muted-foreground">尚未登记来源。</p>}
            {detail.sources.map((source) => <details key={source.source_id} className="border-b py-2">
              <summary className="cursor-pointer text-sm">
                {source.title} <span className="text-[11px] text-muted-foreground">
                  {source.system} · {source.status === 'available' ? source.access === 'owner' ? '本人来源' : '精确分享' : '已撤回'}
                  · 可见 {source.versions.length} 个版本</span>
              </summary>
              {source.versions.map((version) => <div key={version.event_id} className="mt-2 text-[11px] text-muted-foreground">
                v{version.version_seq} · {version.status === 'available' ? '可用' : '已撤下／不可读'}
                {version.correction_of ? <span className="ml-1">更正自 {String(version.correction_of).slice(0, 8)}</span> : null}
                {(version.shares ?? []).length ? ` · 分享 ${version.shares!.length}` : ''}
                {version.status === 'available' && version.segments ? (
                  <details className="mt-1" data-testid={`source-segments-${version.event_id}`}>
                    <summary className="cursor-pointer text-[11px]">可读原文（{version.segments.length} 段）</summary>
                    <div className="mt-1 space-y-1 border-l-2 border-border pl-2">
                      {version.segments.map((segment, index) => (
                        <p key={index} className="whitespace-pre-wrap break-words">
                          <span className="text-foreground">[{index}]</span>
                          {segment.speaker ? ` ${segment.speaker}：` : ' '}
                          {segment.text}
                          {segment.occurred_at ? <span className="ml-1 opacity-70">{segment.occurred_at}</span> : null}
                        </p>))}
                    </div>
                  </details>
                ) : <span className="ml-1">正文不可读（撤回或未授权）</span>}
              </div>)}
            </details>)}
          </section>
          <section className="space-y-2" data-testid="source-runs">
            <h4 className="text-sm font-semibold">场景 Agent 运行（只读）</h4>
            {detail.runs.length === 0 && <p className="text-sm text-muted-foreground">没有运行记录。</p>}
            {detail.runs.map((run) => <p key={run.event_id} className="text-[11px] text-muted-foreground">
              {run.event_id.slice(0, 8)} · {run.status}{run.withheld ? ' · 仅元数据（输入来源当前不可读）'
                : run.purpose ? ` · ${run.purpose}` : ''}</p>)}
          </section>
          <section className="space-y-2" data-testid="source-drafts">
            <h4 className="text-sm font-semibold">跟进草稿与本人决定</h4>
            {detail.drafts.length === 0 && <p className="text-sm text-muted-foreground">没有草稿。</p>}
            {detail.drafts.map((draft) => <div key={draft.event_id} className="rounded border border-border px-3 py-2">
              <div className="text-sm font-medium">
                {draft.withheld || draft.status === 'withheld'
                  ? '草稿已撤下（引用来源当前不可读）'
                  : draft.title ?? '未命名草稿'}
                <Badge variant="outline" className="ml-2 text-[10px]">
                  {draft.status === 'withheld' ? '正文已撤下' : draft.status === 'available' ? '可用' : draft.status}
                </Badge>
              </div>
              {!draft.withheld && (draft.items ?? []).map((item, index) => <div key={index}
                className="mt-1 text-[11px] text-muted-foreground" data-testid={`draft-item-${draft.event_id}-${index}`}>
                <p>{ITEM_KIND_LABELS[String(item.item_kind)] ?? String(item.item_kind)} · {String(item.text)}</p>
                {(Array.isArray(item.citations) ? item.citations as Array<Payload> : []).map((citation, cIndex) => (
                  <blockquote key={cIndex} className="my-0.5 border-l-2 border-border pl-2 italic">
                    “{String(citation.quote ?? '')}” <span className="opacity-70">[{String(citation.segment_index ?? '')}]</span>
                  </blockquote>))}
                {item.decision ? (
                  <p className="mt-0.5 text-foreground">本人/成员决定：{String(item.decision)}
                    {item.decision_note ? `（${String(item.decision_note)}）` : ''}</p>) : null}
              </div>)}
              {detail.decisions.filter((decision) => decision.draft_event_id === draft.event_id)
                .map((decision) => <div key={decision.event_id} className="mt-1 text-[11px]">
                  本人/成员决定：条目 {decision.item_index} → {decision.decision}
                  {decision.note ? `（${decision.note}）` : ''}</div>)}
            </div>)}
          </section>
          <section className="space-y-2" data-testid="source-context">
            <h4 className="text-sm font-semibold">Context 快照</h4>
            <div className="flex gap-2">
              <Input aria-label="Context 快照编号" value={contextId}
                     onChange={(event) => setContextId(event.target.value)} />
              <Button variant="outline" onClick={() => { if (selected) { contextIdRef.current = contextId; void loadContext(selected, contextId) } }}>读取</Button>
            </div>
            {context && <div className="text-[11px] text-muted-foreground" data-testid="source-context-view">
              {(Array.isArray(context.items) ? context.items as Array<Payload> : []).map((item, index) => (
                <p key={index}>{String(item.status ?? 'available')} · {String(item.source_id ?? '').slice(0, 8)}
                  {item.status === 'withheld' ? ' · 正文已撤下' : ''}</p>))}
            </div>}
          </section>
        </>}
      </div>}
      {operation && <form className="space-y-3 border-t pt-4" onSubmit={submit}
                          data-testid={`source-form-${operation}`}>
        <h4 className="font-semibold">{OPERATION_LABELS[operation] ?? operation}</h4>
        {operation === 'context_create' ? <>
          <div><Label htmlFor="source-context-purpose">Context 用途</Label>
            <Textarea id="source-context-purpose" required value={String(values.purpose ?? '')}
                      onChange={(event) => setSimple('purpose', event.target.value)} /></div>
          <div className="space-y-2">
            <Label>精确来源版本（从当前可读版本选择，自动写入精确三元组）</Label>
            {contextVersionOptions.length === 0 && <p className="text-[11px] text-muted-foreground" data-testid="context-no-readable">
              当前场景没有你可读的来源版本；先登记来源或获得精确分享后才能生成 Context 快照。</p>}
            {rowList('items').map((row, index) => <div key={index} className="flex flex-wrap items-end gap-2">
              <select aria-label={`item-${index}-version`} disabled={contextVersionOptions.length === 0}
                      value={String(row.citation ?? '')}
                      className="min-w-[16rem] rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                      onChange={(event) => {
                        const option = contextVersionOptions.find((item) => item.value === event.target.value)
                        patchRow('items', index, { citation: event.target.value,
                          source_id: option?.sourceId ?? '', version_event_id: option?.eventId ?? '',
                          payload_hash: option?.payloadHash ?? '' })
                      }}>
                <option value="">请选择可读来源版本</option>
                {contextVersionOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
              {row.payload_hash ? <p className="text-[10.5px] text-muted-foreground">
                精确版本：{String(row.version_event_id).slice(0, 8)} · {String(row.payload_hash).slice(0, 12)}…</p> : null}
            </div>)}
            <Button type="button" size="sm" variant="outline"
                    onClick={() => setValues((current) => ({ ...current,
                      items: [...rowList('items'), {}] }))}>添加一项</Button>
          </div>
        </> : <>
          {(SCENE_FORMS[operation] ?? []).filter((f) => !f.name.startsWith('evidence_')).map((field) => (
            <div key={field.name}>
              <Label htmlFor={`source-${field.name}`}>{field.label}</Label>
              {field.type === 'fingerprint' ? (
                <div className="space-y-2" data-testid={`source-fingerprint-${field.name}`}>
                  <input type="file" aria-label="选择原始文件（可选）" className="block text-[12px]"
                         onChange={async (event) => {
                           const file = event.target.files?.[0]
                           if (!file) return
                           const hash = await sha256Hex(await file.arrayBuffer())
                           setValues((current) => ({ ...current, fingerprint: hash,
                             media_type: file.type || 'application/octet-stream', content_kind: 'file' })) }} />
                  <Textarea aria-label="粘贴文本（提交文本的指纹）" placeholder="或粘贴文本，计算所提交文本的指纹"
                            value={String(values.pasted_text ?? '')}
                            onChange={async (event) => {
                              const pasted = event.target.value
                              const hash = pasted ? await sha256Hex(new TextEncoder().encode(pasted)) : ''
                              setValues((current) => ({ ...current, pasted_text: pasted,
                                fingerprint: hash, content_kind: 'pasted' })) }} />
                  <p className="text-[11px] text-muted-foreground">
                    {values.fingerprint
                      ? `指纹：${String(values.fingerprint).slice(0, 16)}…（${values.content_kind === 'file' ? '原始文件字节 SHA-256' : '提交文本 SHA-256；不是原始外部文件哈希'}）`
                      : '尚未计算指纹：请选择原始文件或粘贴文本（不做猜测）'}
                  </p>
                </div>
              ) : field.type === 'select' ? (
                <select id={`source-${field.name}`} required={field.required}
                        value={String(values[field.name] ?? '')}
                        className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                        onChange={(event) => setSimple(field.name, event.target.value)}>
                  <option value="">请选择</option>
                  {(field.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}
                  {optionsFor(field).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              ) : field.type === 'datetime' ? (
                <Input id={`source-${field.name}`} type="datetime-local" required={field.required}
                       value={String(values[field.name] ?? '')}
                       onChange={(event) => setSimple(field.name, event.target.value)} />
              ) : field.type === 'textarea' ? (
                <Textarea id={`source-${field.name}`} required={field.required}
                          value={String(values[field.name] ?? '')}
                          onChange={(event) => setSimple(field.name, event.target.value)} />
              ) : field.type === 'rows' && operation === 'followup_draft' ? (
                <div className="space-y-2" data-testid="source-draft-items">
                  {rowList('items').map((row, index) => (
                    <div key={index} className="flex flex-wrap items-end gap-2 rounded border border-border/60 p-2">
                      <div className="min-w-[7rem]">
                        <Label className="text-[11px] text-muted-foreground">类型</Label>
                        <select aria-label={`条目类型-${index}`} value={String(row.item_kind ?? 'fact')}
                                className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                                onChange={(event) => setRow('items', index, 'item_kind', event.target.value)}>
                          {ITEM_KINDS.map((kind) => <option key={kind} value={kind}>{kind}</option>)}
                        </select>
                      </div>
                      <div className="min-w-[10rem] flex-1">
                        <Label className="text-[11px] text-muted-foreground">文本</Label>
                        <Input value={String(row.text ?? '')}
                               onChange={(event) => setRow('items', index, 'text', event.target.value)} />
                      </div>
                      <div className="min-w-[14rem] flex-[2]">
                        <Label className="text-[11px] text-muted-foreground">引用片段（选择后自动填精确来源版本）</Label>
                        <select aria-label={`引用片段-${index}`} value={String(row.citation ?? '')}
                                className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                                onChange={(event) => {
                                  const option = segmentOptions.find((item) => item.value === event.target.value)
                                  const [sourceId, versionEventId, payloadHash, segmentIndex] = event.target.value.split('|')
                                  patchRow('items', index, { citation: event.target.value,
                                    source_id: sourceId ?? '', version_event_id: versionEventId ?? '',
                                    payload_hash: payloadHash ?? '', segment_index: segmentIndex ?? '0',
                                    quote: option?.quote ?? '' }) }}>
                          <option value="">请选择可读片段</option>
                          {segmentOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select>
                        {row.quote ? <p className="mt-0.5 text-[10.5px] text-muted-foreground">已引用：“{String(row.quote).slice(0, 30)}…”</p>
                          : segmentOptions.length === 0 ? <p className="mt-0.5 text-[10.5px] text-muted-foreground">
                            当前没有可读片段；引用来源不可读时不生成草稿条目。</p> : null}
                      </div>
                    </div>
                  ))}
                  <Button type="button" size="sm" variant="outline"
                          onClick={() => setValues((current) => ({ ...current, items: [...rowList('items'), {}] }))}>
                    添加条目</Button>
                </div>
              ) : field.type === 'rows' ? (
                <div className="space-y-2">
                  {rowList(field.name).map((row, index) => <div key={index} className="flex flex-wrap items-end gap-2">
                    {(field.rowFields ?? []).map((column) => <div key={column.name} className="min-w-[9rem] flex-1">
                      <Label className="text-[11px] text-muted-foreground">{column.label}</Label>
                      <Input value={String(row[column.name] ?? '')}
                             onChange={(event) => setRow(field.name, index, column.name, event.target.value)} />
                    </div>)}
                  </div>)}
                  <Button type="button" size="sm" variant="outline"
                          onClick={() => setValues((current) => ({ ...current, [field.name]: [...rowList(field.name), {}] }))}>
                    添加一项</Button>
                </div>
              ) : (
                <Input id={`source-${field.name}`} required={field.required}
                       type={field.type === 'number' ? 'number' : 'text'}
                       value={String(values[field.name] ?? '')}
                       onChange={(event) => setSimple(field.name, event.target.value)} />
              )}
            </div>
          ))}
          {(SCENE_FORMS[operation] ?? []).some((f) => f.name.startsWith('evidence_')) ? (
            <details data-testid="source-evidence-trace">
              <summary className="cursor-pointer text-sm font-medium">
                技术引用（仅限原本已按域协议共享的 EvidenceAsset；默认私有来源不要填写）
              </summary>
              <div className="mt-2 space-y-2">
                {(SCENE_FORMS[operation] ?? []).filter((f) => f.name.startsWith('evidence_')).map((field) => (
                  <div key={field.name}>
                    <Label htmlFor={`source-${field.name}`}>{field.label}</Label>
                    <Input id={`source-${field.name}`} value={String(values[field.name] ?? '')}
                           onChange={(event) => setSimple(field.name, event.target.value)} />
                  </div>
                ))}
              </div>
            </details>
          ) : null}
        </>}
        <p className="text-xs text-muted-foreground">
          预览后才会提交；更正保留历史版本与既有分享，撤回或取消授权后相应原文及派生内容按当前权限隐藏。
          {operation === 'followup_draft' ? ' 草稿条目必须引用确切片段，已接受只能由本人处理产生。' : ''}
        </p>
        <div className="flex gap-2">
          <Button type="submit" disabled={busy || stale}>准备并预览</Button>
          <Button type="button" variant="ghost" onClick={() => setOperation(null)}>取消</Button>
        </div>
        {operation === 'followup_draft' && <p className="text-[11px] text-muted-foreground">
          当前身份：{session.identity.display_name}（本人提交，Agent 不代替确认）</p>}
      </form>}
    </div>
  )
}
