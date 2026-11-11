import type { WatchlistGroupColor } from '@/lib/api'

export interface WatchlistGroupColorOption {
  id: WatchlistGroupColor
  label: string
  text: string
  border: string
  background: string
  dot: string
  ring: string
}

export const DEFAULT_WATCHLIST_GROUP_COLOR: WatchlistGroupColor = 'sky'

/** 分组颜色 = 账簿色相的五色 (见 tailwind.config.ts LEDGER_HUE)。
 *
 *  原来给了 12 个选项, 但账簿把同色相收成一档后 sky/blue/indigo/cyan 全是「黛」,
 *  violet/fuchsia 是「绛」, lime/emerald/teal 是「墨绿」… 12 个名字只渲染出 5 种颜色,
 *  挑色盘上看着像 12 个色、点下去是同一个 —— 收成 5 个, 名字跟着账簿色相叫。
 *
 *  老分组存的仍是旧 id, 由 LEGACY_ALIAS 解析到同一色相, 颜色不变。 */
export const WATCHLIST_GROUP_COLORS: readonly WatchlistGroupColorOption[] = [
  { id: 'sky', label: '黛蓝', text: 'text-sky-400', border: 'border-sky-400/40', background: 'bg-sky-400/10', dot: 'bg-sky-400', ring: 'ring-sky-400/60' },
  { id: 'violet', label: '绛紫', text: 'text-violet-400', border: 'border-violet-400/40', background: 'bg-violet-400/10', dot: 'bg-violet-400', ring: 'ring-violet-400/60' },
  { id: 'rose', label: '墨红', text: 'text-rose-400', border: 'border-rose-400/40', background: 'bg-rose-400/10', dot: 'bg-rose-400', ring: 'ring-rose-400/60' },
  { id: 'amber', label: '暗金', text: 'text-amber-400', border: 'border-amber-400/40', background: 'bg-amber-400/10', dot: 'bg-amber-400', ring: 'ring-amber-400/60' },
  { id: 'teal', label: '墨绿', text: 'text-teal-400', border: 'border-teal-400/40', background: 'bg-teal-400/10', dot: 'bg-teal-400', ring: 'ring-teal-400/60' },
]

/** 旧 id → 当前色相 (老分组颜色不变) */
const LEGACY_ALIAS: Partial<Record<WatchlistGroupColor, WatchlistGroupColor>> = {
  blue: 'sky', indigo: 'sky', cyan: 'sky',
  fuchsia: 'violet',
  orange: 'amber',
  lime: 'teal', emerald: 'teal',
}

export function resolveWatchlistGroupColor(color?: string | null): WatchlistGroupColorOption {
  const id = (color ?? '') as WatchlistGroupColor
  const canonical = LEGACY_ALIAS[id] ?? id
  return WATCHLIST_GROUP_COLORS.find(option => option.id === canonical)
    ?? WATCHLIST_GROUP_COLORS[0]
}
