/**
 * 启动闸门 — 进入应用先检查本地数据, 有问题或缺失就弹阻塞弹窗并自动修复。
 *
 * 阻塞判据 (任一命中):
 *   - blocking_assets 非空: daily_health 里还没清掉的 needs_raw / needs_enriched,
 *     它们会让后端 assert_usable 直接抛 DailyIntegrityError (回测/因子/看板全算不出来);
 *   - resolveHasNoData: 本地一条日K都没有 (全新安装 / 数据目录被清), 必须先同步。
 *
 * 修复动作:
 *   - 有数据但分区不完整 → 拿自检报告做定点修补 (repairDataIntegrity)
 *   - 完全没数据 → 跑一次盘后管道 (pipelineRun) 做首次全量同步
 *
 * 弹窗不可关闭: 没有 X / ESC / 遮罩点击 /「仍要进入应用」。
 * 修补可能失败 (数据源不返回那几天的日K), 所以失败后按退避自动重试, 修好即自愈关闭。
 * 活路是「修好数据」或「退出应用」, 不是绕过闸门带着坏数据进计算页。
 *
 * 统计预热中 (stats_pending) → 不判, 放行等真实统计就绪再判。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { Modal } from '@/components/Modal'
import { BTN_MD, BTN_PRIMARY } from '@/components/controls'
import { ActiveJobCard } from '@/components/data/ActiveJobCard'
import { isDataStatusPending, resolveHasNoData } from '@/lib/useSharedQueries'
import { cn } from '@/lib/cn'

const ASSET_LABELS: Record<string, string> = { stock: '股票', etf: 'ETF', index: '指数' }
/** 自动修复每会话只起一次 (两种动作各算一次) —— 反复起会刷日志、也会撞后端单飞 */
const PIPELINE_KEY = 'tf_boot_gate_pipeline'
const REPAIR_KEY = 'tf_boot_gate_repair'
/** 失败后的自动重试退避 (秒) —— 逐级拉长, 最后每 5 分钟试一次 */
const RETRY_BACKOFF_S = [15, 30, 60, 120, 300]

