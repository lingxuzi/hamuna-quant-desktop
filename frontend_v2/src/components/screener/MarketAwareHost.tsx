import { AlertCircle, Check, Loader2, Radar } from 'lucide-react'
import { restoreMarketAware, useMarketAware } from '@/lib/marketAwareStore'
import { MarketAwareDialog } from './MarketAwareDialog'

/** 市场感知生成宿主 —— 弹窗 + 后台气泡, 单点挂载在 Layout。
 *
 * 气泡贴右下角 (个股分析/财务分析胶囊在其上方), 任务在后台跑时显示进度,
 * 点一下把弹窗叫回来。
 */
export function MarketAwareHost() {
  const { open, minimized } = useMarketAware()
  const dialogVisible = open && !minimized
  return (
    <>
      {dialogVisible && <MarketAwareDialog />}
      {!dialogVisible && <MarketAwareBubble />}
    </>
  )
}

function MarketAwareBubble() {
  const { phase, minimized, logs, result } = useMarketAware()
  const isWorking = phase === 'mining' || phase === 'building'
  const isError = phase === 'error'
  const isDone = phase === 'done'
  // 任务在跑/刚结束/失败 → 常驻气泡; 空闲时只有用户主动缩小才显示
  if (!minimized && !isWorking && !isError && !isDone) return null
  const lastLabel = logs.length > 0 ? logs[logs.length - 1].text : ''
  const title = isWorking
    ? phase === 'mining' ? '市场感知 · 挖掘中' : '市场感知 · AI 合并中'
    : isError ? '市场感知生成失败'
      : isDone ? '策略已生成，点击查看'
        : '市场感知生成'
  const accent = isWorking
    ? 'from-accent/25 to-accent/10 text-accent border-accent/40'
    : isError
      ? 'from-red-500/20 to-red-500/10 text-red-300 border-red-300/40'
      : isDone
        ? 'from-emerald-500/20 to-emerald-500/10 text-emerald-300 border-emerald-300/40'
        : 'from-base to-base text-muted border-border'

  return (
    <button
      type="button"
      onClick={restoreMarketAware}
      title={isWorking ? '市场感知生成中，点击恢复弹窗' : '点击查看详情'}
      className={`fixed bottom-4 right-4 z-[60] flex max-w-[240px] items-center gap-2 rounded-lg border bg-gradient-to-br px-3 py-2 text-left shadow-lg backdrop-blur-xl transition-transform hover:scale-[1.02] active:scale-[0.99] ${accent}`}
    >
      <span className="flex h-[16px] w-[16px] shrink-0 items-center justify-center">
        {isWorking ? <Loader2 className="h-[12px] w-[12px] animate-spin" />
          : isError ? <AlertCircle className="h-[12px] w-[12px]" />
          : <Check className="h-[12px] w-[12px]" />}
      </span>
      <span className="min-w-0">
        <span className="block text-[11px] font-medium leading-none text-foreground">{title}</span>
        <span className="mt-1 block truncate text-[10px] leading-none text-muted">
          {isError || isWorking
            ? lastLabel
            : isDone
              ? result?.decisions.name || result?.strategy_id || ''
              : '点击展开'}
        </span>
      </span>
      <Radar className="h-[13px] w-[13px] shrink-0 opacity-60" />
    </button>
  )
}
