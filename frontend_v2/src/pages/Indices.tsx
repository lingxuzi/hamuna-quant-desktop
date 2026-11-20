import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Lock, RefreshCw } from 'lucide-react'
import { api, type IndexQuote, type KlineRow, type MinuteKlineRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useCapabilities } from '@/lib/useSharedQueries'
import { PageHeader } from '@/components/PageHeader'
import { EChartsCandlestick, type OHLC } from '@/components/EChartsCandlestick'
import { EChartsIntraday } from '@/components/EChartsIntraday'
import { cnAddMonths, cnToday } from '@/lib/cnDate'

function defaultRange() {
  const end = cnToday()
  return { start: cnAddMonths(end, -6), end }
}

function toOHLC(rows: KlineRow[]): OHLC[] {
  return rows
    .filter(r => r?.date != null && r.open != null && r.close != null)
    .map(r => ({
      date: typeof r.date === 'string' ? r.date.slice(0, 10) : String(r.date),
      open: Number(r.open),
      high: Number(r.high),
      low: Number(r.low),
      close: Number(r.close),
      volume: Number(r.volume ?? 0),
      ma5: r.ma5 != null ? Number(r.ma5) : null,
      ma10: r.ma10 != null ? Number(r.ma10) : null,
      ma20: r.ma20 != null ? Number(r.ma20) : null,
      ma60: r.ma60 != null ? Number(r.ma60) : null,
      macd_dif: r.macd_dif != null ? Number(r.macd_dif) : null,
      macd_dea: r.macd_dea != null ? Number(r.macd_dea) : null,
      macd_hist: r.macd_hist != null ? Number(r.macd_hist) : null,
      rsi_6: r.rsi_6 != null ? Number(r.rsi_6) : null,
      rsi_14: r.rsi_14 != null ? Number(r.rsi_14) : null,
      rsi_24: r.rsi_24 != null ? Number(r.rsi_24) : null,
      kdj_k: r.kdj_k != null ? Number(r.kdj_k) : null,
      kdj_d: r.kdj_d != null ? Number(r.kdj_d) : null,
      kdj_j: r.kdj_j != null ? Number(r.kdj_j) : null,
      boll_upper: r.boll_upper != null ? Number(r.boll_upper) : null,
      boll_lower: r.boll_lower != null ? Number(r.boll_lower) : null,
    }))
}

/** 设计稿的千分位: 3,649.18 */
function fmtNum(v: number | null | undefined, digits = 2) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function fmtPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

/** 涨跌语义只改颜色: u 涨 / d 跌 / flat 平 */
const tone = (v: number | null | undefined) => (v == null ? '' : Number(v) > 0 ? 'u' : Number(v) < 0 ? 'd' : 'flat')

// 设计稿左栏 10 个核心指数; 接口对全部行返回 name: null, 中文名在这里补
const CORE_INDEXES = [
  { symbol: '000001.SH', name: '上证指数' },
  { symbol: '399001.SZ', name: '深证成指' },
  { symbol: '399006.SZ', name: '创业板指' },
  { symbol: '000680.SH', name: '科创综指' },
  { symbol: '000300.SH', name: '沪深300' },
  { symbol: '000016.SH', name: '上证50' },
  { symbol: '000905.SH', name: '中证500' },
  { symbol: '000852.SH', name: '中证1000' },
  { symbol: '399005.SZ', name: '中小100' },
  { symbol: '000688.SH', name: '科创50' },
]

