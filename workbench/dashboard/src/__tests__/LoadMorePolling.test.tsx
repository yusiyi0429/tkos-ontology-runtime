import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { GovernanceApp } from '@/GovernanceApp'
import { MethodActions } from '@/MethodActions'
import { SourceScenes } from '@/SourceScenes'

// A refresh (poll/focus) re-reads every page already loaded instead of resetting to page one.
vi.mock('@/App', () => ({ App: () => <div>原只读看板</div> }))
const identity = { scope_id: 'scope', principal_id: 'ceo', display_name: '测试 CEO', auth_epoch: 1,
                   assignments: [{ role: 'CEO', domain_id: 'domain', assignment_id: 'assignment' }] }
const session = { identity, csrf: 'csrf' }
const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value),
  { status: 200, headers: { 'Content-Type': 'application/json' } }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/dashboard/') })

/** The session list readers' limit/after semantics over `all`. */
function paged<T>(all: T[], key: (item: T) => string, url: string) {
  const params = new URL(url, 'http://local').searchParams
  const limit = Number(params.get('limit') ?? 25)
  const after = params.get('after')
  const start = after ? all.findIndex((item) => key(item) === after) + 1 : 0
  const items = all.slice(start, start + limit)
  return { items, next_after: start + limit < all.length ? key(items[items.length - 1]) : null }
}
const ids = (prefix: string) => Array.from({ length: 30 }, (_, index) => `${prefix}-${String(index).padStart(2, '0')}`)

async function loginTo(route: (url: string) => unknown) {
  let loggedIn = false
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return json({ identity, csrf: 'csrf' }) }
    if (url.endsWith('/session')) return loggedIn ? json({ identity, csrf: 'csrf' })
      : Promise.resolve(new Response(JSON.stringify({ error: { code: 'UNAUTHENTICATED' } }), { status: 401 }))
    return json(route(url) ?? { items: [], next_after: null })
  }))
  render(<GovernanceApp />)
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
}

/** Load the second page, change its last item, refresh, and expect both pages under the fresh read. */
async function loadMoreThenRefresh(first: string, last: string, rename: () => void, renamed: string) {
  expect((await screen.findAllByText(first)).length).toBeGreaterThan(0)
  expect(screen.queryAllByText(last)).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: '加载更多' }))
  expect((await screen.findAllByText(last)).length).toBeGreaterThan(0)
  rename()
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect((await screen.findAllByText(renamed)).length).toBeGreaterThan(0)
  expect(screen.getAllByText(first).length).toBeGreaterThan(0)
  expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument()
}

it('keeps loaded todo pages when the list refreshes', async () => {
  const todos = ids('w').map((id) => ({ object_id: id, title: `窗口 ${id}`, phase: 'open', label: '参与共同核对',
                                         contract_version: 'tkos.method/0.3', actions: [] }))
  await loginTo((url) => url.includes('/governance/tasks') ? paged(todos, (t) => t.object_id, url) : undefined)
  await loadMoreThenRefresh('窗口 w-00', '窗口 w-29', () => { todos[29] = { ...todos[29], title: '窗口 w-29（已更新）' } },
                            '窗口 w-29（已更新）')
})

it('keeps loaded Mission pages when the list refreshes', async () => {
  const missions = ids('m').map((id) => ({ object_id: id, domain_id: 'domain',
    effective_revision: { revision_id: `${id}-r1`, payload_hash: 'h', payload: { title: `任务 ${id}` } } }))
  await loginTo((url) => url.includes('/governance/missions') ? paged(missions, (m) => m.object_id, url) : undefined)
  fireEvent.click(await screen.findByRole('button', { name: '正式 Mission' }))
  await loadMoreThenRefresh('任务 m-00', '任务 m-29', () => {
    missions[29] = { ...missions[29], effective_revision: { ...missions[29].effective_revision,
                                                            payload: { title: '任务 m-29（已更新）' } } }
  }, '任务 m-29（已更新）')
})

it('keeps loaded Method task pages when the list refreshes', async () => {
  const tasks = ids('t').map((id) => ({ object_id: id, object_type: 'StrategicAgreement', title: `事项 ${id}`,
                                        phase: 'awaiting_confirmation', contract_version: 'tkos.method/0.4', actions: [] }))
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => json(paged(tasks, (t) => t.object_id, String(input)))))
  render(<MethodActions session={session} prepare={vi.fn()} onError={vi.fn()} onExplore={vi.fn()} />)
  await loadMoreThenRefresh('事项 t-00', '事项 t-29', () => { tasks[29] = { ...tasks[29], title: '事项 t-29（已更新）' } },
                            '事项 t-29（已更新）')
})

it('keeps loaded source scene pages when the list refreshes', async () => {
  const scenes = ids('11111111-0000-0000-0000-0000000000').map((id) => ({ scene_id: id, scene_type: 'meeting',
    external_id: id, title: `场景 ${id.slice(-2)}`, owner_principal_id: 'ceo', participant_principal_ids: [],
    version: 1, created_at: '2026-09-17T00:00:00Z' }))
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => json(paged(scenes, (s) => s.scene_id, String(input)))))
  render(<SourceScenes session={session} prepare={vi.fn()} onError={vi.fn()} />)
  await loadMoreThenRefresh('场景 00', '场景 29', () => { scenes[29] = { ...scenes[29], title: '场景 29（已更新）' } },
                            '场景 29（已更新）')
})
