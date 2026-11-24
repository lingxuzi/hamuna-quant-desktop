import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import {
  Loader2,
  Sparkles,
  LineChart,
  ScanSearch,
  Flame,
  Radar,
  ShieldCheck,
  BellRing,
  TrendingUp,
  FileText,
  Landmark,
  Database,
  Puzzle,
} from 'lucide-react'
import { api } from '@/lib/api'
import { toast } from '@/components/Toast'
import { useCapabilityMatrix } from '@/lib/useSharedQueries'
import { QK } from '@/lib/queryKeys'
import { Inscription, useQuoteDate, useQuoteRunning } from '@/components/Inscription'
import { cn } from '@/lib/cn'

// ===== 引导页:5 步向导 =====
// 0. 使用须知  1. 功能介绍  2. 数据源与 Key  3. 能力路由检测  4. 完成 → 写标记 → 进面板
// 版式取自 design/v2/30-onboarding.html: 卷首 + 步骤条 + 两栏论题/须知 + 页脚动作。

const STEPS = ['使用须知', '功能介绍', '数据源', '能力路由', '完成'] as const

const HIGHLIGHTS = [
  { icon: LineChart,   title: '看板与自选', desc: '市场全景看板、涨跌分布、情绪雷达，自定义自选列表' },
  { icon: ScanSearch,  title: '策略研究',   desc: '自行创建或导入策略，按自定义规则筛选标的' },
  { icon: TrendingUp,  title: '个股分析',   desc: 'AI 四维分析个股，关键价位、技术形态一目了然' },
  { icon: Flame,       title: '连板梯队',   desc: '涨停梯队、封板强度、炸板监控，情绪温度计' },
  { icon: Landmark,    title: '概念行业',   desc: '概念板块、行业维度的资金流向与热度排名' },
  { icon: FileText,    title: '财务概况',   desc: 'AI 解读财报，利润、资负、现金流、核心指标' },
  { icon: ShieldCheck, title: '回测验证',   desc: '策略历史回测、因子分析，用数据验证逻辑' },
  { icon: Radar,       title: '实时监控',   desc: '自定义条件 / 策略监控，盘中触发即推送告警' },
  { icon: BellRing,    title: '本地优先',   desc: '数据本地存储，隐私可控，断网仍可查阅' },
]

export function Onboarding() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const reduceMotion = useReducedMotion()
  const date = useQuoteDate()
  const running = useQuoteRunning()

  const [step, setStep] = useState(0)

  // 完成向导 —— 写后端标记,使守卫放行
  const complete = useMutation({
    mutationFn: api.completeOnboarding,
    onSuccess: (data) => {
      // 用接口返回值同步更新缓存,确保跳转时守卫立即看到 onboarding_completed: true
      // (避免 invalidate 后台重取未返回时, 守卫用旧缓存 false 误重定向回引导页)
      qc.setQueryData(QK.settings, (old: any) =>
        old ? { ...old, onboarding_completed: data.onboarding_completed } : old,
      )
      qc.invalidateQueries({ queryKey: QK.settings })
      navigate('/', { replace: true })
    },
    onError: () => {
      // 不放行: 标记没写进去, 跳过去也会被守卫立刻踢回引导页, 用户看到的是
      // 「每次刷新都走 onboarding」的死循环。停在完成页让用户重试。
      toast('未能保存完成状态, 请重试', 'error')
    },
  })

  const finish = () => complete.mutate()

  return (
    <div className="page scroll-shell h-full overflow-auto scrollbar-gutter-stable">
      <Inscription title="引导" subtitle="初次引导" seal="引" date={date} running={running} />

      <nav className="stepbar" aria-label="初始设置进度">
        {STEPS.map((label, i) => (
          <div
            key={label}
            aria-current={i === step ? 'step' : undefined}
            className={cn('s', i === step && 'on', i < step && 'done')}
          >
            <u>{i + 1}</u>{label}
          </div>
        ))}
      </nav>

      <AnimatePresence mode="wait">
        <motion.div
          key={step}
          initial={reduceMotion ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.16 }}
        >
          {step === 0 && <DisclaimerStep onNext={() => setStep(1)} />}
          {step === 1 && <WelcomeStep onNext={() => setStep(2)} onSkip={finish} />}
          {step === 2 && (
            <DataSourceStep onNext={() => setStep(3)} onBack={() => setStep(1)} />
          )}
          {step === 3 && <ResultStep onNext={() => setStep(4)} onBack={() => setStep(2)} />}
          {step === 4 && <FinishStep onNext={finish} onBack={() => setStep(3)} pending={complete.isPending} />}
        </motion.div>
      </AnimatePresence>
    </div>
  )
}

