/**
 * 开关 — 账簿方块样式 (规范 .sw): 方角细描边, 开时赭石描边 + 小方块右移。
 *
 * 全站统一入口。原先散落 15 处的圆角胶囊开关 (rounded-full + 白圆点) 与
 * 账簿语言冲突, 统一收敛到这里, 换肤时只改 .sw 一处。
 */
import { cn } from '@/lib/cn'

export function Switch({
  checked,
  onChange,
  disabled,
  className,
  label,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  className?: string
  label?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn('sw shrink-0', checked && 'on', disabled && 'cursor-not-allowed opacity-40', className)}
    >
      <i />
    </button>
  )
}
