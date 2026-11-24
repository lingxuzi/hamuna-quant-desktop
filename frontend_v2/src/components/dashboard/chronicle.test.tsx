// 纪事流「盘前」「要闻」两条目 — 有数据才出, 缺数据整条不出
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Chronicle, type ChronicleCtx } from './chronicle'
import type { NewsItem, OverviewMarket, PremarketSnapshot } from '@/lib/api'

const data = { indices: [], breadth: null, limit: null, as_of: '2026-11-24' } as unknown as OverviewMarket

function ctx(partial: Partial<ChronicleCtx>): ChronicleCtx {
  return {
    data,
    openStock: () => {},
    openDimension: () => {},
    alerts: { total: 0, rows: [] },
    ...partial,
  }
}

const premarket = {
  markets: [{ key: 'usINX', name: '标普 500', pct: 1.23, close: 7000, state: 'fresh' }],
  cross_assets: [{ key: 'hf_CHA50CFD', name: '富时中国 A50 期货', pct: -0.5, close: 12000, role: 'scored' }],
  risk: {
    score: 12, stance: '谨慎偏多', color: '#e05a47', confidence: '高', degraded: false, signals: [],
    summary: '风险偏好略占优，但信号并未形成全面共振。',
    advice: ['保留进攻观察清单，同时以市场宽度和外围指数是否续强作为确认条件。'],
  },
  events: [],
  macro: {}, health: {}, as_of: '2026-11-24 09:00',
} as unknown as PremarketSnapshot

const newsItems: NewsItem[] = [
  { id: 'a', title: '贵州茅台上调出厂价', symbols: ['600519.SH'], symbol_names: { '600519.SH': '贵州茅台' }, source: '东方财富' },
  { id: 'b', title: '美股收盘涨跌不一', symbols: [], source: 'CNBC' },
] as unknown as NewsItem[]

describe('Chronicle 盘前 / 要闻条目', () => {
  it('盘前有数据时输出外盘与风险温度', () => {
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({ premarket })} />)
    expect(html).toContain('隔夜外围')
    expect(html).toContain('标普 500')
    expect(html).toContain('+12')
    expect(html).toContain('谨慎偏多')
  })

  it('盘前数据为空时整条不出', () => {
    const empty = { ...premarket, markets: [], cross_assets: [] } as unknown as PremarketSnapshot
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({ premarket: empty })} />)
    expect(html).not.toContain('隔夜外围')
  })

  it('要闻标出命中自选的条目', () => {
    const html = renderToStaticMarkup(
      <Chronicle ctx={ctx({ news: newsItems, watchlistSymbols: new Set(['600519.SH']) })} />,
    )
    expect(html).toContain('实时要闻')
    expect(html).toContain('1 条命中自选')
    // 命中行显示标的名而不是来源, 并带「自选」标记
    expect(html).toContain('贵州茅台')
    expect(html).toContain('自选')
    // 未命中行显示来源
    expect(html).toContain('CNBC')
  })

  it('无新闻时不出要闻条目', () => {
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({ news: [] })} />)
    expect(html).not.toContain('实时要闻')
  })

  it('全部数据缺失时回落到占位文案', () => {
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({})} />)
    expect(html).toContain('当日无可用行情数据')
  })

  it('盘前条目带上风险温度的一句话结论', () => {
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({ premarket })} />)
    expect(html).toContain('风险偏好略占优')
  })

  it('有美股龙头时优先展示龙头股, 没有才退回指数四格', () => {
    const withLeaders = {
      ...premarket,
      us: {
        indices: [], futures: [], treasuries: [], source: 'easytdx',
        leaders: [
          { code: 'NVDA', name: '英伟达', close: 157, pct: -4.88 },
          { code: 'AAPL', name: '苹果', close: 349.78, pct: 0.2 },
        ],
      },
    } as unknown as PremarketSnapshot
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({ premarket: withLeaders })} />)
    expect(html).toContain('英伟达')
    expect(html).toContain('-4.88%')
    // 有龙头就不再画隔夜外盘四格图
    expect(html).not.toContain('隔 夜 外 盘')
  })

  it('事件链把事实与推断分开写, 不混成一句', () => {
    const withEvents = {
      ...premarket,
      events: [{
        key: 'central_bank', label: '央行与利率', headline_count: 2,
        facts: {
          headlines: [{ title: '美联储宣布降息', source: '华尔街见闻' }],
          watch: [{ code: 'US10Y', value: 4.92, unit: '%', state: 'fresh' }],
        },
        inference: {
          macro_path: '政策利率 → 美债 → 流动性',
          a_share_lens: '高估值成长与券商',
          invalidation: '若美债未同向确认, 标题可能只是噪音。',
        },
      }],
    } as unknown as PremarketSnapshot
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({ premarket: withEvents })} />)
    expect(html).toContain('事件传导')
    expect(html).toContain('美联储宣布降息')
    expect(html).toContain('高估值成长与券商')
    expect(html).toContain('失效')
    // 传导路径每段都要成为独立节点, 折成一句话就退化回段落了
    expect(html).toContain('政策利率')
    expect(html).toContain('美债')
    expect(html).toContain('流动性')
    expect(html).toContain('evfig-pt')
  })

  it('没有命中的事件桶时不出事件链条目', () => {
    const noHits = {
      ...premarket,
      events: [{
        key: 'x', label: 'x', headline_count: 0,
        facts: { headlines: [], watch: [] },
        inference: { macro_path: '', a_share_lens: '', invalidation: '' },
      }],
    } as unknown as PremarketSnapshot
    const html = renderToStaticMarkup(<Chronicle ctx={ctx({ premarket: noHits })} />)
    expect(html).not.toContain('事件传导')
  })
})
