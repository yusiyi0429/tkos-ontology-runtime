import { ArrowUpRight, BookOpen, CheckCheck, CircleCheck, ClipboardList, FileText, Fingerprint, GitBranch, GitPullRequest, Inbox, LogOut, Network, RefreshCw, ShieldCheck, Waypoints } from 'lucide-react'
import './governance.css'
import { createContext, useContext, useCallback, useEffect, useRef, useState } from 'react'
import { App } from '@/App'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { formatPeriod, formatTime } from '@/lib/format'
import { ApiError } from '@/lib/errors'
import { MethodActions } from '@/MethodActions'
import { SourceScenes } from '@/SourceScenes'

// This boundary consumes versioned Runtime projections; no browser credential or sample data.
type Ref = { object_id: string; revision_id: string; payload_hash: string }
type Identity = { scope_id: string; principal_id: string; display_name: string; auth_epoch: number; assignments: Array<{ assignment_id: string; domain_id: string; role: string }> }
type Session = { identity: Identity; csrf: string }
type Payload = Record<string, unknown>
type Revision = { revision_id: string; payload_hash: string; payload: Payload }
type Responsibility = { principal?: { principal_id: string; display_name: string } }
type Obj = { responsibilities?: Responsibility[]; object_id: string; domain_id: string; object_version: number; latest_revision: Revision; effective_revision?: Revision; method_state: { phase: string }; protocol: { contract_version: string }; handoff?: Payload }
type Operation = { action_type: string; label: string; allowed: boolean; reason: string | null; target: Ref & { expected_version: number }; formal_effect: string }
type Review = { record_id: string; kind: string; principal_id: string; effective_opinion: boolean; target_object_id: string; target_revision_id: string; content?: Payload; payload?: Payload }
type Scene = { scene_id: string; version: number; definition: { title: string }; monthly: { reviewed_current_candidate: boolean }; events: Payload[] }
type WindowData = { identity: Identity; object: Obj; monthly: { window: Obj; business_period: { start: string; end: string }; feedback_deadline: { value?: string }; targets: Array<{ ref: Ref; revision: Revision; responsibilities?: Responsibility[] }>; candidate_targets: Array<{ ref: Ref; revision: Revision; responsibilities?: Responsibility[] }>; candidate: { status: string; ref?: Ref; revision?: Revision }; differences: Array<{ before_ref: Ref; after_ref: Ref; fields: Array<{ field_path: string; before: unknown; after: unknown }> }>; reviews: Review[]; my_reviews: Review[]; visible_effective_opinion_count: number; members: Array<{ principal_id: string; assignment_id: string }>; member_details: Array<{ display_name?: string; current?: boolean; role?: string }> }; scenes: Scene[]; actions: Operation[]; can_create_scene: boolean; recovery: Payload }
type Command = { preview?: { title: string; members: Array<{ title: string; ref: Ref; payload: Payload; responsibilities?: Responsibility[] }> }; command_id: string; status: string; kind: string; envelope: Payload | null; receipt?: Payload | null; receipt_status?: string; payload_withheld?: string; error?: string }
type Task = { object_id: string; title: string; phase: string; label: string; contract_version: string }
type Basis = { title: string; object_type: string; ref: Ref; strategy_ref?: Ref }
const BASE = '/dashboard/api/v1'
const STATUS: Record<string, string> = { prepared: '等待本人提交', unknown: '结果不明，请核对并恢复', committed: '已生效', rejected: '已拒绝，需重新判断', open: '评论开放', closed: '等待 Co-agent 收拢', resolved: '候选待确认', confirmed: '已确认' }
const REASONS: Record<string, string> = { current_role_not_permitted: '当前任职无权操作', window_state_not_permitted: '当前窗口阶段不允许', review_deadline_passed: '评论期限已过', read_only_contract_version: '此历史规则版本仅供读取', VERSION_CONFLICT: '版本已变化，请刷新后重新判断', STALE_DEPENDENCY: '依据已变化，请核对最新版本', FORBIDDEN: '当前权限不允许', UNAUTHENTICATED: '会话已失效，请重新登录', DEPENDENCY_MISSING: '缺少必要依据', INVALID_REQUEST: '请检查必填内容和版本引用', RESULT_UNKNOWN: '结果尚未确定，请到我的提交恢复', IDEMPOTENCY_CONFLICT: '该提交编号已用于其他内容', LOGIN_RATE_LIMITED: '登录尝试过多，请稍后重试' }
const ACTION: Record<string, string> = { m1b_comment: '发表或替代意见', m1b_withdraw_comment: '撤回本人意见', m1b_confirm_candidates: '确认整个候选集合', m1b_reopen_window: '重开评论窗口', m1b_reopen_candidates: '退回并重开窗口', create: '建立月度核对场景', comment_anchor: '补充评论字段定位', diff_response: '记录本人核对完成', m1a_set_participants: '指定必要参与人', m1a_confirm_agreement: '确认 Agreement（本人）', m1a_confirm_update: '最终确认正式更新', m1b_confirm_ltco: '确认 LTCO 正式版本', m1b_replace_comment: '替代本人意见', m1b_commit_candidate: '提交本人责任承诺', m1b_activate_candidates: '整组激活候选集合', method_confirm_state: '确认正式经营状态', method_open_problem: '登记经营问题', method_revise_problem: '修订经营问题', method_close_problem: '关闭经营问题', scene_create: '建立独立来源场景', source_add: '登记来源', source_version: '登记来源新版本', source_correct: '更正来源', source_withdraw: '撤回来源', source_share: '分享来源片段', source_unshare: '取消分享', followup_draft: '提交跟进草稿', draft_decision: '本人接受或处理草稿条目' }
const FIELD: Record<string, string> = { title: '名称', deliverable: '交付要求', acceptance_criteria: '验收标准', boundary: '边界', hard_deadline: '交付期限', supports: '成果支持关系', owner_principal_id: '责任人', dri_principal_id: '成果 DRI', participants: '参与人', contribution: '支撑贡献', disposition: '取舍', rationale: '取舍理由', adjustment: '实际修改', unit_outcomes: '周期成果', result_statement: '预期结果', criteria: '判断标准', outcomes: '成果', content: '意见', reason: '理由', dispositions: '意见取舍', remaining_differences: '未决差异', why: '为什么（Why）', requirements: '要求',
  period: '周期', start: '开始', end: '结束', primary_scope_id: '主 Scope',
  parent_pco_ref: '父级 PCO（精确引用）', parent_ltco_ref: '父级 LTCO（精确引用）',
  evidence_refs: '依据（精确引用）', architecture_ref: '责任结构依据（精确引用）',
  strategy_ref: '战略依据（精确引用）', scope: 'Scope', horizon: '期限',
  expected_lt_advance: '预期长期推进', current_reality: '当前现实' }
