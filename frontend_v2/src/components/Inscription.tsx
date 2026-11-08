/**
 * 账簿卷首 — 题名 / 英文小题 / 日期与行情状态 / 印章 / 页内动作。
 *
 * 全站唯一的题头实现: PageHeader 与首页(自定义工具条)共用,
 * 保证每个页面的卷首结构一致。数据缺失时日期显示「—」, 不伪造。
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'

const WEEK_CN = ['日', '一', '二', '三', '四', '五', '六']

/** 2026-11-06 → 「2026 · 11 · 06　周五」 */
export function dateLabel(d: string | null | undefined): string {
  if (!d || d.length < 10) return '—'
  const [y, m, dd] = d.split('-')
  const wd = WEEK_CN[new Date(Number(y), Number(m) - 1, Number(dd)).getDay()]
  return `${y} · ${m} · ${dd}　周${wd}`
}

/** 实时轮询状态 → 卷首「实时 / 非实时」 */
export function useQuoteRunning(): boolean {
  const { data } = useQuery({ queryKey: QK.quoteStatus, queryFn: api.quoteStatus })
  return !!data?.running
}

/** 行情日期: 与首页同源 (overview/market 的 as_of) */
export function useQuoteDate(): string | null {
  const { data } = useQuery({ queryKey: QK.overviewMarket(undefined), queryFn: () => api.overviewMarket() })
  return data?.as_of ?? null
}

export function InscriptionMeta({ date, running }: { date: string | null; running: boolean }) {
  return (
    <div className="ins-meta">
      <div>{dateLabel(date)}</div>
      <div className={running ? 'live' : 'live stale'}>
        <i />
        <b>{running ? '实时' : '非实时'}</b>
      </div>
    </div>
  )
}

export function Seal({ children }: { children: React.ReactNode }) {
  return <div className="seal">{children}</div>
}

export function Inscription({
  title,
  subtitle,
  seal,
  right,
  date,
  running,
  className,
}: {
  title: string
  subtitle?: React.ReactNode
  /** 印章单字; 不传则不显示印章 */
  seal?: string
  right?: React.ReactNode
  date: string | null
  running: boolean
  className?: string
}) {
  return (
    <header className={cn('inscription', className)}>
      <h1 className="ins-title shrink-0 whitespace-nowrap">
        {title}
        {subtitle && <i>{subtitle}</i>}
      </h1>
      <div className="ins-right">
        {right}
        <InscriptionMeta date={date} running={running} />
        {seal && <Seal>{seal}</Seal>}
      </div>
    </header>
  )
}
