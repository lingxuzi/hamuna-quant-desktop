// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { WatchlistGroupBar } from './WatchlistGroups'

vi.mock('@/lib/api', () => ({ api: {
  instrumentSearch: vi.fn(),
  preferences: vi.fn(async () => ({})),
} }))

const RESULTS = [
  { symbol: '600519.SH', code: '600519', name: '贵州茅台', asset_type: 'stock' },
  { symbol: '510300.SH', code: '510300', name: '沪深300ETF', asset_type: 'etf' },
]

let host: HTMLDivElement
let root: Root
let client: QueryClient
let added: string[]

/** 搜索框的宿主: 组件是受控的, 这里用 state 承接输入 */
function Harness() {
  const [query, setQuery] = useState('')
  return (
    <QueryClientProvider client={client}>
      <WatchlistGroupBar
        groups={[]}
        counts={{}}
        selected="all"
        total={0}
        onSelect={() => {}}
        onCreate={async () => {}}
        onRename={async () => {}}
        onDelete={async () => {}}
        searchQuery={query}
        onSearchChange={setQuery}
        watchlistSymbols={new Set(['600519.SH'])}
        onAddSymbol={(symbol) => { added.push(symbol) }}
      />
    </QueryClientProvider>
  )
}

function searchInput(): HTMLInputElement {
  const input = host.querySelector('input[type="search"]')
  if (!input) throw new Error('搜索框未渲染')
  return input as HTMLInputElement
}

/** React 的 onChange 挂在 input 事件上, 要走原生 setter 才能被 React 感知 */
async function typeQuery(text: string) {
  const input = searchInput()
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  // 等 React Query 把结果取回来 (queryFn 是 promise, 结果落在下一个微任务 + 一次渲染)
  await act(async () => { await new Promise(r => setTimeout(r, 0)) })
  await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.clearAllMocks()
  added = []
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.mocked(api.instrumentSearch).mockImplementation(async () => ({ results: RESULTS }))
  act(() => { root.render(<Harness />) })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

it('输入后弹出全市场搜索结果', async () => {
  expect(host.textContent).not.toContain('贵州茅台')
  await typeQuery('茅')
  expect(host.textContent).toContain('贵州茅台')
  expect(host.textContent).toContain('沪深300ETF')
  // 已在自选的标的不再显示「加入自选」按钮
  expect(host.querySelector('[aria-label="加入自选 600519.SH"]')).toBeNull()
  expect(host.querySelector('[aria-label="加入自选 510300.SH"]')).not.toBeNull()
})

it('点「+」把搜索到的标的交给宿主加入自选', async () => {
  await typeQuery('ETF')
  const btn = host.querySelector('[aria-label="加入自选 510300.SH"]') as HTMLButtonElement
  await act(async () => { btn.click() })
  expect(added).toEqual(['510300.SH'])
})

it('搜索无结果时给出提示', async () => {
  vi.mocked(api.instrumentSearch).mockImplementation(async () => ({ results: [] }))
  await typeQuery('不存在')
  expect(host.textContent).toContain('未找到匹配的标的')
})
