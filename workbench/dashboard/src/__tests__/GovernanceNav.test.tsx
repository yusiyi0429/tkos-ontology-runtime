import { fireEvent, render, screen, cleanup, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GovernanceApp } from '@/GovernanceApp'
vi.mock('@/App', () => ({ App: () => <div>原只读看板</div> }))

const identity = { scope_id:'scope', principal_id:'ceo', display_name:'测试 CEO', auth_epoch:1,
                   assignments:[{ role:'CEO', domain_id:'domain', assignment_id:'assignment' }] }
const response = (value:unknown, status=200) =>
  Promise.resolve(new Response(JSON.stringify(value), { status, headers:{ 'Content-Type':'application/json' } }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState(null,'','/dashboard/') })

async function signIn() {
  let loggedIn = false
  vi.stubGlobal('fetch', vi.fn((url:string, init?:RequestInit) => {
    if (url.endsWith('/session') && init?.method === 'POST') { loggedIn = true; return response({ identity, csrf:'c' }) }
    if (url.endsWith('/session')) return loggedIn ? response({ identity, csrf:'c' }) : response({ error:{ code:'UNAUTHENTICATED' } }, 401)
    return response({ items:[], next_after:null })
  }))
  render(<GovernanceApp/>)
  fireEvent.change(await screen.findByLabelText('用户名'), { target:{ value:'ceo' } })
  fireEvent.change(screen.getByLabelText('个人登录码'), { target:{ value:'personal-code-for-test' } })
  fireEvent.click(screen.getByRole('button', { name:/^登录$/ }))
  await screen.findByText('需要我处理的事项')
}

const PURPOSES = ['处理核对与确认', '查看业务与依据', '理解本体与规则', '管理来源与追溯']

describe('purpose-grouped navigation', () => {
  it('groups the sidebar by what the reader came to do', async () => {
    await signIn()
    for (const purpose of PURPOSES) {
      expect(screen.getByText(purpose)).toBeInTheDocument()
    }
  })

  it('keeps every existing destination reachable under a purpose', async () => {
    await signIn()
    for (const label of ['我的待办','方法事项','正式 Mission','业务关系图','本体地图','业务定义','独立来源','我的提交']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('still lands on personal tasks, not the purpose home', async () => {
    await signIn()
    expect(screen.getByRole('button', { name:'我的待办' })).toHaveAttribute('aria-current', 'page')
  })

  it('purpose home routes into the real destination for each purpose', async () => {
    await signIn()
    fireEvent.click(screen.getByRole('button', { name:'目的首页' }))
    expect(screen.getByText('看懂治理：从战略到任务的每一步都留痕')).toBeInTheDocument()
    // Each purpose card offers its own destinations; following one switches tab.
    const cards = screen.getAllByRole('heading', { level:3 })
    expect(cards.map(node => node.textContent)).toEqual(PURPOSES)
    // Follow a destination from its own purpose card, not from the sidebar.
    const viewing = cards[1].closest('section')!
    fireEvent.click(within(viewing).getByRole('button', { name:'正式 Mission' }))
    expect(screen.getByText('正式 Mission · 待执行承接')).toBeInTheDocument()
  })

  it('shows committing as its own step, which the CEO cannot perform for others', async () => {
    await signIn()
    const path = screen.getByLabelText('共同核对流程')
    expect([...path.querySelectorAll('li')].map(node => node.textContent?.replace(/^\d/, '')))
      .toEqual(['固定版本评论', 'Co-agent 收拢', '本人承诺', 'CEO 整组确认', '正式 Mission'])
  })
})
