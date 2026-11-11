import { cn } from '@/lib/cn'

/**
 * 卷首下的工具条 — 左: 状态签与读注, 右: 页内动作。
 *
 * 设计稿每页都有这一行 (design/v2/*.html 的 .toolbar), 用发丝线与正文分隔。
 * 只做布局, 不含数据; 状态签不传就不占位。
 */
export function Toolbar({ left, right, className }: {
  left?: React.ReactNode
  right?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('toolbar', className)}>
      <div className="tb-left">{left}</div>
      {right && <div className="tb-right">{right}</div>}
    </div>
  )
}

/** 状态签: tone=warn 用于「未就绪 / 需注意」 */
export function Chip({ children, tone }: { children: React.ReactNode; tone?: 'warn' }) {
  return <span className={cn('tb-chip', tone === 'warn' && 'warn')}>{children}</span>
}

/** 读注: 一行等宽小字, 总结范围与更新时间 */
export function Note({ children }: { children: React.ReactNode }) {
  return <span className="tb-note">{children}</span>
}
