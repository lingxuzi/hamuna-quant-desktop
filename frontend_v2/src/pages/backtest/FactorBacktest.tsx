import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { DailyResultStaleNotice } from '@/components/data/DailyDataHealthNotice'
import { motion } from 'framer-motion'
import { Play, BarChart3, BookmarkPlus, Clock } from 'lucide-react'
import { api, type FactorColumn, type FactorBacktestResult, type GroupStat } from '@/lib/api'
import { fmtPct, priceColorClass } from '@/lib/format'
import { EmptyState } from '@/components/EmptyState'
import { DatePicker } from '@/components/DatePicker'
import { toast } from '@/components/Toast'
import { QK } from '@/lib/queryKeys'
import { FactorICChart } from './charts/FactorICChart'
import { FactorGroupNavChart } from './charts/FactorGroupNavChart'
import { factorResultCandidate } from './researchCandidates'
import { INPUT_MD, SELECT_MD } from '@/components/controls'
import { cn } from '@/lib/cn'
import { cnAddMonths, cnToday } from '@/lib/cnDate'

const monthsAgo = (months: number) => cnAddMonths(cnToday(), -months)
const TODAY = cnToday()
const THREE_MONTHS_AGO = monthsAgo(3)

const INPUT_CLS = cn(INPUT_MD, 'w-full transition-colors duration-150 ease-smooth')

const SELECT_CLS = cn(SELECT_MD, 'w-full transition-colors duration-150 ease-smooth')

/** 读数带的一格: 标签 / 数值 / 旁注 (设计稿 .readout 的一格)。 */
function ReadoutCell({ label, value, note, tone }: {
  label: string
  value: string
  note?: string
  /** up = 涨色 (bull), down = 跌色 (bear) */
  tone?: 'up' | 'down'
}) {
  return (
    <div className={tone}>
      <span className="k">{label}</span>
      <div className="v">{value}</div>
      {note && <small>{note}</small>}
    </div>
  )
}

