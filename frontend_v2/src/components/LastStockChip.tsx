import { Clock } from 'lucide-react'
import type { StockRef } from '@/lib/useLastStock'

/**
 * "上次查看"个股胶囊 —— 显示在 PageHeader 右侧。
 * 上方名称、下方代码,小字体二排;点击恢复该个股的查看。
 */
export function LastStockChip({
  stock,
  onSelect,
}: {
  stock: StockRef | null
  onSelect?: (symbol: string, name: string) => void
}) {
  if (!stock) return null
  return (
    <button
      onClick={() => onSelect?.(stock.symbol, stock.name)}
      title={`继续查看 ${stock.name}`}
      className="ctl ctl-sm ctl-btn group inline-flex items-center gap-1.5 border-border/40 bg-elevated/40 px-1.5 hover:border-border hover:bg-elevated"
    >
      <Clock className="h-[12px] w-[12px] text-muted shrink-0" />
      <span className="flex flex-col items-start leading-tight">
        <span className="text-[11px] font-medium text-secondary group-hover:text-foreground transition-colors max-w-[7em] truncate">
          {stock.name}
        </span>
        <span className="text-[9px] font-mono text-muted">{stock.symbol}</span>
      </span>
    </button>
  )
}
