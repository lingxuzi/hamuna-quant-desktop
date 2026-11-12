/**
 * 因子页 — 按任务组织的三视图: 发现 (检验+挖掘) / 创建 (公式+组合) / 因子库 (管理)。
 *
 * 历史 5 平级 tab (检验/因子库/编辑器/组合/挖掘) 是三种任务被摊开: 评估类散在
 * 两处、创建类散在两处。收敛后子视图各自有分段切换, 旧 tab 参数经 LEGACY_TABS
 * 映射继续工作 (router 的 /mining 与回测页重定向不受影响)。
 */
import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { BookmarkCheck, FlaskConical, LibrarySquare, Search } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/PageHeader'
import { Chip, Toolbar } from '@/components/Toolbar'
import { ResourceTransferButton } from '@/components/ResourceTransferDialog'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { FactorDiscovery } from './backtest/FactorDiscovery'
import { FactorComposite } from './factors/FactorComposite'
import { FactorEditor } from './factors/FactorEditor'
import { FactorLibrary } from './factors/FactorLibrary'
import { ResearchCandidatesDialog } from './backtest/ResearchCandidatesDialog'
import { cnToday } from '@/lib/cnDate'

type Tab = 'discover' | 'create' | 'library'
type CreateMode = 'editor' | 'composite'

const TABS: Record<Tab, { title: string; icon: typeof Search; subtitle: string }> = {
  discover: { title: '发现', icon: Search, subtitle: '批量检验与挖掘: 哪些特征历史上与涨跌相关' },
  create: { title: '创建', icon: FlaskConical, subtitle: '写公式定义因子, 或勾选现有因子组合' },
  library: { title: '因子库', icon: LibrarySquare, subtitle: '因子注册表目录与生命周期管理' },
}

// 名录表的可选列 (设计稿 .fac 的列; 「因子」列恒显)
const FACTOR_COLS = [
  { key: 'group', label: '维度' },
  { key: 'kind', label: '类型' },
  { key: 'formula', label: '公式 / 定义' },
  { key: 'status', label: '状态' },
] as const

// 旧五 tab → 新三 tab; view/mode 让旧深链 (如 tab=mining&run=xx) 落到正确子视图
const LEGACY_TABS: Record<string, { tab: Tab; view?: string; mode?: CreateMode }> = {
  inspect: { tab: 'discover' },
  mining: { tab: 'discover', view: 'mining' },
  editor: { tab: 'create', mode: 'editor' },
  composite: { tab: 'create', mode: 'composite' },
  library: { tab: 'library' },
}

const CREATE_MODES: { id: CreateMode; label: string }[] = [
  { id: 'editor', label: '公式编辑器' },
  { id: 'composite', label: '因子组合' },
]

