// @vitest-environment jsdom
/**
 * 侧栏分组归属 — 专家归「研究」组末尾, 「系统」组只留数据与设置。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Layout } from './Layout'

vi.mock('@/lib/api', () => ({ api: {
  dataSources: vi.fn(async () => ({ builtin: [], custom: [], plugins: [] })),
  analysisMenus: vi.fn(async () => ({ items: [] })),
  indexQuotes: vi.fn(async () => ({ rows: [] })),
  watchlistGroups: vi.fn(async () => ({ groups: [] })),
  watchlistList: vi.fn(async () => ({ symbols: [] })),
  watchlistEnriched: vi.fn(async () => ({ rows: [] })),
  pipelineJobs: vi.fn(async () => ({ active_id: null, items: [] })),
  dataIntegrity: vi.fn(async () => ({ report: null, blocking_assets: [], data_revisions: {} })),
  alertsList: vi.fn(async () => ({ total: 0 })),
  quoteStatus: vi.fn(),
  updateRealtimeQuotes: vi.fn(async () => ({ realtime_quotes_enabled: false, realtime_allowed: false })),
  miningRuns: vi.fn(async () => ({ items: [] })),
  intradayRefresh: vi.fn(async () => ({})),
} }))
vi.mock('@/lib/useSharedQueries', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/useSharedQueries')>(),
  useSettings: () => ({ data: { onboarding_completed: true } }),
  useCapabilityMatrix: () => ({ data: { capabilities: [] } }),
  usePreferences: () => ({ data: { realtime_data_provider: 'fuyao', realtime_quotes_enabled: false } }),
  useVersion: () => ({ data: { version: '0.3.20' } }),
}))
vi.mock('@/lib/useQuoteStream', () => ({
  useQuoteStream: () => {}, useQuoteStreamStatus: () => 'connected',
}))
vi.mock('@/lib/updateCheck', () => ({ useUpdateCheck: () => ({ status: 'latest' }) }))
vi.mock('@/lib/useMediaQuery', () => ({ useIsDesktop: () => true, useMediaQuery: () => true }))
vi.mock('@/extensions/registry', () => ({
  getFrontendExtensionNavigation: () => [{
    id: 'experts', routeId: 'experts', label: '专家', icon: () => null,
    route: { id: 'experts', path: '/experts' }, extensionId: 'assistant.chat',
  }],
}))
vi.mock('@/extensions/ExtensionSlot', () => ({ ExtensionSlot: () => null }))
vi.mock('@/components/Toast', () => ({ ToastContainer: () => null, toast: vi.fn() }))
vi.mock('@/components/AlertToast', () => ({ AlertToastContainer: () => null }))
vi.mock('@/components/financials/AiAnalysisHost', () => ({ AiAnalysisHost: () => null }))
vi.mock('@/components/financials/AiReportBubble', () => ({ AiReportBubble: () => null }))
vi.mock('@/components/stock-analysis/StockAnalysisHost', () => ({ StockAnalysisHost: () => null }))
vi.mock('@/components/stock-analysis/StockAnalysisBubble', () => ({ StockAnalysisBubble: () => null }))

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.clearAllMocks()
  localStorage.clear()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

/** 侧栏分组: 组名 → 该组条目路径 */
async function navGroups(): Promise<Record<string, string[]>> {
  await act(async () => root.render(
    <MemoryRouter><QueryClientProvider client={client}><Layout /></QueryClientProvider></MemoryRouter>,
  ))
  const out: Record<string, string[]> = {}
  for (const group of Array.from(host.querySelectorAll('.toc-g'))) {
    const name = group.querySelector('.toc-g-name')?.textContent ?? ''
    out[name] = Array.from(group.querySelectorAll('a')).map(a => a.getAttribute('href') ?? '')
  }
  return out
}

it('专家排在「研究」组末尾', async () => {
  const groups = await navGroups()
  expect(groups['研究']?.at(-1)).toBe('/experts')
})

it('「系统」组只留数据与设置', async () => {
  const groups = await navGroups()
  expect(groups['系统']).toEqual(['/data', '/settings'])
})
