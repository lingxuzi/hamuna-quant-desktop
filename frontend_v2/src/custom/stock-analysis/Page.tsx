import { useState, useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles, LineChart, History as HistoryIcon, Loader2, Bell, AlertTriangle } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Toolbar, Chip, Note } from '@/components/Toolbar'
import { EmptyState } from '@/components/EmptyState'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { LastStockChip } from '@/components/LastStockChip'
import { AnalysisKChart, type PriceLevel, type LevelType } from '@/components/stock-analysis/AnalysisKChart'
import { PriceAlertDialog } from '@/components/stock-analysis/PriceAlertDialog'
import { api, type KlineRow, type LevelSeries } from '@/lib/api'
import { useLastStock } from '@/lib/useLastStock'
import { QK } from '@/lib/queryKeys'
import { fmtPrice, fmtPct, priceColorClass } from '@/lib/format'
import { toast } from '@/components/Toast'
import { Modal } from '@/components/Modal'
import {
  startAnalysis, findTodayReport, useHistoryReports,
  deleteReport, openHistoryReport, loadHistory,
} from '@/lib/stockAnalysisStore'

/** 设计稿默认标的: 未选股时直接出日 K 与关键位 (design/v2/06-stock-analysis.html)。 */
const DEFAULT_SYMBOL = '600519.SH'
const DEFAULT_NAME = '贵州茅台'
/** 设计稿图注: 日 K 画最近 60 根真实日线。 */
const CHART_BARS = 60

/**
 * 个股分析页 —— 左股池 + 右详情 (design/v2/06-stock-analysis.html)。
 *
 * 与财务分析页的区别:
 *  - 以【行情 + 关键价位】为视觉主体(专用日 K 图表,不复用个股对话框图表)
 *  - AI 分析输出客观技术状态与风险提示(非买卖建议、非财务质量评级)
 *  - 报告胶囊用蓝色系,与财务分析(紫色)并存
 */
