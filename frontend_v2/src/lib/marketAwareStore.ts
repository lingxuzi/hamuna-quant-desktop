/** 市场感知生成的模块级 store — 弹窗只是它的一个视图。
 *
 * 为什么放模块级: 「挖掘 → AI 合并 → 落盘」是长任务 (分钟级), 弹窗关闭/最小化
 * 后必须继续跑完并保留日志, 否则关掉弹窗就等于丢掉整条链路 (个股分析同理)。
 *
 * 事件源:
 * - 挖掘进度来自挖掘任务 store (SSE/轮询), 这里订阅它的快照;
 * - AI 合并阶段来自后端 NDJSON 阶段流 (/strategies/market-aware/build)。
 */
import { useSyncExternalStore } from 'react'
import { api, type MarketAwareBuildResult, type MarketDimension } from './api'
import { cnHms } from './cnDate'
import { getMiningTask, startAutoMining, subscribeMiningTask } from './miningTask'

export type MarketAwarePhase = 'idle' | 'mining' | 'building' | 'done' | 'error'

export interface MarketAwareLogEntry {
  at: string
  text: string
  level: 'info' | 'ok' | 'error'
}

export interface MarketAwareState {
  open: boolean
  minimized: boolean
  phase: MarketAwarePhase
  logs: MarketAwareLogEntry[]
  /** AI 原始输出 (JSON 文本片段), 供展开查看 */
  aiText: string
  result: MarketAwareBuildResult | null
  error: string | null
  assetType: 'stock' | 'etf'
  dimensions: MarketDimension[]
  /** 本轮开始时刻 (毫秒), 用于显示已用时 */
  startedAt: number | null
}

export const MARKET_AWARE_WINDOW_DAYS = 120
const MINING_PROFILE = 'exploratory'

let state: MarketAwareState = {
  open: false,
  minimized: false,
  phase: 'idle',
  logs: [],
  aiText: '',
  result: null,
  error: null,
  assetType: 'stock',
  dimensions: ['market'],
  startedAt: null,
}

const listeners = new Set<() => void>()

function emit() {
  listeners.forEach(listener => listener())
}

function set(patch: Partial<MarketAwareState>) {
  state = { ...state, ...patch }
  emit()
}

function log(text: string, level: MarketAwareLogEntry['level'] = 'info') {
  set({ logs: [...state.logs, { at: cnHms(new Date()), text, level }] })
}

/** 本轮跟踪的挖掘 run (只认本弹窗发起的那个)。 */
let pendingRunId: string | null = null
let buildStarted = false
let lastProgressLabel = ''
/** 本轮是否已真的发起挖掘 —— 用来屏蔽上一轮遗留的任务状态。 */
let ownRunStarted = false

async function runBuild(runId: string, assetType: 'stock' | 'etf', dimensions: MarketDimension[]) {
  set({ phase: 'building' })
  log('开始合并市场事实与 AI 决策')
  try {
    for await (const event of api.marketAwareBuildStream({
      run_id: runId,
      dimensions,
      asset_type: assetType,
      window_days: MARKET_AWARE_WINDOW_DAYS,
    })) {
      if (event.type === 'stage') {
        log(event.label)
      } else if (event.type === 'delta') {
        set({ aiText: state.aiText + event.content })
      } else if (event.type === 'error') {
        set({ phase: 'error', error: event.message })
        log(event.message, 'error')
        return
      } else if (event.type === 'done') {
        set({ phase: 'done', result: event.result })
        log(`策略已生成：${event.result.decisions.name || event.result.strategy_id}`, 'ok')
      }
    }
  } catch (error) {
    const message = String((error as Error)?.message || error)
    set({ phase: 'error', error: message })
    log(message, 'error')
  }
}

// 挖掘任务的任何变化都从这里过一遍: 进度标签进日志, 终态触发 AI 合并。
subscribeMiningTask(() => {
  // 只认自己发起的这一轮: 打开弹窗前就已在跑的任务 (或上一轮的失败) 不算数
  if (state.phase !== 'mining' || !ownRunStarted) return
  const task = getMiningTask()
  const label = task.progress?.label
  if (label && label !== lastProgressLabel) {
    lastProgressLabel = label
    log(label)
  }
  if (task.runId && !pendingRunId) pendingRunId = task.runId
  if (!pendingRunId) {
    // 还没创建 run: 筛选阶段直接失败 (无达标因子)
    if (!task.isPending && task.error) {
      set({ phase: 'error', error: task.error })
      log(task.error, 'error')
    }
    return
  }
  const status = task.run?.status
  if (status === 'succeeded' || status === 'succeeded_with_budget_exhausted') {
    if (!buildStarted) {
      buildStarted = true
      void runBuild(pendingRunId, state.assetType, state.dimensions)
    }
    return
  }
  if (status === 'failed' || status === 'cancelled' || status === 'interrupted' || status === 'skipped_prerequisite') {
    const message = task.run?.error || task.error || '挖掘未成功结束'
    set({ phase: 'error', error: message })
    log(message, 'error')
  }
})

export function useMarketAware(): MarketAwareState {
  return useSyncExternalStore(
    listener => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => state,
    () => state,
  )
}

/** 读取当前快照 (非 React 消费方/测试用)。 */
export function getMarketAwareState(): MarketAwareState {
  return state
}

export function openMarketAware() {
  set({ open: true, minimized: false })
}

export function closeMarketAware() {
  set({ open: false })
}

/** 缩小到后台: 弹窗收起, 任务继续跑, 由悬浮气泡显示进度。 */
export function minimizeMarketAware() {
  set({ minimized: true })
}

export function restoreMarketAware() {
  set({ open: true, minimized: false })
}

export function setMarketAwareAssetType(assetType: 'stock' | 'etf') {
  set({ assetType })
}

export function toggleMarketAwareDimension(id: MarketDimension) {
  const dimensions = state.dimensions.includes(id)
    ? state.dimensions.filter(item => item !== id)
    : [...state.dimensions, id]
  set({ dimensions })
}

/** 发起一轮市场感知生成 (挖掘 → AI 合并 → 落盘)。 */
export function startMarketAwareGeneration(symbols?: string[] | null) {
  const assetType = state.assetType
  const dimensions = state.dimensions
  pendingRunId = null
  buildStarted = false
  lastProgressLabel = ''
  ownRunStarted = false
  set({
    phase: 'mining',
    logs: [],
    aiText: '',
    result: null,
    error: null,
    startedAt: Date.now(),
  })
  log('开始挖掘因子组合')
  ownRunStarted = true
  void startAutoMining({
    asset_type: assetType,
    budget_profile: MINING_PROFILE,
    symbols: dimensions.includes('sector') ? symbols ?? null : null,
  })
}
