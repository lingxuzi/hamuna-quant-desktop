import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, GitMerge, Play, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { api, type GuardianCombinationSuggestion, type GuardianConfig, type GuardianProfile, type LabReport } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { EmptyState } from '@/components/EmptyState'
import { Switch } from '@/components/Switch'
import { INPUT_CLS, PRIMARY_OBJECTIVES, StrategySelect } from './components/paramSweep'

/** 状态徽章: 发丝线描边的等宽小签 (不用药丸底色)。 */
const STATUS_META: Record<GuardianProfile['status'], { label: string; cls: string }> = {
  pending: { label: '未复验', cls: 'border-border text-muted' },
  healthy: { label: '健康', cls: 'border-emerald-500/40 text-emerald-500' },
  watch: { label: '观察', cls: 'border-amber-500/40 text-amber-500' },
  retired: { label: '已退役', cls: 'border-red-500/40 text-red-500' },
}

const BASELINE_KIND_LABEL: Record<string, string> = {
  mining_gate: '挖掘门槛 (OOS)',
  trailing_reference: '尾随参考',
}

function fmtDegradation(v: number | null | undefined): string {
  return v != null && Number.isFinite(v) ? `${(v * 100).toFixed(0)}%` : '—'
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  return v != null && Number.isFinite(v) ? v.toFixed(digits) : '—'
}