export function StockAnalysis() {
  const { last: lastStock, remember: rememberStock } = useLastStock('stock-analysis')
  // 默认选中: 上次查看过的标的 → 设计稿默认标的(贵州茅台)
  const [symbol, setSymbol] = useState<string>(lastStock?.symbol ?? DEFAULT_SYMBOL)
  const [name, setName] = useState<string>(lastStock?.name ?? DEFAULT_NAME)
  const [period, setPeriod] = useState<'daily' | 'weekly'>('daily')
  const [showLevels, setShowLevels] = useState(true)
  const [checking, setChecking] = useState(false)
  const [confirmReport, setConfirmReport] = useState<{ id: string; created_at: string; focus: string } | null>(null)
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const [showPriceAlerts, setShowPriceAlerts] = useState(false)
  const qc = useQueryClient()

  // 进入页面立即加载历史报告(供右侧常驻列表)。store 内部有 historyLoaded 去重, 重复调用安全。
  useEffect(() => { loadHistory() }, [])

  const onSelect = (sym: string, nm: string) => {
    setSymbol(sym)
    setName(nm)
    setConfirmReport(null)
    setShowPriceAlerts(false)
    rememberStock(sym, nm)
  }

  const handleAnalyze = async () => {
    if (!symbol || checking) return
    setChecking(true)
    try {
      // 当日已分析过 → 二次确认(查看今日报告 / 重新分析)
      const today = await findTodayReport(symbol)
      if (today) {
        setConfirmReport({ id: today.id, created_at: today.created_at, focus: today.focus })
      } else {
        await doAnalysis()
      }
    } catch {
      await doAnalysis()
    } finally {
      setChecking(false)
    }
  }

  const doAnalysis = async () => {
    const r = await startAnalysis(symbol, name)
    if (r.error) toast(r.error, 'error')
  }

  const handleAddWatchlist = async () => {
    if (!symbol) return
    try {
      await api.watchlistAdd(symbol)
      await qc.invalidateQueries({ queryKey: QK.watchlistEnriched() })
      toast('已加入自选', 'success')
    } catch (e: any) {
      toast(e?.message || '加入自选失败', 'error')
    }
  }

  // 日 K / 关键位 —— 提到页面级, 供工具条读注与右侧看板共用
  const kline = useQuery({
    // QK 集中键 (start/end 空串 = 滚动最近窗口, 无日期边界)。收益: 键进集中
    // 管理, 且 useQuoteStream 的焦点股增量合并对无边界键生效 — 此前私拼 3 元
    // 键永远修补不到; 注意合并依赖 _focusSymbol (预览弹窗设置焦点), 看板单独
    // 打开时仍靠 60s staleTime 重拉, 与其他日K入口的键 (带真实日期) 不共享。
    queryKey: QK.kline(symbol, '', ''),
    queryFn: () => api.klineDaily(symbol, 250),
    enabled: !!symbol,
    staleTime: 60_000,
    placeholderData: (prev: any, prevQuery: any) =>
      (prevQuery?.queryKey as readonly unknown[] | undefined)?.[1] === symbol ? prev : undefined,
  })

  const levelsQ = useQuery({
    queryKey: QK.stockLevels(symbol),
    queryFn: () => api.stockAnalysisLevels(symbol, 250),
    enabled: !!symbol,
    staleTime: 60_000,
  })

  const rows = kline.data?.rows ?? []
  const levels = (levelsQ.data?.levels ?? {}) as Record<LevelType, PriceLevel[]>
  const levelCount = useMemo(
    () => Object.values(levels).reduce((n, arr) => n + (arr?.length ?? 0), 0),
    [levels],
  )
  const weekly = useMemo(() => toWeekly(rows), [rows])
  const chartRows = (period === 'daily' ? rows : weekly).slice(-CHART_BARS)

  return (
    <div className="min-w-0">
      <PageHeader seal="析"
        title="个股分析"
        subtitle="个股深研"
        right={
          <div className="flex items-center gap-2">
            <StockFinancialSearch onSelect={onSelect} assetTypes="stock,index" />
            {symbol && (
              <>
                <button
                  onClick={handleAnalyze}
                  disabled={checking}
                  className="ctl ctl-md ctl-btn pri inline-flex items-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {checking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  AI 分析
                </button>
                <button
                  onClick={() => setShowPriceAlerts(true)}
                  className="ctl ctl-md ctl-btn inline-flex items-center gap-1.5"
                  title="设置价格点位提醒"
                >
                  <Bell className="h-3.5 w-3.5" />
                  点位提醒
                </button>
              </>
            )}
            <LastStockChip stock={lastStock} onSelect={onSelect} />
          </div>
        }
      />

      <Toolbar
        left={
          <>
            <Chip>{name || symbol} · {symbol.split('.')[0]}</Chip>
            <Note>{rows.length} 个交易日 · 关键位 {levelCount} 项</Note>
          </>
        }
        right={
          <>
            <button className="ctl ctl-md ctl-btn" aria-pressed={period === 'daily'} onClick={() => setPeriod('daily')}>日线</button>
            <button className="ctl ctl-md ctl-btn" aria-pressed={period === 'weekly'} onClick={() => setPeriod('weekly')}>周线</button>
            <button className="ctl ctl-md ctl-btn" aria-pressed={showLevels} onClick={() => setShowLevels(v => !v)}>指标</button>
            <button className="ctl ctl-md ctl-btn" onClick={handleAddWatchlist}>加自选</button>
          </>
        }
      />

      <div className="split">
        <div className="side" aria-label="自选股池">
          <div className="fig-h" style={{ padding: '0 10px 6px' }}>自 选 与 最 近</div>
          <PoolSymbols
            active={symbol}
            activeName={name}
            activePrice={rows.length ? rows[rows.length - 1].close : null}
            activePct={rows.length ? rows[rows.length - 1].change_pct : null}
            onSelect={onSelect}
          />
        </div>

        <div className="main min-w-0">
          {!symbol ? (
            <EmptyState
              icon={LineChart}
              title="选择一只股票开始分析"
              hint="左侧选择自选标的或搜索代码；查看日 K 与关键价位，并可让 AI 做四维分析。"
            />
          ) : (
            <>
              <StockDetailHead rows={rows} name={name} symbol={symbol} />
              <StockAnalysisBoard
                symbol={symbol}
                rows={rows}
                chartRows={chartRows}
                levels={levels}
                series={levelsQ.data?.series}
                seriesDates={levelsQ.data?.dates}
                showLevels={showLevels}
                period={period}
                loading={kline.isLoading}
                error={kline.isError}
              />
            </>
          )}
          <HistorySidebar />
        </div>
      </div>

      {/* 二次确认:已有历史报告 */}
      {confirmReport && (
        <ConfirmModal
          report={confirmReport}
          onView={() => { openHistoryReport(confirmReport.id); setConfirmReport(null) }}
          onRedo={async () => { setConfirmReport(null); await doAnalysis() }}
          onClose={() => setConfirmReport(null)}
        />
      )}

      {/* 个股日 K 详情对话框 */}
      <StockPreviewDialog
        symbol={previewSymbol}
        name={previewSymbol === symbol ? name : undefined}
        triggerInfo={null}
        onClose={() => setPreviewSymbol(null)}
      />

      {showPriceAlerts && symbol && (
        <PriceAlertDialog
          key={symbol}
          symbol={symbol}
          name={name}
          onClose={() => setShowPriceAlerts(false)}
        />
      )}
    </div>
  )
}

