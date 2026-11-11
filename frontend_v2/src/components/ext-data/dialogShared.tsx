import type { ReactNode } from 'react'
import { INPUT_LG, INPUT_MD, SELECT_MD } from '@/components/controls'
import { cn } from '@/lib/cn'

/** 扩展数据弹窗共用的分节标题: mono 序号 + 标题 + 发丝线 + 右侧动作区 */
export function SectionHead({ num, title, children }: {
  num: string
  title: string
  children?: ReactNode
}) {
  return (
    <div className="flex items-center gap-2.5 mb-2.5">
      <span className="font-mono text-[10px] text-amber-500/80 tabular-nums">{num}</span>
      <span className="text-xs font-semibold text-foreground shrink-0">{title}</span>
      <div className="flex-1 h-px bg-border/60" />
      {children}
    </div>
  )
}

export const INPUT_CLS = cn(INPUT_LG, 'w-full bg-base placeholder:text-muted/40 focus:ring-1 focus:ring-accent/30 focus:border-accent/50 transition-shadow')
export const INPUT_SM_CLS = cn(INPUT_MD, 'w-full bg-base placeholder:text-muted/40 focus:ring-1 focus:ring-accent/25 focus:border-accent/50 transition-shadow')
/** md 档字段: 调度表单等密集行的输入/下拉 (bg-base 与弹层纸面对比) */
export const FIELD_MD_CLS = cn(INPUT_MD, 'bg-base focus:ring-1 focus:ring-accent/25')
export const SELECT_MD_CLS = cn(SELECT_MD, 'bg-base focus:ring-1 focus:ring-accent/25')
