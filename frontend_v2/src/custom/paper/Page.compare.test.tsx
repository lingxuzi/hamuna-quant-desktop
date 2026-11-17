// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Paper } from './Page'

const row = {
  account: 'acc-a', name: '账户A', status: 'active', initial_cash: 1_000_000,
  fees: { commission_pct: 0.0002, stamp_tax_pct: 0.001, slippage_bps: 5 },
  total: 1_000_000, cash: 1_000_000, market_value: 0, total_pnl: 0, pnl_pct: 0,
  rounds: 0, win_rate: 0, profit_loss_ratio: null, avg_holding_days: 0,
  realized_pnl: 0, max_drawdown: 0, nav: [], holdings_count: 0, auto_rules: [],
}

vi.mock('echarts', () => ({
  init: () => ({ setOption: () => {}, resize: () => {}, dispose: () => {}, on: () => {} }),
}))

vi.mock('@/lib/api', () => ({
  api: {
    paperCompare: vi.fn(async () => ({ accounts: [row] })),
    paperFreeze: vi.fn(async () => ({ account: { account: 'acc-a', status: 'frozen' } })),
    paperDeleteAccount: vi.fn(async () => ({ ok: true, deleted: 'acc-a' })),
    quoteStatus: vi.fn(async () => ({ is_trading_hours: false })),
    quoteDate: vi.fn(async () => ({ date: '2026-11-17' })),
    quoteRunning: vi.fn(async () => ({ running: false })),
  },
}))

vi.mock('./executionClient', async importOriginal => ({
  ...await importOriginal<typeof import('./executionClient')>(),
  executionApi: { list: vi.fn(async () => ({ bindings: [] })) },
}))

let host: HTMLDivElement
let root: Root
let qc: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  qc.clear()
  host.remove()
  vi.clearAllMocks()
})

async function flush() { await act(async () => { await new Promise(r => setTimeout(r, 20)) }) }

// 操作列第三个控件是展开箭头: 原为裸 svg 无 onClick, 点它永远不展开 (冒泡被 td
// 的 stopPropagation 截断)。这里锁住「点箭头能展开账户面板」, 防止回归。
it('操作列: 展开箭头点击后渲染账户面板', async () => {
  await act(async () => root.render(<QueryClientProvider client={qc}><Paper /></QueryClientProvider>))
  await flush()

  const arrow = host.querySelector('td button[aria-label="展开账户操作"]') as HTMLButtonElement
  expect(arrow).toBeTruthy()
  expect(host.querySelectorAll('tbody tr')).toHaveLength(1)

  await act(async () => arrow.click())
  await flush()
  expect(arrow.getAttribute('aria-expanded')).toBe('true')
  // 展开 = 额外一行面板 (面板内数据接口未 mock, 只验面板确实挂载)
  expect(host.querySelectorAll('tbody tr')).toHaveLength(2)
})