// ===== 左栏: 自选 + 最近 =====
function PoolSymbols({ active, activeName, activePrice, activePct, onSelect }: {
  active: string
  activeName: string
  activePrice: number | null | undefined
  activePct: number | null | undefined
  onSelect: (sym: string, name: string) => void
}) {
  const { data } = useQuery({ queryKey: QK.watchlistEnriched(), queryFn: () => api.watchlistEnriched(), staleTime: 60_000 })
  const rows = data?.rows ?? []
  const inWatchlist = rows.some((r: any) => r.symbol === active)
  return (
    <>
      {/* 不在自选里的当前标的也算「最近」 */}
      {active && !inWatchlist && (
        <button className="q on" onClick={() => onSelect(active, activeName)}>
          <b>{activeName || active}</b>
          <em className={priceColorClass(activePct)}>{activePrice != null ? fmtPrice(activePrice) : '—'}</em>
          <span>{active} · {fmtPct(activePct)}</span>
        </button>
      )}
      {rows.slice(0, 12).map((r: any) => {
        const pct = r.rt_pct ?? r.change_pct
        const price = r.rt_price ?? r.close
        return (
          <button
            key={r.symbol}
            className={`q${r.symbol === active ? ' on' : ''}`}
            onClick={() => onSelect(r.symbol, r.rt_name ?? r.name ?? '')}
          >
            <b>{r.rt_name ?? r.name ?? r.symbol}</b>
            <em className={priceColorClass(pct)}>{price != null ? fmtPrice(price) : '—'}</em>
            <span>{r.symbol} · {fmtPct(pct)}</span>
          </button>
        )
      })}
      {rows.length === 0 && !active && <div className="idx-empty" style={{ minHeight: 80 }}>自选为空</div>}
    </>
  )
}

// ===== 详情抬头: 名称 + 代码 + 现价 + 涨跌 (design/v2/06 的 .detail-hd) =====
function StockDetailHead({ rows, name, symbol }: { rows: KlineRow[]; name: string; symbol: string }) {
  const last = rows[rows.length - 1]
  const prev = rows[rows.length - 2]
  const changeAmt = last && prev ? last.close - prev.close : null
  // change_pct 与自算值都必须是小数制, fmtPct 统一 ×100; 之前 fallback 乘了 100,
  // 两个分支口径不一致, 缺 change_pct 时会多出两个数量级。
  const pct = last?.change_pct ?? (last && prev && prev.close ? (last.close - prev.close) / prev.close : null)
  return (
    <div className="detail-hd">
      <h2>{name || symbol}</h2>
      <span className="s">{symbol}</span>
      {last && (
        <>
          <span className="ml-auto font-mono text-[18px] tabular-nums text-foreground">{fmtPrice(last.close)}</span>
          <span className={`font-mono text-[11px] ${priceColorClass(pct)}`}>
            {changeAmt != null ? `${changeAmt >= 0 ? '+' : ''}${changeAmt.toFixed(2)}` : '—'} · {fmtPct(pct)}
          </span>
        </>
      )}
    </div>
  )
}