function LoadingPanel({ symbolsText }: { symbolsText: string }) {
  return (
    <div className="space-y-4">
      <div className="border-b border-border pb-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-medium text-foreground">正在计算因子分析</div>
            <div className="mt-1 text-xs text-muted">{symbolsText} · 完成后会一次性刷新 IC、分层收益和净值曲线。</div>
          </div>
          <div className="h-[32px] w-[32px] rounded-full border-2 border-accent/25 border-t-accent animate-spin" />
        </div>
        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-base">
          <div className="h-full w-1/2 rounded-full bg-accent/70 animate-pulse" />
        </div>
      </div>

      <div className="hgrid grid grid-cols-2 md:grid-cols-4">
        {['读取因子', '计算 IC', '分层回测', '汇总指标'].map(item => (
          <div key={item} className="p-3.5">
            <div className="h-2 w-10 bg-accent/30 animate-pulse" />
            <div className="mt-3 text-xs text-secondary">{item}</div>
          </div>
        ))}
      </div>

      <div className="plot">
        <div className="plot-hd">
          <b>分层净值预览</b><span>GROUP NAV</span>
          <div className="leg"><i className="m">等待后端返回完整结果</i></div>
        </div>
        <div className="plotbody p-4">
          <div className="flex h-[220px] items-end gap-2 opacity-70">
            {[46, 38, 54, 50, 64, 58, 74, 68, 84, 78, 90, 86].map((h, i) => (
              <div key={i} className="flex-1 bg-accent/20 animate-pulse" style={{ height: `${h}%` }} />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export function FactorBacktest({ initialFactorName = 'momentum_20d', autoRun = false, onRunStateChange }: {
  initialFactorName?: string
  autoRun?: boolean
  /** 回测运行态上报 (独立页工具条的「本次未运行 / 运行中 / 已出结果」签) */
  onRunStateChange?: (state: { running: boolean; hasResult: boolean }) => void
}) {
  const queryClient = useQueryClient()
  const [factorName, setFactorName] = useState(initialFactorName)
  const [symbols, setSymbols] = useState('')
  const [assetType, setAssetType] = useState<'stock' | 'etf'>('stock')
  const [start, setStart] = useState(THREE_MONTHS_AGO)
  const [end, setEnd] = useState(TODAY)
  const [nGroups, setNGroups] = useState(5)
  const [weight, setWeight] = useState<'equal' | 'factor_weight'>('equal')
  const [rebalance, setRebalance] = useState<'daily' | 'weekly' | 'monthly'>('daily')
  const [fees, setFees] = useState('2')
  const [slippage, setSlippage] = useState('5')
  const [result, setResult] = useState<FactorBacktestResult | null>(null)

  const columns = useQuery({
    queryKey: QK.factorColumns,
    queryFn: api.factorColumns,
  })


  // 按 group 分类的因子
  const factorGroups = useMemo(() => {
    const cols = columns.data?.columns ?? []
    const groups: Record<string, FactorColumn[]> = {}
    for (const c of cols) {
      ;(groups[c.group] ??= []).push(c)
    }
    return groups
  }, [columns.data])

  // 当前因子描述
  const factorDesc = useMemo(() => {
    return columns.data?.columns.find(c => c.id === factorName)?.desc ?? ''
  }, [columns.data, factorName])

  const resultFactorLabel = useMemo(() => {
    const resultFactorName = String(result?.config.factor_name ?? factorName)
    return columns.data?.columns.find(c => c.id === resultFactorName)?.label ?? resultFactorName
  }, [columns.data, factorName, result])

  const run = useMutation({
    mutationFn: () =>
      api.factorRun({
        factor_name: factorName,
        asset_type: assetType,
        symbols: symbols ? symbols.split(/[,，;；、\s]+/).map(s => s.trim()).filter(Boolean) : null,
        start: start || null,
        end: end || undefined,
        n_groups: nGroups,
        rebalance,
        weight,
        fees_pct: Number(fees) / 10000,
        slippage_bps: Number(slippage),
      }),
    onSuccess: (data) => {
      if (data.error) {
        setResult(data)
      } else {
        setResult(data)
      }
    },
  })

  // 详情面板等嵌入场景: 挂载即自动运行一次 (默认手动, 页面行为不变)
  const autoRunRef = useRef(false)
  useEffect(() => {
    if (!autoRun || autoRunRef.current) return
    autoRunRef.current = true
    run.mutate()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun])

  useEffect(() => {
    onRunStateChange?.({ running: run.isPending, hasResult: result != null })
  }, [run.isPending, result, onRunStateChange])
  const saveCandidate = useMutation({
    mutationFn: () => {
      if (!result) throw new Error('暂无因子结果')
      return api.researchCandidateCreate(factorResultCandidate(result, resultFactorLabel))
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QK.researchCandidates })
      toast('已保存到候选方案', 'success')
    },
    onError: error => toast(`保存失败 · ${String((error as Error).message || error)}`, 'error'),
  })

  const applyRange = (months: number) => {
    setStart(monthsAgo(months))
    setEnd(cnToday())
  }

  const applyAllRange = () => {
    setStart('')
    setEnd(cnToday())
  }

  const rangeKey = end === TODAY && start === THREE_MONTHS_AGO
    ? '3m'
    : end === TODAY && start === monthsAgo(6)
      ? '6m'
      : end === TODAY && start === monthsAgo(12)
        ? '1y'
        : end === TODAY && start === ''
          ? 'all'
          : 'custom'
  const rangeTitle = rangeKey === '3m'
    ? '近 3 个月'
    : rangeKey === '6m'
      ? '近 6 个月'
      : rangeKey === '1y'
        ? '近 1 年'
        : rangeKey === 'all'
          ? '全部历史'
          : '自定义区间'

  const hasIC = !!result && result.ic_series.length > 0
  const hasNav = !!result && result.group_nav.length > 0

  return (
    <div className="h-full min-h-0 overflow-hidden border-t border-rule-strong grid grid-cols-1 xl:grid-cols-[18rem_minmax(0,1fr)]">
      {/* 配置栏 (design/v2/14 的 .cfg) */}
      <section className="min-w-0 space-y-4 overflow-y-auto border-b border-border px-3 pb-4 xl:border-b-0 xl:border-r xl:pr-4">
        <div className="border-b border-border/70 pb-2">
          <div className="text-xs font-semibold text-foreground">因子配置</div>
          <div className="mt-0.5 text-[10px] leading-4 text-muted">选择因子、区间和分组方式。默认最近 3 个月。</div>
        </div>

        <div>
          <div className="figcap">因 子</div>
          <select
            value={factorName}
            onChange={e => setFactorName(e.target.value)}
            className={SELECT_CLS}
          >
            {Object.entries(factorGroups).map(([group, cols]) => (
              <optgroup key={group} label={group}>
                {cols.map(c => (
                  <option key={c.id} value={c.id}>{c.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          {factorDesc && (
            <p className="mt-1 text-[9.5px] leading-relaxed text-muted">{factorDesc}</p>
          )}
          <p className="mt-1 text-[9.5px] leading-relaxed text-muted">
            因子库 {columns.data?.columns.length ?? 0} 个, {Object.keys(factorGroups).length} 个维度。
          </p>
        </div>

        <div>
          <div className="figcap">资 产 类 型</div>
          <div className="seg">
            {(['stock', 'etf'] as const).map(t => (
              <button
                key={t}
                type="button"
                onClick={() => { setAssetType(t); setSymbols('') }}
                aria-pressed={assetType === t}
              >
                {t === 'stock' ? '股票' : 'ETF'}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="figcap">标 的 池</div>
          <label className="mb-1 block text-[10.5px] text-secondary">
            代码 (逗号分隔，留空=全市场{assetType === 'etf' ? ' ETF' : ''})
          </label>
          <input
            type="text"
            value={symbols}
            onChange={e => setSymbols(e.target.value)}
            placeholder="留空则使用全市场，建议最近3个月"
            className={`ctl ctl-md ctl-input w-full px-2.5 bg-surface font-mono focus:border-accent duration-150 ease-smooth`}
          />
          <p className="mt-1 text-[9.5px] leading-relaxed text-muted">留空表示全市场。</p>
        </div>

        <div>
          <div className="figcap">回 测 范 围</div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-1 block text-[10.5px] text-secondary">开始</label>
              <DatePicker
                value={start}
                onChange={setStart}
                max={end || undefined}
                placeholder="全部历史"
                className="w-full"
                buttonClassName="w-full justify-start"
                align="left"
              />
            </div>
            <div>
              <label className="mb-1 block text-[10.5px] text-secondary">结束</label>
              <DatePicker
                value={end}
                onChange={setEnd}
                min={start || undefined}
                className="w-full"
                buttonClassName="w-full justify-start"
              />
            </div>
          </div>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="text-[10.5px] text-secondary">{rangeTitle}</span>
            <div className="seg">
              <button type="button" onClick={() => applyRange(3)} aria-pressed={rangeKey === '3m'}>3个月</button>
              <button type="button" onClick={() => applyRange(6)} aria-pressed={rangeKey === '6m'}>6个月</button>
              <button type="button" onClick={() => applyRange(12)} aria-pressed={rangeKey === '1y'}>1年</button>
              <button type="button" onClick={applyAllRange} aria-pressed={rangeKey === 'all'}>全部</button>
            </div>
          </div>
        </div>

        <div>
          <div className="figcap">分 组 与 调 仓</div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-1 block text-[10.5px] text-secondary">分组数</label>
              <select value={nGroups} onChange={e => setNGroups(Number(e.target.value))} className={SELECT_CLS}>
                <option value={3}>3组</option>
                <option value={5}>5组</option>
                <option value={10}>10组</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-[10.5px] text-secondary">权重</label>
              <select value={weight} onChange={e => setWeight(e.target.value as any)} className={SELECT_CLS}>
                <option value="equal">等权</option>
                <option value="factor_weight">因子加权</option>
              </select>
            </div>
          </div>
          <label className="mb-1 mt-2 block text-[10.5px] text-secondary">调仓频率</label>
          <select value={rebalance} onChange={e => setRebalance(e.target.value as any)} className={SELECT_CLS}>
            <option value="daily">日度</option>
            <option value="weekly">周度</option>
            <option value="monthly">月度</option>
          </select>
        </div>

        <div>
          <div className="figcap">成 本</div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-1 block text-[10.5px] text-secondary">佣金 (万分之)</label>
              <input type="number" min="0" value={fees} onChange={e => setFees(e.target.value)}
                className={INPUT_CLS} />
            </div>
            <div>
              <label className="mb-1 block text-[10.5px] text-secondary">滑点 (bp)</label>
              <input type="number" min="0" value={slippage} onChange={e => setSlippage(e.target.value)}
                className={INPUT_CLS} />
            </div>
          </div>
        </div>

        <button
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="ctl ctl-md ctl-btn pri w-full inline-flex items-center justify-center gap-1.5 px-2.5 hover:bg-accent/90 duration-150 ease-smooth disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {run.isPending ? '分析中…' : '开始因子分析'}
        </button>
      </section>

      {/* 结果区 (design/v2/14 的 .main) */}
      <section className="min-w-0 space-y-4 overflow-y-auto bg-base/15 px-3 pb-4 xl:pl-5">
        {result?.error && (
          <div className="border-l-2 border-danger bg-danger/10 px-3 py-2 text-sm text-danger">
            {result.error}
          </div>
        )}

        {run.isError && (
          <div className="border-l-2 border-danger bg-danger/10 px-3 py-2 text-sm text-danger">
            {String((run.error as any).message)}
          </div>
        )}

        {!result && !run.isPending && (
          <EmptyState
            icon={BarChart3}
            title="选择因子并开始分析"
            hint="因子回测度量因子的历史有效性 ( IC/IR ) 和分层收益差异。服务器建议优先使用最近3个月；长周期建议本机或 8GB 以上内存环境运行。"
          />
        )}

        {run.isPending && result && (
          <div className="border-b border-border pb-3 text-xs text-secondary">
            正在重新计算，当前暂时展示上一次因子分析结果，完成后会自动替换。
          </div>
        )}

        {run.isPending && !result && (
          <LoadingPanel symbolsText={symbols ? `${symbols.split(',').length} 只标的` : '全市场 · 当前区间'} />
        )}

        <DailyResultStaleNotice result={result} assetType={result?.config.asset_type === 'etf' ? 'etf' : 'stock'} />
        {result && !result.error && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="space-y-4"
          >
            {/* 跑完之后的读数带 */}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="figcap mb-0">因 子 历 史 有 效 性</div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => saveCandidate.mutate()}
                  disabled={saveCandidate.isPending}
                  className="ctl-btn ctl-sm inline-flex items-center gap-1"
                >
                  <BookmarkPlus className="h-[12px] w-[12px]" />
                  {saveCandidate.isPending ? '保存中' : '保存候选'}
                </button>
                <span className="text-[10px] text-muted">
                  Rank IC · {rebalance === 'daily' ? '日度' : rebalance === 'weekly' ? '周度' : '月度'}调仓
                </span>
                {result.elapsed_ms > 0 && (
                  <span className="flex items-center gap-1 text-[10px] text-muted">
                    <Clock className="h-[12px] w-[12px]" />
                    <span className="num">{result.elapsed_ms.toFixed(0)} ms</span>
                  </span>
                )}
              </div>
            </div>

            <div className="readout">
              <ReadoutCell
                label="IC 均 值"
                value={result.ic_mean != null ? fmtPct(result.ic_mean) : '—'}
                note="|IC| > 0.03 视为有效"
                tone={result.ic_mean == null ? undefined : result.ic_mean > 0.03 ? 'up' : result.ic_mean < -0.03 ? 'down' : undefined}
              />
              <ReadoutCell
                label="IC 标 准 差"
                value={result.ic_std != null ? fmtPct(result.ic_std) : '—'}
                note="日频横截面相关波动"
              />
              <ReadoutCell
                label="ICIR"
                value={result.ir != null ? result.ir.toFixed(2) : '—'}
                note="|ICIR| > 0.5 视为稳定"
                tone={result.ir == null ? undefined : Math.abs(result.ir) > 0.5 ? (result.ir > 0 ? 'up' : 'down') : undefined}
              />
              <ReadoutCell
                label="IC 胜 率"
                value={result.ic_win_rate != null ? fmtPct(result.ic_win_rate) : '—'}
                note="IC 为正的天数占比"
              />
              <ReadoutCell
                label="多 空 收 益"
                value={result.long_short_stats?.total_return != null ? fmtPct(result.long_short_stats.total_return) : '—'}
                note="最高组 − 最低组"
                tone={result.long_short_stats?.total_return == null ? undefined : result.long_short_stats.total_return > 0 ? 'up' : 'down'}
              />
              <ReadoutCell
                label="样 本"
                value={`${result.n_symbols} / ${result.n_dates}`}
                note="标的数 / 交易日数"
              />
            </div>

            {result.ic_mean == null && (
              <div className="border-l-2 border-border pl-3 text-xs leading-relaxed text-muted">
                标的数量过少，无法计算 IC/IR（需 ≥2 只）。可清空标的使用全市场，或补充更多标的。
              </div>
            )}

            {/* IC 时序 + 分层净值 */}
            {(hasIC || hasNav) && (
              <div className={`grid gap-4 ${hasIC && hasNav ? 'xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]' : ''}`}>
                {hasIC && (
                  <div className="plot">
                    <div className="plot-hd">
                      <b>IC 时序</b><span>IC SERIES</span>
                    </div>
                    <div className="plotbody p-2">
                      <FactorICChart result={result} />
                    </div>
                  </div>
                )}
                {hasNav && (
                  <div className="plot">
                    <div className="plot-hd">
                      <b>分层净值</b><span>GROUP NAV</span>
                    </div>
                    <div className="plotbody p-2">
                      <FactorGroupNavChart result={result} />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* 分层统计表 */}
            {result.group_stats.length > 0 && (
              <div>
                <div className="figcap">分 组 统 计</div>
                <table className="dt">
                  <thead>
                    <tr>
                      <th scope="col" className="text-left">分组</th>
                      <th scope="col">总收益</th>
                      <th scope="col">年化</th>
                      <th scope="col">最大回撤</th>
                      <th scope="col">夏普</th>
                      <th scope="col">胜率</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.group_stats.map((g: GroupStat) => (
                      <tr key={g.group}>
                        <td>{g.label}</td>
                        <td className={priceColorClass(g.total_return)}>{fmtPct(g.total_return)}</td>
                        <td className={priceColorClass(g.annual_return)}>{fmtPct(g.annual_return)}</td>
                        <td className="text-bear">{fmtPct(g.max_drawdown)}</td>
                        <td>{g.sharpe?.toFixed(2)}</td>
                        <td>{fmtPct(g.win_rate)}</td>
                      </tr>
                    ))}
                    {/* 多空行 */}
                    {result.long_short_stats?.total_return != null && (
                      <tr className="bg-accent/[0.04]">
                        <td className="font-medium text-accent">
                          多空({result.long_short_stats.top_group ?? ''}-{result.long_short_stats.bottom_group ?? ''})
                        </td>
                        <td className={`font-medium ${priceColorClass(result.long_short_stats.total_return as number)}`}>
                          {fmtPct(result.long_short_stats.total_return as number)}
                        </td>
                        <td>—</td>
                        <td className="text-bear">{fmtPct(result.long_short_stats.max_drawdown as number)}</td>
                        <td>—</td>
                        <td>—</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}

            {/* 数据概要 */}
            <div className="flex flex-wrap items-center gap-4 text-[10px] text-muted">
              <span>{result.n_symbols} 只标的</span>
              <span>{result.n_dates} 个交易日</span>
              <span className="num">run_id: {result.run_id}</span>
            </div>
          </motion.div>
        )}
      </section>
    </div>
  )
}
