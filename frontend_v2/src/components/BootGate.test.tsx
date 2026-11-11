// 启动闸门 — 数据不完整时的入口拦截
//
// 两条要守住的线:
//   1. 有未完成修补的日K → 入口必须弹阻塞弹窗, 不能让用户进到算不动的页面;
//   2. 修补失败 (数据源不返回那几天的日K) → 必须还能退出, 否则用户被锁死在入口。
// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type DataStatus, type IntegrityReport, type PipelineJob } from '@/lib/api'
import { BootGate } from './BootGate'

vi.mock('@/lib/api', async importOriginal => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      dataStatus: vi.fn(),
      dataIntegrity: vi.fn(),
      pipelineJobs: vi.fn(),
      pipelineJob: vi.fn(),
      repairDataIntegrity: vi.fn(),
    },
  }
})

const STATUS = { daily: { trading_days: 100 }, enriched: { trading_days: 100 } } as unknown as DataStatus

const REPORT: IntegrityReport = {
  version: 1, id: 'r1', checked_at: '2026-11-11T00:00:00', start_date: '2026-10-13', end_date: '2026-11-10',
  lookback_days: 30, asset_types: ['stock'], status: 'issues', repairable_count: 1, uncertain_count: 0,
  no_data_assets: [],
  issues: [{
    asset_type: 'stock', asset_label: '股票', date: '2026-11-10', kind: 'snapshot',
    kind_label: '盘中快照未定版', reason: '', repairable: true, symbols: [], symbol_count: 2,
  }],
}

const JOB: PipelineJob = {
  id: 'j1', status: 'running', stage: 'sync_daily', progress: 40, stage_pct: 40, log: [],
  started_at: null, finished_at: null, duration_s: null, result: null, error: null,
}

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  sessionStorage.clear()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.mocked(api.dataStatus).mockResolvedValue(STATUS)
  vi.mocked(api.pipelineJobs).mockResolvedValue({ active_id: null, jobs: [] })
  vi.mocked(api.pipelineJob).mockResolvedValue(JOB)
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

async function render() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter><BootGate /></MemoryRouter>
      </QueryClientProvider>,
    )
  })
}

const dialog = () => host.querySelector('[role="dialog"]')

/** 查询是异步 resolve 的: 一格格冲微任务, 每格都包在 act 里 (否则 React 警告未包裹的更新) */
async function until(fn: () => boolean) {
  for (let i = 0; i < 30; i++) {
    if (fn()) return
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
  }
  expect(fn()).toBe(true)
}

async function click(text: string) {
  await until(() => [...host.querySelectorAll('button')].some(b => b.textContent?.includes(text)))
  const btn = [...host.querySelectorAll('button')].find(b => b.textContent?.includes(text))
  await act(async () => { btn!.click() })
}

it('日K有未完成修补 → 入口弹阻塞弹窗', async () => {
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: REPORT, blocking_assets: ['stock'] })
  // 修补请求悬而未决 → 弹窗停在「不完整 + 明细」态, 不会被运行态覆盖
  vi.mocked(api.repairDataIntegrity).mockReturnValue(new Promise(() => {}) as never)
  await render()
  await until(() => !!dialog())
  expect(host.textContent).toContain('本地数据不完整')
  expect(host.textContent).toContain('盘中快照未定版')
})

it('本地数据完整 → 不弹窗', async () => {
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: null, blocking_assets: [] })
  await render()
  await act(async () => { await Promise.resolve() })
  expect(dialog()).toBeFalsy()
})

it('入口自动起一次修补, 弹窗切到进度视图', async () => {
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: REPORT, blocking_assets: ['stock'] })
  vi.mocked(api.repairDataIntegrity).mockResolvedValue({ job_id: 'j1' })
  await render()
  await until(() => !!dialog())
  await until(() => vi.mocked(api.repairDataIntegrity).mock.calls.length > 0)
  expect(vi.mocked(api.repairDataIntegrity)).toHaveBeenCalledWith('r1')
  await until(() => host.textContent?.includes('同步日 K') ?? false)
})

it('修补失败 → 仍能退出, 不把用户锁在入口', async () => {
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: REPORT, blocking_assets: ['stock'] })
  vi.mocked(api.repairDataIntegrity).mockRejectedValue(new Error('数据源未返回 2026-11-10 的修补日K'))
  await render()
  await until(() => host.textContent?.includes('数据源未返回') ?? false)
  await click('仍要进入应用')
  expect(dialog()).toBeFalsy()
})
