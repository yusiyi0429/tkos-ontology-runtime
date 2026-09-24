import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { GovernanceApp } from '@/GovernanceApp'

// Only a lost session returns to login; permission and CSRF rejections stay inline.
vi.mock('@/App', () => ({ App: () => <div>原只读看板</div> }))
const identity = { scope_id: 'scope', principal_id: 'ceo', display_name: '测试 CEO', auth_epoch: 1,
                   assignments: [{ role: 'CEO', domain_id: 'domain', assignment_id: 'assignment' }] }
const json = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json' } }))
const denied = (code: string, status = 403) => json({ error: { code } }, status)
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/dashboard/') })

const task = { object_id: 'o1', object_type: 'StrategicAgreement', title: 'Agreement 1',
               phase: 'awaiting_confirmation', contract_version: 'tkos.method/0.4',
               actions: [{ action_type: 'm1a_confirm_agreement', label: '确认 Agreement（本人）',
                           formal_effect: 'agreement_confirmation_record', allowed: true, reason: null,
                           target: { object_id: 'o1', revision_id: 'r1', payload_hash: 'a'.repeat(64), expected_version: 2 },
                           contract_version: 'tkos.method/0.4' }] }

type Handler = (url: string, init?: RequestInit) => Promise<Response> | undefined
/** Session facade stub; `handle` may answer any request first. */
function serve(handle: Handler = () => undefined) {
  let loggedIn = false
  const fetcher = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const answer = handle(url, init)
    if (answer) return answer
    if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return json({ identity, csrf: 'csrf' }) }
    if (url.endsWith('/session')) return loggedIn ? json({ identity, csrf: 'csrf' }) : denied('UNAUTHENTICATED', 401)
    if (url.includes('/governance/method/tasks')) return json({ items: [task], next_after: null })
    return json({ items: [], next_after: null })
  })
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

async function login() {
  render(<GovernanceApp />)
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
}

async function openConfirmForm() {
  await login()
  fireEvent.click(await screen.findByRole('button', { name: '方法事项' }))
  fireEvent.click(await screen.findByTestId('method-action-o1-m1a_confirm_agreement'))
  fireEvent.change(await screen.findByLabelText('确认说明'), { target: { value: '本人确认该版本' } })
}

it('a wrong login code says the code is wrong, not that a session expired', async () => {
  serve((url, init) => url.endsWith('/session') && init?.method === 'POST' ? denied('LOGIN_FAILED', 401) : undefined)
  await login()
  expect(await screen.findByText('登录码不正确，请核对用户名与个人登录码')).toBeInTheDocument()
  expect(screen.queryByText(/会话已失效/)).not.toBeInTheDocument()
  expect(screen.getByLabelText('个人登录码')).toHaveValue('')
})

it('a 401 on a read returns to login and says the session expired', async () => {
  serve((url) => url.includes('/governance/tasks') ? denied('UNAUTHENTICATED', 401) : undefined)
  await login()
  expect(await screen.findByText('会话已失效，请重新登录')).toBeInTheDocument()
  expect(screen.getByLabelText('个人登录码')).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: '工作台导航' })).not.toBeInTheDocument()
})

it('a 403 on prepare is shown inline and keeps the shell, the form and its input', async () => {
  serve((url) => url.endsWith('/commands/prepare') ? denied('FORBIDDEN') : undefined)
  await openConfirmForm()
  fireEvent.click(screen.getByRole('button', { name: '准备并预览' }))
  expect(await screen.findByText('当前权限不允许')).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '工作台导航' })).toBeInTheDocument()
  expect(screen.getByLabelText('确认说明')).toHaveValue('本人确认该版本')
  expect(screen.queryByLabelText('个人登录码')).not.toBeInTheDocument()
})

it('a CSRF failure asks for a fresh page token and keeps the input for the retry', async () => {
  let token = 'csrf'
  const sent: string[] = []
  serve((url, init) => {
    if (url.endsWith('/session') && init?.method !== 'POST' && token === 'fresh') return json({ identity, csrf: 'fresh-csrf' })
    if (!url.endsWith('/commands/prepare')) return undefined
    const header = (init?.headers as Record<string, string>)['X-CSRF-Token']
    sent.push(header)
    if (header !== 'fresh-csrf') return denied('CSRF_TOKEN_INVALID')
    return json({ command_id: 'cmd-1', status: 'prepared', kind: 'method',
                  envelope: { action_type: 'm1a_confirm_agreement', contract_version: 'tkos.method/0.4' },
                  preview: { title: 'Agreement 1', members: [] } })
  })
  await openConfirmForm()
  fireEvent.click(screen.getByRole('button', { name: '准备并预览' }))
  expect(await screen.findByText(/页面安全令牌已过期/)).toBeInTheDocument()
  expect(screen.queryByText(/会话已失效/)).not.toBeInTheDocument()
  token = 'fresh'
  fireEvent.click(screen.getByRole('button', { name: '更新安全令牌' }))
  await waitFor(() => expect(screen.queryByText(/页面安全令牌已过期/)).not.toBeInTheDocument())
  expect(screen.getByLabelText('确认说明')).toHaveValue('本人确认该版本')
  fireEvent.click(screen.getByRole('button', { name: '准备并预览' }))
  expect(await screen.findByRole('button', { name: '确认并提交' })).toBeInTheDocument()
  expect(sent).toEqual(['csrf', 'fresh-csrf'])
})

it('a 403 on the todo list hides that list only, and a 403 session check keeps the shell', async () => {
  let deny = false
  serve((url) => {
    if (url.includes('/governance/tasks')) return deny ? denied('FORBIDDEN')
      : json({ items: [{ object_id: 'w1', title: '共同核对窗口', phase: 'open', label: '参与共同核对',
                         contract_version: 'tkos.method/0.3', actions: [] }], next_after: null })
    if (url.endsWith('/session') && deny) return denied('FORBIDDEN')
    return undefined
  })
  await login()
  expect(await screen.findByText('共同核对窗口')).toBeInTheDocument()
  deny = true
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect(await screen.findByText('当前权限不允许')).toBeInTheDocument()
  expect(screen.queryByText('共同核对窗口')).not.toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '工作台导航' })).toBeInTheDocument()
  expect(screen.queryByLabelText('个人登录码')).not.toBeInTheDocument()
})

it('a window that is no longer readable says so and offers the way back', async () => {
  serve((url) => url.includes('/governance/tasks')
    ? json({ items: [{ object_id: 'w2', title: '窗口 2', phase: 'open', label: '参与共同核对',
                       contract_version: 'tkos.method/0.3', actions: [] }], next_after: null })
    : url.includes('/governance/review-windows/w2') ? denied('NOT_FOUND', 404) : undefined)
  await login()
  fireEvent.click(await screen.findByRole('button', { name: /查看并办理/ }))
  expect(await screen.findByRole('heading', { name: '该事项不存在或当前不可见' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '工作台导航' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '← 返回我的待办' }))
  expect(await screen.findByRole('button', { name: /查看并办理/ })).toBeInTheDocument()
})
