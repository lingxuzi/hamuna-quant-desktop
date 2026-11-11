import { cn } from '@/lib/cn'

export interface DistRow {
  label: string
  count: number
  pct?: number
}

/** 档位标签 → 分界刻度: "<-5%" → "<−5", "-5~-3%" → "−5" (即该档下界), ">5%" 右端不标 */
export function bandToTick(label: string) {
  const lo = label.replace(/%$/, '').split('~')[0].replace(/-/g, '−')
  return lo.startsWith('>') || lo.startsWith('<') ? null : lo
}

/** 涨跌分布 8 档柱图 — 仪表盘纪事与复盘页共用。
 *
 *  颜色不写在组件里: 左四档走 .dist 的主题层 (跌=墨绿), 右四档走 .bar.u (涨=墨红),
 *  页面自己画颜色时两边必然对不上 (复盘页曾把涨画成赭石)。 */
export function DistFig({ rows, labels = 'ticks', counts = false, className }: {
  rows: DistRow[]
  /** ticks = 只标相邻档分界 (仪表盘); bands = 每档写档位名 (复盘页) */
  labels?: 'ticks' | 'bands'
  /** 是否在柱子上方标只数 */
  counts?: boolean
  className?: string
}) {
  if (!rows?.length) return null
  const max = Math.max(1, ...rows.map(r => r.count))
  return (
    <div className={className}>
      <div className="dist" style={counts ? { height: '78px' } : undefined}>
        {rows.map((r, i) => (
          <div key={r.label} className={cn('bar', i >= 4 && 'u')}>
            {counts && <span className="dc">{r.count.toLocaleString()}</span>}
            <u style={{ height: `${Math.max(2, r.count / max * 100)}%` }} title={`${r.label}: ${r.count} 只`} />
          </div>
        ))}
      </div>
      {labels === 'bands' ? (
        <div className="lbl bands">
          {rows.map(r => <span key={r.label}>{r.label}</span>)}
        </div>
      ) : (
        <div className="lbl">
          <span>{bandToTick(rows[0].label)}</span>
          {rows.slice(1, -1).map(r => <span key={r.label}>{bandToTick(r.label)}</span>)}
          <span />
        </div>
      )}
    </div>
  )
}
