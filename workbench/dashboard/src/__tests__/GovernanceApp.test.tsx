import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GovernanceApp } from '@/GovernanceApp'
vi.mock('@/App', () => ({ App: () => <div>原只读看板</div> }))
const identity = { scope_id:'scope', principal_id:'ceo', display_name:'测试 CEO', auth_epoch:1, assignments:[{ role:'CEO', domain_id:'domain', assignment_id:'assignment' }] }
const response=(value:unknown, status=200)=>Promise.resolve(new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}}))
afterEach(()=>{cleanup();vi.restoreAllMocks();window.history.replaceState(null,'','/dashboard/')})
describe('governance entry and session isolation',()=>{
 it('retains old read-only mode only when session route is absent',async()=>{
  vi.stubGlobal('fetch',vi.fn(()=>response({error:{code:'NOT_FOUND'}},404)))
  render(<GovernanceApp/>);expect(await screen.findByText('原只读看板')).toBeInTheDocument()
 })
 it('expired session does not fall back to shared viewer',async()=>{
  vi.stubGlobal('fetch',vi.fn(()=>response({error:{code:'UNAUTHENTICATED'}},401)))
  render(<GovernanceApp/>);expect(await screen.findByLabelText('个人登录码')).toBeInTheDocument();expect(screen.queryByText('原只读看板')).toBeNull()
 })
 it('login defaults to personal tasks and sends csrf on logout',async()=>{
  let loggedIn=false
  const fetcher=vi.fn((url:string,init?:RequestInit)=>{
   if(url.endsWith('/session')&&init?.method==='POST'){loggedIn=true;return response({identity,csrf:'session-csrf'})}
   if(url.endsWith('/session')&&init?.method==='DELETE'){expect((init.headers as Record<string,string>)['X-CSRF-Token']).toBe('session-csrf');loggedIn=false;return response({signed_out:true})}
   if(url.endsWith('/session'))return loggedIn?response({identity,csrf:'session-csrf'}):response({error:{code:'UNAUTHENTICATED'}},401)
   return response({items:[],next_after:null})
  })
  vi.stubGlobal('fetch',fetcher);render(<GovernanceApp/>);
  fireEvent.change(await screen.findByLabelText('用户名'),{target:{value:'ceo'}})
  fireEvent.change(screen.getByLabelText('个人登录码'),{target:{value:'personal-code-for-test'}})
  fireEvent.click(screen.getByRole('button',{name:/^登录$/}))
  expect(await screen.findByText('需要我处理的事项')).toBeInTheDocument()
  await waitFor(()=>expect(screen.getByText('当前没有待处理事项')).toBeInTheDocument())
  fireEvent.click(screen.getByRole('button',{name:'退出登录'}));expect(await screen.findByLabelText('个人登录码')).toHaveValue('')
 })

 it('has no object-list entry and opens Mission sources in object details', async () => {
  const mission = { object_id: 'mission-1', domain_id: 'domain',
    effective_revision: { revision_id: 'r2', payload_hash: 'h', payload: { title: '正式任务' } } }
  let loggedIn = false
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
   if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return response({ identity, csrf: 'csrf' }) }
   if (url.endsWith('/session')) return loggedIn ? response({ identity, csrf: 'csrf' }) : response({ error: { code: 'UNAUTHENTICATED' } }, 401)
   if (url.includes('/governance/missions')) return response({ items: [mission], next_after: null })
   return response({ items: [], next_after: null })
  }))
  render(<GovernanceApp/>)
  fireEvent.change(await screen.findByLabelText('用户名'),{target:{value:'ceo'}})
  fireEvent.change(screen.getByLabelText('个人登录码'),{target:{value:'personal-code-for-test'}})
  fireEvent.click(screen.getByRole('button',{name:/^登录$/}))
  expect(await screen.findByText('需要我处理的事项')).toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'对象列表'})).not.toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button',{name:'正式 Mission'}))
  expect((await screen.findAllByText('正式任务')).length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button',{name:'查看对象来源'}))
  await screen.findByText('原只读看板')
  expect(window.location.search).toContain('view=graph')
  expect(window.location.search).toContain('object=mission-1')
 })

 it('normalizes a legacy list route into the explorer without leaving the old view', async () => {
  let loggedIn = false
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
   if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return response({ identity, csrf: 'csrf' }) }
   if (url.endsWith('/session')) return loggedIn ? response({ identity, csrf: 'csrf' }) : response({ error: { code: 'UNAUTHENTICATED' } }, 401)
   return response({ items: [], next_after: null })
  }))
  window.history.replaceState(null, '', '/dashboard/?view=list&object=o1')
  render(<GovernanceApp/>)
  fireEvent.change(await screen.findByLabelText('用户名'),{target:{value:'ceo'}})
  fireEvent.change(screen.getByLabelText('个人登录码'),{target:{value:'personal-code-for-test'}})
  fireEvent.click(screen.getByRole('button',{name:/^登录$/}))
  // The legacy route selects the explorer tab that hosts object details.
  expect(await screen.findByText('原只读看板')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '业务关系图' })).toHaveAttribute('aria-current', 'page')
 })
 it('network failure blocks entry rather than showing sample tasks',async()=>{
  vi.stubGlobal('fetch',vi.fn(()=>Promise.reject(new TypeError('network'))));render(<GovernanceApp/>);
  expect(await screen.findByRole('button',{name:'重新连接'})).toBeInTheDocument();expect(screen.queryByText('原只读看板')).toBeNull()
 })
})

