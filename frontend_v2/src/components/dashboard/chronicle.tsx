/**
 * 看板纪事流 — 设计稿 01-dashboard 的主栏: 左侧日期脊 + 一串按事实分条的条目。
 *
 * 一条纪事只陈述接口给出的一条事实(指数、广度、梯队、板块、监控、趋势), 不拼凑结论;
 * 数据缺失的条目整条不出。条目里的数字可以直接点开个股/板块, 与网格组件共用回调。
 */
import type { ReactNode } from 'react'
import type { OverviewMarket } from '@/lib/api'
import { fmtBigNum } from '@/lib/format'
import { cn } from '@/lib/cn'
import { fmtPrice, fmtStockPct, fmtIndexPct, pctClass } from './shared'

export interface ChronicleCtx {
  data: OverviewMarket
  /** 无涨跌幅方向的说明文字(如自选实时模式) */
  quoteNote?: ReactNode
  openStock: (source: string, symbol: string, name?: string) => void
  /** 打开板块成分股: 取该行业行的 source_field, 无扩展源时不响应 */
  openDimension: (item: { name: string; source_field?: string | null }) => void
  alerts: {
    total: number
    rows: Array<{ symbol: string; name?: string | null; desc: string }>
  }
}

interface Entry {
  key: string
  title: string
  en: string
  tag?: string
  body: ReactNode
  figure?: { head: string; node: ReactNode }
}

const WEEK_CN = ['日', '一', '二', '三', '四', '五', '六']

function stampOf(asOf: string | null) {
  if (!asOf || asOf.length < 10) return { date: '—', sub: '' }
  const [y, m, d] = asOf.split('-')
  const dt = new Date(Number(y), Number(m) - 1, Number(d))
  const jan1 = new Date(Number(y), 0, 1)
  const week = Math.floor((dt.getTime() - jan1.getTime()) / 86_400_000 / 7) + 1
  return { date: `${y}-${m}-${d}`, sub: `周${WEEK_CN[dt.getDay()]} · 第 ${week} 周` }
}

/** 八档横条: 零轴居中, 左绿右红, 与设计稿同构 */
/** 一段名册(多空榜/异动): 行可点则做成按钮, 点开个股预览 */
function Roster({ rows, onPick }: {
  rows: Array<{ symbol: string; name?: string | null; pct?: number | null; desc?: string }>
  onPick?: (r: { symbol: string; name?: string | null }) => void
}) {
  return (
    <div className="roster two">
      {rows.map(r => {
        const inner = (
          <>
            <span className="n">{r.name || r.symbol}</span>
            <span className="code">{r.symbol}</span>
            <span className={cn('p', r.pct != null && pctClass(r.pct))}>{r.desc ?? fmtStockPct(r.pct)}</span>
          </>
        )
        return onPick
          ? <button key={r.symbol} type="button" className="it btn" onClick={() => onPick(r)}>{inner}</button>
          : <div key={r.symbol} className="it">{inner}</div>
      })}
    </div>
  )
}

function DistFig({ rows }: { rows: OverviewMarket['distribution'] }) {
  const max = Math.max(...rows.map(r => r.count), 1)
  return (
    <div>
      <div className="dist">
        {rows.map((r, i) => (
          <div key={r.label} className={cn('bar', i >= 4 && 'u')}>
            <u style={{ height: `${Math.max(2, r.count / max * 100)}%` }} title={`${r.label}: ${r.count} 只`} />
          </div>
        ))}
      </div>
      <div className="lbl">
        {rows.map(r => <span key={r.label}>{r.label}</span>)}
      </div>
    </div>
  )
}

