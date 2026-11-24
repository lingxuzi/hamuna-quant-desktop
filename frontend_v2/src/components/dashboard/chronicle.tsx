/**
 * 看板纪事流 — 设计稿 01-dashboard 的主栏: 左侧日期脊 + 一串按事实分条的条目。
 *
 * 一条纪事只陈述接口给出的一条事实(指数、广度、梯队、板块、监控、趋势), 不拼凑结论;
 * 数据缺失的条目整条不出。条目里的数字可以直接点开个股/板块, 与网格组件共用回调。
 */
import type { ReactNode } from 'react'
import type { NewsItem, OverviewMarket, PremarketSnapshot } from '@/lib/api'
import { fmtBigNum } from '@/lib/format'
import { cn } from '@/lib/cn'
import { DistFig } from '@/components/charts/DistFig'
import { EventChainFig } from './EventChainFig'
import { fmtStockPct, fmtIndexPct, fmtParts, pctClass } from './shared'

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
  /** 盘前简报 (隔夜外围 + 外部风险温度); 加载中/失败时为 undefined */
  premarket?: PremarketSnapshot
  /** 最近要闻 (多源 RSS 合并去重) */
  news?: NewsItem[]
  /** 自选代码, 用来在要闻里标出命中行 */
  watchlistSymbols?: Set<string>
}

interface Entry {
  key: string
  title: string
  en: string
  tag?: string
  /** 强调条目: 设计稿首条(CLOSING)与 ALERTS 条都带 key 强调 */
  emphasis?: boolean
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

/** 卷首下四格读数 — 与设计稿 design/v2/01 的 .headline.four 同构: 一行大字结论, 下面一行旁注 */
export function Headline({ data }: { data: OverviewMarket }) {
  const b = data.breadth
  const lim = data.limit
  const tiers = (lim?.tiers ?? []).filter(t => t.count > 0).sort((x, y) => y.boards - x.boards)
  const int = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString())
  return (
    <div className="headline four">
      <div className="hl">
        <span className="hl-k">上涨 / 下跌</span>
        <div className="hl-v"><span className='text-bull'>{int(b?.up)}</span><span className="text-muted"> / </span><span className="text-bear">{int(b?.down)}</span></div>
        <div className="hl-n"><span>平盘 <em className="num">{int(b?.flat)}</em></span></div>
      </div>
      <div className="hl">
        <span className="hl-k">两市成交</span>
        <div className="hl-v">{fmtParts(data.amount?.total).v}<small>{fmtParts(data.amount?.total).u}</small></div>
        <div className="hl-n"><span>均额 <em className="num">{fmtBigNum(data.amount?.avg)}</em></span></div>
      </div>
      <div className="hl">
        <span className="hl-k">涨停 / 跌停</span>
        <div className="hl-v"><span className='text-bull'>{int(lim?.limit_up)}</span><span className="text-muted"> / </span><span className="text-bear">{int(lim?.limit_down)}</span></div>
        <div className="hl-n">
          <span>炸板 <em className="num">{int(lim?.broken)}</em></span>
          {lim?.seal_rate != null && <span>封板率 <em className="num">{lim.seal_rate.toFixed(0)}%</em></span>}
        </div>
      </div>
      <div className="hl">
        <span className="hl-k">最高连板</span>
        <div className="hl-v">{lim?.max_boards ?? '—'}<small>板</small></div>
        <div className="hl-n"><span>梯队 <em className="num">{tiers.length ? tiers.map(t => t.count).join(' / ') : '—'}</em></span></div>
      </div>
    </div>
  )
}

/** 涨跌分布 8 档横条; 刻度只标相邻档的分界(7 条), 纪事与右栏共用 */

/** 指数字条: 只有开高低收时, 用「开高收低」的日形折线示意当日走势形状 */
function Spark({ open, high, low, close, up }: { open: number; high?: number | null; low?: number | null; close?: number | null; up: boolean }) {
  const hi = high ?? Math.max(open, close ?? open)
  const lo = low ?? Math.min(open, close ?? open)
  const span = hi - lo || 1
  const y = (v: number | null | undefined) => 15 - ((v ?? open) - lo) / span * 12
  const pts = [[0, y(open)], [33, y(hi)], [66, y(lo)], [100, y(close ?? open)]]
  return (
    <div className="ix-spark">
      <svg viewBox="0 0 100 16" preserveAspectRatio="none">
        <polyline fill="none" strokeWidth={1} points={pts.map(p => p.join(',')).join(' ')}
          className={up ? 'spark-u' : 'spark-d'} style={{ stroke: up ? 'hsl(var(--bull))' : 'hsl(var(--bear))' }} />
      </svg>
    </div>
  )
}

