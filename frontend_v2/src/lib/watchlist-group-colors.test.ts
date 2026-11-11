// 分组颜色收成 5 色后, 老分组存的旧 id 必须还解析到同一色相 (颜色不变)。
import { expect, it } from 'vitest'
import { WATCHLIST_GROUP_COLORS, resolveWatchlistGroupColor } from './watchlist-group-colors'

const OLD_IDS = [
  'sky', 'blue', 'indigo', 'violet', 'fuchsia', 'rose',
  'orange', 'amber', 'lime', 'emerald', 'teal', 'cyan',
] as const

it('12 个旧 id 全部解析到当前选项里', () => {
  for (const id of OLD_IDS) {
    const option = resolveWatchlistGroupColor(id)
    expect(WATCHLIST_GROUP_COLORS.some(o => o.id === option.id)).toBe(true)
  }
})

it('同色相的旧 id 解析到同一个选项', () => {
  expect(resolveWatchlistGroupColor('blue').id).toBe(resolveWatchlistGroupColor('sky').id)
  expect(resolveWatchlistGroupColor('indigo').id).toBe(resolveWatchlistGroupColor('sky').id)
  expect(resolveWatchlistGroupColor('cyan').id).toBe(resolveWatchlistGroupColor('sky').id)
  expect(resolveWatchlistGroupColor('fuchsia').id).toBe(resolveWatchlistGroupColor('violet').id)
  expect(resolveWatchlistGroupColor('orange').id).toBe(resolveWatchlistGroupColor('amber').id)
  expect(resolveWatchlistGroupColor('lime').id).toBe(resolveWatchlistGroupColor('teal').id)
  expect(resolveWatchlistGroupColor('emerald').id).toBe(resolveWatchlistGroupColor('teal').id)
})

it('未知 / 空的 id 回落到第一个选项', () => {
  expect(resolveWatchlistGroupColor('nope').id).toBe(WATCHLIST_GROUP_COLORS[0].id)
  expect(resolveWatchlistGroupColor(null).id).toBe(WATCHLIST_GROUP_COLORS[0].id)
  expect(resolveWatchlistGroupColor(undefined).id).toBe(WATCHLIST_GROUP_COLORS[0].id)
})
