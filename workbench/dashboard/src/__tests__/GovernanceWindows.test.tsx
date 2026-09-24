import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { GovernanceApp } from '@/GovernanceApp'

// Review windows are handled by the page of the contract their object is bound to.
vi.mock('@/App', () => ({ App: () => <div>原只读看板</div> }))
const identity = { scope_id: 'scope', principal_id: 'ceo', display_name: '测试 CEO', auth_epoch: 1,
                   assignments: [{ role: 'CEO', domain_id: 'domain', assignment_id: 'assignment' }] }
const response = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json' } }))
const ref = (id: string) => ({ object_id: id, revision_id: `${id}-r1`, payload_hash: 'a'.repeat(64) })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/dashboard/') })

function todo(id: string, version: string) {
  return { object_id: id, title: `窗口 ${id}`, phase: 'open', label: '参与共同核对', contract_version: version, actions: [] }
}

function window03(id: string) {
  return {
    identity,
    object: { object_id: id, object_type: 'ReviewWindow', domain_id: 'domain', object_version: 3,
              latest_revision: { revision_id: `${id}-r1`, payload_hash: 'a'.repeat(64), payload: { title: '0.3 共同核对窗口' } },
              method_state: { phase: 'open' }, protocol: { contract_version: 'tkos.method/0.3' } } as Record<string, unknown>,
    monthly: { window: {}, business_period: { start: '2026-09-01', end: '2026-09-30' },
               feedback_deadline: { value: '2026-09-20T00:00:00Z' },
               targets: [{ ref: ref('pco-1'), revision: { revision_id: 'pco-1-r1', payload_hash: 'a'.repeat(64),
                                                          payload: { title: '阶段目标 1' } } }],
               candidate_targets: [], candidate: { status: 'unavailable' }, differences: [], reviews: [],
               my_reviews: [], visible_effective_opinion_count: 0, members: [], member_details: [] } as Record<string, unknown> | null,
    scenes: [], can_create_scene: false, recovery: {},
    actions: [{ action_type: 'm1b_comment', label: '发表意见', allowed: true, reason: null,
                target: { ...ref(id), expected_version: 3 }, formal_effect: 'comment_history',
                contract_version: 'tkos.method/0.3' }],
  }
}

function methodWindow(id: string, version: string) {
  return {
    identity,
    object: { object_id: id, object_type: 'ReviewWindow', domain_id: 'domain', object_version: 2,
              latest_revision: { revision_id: `${id}-r1`, payload_hash: 'b'.repeat(64),
                                 payload: { title: `${version} 复核窗口`, pco_refs: [ref('pco-4')], mission_refs: [] } },
              method_state: { phase: 'open' }, protocol: { contract_version: version } },
    monthly: null, scenes: [], can_create_scene: false, recovery: {},
    actions: [{ action_type: 'm1b_comment', label: '发表意见', allowed: true, reason: null,
                target: { ...ref(id), expected_version: 2 }, object_domain_id: 'domain',
                contract_version: version, formal_effect: 'comment_history',
                options: { targets: [{ value: 'pco-4:pco-4-r1', label: '阶段目标 4', ref: ref('pco-4') }] } }],
  }
}

/** A logged-in session facade; `route` answers every other read. */
function serve(route: (url: string) => unknown) {
  let loggedIn = false
  const prepared: Array<Record<string, unknown>> = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return response({ identity, csrf: 'csrf' }) }
    if (url.endsWith('/session')) return loggedIn ? response({ identity, csrf: 'csrf' }) : response({ error: { code: 'UNAUTHENTICATED' } }, 401)
    if (url.endsWith('/commands/prepare')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      prepared.push(body)
      return response({ command_id: 'cmd-1', status: 'prepared', kind: 'method', envelope: body, preview: { title: '预览', members: [] } })
    }
    return response(route(url) ?? { items: [], next_after: null })
  }))
  return prepared
}

async function openTodo() {
  render(<GovernanceApp />)
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
  fireEvent.click(await screen.findByRole('button', { name: /查看并办理/ }))
}

it('handles a 0.3 window on the 0.3 page and declares the bound 0.3 contract', async () => {
  const prepared = serve((url) => url.includes('/governance/tasks') ? { items: [todo('w3', 'tkos.method/0.3')], next_after: null }
    : url.includes('/governance/review-windows/w3') ? window03('w3') : undefined)
  await openTodo()
  expect(await screen.findByText('固定核对内容')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '发表意见' }))
  fireEvent.change(screen.getByLabelText('意见内容'), { target: { value: '请补充依据' } })
  fireEvent.click(screen.getByRole('button', { name: '准备并预览' }))
  await waitFor(() => expect(prepared).toHaveLength(1))
  expect(prepared[0]).toMatchObject({ action_type: 'm1b_comment', contract_version: 'tkos.method/0.3',
    target: { object_id: 'w3', revision_id: 'w3-r1', expected_version: 3 },
    params: { target_ref: ref('pco-1'), content: '请补充依据' } })
})

