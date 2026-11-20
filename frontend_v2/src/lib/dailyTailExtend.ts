import type { KlineRow } from './api'
import type { TailExtendQuoteDates } from './minuteTailExtend'

interface DailyLiveRow {
  symbol: string
  asset_type?: string | null
  date?: string | null
  open?: number | null
  high?: number | null
  low?: number | null
  close?: number | null
  /** enriched 当日成交量/额: 必须一并带进补出的蜡烛, 否则 VOL5/量比按 0 摊薄 */
  volume?: number | null
  amount?: number | null
  change_pct?: number | null
}

/** 自选日K尾部修补；只生成展示序列，不修改查询缓存。 */
export function extendDailyTail(
  base: Record<string, KlineRow[]>,
  liveRows: DailyLiveRow[] | undefined,
  quoteDates: TailExtendQuoteDates | null | undefined,
): Record<string, KlineRow[]> {
  if (!liveRows?.length) return base
  const liveBySymbol = new Map(liveRows.map(row => [row.symbol, row]))
  const patched: Record<string, KlineRow[]> = {}
  for (const [symbol, rows] of Object.entries(base)) {
    const live = liveBySymbol.get(symbol)
    // 新后端给出逐行日期；旧后端仅按资产日期回退，不能使用混合列表的全局最旧日。
    const asset = (live?.asset_type ?? 'stock') as keyof TailExtendQuoteDates
    const asOf = live?.date ?? quoteDates?.[asset]
    const { open, high, low, close } = live ?? {}
    if (!rows.length || !asOf || !/^\d{4}-\d{2}-\d{2}$/.test(asOf)
      || [open, high, low, close].some(v => typeof v !== 'number' || !Number.isFinite(v) || v <= 0)) {
      patched[symbol] = rows
      continue
    }
    const candle = {
      date: asOf, open: open!, high: high!, low: low!, close: close!,
      // 量/额/涨跌幅是可选携带: 缺了也别写 null, 让下游拿不到就当缺失
      ...(typeof live?.volume === 'number' && Number.isFinite(live.volume) ? { volume: live.volume } : {}),
      ...(typeof live?.amount === 'number' && Number.isFinite(live.amount) ? { amount: live.amount } : {}),
      ...(typeof live?.change_pct === 'number' && Number.isFinite(live.change_pct) ? { change_pct: live.change_pct } : {}),
    }
    const last = rows[rows.length - 1]
    patched[symbol] = last.date === asOf
      ? [...rows.slice(0, -1), { ...last, ...candle }]
      : last.date < asOf ? [...rows, candle] : rows
  }
  return patched
}
