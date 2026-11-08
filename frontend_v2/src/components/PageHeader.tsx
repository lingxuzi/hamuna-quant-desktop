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
 * 版式走 .inscription: 左题名 + 右用印/元信息 + 页内动作, 下压一道粗发丝线。
 */
export function PageHeader({ title, subtitle, titleExtra, right, className }: Props) {
  return (
    <header
      className={cn(
        'inscription mx-5 mt-5',
        className,
      )}
    >
      <div className="flex min-w-0 flex-col justify-center gap-1">
        <h1 className="ins-title shrink-0 whitespace-nowrap">
          {title}
          {subtitle && <i>{subtitle}</i>}
        </h1>
      </div>
      <div className="ins-right">
        {titleExtra}
        {right}
      </div>
    </header>
  )
}