// ===== Step 0: 使用须知 =====
// 两栏: 左论题(主张 + 三步流程), 右纸面须知(三条) —— design/v2/30 的 .ob 结构。

function DisclaimerStep({ onNext }: { onNext: () => void }) {
  return (
    <div className="ob">
      <section className="thesis">
        <h1>量化<br /><em>工作台引擎</em></h1>
        <p>接入行情与扩展数据，用开源引擎回测自己的策略，盘后复核结果。</p>
        <ol className="flow">
          <li><u>01</u><div><b>接入数据</b><small>连接已获授权的数据源，建立研究基础。</small></div></li>
          <li><u>02</u><div><b>定义规则</b><small>创建或导入策略，将研究想法转为条件。</small></div></li>
          <li><u>03</u><div><b>回测验证</b><small>检验历史表现，理解结果与局限。</small></div></li>
        </ol>
      </section>

      <section className="notice">
        <h2>开始前，请了解</h2>
        <dl>
          <div>
            <dt>不内置策略</dt>
            <dd>初次安装的策略库为空，策略由你自行创建或导入。第三方策略和 AI 生成内容均需自行审查与验证。</dd>
          </div>
          <div>
            <dt>不荐股，不预测，不承诺收益</dt>
            <dd>筛选、评分、监控和 AI 输出仅用于学习与研究，不构成投资建议。回测呈现历史表现，不代表未来收益。</dd>
          </div>

          {/*<div className="pt-5">
            <dt>使用公开数据</dt>
            <dd className="mt-2 space-y-2 leading-6 text-secondary">
              <p>通达信提供了日线，分时数据</p>
              <p>同花顺提供了公司财务数据</p>
            </dd>
          </div>*/}
        </dl>
      </section>

      <div className="foot">
        <div>
          <p>独立判断，审慎使用。</p>
        </div>
        <span className="sp" />
        <button type="button" onClick={onNext} className="ctl-btn ctl-sm pri" style={{ padding: '7px 18px', fontSize: '11.5px' }}>
          我已了解，继续
        </button>
      </div>
    </div>
  )
}

// ===== Step 1: 功能介绍 =====
// 九项能力 —— 1px 发丝线切块的平铺网格, 不做卡片描边。

