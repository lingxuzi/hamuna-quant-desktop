import { type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

interface Props {
  /** 自定义图标; 不传则不画标记 */
  icon?: LucideIcon
  /** 宽标记 — 设计稿的 NO STRATEGY / NO RESULT / NO ALERTS 等 */
  eyebrow?: string
  title: string
  hint?: string
  /** 主动作按钮组 (设计稿 .acts) */
  action?: ReactNode
}

// 账簿体例的空态: 宽标记 + 题头 + 归因 + 可执行动作 (设计稿 .bt-empty)。
export function EmptyState({ icon: Icon, eyebrow, title, hint, action }: Props) {
  return (
    <div className="flex h-full items-start px-6">
      <div className="bt-empty">
        {(eyebrow || Icon) && (
          <div className="bt-empty-top">
            {Icon && <Icon className="h-[16px] w-[16px] shrink-0 text-muted" strokeWidth={1.5} />}
            {eyebrow && <span className="em">{eyebrow}</span>}
          </div>
        )}
        <h2>{title}</h2>
        {hint && <p>{hint}</p>}
        {action && <div className="acts">{action}</div>}
      </div>
    </div>
  )
}
