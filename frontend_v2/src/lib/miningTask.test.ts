import { beforeEach, describe, expect, it, vi } from 'vitest'

/** startAutoMining: POST 立即返回 screening_id → 轮询筛选作业 → 接管 run / 报无达标因子。 */

const mocks = vi.hoisted(() => ({
  autoStart: vi.fn(),
  screening: vi.fn(),
  run: vi.fn(),
  result: vi.fn(),
}))

vi.mock('./api', () => ({
  api: {
    miningAutoStart: mocks.autoStart,
    miningScreening: mocks.screening,
    miningRun: mocks.run,
    miningResult: mocks.result,
  },
}))

vi.mock('./taskRegistry', () => ({
  fetchRunningTask: vi.fn().mockResolvedValue(null),
  reportTaskFinished: vi.fn(),
  reportTaskRunning: vi.fn(),
}))

const _screening = (pool: string[], reason_counts: Record<string, number> = {}) => ({
  profile: 'exploratory' as const,
  gate: { min_abs_ic: 0.02, min_abs_ir: 0.15, min_abs_t: 1.5, max_q: 0.2 },
  screen_window: { start: '2025-08-31', end: '2026-08-31' },
  n_total: 61,
  n_qualified: pool.length,
  pool,
  pool_truncated: false,
  qualified: [],
  failed: [],
  reason_counts,
  elapsed_ms: 1,
})

function job(patch: Record<string, any>) {
  return {
    screening_id: 'scr_1', status: 'running', label: '正在筛选因子（7/61）',
    done: 7, total: 61, started: false, reason: null, error: null,
    screening: null, ...patch,
  }
}

describe('startAutoMining · L1 筛选后台作业', () => {
  beforeEach(() => {
    for (const fn of Object.values(mocks)) fn.mockReset()
    mocks.autoStart.mockResolvedValue({ started: true, screening_id: 'scr_1', status: 'queued' })
  })

  it('轮询筛选进度, run 创建后接管', async () => {
    const { startAutoMining, getMiningTask } = await import('./miningTask')
    mocks.screening
      .mockResolvedValueOnce(job({}))
      .mockResolvedValue(job({
        status: 'succeeded', label: '筛选完成，挖掘任务已启动', done: 61, total: 61,
        started: true, screening: _screening(['f1']),
        run: { run_id: 'run-1', status: 'running', progress: null } as any,
      }))
    // 接管后走 SSE, 测试里不需要真的连上
    vi.stubGlobal('EventSource', class { close() {} addEventListener() {} } as any)

    await startAutoMining({ asset_type: 'stock' })

    const task = getMiningTask()
    expect(task.runId).toBe('run-1')
    expect(task.error).toBeNull()
    expect(mocks.screening).toHaveBeenCalledTimes(2)
    vi.unstubAllGlobals()
  })

  it('筛选过程中进度写进 progress.label', async () => {
    const { startAutoMining, getMiningTask } = await import('./miningTask')
    let sawLabel = ''
    let calls = 0
    mocks.screening.mockImplementation(async () => {
      calls += 1
      // 第二次轮询时读到的是第一次响应写入的 label
      sawLabel = getMiningTask().progress?.label ?? ''
      return calls === 1 ? job({}) : job({ status: 'failed', error: '筛选失败' })
    })

    await startAutoMining({ asset_type: 'stock' })

    expect(sawLabel).toBe('正在筛选因子（7/61）')
    expect(getMiningTask().error).toBe('筛选失败')
  })

  it('无达标因子: 不建任务, 用原因分布报错', async () => {
    const { startAutoMining, getMiningTask } = await import('./miningTask')
    mocks.screening.mockResolvedValue(job({
      status: 'succeeded', started: false, reason: 'no_qualified_factors',
      screening: _screening([], { 预测力弱: 40, 稳定度低: 21 }),
    }))

    await startAutoMining({ asset_type: 'stock' })

    const task = getMiningTask()
    expect(task.runId).toBeNull()
    expect(task.isPending).toBe(false)
    expect(task.error).toContain('0 个达标')
    expect(task.error).toContain('预测力弱 40 个')
  })
})