// ===== 分析看板:日 K + 关键价位 =====
function StockAnalysisBoard({
  symbol, rows, chartRows, levels, series, seriesDates, showLevels, period, loading, error,
}: {
  symbol: string
  rows: KlineRow[]
  chartRows: KlineRow[]
  levels: Record<LevelType, PriceLevel[]>
  series?: LevelSeries
  seriesDates?: string[]
  showLevels: boolean
  period: 'daily' | 'weekly'
  loading: boolean
  error: boolean
}) {
  if (loading) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-[20px] w-[20px] animate-spin text-muted" /></div>
  }

  if (error) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="日 K 数据加载失败"
        hint="请检查网络或数据源配置后重试。"
      />
    )
  }

  if (rows.length === 0) {
    return <EmptyState icon={LineChart} title="暂无日 K 数据" hint="该标的尚未同步日 K,请先在数据页或自选页同步。" />
  }

  const last = rows[rows.length - 1]
  const amount = fmtAmount(last.amount)
  const turnover = last.turnover_rate != null ? Number(last.turnover_rate) : null
  const keyRows = sortByDistance(pickLevels(levels, KEY_LEVEL_TYPES))
  const channelRows = pickLevels(levels, CHANNEL_LEVEL_TYPES).sort((a, b) => b.value - a.value)

  return (
    <>
      {/* 5 格指标: 今开/最高/最低/成交额/换手率 */}
      <div className="kv5">
        <div><span className="kv5-k">今开</span><span className="kv5-v">{fmtPrice(last.open)}</span></div>
        <div><span className="kv5-k">最高</span><span className="kv5-v">{fmtPrice(last.high)}</span></div>
        <div><span className="kv5-k">最低</span><span className="kv5-v">{fmtPrice(last.low)}</span></div>
        <div>
          <span className="kv5-k">成交额</span>
          <span className="kv5-v">{amount.v}{amount.u && <span className="kv5-sub">{amount.u}</span>}</span>
        </div>
        <div>
          <span className="kv5-k">换手率</span>
          <span className="kv5-v">{turnover != null ? turnover.toFixed(2) : '—'}{turnover != null && <span className="kv5-sub">%</span>}</span>
        </div>
      </div>

      <div className="plot">
        <div className="plot-hd">
          <b>{period === 'daily' ? '日 K' : '周 K'}</b><span>{period === 'daily' ? 'DAILY' : 'WEEKLY'} · {chartRows.length} 根</span>
          <div className="leg"><i className="u">阳线</i><i className="d">阴线</i><i className="a">关键位</i></div>
        </div>
        <div className="plotbody">
          <AnalysisKChart
            key={`${symbol}-${period}-${showLevels}`}
            rows={chartRows}
            levels={levels}
            series={series}
            seriesDates={seriesDates}
            defaultLevelTypes={showLevels ? ['sr', 'pivot', 'keltner_s'] : []}
            height={480}
          />
        </div>
      </div>

      {/* 关键位: 支撑与压力 / 通道与止损 两栏 */}
      <div className="lv-cols">
        <div>
          <div className="fig-h">关 键 位 · 支撑与压力</div>
          <table className="lv">
            <tbody>
              {keyRows.map(p => <LevelRow key={`${p.type}${p.value}`} p={p} />)}
              {keyRows.length === 0 && <tr><td className="n">暂无关键位</td></tr>}
            </tbody>
          </table>
        </div>
        <div>
          <div className="fig-h">通 道 与 止 损</div>
          <table className="lv">
            <tbody>
              {channelRows.map(p => <LevelRow key={`${p.type}${p.value}`} p={p} />)}
              {channelRows.length === 0 && <tr><td className="n">暂无通道位</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

/** 关键位表: 压力/支撑/枢轴/极值/布林/缺口 */
const KEY_LEVEL_TYPES: LevelType[] = ['sr', 'pivot', 'extreme', 'boll', 'gap']
/** 通道与止损表: ATR 通道/整数关口/Keltner 三档/Fib */
const CHANNEL_LEVEL_TYPES: LevelType[] = ['atr_stop', 'round', 'keltner_s', 'keltner_m', 'keltner_l', 'fib']

function pickLevels(levels: Record<LevelType, PriceLevel[]>, types: LevelType[]): PriceLevel[] {
  const out: PriceLevel[] = []
  for (const t of types) out.push(...(levels[t] ?? []))
  return out
}

/** 排序: 压力位由近及远 → 中性位 → 支撑位由近及远 */
function sortByDistance(list: PriceLevel[]): PriceLevel[] {
  const res = list.filter(p => p.side === 'resistance').sort((a, b) => a.value - b.value)
  const neu = list.filter(p => p.side === 'neutral')
  const sup = list.filter(p => p.side === 'support').sort((a, b) => b.value - a.value)
  return [...res, ...neu, ...sup]
}

/** 第三列注脚: 该点位怎么来的 (设计稿口径) */
function levelNote(p: PriceLevel): string {
  switch (p.type) {
    case 'pivot': return p.side === 'neutral' ? '当前价接近' : '枢轴计算'
    case 'extreme': return /前高|前低/.test(p.label) ? '历史密集' : '区间极值'
    case 'boll': return p.label.includes('上轨') ? '20 日 2σ' : p.label.includes('下轨') ? '20 日 −2σ' : '20 日均线'
    case 'sr': return p.label.includes('POC') ? '筹码峰' : '历史密集'
    case 'gap': return '未回补'
    case 'round': return '心理位'
    case 'atr_stop': return p.side === 'resistance' ? '14 日 ATR' : '止损参考'
    case 'keltner_s': return 'Keltner 20'
    case 'keltner_m': return 'Keltner 60'
    case 'keltner_l': return 'Keltner 120'
    case 'fib': return '回撤位'
    default: return ''
  }
}

function LevelRow({ p }: { p: PriceLevel }) {
  const side = p.side === 'resistance' ? 'r' : p.side === 'support' ? 's' : ''
  return (
    <tr>
      <td className="n">{p.label}</td>
      <td className={`v ${side}`}>{fmtPrice(p.value)}</td>
      <td className="n">{levelNote(p)}</td>
    </tr>
  )
}

/** 成交额: 亿/万 分档, 与设计稿「62.0 亿」同口径 */
function fmtAmount(v: number | null | undefined): { v: string; u: string } {
  if (v == null || Number.isNaN(v)) return { v: '—', u: '' }
  if (v >= 1e8) return { v: (v / 1e8).toFixed(1), u: '亿' }
  if (v >= 1e4) return { v: (v / 1e4).toFixed(1), u: '万' }
  return { v: v.toFixed(0), u: '' }
}

/** 日线 → 周线重采样 (按自然周, 首开/最高/最低/末收/量累加) */
function toWeekly(rows: KlineRow[]): KlineRow[] {
  const out: KlineRow[] = []
  let cur: KlineRow | null = null
  let curKey = ''
  for (const r of rows) {
    const key = weekKey(r.date)
    if (!cur || key !== curKey) {
      if (cur) out.push(cur)
      cur = { ...r }
      curKey = key
    } else {
      cur.high = Math.max(cur.high, r.high)
      cur.low = Math.min(cur.low, r.low)
      cur.close = r.close
      cur.volume = (cur.volume ?? 0) + (r.volume ?? 0)
      cur.amount = (cur.amount ?? 0) + (r.amount ?? 0)
    }
  }
  if (cur) out.push(cur)
  // 周涨跌幅按相邻周收盘计算; 保持小数制, 与日线 change_pct 同口径 (fmtPct 才 ×100)
  for (let i = 0; i < out.length; i++) {
    const prev = out[i - 1]
    out[i].change_pct = prev && prev.close ? (out[i].close - prev.close) / prev.close : undefined
  }
  return out
}

/** 自然周键: 该日期所在周的周一 (UTC 计算, 避免时区偏移) */
function weekKey(date: string): string {
  const d = new Date(`${String(date).slice(0, 10)}T00:00:00Z`)
  const day = (d.getUTCDay() + 6) % 7
  d.setUTCDate(d.getUTCDate() - day)
  return d.toISOString().slice(0, 10)
}

// ===== 历史报告: 详情下方常驻列表 =====
function HistorySidebar() {
  const { reports, loaded } = useHistoryReports()

  return (
    <section className="hist-panel">
      <div className="fig-h">
        <HistoryIcon className="h-3.5 w-3.5 text-accent" />
        历 史 报 告
        {loaded && reports.length > 0 && <em className="ml-auto">{reports.length}</em>}
      </div>
      {!loaded ? (
        <div className="flex items-center justify-center py-10"><Loader2 className="h-[16px] w-[16px] animate-spin text-muted" /></div>
      ) : reports.length === 0 ? (
        <p className="hist-empty">还没有任何个股分析报告 · 点上方「AI 分析」生成</p>
      ) : (
        <div className="max-h-[420px] overflow-y-auto">
          {reports.map(r => (
            <div key={r.id} className="hist-row group">
              <button onClick={() => openHistoryReport(r.id)} className="flex-1 text-left min-w-0">
                <span className="hist-name">{r.name || r.symbol}</span>
                <span className="hist-sym">{r.symbol}</span>
                <span className="hist-meta">
                  {fmtRelative(r.created_at)}
                  {r.close != null && ` · ${fmtPrice(r.close)}`}
                  {r.focus && ` · ${r.focus}`}
                </span>
                {r.summary && <span className="hist-sum">{r.summary}</span>}
              </button>
              <button
                onClick={() => { deleteReport(r.id); toast('已删除', 'success') }}
                className="hist-del opacity-0 group-hover:opacity-100"
                title="删除"
              >
                删除
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// ===== 二次确认弹窗 =====
function ConfirmModal({ report, onView, onRedo, onClose }: {
  report: { id: string; created_at: string; focus: string }
  onView: () => void
  onRedo: () => void
  onClose: () => void
}) {
  return (
    <Modal
      onClose={onClose}
      labelledBy="stock-analysis-confirm-title"
      panelClassName="w-full max-w-sm bg-surface border border-border p-5 shadow-2xl"
      overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
    >
      <div className="flex items-center gap-2 mb-2">
        <HistoryIcon className="h-[16px] w-[16px] text-accent" />
        <span id="stock-analysis-confirm-title" className="text-sm font-medium text-foreground">该个股已有分析报告</span>
      </div>
      <p className="text-xs text-secondary leading-relaxed mb-1">
        最近一次报告生成于 <span className="text-foreground">{fmtRelative(report.created_at)}</span>。
      </p>
      {report.focus && <p className="text-xs text-muted mb-1">关注点: {report.focus}</p>}
      <p className="text-xs text-muted mb-4">可直接查看历史,或重新生成一份新报告。</p>
      <div className="flex gap-2">
        <button onClick={onView} className="ctl ctl-md ctl-btn flex-1 justify-center">查看历史</button>
        <button onClick={onRedo} className="ctl ctl-md ctl-btn pri flex-1 justify-center">重新分析</button>
      </div>
    </Modal>
  )
}

function fmtRelative(iso: string): string {
  try {
    const t = new Date(iso).getTime()
    const diff = Date.now() - t
    if (diff < 60_000) return '刚刚'
    if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分钟前`
    if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`
    if (diff < 7 * 86400_000) return `${Math.floor(diff / 86400_000)} 天前`
    return new Date(iso).toLocaleDateString('zh-CN')
  } catch { return iso }
}
