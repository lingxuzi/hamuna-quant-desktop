import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Pencil, Search, Trash2 } from 'lucide-react'
import { api, type Lot } from '@/lib/api'
import { fetchDailyBatchChunked } from '@/lib/klineBatchChunked'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { fmtPct, fmtPrice, priceColorClass } from '@/lib/format'
import { PageHeader } from '@/components/PageHeader'
import { Modal } from '@/components/Modal'
import { DatePicker } from '@/components/DatePicker'
import { DateShortcuts } from '@/components/DateShortcuts'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { toNavItems } from '@/lib/listNav'
import { boardTag } from '@/components/stock-table/primitives'

const emptyDraft = (): Lot => ({
  id: '',
  symbol: '',
  qty: 0,
  cost_price: 0,
  buy_date: '',
  target_pct: 0,
  stop_pct: 0,
  remind_date: '',
  lead_days: 1,
})

/** 剩余天数单元格: 到期日 − 今天, 可为负 = 已超期; 无到期日 → — */
function RemainingDays({ remind }: { remind?: string | null }) {
  if (!remind) return <span className="text-muted/60">—</span>
  const remindMs = new Date(`${remind}T00:00:00`).getTime()
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const n = Math.floor((remindMs - today.getTime()) / 86400000)
  if (n < 0) return <span className="font-mono text-warning">已超期{-n}天</span>
  return <span className="font-mono text-secondary">{n}天</span>
}

/** 成本 vs 现价的盈亏% (纯价格比例, 无数量参与) */
function CostPnL({ close, cost }: { close?: number; cost: number }) {
  if (close == null || !(cost > 0)) return <span className="text-muted/60">—</span>
  const pnl = (close - cost) / cost
  return <span className={cn('font-mono', priceColorClass(pnl))}>{fmtPct(pnl)}</span>
}

/** 买入至今的天数 (无买入日期 → null) */
function holdingDays(buyDate?: string | null): number | null {
  if (!buyDate) return null
  const t = new Date(`${buyDate}T00:00:00`).getTime()
  if (Number.isNaN(t)) return null
  return Math.floor((Date.now() - t) / 86400000)
}

