import { type LucideIcon } from 'lucide-react'

interface Props {
  /** 自定义图标; 不传则用账簿印记「○」 */
  icon?: LucideIcon
  title: string
  hint?: string
}

// 账簿体例的空态: 印记 + 题头 + 归因, 不画大灰图标。
export function EmptyState({ icon: Icon, title, hint }: Props) {
  return (
    <div className="flex h-full items-start px-6">
      <div className="fail">
        <div className="fail-mark flat">{Icon ? <Icon className="h-4 w-4" strokeWidth={1.5} /> : '○'}</div>
        <h2>{title}</h2>
        {hint && <p>{hint}</p>}
      </div>
    </div>
  )
}