function StatusBadge({ status }: { status: GuardianProfile['status'] }) {
  const meta = STATUS_META[status]
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap border px-1.5 py-0.5 font-mono text-[9.5px] ${meta.cls}`}>
      {status === 'healthy' && <ShieldCheck className="h-[12px] w-[12px]" />}
      {meta.label}
    </span>
  )
}

/** 守护配置: 阈值改动即时 PATCH, 与后端 GuardianThresholds 同一约束。 */
function ConfigCard({ config }: { config: GuardianConfig }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const patch = useMutation({
    mutationFn: (body: Parameters<typeof api.guardianPatchConfig>[0]) => api.guardianPatchConfig(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: QK.guardianOverview }),
    onError: (e: Error) => setError(e.message),
  })
  const update = (body: Parameters<typeof api.guardianPatchConfig>[0]) => {
    setError(null)
    patch.mutate(body)
  }
  return (
    <section>
      <div className="figcap">守 护 范 围</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <label className="block">
          <span className="mb-1 block text-[10.5px] text-secondary">观察阈值</span>
          <input
            type="number" step="0.05" min="0.05" max="0.9"
            defaultValue={config.guardian_watch_pct}
            key={`w-${config.guardian_watch_pct}`}
            onBlur={e => update({ guardian_watch_pct: Number(e.target.value) })}
            className={`${INPUT_CLS} w-24`}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10.5px] text-secondary">退役阈值</span>
          <input
            type="number" step="0.05" min="0.1" max="1"
            defaultValue={config.guardian_retire_pct}
            key={`r-${config.guardian_retire_pct}`}
            onBlur={e => update({ guardian_retire_pct: Number(e.target.value) })}
            className={`${INPUT_CLS} w-24`}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10.5px] text-secondary">验证段数上限 K</span>
          <input
            type="number" step="1" min="2" max="4"
            defaultValue={config.guardian_k_max}
            key={`k-${config.guardian_k_max}`}
            onBlur={e => update({ guardian_k_max: Number(e.target.value) })}
            className={`${INPUT_CLS} w-24`}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10.5px] text-secondary">挖掘策略名额上限</span>
          <input
            type="number" step="1" min="0" max="50"
            defaultValue={config.lab_mined_slot_limit}
            key={`slot-${config.lab_mined_slot_limit}`}
            onBlur={e => update({ lab_mined_slot_limit: Number(e.target.value) })}
            className={`${INPUT_CLS} w-24`}
          />
        </label>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 border-t border-border pt-2">
        <span className="text-[10.5px] text-secondary">每日自动复验 (收盘数据更新后)</span>
        <Switch
          checked={config.guardian_schedule_enabled}
          onChange={next => update({ guardian_schedule_enabled: next })}
          label="每日自动复验"
        />
      </div>
      <p className="mt-1.5 text-[9.5px] leading-relaxed text-muted">
        基线后退化为百分比; 段回测失败只降级到观察, 不会直接退役
      </p>
      {error && <div className="mt-2 text-[11px] text-danger">{error}</div>}
    </section>
  )
}

/** 手动入护: 任意策略 + 目标 (尾随参考基线, 首跑自动标定)。 */
function AdoptCard({ onChanged }: { onChanged: () => void }) {
  const { data: stratData } = useQuery({ queryKey: ['strategies'], queryFn: () => api.strategyList() })
  const strategies = stratData?.strategies ?? []
  const [strategyId, setStrategyId] = useState('')
  const [objective, setObjective] = useState('sharpe')
  const [error, setError] = useState<string | null>(null)
  const adopt = useMutation({
    mutationFn: () => api.guardianAdopt(strategyId, objective),
    onSuccess: () => { setError(null); onChanged() },
    onError: (e: Error) => setError(e.message),
  })
  return (
    <section>
      <div className="figcap">手 动 加 入</div>
      <label className="mb-1 block text-[10.5px] text-secondary">选择要守护的策略</label>
      <StrategySelect strategies={strategies} value={strategyId} onChange={setStrategyId} />
      <div className="mt-2 grid grid-cols-2 gap-2">
        <select value={objective} onChange={e => setObjective(e.target.value)} className={INPUT_CLS}>
          {PRIMARY_OBJECTIVES.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
        <button
          onClick={() => adopt.mutate()}
          disabled={!strategyId || adopt.isPending}
          className="ctl-btn ctl-sm pri inline-flex items-center justify-center gap-1.5"
        >
          <ShieldCheck className="h-3.5 w-3.5" /> 入护并复验
        </button>
      </div>
      <p className="mt-1.5 text-[9.5px] leading-relaxed text-muted">
        手动入护用入护日前 252 个交易日的尾随表现作参考基线 (非样本外), 判定只针对此后新数据; 挖掘发布的策略自动入护 (门槛基线, 天然 OOS)。
      </p>
      {error && <div className="mt-2 text-[11px] text-danger">{error}</div>}
    </section>
  )
}

/** P3 实验室战报: 挖掘/守护/名额聚合总览。 */
function LabReportCard({ report }: { report: LabReport | undefined }) {
  if (!report) return null
  const mining = report.mining.last_run
  const refinement = mining?.refinement as { applied?: boolean; refined_weights?: number[]; original_weights?: number[] } | null | undefined
  return (
    <section>
      <div className="figcap">实 验 室 战 报</div>
      <div className="readout">
        <div>
          <span className="k">守 护 中</span>
          <div className="v">{report.guardian.profiles}</div>
          <small>健康 {report.guardian.by_status.healthy ?? 0}</small>
        </div>
        <div>
          <span className="k">观 察</span>
          <div className="v">{report.guardian.by_status.watch ?? 0}</div>
          <small className={report.guardian.alerts > 0 ? 'text-amber-500' : undefined}>
            {report.guardian.alerts > 0 && <BellRing className="mr-0.5 inline h-[12px] w-[12px]" />}
            连续告警 {report.guardian.alerts}
          </small>
        </div>
        <div>
          <span className="k">已 退 役</span>
          <div className="v">{report.guardian.by_status.retired ?? 0}</div>
          <small>不再复验</small>
        </div>
        <div>
          <span className="k">挖 掘 名 额</span>
          <div className="v">{report.slots.active_mined}/{report.slots.limit === 0 ? '∞' : report.slots.limit}</div>
          <small className={report.slots.full ? 'text-danger' : undefined}>
            {report.slots.full ? '已满, 新候选等位' : '仍可继续挖掘'}
          </small>
        </div>
      </div>
      <div className="mt-2 text-[10px] leading-relaxed text-muted">
        {mining
          ? `最近挖掘 ${mining.status} · ${mining.algorithm_version ?? '—'} · 候选 ${mining.candidate_count ?? '—'} 个 · 有效折 ${mining.valid_fold_count ?? '—'}`
          : '还没有挖掘运行'}
        {refinement?.applied === true && (
          <span className="text-emerald-500"> · 冠军权重已精调 {JSON.stringify(refinement.original_weights)} → {JSON.stringify(refinement.refined_weights)}</span>
        )}
      </div>
    </section>
  )
}

/** P2d 组合建议: 健康冠军低相关对, 确认后生成 composite 并入护。 */
function CombinationsCard({
  combinations,
  busy,
  onScan,
  onCreate,
}: {
  combinations: GuardianCombinationSuggestion[]
  generatedAt?: string | null
  busy: boolean
  onScan: () => void
  onCreate: (a: string, b: string) => Promise<unknown>
}) {
  const [creating, setCreating] = useState<string | null>(null)
  return (
    <section>
      <div className="flex items-center justify-between gap-3">
        <div className="figcap mb-0">组 合 建 议</div>
        <button
          onClick={onScan}
          disabled={busy}
          className="ctl-btn ctl-sm inline-flex items-center gap-1"
        >
          <RefreshCw className="h-[12px] w-[12px]" /> 扫描相关性
        </button>
      </div>
      {combinations.length === 0 ? (
        <p className="mt-1.5 text-[10px] leading-relaxed text-muted">
          暂无建议 — 扫描会计算健康守护冠军间近 126 个交易日的日收益相关性, 相关性 ≤0.7 的对才会出现在这里。
        </p>
      ) : (
        <div className="rowset mt-1.5">
          {combinations.map(item => {
            const key = `${item.a}+${item.b}`
            return (
              <div key={key} className="r">
                <div className="lab">
                  <b className="flex items-center gap-1.5">
                    <GitMerge className="h-[12px] w-[12px] text-muted" />
                    {item.a} <span className="text-muted">×</span> {item.b}
                  </b>
                  <small>相关性 {item.corr.toFixed(2)} · {item.common_days} 日</small>
                </div>
                <div className="ctl">
                  <button
                    onClick={() => {
                      if (window.confirm(`生成组合策略 ${item.a} + ${item.b}? (等权 composite, 自动加入守护)`)) {
                        setCreating(key)
                        // 成功或失败都要复位按钮状态 (错误展示走父组件 error)
                        Promise.resolve(onCreate(item.a, item.b))
                          .catch(() => {})
                          .finally(() => setCreating(null))
                      }
                    }}
                    disabled={busy || creating === key}
                    className="ctl-btn ctl-sm pri"
                  >
                    {creating === key ? '生成中…' : '生成组合'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

export function StrategyGuardian() {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const { data, isLoading, error: queryError } = useQuery({
    queryKey: QK.guardianOverview,
    queryFn: () => api.guardianOverview(),
    // 复验进行中每 2s 轮询; 平时按 TanStack 默认 (staleTime 到点后窗口聚焦刷新)
    refetchInterval: query => (query.state.data?.active_run ? 2000 : false),
  })
  const { data: labReport } = useQuery({
    queryKey: QK.labReport,
    queryFn: () => api.labReport(),
    refetchInterval: () => (data?.active_run ? 5000 : false),
  })
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: QK.guardianOverview })
    queryClient.invalidateQueries({ queryKey: QK.labReport })
  }
  const runAll = useMutation({
    mutationFn: () => api.guardianRunAll(),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const rerun = useMutation({
    mutationFn: (sid: string) => api.guardianRerun(sid),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const drop = useMutation({
    mutationFn: (sid: string) => api.guardianDrop(sid),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const scanCombinations = useMutation({
    mutationFn: () => api.guardianScanCombinations(),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const createCombination = useMutation({
    mutationFn: ({ a, b }: { a: string; b: string }) => api.guardianCreateCombination(a, b),
    onSuccess: () => { setError(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })

  const items = data?.items ?? []
  const activeRun = data?.active_run ?? null
  const progress = activeRun?.progress

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto">
      {(error || queryError) && (
        <div className="rounded-input border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error ?? (queryError instanceof Error ? queryError.message : '加载失败')}
        </div>
      )}

      {activeRun && (
        <div className="border-b border-border pb-3">
          <div className="flex items-center justify-between text-xs">
            <span className="inline-flex items-center gap-1.5 text-secondary">
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              复验进行中 {progress?.strategy_id ? `· ${progress.strategy_id}` : ''}
            </span>
            <span className="num text-secondary">{progress?.done ?? 0}/{progress?.total ?? '?'}</span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-elevated">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${progress?.total ? ((progress.done ?? 0) / progress.total) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      <div className="grid gap-x-6 xl:grid-cols-[18rem_minmax(0,1fr)]">
        {/* 左栏: 守护配置 (design/v2/13 的 .cfg 栏) */}
        <div className="min-w-0 space-y-5 border-b border-border pb-4 xl:border-b-0 xl:border-r xl:pb-0 xl:pr-6">
          {data?.config && <ConfigCard config={data.config} />}
          <AdoptCard onChanged={refresh} />
        </div>

        {/* 右栏: 战报 / 组合建议 / 守护名单 */}
        <div className="min-w-0 space-y-6 xl:pl-6">
          <LabReportCard report={labReport} />
          <CombinationsCard
            combinations={data?.combinations?.suggestions ?? []}
            busy={!!activeRun || scanCombinations.isPending || createCombination.isPending}
            onScan={() => scanCombinations.mutate()}
            onCreate={(a, b) => createCombination.mutateAsync({ a, b })}
          />

          <section>
            <div className="flex items-center justify-between gap-3">
              <div className="figcap mb-0">守 护 中 的 策 略 ({items.length})</div>
              <button
                onClick={() => runAll.mutate()}
                disabled={runAll.isPending || !!activeRun}
                className="ctl-btn ctl-sm pri inline-flex items-center gap-1.5"
              >
                <Play className="h-3.5 w-3.5" /> 全部复验
              </button>
            </div>

            {isLoading ? (
              <div className="py-10 text-center text-xs text-secondary">加载中…</div>
            ) : items.length === 0 ? (
              <EmptyState
                title="还没有守护中的策略"
                hint="挖掘发布的策略会在发布时自动建立守护基线; 也可以在上方手动把任意策略加入守护。守护会在每天收盘数据更新后, 用基线之后的新数据自动复验, 退化时告警、严重时标记退役。"
              />
            ) : (
              <div className="mt-2 overflow-x-auto">
                <table className="dt min-w-[760px]">
                  <thead>
                    <tr>
                      <th scope="col" className="w-[18%] text-left">策略</th>
                      <th scope="col" className="w-[12%] text-left">状态</th>
                      <th scope="col" className="w-[20%] text-left">基线</th>
                      <th scope="col" className="w-[16%] text-left">最近复验</th>
                      <th scope="col" className="w-[8%]">退化</th>
                      <th scope="col" className="w-[18%] text-left">说明</th>
                      <th scope="col" className="w-[8%]">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map(p => {
                      const last = p.last_check
                      const lastText = last?.skipped
                        ? (last.reason ?? '跳过')
                        : last?.error
                          ? last.error
                          : last?.verdict
                            ? `${last.window_start ?? ''} ~ ${last.window_end ?? ''} · ${last.k ?? '—'} 段`
                            : '—'
                      return (
                        <tr key={p.strategy_id}>
                          <td className="whitespace-normal align-top">
                            <div className="font-medium text-foreground">{p.name ?? p.strategy_id}</div>
                            <div className="text-[10px] text-muted">{p.strategy_id}</div>
                          </td>
                          <td className="whitespace-normal align-top">
                            <div className="flex flex-col items-start gap-1">
                              <StatusBadge status={p.status} />
                              {p.alert && (
                                <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-500">
                                  <BellRing className="h-[12px] w-[12px]" /> 连续 {p.consecutive_watch} 次观察
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="whitespace-normal align-top">
                            <div className="font-sans">{BASELINE_KIND_LABEL[p.baseline.kind ?? ''] ?? '—'}</div>
                            <div className="text-[10px]">
                              {p.baseline.objective ?? '—'} {fmtNum(p.baseline.score)} · {p.baseline.n_trades ?? '—'} 笔 · 截至 {p.baseline.data_as_of ?? '—'}
                            </div>
                          </td>
                          <td className="whitespace-normal align-top">
                            <div className="font-sans">{last?.checked_at ? new Date(last.checked_at).toLocaleString('zh-CN', { hour12: false }) : '—'}</div>
                            <div className="text-[10px] text-muted" title={lastText}>{lastText}</div>
                          </td>
                          <td className="align-top font-medium" style={{ color: last?.degradation_pct != null && last.degradation_pct > 0 ? 'hsl(var(--danger))' : undefined }}>
                            {fmtDegradation(last?.degradation_pct)}
                          </td>
                          <td className="whitespace-normal align-top text-[10px]">
                            <span className="font-sans">{(last?.reasons?.length ? last.reasons : [p.status_reason]).filter(Boolean).join('; ') || '—'}</span>
                          </td>
                          <td className="align-top">
                            <div className="flex justify-end gap-1">
                              <button
                                onClick={() => rerun.mutate(p.strategy_id)}
                                disabled={rerun.isPending || !!activeRun}
                                title="立即复验"
                                className="ctl ctl-sm ctl-ghost px-1.5"
                              >
                                <RefreshCw className="h-3.5 w-3.5" />
                              </button>
                              <button
                                onClick={() => {
                                  if (window.confirm(`移除 ${p.name ?? p.strategy_id} 的守护? 档案与复验历史将删除 (策略本身不受影响)。`))
                                    drop.mutate(p.strategy_id)
                                }}
                                disabled={drop.isPending}
                                title="移除守护"
                                className="ctl ctl-sm ctl-ghost px-1.5 hover:border-danger/50 hover:text-danger"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
