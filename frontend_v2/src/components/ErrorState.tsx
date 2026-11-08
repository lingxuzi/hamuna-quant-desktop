import { type LucideIcon, RefreshCw } from 'lucide-react'

interface Props {
  /** 自定义图标; 不传则用账簿印记「!」 */
  icon?: LucideIcon
  title?: string
  hint?: string
  retrying?: boolean
  onRetry?: () => void
}

// 错误态与空态同构, 但语义是「加载失败」而非「无数据」, 避免错误被伪装成空态误导排查方向。
export function ErrorState({ icon: Icon, title = '数据加载失败', hint, retrying, onRetry }: Props) {
  return (
    <div className="flex h-full items-start px-6">
      <div className="fail">
        <div className="fail-mark">{Icon ? <Icon className="h-4 w-4" strokeWidth={1.5} /> : '!'}</div>
        <h2>{title}</h2>
        {hint && <p>{hint}</p>}
        {onRetry && (
          <div className="fail-act">
            <button type="button" onClick={onRetry} disabled={retrying} className="tb-btn inline-flex items-center gap-1.5 disabled:opacity-50">
              <RefreshCw className={retrying ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
              重试
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