describe('0.4 formal Mission fields', () => {
 it('shows why, period, requirements, scope and exact references without raw JSON', async () => {
  const mission = {
   object_id: 'mission-1', domain_id: 'domain',
   effective_revision: { revision_id: 'r2', payload_hash: 'h', payload: {
     title: '正式任务', why: '为什么：保障周期结果', requirements: '必须按期交付证据',
     period: { start: '2026-09-01', end: '2026-09-30' },
     primary_scope_id: 'scope-1',
     parent_pco_ref: { object_id: 'abcdef12-3456-7890-abcd-ef1234567890',
                       revision_id: '12345678-90ab-cdef-1234-567890abcdef', payload_hash: 'a'.repeat(64) },
     evidence_refs: [{ object_id: 'evidence12-3456-7890-abcd-ef1234567890',
                       revision_id: 'evid1234-90ab-cdef-1234-567890abcdef', payload_hash: 'b'.repeat(64) }],
     criteria: ['标准一'], owner_principal_id: 'p-owner',
   } },
  }
  let loggedIn = false
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
   if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return response({ identity, csrf: 'csrf' }) }
   if (url.endsWith('/session')) return loggedIn ? response({ identity, csrf: 'csrf' }) : response({ error: { code: 'UNAUTHENTICATED' } }, 401)
   if (url.includes('/governance/missions')) return response({ items: [mission], next_after: null })
   return response({ items: [], next_after: null })
  }))
  render(<GovernanceApp />)
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
  fireEvent.click(await screen.findByRole('button', { name: '正式 Mission' }))
  expect((await screen.findAllByText('正式任务')).length).toBeGreaterThan(0)
  expect(screen.getByText('为什么（Why）')).toBeInTheDocument()
  expect(screen.getByText('为什么：保障周期结果')).toBeInTheDocument()
  expect(screen.getByText('必须按期交付证据')).toBeInTheDocument()
  expect(screen.getByTestId('period-range')).toHaveTextContent('2026')
  expect(screen.getByText('主 Scope')).toBeInTheDocument()
  expect(screen.getAllByTestId('exact-ref').length).toBeGreaterThanOrEqual(2)
 })
})

