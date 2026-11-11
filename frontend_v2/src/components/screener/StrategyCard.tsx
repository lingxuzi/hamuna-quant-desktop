import { Settings2, RadioTower } from 'lucide-react'
import { storage } from '@/lib/storage'

// ===== 卡片尺寸 =====

export type CardSize = 'mini' | 'normal' | 'large' | 'hidden'

export function loadCardSize(): CardSize {
  const v = storage.screenerCardSize.get('normal')
  if (v === 'mini' || v === 'normal' || v === 'large' || v === 'hidden') return v
  return 'normal'
}

// ===== 来源标签 =====

const SRC_MAP: Record<string, string> = { custom: '自定义', ai: 'AI', composite: '叠加' }

// ===== 策略竖条 =====

interface StrategyCardProps {
  name: string
  description?: string
  source?: string
  active: boolean
  count?: number
  /** 今日已失效数 (曾命中 - 当前命中) */
  expiredCount?: number
  loading: boolean
  cardSize: CardSize
  onRun: () => void
  disabled: boolean
  onSettings: () => void
  /** 是否已加入策略监控 */
  monitored?: boolean
  /** 切换策略监控 (点击 RadioTower 图标) */
  onToggleMonitor?: () => void
  /** 周期徽章 (如 '分钟'); 日线策略不传 */
  timeframeBadge?: string
  /** 后台计算中 (渐进式 run_all): 数字未出时显示脉冲占位 */
  computing?: boolean
  /** 等待运行 (自动计算关闭/失败时的分钟策略): 数字未出时显示「待计算」点击引导 */
  awaitRun?: boolean
}

/** 策略清单的一行 (design/v2/03 的 .st): 左竖条标选中, 第二行是元信息 */
export function StrategyCard({
  name, description, source, active, count, expiredCount,
  loading, cardSize,
  onRun, disabled, onSettings, monitored, onToggleMonitor, timeframeBadge, computing, awaitRun,
}: StrategyCardProps) {
  const srcLabel = SRC_MAP[source ?? ''] ?? '自定义'
  const hasExpired = expiredCount != null && expiredCount > 0
  return (
    <div className={`group relative ${active ? 'border-l-2 border-accent bg-accent/[0.07]' : 'border-l-2 border-transparent'}`}>
      <button
        onClick={onRun}
        disabled={disabled}
        className={`block w-full cursor-pointer py-2 pl-3 pr-11 text-left transition-colors disabled:cursor-wait disabled:opacity-50
          ${active ? 'text-accent' : 'text-secondary hover:bg-elevated/60 hover:text-foreground'}`}
      >
        <span className="block truncate text-xs font-medium">{name}</span>
        <span className={`mt-0.5 flex items-center gap-1.5 font-mono text-[9px] ${active ? 'text-accent/75' : 'text-muted'}`}>
          <span className="truncate">{cardSize === 'mini' ? srcLabel : description || srcLabel}</span>
          {timeframeBadge && <span className="shrink-0">· {timeframeBadge}</span>}
          {count != null && !loading && <span className="shrink-0 tabular-nums">· {count}</span>}
          {count == null && !loading && computing && <span className="shrink-0 animate-pulse">···</span>}
          {count == null && !loading && !computing && awaitRun && <span className="shrink-0">· 待计算</span>}
          {hasExpired && <span className="shrink-0 tabular-nums">· 失效 {expiredCount}</span>}
          {loading && <span className="h-2.5 w-6 shrink-0 animate-pulse rounded-sm bg-elevated" />}
        </span>
      </button>
      <div className="absolute right-1 top-1.5 flex items-center gap-0.5">
        {onToggleMonitor && (
          <button
            onClick={event => { event.stopPropagation(); onToggleMonitor() }}
            className="ctl ctl-sm ctl-ghost relative inline-flex w-[24px] px-0 items-center justify-center hover:bg-elevated"
            title={monitored ? '取消策略监控' : '开启策略监控'}
          >
            <RadioTower className={`h-[12px] w-[12px] ${monitored ? 'text-accent' : 'text-muted hover:text-accent'}`} />
            {monitored && <span className="absolute inset-0 animate-ping rounded-sm bg-accent/20" />}
          </button>
        )}
        <button onClick={event => { event.stopPropagation(); onSettings() }} title="策略设置" className="ctl ctl-sm ctl-ghost inline-flex w-[24px] px-0 items-center justify-center hover:bg-elevated">
          <Settings2 className="h-[12px] w-[12px] text-muted hover:text-accent" />
        </button>
      </div>
    </div>
  )
}