export function Indices() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const symbolParam = searchParams.get('symbol') ?? ''
  const [selected, setSelected] = useState<string>(symbolParam)
  const [range] = useState(defaultRange)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [linkedPrice, setLinkedPrice] = useState<number | null>(null)
  const [tab, setTab] = useState<'daily' | 'minute'>('daily')
  /** 页头「搜索指数」: 打开左栏筛选框, 名字或代码匹配 */
  const [filterOpen, setFilterOpen] = useState(false)
  const [filter, setFilter] = useState('')

  // 分时数据依赖分钟K批量数据 (kline.minute.batch)
  const caps = useCapabilities()
  const hasMinuteCap = !!caps.data?.capabilities?.['kline.minute.batch']

  const selectedSymbol = selected || CORE_INDEXES[0].symbol

  useEffect(() => {
    if (symbolParam && symbolParam !== selected) setSelected(symbolParam)
  }, [selected, symbolParam])

  const selectIndex = (symbol: string) => {
    setSelected(symbol)
    setSearchParams({ symbol })
  }

  const quotes = useQuery({
    queryKey: QK.indexQuotes,
    queryFn: () => api.indexQuotes(),
    placeholderData: (prev) => prev,
  })

  const daily = useQuery({
    queryKey: QK.indexDaily(selectedSymbol, range.start, range.end),
    queryFn: () => api.indexDaily(selectedSymbol, 180, range),
    enabled: !!selectedSymbol,
    placeholderData: (prev) => prev,
  })

  const minute = useQuery({
    queryKey: QK.indexMinute(selectedSymbol, selectedDate ?? ''),
    queryFn: () => api.indexMinute(selectedSymbol, selectedDate ?? undefined),
    enabled: !!selectedSymbol && !!selectedDate && hasMinuteCap,
  })

  const syncDaily = useMutation({
    mutationFn: () => api.syncIndexDaily(365),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.indexQuotes })
      qc.invalidateQueries({ queryKey: ['index-daily'] })
    },
  })

  const quoteBySymbol = useMemo(() => {
    const m = new Map<string, IndexQuote>()
    for (const q of quotes.data?.rows ?? []) m.set(q.symbol, q)
    return m
  }, [quotes.data?.rows])
  const selectedQuote = selectedSymbol ? quoteBySymbol.get(selectedSymbol) : null
  const selectedQuoteValue = selectedQuote?.last_price ?? selectedQuote?.close
  const selectedQuotePct = selectedQuote?.change_pct

  const chartRows = useMemo(() => toOHLC(daily.data?.rows ?? []), [daily.data?.rows])
  const lastBar = chartRows[chartRows.length - 1]
  const selectedInfo = CORE_INDEXES.find(r => r.symbol === selectedSymbol) || daily.data?.index_info
  const minuteRows: MinuteKlineRow[] = minute.data?.symbol === selectedSymbol
    && minute.data?.date === selectedDate && daily.data?.symbol === selectedSymbol
    ? minute.data.rows : []
  const selectedIdx = selectedDate ? chartRows.findIndex(r => r.date === selectedDate) : -1
  const selectedBar = selectedIdx >= 0 ? chartRows[selectedIdx] : undefined
  // 昨收 = 所选日的前一根收盘; 区间第一根没有前一根, 不要回落到「倒数第二天」
  // (那会让分时图中轴取到未来某天的价格, 整条线偏离数十个百分点)。
  const prevClose = selectedIdx > 0 ? chartRows[selectedIdx - 1].close : undefined

  useEffect(() => {
    setSelectedDate(null)
    setLinkedPrice(null)
    setTab('daily')
  }, [selectedSymbol])

  useEffect(() => {
    if ((!selectedDate || !chartRows.some(r => r.date === selectedDate)) && chartRows.length > 0 && daily.data?.symbol === selectedSymbol) {
      setSelectedDate(chartRows[chartRows.length - 1].date)
    }
  }, [chartRows, daily.data?.symbol, selectedDate, selectedSymbol])

  // 读数优先取实时快照, 但仅当所选日期就是该快照的日期。点历史某根 K 线时必须
  // 显示那一天的 OHLC, 否则「今开/最高/最低」永远是最近一日, 与选中蜡烛对不上。
  const quoteDate = selectedQuote?.date
  const bar = selectedBar ?? lastBar
  const quoteIsCurrent = !!bar && (!quoteDate || quoteDate === bar.date)
  const closeValue = quoteIsCurrent ? (selectedQuoteValue ?? bar?.close) : bar?.close
  const changeAmt = quoteIsCurrent ? selectedQuote?.change_amount : null
  const openValue = quoteIsCurrent ? (selectedQuote?.open ?? bar?.open) : bar?.open
  const highValue = quoteIsCurrent ? (selectedQuote?.high ?? bar?.high) : bar?.high
  const lowValue = quoteIsCurrent ? (selectedQuote?.low ?? bar?.low) : bar?.low
  // 前收与图例昨收同源 (prevClose), 不能再单独取「倒数第二根」—— 那是最近一日的
  // 前收, 点历史 K 线时与上面的今开/最高/最低不一致。
  const prevCloseValue = quoteIsCurrent && selectedQuote?.prev_close != null
    ? selectedQuote.prev_close
    : prevClose
  // 选中历史日时不能用实时快照的涨跌幅 (那是最近一日的); 按该日收盘与前收现算。
  const barPct = bar && prevClose ? (bar.close - prevClose) / prevClose : null
  const displayPct = quoteIsCurrent ? selectedQuotePct : barPct

  return (
    <div className="min-w-0">
      <PageHeader
        seal="指"
        title="指数"
        subtitle="INDICES"
        titleExtra={
          <>
            <span className="tb-chip">{quotes.data?.count ?? CORE_INDEXES.length} 个指数</span>
            <span className="tb-chip">{quoteDate ? `${quoteDate.replaceAll('-', ' · ')} 收盘` : '行情快照未就绪'}</span>
          </>
        }
        right={
          <>
          <button
            type="button"
            onClick={() => { setFilterOpen(open => !open); setFilter('') }}
            aria-pressed={filterOpen}
            className="ctl ctl-md ctl-btn"
          >
            搜索指数
          </button>
          <button
            type="button"
            onClick={() => navigate('/watchlist')}
            className="ctl ctl-md ctl-btn"
          >
            自选
          </button>
          <button
            onClick={() => syncDaily.mutate()}
            disabled={syncDaily.isPending}
            className="ctl ctl-md ctl-btn inline-flex items-center gap-1 disabled:opacity-50"
          >
            {syncDaily.isPending ? <Loader2 className="h-[12px] w-[12px] animate-spin" /> : <RefreshCw className="h-[12px] w-[12px]" />}
            同步指数日K
          </button>
          </>
        }
      />

      <div className="split">
        <div className="side" aria-label="核心指数">
          {filterOpen && (
            <input
              autoFocus
              type="search"
              value={filter}
              onChange={event => setFilter(event.target.value)}
              placeholder="名称或代码"
              aria-label="筛选指数"
              className="ctl ctl-md ctl-input mb-2 w-full px-2.5 focus:border-accent bg-base"
            />
          )}
          {CORE_INDEXES.filter(item => !filter || item.name.includes(filter) || item.symbol.toLowerCase().includes(filter.toLowerCase())).map(item => {
            const q = quoteBySymbol.get(item.symbol)
            const pct = q?.change_pct
            const price = q?.last_price ?? q?.close
            const active = item.symbol === selectedSymbol
            return (
              <button
                key={item.symbol}
                type="button"
                aria-pressed={active}
                onClick={() => selectIndex(item.symbol)}
                className={`q${active ? ' on' : ''}`}
              >
                <b>{item.symbol}</b>
                <em className={tone(pct)}>{fmtPct(pct)}</em>
                <span>{item.name} · {fmtNum(price)}</span>
              </button>
            )
          })}
        </div>

        <div className="main">
          <div className="idx-head">
            <div>
              <h2>{selectedInfo?.name || selectedSymbol || '未选择指数'}</h2>
              <div className="px">
                <b className="num">{fmtNum(closeValue)}</b>
                <i className={tone(displayPct)}>
                  {changeAmt != null && quoteIsCurrent && `${Number(changeAmt) > 0 ? '+' : ''}${fmtNum(changeAmt)}　`}
                  {fmtPct(displayPct)}
                </i>
              </div>
            </div>
            <div className="code">{selectedSymbol}</div>
          </div>

          <div className="kv5">
            <div><span className="kv5-k">今 开</span><span className="kv5-v">{fmtNum(openValue)}</span></div>
            <div><span className="kv5-k">最 高</span><span className="kv5-v">{fmtNum(highValue)}</span></div>
            <div><span className="kv5-k">最 低</span><span className="kv5-v">{fmtNum(lowValue)}</span></div>
            <div>
              <span className="kv5-k">成 交 额</span>
              <span className="kv5-v">
                —
                <span className="kv5-sub">指数源未给</span>
              </span>
            </div>
            <div><span className="kv5-k">前 收</span><span className="kv5-v">{fmtNum(prevCloseValue)}</span></div>
          </div>

          <div className="num-tabs" role="tablist" aria-label="图表类型">
            <button type="button" role="tab" aria-selected={tab === 'daily'} aria-pressed={tab === 'daily'} onClick={() => setTab('daily')}>日线</button>
            <button type="button" role="tab" aria-selected={tab === 'minute'} aria-pressed={tab === 'minute'} onClick={() => setTab('minute')}>分时</button>
          </div>

          {daily.isLoading && <div className="idx-empty">日K加载中…</div>}
          {daily.isError && <div className="idx-empty text-danger">指数日K加载失败</div>}
          {!daily.isLoading && !daily.isError && chartRows.length === 0 && (
            <div className="idx-empty">暂无日K数据。可以先同步指数日K，或选择其他指数。</div>
          )}

          {chartRows.length > 0 && (
            <>
              <div className="plot" role="tabpanel" hidden={tab !== 'daily'}>
                <div className="plot-hd">
                  <b>日 K · 近 180 个交易日</b><span>DAILY</span>
                  <div className="leg"><i className="u">涨</i><i className="d">跌</i><i className="m">MA20</i></div>
                </div>
                <div className="plotbody" data-day-chart>
                  <EChartsCandlestick
                    data={chartRows}
                    height={520}
                    showMA={true}
                    showInfoBar={true}
                    showMarkers={false}
                    symbol={selectedSymbol}
                    linkedPrice={linkedPrice}
                    onDateClick={(date) => {
                      setSelectedDate(date)
                      setLinkedPrice(null)
                    }}
                    visibleBars={48}
                    activeIndicators={['vol', 'macd']}
                  />
                </div>
              </div>

              <div className="plot" role="tabpanel" hidden={tab !== 'minute'}>
                <div className="plot-hd">
                  <b>分时 · {selectedDate ?? '—'}</b><span>INTRADAY</span>
                  <div className="leg"><i className="m">昨收 {fmtNum(prevClose)}</i></div>
                </div>
                <div className="plotbody">
                  {!hasMinuteCap ? (
                    <div className="idx-empty"><Lock className="h-[16px] w-[16px]" />指数分时数据不可用</div>
                  ) : minute.isLoading ? (
                    <div className="idx-empty">分时加载中…</div>
                  ) : minuteRows.length === 0 ? (
                    <div className="idx-empty">暂无分时数据</div>
                  ) : (
                    <EChartsIntraday
                      key={`${selectedSymbol}:${selectedDate}`}
                      data={minuteRows}
                      height={520}
                      prevClose={prevClose}
                      date={selectedDate ?? undefined}
                      showLimitLines={false}
                      showAvgLine={false}
                      onPriceHover={setLinkedPrice}
                    />
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
