// 启动闸门 — 数据不完整 / 缺失时的入口拦截
//
// 三条要守住的线:
//   1. 有未完成修补的日K → 入口必须弹阻塞弹窗, 不能让用户进到算不动的页面;
//   2. 本地一条日K都没有 → 同样弹窗, 并跑一次全量同步 (不能放行到空页面);
//   3. 弹窗不可关闭 (无 X / ESC / 遮罩 /「仍要进入应用」); 失败后自动退避重试,
//      活路是修好数据或退出应用, 不是绕过闸门。
// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type DataStatus, type IntegrityReport, type PipelineJob } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
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
      pipelineRun: vi.fn(),
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
  vi.clearAllMocks()   // 调用记录不跨用例累积 (实现不受影响, 下面重新设)
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

it('修补失败 → 弹窗保留且无关闭口, 只能重试', async () => {
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: REPORT, blocking_assets: ['stock'] })
  vi.mocked(api.repairDataIntegrity).mockRejectedValue(new Error('数据源未返回 2026-11-10 的修补日K'))
  await render()
  await until(() => host.textContent?.includes('数据源未返回') ?? false)
  expect(dialog()).toBeTruthy()   // 修补失败后弹窗不能被关掉
  expect(host.textContent).not.toContain('仍要进入应用')   // 不留绕过闸门的出口
  await click('立即重试')
  await until(() => vi.mocked(api.repairDataIntegrity).mock.calls.length >= 2)
})

it('弹窗不可关闭: ESC 也不放行', async () => {
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: REPORT, blocking_assets: ['stock'] })
  vi.mocked(api.repairDataIntegrity).mockReturnValue(new Promise(() => {}) as never)
  await render()
  await until(() => !!dialog())
  await act(async () => {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  })
  expect(dialog()).toBeTruthy()
})

it('本地没有日K → 弹窗并跑一次全量同步', async () => {
  vi.mocked(api.dataStatus).mockResolvedValue({
    daily: { trading_days: 0 }, enriched: { trading_days: 0 },
  } as unknown as DataStatus)
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: null, blocking_assets: [] })
  vi.mocked(api.pipelineRun).mockResolvedValue({ job_id: 'j1', reused: false })
  await render()
  await until(() => !!dialog())
  await until(() => vi.mocked(api.pipelineRun).mock.calls.length > 0)
  expect(vi.mocked(api.repairDataIntegrity)).not.toHaveBeenCalled()
})

it('修复进行中 (统计处于中间态) 弹窗不消失', async () => {
  vi.mocked(api.dataStatus).mockResolvedValue({
    daily: { trading_days: 0 }, enriched: { trading_days: 0 },
  } as unknown as DataStatus)
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: null, blocking_assets: [] })
  vi.mocked(api.pipelineRun).mockResolvedValue({ job_id: 'j1', reused: false })
  await render()
  await until(() => !!dialog())

  // 后端开始同步 → 统计进入中间态 (stats_pending, 已有部分行)
  vi.mocked(api.dataStatus).mockResolvedValue({
    stats_pending: true, daily: { trading_days: 3 }, enriched: { trading_days: 3 },
  } as unknown as DataStatus)
  await act(async () => { await client.invalidateQueries({ queryKey: QK.dataStatus }) })
  await until(() => vi.mocked(api.dataStatus).mock.calls.length > 1)

  expect(dialog()).toBeTruthy(), '修复进行中弹窗消失了, 用户能带着半份数据进应用'
})

it('无数据时「我知道了」放行并跳到数据页', async () => {
  vi.mocked(api.dataStatus).mockResolvedValue({
    daily: { trading_days: 0 }, enriched: { trading_days: 0 },
  } as unknown as DataStatus)
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: null, blocking_assets: [] })
  // 本会话已自动起过一次同步 (标记在) → 不再自动起, 停在「待用户处理」态, 放行按钮可见
  sessionStorage.setItem('tf_boot_gate_pipeline', '1')
  await render()
  await until(() => !!dialog())
  await click('我知道了')
  await until(() => !dialog())
  expect(dialog()).toBeFalsy()
})

it('数据不完整时不给「我知道了」出口', async () => {
  vi.mocked(api.dataIntegrity).mockResolvedValue({ report: REPORT, blocking_assets: ['stock'] })
  vi.mocked(api.repairDataIntegrity).mockReturnValue(new Promise(() => {}) as never)
  await render()
  await until(() => host.textContent?.includes('本地数据不完整') ?? false)
  expect(host.textContent).not.toContain('我知道了')
})
