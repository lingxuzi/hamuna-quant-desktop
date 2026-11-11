import { type LucideIcon } from 'lucide-react'

interface Props {
  /** 段落图示; 与 kicker 二选一 */
  icon?: LucideIcon
  /** 字标 (如「目录」「凭据」): 小号大写赭石, 用于不带图的段落 */
  kicker?: string
  title: React.ReactNode
  /** 题下说明 */
  desc?: React.ReactNode
  /** 右侧操作位 (按钮/开关) */
  right?: React.ReactNode
  /** 追加类 (间距档/下边距随调用处密度) */
  className?: string
}

// 面板段落题头 — 原先「赭石图标 + h3 题头 + 说明」这套写法在设置页各写一份,
// 收拢到这一个件, 字号/图标尺寸/间距不再各写一份。宋体题头是账簿层级的手段
// (与正文无衬线拉开)。尺寸固定在段落级; 单卡片页 (如「统一定义目录」) 的
// 页级题头更大, 不套这个件。
// 不收录的另一族: 次级色图标 + 徽标 + 右侧操作的卡片头 (Monitoring 的 Card /
// Backup 的 CardHeader 已是各自的局部件), 以及带图标底板的对话框头。
export function SectionHead({ icon: Icon, kicker, title, desc, right, className }: Props) {
  return (
    <div className={className}>
      {kicker && <div className="text-[11px] uppercase tracking-[0.2em] text-accent/80">{kicker}</div>}
      <div className={`flex items-start justify-between gap-3 ${kicker ? 'mt-2' : ''}`}>
        <div className="flex min-w-0 items-center gap-2">
          {Icon && <Icon className="h-[16px] w-[16px] shrink-0 text-accent" strokeWidth={1.5} />}
          <h3 className="truncate font-serif text-[15px] font-semibold tracking-tight text-foreground">{title}</h3>
        </div>
        {right}
      </div>
      {desc && <p className="mt-2 max-w-3xl text-xs leading-5 text-secondary">{desc}</p>}
    </div>
  )
}