function WelcomeStep({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  return (
    <div>
      <section className="thesis">
        <h1>九项能力，<em>一处入口</em></h1>
        <p>看板、策略、个股、梯队、行业、财务、回测、监控 —— 全部在本机运行，数据不出机器。</p>
      </section>

      <div className="mt-6 grid grid-cols-1 gap-px border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
        {HIGHLIGHTS.map(h => {
          const Icon = h.icon
          return (
            <div key={h.title} className="bg-surface px-2.5 py-2">
              <Icon aria-hidden="true" className="h-[16px] w-[16px] text-accent" strokeWidth={1.5} />
              <b className="mt-2 block text-[11.5px] font-medium text-foreground">{h.title}</b>
              <small className="mt-1 block text-[10px] leading-[1.65] text-muted">{h.desc}</small>
            </div>
          )
        })}
      </div>

      <div className="foot">
        <button type="button" onClick={onSkip} className="ctl-btn ctl-sm ctl-ghost">稍后再说</button>
        <span className="sp" />
        <button type="button" onClick={onNext} className="ctl-btn ctl-sm pri" style={{ padding: '7px 18px', fontSize: '11.5px' }}>
          开始配置
        </button>
      </div>
    </div>
  )
}

// ===== Step 2: 配置数据源 =====
// 两个条目行 = 各数据源的全部信息聚合 (名称 / 数据集 / 说明 / 操作):
// 内置源 easytdx 装好依赖即可用 (免 Key), 第三方源 fuyao 填好密钥后启用。
// 每项能力最终用哪个源, 由「设置 → 数据源」逐项选择, 本步只负责把源接通。

/** datasets 标签的中文名 (与能力注册表口径一致) */
const DATASET_LABELS: Record<string, string> = {
  daily: '日K',
  adj_factor: '除权因子',
  realtime: '实时行情',
  minute: '分钟K',
  full_minute: '全量分钟',
  depth5: '五档盘口',
  financial: '财务数据',
}

function DataSourceStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const qc = useQueryClient()
  const sources = useQuery({
    queryKey: QK.dataSources,
    queryFn: api.dataSources,
    staleTime: 60_000,
  })

  const plugins = sources.data?.plugins ?? []
  const easyPlugin = plugins.find(p => p.name === 'easytdx')
  const fyPlugin = plugins.find(p => p.name === 'fuyao')

  // 装依赖 (easytdx): 后端按插件声明的运行方式自动安装, 装完刷新插件清单
  const [installMsg, setInstallMsg] = useState<string | null>(null)
  const install = useMutation({
    mutationFn: () => api.installPlugin('easytdx'),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.dataSources })
      qc.invalidateQueries({ queryKey: QK.capabilityMatrix })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      setInstallMsg(data.install_ok
        ? '依赖已装好，行情数据可用'
        : (data.install_message || '安装未完成，请重试'))
    },
    onError: (e: Error) => setInstallMsg(`安装失败：${e.message}`),
  })

  // fuyao Key 配置 (先探后存): 验证通过才落盘
  const [fyKey, setFyKey] = useState('')
  const [fyExpanded, setFyExpanded] = useState(false)
  const [fyError, setFyError] = useState<string | null>(null)
  const [fyMsg, setFyMsg] = useState<string | null>(null)
  const saveKey = useMutation({
    mutationFn: (key: string) => api.savePluginKey('fuyao', key),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: QK.dataSources })
      qc.invalidateQueries({ queryKey: QK.capabilityMatrix })
      qc.invalidateQueries({ queryKey: QK.capabilities })
      if (!data.ok) {
        setFyError(data.error || (data.reason === 'invalid' ? '密钥验证失败，请检查后重试' : '保存失败，请重试'))
        return
      }
      setFyKey('')
      setFyExpanded(false)
      setFyError(null)
      setFyMsg('同花顺接口密钥已保存，财务能力默认使用同花顺接口；其他能力可在数据源页单独选择来源')
    },
    onError: (e: Error) => setFyError(`保存失败：${e.message}`),
  })

  const easyReady = !!easyPlugin?.available
  const fyConfigured = !!fyPlugin?.api_key_masked

  return (
    <div>
      <section className="thesis">
        <h1>数据源与 <em>密钥</em></h1>
        <p>内置源装好依赖即可用，第三方源填好密钥后启用。所有密钥仅存本机，保存前会先验证。</p>
      </section>

      {sources.isLoading ? (
        <p className="mt-5 flex items-center gap-2 text-[11px] text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在加载数据源…
        </p>
      ) : (
        <div className="rowset mt-6">
          {/* 内置源 (内部数据源): 装依赖即用 */}
          <div className="r">
            <div className="lab">
              <b>
                内部数据源
                <span className="ml-1.5 text-[10px] font-normal text-muted/60">内置</span>
              </b>
              <div className="tag-row mt-1.5">
                {(easyPlugin?.datasets ?? []).map(ds => <span key={ds}>{DATASET_LABELS[ds] ?? ds}</span>)}
              </div>
              <small>
                内置直连行情源，提供日K、除权因子、实时行情、分钟K、五档盘口与全量分钟，无需密钥与计费。
              </small>
              {!easyReady && <small>依赖尚未就绪，点「安装依赖」自动补齐（需几分钟）。</small>}
              {installMsg && <p className="mt-1 text-[10.5px] text-bear">{installMsg}</p>}
            </div>
            <div className="ctl">
              <span className={cn('tb-chip', !easyReady && 'warn')}>{easyReady ? '已就绪' : '待安装依赖'}</span>
              {!easyReady && (
                <button type="button" className="ctl-btn ctl-sm pri" disabled={install.isPending} onClick={() => install.mutate()}>
                  {install.isPending ? '安装中…' : '安装依赖'}
                </button>
              )}
            </div>
          </div>

          {/* 第三方源 (同花顺接口): 填密钥启用 */}
          <div className="r">
            <div className="lab">
              <b>
                同花顺接口
                <span className="ml-1.5 text-[10px] font-normal text-muted/60">第三方</span>
              </b>
              <div className="tag-row mt-1.5">
                {(fyPlugin?.datasets ?? []).map(ds => <span key={ds}>{DATASET_LABELS[ds] ?? ds}</span>)}
              </div>
              <small>
                同花顺金融数据 API，补充财务数据。
                {' · '}
                <a href="https://fuyao.aicubes.cn" target="_blank" rel="noreferrer" className="text-accent hover:underline">
                  前往官网申请密钥 ↗
                </a>
              </small>
              {fyError && <p className="mt-1 text-[10.5px] text-danger">{fyError}</p>}
              {fyMsg && <p className="mt-1 text-[10.5px] text-bear">{fyMsg}</p>}
            </div>
            <div className="ctl">
              <span className={cn('tb-chip', !fyConfigured && 'warn')}>{fyConfigured ? '已配置' : '未配置'}</span>
              {fyConfigured && !fyExpanded ? (
                <>
                  <code className="v">{fyPlugin?.api_key_masked}</code>
                  <button type="button" className="ctl-btn ctl-sm" onClick={() => setFyExpanded(true)}>替换密钥</button>
                </>
              ) : (
                <form
                  className="flex items-center gap-2"
                  onSubmit={e => {
                    e.preventDefault()
                    if (fyKey.trim() && !saveKey.isPending) saveKey.mutate(fyKey.trim())
                  }}
                >
                  <input
                    type="password"
                    value={fyKey}
                    onChange={e => { setFyKey(e.target.value); setFyError(null) }}
                    placeholder="粘贴同花顺接口密钥"
                    autoComplete="off"
                    aria-label="同花顺接口密钥"
                    className="ctl ctl-sm ctl-input min-w-0 flex-1"
                  />
                  <button type="submit" className="ctl-btn ctl-sm pri" disabled={!fyKey.trim() || saveKey.isPending}>
                    {saveKey.isPending ? '验证中…' : '验证并保存'}
                  </button>
                  {fyConfigured && (
                    <button type="button" className="ctl-btn ctl-sm ctl-ghost" onClick={() => setFyExpanded(false)}>取消</button>
                  )}
                </form>
              )}
            </div>
          </div>
        </div>
      )}

      <p className="mt-3 flex items-start gap-2 text-[10.5px] leading-[1.75] text-muted">
        <Puzzle aria-hidden="true" className="mt-px h-3.5 w-3.5 shrink-0 text-accent/70" />
        <span>内部数据源覆盖行情，同花顺接口补充财务数据；每个能力可在数据源页单独选择来源。</span>
      </p>

      <div className="foot">
        <button type="button" onClick={onBack} className="ctl-btn ctl-sm">上一步</button>
        <span className="sp" />
        <button type="button" onClick={onNext} className="ctl-btn ctl-sm pri">下一步</button>
      </div>
    </div>
  )
}

