import { Inscription, useQuoteDate, useQuoteRunning } from './Inscription'
import { Toolbar } from './Toolbar'

interface Props {
  title: string
  subtitle?: React.ReactNode
  /** 工具条左侧的状态签 / 读注(如计数、数据未就绪提示) */
  titleExtra?: React.ReactNode
  /** 工具条右侧的页内动作 */
  right?: React.ReactNode
  /** 印章单字 — 设计稿每页右上一枚朱印 (如 盘/策/因) */
  seal?: string
  className?: string
}

/**
 * 卷首 + 工具条。
 *
 * 账簿体例: 上一条是题名/日期/印章, 下一条是状态签与页内动作 (design/v2 的 .toolbar)。
 * 两条都用发丝线分隔, 各页只需给 title / seal / 状态与动作, 不自己排页面顶。
 */
export function PageHeader({ title, subtitle, titleExtra, right, seal, className }: Props) {
  const date = useQuoteDate()
  const running = useQuoteRunning()
  return (
    <div className={className}>
      <Inscription title={title} subtitle={subtitle} seal={seal} date={date} running={running} />
      {(titleExtra || right) && <Toolbar left={titleExtra} right={right} />}
    </div>
  )
}
