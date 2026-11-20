import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  listeners: new Set<() => void>(),
  startAutoMining: vi.fn(),
  buildStream: vi.fn(),
  task: null as any,
}))

vi.mock('./miningTask', () => ({
  startAutoMining: mocks.startAutoMining,
  subscribeMiningTask: (fn: () => void) => {
    mocks.listeners.add(fn)
    return () => { mocks.listeners.delete(fn) }
  },
  getMiningTask: () => mocks.task,
}))

vi.mock('./api', () => ({
  api: { marketAwareBuildStream: mocks.buildStream },
}))

/** 推进挖掘任务快照并通知订阅方。 */
function pushMiningTask(patch: any) {
  mocks.task = { ...mocks.task, ...patch }
  mocks.listeners.forEach(fn => fn())
}

describe('marketAwareStore', () => {
  beforeEach(async () => {
    mocks.buildStream.mockReset()
    mocks.startAutoMining.mockReset()
    mocks.task = { runId: null, isPending: true, progress: null, run: null, error: null }
    const store = await import('./marketAwareStore')
    store.startMarketAwareGeneration(null)
  })

  it('挖掘成功后自动合并生成策略, 阶段进日志', async () => {
    const { getMarketAwareState } = await import('./marketAwareStore')
    mocks.buildStream.mockImplementation(async function* () {
      yield { type: 'stage', key: 'ai', label: 'AI 生成入场 / 离场与风控参数' }
      yield { type: 'delta', content: '{"name":"市场感知策略"}' }
      yield {
        type: 'done',
        result: {
          ok: true,
          strategy_id: 'custom_market_aware_abc',
          decisions: { name: '市场感知策略' },
          champion: { factors: {}, directions: {} },
          facts: {},
        },
      }
    })

    expect(getMarketAwareState().phase).toBe('mining')
    expect(mocks.startAutoMining).toHaveBeenCalledTimes(1)

    pushMiningTask({
      runId: 'run-1',
      isPending: false,
      progress: { phase: 'running', label: '正在评估候选组合' },
      run: { status: 'succeeded' },
    })

    await vi.waitFor(() => expect(getMarketAwareState().phase).toBe('done'))
    const state = getMarketAwareState()
    expect(state.result?.strategy_id).toBe('custom_market_aware_abc')
    expect(state.aiText).toContain('市场感知策略')
    expect(state.logs.map(entry => entry.text)).toContain('AI 生成入场 / 离场与风控参数')
    expect(state.logs.some(entry => entry.level === 'ok')).toBe(true)
  })

  it('流内错误事件把阶段切到 error 并写进日志', async () => {
    const { getMarketAwareState } = await import('./marketAwareStore')
    mocks.buildStream.mockImplementation(async function* () {
      yield { type: 'error', message: 'AI 生成失败: 模型不可用' }
    })

    pushMiningTask({ runId: 'run-2', isPending: false, run: { status: 'succeeded' } })

    await vi.waitFor(() => expect(getMarketAwareState().phase).toBe('error'))
    expect(getMarketAwareState().error).toContain('模型不可用')
    expect(getMarketAwareState().logs.some(entry => entry.level === 'error')).toBe(true)
  })

  it('新一轮开始时忽略上一轮遗留的挖掘失败', async () => {
    const store = await import('./marketAwareStore')
    mocks.task = {
      runId: null, isPending: false, progress: null, run: null, error: '上一轮筛选失败',
    }
    store.startMarketAwareGeneration(null)
    expect(store.getMarketAwareState().phase).toBe('mining')
    expect(store.getMarketAwareState().error).toBe(null)
  })
})