// ===== Step 3: 能力路由检测结果 =====
// 以能力路由矩阵呈现: 每个标准化数据集一行, 展示当前生效源与可用性,
// 不再以单一数据源为中心 —— 各能力独立路由, 矩阵即真相。
// 路由由后端按能力给出默认值 (行情类走内置源 easytdx, 财务走 fuyao),
// 本步只做展示: 哪个能力由哪个源提供、当前是否可用。想改来源去「设置 → 数据源」。

function ResultStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const matrix = useQuery({
    queryKey: QK.capabilityMatrix,
    queryFn: api.capabilityMatrix,
    staleTime: 30_000,
  })

  const routes = matrix.data?.capabilities ?? []
  const usableCount = routes.filter(r => r.usable).length

  return (
    <div>
      <section className="thesis">
        <h1>能力路由 <em>检测</em></h1>
        <p>
          下方是每项能力当前由哪个数据源提供、是否可用。默认行情类能力走内置源、财务走同花顺接口；后续可随时在「设置 → 数据源」按能力单独改选。
        </p>
      </section>

      {matrix.isLoading ? (
        <p className="mt-5 flex items-center gap-2 text-[11px] text-muted">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在检测能力路由…
        </p>
      ) : matrix.isError ? (
        <div className="mt-5">
          <p className="text-[11px] text-secondary">能力路由检测失败，请重试</p>
          <button type="button" onClick={() => matrix.refetch()} disabled={matrix.isFetching} className="ctl-btn ctl-sm mt-2">
            {matrix.isFetching ? <Loader2 className="mr-1 inline h-[12px] w-[12px] animate-spin" /> : null}
            重试
          </button>
        </div>
      ) : routes.length === 0 ? (
        <p className="mt-5 text-[11px] text-muted">暂未获取到能力路由矩阵，可稍后在「设置 → 数据源」中重新检测。</p>
      ) : (
        <>
          <div className="mt-5">
            {routes.map(r => (
              <div className="cap" key={r.id}>
                <b>{r.label}</b>
                <div className="cur">
                  <span>{r.effective_display}</span>
                  <i>生效</i>
                </div>
                <div className="cands">
                  {r.candidates.map(c => (
                    <u key={c.name} className={c.available ? 'ok' : ''}>{c.display}</u>
                  ))}
                </div>
                <span className={r.usable ? 'ok' : 'no'}>{r.usable ? '可用' : '不可用'}</span>
              </div>
            ))}
          </div>

          <p className="mt-2 text-[10.5px] text-muted">
            {usableCount}/{routes.length} 项能力可用
            {' · 调整：设置 → 数据源'}
          </p>
        </>
      )}

      <div className="foot">
        <button type="button" onClick={onBack} className="ctl-btn ctl-sm">上一步</button>
        <span className="sp" />
        <button type="button" onClick={onNext} className="ctl-btn ctl-sm pri">下一步</button>
      </div>
    </div>
  )
}

