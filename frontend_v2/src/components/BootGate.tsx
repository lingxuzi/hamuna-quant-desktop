/**
 * 启动闸门 — 进入应用先确认本地日K数据完整, 不完整就弹阻塞弹窗并显示数据同步进度。
 *
 * 判据是 /api/data/integrity 的 blocking_assets: daily_health 里还没清掉的
 * needs_raw / needs_enriched 条目。这些条目会让后端 assert_usable 直接抛
 * DailyIntegrityError, 回测 / 因子 / 看板全都算不出来。与其让用户点开各页面挨个
 * 撞报错, 不如在入口一次修完。
 * 统计预热中 / 本地还没有数据 → 不判, 放行 (首页欢迎流程自己接管)。
 *
 * 进度取自 /api/pipeline/jobs 的活跃任务: 入口发起的修补、用户在数据页手动跑的
 * 同步是同一个 job 结构, 复用 ActiveJobCard 展示阶段与百分比。
 *
 * 死锁出口: 修补未必成功 (数据源不返回那几天的日K时必然失败), 所以任何状态都留
 * 「仍要进入应用」, 否则用户被锁死在入口。
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { Modal } from '@/components/Modal'
import { BTN_MD, BTN_PRIMARY } from '@/components/controls'
import { ActiveJobCard } from '@/components/data/ActiveJobCard'
import { isDataStatusPending, resolveHasNoData } from '@/lib/useSharedQueries'
import { cn } from '@/lib/cn'

const ASSET_LABELS: Record<string, string> = { stock: '股票', etf: 'ETF', index: '指数' }
/** 用户选择「仍要进入应用」后, 本次会话不再拦截 */
const BAIL_KEY = 'tf_boot_gate_bail'
/** 入口自动修补的尝试标记 —— 每个会话只自动起一次 */
const ATTEMPT_KEY = 'tf_boot_gate_attempt'