export function BootGate() {
  const qc = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const [countdown, setCountdown] = useState<number | null>(null)
  const attempts = useRef(0)

  const status = useQuery({
    queryKey: QK.dataStatus, queryFn: api.dataStatus,
    refetchInterval: q => (q.state.data?.stats_pending ? 2_000 : false),
  })
  const integrity = useQuery({ queryKey: QK.dataIntegrity, queryFn: api.dataIntegrity })
  const jobs = useQuery({
    queryKey: QK.pipelineJobs, queryFn: () => api.pipelineJobs(1),
    refetchInterval: q => (q.state.data?.active_id ? 2_000 : 10_000),
  })
  // 优先跟踪自己发起的修复; 没有就从全局活跃任务里取 (用户在数据页手动跑的同步)
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
  // 任务查不到 (id 失效) 时别一直停在「正在修补」—— 没有进度就按没有任务处理。
  // 任务列表本身查失败 (jobs.isError) 时 trackedId 恒为 null, 同样不能算「在跑」,
  // 否则弹窗永久停在「正在同步数据」, 自动重试也被 running 挡住。
  const running = !!trackedId && !job.isError && !jobs.isError && jobStatus !== 'succeeded' && jobStatus !== 'failed'
  const failed = jobStatus === 'failed'
  // 报告是修补的确认凭证: 它必须覆盖全部阻塞资产, 否则修补不到那些分区
  const covered = blocking.every(a => report?.asset_types?.includes(a))
  // 「本地无数据」的判定必须粘住: 同步进行中后端 stats_pending=true, 统计处于
  // 中间态 —— 若按瞬时值判定, 弹窗会在修复中途消失, 用户能带着半份数据进应用。
  // 只在统计落定 (settled) 时更新结论, 同步期间保持上一次。
  const settled = !status.isPending && !isDataStatusPending(status.data)
  const [noData, setNoData] = useState(false)
  const observed = settled ? resolveHasNoData(status.data) : null
  if (observed !== null && observed !== noData) setNoData(observed)

  const { mutate: startRepair, isPending: repairing, error: startError } = useMutation({
    mutationFn: (reportId: string) => api.repairDataIntegrity(reportId),
    onSuccess: ({ job_id }) => setJobId(job_id),
    // 后端会拒掉过期的报告凭证 (409), 刷新后用户再点一次就能用新报告
    onError: () => { void qc.invalidateQueries({ queryKey: QK.dataIntegrity }) },
  })
  const { mutate: startPipeline, isPending: piping } = useMutation({
    mutationFn: () => api.pipelineRun(),
    onSuccess: ({ job_id }) => setJobId(job_id),
    onError: () => { void qc.invalidateQueries({ queryKey: QK.dataStatus }) },
  })

  const blocked = !integrity.isPending && (noData || blocking.length > 0)

  /** 发起修复: 没数据 (或没有可修补的报告) 走全量同步, 否则走定点修补。 */
  const startFix = useCallback(() => {
    if (noData || !report?.repairable_count) startPipeline()
    else startRepair(report.id)
  }, [noData, report, startPipeline, startRepair])

  // 入口自动起一次修复: 弹窗一出现就把数据补上, 用户不用点。
  // 已有活跃任务 (用户在数据页手动跑的) → 不重复起。
  // 两种动作各只自动起一次: 首次同步成功但仍有未完成分区时, 定点修补还能自动接上;
  // 再之后就交给用户点按钮 (否则成功→仍阻塞→再修补会打成死循环)。
  useEffect(() => {
    if (!blocked || trackedId || repairing || piping || failed) return
    if (noData) {
      if (sessionStorage.getItem(PIPELINE_KEY)) return
      sessionStorage.setItem(PIPELINE_KEY, '1')
      startPipeline()
      return
    }
    if (!report?.repairable_count || !covered) return
    if (sessionStorage.getItem(REPAIR_KEY)) return
    sessionStorage.setItem(REPAIR_KEY, '1')
    startRepair(report.id)
  }, [blocked, trackedId, repairing, piping, failed, noData, report?.id, report?.repairable_count, covered, startPipeline, startRepair])

  // 任务收尾后重新自检: 成功要刷新阻塞条目; 失败也要刷新 —— 后端失败时会重存一份
  // 新报告 (报告 id 变了), 不刷新的话下一次修补会被「自检报告已变化」挡下。
  useEffect(() => {
    if (jobStatus !== 'succeeded' && jobStatus !== 'failed') return
    void qc.invalidateQueries({ queryKey: QK.dataIntegrity })
    void qc.invalidateQueries({ queryKey: QK.dataStatus })
  }, [jobStatus, qc])

  // 失败后自动重试 (退避) —— 弹窗不可关闭, 没有重试就等于死锁。
  // 数据源恢复的那一刻数据自己修好, 闸门自动放行。
  useEffect(() => {
    if (!failed || !blocked) return
    const delay = RETRY_BACKOFF_S[Math.min(attempts.current, RETRY_BACKOFF_S.length - 1)]
    setCountdown(delay)
    const timer = window.setTimeout(() => {
      setCountdown(null)
      attempts.current += 1
      startFix()
    }, delay * 1000)
    return () => window.clearTimeout(timer)
  }, [failed, blocked, startFix])

  // 倒计时显示
  useEffect(() => {
    if (countdown === null || countdown <= 0) return
    const timer = window.setTimeout(() => setCountdown(c => (c === null ? null : c - 1)), 1000)
    return () => window.clearTimeout(timer)
  }, [countdown])

  if (!blocked) return null

  const assetsText = blocking.map(a => ASSET_LABELS[a] ?? a).join('、')
  const canRepair = !!report?.repairable_count && covered && !running
  const title = noData
    ? running ? '正在同步数据' : failed ? '数据同步失败' : '本地还没有数据'
    : running ? '正在同步数据' : failed ? '修补失败' : jobStatus === 'succeeded' ? '仍有未处理的分区' : '本地数据不完整'
  const reason = failed ? job.data?.error : startError?.message
  const message = failed
    ? reason || '修补失败, 请稍后重试。'
    : startError
      ? startError.message
      : noData
        ? running
          ? '正在首次同步行情数据, 完成后即可使用。'
          : '本地还没有行情数据, 需要先完成一次同步。'
        : running
          ? `正在修补${assetsText}日K, 期间请勿关闭页面。`
          : jobStatus === 'succeeded'
            ? `修补完成后仍有 ${issues.length || '若干'} 项无法自动处理, 正在重试。`
            : `${assetsText}日K有未完成修补或重算的分区, 回测与指标计算会失败。`

  return (
    <Modal
      onClose={() => {}}
      closable={false}
      closeOnBackdrop={false}
      ariaLabel="本地数据不完整"
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
            {failed && countdown !== null && (
              <p className="mt-1 text-xs text-muted">{countdown} 秒后自动重试</p>
            )}
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
          {(failed || startError) && (
            <button
              type="button"
              className={cn(BTN_MD, BTN_PRIMARY, 'inline-flex items-center gap-1.5')}
              disabled={repairing || piping}
              onClick={() => { attempts.current = 0; startFix() }}
            >
              <RefreshCw className={cn('h-[12px] w-[12px]', (repairing || piping) && 'animate-spin')} />
              立即重试
            </button>
          )}
          {!failed && !startError && !running && canRepair && (
            <button
              type="button"
              className={cn(BTN_MD, BTN_PRIMARY, 'inline-flex items-center gap-1.5')}
              disabled={repairing}
              onClick={() => startRepair(report!.id)}
            >
              <RefreshCw className={cn('h-[12px] w-[12px]', repairing && 'animate-spin')} />
              立即修补
            </button>
          )}
          {!failed && !startError && !running && !canRepair && !noData && (
            <button
              type="button"
              className={cn(BTN_MD, BTN_PRIMARY, 'inline-flex items-center gap-1.5')}
              disabled={piping}
              onClick={() => startPipeline()}
            >
              <RefreshCw className={cn('h-[12px] w-[12px]', piping && 'animate-spin')} />
              重新同步全部数据
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}