function err(error: unknown) { return error instanceof ApiError ? REASONS[error.code] ?? `操作未完成（${error.code}）` : '服务暂时不可用，请重试；已发送的提交请先核对结果。' }
function text(value: unknown): string { if (value == null) return '未记录'; if (typeof value === 'string') return value; if (Array.isArray(value)) return value.map(text).join('；'); if (typeof value === 'object') { const v = value as Payload; return String(v.content ?? v.reason ?? '结构化记录（详见来源）') }; return String(value) }
function Picker({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: Array<{ value: string; label: string }>; label: string }) { return <Select value={value} onValueChange={onChange}><SelectTrigger aria-label={label} className="w-full"><SelectValue placeholder={label} /></SelectTrigger><SelectContent>{options.map(o => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent></Select> }
const Names = createContext<Record<string, string>>({})
function personNames(entries?: Responsibility[]) { return Object.fromEntries((entries ?? []).filter(e => e.principal).map(e => [e.principal!.principal_id, e.principal!.display_name])) }
function BusinessValue({ value, names }: { value: unknown; names: Record<string,string> }) {
  if (value == null) return <span>未记录</span>
  if (Array.isArray(value)) return <ul className="list-disc space-y-2 pl-5">{value.map((v,i) => <li key={i}><BusinessValue value={v} names={names}/></li>)}</ul>
  if (typeof value === 'object') {
    const v = value as Payload
    if (typeof v.object_id === 'string' && typeof v.revision_id === 'string') {
      return <span data-testid="exact-ref">精确引用 {v.object_id.slice(0, 8)}… · 版本 {v.revision_id.slice(0, 8)}…</span>
    }
    if (v.start !== undefined && v.end !== undefined) {
      return <span data-testid="period-range">{formatPeriod({ start: String(v.start), end: String(v.end) })}</span>
    }
    const ref = v.outcome_ref as Payload | undefined
    return <div className="space-y-1">{ref && <p>支撑成果：{names[String(ref.outcome_id)] ?? String(ref.outcome_id)}</p>}{Object.entries(v).filter(([k]) => k !== 'outcome_ref' && FIELD[k]).map(([k,x]) => <div key={k}><span className="text-muted-foreground">{FIELD[k]}：</span><BusinessValue value={x} names={names}/></div>)}</div>
  }
  const str = String(value)
  if (names[str]) return <span>{names[str]}</span>
  if (/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(str)) return <span>引用已记录（详见技术溯源）</span>
  return <span>{str}</span>
}
function Facts({ payload, responsibilities }: { payload: Payload; responsibilities?: Responsibility[] }) {
  const inherited = useContext(Names); const names = {...inherited, ...personNames(responsibilities)}
  return <dl className="grid gap-3 text-sm">{Object.entries(payload).filter(([k]) => FIELD[k]).map(([k,v]) => <div key={k}><dt className="font-medium text-muted-foreground">{FIELD[k]}</dt><dd className="whitespace-pre-wrap break-words">{k === 'hard_deadline' ? formatTime(String(v)) : <BusinessValue value={v} names={names}/>}</dd></div>)}</dl>
}

const NAV = [
  { id: 'tasks', label: '我的待办', icon: Inbox, description: '共同核对与待确认事项' },
  { id: 'method', label: '方法事项', icon: GitPullRequest, description: 'Agreement、责任承诺与正式状态确认' },
  { id: 'sources', label: '独立来源', icon: FileText, description: '无锚点会议／文档来源、分享与跟进草稿' },
  { id: 'missions', label: '正式 Mission', icon: CheckCheck, description: '已确认的责任与交付要求' },
  { id: 'map', label: '本体地图', icon: Waypoints, description: '理解业务对象与规则' },
  { id: 'definitions', label: '业务定义', icon: BookOpen, description: '44 项业务概念、目的与实现支持' },
  { id: 'graph', label: '业务关系图', icon: Network, description: '查看目标、责任与依据的关联' },
  { id: 'submissions', label: '我的提交', icon: ClipboardList, description: '核对回执与恢复未明结果' },
]
function EmptyState({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="gov-empty"><CircleCheck size={32} strokeWidth={1.4}/><h3>{title}</h3><p>{children}</p></div>
}
function ReviewPath({ phase }: { phase?: string }) {
  const current = phase === 'open' ? 0 : phase === 'closed' ? 1 : phase === 'resolved' ? 2 : -1
  return <ol className="gov-review-path" aria-label="共同核对流程">{['DRI 共同核对', 'Co-agent 收拢', 'CEO 整组确认', '正式 Mission'].map((label, i) => <li key={label} aria-current={i === current ? 'step' : undefined}><span>{i + 1}</span>{label}</li>)}</ol>
}

export async function governanceFetch<T>(path: string, method = 'GET', body?: unknown, csrf?: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(BASE + path, { method, credentials: 'same-origin', cache: 'no-store', signal,
    headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}), ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}) })
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new ApiError(response.status, data.error?.code ?? 'UNAVAILABLE', '') }
  return response.json() as Promise<T>
}

