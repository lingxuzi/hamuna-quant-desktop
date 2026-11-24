import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  RefreshCw, Lock, Loader2, X, Search, CheckCircle2, Hourglass, Download, Lightbulb,
} from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Toolbar } from '@/components/Toolbar'
import { EmptyState } from '@/components/EmptyState'
import { useCapabilities, useCapabilityMatrix } from '@/lib/useSharedQueries'
import { routeCapUsable, routeProviderDisplay } from '@/lib/capability-labels'
import { useFinancialStatus, useFinancialSync } from '@/lib/useFinancials'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { StockFinancialDetail } from '@/components/financials/StockFinancialDetail'
import { ReportHistoryPanel } from '@/components/financials/ReportHistoryPanel'
import { LastStockChip } from '@/components/LastStockChip'
import { useLastStock } from '@/lib/useLastStock'
import { fmtBigNum } from '@/lib/format'
import { toast } from '@/components/Toast'

const TABLE_LABELS: Record<string, string> = {
  metrics: '核心指标',
  income: '利润表',
  balance_sheet: '资产负债表',
  cash_flow: '现金流量表',
  shares: '股本表',
}

/** 「同 步 之 后 看 什 么」说明表 (design/v2/23 的 5 行表) */
const SYNC_GUIDE: [string, string][] = [
  ['核心指标', 'ROE、毛利率、净利率、资产负债率、经营现金流 / 净利润'],
  ['利润表', '营业收入、营业成本、期间费用、归母净利润，逐季同比与环比'],
  ['资产负债表', '总资产、负债、商誉、有息负债，看结构是否在恶化'],
  ['现金流量表', '经营、投资、筹资三条线，重点是经营现金流是否跟得上利润'],
  ['股本表', '总股本与流通股本变化，配合市值与换手率看'],
]

/** 搜索框的账簿体例: 直角发丝线框 + 次级面底 (设计稿 .srch-big input) */
const SEARCH_BOX =
  '[&>div]:max-w-none [&>div]:mx-0 [&_input]:h-9 [&_input]:rounded-none [&_input]:border-border [&_input]:bg-elevated [&_input]:text-[13px]'

