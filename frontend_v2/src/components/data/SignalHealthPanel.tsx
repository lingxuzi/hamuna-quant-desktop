import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

export function SignalHealthPanel({ isRunning }: { isRunning: boolean }) {
  const qc = useQueryClient()
  const health = useQuery({ queryKey: QK.signalHealth, queryFn: api.signalHealth })

  const repair = useMutation({
    mutationFn: api.repairSignalSnapshot,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.signalHealth })
      qc.invalidateQueries({ queryKey: QK.dataStatus })
    },
  })

  const h = health.data
  const broken = h ? !h.ok || (h.today_degraded?.length ?? 0) > 0 : false
  const canRepair = !!h && !isRunning && !repair.isPending

  return (
    <div className="space-y-3">
      <div className="rounded-card bg-accent/8 border border-accent/20 p-3 space-y-1.5">
        <div className="text-xs text-foreground">
          策略信号在当日数据上算不出来时（如金叉恒为假），选股会一组不出。这里可一键重算最新一天。
        </div>
        <div className="text-[10px] text-muted leading-relaxed">
          修复只重算最新一个交易日的指标与信号，不重拉历史数据。
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-secondary">检查结果</span>
        <span className="font-mono text-xs">
          {health.isLoading ? (
            <span className="text-muted">检查中…</span>
          ) : health.isError ? (
            <span className="text-danger">检查失败</span>
          ) : (
            <span className={broken ? 'text-warning' : 'text-success'}>
              {broken ? '需要修复' : '正常'}
            </span>
          )}
        </span>
      </div>

      {h && (
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted">最新交易日</span>
          <span className="font-mono text-xs text-secondary">{h.as_of ?? '—'}</span>
        </div>
      )}

      {(h?.missing.length ?? 0) > 0 && (
        <div className="flex items-center justify-between gap-2 min-w-0">
          <span className="text-xs text-muted shrink-0">缺失信号</span>
          <span className="font-mono text-xs text-warning truncate" title={h!.missing.join(', ')}>
            {h!.missing.length} 个 · {h!.missing.join(', ')}
          </span>
        </div>
      )}

      {(h?.today_degraded?.length ?? 0) > 0 && (
        <div className="flex items-center justify-between gap-2 min-w-0">
          <span className="text-xs text-muted shrink-0">盘中降级</span>
          <span className="font-mono text-xs text-warning truncate" title={h!.today_degraded!.join(', ')}>
            {h!.today_degraded!.length} 个 · {h!.today_degraded!.join(', ')}
          </span>
        </div>
      )}

      <button
        onClick={() => repair.mutate()}
        disabled={!canRepair}
        className="ctl ctl-md ctl-btn soft w-full inline-flex items-center justify-center gap-1.5 px-2.5 hover:bg-accent disabled:opacity-40 disabled:pointer-events-none duration-150 bg-accent/90"
      >
        {repair.isPending ? (
          <>
            <Loader2 className="h-[12px] w-[12px] animate-spin" />
            修复中…
          </>
        ) : (
          '修复信号'
        )}
      </button>

      {repair.data && (
        <div className="text-[10px] text-secondary text-center">
          {repair.data.repaired
            ? `已重算 ${repair.data.as_of ?? ''} 共 ${repair.data.rows ?? 0} 行`
            : '当前无需修复'}
        </div>
      )}
      {repair.isError && (
        <div className="text-[10px] text-danger text-center">
          修复失败：{(repair.error as Error).message}
        </div>
      )}
    </div>
  )
}