export function GovernanceApp() {
  const [mode, setMode] = useState<'loading' | 'read' | 'login' | 'ready' | 'error'>('loading')
  const [session, setSession] = useState<Session | null>(null)
  const [failure, setFailure] = useState('')
  const [username, setUsername] = useState(''); const [code, setCode] = useState(''); const [busy, setBusy] = useState(false)
  const alive = useRef(true); const modeRef = useRef(mode); modeRef.current = mode
  // Identity intent epoch: login/logout/auth-lost invalidate any older
  // session poll so a late response cannot restore a previous identity.
  const authEpoch = useRef(0); const checkAbort = useRef<AbortController | null>(null)
  const invalidateAuth = useCallback(() => { authEpoch.current += 1; checkAbort.current?.abort() }, [])
  useEffect(() => { if (mode !== 'read') document.title = 'Runtime 治理工作台' }, [mode])
  const check = useCallback(async () => {
    const epoch = ++authEpoch.current
    checkAbort.current?.abort()
    const controller = new AbortController()
    checkAbort.current = controller
    try {
      const s = await governanceFetch<Session>('/session', 'GET', undefined, undefined, controller.signal)
      if (!alive.current || epoch !== authEpoch.current) return
      setSession(s); setMode('ready'); setFailure('')
    }
    catch (e) {
      if (!alive.current || epoch !== authEpoch.current) return
      if (e instanceof DOMException && e.name === 'AbortError') return
      if (e instanceof ApiError && e.status === 404) setMode('read')
      else if (e instanceof ApiError && [401, 403].includes(e.status)) { setSession(null); setMode('login') }
      else { setSession(null); setFailure(err(e)); setMode('error') }
    }
  }, [])
  // Stable for the whole identity: a session poll that only refreshes the
  // profile object must not rebuild these callbacks and starve child timers.
  const sessionRef = useRef<Session | null>(null)
  sessionRef.current = session
  const onAuthLost = useCallback(() => { invalidateAuth(); setSession(null); setMode('login') }, [invalidateAuth])
  const onLogout = useCallback(async () => {
    invalidateAuth()
    await governanceFetch('/session', 'DELETE', undefined, sessionRef.current?.csrf).catch(() => undefined)
    setSession(null); setMode('login')
  }, [invalidateAuth])

  useEffect(() => { alive.current = true; void check(); const refreshSession = () => { if (modeRef.current === 'ready' && !document.hidden) void check() }; const id = window.setInterval(refreshSession, 5000); window.addEventListener('focus', refreshSession); return () => { alive.current = false; clearInterval(id); window.removeEventListener('focus', refreshSession) } }, [check])
  if (mode === 'read') return <App />
  if (mode === 'ready' && session) return <GovernanceShell key={`${session.identity.scope_id}/${session.identity.principal_id}/${session.identity.auth_epoch}`} session={session} onAuthLost={onAuthLost} onLogout={onLogout} />
  return <main className="gov-login"><section className="gov-login-story"><div className="gov-wordmark"><GitBranch size={24}/> TKOS <span>RUNTIME</span></div><div><p className="gov-eyebrow">业务治理 · 有据可循</p><h2>让每一次确认，<br/>都有清晰的依据。</h2><p>从共同核对到正式 Mission，连接业务对象、责任人和每一次有据可查的决定。</p><ReviewPath /></div><p className="gov-login-foot">对象 · 关系 · 规则 · 正式效力</p></section><section className="gov-login-form"><div className="gov-login-mark"><ShieldCheck size={28}/></div><p className="gov-eyebrow">个人工作空间</p><h1>Runtime 治理工作台</h1><p className="gov-login-intro">使用个人身份登录，查看与你相关的事项。</p>
    {failure && <Alert><AlertDescription>{failure}</AlertDescription></Alert>}
    {mode === 'loading' ? <p>正在核验会话…</p> : mode === 'error' ? <Button onClick={check}>重新连接</Button> : <form className="space-y-4" onSubmit={async e => { e.preventDefault(); setBusy(true); setFailure(''); try { invalidateAuth(); const s = await governanceFetch<Session>('/session', 'POST', { username, code }); setCode(''); setSession(s); setMode('ready') } catch (e) { setFailure(err(e)); setCode('') } finally { setBusy(false) } }}>
      <div><Label htmlFor="username">用户名</Label><Input id="username" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></div>
      <div><Label htmlFor="code">个人登录码</Label><Input id="code" type="password" autoComplete="current-password" value={code} onChange={e => setCode(e.target.value)} required /></div><Button type="submit" disabled={busy}>{busy ? '正在核验…' : '登录'}</Button></form>}
  <p className="gov-login-note"><Fingerprint size={16}/>本机隔离测试 · 操作以本人身份记录</p></section></main>
}