it.each(['tkos.method/0.4', 'tkos.method/0.5'])('routes a %s window to the Method action page and declares that bound version', async (version) => {
  const prepared = serve((url) => url.includes('/governance/tasks') ? { items: [todo('w4', version)], next_after: null }
    : url.includes('/governance/review-windows/w4') ? methodWindow('w4', version) : undefined)
  await openTodo()
  expect(await screen.findByTestId('method-task-w4')).toBeInTheDocument()
  expect(screen.queryByText('固定核对内容')).not.toBeInTheDocument()
  fireEvent.click(screen.getByTestId('method-action-w4-m1b_comment'))
  fireEvent.change(await screen.findByLabelText('评论对象（窗口冻结的确切目标）'), { target: { value: 'pco-4:pco-4-r1' } })
  fireEvent.change(screen.getByLabelText('意见内容'), { target: { value: '补充意见内容' } })
  fireEvent.click(screen.getByRole('button', { name: '准备并预览' }))
  await waitFor(() => expect(prepared).toHaveLength(1))
  expect(prepared[0]).toMatchObject({ action_type: 'm1b_comment', contract_version: version,
    target: { object_id: 'w4', revision_id: 'w4-r1', expected_version: 2 },
    params: { target_ref: ref('pco-4'), content: '补充意见内容' } })
  expect(JSON.stringify(prepared)).not.toContain('tkos.method/0.3')
})

it('follows the loaded binding when a todo still names 0.3 and the window is now bound to 0.5', async () => {
  serve((url) => url.includes('/governance/tasks') ? { items: [todo('w5', 'tkos.method/0.3')], next_after: null }
    : url.includes('/governance/review-windows/w5') ? methodWindow('w5', 'tkos.method/0.5') : undefined)
  await openTodo()
  expect(await screen.findByTestId('method-task-w5')).toBeInTheDocument()
  expect(screen.queryByTestId('pane-error')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '← 返回我的待办' }))
  expect(await screen.findByRole('button', { name: /查看并办理/ })).toBeInTheDocument()
})

it('shows a notice instead of crashing when a window read has no monthly view', async () => {
  const value = window03('w6')
  value.monthly = null
  serve((url) => url.includes('/governance/tasks') ? { items: [todo('w6', 'tkos.method/0.3')], next_after: null }
    : url.includes('/governance/review-windows/w6') ? value : undefined)
  await openTodo()
  expect(await screen.findByText(/没有共同核对内容/)).toBeInTheDocument()
  expect(screen.queryByTestId('pane-error')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '← 返回我的待办' }))
  expect(await screen.findByRole('button', { name: /查看并办理/ })).toBeInTheDocument()
})

it('keeps the shell when a window page fails to render and offers a way back', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  const broken = window03('w9')
  delete broken.object.latest_revision
  serve((url) => url.includes('/governance/tasks') ? { items: [todo('w9', 'tkos.method/0.3')], next_after: null }
    : url.includes('/governance/review-windows/w9') ? broken : undefined)
  await openTodo()
  expect(await screen.findByTestId('pane-error')).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '工作台导航' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '退出登录' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '← 返回我的待办' }))
  expect(await screen.findByRole('button', { name: /查看并办理/ })).toBeInTheDocument()
  expect(screen.queryByTestId('pane-error')).not.toBeInTheDocument()
})

it('ErrorBoundary catches a throwing child, retries, and resets on a new key', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  let broken = true
  function Child() { if (broken) throw new Error('render failed'); return <p>恢复后的内容</p> }
  const onBack = vi.fn()
  const view = render(<ErrorBoundary resetKey="a" onBack={onBack} backLabel="返回列表"><Child /></ErrorBoundary>)
  expect(screen.getByTestId('pane-error')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '返回列表' }))
  expect(onBack).toHaveBeenCalledOnce()
  expect(screen.getByTestId('pane-error')).toBeInTheDocument()
  broken = false
  fireEvent.click(screen.getByRole('button', { name: '重新显示' }))
  expect(screen.getByText('恢复后的内容')).toBeInTheDocument()
  broken = true
  view.rerender(<ErrorBoundary resetKey="a"><p>另一部分</p><Child /></ErrorBoundary>)
  expect(screen.getByTestId('pane-error')).toBeInTheDocument()
  broken = false
  view.rerender(<ErrorBoundary resetKey="b"><Child /></ErrorBoundary>)
  expect(screen.getByText('恢复后的内容')).toBeInTheDocument()
})