// ===== Step 4: 完成 =====

function FinishStep({ onNext, onBack, pending }: { onNext: () => void; onBack: () => void; pending: boolean }) {
  const matrix = useCapabilityMatrix()
  // 日K 能力可用 = 行情数据源已就绪 (依赖装好 / 源已接通)
  const dailyReady = matrix.data?.capabilities.find(c => c.id === 'daily')?.usable

  // 首要行动:获取数据(不管配没配 Key, 新用户都需要先拉数据)
  // 快速上手入口(精简为核心功能)
  const tips = [
    { icon: TrendingUp, title: '「个股分析」', desc: '输入代码,AI 四维分析 + 关键价位' },
    { icon: ScanSearch, title: '「策略」页', desc: '自行创建或导入策略，一键扫描全市场' },
    { icon: ShieldCheck, title: '「回测」页', desc: '用历史数据验证策略表现，用数据说话' },
  ]

  return (
    <div>
      <section className="thesis">
        <h1>一切<em>就绪</em></h1>
        <p>
          {dailyReady
            ? '数据源已就绪，进入面板后系统会自动引导你获取行情数据，完成后即可使用全部功能。'
            : '进入面板后系统会自动引导你获取历史日K数据；若行情能力显示不可用，请到「设置 → 数据源」补齐数据源。'}
        </p>
      </section>

      <div className="rowset mt-6">
        {/* 首要行动:获取数据 */}
        <div className="r">
          <div className="lab">
            <b>下一步：获取行情数据</b>
            <small>进入面板后，看板会自动引导你拉取近 1 年全 A 股日K(约 5500 只，预计 1-3 分钟)。同步期间可浏览其他页面。</small>
          </div>
          <div className="ctl">
            <Database aria-hidden="true" className="h-[16px] w-[16px] text-accent" />
          </div>
        </div>

        {/* 快速上手入口 */}
        {tips.map(t => {
          const Icon = t.icon
          return (
            <div className="r" key={t.title}>
              <div className="lab">
                <b>{t.title}</b>
                <small>{t.desc}</small>
              </div>
              <div className="ctl">
                <Icon aria-hidden="true" className="h-[16px] w-[16px] text-accent" />
              </div>
            </div>
          )
        })}
      </div>

      <div className="foot">
        <button type="button" onClick={onBack} className="ctl-btn ctl-sm">上一步</button>
        <span className="sp" />
        <button type="button" onClick={onNext} disabled={pending} className="ctl-btn ctl-sm pri">
          {pending ? '正在进入…' : '进入面板'}
          {!pending && <Sparkles aria-hidden="true" className="ml-1 inline h-[12px] w-[12px]" />}
        </button>
      </div>
    </div>
  )
}