it('rejects a late session poll from a previous identity after re-login', async () => {
  const identityA = { ...identity, display_name: '旧身份 A' }
  const identityB = { ...identity, display_name: '新身份 B' }
  const deferred: { resolve?: (value: Response) => void } = {}
  let sessionGets = 0
  let logins = 0
  let current: typeof identity = identityA
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/session') && init?.method === 'POST') {
      logins += 1
      current = logins === 1 ? identityA : identityB
      return response({ identity: current, csrf: 'csrf' })
    }
    if (url.endsWith('/session') && init?.method === 'DELETE') return response({ signed_out: true })
    if (url.endsWith('/session')) {
      sessionGets += 1
      if (sessionGets === 1) return response({ error: { code: 'UNAUTHENTICATED' } }, 401)
      if (sessionGets === 2) return new Promise<Response>((resolve) => { deferred.resolve = resolve })
      return response({ identity: current, csrf: 'csrf' })
    }
    return response({ items: [], next_after: null })
  }))
  render(<GovernanceApp />)
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
  expect(await screen.findByText('旧身份 A')).toBeInTheDocument()
  window.dispatchEvent(new Event('focus'))
  await waitFor(() => expect(sessionGets).toBe(2))
  fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
  fireEvent.change(await screen.findByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
  expect(await screen.findByText('新身份 B')).toBeInTheDocument()
  deferred.resolve?.(new Response(JSON.stringify({ identity: identityA, csrf: 'csrf' }),
    { status: 200, headers: { 'Content-Type': 'application/json' } }))
  await new Promise((resolve) => setTimeout(resolve, 30))
  expect(screen.getByText('新身份 B')).toBeInTheDocument()
  expect(screen.queryByText('旧身份 A')).not.toBeInTheDocument()
})

it('clears an old prepare failure on the next explicit successful prepare', async () => {
  const task = { object_id: 'o1', object_type: 'StrategicAgreement', title: 'Agreement 1',
                 phase: 'awaiting_confirmation', contract_version: 'tkos.method/0.4',
                 actions: [{ action_type: 'm1a_confirm_agreement', label: '确认 Agreement（本人）',
                             formal_effect: 'agreement_confirmation_record', allowed: true, reason: null,
                             target: { object_id: 'o1', revision_id: 'r1', payload_hash: 'a'.repeat(64),
                                       expected_version: 2 },
                             contract_version: 'tkos.method/0.4' }] }
  let prepareFails = true
  let loggedIn = false
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return response({ identity, csrf: 'csrf' }) }
    if (url.endsWith('/session')) return loggedIn ? response({ identity, csrf: 'csrf' }) : response({ error: { code: 'UNAUTHENTICATED' } }, 401)
    if (url.includes('/governance/method/tasks')) return response({ items: [task], next_after: null })
    if (url.endsWith('/commands/prepare')) {
      if (prepareFails) return response({ error: { code: 'VERSION_CONFLICT' } }, 409)
      return response({ command_id: 'cmd-1', status: 'prepared', kind: 'method',
                        envelope: { action_type: 'm1a_confirm_agreement', contract_version: 'tkos.method/0.4' },
                        preview: { title: 'Agreement 1', members: [] } })
    }
    return response({ items: [], next_after: null })
  }))
  render(<GovernanceApp />)
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
  fireEvent.click(await screen.findByRole('button', { name: '方法事项' }))
  await screen.findByTestId('method-task-o1')
  fireEvent.click(screen.getByTestId('method-action-o1-m1a_confirm_agreement'))
  fireEvent.change(await screen.findByLabelText('确认说明'), { target: { value: '本人确认' } })
  fireEvent.click(screen.getByRole('button', { name: '准备并预览' }))
  expect(await screen.findByText('版本已变化，请刷新后重新判断')).toBeInTheDocument()
  prepareFails = false
  fireEvent.click(screen.getByRole('button', { name: '准备并预览' }))
  await screen.findByText('确认并提交')
  await waitFor(() => expect(screen.queryByText('版本已变化，请刷新后重新判断')).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: '确认并提交' })).toBeEnabled()
})
