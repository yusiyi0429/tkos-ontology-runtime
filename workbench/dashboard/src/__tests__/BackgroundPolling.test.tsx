import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { GovernanceApp } from '@/GovernanceApp'
import { useLiveResource } from '@/lib/live'
import { MethodActions } from '@/MethodActions'
import { SourceScenes } from '@/SourceScenes'

// Timer-driven polls carry the background marker so they never extend the
// session's idle limit; the person's own reads, focus returns and writes do not.
vi.mock('@/App', () => ({ App: () => <div>原只读看板</div> }))
const identity = { scope_id: 'scope', principal_id: 'ceo', display_name: '测试 CEO', auth_epoch: 1,
                   assignments: [{ role: 'CEO', domain_id: 'domain', assignment_id: 'assignment' }] }
const json = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json' } }))
beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { vi.useRealTimers(); cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/dashboard/') })

type Call = { url: string; method: string; background: boolean }
function record(respond: (url: string, init?: RequestInit) => Promise<Response>) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const headers = (init?.headers ?? {}) as Record<string, string>
    calls.push({ url: String(input), method: init?.method ?? 'GET', background: headers['X-TKOS-Background'] === '1' })
    return respond(String(input), init)
  }))
  return calls
}

/** Advance one poll period and wait until every expected poll has been sent. */
async function nextPoll(calls: Call[], expected: RegExp[]) {
  const before = calls.length
  await act(async () => { vi.advanceTimersByTime(5000) })
  await waitFor(() => expect(expected.every((pattern) =>
    calls.slice(before).some((call) => pattern.test(call.url)))).toBe(true))
  return calls.slice(before)
}

async function focusReturn(calls: Call[]) {
  const before = calls.length
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  await waitFor(() => expect(calls.length).toBeGreaterThan(before))
  return calls.slice(before)
}

it('useLiveResource passes background only for the interval poll', async () => {
  const fetcher = vi.fn(async (key: string, _signal: AbortSignal, _background: boolean) => ({ id: key }))
  renderHook(() => useLiveResource({ key: 'a', fetcher, identity: (data: { id: string }) => data.id }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  expect(fetcher.mock.calls[0][2]).toBe(false)
  await act(async () => { vi.advanceTimersByTime(5000) })
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  expect(fetcher.mock.calls[1][2]).toBe(true)
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3))
  expect(fetcher.mock.calls[2][2]).toBe(false)
})

it('the workbench marks its session and list polls, never the login, own reads or focus returns', async () => {
  let loggedIn = false
  const calls = record((url, init) => {
    if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return json({ identity, csrf: 'csrf' }) }
    if (url.endsWith('/session')) return loggedIn ? json({ identity, csrf: 'csrf' }) : json({ error: { code: 'UNAUTHENTICATED' } }, 401)
    return json({ items: [], next_after: null })
  })
  render(<GovernanceApp />)
  fireEvent.change(await screen.findByLabelText('用户名'), { target: { value: 'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target: { value: 'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name: /^登录$/ }))
  await screen.findByText('当前没有待处理事项')
  expect(calls.some((call) => call.background)).toBe(false)
  const polled = await nextPoll(calls, [/\/session$/, /\/governance\/tasks/])
  expect(polled.every((call) => call.background && call.method === 'GET')).toBe(true)
  const woke = await focusReturn(calls)
  expect(woke.every((call) => !call.background)).toBe(true)
})

it('the Method task and source scene polls are marked too', async () => {
  const calls = record((url) => json(url.includes('/governance/sources') || url.includes('/governance/method/tasks')
    ? { items: [], next_after: null } : {}))
  render(<>
    <MethodActions session={{ identity, csrf: 'csrf' }} prepare={vi.fn()} onError={vi.fn()} onExplore={vi.fn()} />
    <SourceScenes session={{ identity, csrf: 'csrf' }} prepare={vi.fn()} onError={vi.fn()} />
  </>)
  await screen.findByTestId('method-actions-empty')
  await screen.findByTestId('source-empty')
  expect(calls.some((call) => call.background)).toBe(false)
  const polled = await nextPoll(calls, [/\/governance\/method\/tasks/, /\/governance\/sources/])
  expect(polled.every((call) => call.background)).toBe(true)
  const woke = await focusReturn(calls)
  expect(woke.every((call) => !call.background)).toBe(true)
})