export function Chronicle({ ctx }: { ctx: ChronicleCtx }) {
  const { data } = ctx
  const entries: Entry[] = []

  // 1. 指数
  const ix = data.indices ?? []
  if (ix.length) {
    const worst = ix.reduce((a, b) => ((b.change_pct ?? 0) < (a.change_pct ?? 0) ? b : a))
    const vals = ix.map(i => i.change_pct ?? 0)
    const head = vals.every(v => v < 0) ? '全线收低'
      : vals.every(v => v > 0) ? '全线收高'
      : '涨跌互现'
    entries.push({
      key: 'indices', title: `${head}，${worst.name || worst.symbol} ${fmtIndexPct(worst.change_pct)}`,
      en: 'INDICES', body: '四大核心指数的当日涨跌，点开进入指数页查看分时与历史。',
      figure: {
        head: '指 数 涨 跌',
        node: (
          <div className="strip-4">
            {ix.map(i => (
              <div key={i.symbol} className="c">
                <span className="k">{i.name || i.symbol}</span>
                <div className={cn('v', pctClass(i.change_pct))}>{fmtIndexPct(i.change_pct)}</div>
              </div>
            ))}
          </div>
        ),
      },
    })
  }

  // 2. 广度
  const b = data.breadth
  if (b?.total) {
    const downShare = (b.down / b.total * 100).toFixed(1)
    entries.push({
      key: 'breadth',
      title: `涨 ${b.up} / 跌 ${b.down}，下跌家数占 ${downShare}%`,
      en: 'BREADTH',
      body: (
        <>
          全市场 <b>{b.total}</b> 只，平盘 <b>{b.flat}</b> 只，上涨率 <b>{b.up_pct.toFixed(1)}%</b>；
          平均涨跌 <b className={pctClass(b.avg_pct)}>{fmtStockPct(b.avg_pct)}</b>，
          中位涨跌 <b className={pctClass(b.median_pct)}>{fmtStockPct(b.median_pct)}</b>。
          涨跌 ≥3% 的强势 {b.strong_up ?? 0} 只、弱势 {b.strong_down ?? 0} 只。
        </>
      ),
      figure: { head: '涨 跌 分 布 · 8 档', node: <DistFig rows={data.distribution} /> },
    })
  }

  // 3. 涨停梯队
  const lim = data.limit
  if (lim) {
    const tiers = lim.tiers.filter(t => t.count > 0)
    entries.push({
      key: 'limit',
      title: `涨停 ${lim.limit_up} 家，最高 ${lim.max_boards || 0} 板`,
      en: 'LIMIT LADDER',
      tag: lim.sealed_ready ? undefined : '未定版',
      body: (
        <>
          涨停 <b>{lim.limit_up}</b> 只、跌停 <b>{lim.limit_down}</b> 只，
          炸板 <b>{lim.broken ?? 0}</b> 只，封板率 <b>{(lim.seal_rate ?? 0).toFixed(0)}%</b>。
          {tiers.length > 0 && `梯队 ${tiers.slice().sort((x, y) => y.boards - x.boards).map(t => t.count).join(' / ')}。`}
          {!lim.sealed_ready && ' 当日封单尚未定版，涨跌停按原始口径统计。'}
        </>
      ),
      figure: tiers.length ? {
        head: '连 板 梯 队',
        node: (
          <div className="ladder">
            {tiers.slice().sort((x, y) => y.boards - x.boards).map(t => {
              const top = t.stocks?.[0]
              const row = (
                <>
                  <span className="k">{t.boards}</span>
                  <u style={{ width: `${Math.max(2, t.count / Math.max(...tiers.map(z => z.count)) * 100)}%` }} />
                  <span className="num flex items-baseline gap-2"><span className="m">{top?.name ?? ''}</span><b>{t.count}</b></span>
                </>
              )
              return top ? (
                <button key={t.boards} type="button" className="r btn" onClick={() => ctx.openStock('alert', top.symbol, top.name)}>{row}</button>
              ) : <div key={t.boards} className="r">{row}</div>
            })}
          </div>
        ),
      } : undefined,
    })
  }

  // 4. 板块
  const ind = data.industry_rank
  const con = data.concept_rank
  const lead = ind?.leading?.[0]
  const lag = ind?.lagging?.[0]
  if (lead || lag) {
    entries.push({
      key: 'sectors',
      title: con?.leading?.[0] ? `行业与概念：${lead?.name ?? '—'} 领涨` : '行业榜',
      en: 'SECTORS',
      body: (
        <>
          {lead && <>行业领涨 <button type="button" className="lk" onClick={() => ctx.openDimension(lead)}>{lead.name}</button> <span className={pctClass(lead.avg_pct)}>{fmtStockPct(lead.avg_pct)}</span></>}
          {lag && <>，领跌 <button type="button" className="lk" onClick={() => ctx.openDimension(lag)}>{lag.name}</button> <span className={pctClass(lag.avg_pct)}>{fmtStockPct(lag.avg_pct)}</span></>}
          {con?.leading?.[0] && <>；概念领涨 <b>{con.leading[0].name}</b> <span className={pctClass(con.leading[0].avg_pct)}>{fmtStockPct(con.leading[0].avg_pct)}</span></>}
          。
        </>
      ),
      figure: lead || lag ? {
        head: '行 业 涨 跌 前 列',
        node: (
          <Roster
            rows={[...(ind?.leading ?? []).slice(0, 4), ...(ind?.lagging ?? []).slice(0, 4)]
              .map(x => ({ symbol: x.name, name: x.name, pct: x.avg_pct }))}
            onPick={r => {
              const it = [...(ind?.leading ?? []), ...(ind?.lagging ?? [])].find(x => x.name === r.name)
              if (it) ctx.openDimension(it)
            }}
          />
        ),
      } : undefined,
    })
  }

  // 5. 监控
  if (ctx.alerts.total > 0) {
    entries.push({
      key: 'alerts',
      title: `监控触发 ${ctx.alerts.total} 条异动`,
      en: 'ALERTS',
      tag: '需处理',
      body: <>点开行可查看个股当日分时与五档。</>,
      figure: ctx.alerts.rows.length ? {
        head: '最 近 触 发',
        node: <Roster rows={ctx.alerts.rows} onPick={r => ctx.openStock('alert', r.symbol, r.name ?? undefined)} />,
      } : undefined,
    })
  }

  // 6. 趋势
  const t = data.trend
  if (t) {
    entries.push({
      key: 'trend',
      title: `站上 20 日均线 ${t.above_ma20_pct.toFixed(0)}%，60 日新高 ${t.new_high} / 新低 ${t.new_low}`,
      en: 'TREND',
      body: (
        <>
          站上 MA5 <b>{t.above_ma5_pct.toFixed(0)}%</b>、MA20 <b>{t.above_ma20_pct.toFixed(0)}%</b>、
          MA60 <b>{t.above_ma60_pct.toFixed(0)}%</b>；60 日新高 <b className="text-bull">{t.new_high}</b> 只、
          新低 <b className="text-bear">{t.new_low}</b> 只。成交额 <b>{fmtBigNum(data.amount?.total)}</b>。
        </>
      ),
    })
  }

  if (!entries.length) {
    return <div className="py-12 text-center text-xs text-muted">当日无可用行情数据</div>
  }

  const stamp = stampOf(data.as_of)
  return (
    <div className="chronicle">
      <div className="stamp"><b>{stamp.date}</b><span>{stamp.sub}</span></div>
      {entries.map((e, i) => (
        <article key={e.key} className={cn('entry', i === 0 && 'key')}>
          <h2>
            <span className="e-t">{e.title}</span>
            <span className="e-en">{e.en}</span>
            {e.tag && <span className="e-tag">{e.tag}</span>}
          </h2>
          <p>{e.body}</p>
          {e.figure && (
            <div className="fig">
              <div className="fig-h">{e.figure.head}</div>
              {e.figure.node}
            </div>
          )}
        </article>
      ))}
    </div>
  )
}