function GovernanceShell({ session, onAuthLost, onLogout }: { session: Session; onAuthLost: () => void; onLogout: () => Promise<void> }) {
  const EXPLORER = ['map', 'definitions', 'graph']
  // A legacy ?view=list link still selects the explorer; the embedded App then
  // folds it into the ontology map (or object details) and rewrites the URL.
  const [tab, setTab] = useState(() => {
    const params = new URLSearchParams(window.location.search)
    const view = params.get('view')
    if (view === 'list') return params.get('object') ? 'graph' : 'map'
    return view && EXPLORER.includes(view) ? view : 'tasks'
  }); const [windowId, setWindowId] = useState<string | null>(null)
  const [tasks, setTasks] = useState<Task[]>([]); const [after, setAfter] = useState<string | null>(null)
  const [commands, setCommands] = useState<Command[]>([]); const [missions, setMissions] = useState<Obj[]>([])
  const [missionAfter, setMissionAfter] = useState<string | null>(null)
  const [failure, setFailure] = useState(''); const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState<Command | null>(null); const [busy, setBusy] = useState(false)
  const epoch = useRef(0); const intentEpoch = useRef(0); const controller = useRef<AbortController | null>(null); const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; epoch.current++; controller.current?.abort() } }, [])
  const handleError = useCallback((e: unknown) => { if (!mounted.current || (e instanceof DOMException && e.name === 'AbortError')) return; if (e instanceof ApiError && [401, 403].includes(e.status)) onAuthLost(); else setFailure(err(e)) }, [onAuthLost])
  const refresh = useCallback(async () => {
    const generation = ++epoch.current; controller.current?.abort(); const c = new AbortController(); controller.current = c
    try {
      if (tab === 'tasks') { const page = await governanceFetch<{ items: Task[]; next_after: string | null }>('/governance/tasks', 'GET', undefined, undefined, c.signal); if (generation === epoch.current && mounted.current) { setTasks(page.items); setAfter(page.next_after) } }
      if (tab === 'submissions') { const page = await governanceFetch<{ items: Command[] }>('/commands', 'GET', undefined, undefined, c.signal); if (generation === epoch.current && mounted.current) setCommands(page.items) }
      if (tab === 'missions') { const page = await governanceFetch<{ items: Obj[]; next_after: string | null }>('/governance/missions', 'GET', undefined, undefined, c.signal); if (generation === epoch.current && mounted.current) { setMissions(page.items); setMissionAfter(page.next_after) } }
      // Only a tab that actually performed an authorized read may clear a
      // business submission error; method/source panes keep it until success
      // or an explicit user retry/page change.
      if (generation === epoch.current && mounted.current) {
        if (tab === 'tasks' || tab === 'submissions' || tab === 'missions') setFailure('')
        setLoading(false)
      }
    } catch (e) { if (generation === epoch.current) { handleError(e); setLoading(false) } }
  }, [tab, handleError])
  useEffect(() => { setLoading(true); void refresh(); const timer = setInterval(() => { if (!document.hidden) void refresh() }, 5000); window.addEventListener('focus', refresh); return () => { clearInterval(timer); window.removeEventListener('focus', refresh); epoch.current++; controller.current?.abort() } }, [refresh])
  const prepare = async (body: Payload) => { if (busy) return; const intent = intentEpoch.current; setBusy(true); try { const data = await governanceFetch<Command>('/commands/prepare', 'POST', body, session.csrf); if (mounted.current && intent === intentEpoch.current) { setPreview(data); setFailure('') } } catch (e) { handleError(e); throw e } finally { if (mounted.current) setBusy(false) } }
  const submit = async (command: Command, retry = false) => { if (busy) return; const intent = intentEpoch.current; setBusy(true); try { const data = await governanceFetch<Command>(`/commands/${command.command_id}/${retry ? 'retry' : 'commit'}`, 'POST', undefined, session.csrf); if (mounted.current && intent === intentEpoch.current) { setPreview(data); if (data.status === 'committed') { setFailure(''); window.dispatchEvent(new CustomEvent('governance-committed',
            { detail: { context_id: (data.receipt as Payload | undefined)?.context_id } })); void refresh() } } } catch (e) { handleError(e) } finally { if (mounted.current) setBusy(false) } }
  useEffect(() => {
    if (!preview || preview.status !== 'committed') return
    let active = true
    const controller = new AbortController()
    const commandId = preview.command_id
    const refreshCommitted = async () => {
      try {
        const fresh = await governanceFetch<Command>(`/commands/${commandId}`, 'GET', undefined,
                                                     undefined, controller.signal)
        if (active && mounted.current) setPreview(fresh)
      } catch (e) { if (active) handleError(e) }
    }
    void refreshCommitted()
    const timer = setInterval(() => { if (!document.hidden) void refreshCommitted() }, 5000)
    window.addEventListener('focus', refreshCommitted)
    return () => { active = false; controller.abort(); clearInterval(timer)
      window.removeEventListener('focus', refreshCommitted) }
  }, [preview?.command_id, preview?.status, handleError])

  const switchTab = (value: string) => { const legacy = value === 'list'; const object = new URLSearchParams(location.search).get('object'); const next = legacy ? (object ? 'graph' : 'map') : value; intentEpoch.current++; setWindowId(null); setPreview(null); setTab(next); const url = new URL(location.href); if (EXPLORER.includes(next)) url.searchParams.set('view', next); else url.searchParams.delete('view'); history.replaceState(null, '', url); window.dispatchEvent(new PopStateEvent('popstate')) }
  const activeNav = NAV.find(n => n.id === tab) ?? NAV[0]
  return <div className="gov-shell"><aside className="gov-sidebar"><div className="gov-wordmark"><GitBranch size={26}/><span>TKOS <small>RUNTIME</small></span></div><p className="gov-sidebar-caption">治理工作台</p><nav aria-label="工作台导航">{NAV.map(({id, label, icon: Icon}, i) => <div key={id}>{i === 0 && <p className="gov-nav-group">我的工作</p>}{i === 2 && <p className="gov-nav-group">业务与本体</p>}{i === 5 && <p className="gov-nav-group">操作追溯</p>}<button aria-current={tab === id ? 'page' : undefined} className="gov-nav-item" onClick={() => switchTab(id)}><Icon size={18}/><span>{label}</span>{tab === id && <span className="gov-nav-dot"/>}</button></div>)}</nav><div className="gov-sidebar-foot"><ShieldCheck size={17}/><span>本人权限 · 正式回执</span></div></aside><div className="gov-body"><header className="gov-topbar"><div><span className="gov-breadcrumb">工作空间 / </span><span>{activeNav.label}</span></div><div className="gov-user"><span className="gov-environment">本机隔离测试</span><span className="gov-avatar">{session.identity.display_name.slice(0,1)}</span><div><strong>{session.identity.display_name}</strong><small>{[...new Set(session.identity.assignments.map(a => ({CEO:'CEO', DOMAIN_DRI:'域负责人', MISSION_DRI:'Mission 负责人'}[a.role] ?? a.role)))].join(' / ')}</small></div><Button variant="ghost" size="icon" aria-label="退出登录" onClick={() => void onLogout().catch(handleError)}><LogOut size={17}/></Button></div></header>
    {failure && <Alert className="mx-auto my-4 max-w-5xl"><AlertDescription>{failure} <Button variant="link" onClick={refresh}>刷新核对</Button></AlertDescription></Alert>}
    {EXPLORER.includes(tab) ? <div className="gov-explorer"><App embedded /></div> : <main className="gov-main">
      {tab === 'sources' ? <SourceScenes session={session} prepare={prepare} onError={handleError} /> : tab === 'method' ? <MethodActions session={session} prepare={prepare} onError={handleError} onExplore={(objectId) => { const url = new URL(location.href); url.searchParams.set('object', objectId); history.replaceState(null, '', url); switchTab('graph') }} /> : windowId && tab === 'tasks' ? <WindowPane key={windowId} id={windowId} session={session} prepare={prepare} busy={busy || !!failure} onError={handleError} onBack={() => { intentEpoch.current++; setPreview(null); setWindowId(null) }} /> : <>
        <div className="gov-page-heading"><div><p className="gov-eyebrow">{tab === 'tasks' ? '共同核对 / M1B' : tab === 'missions' ? '正式成果 / MISSION' : '操作追溯 / RECEIPTS'}</p><h2>{tab === 'tasks' ? '需要我处理的事项' : tab === 'missions' ? '正式 Mission · 待执行承接' : '提交记录与恢复'}</h2><p className="gov-page-description">{activeNav.description}。所有内容按你当前的权限展示。</p></div><Button variant="outline" onClick={refresh}><RefreshCw size={15}/>刷新</Button></div>{tab === 'tasks' && <div className="gov-flow-panel"><div><span className="gov-section-label">从核对到生效</span><p>每一步保留责任与依据</p></div><ReviewPath /></div>}
        {loading && <p>正在读取…</p>}
        {tab === 'tasks' && !loading && !failure && <>{tasks.length === 0 && <EmptyState title="当前没有待处理事项">新的共同核对或候选审阅事项出现后，会显示在这里。已生效的任务可在“正式 Mission”中查看。</EmptyState>}{tasks.map(t => <section key={t.object_id} className="gov-task-card"><div className="gov-task-symbol"><Inbox size={22}/></div><div className="gov-task-copy"><div className="gov-card-kicker"><span>{STATUS[t.phase]}</span><small>{t.contract_version}</small></div><h3>{t.title}</h3><p>{t.label}</p></div><Button variant="outline" onClick={() => { intentEpoch.current++; setPreview(null); setWindowId(t.object_id) }}>查看并办理<ArrowUpRight size={15}/></Button></section>)}{after && <Button variant="outline" onClick={async () => { const e = epoch.current; try { const page = await governanceFetch<{ items: Task[]; next_after: string | null }>(`/governance/tasks?after=${after}`); if (mounted.current && e === epoch.current) { setTasks(v => [...v, ...page.items]); setAfter(page.next_after) } } catch (e) { handleError(e) } }}>加载更多</Button>}</>}
        {tab === 'submissions' && <>{!commands.length && !loading && !failure && <EmptyState title="还没有提交记录">办理事项后，可以在这里查看正式回执或恢复结果不明的提交。</EmptyState>}{commands.map(c => <section key={c.command_id} className="gov-submission-card"><div className="flex items-center justify-between"><p>{(() => { const envelope = (c.envelope as Payload | null) ?? {}; return ACTION[String(envelope.action_type ?? (envelope.event as Payload)?.kind ?? '')] ?? '提交' })()} · {STATUS[c.status]}{c.payload_withheld ? ' · 正文已按当前授权撤下' : ''}</p><Button variant="outline" onClick={() => setPreview(c)}>查看结果／恢复</Button></div>{c.receipt === null ? <p className="mt-2 text-xs text-muted-foreground">回执已按当前授权隐藏</p>
              : c.receipt ? <p className="mt-2 text-xs text-muted-foreground">回执 {String((c.receipt as Payload).receipt_id ?? '未记录')}</p> : null}</section>)}</>}
        {tab === 'missions' && <>{!missions.length && !loading && !failure && <EmptyState title="尚无正式 Mission">CEO 确认完整候选集合后，你有权查看的正式 Mission 会显示在这里。</EmptyState>}{missions.map(m => <section key={m.object_id} className="gov-mission-card"><h3 className="text-lg font-medium">{String(m.effective_revision?.payload.title ?? '')}</h3><Badge variant="secondary">正式内容 · 待执行承接</Badge><Facts payload={m.effective_revision?.payload ?? {}} responsibilities={m.responsibilities} /><details><summary>确认依据与下游承接</summary><pre className="overflow-auto text-xs">{JSON.stringify(m.handoff, null, 2)}</pre></details><Button variant="outline" onClick={() => { const url = new URL(location.href); url.searchParams.set('object', m.object_id); history.replaceState(null, '', url); switchTab('graph') }}>查看对象来源</Button></section>)}{missionAfter && <Button onClick={async () => { const e = epoch.current; try { const p = await governanceFetch<{ items: Obj[]; next_after: string | null }>(`/governance/missions?after=${missionAfter}`); if (mounted.current && e === epoch.current) { setMissions(v => [...v, ...p.items]); setMissionAfter(p.next_after) } } catch (e) { handleError(e) } }}>加载更多</Button>}</>}
      </>}
    </main>}
    </div><Sheet open={!!preview} onOpenChange={open => { if (!open) setPreview(null) }}><SheetContent className="overflow-y-auto sm:max-w-xl"><SheetHeader><SheetTitle>核对本次操作</SheetTitle></SheetHeader>{preview && <div className="gov-command-preview space-y-5 p-6"><Badge>{STATUS[preview.status]}</Badge><p>操作人：{session.identity.display_name}</p><p>操作：{(() => { const envelope = (preview.envelope as Payload | null) ?? {}; return ACTION[String(envelope.action_type ?? (envelope.event as Payload)?.kind)] ?? (envelope.items !== undefined ? '生成来源 Context 快照' : '操作') })()}</p>{(preview.envelope as Payload | null)?.action_type === 'm1b_confirm_candidates' && <Alert><AlertDescription>将确认整个 PCO 与 Mission 候选集合。核对完成不等于正式确认；本次确认不产生执行授权。</AlertDescription></Alert>}<Facts payload={(((preview.envelope as Payload | null)?.params ?? (preview.envelope as Payload | null)?.event ?? {}) as Payload)} /><p>对象：{preview.preview?.title ?? "查看确切引用"}</p>{preview.preview?.members.map(m => <details key={m.ref.object_id} open><summary>{m.title}</summary><Facts payload={m.payload} responsibilities={m.responsibilities} /></details>)}<details><summary>技术溯源：确切对象、版本与本次输入</summary><pre className="mt-3 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(preview.envelope, null, 2)}</pre></details>{preview.error && <Alert><AlertDescription>{REASONS[preview.error] ?? preview.error}</AlertDescription></Alert>}{preview.envelope === null ? (
            <Alert><AlertDescription data-testid="envelope-withheld">此提交的正文已按当前来源授权撤下，只能查看状态；原信封仍可用于安全重试。</AlertDescription></Alert>
          ) : null}
          {preview.receipt && (preview.kind === 'context_v02' ? (
            <div data-testid="context-receipt"><p>Context 已保存</p>
              <p className="text-xs text-muted-foreground">快照引用：{String((preview.receipt as Payload).context_id ?? '未记录')} · 无业务回执编号（不伪造）</p>
            </div>
          ) : (<div><p>{preview.receipt === null ? `回执已按当前授权隐藏（${String(preview.receipt_status ?? 'withheld')}）` : `正式回执：${String((preview.receipt as Payload | null)?.receipt_id ?? '未记录')}`}</p><details><summary>结果引用</summary><pre className="overflow-auto text-xs">{JSON.stringify((preview.receipt as Payload | null)?.result, null, 2)}</pre></details></div>))}{preview.status === 'prepared' && <Button disabled={busy || !!failure || preview.envelope === null} onClick={() => void submit(preview)}>确认并提交</Button>}{preview.status === 'unknown' && <Button disabled={busy || !!failure || preview.envelope === null} onClick={() => void submit(preview, true)}>以原请求核对并恢复</Button>}{preview.status === 'rejected' && <p>该请求已被明确拒绝。返回事项刷新并重新判断后，再发起新的提交。</p>}</div>}</SheetContent></Sheet>
  </div>
}

