import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ChartLine, FlaskConical, Info, Pickaxe, ShieldCheck } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { FactorDiscovery } from './backtest/FactorDiscovery'
import { FactorBacktest } from './backtest/FactorBacktest'
import { RobustnessValidation } from './backtest/RobustnessValidation'
import { StrategyBacktest } from './backtest/StrategyBacktest'
import { WF_DEFAULT_TRAIN_DAYS, WF_DEFAULT_TEST_DAYS } from './backtest/StrategyWalkForward'
import { api, type ResearchCandidate } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

type Tab = 'strategy' | 'robustness' | 'factor' | 'mining'

const MODES: Record<Tab, { title: string; subtitle: string; icon: typeof FlaskConical }> = {
  strategy: {
    title: '策略',
    subtitle: '现有策略评估与候选沉淀',
    icon: FlaskConical,
  },
  robustness: {
    title: '验证',
    subtitle: '稳健参数筛选与滚动样本外检验',
    icon: ShieldCheck,
  },
  factor: {
    title: '因子',
    subtitle: '单因子分组回测 (IC 与分层净值)',
    icon: ChartLine,
  },
  mining: {
    title: '挖掘与候选',
    subtitle: '因子组合挖掘、样本外复核与候选方案',
    icon: Pickaxe,
  },
}

type RobustnessMode = 'sensitivity' | 'walkforward' | 'guardian'

/** 1:1 对齐 design/v2/11 稳健优化 · 12 滚动样本外 · 13 策略守护 */
const ROBUST_META: Record<RobustnessMode, { title: string; english: string; seal: string }> = {
  sensitivity: { title: '稳健优化', english: 'ROBUST OPTIMIZE', seal: '稳' },
  walkforward: { title: '滚动样本外', english: 'WALK-FORWARD', seal: '滚' },
  guardian: { title: '策略守护', english: 'STRATEGY GUARDIAN', seal: '守' },
}

