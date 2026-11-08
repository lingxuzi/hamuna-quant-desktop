import { cn } from '@/lib/cn'
import { Inscription, useQuoteDate, useQuoteRunning } from './Inscription'

interface Props {
  title: string
  subtitle?: React.ReactNode
  /** 标题右侧、subtitle 之前的额外节点(如状态徽标) */
  titleExtra?: React.ReactNode
  right?: React.ReactNode
  /** 印章单字 — 设计稿每页右上一枚朱印 (如 盘/策/因) */
  seal?: string
  className?: string
}

/**
 * 卷首: 中文页名用宋体大字号, 英文小题用等宽大写小字。
 * 账簿版式里标题是「一笔题头」, 不做渐变字, 靠字号与留白分层。
 * 版式走 .inscription: 左题名 + 右日期/行情状态/印章 + 页内动作, 下压一道粗发丝线。
 */
export function PageHeader({ title, subtitle, titleExtra, right, seal, className }: Props) {
  const date = useQuoteDate()
  const running = useQuoteRunning()
  return (
    <Inscription
      title={title}
      subtitle={subtitle}
      seal={seal}
      right={titleExtra || right ? <>{titleExtra}{right}</> : undefined}
      date={date}
      running={running}
      className={cn('mx-5 mt-5', className)}
    />
  )
}
