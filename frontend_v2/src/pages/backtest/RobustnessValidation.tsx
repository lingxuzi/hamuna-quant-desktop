import { ShieldCheck, SlidersHorizontal, Waypoints } from 'lucide-react'
import { StrategyOptimizer } from './StrategyOptimizer'
import { StrategyWalkForward } from './StrategyWalkForward'
import { StrategyGuardian } from './StrategyGuardian'

export type Mode = 'sensitivity' | 'walkforward' | 'guardian'

/** 子模式由父级以 URL 参数承载 (page 卷首标题需要同步), 本组件只渲染切换与内容。 */
export function RobustnessValidation({ mode, onModeChange }: {
  mode: Mode
  onModeChange: (mode: Mode) => void
}) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex shrink-0 items-center border-b border-border/70 px-1 pb-2">
        <div className="inline-flex rounded-btn border border-border bg-surface/80 p-0.5">
          {([
            ['sensitivity', '稳健优化', SlidersHorizontal],
            ['walkforward', '滚动样本外检验', Waypoints],
            ['guardian', '策略守护', ShieldCheck],
          ] as const).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              onClick={() => onModeChange(value)}
              className={`ctl ctl-md ctl-ghost inline-flex items-center gap-1.5 rounded-[5px] px-2.5 ${mode === value
                ? 'bg-accent text-white shadow-sm'
                : 'text-secondary hover:bg-elevated hover:text-foreground'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {mode === 'sensitivity' && <StrategyOptimizer />}
        {mode === 'walkforward' && <StrategyWalkForward />}
        {mode === 'guardian' && <StrategyGuardian />}
      </div>
    </div>
  )
}
