// @vitest-environment jsdom
// 系统设置面板渲染冒烟 + 「版权与许可」入口检查 (该区块从已删除的关于页迁入)。
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import { SettingsSystemPanel } from './System'

const mocks = vi.hoisted(() => ({ guard: vi.fn(), sounds: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: {
  upgradeGuardStatus: mocks.guard,
  listSoundPacks: mocks.sounds,
  updateRealtimeMonitorConfig: vi.fn(),
  updateDailyBatchSymbols: vi.fn(),
  updateMinuteBatchSymbols: vi.fn(),
  updateBacktestWorkerRssLimit: vi.fn(),
  upgradeGuardScan: vi.fn(),
  upgradeGuardRestore: vi.fn(),
  upgradeGuardDismiss: vi.fn(),
  uploadSoundPack: vi.fn(),
  deleteSoundPack: vi.fn(),
} }))
vi.mock('@/lib/useSharedQueries', () => ({
  usePreferences: () => ({ data: {
    screener_auto_run: true,
    daily_batch_symbols: 400,
    minute_batch_symbols: 400,
    backtest_worker_rss_limit_gib: 6,
  } }),
}))
vi.mock('@/extensions/ExtensionSlot', () => ({ ExtensionSlot: () => null }))

let root: Root
const node = document.createElement('div')
document.body.appendChild(node)

async function render() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  root = createRoot(node)
  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={qc}><SettingsSystemPanel /></QueryClientProvider></MemoryRouter>
  ))
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.guard.mockReset()
  mocks.sounds.mockReset()
  mocks.guard.mockResolvedValue({ enabled: false })
  mocks.sounds.mockResolvedValue([])
})

it('渲染版权与许可区块, 并可打开许可弹窗', async () => {
  await render()

  expect(node.textContent).toContain('版权与许可')
  expect(node.textContent).toContain('tick-stock-panel contributors')
  // TradingView 署名与链接必须留在用户可达页面
  expect(node.textContent).toContain('TradingView Lightweight Charts')

  const btn = [...node.querySelectorAll('button')].find(el => el.textContent?.trim() === '查看许可')
  expect(btn).toBeTruthy()
  await act(async () => btn!.click())

  expect(node.textContent).toContain('许可合规声明')
  expect(node.textContent).toContain('前端组件')
})
