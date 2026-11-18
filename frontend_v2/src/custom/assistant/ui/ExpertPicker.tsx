/**
 * @ 引用专家的选择面板 —— 双栏: 左分类, 右该分类下的专家。
 *
 * 分类顺序沿用后端给的分类体系 (用户拖拽排序的结果), 没有归属的专家归到
 * 「未分类」放在最后 —— 面板里的顺序与设置页看到的分类页签一致。
 * 搜索时只留有命中的分类; 当前分类被搜空则自动落到第一个有结果的分类。
 */
import { useState } from 'react'
import type { ExpertItem } from '../client'

const UNCATEGORIZED = '未分类'

export function ExpertPicker({
  categories,
  experts,
  query,
  mentions,
  onPick,
}: {
  categories: string[]
  experts: ExpertItem[]
  query: string
  mentions: string[]
  onPick: (name: string) => void
}) {
  const [active, setActive] = useState<string | null>(null)
  const keyword = query.trim().toLowerCase()
  const matched = experts.filter(item => {
    if (!item.enabled) return false
    if (!keyword) return true
    return item.title.toLowerCase().includes(keyword)
      || item.name.toLowerCase().includes(keyword)
      || item.description.toLowerCase().includes(keyword)
  })

  const groups = new Map<string, ExpertItem[]>()
  for (const item of matched) {
    const key = item.category || UNCATEGORIZED
    groups.set(key, [...(groups.get(key) ?? []), item])
  }
  const order = [
    ...categories.filter(name => groups.has(name)),
    ...(groups.has(UNCATEGORIZED) ? [UNCATEGORIZED] : []),
  ]

  if (!order.length) {
    return (
      <div className="drv-pick">
        <div className="px-3 py-3 text-[11px] text-muted">没有可引用的专家</div>
      </div>
    )
  }

  // 当前分类被搜空时落到第一个有结果的分类 —— 右栏不留空白。
  const current = active && order.includes(active) ? active : order[0]

  return (
    <div className="drv-pick drv-pick-2col">
      <div className="drv-pick-cats" aria-label="专家分类">
        {order.map(group => (
          <button
            key={group}
            type="button"
            aria-current={group === current}
            onClick={() => setActive(group)}
            className="drv-pick-cat"
          >
            <span className="min-w-0 truncate">{group}</span>
            <span className="drv-pick-cat-n">{groups.get(group)!.length}</span>
          </button>
        ))}
      </div>
      <div className="drv-pick-list" role="listbox" aria-label="选择要引用的专家">
        {groups.get(current)!.map(item => (
          <button
            key={item.name}
            type="button"
            role="option"
            aria-selected={mentions.includes(item.name)}
            onClick={() => onPick(item.name)}
            className="drv-pick-card"
          >
            <span className="drv-pick-card-t">{item.title}</span>
            <span className="drv-pick-card-d">{item.description}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
