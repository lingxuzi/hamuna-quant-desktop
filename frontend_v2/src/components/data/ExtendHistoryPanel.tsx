import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { MissingCapChip } from '@/lib/capability-labels'

// hasCap: 日K批量能力当前是否可用 (路由矩阵判定, 生效源含插件/自定义源)
export function ExtendHistoryPanel({ hasCap, isRunning, earliestDate, onStart }: {
  hasCap: boolean
  isRunning: boolean
  earliestDate: string | null
  onStart: () => void
}) {
  const qc = useQueryClient()
  const [value, setValue] = useState(6)
  const [unit, setUnit] = useState<'month' | 'year'>('month')
  const hasBatchCap = hasCap

  const extend = useMutation({
    mutationFn: () => api.extendHistory(value, unit),
    onSuccess: () => {
      onStart()
      qc.invalidateQueries({ queryKey: QK.pipelineJobs })
    },
  })

  const offsetDays = unit === 'month' ? value * 30 : value * 365
  const estimate = earliestDate
    ? (() => {
        const d = new Date(earliestDate)
        d.setDate(d.getDate() - offsetDays)
        return d.toISOString().slice(0, 10)
      })()
    : null

  return (
    <div className="px-4 pb-4 pt-3 border-t border-accent/20 space-y-3">
      <div className="text-[10px] text-secondary">向前扩展历史数据</div>

      <div className="flex items-center gap-2">
        <div className="flex items-center">
          <button
            onClick={() => setValue(Math.max(1, value - 1))}
            disabled={!hasBatchCap || isRunning}
            className="ctl ctl-sm ctl-btn filled w-6 px-0 rounded-l-btn hover:bg-border/50 disabled:opacity-30"
          >−</button>
          <div className="h-6 w-8 flex items-center justify-center border-y border-border text-[11px] font-mono tabular-nums text-foreground bg-base">
            {value}
          </div>
          <button
            onClick={() => setValue(Math.min(unit === 'year' ? 10 : 36, value + 1))}
            disabled={!hasBatchCap || isRunning}
            className="ctl ctl-sm ctl-btn filled w-6 px-0 rounded-r-btn hover:bg-border/50 disabled:opacity-30"
          >+</button>
        </div>

        <div className="flex rounded-btn border border-border overflow-hidden">
          {(['month', 'year'] as const).map(u => (
            <button
              key={u}
              onClick={() => { setUnit(u); if (u === 'year' && value > 10) setValue(1); if (u === 'month' && value > 36) setValue(6) }}
              className={`ctl ctl-sm ctl-ghost px-1.5 ${
                unit === u ? 'bg-accent/15 text-accent' : 'text-secondary hover:bg-elevated'
              }`}
            >{u === 'month' ? '月' : '年'}</button>
          ))}
        </div>
      </div>

      {estimate && (
        <div className="text-[10px] text-muted">
          预计扩展至 <span className="font-mono text-secondary">{estimate}</span>
          {earliestDate && <span> (当前最早: <span className="font-mono text-secondary">{earliestDate}</span>)</span>}
        </div>
      )}

      <button
        onClick={() => extend.mutate()}
        disabled={!hasBatchCap || isRunning || extend.isPending || !earliestDate}
        className="ctl ctl-md ctl-btn soft w-full inline-flex items-center justify-center gap-1.5 px-2.5 hover:bg-accent disabled:opacity-40 disabled:pointer-events-none duration-150 bg-accent/90"
      >
        {extend.isPending ? (
          <>
            <Loader2 className="h-[12px] w-[12px] animate-spin" />
            请求中…
          </>
        ) : (
          <>获取数据</>
        )}
      </button>

      {!hasBatchCap && (
        <MissingCapChip capKey="kline.daily.batch" />
      )}
    </div>
  )
}