export function BootGate() {
  const qc = useQueryClient()
  const [bailed, setBailed] = useState(() => sessionStorage.getItem(BAIL_KEY) === '1')
  const [jobId, setJobId] = useState<string | null>(null)

  const status = useQuery({
    queryKey: QK.dataStatus, queryFn: api.dataStatus,
    refetchInterval: q => (q.state.data?.stats_pending ? 2_000 : false),
  })
  const integrity = useQuery({ queryKey: QK.dataIntegrity, queryFn: api.dataIntegrity })
  const jobs = useQuery({
    queryKey: QK.pipelineJobs, queryFn: () => api.pipelineJobs(1),
    refetchInterval: q => (q.state.data?.active_id ? 2_000 : 10_000),
  })
  // 优先跟踪自己发起的修补; 没有就从全局活跃任务里取 (用户在数据页手动跑的同步)
  const trackedId = jobId ?? jobs.data?.active_id ?? null
  const job = useQuery({
    queryKey: QK.pipelineJob(trackedId ?? ''),
    queryFn: () => api.pipelineJob(trackedId!),
    enabled: !!trackedId,
    refetchInterval: q => {
      const s = q.state.data?.status
      return s === 'succeeded' || s === 'failed' ? false : 1_000
    },
  })

  const report = integrity.data?.report
  const blocking = integrity.data?.blocking_assets ?? []
  const issues = report?.issues ?? []
  const jobStatus = job.data?.status
  // 任务查不到 (id 失效) 时别一直停在「正在修补」—— 没有进度就按没有任务处理
  const running = !!trackedId && !job.isError && jobStatus !== 'succeeded' && jobStatus !== 'failed'
  const failed = jobStatus === 'failed'
  // 报告是修补的确认凭证: 它必须覆盖全部阻塞资产, 否则修补不到那些分区
  const covered = blocking.every(a => report?.asset_types?.includes(a))

  const { mutate: startRepair, isPending: repairing, error: startError } = useMutation({
    mutationFn: (reportId: string) => api.repairDataIntegrity(reportId),
    onSuccess: ({ job_id }) => setJobId(job_id),
    // 后端会拒掉过期的报告凭证 (409), 刷新后用户再点一次就能用新报告
    onError: () => { void qc.invalidateQueries({ queryKey: QK.dataIntegrity }) },
  })

  const bail = () => {
    sessionStorage.setItem(BAIL_KEY, '1')
    setBailed(true)
  }

  // 任务收尾后重新自检: 成功要刷新阻塞条目; 失败也要刷新 —— 后端失败时会重存一份
  // 新报告 (报告 id 变了), 不刷新的话下一次修补会被「自检报告已变化」挡下。
  useEffect(() => {
    if (jobStatus !== 'succeeded' && jobStatus !== 'failed') return
    void qc.invalidateQueries({ queryKey: QK.dataIntegrity })
    void qc.invalidateQueries({ queryKey: QK.dataStatus })
  }, [jobStatus, qc])

  const blocked = !bailed && !status.isPending && !integrity.isPending
    && !isDataStatusPending(status.data) && !resolveHasNoData(status.data)
    && blocking.length > 0

  // 入口自动起一次修补: 弹窗的主内容就是同步进度, 没有进度可看时先自己起一个。
  // 每个会话只自动起一次 —— 失败多半是数据源缺那几天的日K, 反复重试只会刷日志。
  useEffect(() => {
    if (!blocked || trackedId || repairing) return
    if (!report?.repairable_count || !covered) return
    if (sessionStorage.getItem(ATTEMPT_KEY)) return
    sessionStorage.setItem(ATTEMPT_KEY, '1')
    startRepair(report.id)
  }, [blocked, trackedId, repairing, report?.id, report?.repairable_count, covered, startRepair])

  if (!blocked) return null

  const assetsText = blocking.map(a => ASSET_LABELS[a] ?? a).join('、')
  const canRepair = !!report?.repairable_count && covered && !running
  const title = running ? '正在同步数据' : failed ? '修补失败' : jobStatus === 'succeeded' ? '仍有未处理的分区' : '本地数据不完整'
  const message = failed
    ? job.data?.error || '修补失败, 请到数据页重试。'
    : startError
      ? startError.message
      : running
        ? `正在修补${assetsText}日K, 期间请勿关闭页面。`
        : jobStatus === 'succeeded'
          ? `修补完成后仍有 ${issues.length || '若干'} 项无法自动处理, 请到数据页查看明细。`
          : `${assetsText}日K有未完成修补或重算的分区, 回测与指标计算会失败。`

  return (
    <Modal
      onClose={bail}
      ariaLabel="本地数据不完整"
      closeOnBackdrop={false}
      overlayClassName="fixed inset-0 z-[60] flex items-center justify-center bg-[hsl(var(--scrim)/0.72)] backdrop-blur-[2px]"
      panelClassName="w-[92vw] max-w-lg max-h-[88vh] overflow-y-auto border border-border rounded-card bg-surface shadow-xl"
    >
      <div className="p-5">
        <div className="flex items-start gap-3">
          <div className={cn('shrink-0 rounded-lg p-2', failed ? 'bg-danger/10' : 'bg-accent/10')}>
            {failed
              ? <AlertTriangle className="h-[16px] w-[16px] text-danger" />
              : running
                ? <Loader2 className="h-[16px] w-[16px] animate-spin text-accent" />
                : <AlertTriangle className="h-[16px] w-[16px] text-accent" />}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-medium text-foreground">{title}</h2>
            <p className="mt-1 text-xs leading-relaxed text-secondary">{message}</p>
          </div>
        </div>

        {job.data
          ? <div className="mt-4"><ActiveJobCard job={job.data} /></div>
          : issues.length > 0 && (
            <ul className="mt-4 space-y-1.5">
              {issues.slice(0, 4).map((issue, i) => (
                <li key={`${issue.asset_type}-${issue.date}-${i}`} className="flex items-baseline gap-2 rounded-btn border border-border bg-base/60 px-3 py-2 text-xs">
                  <span className="font-mono tabular text-muted">{issue.date}</span>
                  <span className="text-foreground">{issue.asset_label}</span>
                  <span className="min-w-0 truncate text-secondary">{issue.kind_label}</span>
                  {issue.symbol_count > 0 && <span className="ml-auto shrink-0 text-muted">{issue.symbol_count} 只</span>}
                </li>
              ))}
            </ul>
          )}

        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" className={BTN_MD} onClick={bail}>仍要进入应用</button>
          <Link to="/data" className={cn(BTN_MD, 'inline-flex items-center')} onClick={bail}>前往数据页</Link>
          {canRepair && (
            <button
              type="button"
              className={cn(BTN_MD, BTN_PRIMARY, 'inline-flex items-center gap-1.5')}
              disabled={repairing}
              onClick={() => report && startRepair(report.id)}
            >
              <RefreshCw className={cn('h-[12px] w-[12px]', repairing && 'animate-spin')} />
              {failed || jobStatus === 'succeeded' ? '重新修补' : '开始修补'}
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}