/** 右侧指标栏: 指数 / 成交广度 / 分布 / 情绪 / 梯队, 只读, 不参与自定义布局 */
export function MetricRail({ data }: { data: OverviewMarket }) {
  const b = data.breadth
  const lim = data.limit
  const tiers = (lim?.tiers ?? []).filter(t => t.boards >= 2).slice(0, 6)
  const maxTier = Math.max(...tiers.map(t => t.count), 1)
  return (
    <aside className="rail" aria-label="市场指标">
      <div className="rail-b">
        <div className="rail-h"><b>指数</b><span>INDEX</span></div>
        {(data.indices ?? []).map(i => (
          <div key={i.symbol} className="ix">
            <span className="ix-n">{i.name || i.symbol}</span>
            <span className={cn('ix-v', pctClass(i.change_pct))}>{fmtPrice(i.last_price)} · {fmtIndexPct(i.change_pct)}</span>
          </div>
        ))}
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>成交与广度</b><span>BREADTH</span></div>
        <div className="rail-kv">
          <div><span className="k">成交额</span><span className="v">{fmtBigNum(data.amount?.total)}</span></div>
          <div><span className="k">平均成交额</span><span className="v">{fmtBigNum(data.amount?.avg)}</span></div>
          <div><span className="k">上涨 / 下跌</span><span className="v"><span className="text-bull">{b?.up ?? '—'}</span> / <span className="text-bear">{b?.down ?? '—'}</span></span></div>
          <div><span className="k">上涨率</span><span className="v">{b ? `${b.up_pct.toFixed(1)}%` : '—'}</span></div>
        </div>
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>涨跌分布</b><span>DISTRIBUTION</span></div>
        <DistFig rows={data.distribution ?? []} />
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>情绪雷达</b><span>{data.emotion?.label ?? '—'} · {data.emotion?.score ?? '—'}</span></div>
        <div className="radar-leg">
          {(data.radar ?? []).map(r => (
            <span key={r.key}>{r.label}<b>{r.value.toFixed(0)}</b></span>
          ))}
        </div>
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>涨停梯队</b><span>{lim?.limit_up ?? '—'} 家</span></div>
        <div className="ladder">
          {tiers.length === 0 && <div className="py-2 text-[11px] text-muted">暂无 2 板以上</div>}
          {tiers.map(t => (
            <div key={t.boards} className="r">
              <span className="k">{t.boards}</span>
              <u style={{ width: `${Math.max(4, t.count / maxTier * 100)}%` }} />
              <b>{t.count}</b>
            </div>
          ))}
        </div>
      </div>
    </aside>
  )
}