/** 六轴情绪雷达 — 只画正六边形的环与值多边形, 轴标签在右侧图例里 */
function RadarFig({ radar }: { radar: OverviewMarket['radar'] }) {
  if (!radar.length) return null
  const pt = (i: number, r: number) => {
    const a = -Math.PI / 2 + i * 2 * Math.PI / radar.length
    return [50 + r * Math.cos(a), 50 + r * Math.sin(a)]
  }
  const ring = (r: number) => radar.map((_, i) => pt(i, r).map(v => v.toFixed(1)).join(',')).join(' ')
  const val = radar.map((d, i) => pt(i, 6 + Math.max(0, Math.min(100, d.value)) / 100 * 36).map(v => v.toFixed(1)).join(',')).join(' ')
  return (
    <svg viewBox="0 0 100 100" role="img" aria-label="情绪雷达">
      {[36, 24, 12].map(r => <polygon key={r} points={ring(r)} fill="none" stroke="hsl(var(--border))" />)}
      <polygon points={val} fill="hsl(var(--accent))" fillOpacity=".14" stroke="hsl(var(--accent))" strokeWidth="1.2" />
    </svg>
  )
}

export function Chronicle({ ctx }: { ctx: ChronicleCtx }) {
  const { data } = ctx
  const entries: Entry[] = []

  // 0. 盘前 —— 当日第一件事: 隔夜外围怎么走, 决定开盘情绪
  const pm = ctx.premarket
  if (pm?.markets?.length || pm?.cross_assets?.length) {
    const risk = pm.risk
    const mk = pm.markets ?? []
    const cx = pm.cross_assets ?? []
    const us = pm.us
    // 指数按展示顺序排, 期指(last=N225) 与跨资产另起句
    const idx = mk.filter(m => m.kind !== 'future')
    const fut = mk.filter(m => m.kind === 'future')
    // 外盘里最弱的一条用来做标题抓手; 全缺时退回风险温度
    const worst = idx.filter(m => m.pct != null)
      .reduce<typeof idx[number] | null>((a, b) => (!a || (b.pct ?? 0) < (a.pct ?? 0) ? b : a), null)
    entries.push({
      key: 'premarket',
      title: worst
        ? `隔夜外围，${worst.name} ${fmtIndexPct(worst.pct ?? 0)} 最弱`
        : `隔夜外围 ${idx.length} 个市场`,
      en: '隔夜外围',
      tag: risk ? `${risk.stance} · ${risk.confidence}` : undefined,
      body: (
        <>
          {idx.length > 0 && (
            <>
              美股{' '}
              {idx.map((m, n) => (
                <span key={m.key}>
                  {n > 0 && '、'}
                  {m.name} <b className={pctClass(m.pct)}>
                    {m.pct != null ? fmtIndexPct(m.pct) : m.close}
                  </b>
                </span>
              ))}。
            </>
          )}
          {fut.length > 0 && (
            <>
              期指 {fut.map((m, n) => (
                <span key={m.key}>
                  {n > 0 && '、'}
                  {m.name.replace(' 期指', '')} <b className={pctClass(m.pct)}>
                    {m.pct != null ? fmtIndexPct(m.pct) : m.close}
                  </b>
                </span>
              ))}。
            </>
          )}
          {cx.length > 0 && (
            <>
              跨资产 {cx.map((c, n) => (
                <span key={c.key}>
                  {n > 0 && '、'}
                  {c.name} <b className={pctClass(c.pct)}>
                    {c.pct != null ? fmtIndexPct(c.pct) : (c.close ?? '—')}
                  </b>
                </span>
              ))}。
            </>
          )}
          {risk && (
            <>
              外部风险温度 <b style={{ color: risk.color }}>{risk.score > 0 ? `+${risk.score}` : risk.score}</b>
              （{risk.stance}）—— {risk.summary}
              {risk.degraded && <span className="p"> · 部分数据源降级，置信度下调</span>}
            </>
          )}
        </>
      ),
      figure: us?.leaders?.length ? {
        head: '美 股 龙 头',
        node: (
          <div className="roster two">
            {us.leaders.slice(0, 6).map(s => (
              <div key={s.code} className="it">
                <span className="n">{s.name}</span>
                <span className="code">{s.code}</span>
                <span className={cn('p', pctClass(s.pct))}>
                  {s.pct != null ? fmtIndexPct(s.pct) : (s.close ?? '—')}
                </span>
              </div>
            ))}
          </div>
        ),
      } : idx.length ? {
        head: '隔 夜 外 盘',
        node: (
          <div className="strip-4">
            {idx.slice(0, 4).map(m => (
              <div key={m.key} className="c">
                <span className="k">{m.name}</span>
                <div className={cn('v', pctClass(m.pct))}>
                  {m.pct != null ? fmtIndexPct(m.pct) : m.close}
                </div>
              </div>
            ))}
          </div>
        ),
      } : undefined,
    })
  }

  // 0a. 事件传导链 —— 新闻标题按关键词分桶。事实在左, 推断向右流动, 用粒子图而不是段落
  const chains = (pm?.events ?? []).filter(e => e.headline_count > 0)
  if (chains.length) {
    const shown = chains.slice(0, 4)
    entries.push({
      key: 'events',
      title: chains.length > 1
        ? `${chains.length} 条事件线索待验证`
        : `事件线索：${chains[0].label}`,
      en: '事件传导',
      tag: '待验证',
      body: (
        <>
          第一个节点是命中标题数与指标读数，中间是推断的传导路径，末节点是可能落到的 A 股方向。
          线索不参与风险温度计分，失效条件写在每行末尾。
        </>
      ),
      figure: { head: '事 实 → 推 断 → A 股', node: <EventChainFig events={shown} /> },
    })
  }


  // 0b. 要闻 —— 命中自选的行标出来, 端上是自选/持仓命中的系统通知
  const newsRows = (ctx.news ?? []).filter(n => n.title)
  if (newsRows.length) {
    const watch = ctx.watchlistSymbols ?? new Set<string>()
    const hitCount = newsRows.filter(n => (n.symbols ?? []).some(s => watch.has(s))).length
    entries.push({
      key: 'news',
      title: hitCount > 0
        ? `要闻 ${newsRows.length} 条，${hitCount} 条命中自选`
        : `要闻 ${newsRows.length} 条`,
      en: '实时要闻',
      tag: hitCount > 0 ? '自选相关' : undefined,
      body: <>标题与来源来自公开新闻源，按标题近似度去重；命中自选标的的条目会另发系统通知。</>,
      figure: {
        head: '最 近 要 闻',
        node: (
          <div className="roster one">
            {newsRows.slice(0, 6).map(n => {
              const hits = (n.symbols ?? []).filter(s => watch.has(s))
              return (
                <div key={n.id} className="it">
                  <span className="n">{n.title}</span>
                  <span className="code">
                    {hits.length > 0
                      ? hits.map(s => n.symbol_names?.[s] || s).join('、')
                      : (n.source || '')}
                  </span>
                  {hits.length > 0 && <span className="p">自选</span>}
                </div>
              )
            })}
          </div>
        ),
      },
    })
  }

  // 1. 指数
  const ix = data.indices ?? []
  if (ix.length) {
    const worst = ix.reduce((a, b) => ((b.change_pct ?? 0) < (a.change_pct ?? 0) ? b : a))
    const best = ix.reduce((a, b) => ((b.change_pct ?? 0) > (a.change_pct ?? 0) ? b : a))
    const vals = ix.map(i => i.change_pct ?? 0)
    const head = vals.every(v => v < 0) ? '全线收低'
      : vals.every(v => v > 0) ? '全线收高'
      : '涨跌互现'
    const br = data.breadth
    entries.push({
      key: 'indices', title: `${head}，${worst.name || worst.symbol} ${fmtIndexPct(worst.change_pct)}`,
      en: '当日行情', tag: '收盘',
      body: (
        <>
          四大核心指数{head}，{ix.map((i, n) => {
            // 设计稿把当日最强/最弱的两个指数加粗
            const strong = i.symbol === best.symbol || i.symbol === worst.symbol
            const txt = <>{i.name || i.symbol} {fmtIndexPct(i.change_pct)}</>
            return (
              <span key={i.symbol}>
                {n > 0 && '、'}
                {strong ? <b>{txt}</b> : txt}
              </span>
            )
          })}。
          {br?.total ? (
            <>
              全市场 <b>{br.total}</b> 只里 <b>下跌 {br.down}</b> 只，
              下跌家数占比 <b>{(br.down / br.total * 100).toFixed(1)}%</b>，
              仅 <b>{br.up}</b> 只上涨；跌幅 ≥3% 的有 <b>{br.strong_down ?? 0}</b> 只。
            </>
          ) : null}
        </>
      ),
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
      en: '成交广度',
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
      en: '涨停梯队',
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
              // 设计稿每行只有「板数 + 条 + 数量」; 该层首只股票名收进 title/aria-label
              const row = (
                <>
                  <span className="k">{t.boards}</span>
                  <u style={{ width: `${Math.max(2, t.count / Math.max(...tiers.map(z => z.count)) * 100)}%` }} />
                  <b>{t.count}</b>
                </>
              )
              return top ? (
                <button
                  key={t.boards}
                  type="button"
                  className="r btn"
                  title={`${t.boards} 板 · 打开该层首只股票 ${top.name ?? top.symbol}`}
                  aria-label={`${t.boards} 板 ${t.count} 只，打开 ${top.name ?? top.symbol}`}
                  onClick={() => ctx.openStock('alert', top.symbol, top.name)}
                >
                  {row}
                </button>
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
      en: '板块强弱',
      body: (
        <>
          {lead && <>行业领涨 <button type="button" className="lk" onClick={() => ctx.openDimension(lead)}>{lead.name}</button> <span className={pctClass(lead.avg_pct)}>{fmtStockPct(lead.avg_pct)}</span></>}
          {lag && <>，领跌 <button type="button" className="lk" onClick={() => ctx.openDimension(lag)}>{lag.name}</button> <span className={pctClass(lag.avg_pct)}>{fmtStockPct(lag.avg_pct)}</span></>}
          {con?.leading?.[0] && <>；概念领涨 <b>{con.leading[0].name}</b> <span className={pctClass(con.leading[0].avg_pct)}>{fmtStockPct(con.leading[0].avg_pct)}</span></>}
          。
        </>
      ),
    })
  }

  // 5. 监控 —— 有行才出: 只报总数却画不出行, 读者只会以为列表丢了
  if (ctx.alerts.total > 0 && ctx.alerts.rows.length > 0) {
    entries.push({
      key: 'alerts',
      title: `监控触发 ${ctx.alerts.total} 条异动`,
      en: '盯盘提醒',
      tag: '需处理',
      emphasis: true,
      body: <>点开行可查看个股当日分时与五档。</>,
      figure: {
        head: '最 近 触 发',
        node: <Roster rows={ctx.alerts.rows} onPick={r => ctx.openStock('alert', r.symbol, r.name ?? undefined)} />,
      },
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
        <article key={e.key} className={cn('entry', (i === 0 || e.emphasis) && 'key')}>
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
  // 设计稿右栏梯队含首板行 (k=1), 不止 2 板以上
  const tiers = (lim?.tiers ?? []).filter(t => t.count > 0).sort((a, b) => b.boards - a.boards).slice(0, 6)
  const maxTier = Math.max(...tiers.map(t => t.count), 1)
  return (
    <aside className="rail" aria-label="市场指标">
      <div className="rail-b">
        <div className="rail-h"><b>指数</b><span>核心四项</span></div>
        {(data.indices ?? []).map(i => (
          <div key={i.symbol} className="ix">
            <span className="ix-n">{i.name || i.symbol}</span>
            <span className={cn('ix-v', pctClass(i.change_pct))}>{fmtIndexPct(i.change_pct)}</span>
            {i.open != null && <Spark open={i.open} high={i.high} low={i.low} close={i.last_price ?? i.close} up={(i.change_pct ?? 0) >= 0} />}
          </div>
        ))}
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>成交与广度</b><span>两市合计</span></div>
        <div className="rail-kv">
          <div><span className="k">两市成交</span><span className="v">{fmtBigNum(data.amount?.total)}</span></div>
          <div><span className="k">平均涨幅</span><span className={cn('v', pctClass(b?.avg_pct))}>{fmtStockPct(b?.avg_pct)}</span></div>
          <div><span className="k">上涨</span><span className="v text-bull">{b?.up ?? '—'}</span></div>
          <div><span className="k">下跌</span><span className="v text-bear">{b?.down ?? '—'}</span></div>
          <div><span className="k">涨 ≥3%</span><span className="v text-bull">{b?.strong_up ?? '—'}</span></div>
          <div><span className="k">跌 ≥3%</span><span className="v text-bear">{b?.strong_down ?? '—'}</span></div>
        </div>
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>涨跌分布</b><span>八档区间</span></div>
        <DistFig rows={data.distribution ?? []} />
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>情绪雷达</b><span>{data.emotion?.label ?? '—'} · {data.emotion?.score ?? '—'}</span></div>
        <div className="radar-wrap">
          <RadarFig radar={data.radar ?? []} />
          <div className="radar-leg">
            {(data.radar ?? []).map(r => (
              <span key={r.key}>{r.label}<b>{r.value.toFixed(0)}</b></span>
            ))}
          </div>
        </div>
      </div>
      <div className="rail-b">
        <div className="rail-h"><b>涨停梯队</b><span>{lim?.limit_up ?? '—'} 家</span></div>
        <div className="ladder">
          {tiers.length === 0 && <div className="py-2 text-[11px] text-muted">暂无涨停梯队</div>}
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