export function Financials() {
  const { data: caps } = useCapabilities()
  const { data: matrix } = useCapabilityMatrix()
  const { data: status, isLoading } = useFinancialStatus()
  // 路由感知门控:生效源当前能否提供财务数据 (含插件/自定义源);
  // 矩阵未加载时回退后端可用状态
  const hasFinancial = routeCapUsable(matrix, 'financial')
    ?? (caps?.capabilities?.['financial'] != null || status?.available === true)
  const syncMut = useFinancialSync()
  // 同步进行中 = 服务端真值(status.syncing)或本地乐观态(请求已发出待确认)。
  // 乐观窗口:点击后到 invalidate 触发的 refetch 返回之间,status.syncing 暂为 false,
  // 乐观窗口:点击后到 invalidate 触发的 refetch 返回之间,status.syncing 暂为 false,
  // 后端 trigger() 返回时 syncing 已为 true,refetch 到达后 status.syncing 接管。
  const syncing = (status?.syncing ?? false) || syncMut.isPending
  // 本次同步开始时间戳(ms): 用于判断每张表的 last_sync 是否属于本次同步
  // (后端每张表完成即更新 last_sync, 前端轮询时对比时间戳得到精确进度)
  const [syncStartedAt, setSyncStartedAt] = useState<number | null>(null)
  // 单表同步时记录表名 (null = 全量同步), 用于区分卡片状态
  const [syncSingleTable, setSyncSingleTable] = useState<string | null>(null)
  // 同步自然结束(服务端 syncing 由 true→false):清空本次同步记录。
  // 这是可靠的收尾时机 —— 不依赖 mutation 的 onSettled(它现在瞬间触发,会误清)。
  useEffect(() => {
    if (!syncing && syncStartedAt !== null) {
      setSyncStartedAt(null)
      setSyncSingleTable(null)
    }
  }, [syncing, syncStartedAt])
  // 选中的个股(模糊搜索结果);null 时显示搜索引导
  const [selected, setSelected] = useState<{ symbol: string; name: string } | null>(null)
  const { last: lastStock, remember: rememberStock } = useLastStock('financials')
  const pick = (symbol: string, name: string) => {
    setSelected({ symbol, name })
    rememberStock(symbol, name)
  }

  if (!hasFinancial) {
    return (
      <>
        <PageHeader seal="财" title="财务概况" subtitle="财务概况" />
        {/* 设计稿 23 卷首下始终有工具条; 无财务能力时状态照实 (数据源/五表/同步) */}
        <Toolbar
          left={
            <>
              <span className="tb-chip">数据源 不可用</span>
              <span className="tb-chip warn">五表全空</span>
              <span className="tb-chip warn">未同步</span>
            </>
          }
          right={
            <Link to="/settings?tab=data-sources" className="ctl ctl-md ctl-btn">
              前往数据源配置
            </Link>
          }
        />
        {/* 能力门控: 锁定页走发丝线纸面 (设计稿 23 .empty-rv 的体例) */}
        <div className="mx-auto mt-4 max-w-md border border-border bg-surface p-8 text-center">
          <Lock className="mx-auto h-[20px] w-[20px] text-warning" />
          <h3 className="mt-4 font-serif text-[17px] font-medium text-foreground">财务数据不可用</h3>
          <p className="mx-auto mt-2 max-w-[42ch] text-[11.5px] leading-[1.8] text-secondary">
            当前数据源未提供财务数据。配置提供财务数据的数据源后，此页自动显示财务数据面板。
          </p>
          <Link to="/settings?tab=data-sources" className="ctl-btn ctl-sm pri mt-4">
            前往数据源配置
          </Link>
          {/* 财务数据源由数据能力路由决定,未配置时提示到数据源设置补齐 */}
          <div className="mt-6 border-t border-border pt-3 text-left">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-accent">
              <Lightbulb className="h-3.5 w-3.5 shrink-0" />
              关于数据源
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-secondary">
              财务数据可由内置源或第三方数据源提供，可在数据源页为「财务数据」能力单独选择来源。若你有好用的财务数据源, 可在「设置 → 数据源」里接入自定义源。
            </p>
          </div>
        </div>
      </>
    )
  }

  const handleSync = (table: string) => {
    // 防重复点击:syncing 中不再触发(后端 trigger 也有 _is_syncing 兜底)
    if (syncing) return
    // 记录开始时间: 全量同步判断所有财务表, 单表同步只判断这一张
    setSyncStartedAt(Date.now())
    setSyncSingleTable(table === 'all' ? null : table)
    syncMut.mutate(table, {
      onSuccess: (r) => {
        // 后端 trigger 立即返回 started 状态;若被防并发跳过(已有同步在进行),
        // 给用户明确反馈,并清空本次误设的记录。
        if (!r.synced?.started) {
          if (r.synced?.reason === 'already running') {
            toast('财务数据正在同步中，请稍候', 'success')
          } else if (r.synced?.reason === 'no FINANCIAL capability') {
            // 能力未就绪:通常发生在升级/刷新 Key 后调度器状态未同步 —— 提示用户检查 Key
            toast('财务数据能力未就绪，请检查接口密钥或刷新页面后重试', 'error')
          } else {
            toast(`同步未能开始${r.synced?.reason ? `:${r.synced.reason}` : ''}`, 'error')
          }
          setSyncStartedAt(null)
          setSyncSingleTable(null)
        }
      },
      onError: () => {
        // 请求失败:清空本次记录(request 已弹错误 toast)
        setSyncStartedAt(null)
        setSyncSingleTable(null)
      },
    })
  }

  const tables = status?.tables ?? {}
  const available = status?.available ?? false
  const lastSync = status?.last_sync ?? {}
  // 工具条状态签 (design/v2/23): 已 sync 过的表张数 / 是否同步过
  const anySynced = Object.values(lastSync).some(Boolean)
  const tableCount = Object.values(tables).filter(Boolean).length
  // 本次同步进度: 仅当 syncStartedAt 存在且 syncing 时, 按 last_sync 时间戳判断
  const isFullSync = syncing && syncStartedAt && !syncSingleTable  // 全量同步
  const isSingleSync = syncing && syncStartedAt && !!syncSingleTable  // 单表同步
  const TABLE_ORDER = ['metrics', 'income', 'balance_sheet', 'cash_flow', 'shares'] as const
  const tableDoneThisRound = (key: string): boolean => {
    if (!syncStartedAt || !syncing) return false
    // 单表同步: 只判断这一张表是否完成
    if (syncSingleTable && key !== syncSingleTable) return false
    const ls = lastSync[key]
    if (!ls) return false
    return new Date(ls).getTime() >= syncStartedAt
  }
  // 当前正在同步的表:
  // 全量同步 → 第一个未完成的; 单表同步 → 那张表(未完成时)
  const currentSyncingTable = syncing && syncStartedAt
    ? (syncSingleTable
        ? (tableDoneThisRound(syncSingleTable) ? null : syncSingleTable)
        : TABLE_ORDER.find(t => !tableDoneThisRound(t)) ?? null)
    : null
  const syncedCount = TABLE_ORDER.filter(t => tableDoneThisRound(t)).length
  // 右栏「数 据 源」表 (design/v2/23): 生效源展示名 / 上次同步 / 覆盖标的
  const providerDisplay = routeProviderDisplay(matrix, 'financial')
  const totalSymbols = TABLE_ORDER.reduce((n, t) => n + (tables[t]?.symbols ?? 0), 0)
  const lastSyncTimes = Object.values(lastSync).filter(Boolean)
  const lastSyncText = lastSyncTimes.length
    ? new Date(Math.max(...lastSyncTimes.map(v => new Date(v).getTime()))).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '从未'
  // 表全空 = 五张表 rows 全为 0 (能力接通但从未同步过 → 引导同步, 与「未选股票」分支分开)
  const tablesEmpty = available && TABLE_ORDER.every(t => (tables[t]?.rows ?? 0) === 0)
  // 卡片三态: 仅全量同步时未轮到的表显示"等待"; 单表同步时其他表保持原样
  const isWaitingTable = (key: string): boolean =>
    !!isFullSync && !tableDoneThisRound(key) && currentSyncingTable !== key

  return (
    <>
      <PageHeader seal="财"
        title="财务概况"
        subtitle="财务概况"
        titleExtra={
          <>
            <span className="tb-chip">数据源 {available ? '可用' : '不可用'}</span>
            <span className={`tb-chip${tableCount ? '' : ' warn'}`}>五表{tableCount ? `已落 ${tableCount} 张` : '全空'}</span>
            <span className={`tb-chip${anySynced ? '' : ' warn'}`}>{anySynced ? '已同步' : '未同步'}</span>
          </>
        }
        right={
          <div className="flex items-center gap-2">
            <LastStockChip stock={lastStock} onSelect={pick} />
            {syncing && (
              <span className="text-xs text-accent/80 flex items-center gap-1.5">
                <Loader2 className="w-[12px] h-[12px] animate-spin" />
                {isFullSync
                  ? `已同步 ${syncedCount}/${TABLE_ORDER.length} 张表…`
                  : isSingleSync
                    ? `同步${TABLE_LABELS[syncSingleTable!] ?? syncSingleTable}…`
                    : '同步中…'}
              </span>
            )}
            <button
              className="ctl-btn ctl-sm pri"
              onClick={() => handleSync('all')}
              disabled={syncing}
              title={syncing ? '正在同步，请稍候…' : '同步全部财务表'}
            >
              {syncing
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <RefreshCw className="h-3.5 w-3.5" />}
              {syncing ? '同步中…' : '同步财务数据'}
            </button>
          </div>
        }
      />

      <div className="space-y-6">
        {syncing && (
          <div className="flex items-center gap-2 border-b border-accent/30 pb-2 text-[11px] text-accent">
            <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
            正在从财务数据源拉取数据，请稍候…
          </div>
        )}

        {/* 五表读数带 —— 1px 间隙网格, 格子自己不带框 (设计稿 23 .fh) */}
        {!isLoading && available && (
          <div className="band grid grid-cols-2 md:grid-cols-5">
            {TABLE_ORDER.map(key => {
              const label = TABLE_LABELS[key]
              const info = tables[key]
              // 本次同步三态:完成 / 同步中 / 等待 (仅全量同步时未轮到的表才"等待")
              const doneThisRound = tableDoneThisRound(key)
              const isThisSyncing = currentSyncingTable === key
              const isWaiting = isWaitingTable(key)
              return (
                <div key={key} className="group/cell">
                  {/* 静止态只有 名称/行数/标的 (设计稿 23 .fh); 单表同步按钮 hover 才出现 */}
                  <div className="flex items-center justify-between gap-2">
                    <span className="k">{label}</span>
                    <button
                      className="shrink-0 text-muted opacity-0 transition-opacity hover:text-accent disabled:opacity-40 disabled:cursor-not-allowed group-hover/cell:opacity-100 focus:opacity-100"
                      onClick={() => handleSync(key)}
                      disabled={syncing}
                      title={syncing ? '正在同步…' : `更新${label}`}
                    >
                      {isThisSyncing
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
                        : doneThisRound
                          ? <CheckCircle2 className="h-3.5 w-3.5 text-bull" />
                          : isWaiting
                            ? <Hourglass className="h-3.5 w-3.5 text-muted/60" />
                            : <Download className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                  <div className="v">
                    {fmtBigNum(info?.rows ?? 0)}
                    <span className="ml-1 align-baseline text-[10px] font-normal text-muted">行</span>
                  </div>
                  <small>{fmtBigNum(info?.symbols ?? 0)} 只标的</small>
                </div>
              )
            })}
          </div>
        )}

        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-[20px] w-[20px] animate-spin text-muted" />
          </div>
        ) : !available ? (
          <div className="border border-border bg-surface px-6 py-14 text-center">
            <div className="text-sm text-secondary">暂无财务数据</div>
            <div className="mt-1 text-xs text-muted">点击右上角"同步财务数据"从当前数据源拉取</div>
          </div>
        ) : (
          <>
            {/* 个股搜索区 —— 一行输入 + 右侧「查看」动作 (设计稿 23 .srch-big) */}
            <div className="border-t border-rule-strong pt-4">
              {selected ? (
                // 已选个股:搜索条 + 清除按钮(便于换股)
                <div className="flex items-center gap-2">
                  <div className={`min-w-0 flex-1 ${SEARCH_BOX}`}>
                    <StockFinancialSearch onSelect={pick} />
                  </div>
                  <button
                    onClick={() => setSelected(null)}
                    className="ctl-btn ctl-sm shrink-0"
                    title="清除选择"
                  >
                    <X className="h-3.5 w-3.5" />
                    清除
                  </button>
                </div>
              ) : (
                // 未选个股:一条搜索行 + 读注
                <div className={SEARCH_BOX}>
                  <StockFinancialSearch onSelect={pick} showViewButton />
                </div>
              )}
            </div>

            {/* 双栏主体: 左财务面板 / 右 AI 分析 (设计稿 23 .rv) */}
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
              <div className="min-w-0">
                <div className="sec-h">财 务 面 板</div>
                {selected ? (
                  <StockFinancialDetail symbol={selected.symbol} name={selected.name} />
                ) : tablesEmpty ? (
                  // 表全空分支 (design/v2/23 的 .empty-rv): 能力接通但从未同步 → 引导同步
                  <EmptyState
                    eyebrow="暂无表格"
                    title="五张财务表都是空的"
                    hint="数据源能提供财务数据，但还没同步过。同步一次会把全市场的三张报表、核心指标和股本数据拉进本地库，之后搜任意一只就能看。"
                    action={
                      <>
                        <button onClick={() => handleSync('all')} disabled={syncing} className="ctl ctl-md ctl-btn pri">
                          同步财务数据
                        </button>
                        <Link to="/settings?tab=data-sources" className="ctl ctl-md ctl-btn">
                          去配置数据源
                        </Link>
                      </>
                    }
                  />
                ) : (
                  <div className="flex justify-center py-6 [&_h2]:text-center [&_p]:text-center">
                    <EmptyState
                      icon={Search}
                      title="未选择股票"
                      hint="在上方搜索框输入股票代码或名称，选择后即可查看该股的核心指标、财务报表与股本历史。"
                    />
                  </div>
                )}

                {/* 同 步 之 后 看 什 么 (design/v2/23 的 5 行说明表) */}
                <div className="sec-h">同 步 之 后 看 什 么</div>
                <table className="dt">
                  <colgroup>
                    <col className="w-[92px]" />
                    <col />
                  </colgroup>
                  <tbody>
                    {SYNC_GUIDE.map(([label, desc]) => (
                      <tr key={label}>
                        <td className="font-sans text-[11.5px]">{label}</td>
                        <td className="text-left font-sans text-[11px] leading-relaxed text-secondary">{desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <aside className="min-w-0 lg:border-l lg:border-border lg:pl-5">
                <div className="sec-h">数 据 源</div>
                <table className="dt">
                  <colgroup>
                    <col />
                    <col className="w-[78px]" />
                  </colgroup>
                  <tbody>
                    <tr>
                      <td>财务数据能力</td>
                      <td className={available ? 'text-bull' : ''}>{available ? '可用' : '不可用'}</td>
                    </tr>
                    <tr>
                      <td>提供方</td>
                      <td className="font-sans text-[11px]">{providerDisplay ?? '—'}</td>
                    </tr>
                    <tr>
                      <td>当前状态</td>
                      <td>{syncing ? '同步中' : '空闲'}</td>
                    </tr>
                    <tr>
                      <td>上次同步</td>
                      <td>{lastSyncText}</td>
                    </tr>
                    <tr>
                      <td>覆盖标的</td>
                      <td>{fmtBigNum(totalSymbols)} 只</td>
                    </tr>
                  </tbody>
                </table>

                <div className="sec-h">同 步 说 明</div>
                <p className="text-[11px] leading-[1.85] text-secondary">
                  五张表可以单独同步，也可以一次全量。每张表完成后左上角对应格子的数量会变，不用等全部结束。
                  <br />
                  同步是后台任务，关掉页面也会继续跑。
                </p>

                <div className="sec-h">AI 财 务 分 析</div>
                <p className="text-[11px] leading-[1.85] text-secondary">
                  同步完成后，选中一只股票可以让 AI 读它的五张表写一段分析。分析结果单独存，不写回财务表。
                </p>
                <ReportHistoryPanel />
              </aside>
            </div>
          </>
        )}
      </div>
    </>
  )
}
