import { cn } from '@/lib/cn'

interface Props {
  title: string
  subtitle?: React.ReactNode
  /** 标题右侧、subtitle 之前的额外节点(如状态徽标) */
  titleExtra?: React.ReactNode
  right?: React.ReactNode
  className?: string
}

/**
 * 卷首: 中文页名用宋体大字号, 英文小题用等宽大写小字。
 * 账簿版式里标题是「一笔题头」, 不做渐变字, 靠字号与留白分层。
 */
export function PageHeader({ title, subtitle, titleExtra, right, className }: Props) {
  return (
    <header
      className={cn(
        'px-5 pt-5 pb-3 border-b border-foreground/80 flex items-end justify-between gap-4',
        className,
      )}
    >
      <div className="flex min-w-0 flex-col justify-center gap-1">
        <div className="flex items-baseline gap-2.5">
          <h1 className="shrink-0 whitespace-nowrap font-serif text-[26px] font-semibold leading-none tracking-[0.01em] text-foreground">
            {title}
          </h1>
          {titleExtra}
        </div>
        {subtitle && (
          <span className="truncate whitespace-nowrap font-mono text-[9.5px] uppercase leading-tight tracking-[0.16em] text-muted">
            {subtitle}
          </span>
        )}
      </div>
      {right}
    </header>
  )
}