export function Backtest() {
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const requestedTab = searchParams.get('tab')
  // 候选「载入复测」: 因子页弹窗选定 → 切到策略页 → StrategyBacktest 消费后清空。
  // 回测页自己的「候选方案」按钮已删 (与「挖掘与候选」tab 及因子页入口重复)
  const [pendingLoad, setPendingLoad] = useState<ResearchCandidate | null>(null)

  // 跨页「载入复测」: 因子页候选弹窗经 router state 传入 (location.state), 消费后清除防止刷新重复载入
  const stateCandidate = (location.state as { loadCandidate?: ResearchCandidate } | null)?.loadCandidate ?? null
  useEffect(() => {
    if (!stateCandidate) return
    setPendingLoad(stateCandidate)
    navigate({ pathname: location.pathname, search: location.search }, { replace: true })
  }, [stateCandidate, location.pathname, location.search, navigate])

  // 工具条计数 (design/v2/10 的「策略 0 个」「环境覆盖 243 日」): 均为已有接口, 不新增请求
  const strategies = useQuery({ queryKey: QK.screenerStrategies('stock', 'all'), queryFn: () => api.screenerStrategies('stock', 'all') })
  // 稳健优化三种子模式 (design/v2/11–13): 子页标题/英文小题随模式变化, 旧链接不带参数时默认稳健优化
  const robustnessMode = (searchParams.get('mode') ?? 'sensitivity') as RobustnessMode
  const robustMeta = ROBUST_META[robustnessMode] ?? ROBUST_META.sensitivity
  const regime = useQuery({ queryKey: QK.regimeHistory({ limit: 250 }), queryFn: () => api.regimeHistory(undefined, undefined, 250) })
  const strategyCount = strategies.data?.presets?.length ?? 0
  const regimeDays = regime.data?.rows?.length ?? 0

  // design/v2/14: 因子回测独立成子页; 因子数取自因子目录
  const factorColumns = useQuery({ queryKey: QK.factorColumns, queryFn: api.factorColumns })
  const factorCount = factorColumns.data?.columns.length ?? 0
  const [factorRun, setFactorRun] = useState<{ running: boolean; hasResult: boolean }>({ running: false, hasResult: false })

  // design/v2/15 「候选 0 个」与 design/v2/13 「守护 0 个 / 定时复验 关闭」的状态签数据源
  const candidates = useQuery({
    queryKey: QK.researchCandidates,
    queryFn: api.researchCandidates,
    enabled: requestedTab === 'mining',
  })
  const candidateCount = candidates.data?.items.length ?? 0
  const guardian = useQuery({
    queryKey: QK.guardianOverview,
    queryFn: api.guardianOverview,
    enabled: requestedTab === 'robustness' && searchParams.get('mode') === 'guardian',
  })
  const guardianCount = guardian.data?.items.length ?? 0
  const guardianScheduleOn = guardian.data?.config.guardian_schedule_enabled ?? false

  // 旧链接兼容: 带 focus 的因子筛选链路仍指向因子目录 (在主组件外重定向, 保持 hooks 顺序稳定)
  if (requestedTab === 'factor' && searchParams.get('focus')) {
    return <Navigate to="/factors?tab=discover" replace />
  }
  // 旧链接兼容: 因子已升级为一级路由 /factors, 保留其余参数重定向

  const activeTab: Tab = requestedTab && requestedTab in MODES
    ? requestedTab as Tab
    : 'strategy'

  const changeTab = (tab: Tab) => {
    const next = new URLSearchParams(searchParams)
    next.set('tab', tab)
    setSearchParams(next, { replace: true })
  }

  // 卷首: design/v2/10–15 每页一套题名 / 英文小题 / 印章
  const pageTitle = activeTab === 'robustness'
    ? `回测 · ${robustMeta.title}`
    : `回测 · ${MODES[activeTab].title}`
  const pageEnglish = activeTab === 'robustness'
    ? robustMeta.english
    : activeTab === 'strategy'
      ? 'STRATEGY BACKTEST'
      : activeTab === 'mining' ? 'MINING & CANDIDATES' : 'FACTOR BACKTEST'
  const pageSeal = activeTab === 'robustness'
    ? robustMeta.seal
    : activeTab === 'strategy' ? '策' : activeTab === 'factor' ? '因' : '挖'

  // 状态签: design/v2/10–15 每个子页各一组
  const chips = activeTab === 'factor' ? (
    <>
      <span className="tb-chip">{factorCount} 个因子</span>
      <span className={`tb-chip${factorRun.running || factorRun.hasResult ? '' : ' warn'}`}>
        {factorRun.running ? '运行中' : factorRun.hasResult ? '已出结果' : '本次未运行'}
      </span>
    </>
  ) : activeTab === 'mining' ? (
    <>
      <span className={`tb-chip${candidateCount ? '' : ' warn'}`}>候选 {candidateCount} 个</span>
      <span className="tb-chip">合并自 /factors?tab=mining</span>
    </>
  ) : activeTab === 'robustness' ? (
    robustnessMode === 'guardian' ? (
      <>
        <span className={`tb-chip${guardianCount ? '' : ' warn'}`}>守护 {guardianCount} 个</span>
        <span className={`tb-chip${guardianScheduleOn ? '' : ' warn'}`}>定时复验 {guardianScheduleOn ? '开启' : '关闭'}</span>
      </>
    ) : robustnessMode === 'walkforward' ? (
      <>
        <span className={`tb-chip${strategyCount ? '' : ' warn'}`}>{strategyCount ? '已有策略' : '等待策略'}</span>
        <span className="tb-chip">训练 {WF_DEFAULT_TRAIN_DAYS} / 测试 {WF_DEFAULT_TEST_DAYS}</span>
      </>
    ) : (
      <>
        <span className={`tb-chip${strategyCount ? '' : ' warn'}`}>{strategyCount ? '已有策略' : '等待策略'}</span>
        <span className="tb-chip">扫描上限未定</span>
      </>
    )
  ) : (
    <>
      <span className={`tb-chip${strategyCount ? '' : ' warn'}`}>策略 {strategyCount} 个</span>
      <span className="tb-chip">环境覆盖 {regimeDays} 日</span>
    </>
  )

  return (
    <div className="flex min-h-full flex-col bg-base">
      <PageHeader seal={pageSeal}
        title={pageTitle}
        subtitle={pageEnglish}
        titleExtra={chips}
        className="mb-3"
        right={(
          <>
          <div className="seg" role="tablist" aria-label="回测视图">
            {(Object.keys(MODES) as Tab[]).map(tab => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-pressed={activeTab === tab}
                onClick={() => changeTab(tab)}
              >
                {MODES[tab].title}
              </button>
            ))}
          </div>
          </>
        )}
      />
      <main className="min-h-0 flex-1 pb-3 pt-3">
        <p role="note" aria-label="回测说明" className="mb-3 flex items-start gap-2 rounded-btn border border-border/60 bg-surface px-3 py-2 text-xs leading-5 text-secondary">
          <Info aria-hidden="true" className="mt-0.5 h-[16px] w-[16px] shrink-0 text-muted" />
          <span>
            回测曲线与收益指标基于历史数据和模拟假设，
            <strong className="font-medium text-foreground">不代表未来实际收益</strong>，不构成投资建议。
          </span>
        </p>
        {activeTab === 'strategy' && (
          <StrategyBacktest
            loadCandidate={pendingLoad}
            onLoadConsumed={() => setPendingLoad(null)}
          />
        )}
        {activeTab === 'factor' && (
          <div className="h-[calc(100vh-16rem)] min-h-[32rem]">
            <FactorBacktest onRunStateChange={setFactorRun} />
          </div>
        )}
        {activeTab === 'mining' && (
          <FactorDiscovery initialView="mining" views={['mining']} />
        )}
        {activeTab === 'robustness' && (
          <RobustnessValidation
            mode={robustnessMode}
            onModeChange={mode => {
              const next = new URLSearchParams(searchParams)
              next.set('mode', mode)
              setSearchParams(next, { replace: true })
            }}
          />
        )}
      </main>

    </div>
  )
}