export function Lots() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Lot | null>(null) // null=关闭
  const [srch, setSrch] = useState('') // 右栏搜索框
  const [seed, setSeed] = useState('') // 右栏搜索带入弹窗的标的关键词
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const lotsQuery = useQuery({ queryKey: QK.lots, queryFn: api.lotsList })
  const lots = lotsQuery.data?.lots ?? []

  const allSymbols = useMemo(() => Array.from(new Set(lots.map(l => l.symbol))), [lots])
  const namesQuery = useQuery({
    queryKey: QK.instrumentNames(allSymbols),
    queryFn: () => api.instrumentNames(allSymbols),
    enabled: allSymbols.length > 0,
    staleTime: 300000,
  })
  const symbolNames = namesQuery.data?.names ?? {}

  // 9999 哨兵: 未记买入日期的排最后
  const sortedLots = useMemo(() => {
    return [...lots].sort((a, b) => (a.buy_date ?? '9999-12-31').localeCompare(b.buy_date ?? '9999-12-31'))
  }, [lots])
  const lotsNavItems = useMemo(
    () => toNavItems(sortedLots.map(l => ({ symbol: l.symbol, name: symbolNames[l.symbol] }))),
    [sortedLots, symbolNames],
  )

  const dailyQuery = useQuery({
    queryKey: QK.lotsKline(allSymbols.join(',')),
    // 后端单次 symbols 上限 400, 分块拉取合并 (响应形状不变)
    queryFn: () => fetchDailyBatchChunked(allSymbols, 5),
    enabled: allSymbols.length > 0,
    staleTime: 60000,
  })
  const lastPrices = useMemo(() => {
    const m: Record<string, number> = {}
    for (const [sym, rows] of Object.entries(dailyQuery.data?.data ?? {})) {
      const last = rows[rows.length - 1]
      if (last?.close != null) m[sym] = Number(last.close)
    }
    return m
  }, [dailyQuery.data])

  const del = useMutation({
    mutationFn: api.lotDelete,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.lots })
      qc.invalidateQueries({ queryKey: QK.monitorRules })
      setConfirmId(null)
    },
  })

  // 右栏搜索 → 直接进入记一笔, 关键词带进弹窗的标的搜索框 (可继续补全选择)
  const openNew = (seedSymbol = '') => {
    setSeed(seedSymbol)
    setEditing(emptyDraft())
  }
  const openEdit = (lot: Lot) => {
    setSeed('')
    setEditing(lot)
  }

  // 删除: 第一次进确认态, 第二次真删, 3 秒后自动复位 (与监控中心一致)
  const handleClickDelete = (id: string) => {
    if (confirmId === id) {
      if (resetTimer.current) clearTimeout(resetTimer.current)
      setConfirmId(null)
      del.mutate(id)
    } else {
      setConfirmId(id)
      if (resetTimer.current) clearTimeout(resetTimer.current)
      resetTimer.current = setTimeout(() => setConfirmId(null), 3000)
    }
  }

  // 右栏「最新批次」: 最近 5 笔 (买入日新→旧), 无买入日的排末
  const recentLots = useMemo(
    () => [...sortedLots].reverse().slice(0, 5),
    [sortedLots],
  )

  return (
    <div className="flex flex-col h-full">
      <PageHeader seal="批" title="持仓提醒" subtitle="持仓批次"
        titleExtra={(
          <>
            <span className={cn('tb-chip', lots.length === 0 && 'warn')}>批次 {lots.length} 条</span>
            <span className="tb-chip">手工记账</span>
          </>
        )}
        right={(
          <button onClick={() => openNew()} className="ctl ctl-md ctl-btn pri">记一笔</button>
        )}
      />
      <div className="flex-1 min-h-0 overflow-y-auto">
        {/* 左: 批次清单 | 右: 快速记一笔 (design/v2/21-lots.html 的 .lt 双栏) */}
        <div className="grid grid-cols-1 gap-6 border-t border-rule-strong pt-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          <section className="min-w-0">
            {lotsQuery.isLoading ? (
              <div className="border border-dashed border-border px-6 py-12 text-center text-xs text-muted">加载中…</div>
            ) : lotsQuery.isError ? (
              <div className="border border-dashed border-border px-6 py-12 text-center">
                <div className="text-xs text-danger">批次加载失败</div>
                <button
                  onClick={() => lotsQuery.refetch()}
                  className="ctl ctl-sm ctl-btn mt-2 px-1.5 hover:bg-elevated"
                >
                  重试
                </button>
              </div>
            ) : lots.length === 0 ? (
              <div className="flex min-h-[200px] flex-col items-center justify-center border border-border bg-surface px-6 py-10 text-center">
                <span className="font-mono text-[9.5px] tracking-[0.2em] text-accent">暂 无 持 仓</span>
                <h2 className="mt-2 font-serif text-[16px] font-medium text-foreground">还没有记录持仓批次</h2>
                <p className="mt-1.5 max-w-[38ch] text-[11.5px] leading-[1.75] text-secondary">
                  把每一笔买入记成一批：买入价、股数、买入日。之后这里按批显示成本、现价、浮盈和持有天数，卖出时自动按先进先出核销。
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="dt min-w-[920px]">
                  <colgroup>
                    <col />
                    <col className="w-[56px]" />
                    <col className="w-[64px]" />
                    <col className="w-[64px]" />
                    <col className="w-[64px]" />
                    <col className="w-[56px]" />
                    <col className="w-[56px]" />
                    <col className="w-[82px]" />
                    <col className="w-[64px]" />
                    <col className="w-[132px]" />
                    <col className="w-[62px]" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">标的</th>
                      <th scope="col">数量</th>
                      <th scope="col">成本价</th>
                      <th scope="col">现价</th>
                      <th scope="col">盈亏%</th>
                      <th scope="col">止盈%</th>
                      <th scope="col">止损%</th>
                      <th scope="col">买入日期</th>
                      <th scope="col">剩余天数</th>
                      <th scope="col">到期提醒</th>
                      <th scope="col" />
                    </tr>
                  </thead>
                  <tbody>
                    {sortedLots.map(lot => (
                      <tr key={lot.id}>
                        <td>
                          <button
                            onClick={() => setPreviewSymbol(lot.symbol)}
                            title={`查看 ${lot.symbol} 日K`}
                            className="inline-flex min-w-0 items-center gap-1.5 transition-colors cursor-pointer"
                          >
                            <span className="font-mono font-medium text-foreground">{lot.symbol}</span>
                            {(() => { const b = boardTag(lot.symbol); return b && <span className={`inline-flex items-center justify-center rounded px-1 text-[9px] font-bold leading-tight border ${b.color}`}>{b.label}</span> })()}
                            {symbolNames[lot.symbol] && <span className="text-secondary truncate max-w-28">{symbolNames[lot.symbol]}</span>}
                          </button>
                        </td>
                        <td className="text-secondary">{lot.qty}</td>
                        <td className="text-foreground">{lot.cost_price}</td>
                        <td className="text-secondary">{lastPrices[lot.symbol] != null ? fmtPrice(lastPrices[lot.symbol]) : '—'}</td>
                        <td><CostPnL close={lastPrices[lot.symbol]} cost={lot.cost_price} /></td>
                        <td className="text-bull">{lot.target_pct > 0 ? `${lot.target_pct}%` : '—'}</td>
                        <td className="text-bear">{lot.stop_pct > 0 ? `${lot.stop_pct}%` : '—'}</td>
                        <td className="text-muted">{lot.buy_date || '—'}</td>
                        <td><RemainingDays remind={lot.remind_date} /></td>
                        <td className="text-left">
                          {lot.remind_date ? (
                            <span className="inline-flex items-center gap-1 text-rose-400">
                              <CalendarClock className="h-[12px] w-[12px]" />
                              {lot.remind_date}
                              {lot.lead_days > 0 && <span className="text-muted">· 提前{lot.lead_days}天</span>}
                            </span>
                          ) : <span className="text-muted/60">—</span>}
                        </td>
                        <td>
                          <div className="flex items-center justify-end gap-0.5">
                            <button
                              onClick={() => openEdit(lot)}
                              title="编辑"
                              className="ctl ctl-sm ctl-ghost ctl-icon text-secondary hover:bg-accent/10 hover:text-accent"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            {confirmId === lot.id ? (
                              <button
                                onClick={() => handleClickDelete(lot.id)}
                                title="再次点击确认删除"
                                className="ctl ctl-sm ctl-btn soft inline-flex items-center gap-1 px-1.5 border-danger/30 animate-pulse"
                              >
                                <Trash2 className="h-2.5 w-2.5" />确认
                              </button>
                            ) : (
                              <button
                                onClick={() => handleClickDelete(lot.id)}
                                title="删除 (同步删除生成的监控规则)"
                                className="ctl ctl-sm ctl-ghost ctl-icon text-secondary hover:bg-danger/10 hover:text-danger"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 右栏: 快速记一笔 + 最新批次 */}
          <aside className="min-w-0">
            <div className="figcap">快 速 记 一 笔</div>
            <div className="flex gap-1.5">
              <input
                value={srch}
                onChange={e => setSrch(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') openNew(srch.trim()) }}
                placeholder="代码或名称"
                aria-label="搜索标的以快速记一笔"
                className="ctl ctl-md ctl-input min-w-0 flex-1 px-2.5 font-mono focus:border-accent/50"
              />
              <button onClick={() => openNew(srch.trim())} className="ctl ctl-md ctl-btn">查</button>
            </div>

            <table className="dt mt-3">
              <colgroup>
                <col />
                <col className="w-[62px]" />
                <col className="w-[52px]" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">批次</th>
                  <th scope="col">浮盈</th>
                  <th scope="col">持有</th>
                </tr>
              </thead>
              <tbody>
                {recentLots.length === 0 ? (
                  <tr><td className="z">尚无批次</td><td /><td /></tr>
                ) : recentLots.map(lot => {
                  const days = holdingDays(lot.buy_date)
                  return (
                    <tr key={lot.id}>
                      <td>
                        <button
                          onClick={() => setPreviewSymbol(lot.symbol)}
                          className="inline-flex min-w-0 items-center gap-1.5 transition-colors cursor-pointer"
                          title={`查看 ${lot.symbol} 日K`}
                        >
                          <span className="font-mono text-foreground">{lot.symbol}</span>
                          {symbolNames[lot.symbol] && <span className="truncate text-secondary">{symbolNames[lot.symbol]}</span>}
                        </button>
                      </td>
                      <td><CostPnL close={lastPrices[lot.symbol]} cost={lot.cost_price} /></td>
                      <td className="text-secondary">{days == null ? '—' : `${days}天`}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            <div className="figcap mt-6">为 什 么 记 批 次</div>
            <p className="mt-2 text-[11px] leading-[1.8] text-secondary">
              券商只告诉你「持有 500 股、成本 12.30」。同一个标的你可能在 10.8 买过一次、13.2 补过一次 —— 两个批次的止损位不该一样。按批记账，才谈得上分批止盈。
            </p>
          </aside>
        </div>
      </div>

      {editing && <LotDialog lot={editing} seed={seed} onClose={() => { setEditing(null); setSeed('') }} />}

      <StockPreviewDialog
        symbol={previewSymbol}
        name={previewSymbol ? symbolNames[previewSymbol] : undefined}
        navList={lotsNavItems}
        onNavigate={(sym) => setPreviewSymbol(sym)}
        onClose={() => setPreviewSymbol(null)}
      />
    </div>
  )
}

function LotDialog({ lot, seed = '', onClose }: { lot: Lot; seed?: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Lot>(() => ({ ...lot }))
  const [symbolQuery, setSymbolQuery] = useState(seed)
  const [error, setError] = useState('')

  // 数字字段用本地字符串承载 (可先清空再输入), 提交时才解析成数值;
  // 否则受控 number + parseFloat 会在清空瞬间把值塞回 0, 导致「0 去不掉」。
  const [nums, setNums] = useState<Record<string, string>>(() => {
    const f = (v: number | undefined | null) => (v == null || v === 0 ? '' : String(v))
    return {
      qty: f(lot.qty), cost_price: f(lot.cost_price),
      target_pct: f(lot.target_pct), stop_pct: f(lot.stop_pct),
      lead_days: f(lot.lead_days),
    }
  })
  const numField = (key: string) => ({
    value: nums[key] ?? '',
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setNums(s => ({ ...s, [key]: e.target.value })),
  })

  const symbolSearch = useQuery({
    queryKey: QK.instrumentSearch(symbolQuery, 'stock,etf'),
    queryFn: () => api.instrumentSearch(symbolQuery, 20, 'stock,etf'),
    enabled: symbolQuery.trim().length > 0,
  })

  const save = useMutation({
    mutationFn: (vals: Partial<Lot>) => api.lotSave({ ...draft, ...vals, symbol: draft.symbol.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.lots })
      qc.invalidateQueries({ queryKey: QK.monitorRules })
      onClose()
    },
    onError: err => setError(String((err as any)?.message ?? err)),
  })

  const parseNum = (s: string | undefined) => {
    const n = parseFloat(s ?? '')
    return Number.isFinite(n) ? n : 0
  }

  const submit = () => {
    setError('')
    if (!draft.symbol.trim()) return setError('请选择标的')
    const vals: Partial<Lot> = {
      qty: parseNum(nums.qty),
      cost_price: parseNum(nums.cost_price),
      target_pct: parseNum(nums.target_pct),
      stop_pct: parseNum(nums.stop_pct),
      lead_days: Math.floor(parseNum(nums.lead_days)),
    }
    if (!((vals.cost_price ?? 0) > 0)) return setError('成本价必须为正数')
    if ((vals.qty ?? 0) < 0 || (vals.target_pct ?? 0) < 0 || (vals.stop_pct ?? 0) < 0 || (vals.lead_days ?? 0) < 0) {
      return setError('数量 / 百分比 / 提前天数不能为负数')
    }
    if (!((vals.target_pct ?? 0) > 0 || (vals.stop_pct ?? 0) > 0 || draft.remind_date)) {
      return setError('止盈% / 止损% / 到期日 至少设置一项')
    }
    save.mutate(vals)
  }

  return (
    <Modal onClose={onClose} ariaLabel={lot.id ? '编辑批次' : '新增批次'} panelClassName="w-[92vw] max-w-md bg-surface border border-border shadow-xl">
      <div className="flex items-center justify-between border-b border-rule-strong px-4 py-3">
        <span className="font-serif text-[15px] font-medium text-foreground">{lot.id ? '编辑批次' : '新增批次'}</span>
        <span className="font-mono text-[9.5px] tracking-[0.12em] text-muted">保存后自动同步监控规则</span>
      </div>
      <div className="space-y-3 px-4 py-4">
        {/* 标的 */}
        <div className="space-y-1.5">
          <span className="text-[11px] text-secondary">标的</span>
          {draft.symbol ? (
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded bg-elevated px-2 py-1 font-mono text-[11px] text-secondary">
                {draft.symbol}
                <button onClick={() => setDraft(d => ({ ...d, symbol: '' }))} className="text-muted hover:text-danger cursor-pointer"><span className="text-[10px]">✕</span></button>
              </span>
              <span className="text-[10px] text-muted">点 ✕ 可重选</span>
            </div>
          ) : (
            <div className="relative">
              <input
                value={symbolQuery}
                onChange={e => setSymbolQuery(e.target.value)}
                placeholder="搜索代码或名称..."
                autoFocus
                className="ctl ctl-md ctl-input w-full pl-8 pr-3 font-mono focus:border-accent/50 bg-base"
              />
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted" />
              {symbolSearch.data && symbolSearch.data.results.length > 0 && (
                <div className="absolute z-10 mt-1 max-h-48 w-full overflow-auto rounded border border-border bg-surface shadow-lg">
                  {symbolSearch.data.results.map(r => (
                    <button
                      key={r.symbol}
                      onClick={() => { setDraft(d => ({ ...d, symbol: r.symbol })); setSymbolQuery('') }}
                      className="block w-full px-2.5 py-1.5 text-left text-[11px] hover:bg-elevated cursor-pointer"
                    >
                      <span className="font-mono text-foreground/80">{r.symbol}</span>
                      <span className="ml-1.5 text-muted">{r.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1.5">
            <span className="text-[11px] text-secondary">数量 (参考)</span>
            <input type="number" min={0} placeholder="0" {...numField('qty')} className="ctl ctl-md ctl-input w-full bg-base px-2.5 font-mono" />
          </label>
          <label className="space-y-1.5">
            <span className="text-[11px] text-secondary">成本价</span>
            <input type="number" min={0} step="any" placeholder="0" {...numField('cost_price')} className="ctl ctl-md ctl-input w-full bg-base px-2.5 font-mono" />
          </label>
          <label className="space-y-1.5">
            <span className="text-[11px] text-secondary">止盈%</span>
            <input type="number" min={0} step="any" placeholder="0" {...numField('target_pct')} className="ctl ctl-md ctl-input w-full bg-base px-2.5 font-mono" />
          </label>
          <label className="space-y-1.5">
            <span className="text-[11px] text-secondary">止损%</span>
            <input type="number" min={0} step="any" placeholder="0" {...numField('stop_pct')} className="ctl ctl-md ctl-input w-full bg-base px-2.5 font-mono" />
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <span className="text-[11px] text-secondary">买入日期 (可选)</span>
            <DateShortcuts value={draft.buy_date ?? ''} onChange={v => setDraft(d => ({ ...d, buy_date: v || null }))} options={[{ label: '今天', days: 0 }]} />
            <DatePicker value={draft.buy_date ?? ''} onChange={v => setDraft(d => ({ ...d, buy_date: v || null }))} placeholder="不记录" />
          </div>
          <div className="space-y-1.5">
            <span className="text-[11px] text-secondary">到期日 (可选)</span>
            <DateShortcuts value={draft.remind_date ?? ''} onChange={v => setDraft(d => ({ ...d, remind_date: v || null }))} options={[{ label: '5天', days: 5 }, { label: '10天', days: 10 }, { label: '15天', days: 15 }]} base={draft.buy_date || undefined} />
            <DatePicker value={draft.remind_date ?? ''} onChange={v => setDraft(d => ({ ...d, remind_date: v || null }))} placeholder="不提醒" />
          </div>
        </div>

        {draft.remind_date && (
          <label className="space-y-1.5">
            <span className="text-[11px] text-secondary">提前提醒天数</span>
            <input type="number" min={0} placeholder="1" {...numField('lead_days')} className="ctl ctl-md ctl-input w-full bg-base px-2.5 font-mono" />
            <span className="block text-[10px] text-muted">提醒仅在交易时段评估; 到期日若逢周末或长假, 请把提前天数调大些 (建议 ≥ 2, 长假更大)</span>
          </label>
        )}

        {error && <div className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">{error}</div>}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-rule-strong px-4 py-3">
        <button onClick={onClose} className="ctl-btn ctl-sm">取消</button>
        <button
          onClick={submit}
          disabled={save.isPending}
          className={cn('mini pri disabled:opacity-50')}
        >
          {save.isPending ? '保存中...' : '保存'}
        </button>
      </div>
    </Modal>
  )
}