function WindowPane({ id, session, prepare, busy, onError, onBack }: { id: string; session: Session; prepare: (v: Payload) => Promise<void>; busy: boolean; onError: (e: unknown) => void; onBack: () => void }) {
  const [data, setData] = useState<WindowData | null>(null); const [stale, setStale] = useState(false)
  const [operation, setOperation] = useState<Operation | null>(null); const [content, setContent] = useState(''); const [reason, setReason] = useState('')
  const [target, setTarget] = useState(''); const [replaceId, setReplaceId] = useState(''); const [title, setTitle] = useState(''); const [deadline, setDeadline] = useState('')
  const [sceneId, setSceneId] = useState(''); const [bases, setBases] = useState<Basis[]>([]); const [ltcoId, setLtcoId] = useState('original')
  const [anchorReview, setAnchorReview] = useState(''); const [field, setField] = useState(''); const [snapshot, setSnapshot] = useState(''); const [context, setContext] = useState<unknown>(null)
  useEffect(() => { let active = true; let c: AbortController | null = null; const load = async () => { c?.abort(); c = new AbortController(); try { const d = await governanceFetch<WindowData>(`/governance/review-windows/${id}`, 'GET', undefined, undefined, c.signal); if (active) { setData(d); setStale(false); setSceneId(v => v || (d.scenes.length === 1 ? d.scenes[0].scene_id : '')); setTarget(v => v || d.monthly.targets[0]?.ref.object_id || '') } } catch (e) { if (active && !(e instanceof DOMException && e.name === 'AbortError')) { setStale(true); if (e instanceof ApiError && [401, 403, 404].includes(e.status)) setData(null); onError(e) } } }; void load(); const timer = setInterval(() => { if (!document.hidden) void load() }, 5000); window.addEventListener('focus', load); window.addEventListener('governance-committed', load); return () => { active = false; c?.abort(); clearInterval(timer); window.removeEventListener('focus', load); window.removeEventListener('governance-committed', load) } }, [id, onError])
  if (!data) return <p>正在读取当前有权访问的窗口…</p>
  const m = data.monthly; const scene = data.scenes.find(s => s.scene_id === sceneId)
  const disabled = busy || stale
  const sceneCommand = (event: Payload) => { if (scene) void prepare({ contract_version: 'tkos.workspace/0.1', scene_id: scene.scene_id, expected_version: scene.version, idempotency_key: crypto.randomUUID(), event }) }
  const begin = async (o: Operation, review?: Review) => { setOperation(o); setReason(''); setContent(String(review?.content?.content ?? review?.payload?.content ?? '')); setReplaceId(review?.record_id ?? ''); if (review) setTarget(review.target_object_id); setTitle(`${String(data.object.latest_revision.payload.title)} · 新一轮`); setDeadline(''); setLtcoId('original'); if (o.action_type.startsWith('m1b_reopen')) { try { setBases((await governanceFetch<{ items: Basis[] }>('/governance/bases')).items) } catch (e) { onError(e) } } }
  const send = async () => { if (!operation) return; const kind = operation.action_type; let params: Payload
    if (kind === 'm1b_comment') { const ref = m.targets.find(t => t.ref.object_id === target)?.ref; if (!ref) return; params = { target_ref: ref, content, ...(replaceId ? { replaces_record_id: replaceId } : {}) } }
    else if (kind === 'm1b_withdraw_comment') params = { review_record_id: replaceId, reason }
    else if (kind.startsWith('m1b_reopen')) { params = { reason, title, feedback_deadline: new Date(deadline).toISOString() }; if (ltcoId !== 'original') { const b = bases.find(b => b.ref.object_id === ltcoId); if (!b?.strategy_ref) return; params.rebase_strategy_ref = b.strategy_ref; params.rebase_ltco_ref = b.ref } }
    else params = { reason }
    const { payload_hash: _hash, ...targetRef } = operation.target
    await prepare({ action_type: kind, contract_version: 'tkos.method/0.3', target: targetRef, expected_versions: [], idempotency_key: crypto.randomUUID(), reason: reason || '本人提交共同核对意见', params })
  }
  const commentAction = data.actions.find(a => a.action_type === 'm1b_comment')
  const withdrawAction = data.actions.find(a => a.action_type === 'm1b_withdraw_comment')
  const reviewForAnchor = m.my_reviews.find(r => r.record_id === anchorReview)
  const anchorTarget = m.targets.find(t => t.ref.object_id === reviewForAnchor?.target_object_id)
  const names = Object.assign({}, ...[...m.targets, ...m.candidate_targets].map(t => personNames(t.responsibilities)), ...m.targets.flatMap(t => ((t.revision.payload.unit_outcomes ?? []) as Payload[]).map(o => ({[String(o.outcome_id)]: String(o.title)}))))
  return <Names.Provider value={names}><div className="gov-window space-y-6"><Button variant="ghost" onClick={onBack}>← 返回我的待办</Button><div><h2 className="text-2xl font-semibold">{String(data.object.latest_revision.payload.title)}</h2><div className="mt-2 flex gap-2"><Badge>{STATUS[data.object.method_state.phase]}</Badge><Badge variant="outline">{data.object.protocol.contract_version}</Badge></div></div>
    <ReviewPath phase={data.object.method_state.phase}/>{stale && <Alert><AlertDescription>数据暂未刷新，提交已暂停。</AlertDescription></Alert>}
    <div className="grid gap-3 text-sm md:grid-cols-2"><p>业务周期：{formatPeriod(m.business_period)}</p><p>评论截止：{formatTime(m.feedback_deadline.value)}</p><p>可见有效意见：{m.visible_effective_opinion_count}</p><p>参与人：{m.member_details.map(p => `${p.display_name ?? '未记录姓名'}${p.current ? '' : '（任职无效）'}`).join('、')}</p></div>
    <section className="space-y-4"><h3 className="text-lg font-semibold">固定核对内容</h3>{m.targets.map(t => <details key={t.ref.object_id} className="border-b py-3" open><summary className="cursor-pointer font-medium">{String(t.revision.payload.title)}</summary><div className="pt-3"><Facts payload={t.revision.payload} responsibilities={t.responsibilities} /></div></details>)}</section>
    <section className="space-y-3"><h3 className="text-lg font-semibold">本人意见与历史</h3>{!m.my_reviews.length && <p className="text-sm text-muted-foreground">尚未发表意见。</p>}{m.my_reviews.map(r => <div className="border-b py-3" key={r.record_id}><p>{text(r.content ?? r.payload)}</p><Badge variant="outline">{r.effective_opinion ? '有效意见' : '历史记录'}</Badge>{r.effective_opinion && <div className="mt-2 flex gap-2"><Button size="sm" variant="outline" disabled={disabled || !commentAction?.allowed} onClick={() => commentAction && void begin(commentAction, r)}>替代这条意见</Button><Button size="sm" variant="outline" disabled={disabled || !withdrawAction?.allowed} onClick={() => withdrawAction && void begin(withdrawAction, r)}>撤回这条意见</Button></div>}</div>)}<details><summary>协作历史</summary>{m.reviews.map(r => <p className="my-2 text-sm" key={r.record_id}>{r.principal_id === session.identity.principal_id ? '本人' : '参与者'} · {text(r.content ?? r.payload)}</p>)}</details></section>
    {m.candidate.status === 'available' && <section className="space-y-3"><h3 className="text-lg font-semibold">候选集合与差异</h3><Facts payload={m.candidate.revision?.payload ?? {}} />{m.candidate_targets.map(t => <details key={t.ref.object_id}><summary>完整候选：{String(t.revision.payload.title)}</summary><Facts payload={t.revision.payload} responsibilities={t.responsibilities} /></details>)}{m.differences.map(d => <details open key={d.after_ref.object_id}><summary>{String(m.targets.find(t => t.ref.object_id === d.after_ref.object_id)?.revision.payload.title ?? '候选成员')}</summary>{d.fields.filter(f => !f.field_path.includes("_ref/")).map(f => <div key={f.field_path} className="my-3 grid gap-2 border-l-2 pl-3"><span className="font-medium">{FIELD[f.field_path.split('/')[1]] ?? f.field_path}</span><div className="text-sm text-muted-foreground">原内容：<BusinessValue value={f.before} names={{}} /></div><div className="text-sm">候选：<BusinessValue value={f.after} names={{}} /></div></div>)}</details>)}<p className="text-sm text-muted-foreground">候选尚未获得执行授权。CEO 对整个集合做出决定。</p></section>}
    <section className="space-y-3"><h3 className="text-lg font-semibold">月度核对记录</h3>{data.scenes.length > 0 ? <Picker value={sceneId} onChange={setSceneId} label="选择核对场景" options={data.scenes.map(s => ({ value: s.scene_id, label: s.definition.title }))} /> : <p>尚未建立月度场景，正式评论仍可提交；字段定位和差异核对需先建立场景。</p>}
      {!data.scenes.length && data.can_create_scene && <Button variant="outline" disabled={disabled} onClick={() => { const owner = session.identity.assignments.find(a => a.role === 'CEO' && a.domain_id === data.object.domain_id); if (!owner) return; void prepare({ contract_version: 'tkos.workspace/0.1', scene_id: crypto.randomUUID(), expected_version: 0, idempotency_key: crypto.randomUUID(), event: { kind: 'create', scene_type: 'monthly', anchor_ref: { object_id: id, revision_id: data.object.latest_revision.revision_id, payload_hash: data.object.latest_revision.payload_hash }, external_id: `runtime:monthly:${id}`, title: `${String(data.object.latest_revision.payload.title)} · 月度核对`, owner_assignment_id: owner.assignment_id, participant_assignment_ids: m.members.map(p => p.assignment_id).filter(v => v !== owner.assignment_id) } }) }}>建立月度核对场景</Button>}
      {scene && <><p>{scene.monthly.reviewed_current_candidate ? '本人已核对当前候选版本' : '本人尚未核对当前候选版本'}</p>{m.candidate.ref && m.members.some(p => p.principal_id === session.identity.principal_id) && <Button disabled={disabled || scene.monthly.reviewed_current_candidate} onClick={() => sceneCommand({ kind: 'diff_response', candidate_ref: m.candidate.ref, response: 'reviewed' })}>记录核对完成</Button>}
        <details><summary>为已保存评论补充字段定位</summary><div className="mt-3 space-y-3"><Picker value={anchorReview} onChange={v => { setAnchorReview(v); setField('') }} label="选择本人评论" options={m.my_reviews.filter(r => r.kind === 'window_comment').map(r => ({ value: r.record_id, label: text(r.content ?? r.payload) }))} />{anchorTarget && <Picker value={field} onChange={setField} label="选择评论字段" options={Object.keys(anchorTarget.revision.payload).filter(k => FIELD[k]).map(k => ({ value: '/' + k, label: FIELD[k] }))} />}<Button disabled={disabled || !anchorTarget || !field} onClick={() => sceneCommand({ kind: 'comment_anchor', review_record_id: anchorReview, target_ref: anchorTarget?.ref, field_path: field })}>保存字段定位</Button><p className="text-xs text-muted-foreground">此步骤有独立回执；失败不会撤销已保存的评论。</p><details><summary>已保存的场景记录</summary><pre className="overflow-auto text-xs">{JSON.stringify(scene.events, null, 2)}</pre></details></div></details></>}
    </section>
    <section className="space-y-4"><h3 className="text-lg font-semibold">当前可办理操作</h3>{data.actions.filter(a => a.action_type !== 'm1b_withdraw_comment').map(a => <div key={a.action_type} className="flex items-center gap-3"><Button variant="outline" disabled={disabled || !a.allowed} onClick={() => void begin(a)}>{a.label}</Button>{a.reason && <span className="text-sm text-muted-foreground">{REASONS[a.reason] ?? a.reason}</span>}</div>)}<p className="text-sm text-muted-foreground">关窗和生成候选由 Co-agent 执行，本工作台展示结果。</p>
      {operation && <form className="space-y-4 border-t pt-4" onSubmit={e => { e.preventDefault(); void send() }}><h4 className="font-semibold">{replaceId && operation.action_type === 'm1b_comment' ? '替代本人意见' : operation.label}</h4>{operation.action_type === 'm1b_comment' ? <><Picker value={target} onChange={setTarget} label="评论对象" options={m.targets.filter(t => !replaceId || t.ref.object_id === target).map(t => ({ value: t.ref.object_id, label: String(t.revision.payload.title) }))} /><Label htmlFor="comment">意见内容</Label><Textarea id="comment" value={content} onChange={e => setContent(e.target.value)} required /></> : <><Label htmlFor="reason">操作理由</Label><Textarea id="reason" value={reason} onChange={e => setReason(e.target.value)} required minLength={5} /></>}
      {operation.action_type.startsWith('m1b_reopen') && <><Label htmlFor="window-title">新窗口名称</Label><Input id="window-title" value={title} onChange={e => setTitle(e.target.value)} required /><Label htmlFor="deadline">评论截止时间（本地时间）</Label><Input id="deadline" type="datetime-local" value={deadline} onChange={e => setDeadline(e.target.value)} required /><Picker value={ltcoId} onChange={setLtcoId} label="选择战略与 LTCO 依据" options={[{ value: 'original', label: '保留原战略与 LTCO 依据' }, ...bases.filter(b => b.object_type === 'LTCO' && bases.some(s => s.object_type === 'Strategy' && JSON.stringify(s.ref) === JSON.stringify(b.strategy_ref))).map(b => ({ value: b.ref.object_id, label: `采用：${b.title}` }))]} /></>}
      <p className="text-xs text-muted-foreground">预览后才会提交；若版本已变化，请重新选择操作并核对内容。</p><Button type="submit" disabled={disabled}>准备并预览</Button><Button type="button" variant="ghost" onClick={() => setOperation(null)}>取消编辑</Button></form>}
    </section>
    <details><summary>Agent 产物与运行追溯</summary><p className="my-3 text-sm">仅显示已记录且本人有权读取的关联；本工作台不运行模型。</p><pre className="overflow-auto text-xs">{JSON.stringify(data.recovery, null, 2)}</pre><div className="my-3 flex gap-2"><Input aria-label="已知 Context 快照编号" placeholder="已知 Context 快照编号" value={snapshot} onChange={e => setSnapshot(e.target.value)} /><Button variant="outline" onClick={async () => { try { setContext(await governanceFetch(`/governance/context-packs/${encodeURIComponent(snapshot)}`)) } catch (e) { setContext(null); onError(e) } }}>读取快照</Button></div>{context != null && <pre className="overflow-auto text-xs">{JSON.stringify(context, null, 2)}</pre>}</details>
  </div></Names.Provider>
}