export function Factors() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [candidatesOpen, setCandidatesOpen] = useState(false)
  const [colsOpen, setColsOpen] = useState(false)
  const [hiddenCols, setHiddenCols] = useState<string[]>([])
  const navigate = useNavigate()

  const requestedTab = searchParams.get('tab')
  const legacy = requestedTab ? LEGACY_TABS[requestedTab] : undefined
  // 设计稿首屏 = 名录表 + 右侧检验栏 (因子库视图), 故默认落在因子库
  const activeTab: Tab = requestedTab && requestedTab in TABS
    ? requestedTab as Tab
    : legacy?.tab ?? 'library'
  const requestedView = searchParams.get('view') ?? legacy?.view
  const discoverView = requestedView === 'single' || requestedView === 'mining' ? requestedView : 'batch'
  const requestedMode = (searchParams.get('mode') ?? legacy?.mode) as CreateMode | null
  const createMode: CreateMode = requestedMode === 'composite' ? 'composite' : 'editor'
  const focusFactor = activeTab === 'discover' ? searchParams.get('focus') ?? '' : ''
  const editId = searchParams.get('edit') ?? ''

  const patchParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams)
    for (const [key, value] of Object.entries(patch)) {
      if (value == null) next.delete(key)
      else next.set(key, value)
    }
    setSearchParams(next, { replace: true })
  }

  const changeTab = (tab: Tab) => {
    patchParams({ tab, focus: null, edit: null, view: null, mode: null })
  }

  // 页头状态签的数据源: 与因子库表格共用同一条查询 (react-query 同 key 只打一次请求)
  const lib = useQuery({
    queryKey: QK.factorLibrary('all'),
    queryFn: () => api.factorLibrary(),
    staleTime: 60_000,
  })
  const factors = lib.data?.factors ?? []
  const factorCount = factors.length
  const dimensionCount = useMemo(() => new Set(factors.map(f => f.group)).size, [factors])
  // 本次会话是否已检验过 (与「发现」页的会话记忆同一份 sessionStorage)
  const inspected = useMemo(() => {
    try {
      return !!sessionStorage.getItem('factor-batch-results.v1')
    } catch {
      return false
    }
  }, [])

  // 导出名录 (CSV): 设计稿工具条右侧的「导出」
  const handleExport = () => {
    const rows = [
      ['因子', '维度', '类型', '公式 / 定义'],
      ...factors.map(f => [f.label, f.group, f.kind, f.formula ?? '']),
    ]
    const csv = rows
      .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))
      .join('\r\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `因子名录_${cnToday()}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  const toggleCol = (key: string) => {
    setHiddenCols(prev => (prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]))
  }

  return (
    <div className="flex min-h-full flex-col bg-base xl:h-full">
      <PageHeader seal="因"
        title="因子"
        subtitle="FACTORS"
        titleExtra={(
          <>
            <Chip>{factorCount} 个因子</Chip>
            <Chip>{dimensionCount} 个维度</Chip>
            {inspected ? <Chip>本次已检验</Chip> : <Chip tone="warn">本次未检验</Chip>}
          </>
        )}
        className="mb-0"
        right={(
          <div className="flex w-full min-w-0 flex-wrap items-center gap-1.5 sm:gap-2 lg:w-auto lg:flex-nowrap">
            {/* 列设置: 名录表的可选列 */}
            <div className="relative shrink-0">
              <button
                type="button"
                className="ctl ctl-md ctl-btn"
                aria-expanded={colsOpen}
                onClick={() => setColsOpen(v => !v)}
              >
                列设置
              </button>
              {colsOpen && (
                <>
                  <div className="fixed inset-0 z-30" onClick={() => setColsOpen(false)} aria-hidden="true" />
                  <div className="absolute right-0 top-[calc(100%+4px)] z-40 w-40 border border-border bg-surface py-1 shadow-lg">
                    {FACTOR_COLS.map(col => (
                      <label
                        key={col.key}
                        className="flex cursor-pointer items-center gap-2 px-2.5 py-1 text-[11px] text-secondary hover:bg-elevated hover:text-foreground"
                      >
                        <input
                          type="checkbox"
                          checked={!hiddenCols.includes(col.key)}
                          onChange={() => toggleCol(col.key)}
                          className="accent-accent"
                        />
                        {col.label}
                      </label>
                    ))}
                  </div>
                </>
              )}
            </div>
            <button type="button" className="ctl ctl-md ctl-btn" onClick={handleExport} disabled={!factors.length}>
              导出
            </button>
            <span className="hidden h-5 w-px shrink-0 bg-border sm:block" aria-hidden="true" />
            <button
              type="button"
              onClick={() => setCandidatesOpen(true)}
              aria-label="打开候选方案"
              title="候选方案"
              className="ctl ctl-md ctl-btn filled inline-flex shrink-0 items-center gap-1.5 px-2.5 hover:border-accent/40 hover:text-accent sm:px-2.5 sm:"
            >
              <BookmarkCheck className="h-3.5 w-3.5" />
              <span>候选方案</span>
            </button>
            <ResourceTransferButton kind="factor" className="h-[32px] w-[32px]" />
          </div>
        )}
      />

      {/* 第二行工具条: 子视图切换 + 口径读注 (设计稿 05 的第二行 .toolbar) */}
      <Toolbar
        left={(
          <>
            <div className="seg" role="tablist" aria-label="因子视图">
              {(Object.keys(TABS) as Tab[]).map(tab => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  title={TABS[tab].subtitle}
                  aria-pressed={activeTab === tab}
                  onClick={() => changeTab(tab)}
                >
                  {TABS[tab].title}
                </button>
              ))}
            </div>
            <span className="tb-note">批量检验 {factorCount} 个因子 · 基准池 自选</span>
          </>
        )}
      />

      <div className="min-h-0 flex-1 pt-2">
        {activeTab === 'discover' && (
          <FactorDiscovery focusFactor={focusFactor} initialView={discoverView} />
        )}
        {activeTab === 'create' && (
          <div className="flex h-full min-h-0 flex-col gap-3">
            <div className="flex shrink-0 items-center px-1">
              <div className="inline-flex rounded-btn border border-border bg-surface/80 p-0.5">
                {CREATE_MODES.map(({ id, label }) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => patchParams({ mode: id, edit: null })}
                    aria-current={createMode === id ? 'true' : undefined}
                    className={`ctl ctl-md ctl-ghost inline-flex items-center gap-1.5 rounded-[5px] px-2.5 ${createMode === id
                      ? 'bg-accent text-white shadow-sm'
                      : 'text-secondary hover:bg-elevated hover:text-foreground'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {createMode === 'editor' && editId && (
                <button
                  type="button"
                  onClick={() => patchParams({ edit: null })}
                  className="ctl ctl-sm ctl-ghost ml-2 inline-flex items-center gap-1 px-1.5 hover:bg-elevated hover:text-foreground"
                  title="退出编辑, 回到新建"
                >
                  ← 新建因子
                </button>
              )}
            </div>
            <div className="min-h-0 flex-1">
              {createMode === 'editor'
                ? <FactorEditor key={editId} editId={editId} />
                : <FactorComposite />}
            </div>
          </div>
        )}
        {activeTab === 'library' && (
          /* 设计稿 05 的两栏骨架: 左名录表 + 右 IC 检验区; 窄屏折成单列 */
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_268px]">
            <div className="min-w-0">
              <FactorLibrary
                hiddenCols={hiddenCols}
                onInspect={factorId => patchParams({ tab: 'discover', view: 'batch', focus: factorId, edit: null, mode: null })}
                onEdit={factorId => patchParams({ tab: 'create', mode: 'editor', edit: factorId, focus: null, view: null })}
              />
            </div>
            <aside className="pt-1" aria-label="最近一次检验">
              <div className="text-[9.5px] tracking-[.1em] text-muted">最 近 一 次 检 验</div>
              <div className="mt-2.5 border border-border bg-surface">
                <div className="flex items-baseline gap-2.5 border-b border-border px-2.5 py-[7px]">
                  <b className="text-[11.5px] font-medium text-foreground">IC 衰减</b>
                  <span className="font-mono text-[9px] tracking-[.14em] text-muted">IC DECAY</span>
                </div>
                <div className="flex h-[150px] items-center justify-center px-3 text-center text-[11px] leading-[1.8] text-muted">
                  还没有检验记录<br />选中左侧任一因子后点「检验全部」
                </div>
              </div>
              <div className="mt-px grid grid-cols-3 gap-px border border-border bg-border">
                {[['IC 均值', '横截面相关'], ['IR', 'IC / 标准差'], ['多空收益', '五分组首尾']].map(([k, s]) => (
                  <div key={k} className="bg-surface px-2.5 py-2">
                    <span className="block text-[9px] tracking-[.1em] text-muted">{k}</span>
                    <span className="mt-[3px] block font-mono text-[16px] tabular-nums text-foreground">—</span>
                    <span className="mt-0.5 block text-[9.5px] text-muted">{s}</span>
                  </div>
                ))}
              </div>
              <div className="mt-2.5 border border-border bg-surface">
                <div className="flex items-baseline gap-2.5 border-b border-border px-2.5 py-[7px]">
                  <b className="text-[11.5px] font-medium text-foreground">分组累计收益</b>
                  <span className="font-mono text-[9px] tracking-[.14em] text-muted">Q1 – Q5</span>
                </div>
                <div className="flex h-[130px] items-center justify-center px-3 text-center text-[11px] leading-[1.8] text-muted">
                  检验完成后在此按五分组画累计净值
                </div>
              </div>
              <div className="mt-3.5 text-[10.5px] leading-[1.8] text-muted">
                创建区：公式编辑器 + 因子组合。写一条公式点「校验」，通过后进因子库并可直接在检验区选中。
              </div>
            </aside>
          </div>
        )}
      </div>

      {candidatesOpen && (
        <ResearchCandidatesDialog
          onClose={() => setCandidatesOpen(false)}
          onLoadStrategy={candidate => {
            setCandidatesOpen(false)
            // 跨页「载入复测」: 候选经 router state 交给回测策略页消费
            navigate('/backtest?tab=strategy', { state: { loadCandidate: candidate } })
          }}
        />
      )}
    </div>
  )
}
