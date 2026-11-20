import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, Loader2, Minimize2, Sparkles, X } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { toast } from '@/components/Toast'
import { CHIP } from '@/components/controls'
import { api, type MarketDimension, type MarketFacts } from '@/lib/api'
import { cn } from '@/lib/cn'
import {
  MARKET_AWARE_WINDOW_DAYS,
  closeMarketAware,
  minimizeMarketAware,
  setMarketAwareAssetType,
  startMarketAwareGeneration,
  toggleMarketAwareDimension,
  useMarketAware,
} from '@/lib/marketAwareStore'
import { QK } from '@/lib/queryKeys'

const DIMENSIONS: { id: MarketDimension; label: string; hint: string }[] = [
  { id: 'market', label: '大盘', hint: '市场状态与情绪阶段' },
  { id: 'sector', label: '板块', hint: '强势板块与成分股池' },
  { id: 'money_flow', label: '资金流', hint: '扩展表已配的数值列' },
]

function pct(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

function num(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return String(value)
}

function elapsedText(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000))
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}

/** 市场感知生成: 近 120 天事实 → 挖掘冠军因子池 → AI 决策 → 合并成因子排名策略。 */
export function MarketAwareDialog() {
  const {
    phase, logs, aiText, result, error, assetType, dimensions, startedAt,
  } = useMarketAware()
  const qc = useQueryClient()
  const logRef = useRef<HTMLDivElement>(null)
  const toastedRef = useRef<string | null>(null)
  const [showAiRaw, setShowAiRaw] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  // 事实预览: 维度/标的变化即刷新; 缺数据的维度由后端给指路文案
  const factsQuery = useQuery({
    queryKey: QK.marketFacts(dimensions, assetType),
    queryFn: () => api.marketFacts({
      dimensions,
      asset_type: assetType,
      window_days: MARKET_AWARE_WINDOW_DAYS,
    }),
    enabled: dimensions.length > 0,
    retry: false,
  })
  const facts: MarketFacts | undefined = factsQuery.data?.facts
  const factsError = factsQuery.error
    ? String((factsQuery.error as Error).message || factsQuery.error)
    : null

  // 挖掘可用性预检: 观察窗口 ≠ 挖掘窗口, 挖掘用全部历史
  const availabilityQuery = useQuery({
    queryKey: QK.miningAvailability(assetType, 'exploratory', '', ''),
    queryFn: () => api.miningAvailability({
      assetType,
      budgetProfile: 'exploratory',
    }),
    staleTime: 30_000,
  })
  const availability = availabilityQuery.data
  const canMine = availability?.eligible ?? false

  const running = phase === 'mining' || phase === 'building'

  // 计时: 运行中每秒刷新已用时
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])

  // 日志自动滚到底
  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs.length])

  // 生成成功后刷新策略列表并提示 (同一策略只提示一次)
  useEffect(() => {
    if (!result || toastedRef.current === result.strategy_id) return
    toastedRef.current = result.strategy_id
    void qc.invalidateQueries({ queryKey: ['screener-strategies'] })
    toast(`已生成策略「${result.decisions.name || result.strategy_id}」`, 'success')
  }, [result, qc])

  const stateText = useMemo(() => {
    if (phase === 'mining') return '正在挖掘因子组合'
    if (phase === 'building') return '正在合并市场事实与 AI 决策'
    if (phase === 'done') return '生成完成'
    if (phase === 'error') return '生成失败'
    return ''
  }, [phase])

  const sectorPoolSize = facts?.sector?.pool?.size ?? 0
  const errorText = error

  return (
    <Modal
      onClose={closeMarketAware}
      labelledBy="market-aware-title"
      panelClassName="flex max-h-[86vh] w-[92vw] max-w-2xl flex-col overflow-hidden border border-border bg-surface shadow-xl rounded-card"
    >
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Sparkles className="h-[16px] w-[16px] shrink-0 text-accent" />
            <span id="market-aware-title" className="text-sm font-semibold text-foreground">
              市场感知生成
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">
            按近 {MARKET_AWARE_WINDOW_DAYS} 天的市场走势生成策略：先挖掘出当下最合适的因子组合，
            再让 AI 结合所选维度的市场事实决定入场、离场与风控参数。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={minimizeMarketAware}
            aria-label="缩小到后台"
            title="缩小到后台"
            className="ctl ctl-md ctl-ghost inline-flex w-[28px] items-center justify-center px-0 hover:text-foreground"
          >
            <Minimize2 className="h-[16px] w-[16px]" />
          </button>
          <button
            type="button"
            onClick={closeMarketAware}
            aria-label="关闭"
            className="ctl ctl-md ctl-ghost inline-flex w-[28px] items-center justify-center px-0 hover:text-foreground"
          >
            <X className="h-[16px] w-[16px]" />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="mb-1 text-[10px] text-muted">标的</div>
            <div className="inline-flex h-[30px] rounded-btn border border-border bg-surface/80">
              {(['stock', 'etf'] as const).map(value => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setMarketAwareAssetType(value)}
                  className={cn(
                    'ctl ctl-md ctl-ghost px-2.5',
                    assetType === value
                      ? 'bg-accent text-white'
                      : 'text-secondary hover:text-foreground',
                  )}
                >
                  {value === 'stock' ? '个股' : 'ETF'}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-1 text-[10px] text-muted">观察窗口</div>
            <div className="ctl ctl-md ctl-field flex h-[30px] items-center text-secondary">
              近 {MARKET_AWARE_WINDOW_DAYS} 天
            </div>
          </div>
        </div>

        <div>
          <div className="mb-1 text-[10px] text-muted">参考维度（可多选）</div>
          <div className="flex flex-wrap gap-1.5">
            {DIMENSIONS.map(item => {
              const active = dimensions.includes(item.id)
              return (
                <button
                  key={item.id}
                  type="button"
                  title={item.hint}
                  onClick={() => toggleMarketAwareDimension(item.id)}
                  className={cn(
                    CHIP,
                    active
                      ? 'border-accent bg-accent text-white'
                      : 'border-border bg-base text-muted hover:border-accent/40',
                  )}
                >
                  {item.label}
                </button>
              )
            })}
          </div>
        </div>

        {factsError && (
          <div className="flex items-start gap-2 rounded-btn border border-danger/40 bg-danger/5 px-3 py-2 text-[11px] leading-relaxed text-danger">
            <AlertTriangle className="mt-[1px] h-3.5 w-3.5 shrink-0" />
            <span>{factsError}</span>
          </div>
        )}

        {facts?.market && (
          <section className="rounded-btn border border-border bg-base/60 px-3 py-2">
            <div className="mb-1.5 text-[11px] font-medium text-foreground">大盘</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-secondary sm:grid-cols-3">
              <span>状态分布：{facts.market.states.map(s => `${s.state} ${s.days}天`).join('、') || '—'}</span>
              <span>情绪阶段：{facts.market.phases.map(p => `${p.phase} ${p.days}天`).join('、') || '—'}</span>
              <span>最新阶段：{facts.market.phase_last ?? '—'}</span>
              <span>评分均值：{num(facts.market.score.mean)}</span>
              <span>上涨占比：{pct(facts.market.up_share?.mean)}</span>
              <span>站上均线占比：{pct(facts.market.above_ma20_pct.mean)}</span>
            </div>
          </section>
        )}

        {facts?.sector && (
          <section className="rounded-btn border border-border bg-base/60 px-3 py-2">
            <div className="mb-1.5 text-[11px] font-medium text-foreground">
              板块
              <span className="ml-2 font-normal text-muted">
                强势 {facts.sector.sectors.length} 个 · 成分股池 {sectorPoolSize} 只
              </span>
            </div>
            <div className="space-y-0.5 text-[11px] text-secondary">
              {facts.sector.sectors.slice(0, 5).map(item => (
                <div key={item.member} className="flex justify-between gap-3">
                  <span className="truncate">{item.member}</span>
                  <span className="shrink-0 text-muted">
                    上榜 {item.days} 天 · 强度 {num(item.avg_score)}
                  </span>
                </div>
              ))}
            </div>
            {facts.sector.pool.note && (
              <p className="mt-1.5 text-[10px] text-muted">{facts.sector.pool.note}</p>
            )}
          </section>
        )}

        {facts?.money_flow && (
          <section className="rounded-btn border border-border bg-base/60 px-3 py-2">
            <div className="mb-1.5 text-[11px] font-medium text-foreground">资金流</div>
            <div className="space-y-0.5 text-[11px] text-secondary">
              {facts.money_flow.tables.slice(0, 5).map(item => (
                <div key={`${item.table}-${item.field}`} className="flex justify-between gap-3">
                  <span className="truncate">{item.table}·{item.field}</span>
                  <span className="shrink-0 text-muted">
                    最新 {num(item.last)} · 近 20 期变化 {num(item.recent_vs_earlier)}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {availability && !availability.eligible && (
          <div className="flex items-start gap-2 rounded-btn border border-danger/40 bg-danger/5 px-3 py-2 text-[11px] leading-relaxed text-danger">
            <AlertTriangle className="mt-[1px] h-3.5 w-3.5 shrink-0" />
            <span>
              当前{assetType === 'etf' ? ' ETF' : '个股'}数据只有 {availability.trading_bars} 个交易日，
              不足以支撑挖掘（需 {availability.required_bars} 个）。
              请先到数据页的{assetType === 'etf' ? '「ETF」卡片' : '「日 K」卡片'}里补拉更早的历史行情。
            </span>
          </div>
        )}

        {errorText && (
          <div className="flex items-start gap-2 rounded-btn border border-danger/40 bg-danger/5 px-3 py-2 text-[11px] leading-relaxed text-danger">
            <AlertTriangle className="mt-[1px] h-3.5 w-3.5 shrink-0" />
            <span>{errorText}</span>
          </div>
        )}

        {result && (
          <section className="rounded-btn border border-accent/40 bg-accent/5 px-3 py-2">
            <div className="mb-1.5 text-[11px] font-medium text-foreground">
              {result.decisions.name || '已生成策略'}
            </div>
            <p className="mb-1.5 text-[11px] leading-relaxed text-secondary">
              {result.decisions.description}
            </p>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-secondary sm:grid-cols-3">
              <span>入场分：{result.decisions.params.entry_score}</span>
              <span>离场分：{result.decisions.params.exit_score}</span>
              <span>每日入选：{result.decisions.params.top_rank}</span>
              <span>止损：{pct(result.decisions.risk.stop_loss)}</span>
              <span>最长持有：{result.decisions.risk.max_hold_days} 天</span>
              <span>策略编号：{result.strategy_id}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {Object.entries(result.champion.factors).map(([name, weight]) => (
                <span
                  key={name}
                  className={cn(CHIP, 'border-border bg-base text-secondary')}
                >
                  {name} {weight.toFixed(2)}
                  {result.champion.directions[name] === 'high' ? ' 越高越好' : ' 越低越好'}
                </span>
              ))}
            </div>
          </section>
        )}

        {running && (
          <div className="flex items-center gap-2 rounded-btn border border-border bg-base/60 px-3 py-2 text-[11px] text-secondary">
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
            <span>{stateText}</span>
            {startedAt && (
              <span className="ml-auto shrink-0 text-[10px] text-muted">
                已用时 {elapsedText(now - startedAt)}
              </span>
            )}
          </div>
        )}

        {logs.length > 0 && (
          <section className="rounded-btn border border-border bg-base/60">
            <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
              <span className="text-[10px] text-muted">执行日志</span>
              {aiText && (
                <button
                  type="button"
                  onClick={() => setShowAiRaw(prev => !prev)}
                  className="ctl ctl-sm ctl-ghost inline-flex items-center gap-1 px-1.5 text-[10px] text-muted hover:text-foreground"
                >
                  AI 原始输出
                  <ChevronDown className={cn('h-3 w-3 transition-transform', showAiRaw && 'rotate-180')} />
                </button>
              )}
            </div>
            <div
              ref={logRef}
              className="max-h-[160px] overflow-y-auto px-3 py-2 font-mono text-[11px] leading-relaxed"
            >
              {logs.map((entry, index) => (
                <div
                  key={index}
                  className={cn(
                    'flex gap-2',
                    entry.level === 'error' ? 'text-danger'
                      : entry.level === 'ok' ? 'text-success'
                        : 'text-secondary',
                  )}
                >
                  <span className="shrink-0 text-muted">{entry.at}</span>
                  <span className="min-w-0 break-words">{entry.text}</span>
                </div>
              ))}
              {showAiRaw && aiText && (
                <pre className="mt-2 whitespace-pre-wrap break-words border-t border-border pt-2 text-[10px] text-muted">
                  {aiText}
                </pre>
              )}
            </div>
          </section>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
        <span className="text-[10px] text-muted">
          挖掘在全历史区间上做样本外验证，{MARKET_AWARE_WINDOW_DAYS} 天只用于生成参数
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={closeMarketAware}
            className="ctl ctl-md ctl-ghost px-2.5 hover:text-foreground"
          >
            关闭
          </button>
          <button
            type="button"
            onClick={() => startMarketAwareGeneration(facts?.sector?.pool?.symbols ?? null)}
            disabled={
              running ||
              !canMine ||
              dimensions.length === 0 ||
              !!factsError
            }
            className="ctl ctl-md ctl-btn pri inline-flex items-center gap-1.5 px-2.5 hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" />
            )}
            生成策略
          </button>
        </div>
      </div>
    </Modal>
  )
}
