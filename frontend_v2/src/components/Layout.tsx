import { useEffect, useLayoutEffect, useMemo, useRef, useState, Suspense, type ReactNode } from 'react'
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { BootGate } from '@/components/BootGate'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { useQuoteStream, useQuoteStreamStatus } from '@/lib/useQuoteStream'
import { ToastContainer, toast } from '@/components/Toast'
import { AlertToastContainer } from '@/components/AlertToast'
import { AiAnalysisHost } from '@/components/financials/AiAnalysisHost'
import { AiReportBubble } from '@/components/financials/AiReportBubble'
import { DailyDataHealthNotice } from '@/components/data/DailyDataHealthNotice'
import { StockAnalysisHost } from '@/components/stock-analysis/StockAnalysisHost'
import { StockPreviewHost } from '@/components/StockPreviewHost'
import { StockAnalysisBubble } from '@/components/stock-analysis/StockAnalysisBubble'
import { MarketAwareHost } from '@/components/screener/MarketAwareHost'
import {
  useCapabilityMatrix,
  useSettings,
  usePreferences,
  useQuoteStatus,
  useVersion,
} from '@/lib/useSharedQueries'
import { useUpdateCheck, RELEASES_URL } from '@/lib/updateCheck'
import {
  useToggleRealtimeQuotes,
} from '@/lib/useSharedMutations'
import { QK, MINUTE_DATA_QUERY_PREFIXES } from '@/lib/queryKeys'
import { setUiPref } from '@/lib/uiPrefs'
import { getPageScale } from '@/lib/pageZoom'
import { setKlineBatchLimits } from '@/lib/klineBatchLimits'
import { compareByDefaultNav, defaultNavIndex, seedHiddenForNewUser } from '@/lib/navOrder'
import {
  DatabaseZap,
  Loader2,
  Tags,
  BarChart3,
  Sparkles,
  CheckCircle2,
  ChevronRight,
  ChevronDown,
  X,
  WifiOff,
  Menu,
  PanelLeft,
  PanelLeftClose,
  PanelLeftOpen,
  Download,
} from 'lucide-react'
import {
  IconDashboard,
  IconWatchlist,
  IconStrategy,
  IconFactors,
  IconBacktest,
  IconRegime,
  IconIndices,
  IconData,
  IconSettings,
  type BrandIconProps,
} from './BrandIcons'
import { Logo } from './Logo'
import { api, type CapabilityMatrix, type IndexQuote } from '@/lib/api'
import { cn } from '@/lib/cn'
import { useIsDesktop, useMediaQuery } from '@/lib/useMediaQuery'
import { useDialogBackdrop } from '@/lib/useDialogBackdrop'
import { resolveWatchlistGroupColor } from '@/lib/watchlist-group-colors'
import { computeGroupPcts, groupPctColor, groupPctTitle } from '@/lib/watchlistGroupStats'
import { fmtPct } from '@/lib/format'
import { findDataSource } from '@/lib/dataSources'
import { setTheme, useTheme } from '@/lib/theme'
import { setCurrentTotal as setAlertTotal, useUnreadAlerts } from '@/lib/monitorBadge'
import { ExtensionSlot } from '@/extensions/ExtensionSlot'
import { RailSlotContext, type RailSlotValue } from './RailSlot'
import { getFrontendExtensionNavigation } from '@/extensions/registry'

// 品牌色 — 只用于 logo / brand 区域,不影响功能语义色
const BRAND = '#B7410E'

// 核心四只指数 — 与后端 index_const.py 单一权威对齐 (前端展示层固定清单)
export const CORE_INDEXES = [
  { symbol: '000001.SH', name: '上证指数' },
  { symbol: '399001.SZ', name: '深证成指' },
  { symbol: '399006.SZ', name: '创业板指' },
  { symbol: '000680.SH', name: '科创综指' },
] as const

type CoreIndex = (typeof CORE_INDEXES)[number]

/**
 * 目录分组 — 账簿目录体例: 纯文字, 五组。分组只决定「显示在哪个标题下」,
 * 排序(nav_order)与隐藏(nav_hidden)仍按路径全局生效, 未列出的路径归入「系统」组末尾。
 */
const NAV_GROUPS: ReadonlyArray<{ name: string; paths: ReadonlyArray<string> }> = [
  { name: '每日', paths: ['/', '/watchlist'] },
  { name: '研究', paths: ['/screener', '/factors', '/backtest', '/stock-analysis', '/signals', '/experts'] },
  { name: '市场', paths: ['/regime', '/indices', '/limit-ladder', '/industry-analysis', '/concept-analysis', '/abnormal'] },
  { name: '交易', paths: ['/paper', '/lots', '/monitor', '/review', '/financials'] },
  { name: '系统', paths: ['/data', '/settings'] },
]
const nav = [
  { to: '/',                 label: '看板',     icon: IconDashboard },
  { to: '/watchlist',        label: '自选',     icon: IconWatchlist },
  { to: '/screener',         label: '策略选股', icon: IconStrategy },
  { to: '/factors',          label: '因子',     icon: IconFactors },
  { to: '/backtest',         label: '回测',     icon: IconBacktest },
  { to: '/regime',           label: '市场环境', icon: IconRegime },
  { to: '/indices',          label: '指数',     icon: IconIndices },
  { to: '/data',             label: '数据',     icon: IconData },
  { to: '/settings',         label: '设置',     icon: IconSettings },
] as const

/** 皮肤切换 — 纸 / 暗 / 亮 三段, 状态存 localStorage, 生效见 lib/theme.ts */
const THEME_OPTS = [
  { id: 'paper', label: '纸' },
  { id: 'dark', label: '暗' },
  { id: 'light', label: '亮' },
] as const

function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const theme = useTheme()
  const current = THEME_OPTS.find(o => o.id === theme) ?? THEME_OPTS[0]
  // 图标条形态放不下三段控件 → 换成「点击轮换」的单按钮
  if (compact) {
    const next = THEME_OPTS[(THEME_OPTS.findIndex(o => o.id === theme) + 1) % THEME_OPTS.length]
    return (
      <button
        type="button"
        onClick={() => setTheme(next.id)}
        title={`界面皮肤: ${current.label}`}
        aria-label={`界面皮肤: ${current.label}, 点击切换`}
        className="toc-rail-link font-mono text-[10px] text-muted"
      >
        {current.label}
      </button>
    )
  }
  return (
    <div role="radiogroup" aria-label="界面皮肤" className="seg">
      {THEME_OPTS.map((o) => {
        const on = theme === o.id
        return (
          <button
            key={o.id}
            role="radio"
            aria-checked={on}
            onClick={() => setTheme(o.id)}
            className={cn(on && 'on')}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

function fmtIndexValue(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return Number(v).toFixed(2)
}

function fmtIndexPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return '--'
  return `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`
}

function indexPctClass(v: number | null | undefined) {
  if (v == null || Number.isNaN(Number(v))) return 'text-muted'
  const n = Number(v)
  if (n === 0) return 'text-foreground'
  return n > 0 ? 'text-bull' : 'text-bear'
}

/** 监控中心未读徽标 — 仅在非监控页且有未读时显示。 */
function MonitorBadge({ active }: { active: boolean }) {
  const unread = useUnreadAlerts()
  // 尊重用户设置: 可在菜单设置里关闭数字提示
  const badgeEnabled = (() => {
    try { return localStorage.getItem('monitor_badge_enabled') !== '0' } catch { return true }
  })()
  if (active || unread <= 0 || !badgeEnabled) return null
  return (
    <span className="inline-flex h-[16px] min-w-[16px] shrink-0 items-center justify-center rounded-full bg-danger px-1 text-[9px] font-bold text-white animate-pulse">
      {unread > 99 ? '99+' : unread}
    </span>
  )
}

function SidebarIndexQuotes({ rows, items }: { rows: IndexQuote[] | undefined; items: readonly CoreIndex[] }) {
  if (items.length === 0) return null
  const quoteBySymbol = new Map((rows ?? []).map(q => [q.symbol, q]))
  return (
    <div className="toc-ix">
      {items.map(item => {
        const q = quoteBySymbol.get(item.symbol)
        const value = q?.last_price ?? q?.close
        const pct = q?.change_pct
        return (
          <NavLink
            key={item.symbol}
            to={`/indices?symbol=${encodeURIComponent(item.symbol)}`}
            className="toc-ix-row"
            title={`${item.name} ${item.symbol}`}
          >
            <span className="toc-ix-n">{item.name}</span>
            <span className={`toc-ix-v ${indexPctClass(pct)}`}>{fmtIndexPct(pct)}</span>
            <span className={`toc-ix-p ${indexPctClass(pct)}`}>{fmtIndexValue(value)}</span>
          </NavLink>
        )
      })}
    </div>
  )
}

// ===== 数据源能力健康卡 =====
// 能力路由架构下的侧栏状态: 不再展示「主数据源」(单源时代遗留 —
// 五个能力各自路由, 拿日K的源代表全局是随意的), 改为回答「各能力当前是否都有源在供」。
// 数据源详情归设置页数据源页 (源详情里自带说明)。
// 单能力方格: 可用=绿 / 日K缺失=红 / 其他缺失=琥珀 (与悬浮卡中同色, 一眼对应)
function capSquareCls(c: { id: string; usable: boolean }) {
  return c.usable ? 'bg-accent' : c.id === 'daily' ? 'bg-danger' : 'bg-warning/80'
}

function DataSourceHealthBadge({ matrix, className }: { matrix: CapabilityMatrix | undefined; className?: string }) {
  const caps = matrix?.capabilities ?? []
  const loading = caps.length === 0
  const usableCount = caps.filter(c => c.usable).length
  const down = caps.filter(c => !c.usable)
  // 日K是核心能力 (其他一切派生于它): 挂了用危险色; 一般缺项琥珀; 全可用绿
  const level = loading
    ? 'loading'
    : down.length === 0 ? 'ok' : down.some(c => c.id === 'daily') ? 'danger' : 'warn'
  const countCls = level === 'ok' ? 'text-accent/80'
    : level === 'danger' ? 'text-danger'
    : level === 'warn' ? 'text-warning'
    : 'text-muted'

  // 悬浮卡: 侧栏 aside 是 overflow-hidden, 用 fixed 定位逃逸裁剪 (坐标取自徽标实时位置)。
  // 徽标靠近屏幕顶部时居中定位会把卡片上半截推出视口 → 渲染后按实际高度钳制进视口。
  const linkRef = useRef<HTMLAnchorElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<number | undefined>(undefined)
  const [popPos, setPopPos] = useState<{ left: number; top: number } | null>(null)
  const openPop = () => {
    window.clearTimeout(closeTimer.current)
    const rect = linkRef.current?.getBoundingClientRect()
    // rect 是视觉坐标, fixed 定位值是 #root 布局坐标 (缩放层), 需 ÷ scale 换算。
    if (rect) {
      const z = getPageScale()
      setPopPos({ left: rect.right / z, top: (rect.top + rect.height / 2) / z })
    }
  }
  const closePop = () => {
    window.setTimeout(() => setPopPos(null), 80)
  }
  useEffect(() => () => window.clearTimeout(closeTimer.current), [])
  useLayoutEffect(() => {
    if (!popPos || !popRef.current) return
    const h = popRef.current.offsetHeight
    const margin = 8
    const minCenter = margin + h / 2
    const maxCenter = window.innerHeight / getPageScale() - margin - h / 2
    const clamped = Math.min(maxCenter, Math.max(minCenter, popPos.top))
    if (clamped !== popPos.top) setPopPos({ ...popPos, top: clamped })
  }, [popPos])

  return (
    <>
      <NavLink
        ref={linkRef}
        to="/settings?tab=data-sources"
        aria-label={`数据源能力 ${usableCount}/${caps.length || 5} 可用, 点击前往数据源配置`}
        onMouseEnter={openPop}
        onMouseLeave={closePop}
        onFocus={openPop}
        onBlur={closePop}
        onKeyDown={e => { if (e.key === 'Escape') setPopPos(null) }}
        className={cn('group relative flex items-center gap-2 overflow-hidden transition-colors duration-150 hover:text-foreground', className)}
      >
        <DatabaseZap className="h-[12px] w-[12px] shrink-0 text-muted group-hover:text-foreground transition-colors" />
        <span className="text-[10px] text-secondary group-hover:text-foreground transition-colors">数据源</span>
        {/* 能力方格 (按注册顺序: 实时/日K/分钟/除权/财务), 与悬浮卡逐格同色对应 */}
        <span className="ml-auto flex items-center gap-1 shrink-0">
          {loading
            ? Array.from({ length: 5 }, (_, i) => (
                <span key={i} className="h-1.5 w-1.5 rounded-[1px] bg-muted animate-pulse" />
              ))
            : caps.map(c => (
                <span key={c.id} className={`h-1.5 w-1.5 rounded-[1px] ${capSquareCls(c)}`} />
              ))}
        </span>
        {!loading && (
          <span className={`text-[10px] font-mono leading-none shrink-0 ${countCls}`}>
            {usableCount}/{caps.length}
          </span>
        )}
      </NavLink>
      {popPos && (
        <div
          ref={popRef}
          className="fixed z-50 -translate-y-1/2 pl-3"
          style={{ left: popPos.left, top: popPos.top }}
          onMouseEnter={() => window.clearTimeout(closeTimer.current)}
          onMouseLeave={closePop}
        >
          <motion.div
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
            className="w-64 rounded-md border border-border bg-surface py-2.5 pl-3 pr-3.5 shadow-2xl shadow-scrim/40"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                <DatabaseZap className="h-3.5 w-3.5 text-accent" />
                数据源能力
              </span>
              <span className={`text-[10px] font-mono font-bold ${countCls}`}>
                {loading ? '获取中…' : `${usableCount}/${caps.length} 可用`}
              </span>
            </div>
            <div className="space-y-1.5 border-t border-border/60 pt-2">
              {loading ? (
                <div className="py-0.5 text-[11px] text-muted">正在获取能力路由状态…</div>
              ) : caps.map(c => (
                <div key={c.id} className="flex min-w-0 items-center gap-2">
                  <span className={`h-[8px] w-[8px] shrink-0 rounded-[2px] ${capSquareCls(c)}`} />
                  <span className="shrink-0 text-xs font-medium text-secondary">{c.label}</span>
                  <span className="ml-auto flex min-w-0 shrink items-center gap-1.5">
                    {c.usable ? (
                      <>
                        <span className="truncate text-[11px] text-muted">{c.effective_display}</span>
                        <CheckCircle2 className="h-[12px] w-[12px] shrink-0 text-accent" />
                      </>
                    ) : (
                      <span className="text-[11px] text-muted/70">未接入</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
            {/* 分时有分钟K功能替身 (intraday_monitor_support 三路可达), 不单独占能力格, 在此备注 */}
            <div className="mt-1.5 text-[10px] leading-relaxed text-muted/70">
              分时信号监控可由分钟 K 数据驱动，不单独设能力格
            </div>
            <div className="mt-2 flex items-center gap-1 border-t border-border/60 pt-1.5 text-[10px] text-muted">
              点击前往数据源配置
              <ChevronRight className="h-[12px] w-[12px]" />
            </div>
          </motion.div>
        </div>
      )}
    </>
  )
}

function AIConfigBadge({ configured, model, className }: { configured?: boolean; model?: string; className?: string }) {
  const descText = configured ? (model || '已接入模型') : '接入策略生成模型'
  return (
    <NavLink
      to="/settings?tab=ai"
      className={cn('group relative flex items-center gap-2 overflow-hidden transition-colors duration-150 hover:text-foreground', className)}
      title={`AI 配置 — ${descText}`}
    >
      <Sparkles className="h-[12px] w-[12px] shrink-0 text-muted group-hover:text-foreground transition-colors" />
      {configured ? (
        <span className="min-w-0 flex-1 truncate text-[10px] text-secondary group-hover:text-foreground transition-colors">
          {model || '已接入模型'}
        </span>
      ) : (
        <>
          <span className="text-[10px] text-secondary group-hover:text-foreground transition-colors">AI 配置</span>
          <span className="ml-auto text-[10px] font-mono leading-none text-muted">未配置</span>
        </>
      )}
      <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${configured ? 'bg-bear' : 'bg-warning'}`} />
    </NavLink>
  )
}

// 侧边栏桌面三态: expanded(14rem) / rail(3.5rem 图标条) / hidden(0 + 左缘悬浮按钮)。
// 移动端 (<768px) 不参与三态 — aside 以抽屉呈现 (见 Layout 内 drawerOpen)。
type NavState = 'expanded' | 'rail' | 'hidden'

export function Layout() {
  // ===== 共享 hooks (替代内联 useQuery) =====
  const { data: settingsState } = useSettings()
  const { data: matrix } = useCapabilityMatrix()
  const { data: versionData } = useVersion()
  // 更新检查 (单例 store): 启动静默查一次 GitHub Releases, 供左下角版本号徽标
  const update = useUpdateCheck()
  const hasUpdate = update.status === 'found'
  const { data: prefs } = usePreferences()
  // 批量端点单请求上限 = 前端分块大小 (与后端校验同源, 见 lib/klineBatchLimits)。
  // 在渲染期写入而非 effect: 分块助手在子组件 queryFn 内调用, 而 queryFn 早于
  // 父组件 effect 触发 —— 用 effect 会让首轮请求仍按默认值分块。幂等, 无状态更新。
  setKlineBatchLimits({
    daily: prefs?.daily_batch_symbols,
    minute: prefs?.minute_batch_symbols,
  })
  // 数据源列表 (用于实时行情状态显示当前数据源名称)
  const { data: dataSources } = useQuery({
    queryKey: QK.dataSources,
    queryFn: api.dataSources,
    staleTime: 60_000,
  })
  // poll=true: 全局唯一开启条件轮询 (非交易时段 60s 兜底, 交易时段靠 SSE)
  const { data: quoteStatus } = useQuoteStatus({ poll: true })
  const { data: analysisMenus } = useQuery({
    queryKey: QK.analysisMenus,
    queryFn: api.analysisMenus,
  })

  // 自选分组 — 仅当用户开启「显示在侧边栏」时拉取
  const groupsInNav = prefs?.watchlist_groups_in_nav ?? false
  const location = useLocation()
  const { data: watchlistGroupsData } = useQuery({
    queryKey: QK.watchlistGroups,
    queryFn: api.watchlistGroups,
    enabled: groupsInNav,
    staleTime: 60_000,
  })
  const watchlistGroups = watchlistGroupsData?.groups ?? []
  // 自选二级菜单展开状态 — 默认当前在自选页时展开
  const [watchlistNavExpanded, setWatchlistNavExpanded] = useState(location.pathname === '/watchlist')

  // 侧边栏三态 — expanded(14rem) / rail(3.5rem 图标条) / hidden(0, 左缘悬浮按钮唤出)。
  // 仅桌面 (≥768px) 参与三态; 移动端 aside 以抽屉呈现, 由 drawerOpen 控制, 恒渲染完整形态。
  // 持久化到 localStorage; 迁移旧两态键 tf-nav-collapsed (收起 → 图标条)。
  const [navState, setNavState] = useState<NavState>(() => {
    try {
      // 两态导航: expanded(14rem) / rail(3.5rem 图标条)。历史三态版本的 'hidden' 归一为展开
      const v = localStorage.getItem('tf-nav-state')
      if (v === 'expanded' || v === 'rail') return v
      return localStorage.getItem('tf-nav-collapsed') === '1' ? 'rail' : 'expanded'
    } catch { return 'expanded' }
  })
  const [drawerOpen, setDrawerOpen] = useState(false)
  const isDesktop = useIsDesktop()
  // 桌面 hidden 态的左缘悬浮按钮: hover 1s 以 overlay 预览 (不挤压主区), 点击固定展开 (push)
  const [overlayPreview, setOverlayPreview] = useState(false)
  const overlayTimer = useRef<number | undefined>(undefined)
  const setNavStatePersist = (s: NavState) => {
    setNavState(s)
    try { setUiPref('tf-nav-state', s) } catch {}
  }
  // 图标条形态仅桌面 rail 态成立 (移动端抽屉与 overlay 预览恒为完整形态)
  const railMode = isDesktop && navState === 'rail'
  // 路由跳转/切回桌面时关抽屉; ESC 同样关闭
  useEffect(() => { setDrawerOpen(false) }, [location.pathname, isDesktop])

  // 右侧指标栏插槽 — 看板页把指标栏 portal 进来, 作为壳层第三栏(设计稿 .app.with-rail 同构)。
  // 指标栏占位宽 272px; 断点与设计稿一致(窄屏 ≤1080px 收单列), 主栏优先。
  const [railHost, setRailHost] = useState<HTMLElement | null>(null)
  const [railActive, setRailActive] = useState(false)
  const railWide = useMediaQuery('(min-width: 1081px)')
  const railCol = railActive && railWide ? '272px' : '0px'
  const railSlot = useMemo<RailSlotValue>(() => ({ host: railHost, setActive: setRailActive }), [railHost])
  useEffect(() => {
    if (!drawerOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDrawerOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawerOpen])

  // 分组等权平均涨跌幅 — 复用 watchlist/enriched 查询缓存(与自选页同 key,
  // 盘中随 SSE 刷新)。可见性门控: 子菜单实际可见(桌面展开 + 二级菜单展开, 或
  // 移动端抽屉打开) 时才拉取, 收起/隐藏/抽屉关闭下不为隐藏 UI 发请求。
  const sidebarFullyVisible = navState === 'expanded' && (isDesktop || drawerOpen)
  const navGroupPctVisible = groupsInNav && sidebarFullyVisible && watchlistNavExpanded
  const { data: navWatchlist } = useQuery({
    queryKey: QK.watchlist,
    queryFn: api.watchlistList,
    enabled: navGroupPctVisible,
    staleTime: 60_000,
  })
  const { data: navEnriched } = useQuery({
    // 无参 → key 归一为 ['watchlist-enriched', ''], 与自选页 (无 ext 列时) 共享缓存,
    // 消除侧栏与自选页各拉一份整表的双份内存
    queryKey: QK.watchlistEnriched(),
    queryFn: () => api.watchlistEnriched(),
    enabled: navGroupPctVisible,
    staleTime: 60_000,
  })
  const navGroupPcts = useMemo(
    () => computeGroupPcts(
      navWatchlist?.symbols ?? [],
      new Map((navEnriched?.rows ?? []).map((r: any) => [r.symbol as string, r])),
    ),
    [navWatchlist, navEnriched],
  )

  // 数据同步状态轮询: 有活跃 job 时「数据」菜单项显示转圈。
  // 标签页隐藏时 TanStack 默认暂停轮询 — 转圈提示只在页面可见时有意义, 不再强制后台轮询
  const { data: pipelineJobs } = useQuery({
    queryKey: QK.pipelineJobs,
    queryFn: () => api.pipelineJobs(1),
    refetchInterval: (query) => (query.state.data?.active_id ? 2000 : 15000),
  })
  const isDataSyncing = !!pipelineJobs?.active_id

  // 数据同步完成的"瞬时反馈": isDataSyncing 从 true→false 时显示绿色对勾,
  // 闪烁约 3 秒后自动消失。
  const [dataSyncJustDone, setDataSyncJustDone] = useState(false)
  const prevSyncingRef = useRef(false)
  useEffect(() => {
    // 仅在"刚结束"(true→false)且非首次挂载时触发
    if (prevSyncingRef.current && !isDataSyncing) {
      setDataSyncJustDone(true)
      const t = setTimeout(() => setDataSyncJustDone(false), 3000)
      prevSyncingRef.current = isDataSyncing
      return () => clearTimeout(t)
    }
    prevSyncingRef.current = isDataSyncing
  }, [isDataSyncing])

  const qc = useQueryClient()
  const latestDataJob = pipelineJobs?.jobs?.[0]
  const refreshedMinuteJob = useRef<string | null>(null)
  useEffect(() => {
    if (!latestDataJob || !['succeeded', 'failed'].includes(latestDataJob.status)) return
    if (latestDataJob.result?.minute_rows == null && !['sync_minute', 'extend_minute'].includes(latestDataJob.stage)) return
    if (refreshedMinuteJob.current === latestDataJob.id) return
    refreshedMinuteJob.current = latestDataJob.id
    // 复用常驻的任务轮询, 离开数据页后补拉结束也会刷新; 取消/失败可能已写入部分数据。
    qc.invalidateQueries({ predicate: q => MINUTE_DATA_QUERY_PREFIXES.has(String(q.queryKey[0])) })
    qc.invalidateQueries({ queryKey: QK.dataStatus })
  }, [latestDataJob?.id, latestDataJob?.status, qc])
  const navigate = useNavigate()
  const version = versionData?.version
  const realtimeEnabled = prefs?.realtime_quotes_enabled ?? false
  // 自选实时模式限制提示: 可手动关闭, 不持久化 (刷新后恢复显示)
  const [dismissFreeHint, setDismissFreeHint] = useState(false)
  // 开启实时行情时若存在排队中的挖掘任务 → 确认弹窗 (实时落盘会让排队任务开跑即失败)
  const [miningQueuedWarning, setMiningQueuedWarning] = useState<number | null>(null)
  const miningWarnBackdrop = useDialogBackdrop(() => setMiningQueuedWarning(null))
  // 两态切换 (仅桌面): 展开 <-> 图标条
  const toggleNavCollapsed = () => {
    setNavStatePersist(navState === 'expanded' ? 'rail' : 'expanded')
  }
  // 指数条: 固定核心四只 (产品契约, 不再可配置), 常驻显示
  const sidebarIndexes = CORE_INDEXES
  const { data: sidebarIndexQuotes } = useQuery({
    queryKey: [...QK.indexQuotes, 'sidebar', 'core'] as const,
    queryFn: () => api.indexQuotes(sidebarIndexes.map(p => p.symbol)),
    enabled: sidebarIndexes.length > 0,
    placeholderData: (prev) => prev,
  })

  // 全市场只数 — 目录尾部「沪深京」读数 (与页眉 Inscription 共用同一缓存条目)
  const { data: sidebarOverview } = useQuery({
    queryKey: QK.overviewMarket(undefined),
    queryFn: () => api.overviewMarket(),
    staleTime: 60_000,
  })
  const universeCount = sidebarOverview?.breadth?.total

  // SSE: 行情更新时自动刷新相关 queries + 告警通知
  useQuoteStream(realtimeEnabled, prefs?.sse_refresh_pages)
  // 实时 SSE 连接状态 — 断开时底部显示提示, 提示可能漏策略告警
  const streamStatus = useQuoteStreamStatus()

  const toggleQuote = useToggleRealtimeQuotes()
  const isRunning = quoteStatus?.running ?? false
  const isTrading = quoteStatus?.is_trading_hours ?? false
  // 管道/数据修正运行期间实时行情被临时暂停 — 此时禁止开启
  const isPaused = quoteStatus?.paused ?? false
  // 实时模式以 quote_status 为准 (数据源无关): none=不可用 / watchlist=自选实时 / full_market=全市场
  const quoteMode = quoteStatus?.mode ?? 'none'
  const realtimeUnavailable = quoteMode === 'none'
  const isWatchlistMode = quoteMode === 'watchlist'
  const realtimeModeLabel = isWatchlistMode ? '自选' : '全市场'
  // 当前实时行情数据源名称 (插件/自定义源显示源名, 内置源不显示)
  const realtimeProvider = prefs?.realtime_data_provider
  const realtimeProviderName = realtimeProvider && realtimeProvider !== 'easytdx'
    ? (findDataSource(dataSources, realtimeProvider)?.display_name || realtimeProvider)
    : null
  const realtimeToggleDisabled = toggleQuote.isPending || isPaused
  const realtimeActive = realtimeEnabled && isRunning && isTrading
  const realtimeStatusLabel = toggleQuote.isPending
    ? '正在更新'
    : isPaused
      ? '同步期间暂停'
      : realtimeActive
        ? '运行中'
        : realtimeEnabled
          ? (isTrading ? '正在连接' : '等待交易时段')
          : '已关闭'
  const realtimeStatusClass = realtimeActive
    ? 'text-accent'
    : realtimeEnabled || isPaused
      ? 'text-warning/80'
      : 'text-muted'
  const realtimeIndicatorClass = realtimeActive
    ? 'bg-accent animate-pulse'
    : realtimeEnabled || isPaused
      ? 'bg-warning/70'
      : 'bg-muted'
  const realtimeToggleTitle = isPaused
    ? '数据同步运行中，实时行情已临时暂停'
    : toggleQuote.isPending
      ? '正在更新实时行情设置'
      : realtimeEnabled
        ? '关闭实时行情'
        : '开启实时行情'

  // 轮询触发记录总数 → 更新监控中心徽标 (每 15 秒; 后台标签页由 SSE 事件驱动, 不轮询)。
  // 非交易时段无新告警, 降为 2 分钟兜底
  const alertsTotalQuery = useQuery({
    queryKey: ['alerts-total'],
    queryFn: () => api.alertsList({ days: 7, limit: 1 }),
    refetchInterval: () => (isTrading ? 15000 : 120000),
    select: (data) => data.total,
  })
  // 只在拿到真实总数时同步徽标 (避免 data=undefined 时传 0 重置 lastSeen)
  const alertsTotal = alertsTotalQuery.data
  useEffect(() => {
    if (alertsTotal != null) setAlertTotal(alertsTotal)
  }, [alertsTotal])

  // 合并内置页面 + 可见的扩展分析菜单
  type NavIcon = (props: BrandIconProps) => ReactNode
  type NavItem = { to: string; label: string; icon: NavIcon; badge?: string }
  const analysisNav: NavItem[] = (analysisMenus?.items ?? [])
    .filter(m => m.visible)
    .map(m => ({ to: `/analysis/${m.id}`, label: m.label, icon: m.icon === 'tags' ? Tags : BarChart3 }))
  const extensionNav: NavItem[] = getFrontendExtensionNavigation().map(item => ({
    to: item.route.path,
    label: item.label,
    icon: item.icon,
    badge: item.badge,
  }))

  const allNav: NavItem[] = [...nav, ...analysisNav, ...extensionNav]
  const savedOrder = prefs?.nav_order ?? []

  const navItems = savedOrder.length > 0
    ? (() => {
        const byTo = new Map(allNav.map(n => [n.to, n]))
        const ordered = (savedOrder
          .map(id => byTo.get(id) ?? byTo.get(`/analysis/${id}`))
          .filter(Boolean)) as typeof allNav
        const seen = new Set(ordered.map(n => n.to))
        const merged = [...ordered]
        for (const item of allNav) {
          if (seen.has(item.to)) continue
          // 未保存过排序的新条目: 默认基准内的路径插回默认位置(排在已出现的
          // 最近默认前驱之后), 分析菜单/未知扩展追加到末尾。基准含迁移到
          // custom/ 的扩展域页面, 保证老用户升级无感 (lib/navOrder)。
          const di = defaultNavIndex(item.to)
          let anchor = -1
          if (Number.isFinite(di) && di > 0) {
            for (let i = di - 1; i >= 0 && anchor < 0; i -= 1) {
              anchor = merged.findIndex(n => defaultNavIndex(n.to) === i)
            }
          }
          if (anchor >= 0) merged.splice(anchor + 1, 0, item)
          else if (di === 0) merged.unshift(item)
          else merged.push(item)
        }
        return merged
      })()
    // 无保存排序 (新装/从未调整过菜单): 按迁移前默认基准排序, 扩展域页面
    // 不沉底 — 老用户升级侧栏顺序与迁移前一致; 未知路径稳定追加在后。
    : [...allNav].sort((a, b) => compareByDefaultNav(a.to, b.to))

  const hiddenIds = new Set(prefs?.nav_hidden ?? [])
  const visibleNavItems = navItems.filter(n => !hiddenIds.has(n.to) && !hiddenIds.has(n.to.replace(/^\/analysis\//, '')))

  // 新装用户一次性种子: 扩展域页面默认隐藏, 菜单设置里可逐个开启。
  // 老用户 (升级前已完成 onboarding) 只写标记不动偏好, 升级前后侧栏一致。
  // 种子标记存 localStorage (每设备一次, 保存成功后才写); 隐藏偏好本体持久化在后端 nav_hidden。
  const extSeedRef = useRef(false)
  useEffect(() => {
    if (!prefs || !settingsState || extSeedRef.current) return
    let seeded = false
    try { seeded = localStorage.getItem('tf-ext-pages-seed') === '1' } catch { return }
    if (seeded) { extSeedRef.current = true; return }
    const next = seedHiddenForNewUser(settingsState.onboarding_completed, prefs.nav_hidden)
    if (!next) {
      extSeedRef.current = true
      try { localStorage.setItem('tf-ext-pages-seed', '1') } catch {}
      return
    }
    extSeedRef.current = true
    api.saveNavHidden(next)
      .then(() => { try { localStorage.setItem('tf-ext-pages-seed', '1') } catch {} })
      .then(() => qc.invalidateQueries({ queryKey: QK.preferences }))
      .catch(() => { /* 保存失败不写标记, 下次启动重试; 结果集幂等, 重复触发无害 */ })
  }, [prefs, settingsState, qc])

  const doEnableRealtime = async () => {
    await toggleQuote.mutateAsync(true)
    // 仅在交易时段立即获取一次行情
    if (isTrading) {
      api.intradayRefresh().catch(() => {})
    }
  }

  const handleToggle = async (enabled: boolean) => {
    // 开启时重新校验实时权限 (以 quote_status 的数据源无关判定为准)
    if (!enabled) {
      await toggleQuote.mutateAsync(false)
      return
    }
    const fresh = await qc.fetchQuery({
      queryKey: QK.quoteStatus,
      queryFn: api.quoteStatus,
      // 刚切换到 fuyao 等源时, 全局 staleTime 内仍可能缓存旧源的权限。
      staleTime: 0,
    })
    if (!fresh.realtime_allowed) {
      toast('当前数据源无实时行情能力, 请先配置数据源', 'error')
      return
    }
    // 有排队中的挖掘任务时确认: 实时落盘会让排队任务开跑即失败
    // (data generation changed); 运行中的任务会自动跟随新数据, 不受影响。
    try {
      const runs = await qc.fetchQuery({
        queryKey: QK.miningRuns,
        queryFn: api.miningRuns,
        staleTime: 5_000,
      })
      const queued = (runs?.items ?? []).filter(r => r.status === 'queued').length
      if (queued > 0) {
        setMiningQueuedWarning(queued)
        return
      }
    } catch {
      // 挖掘运行历史查询失败不阻塞开关实时行情
    }
    await doEnableRealtime()
  }

  /** 目录中的一条 — 自选项在开启分组侧栏时渲染为可展开父项 + 二级分组 */
  const renderNavItem = ({ to, label, icon: Icon, badge }: NavItem) => {
    const isWatchlistExpandable = to === '/watchlist' && groupsInNav && !railMode && watchlistGroups.length > 0
    // 图标条形态: 设计稿没有这一态, 保留 v1 的图标条 (功能不丢)
    if (railMode) {
      return (
        <NavLink
          key={to}
          to={to}
          title={label}
          className={({ isActive }) => cn('toc-rail-link', isActive && 'on')}
        >
          {({ isActive }) => (
            <>
              <span className="toc-rail-icon">
                <Icon className={cn('h-[13px] w-[13px]', isActive ? 'text-accent' : 'text-muted')} />
              </span>
              {badge && <span className="toc-rail-dot" />}
            </>
          )}
        </NavLink>
      )
    }
    return (
      <div key={to}>
        {isWatchlistExpandable ? (
          <button
            onClick={() => setWatchlistNavExpanded(v => !v)}
            className={cn('toc-row', location.pathname === '/watchlist' && 'on')}
          >
            <span>{label}</span>
            {watchlistNavExpanded
              ? <ChevronDown className="toc-chev" />
              : <ChevronRight className="toc-chev" />
            }
          </button>
        ) : (
          <NavLink
            to={to}
            className={({ isActive }) => cn(isActive && 'on')}
          >
            {({ isActive }) => (
              <>
                <span>{label}</span>
                {badge && (
                  <em className="inline-flex items-center rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-400 shrink-0">
                    {badge}
                  </em>
                )}
                {to === '/data' && isDataSyncing && (
                  <Loader2 className="ml-auto h-[12px] w-[12px] shrink-0 animate-spin text-accent" />
                )}
                {to === '/data' && !isDataSyncing && dataSyncJustDone && (
                  <CheckCircle2 className="ml-auto h-[12px] w-[12px] shrink-0 text-bull animate-pulse" />
                )}
                {to === '/monitor' && <MonitorBadge active={isActive} />}
              </>
            )}
          </NavLink>
        )}

        {/* 自选分组二级子菜单 — 展开时显示 */}
        {isWatchlistExpandable && watchlistNavExpanded && (
          <div className="toc-sub">
            <NavLink
              to="/watchlist"
              className={({ isActive }) => cn('toc-row', isActive && !location.search && 'on')}
            >
              <span className="toc-dot bg-muted" />
              <span>全部</span>
              {(() => {
                const info = navGroupPcts['all']
                return info && info.pct != null ? (
                  <em className={`font-mono text-[10px] tabular-nums ${groupPctColor(info.pct)}`} title={groupPctTitle(info)}>
                    {fmtPct(info.pct)}
                  </em>
                ) : null
              })()}
            </NavLink>
            {watchlistGroups.map(group => {
              const color = resolveWatchlistGroupColor(group.color)
              const groupPath = `/watchlist?group=${group.id}`
              const isGroupActive = location.pathname === '/watchlist' && location.search === `?group=${group.id}`
              const pctInfo = navGroupPcts[group.id]
              return (
                <NavLink
                  key={group.id}
                  to={groupPath}
                  className={cn('toc-row', isGroupActive && 'on')}
                >
                  <span className="toc-dot" style={{ background: color.dot }} />
                  <span>{group.name}</span>
                  {pctInfo && pctInfo.pct != null && (
                    <em className={`font-mono text-[10px] tabular-nums ${groupPctColor(pctInfo.pct)}`} title={groupPctTitle(pctInfo)}>
                      {fmtPct(pctInfo.pct)}
                    </em>
                  )}
                </NavLink>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  return (
    <div
      className="h-full grid bg-base text-foreground overflow-hidden transition-[grid-template-columns] duration-200 ease-smooth"
      style={{ gridTemplateColumns: isDesktop && !overlayPreview
        ? `${navState === 'expanded' ? '14rem' : navState === 'rail' ? '3.25rem' : '0'} minmax(0, 1fr) ${railCol}`
        : `minmax(0, 1fr) ${railCol}` }}
    >
      {/* 移动端抽屉遮罩 */}
      {!isDesktop && drawerOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50"
          onClick={() => setDrawerOpen(false)}
          aria-hidden="true"
        />
      )}
      {/* 移动端汉堡按钮 / 桌面 hidden 态左缘悬浮按钮 (hover 1s overlay 预览, 点击固定展开) */}
      {(!isDesktop || navState === 'hidden') && !overlayPreview && (
        <button
          onClick={() => {
            window.clearTimeout(overlayTimer.current)
            setOverlayPreview(false)
            if (isDesktop) setNavStatePersist('expanded')
            else setDrawerOpen(true)
          }}
          onMouseEnter={() => {
            if (!isDesktop) return
            window.clearTimeout(overlayTimer.current)
            overlayTimer.current = window.setTimeout(() => setOverlayPreview(true), 1000)
          }}
          onMouseLeave={() => window.clearTimeout(overlayTimer.current)}
          className={cn(
            'fixed z-30 rounded-btn border border-border bg-surface/90 text-muted shadow-lg backdrop-blur-sm',
            'hover:text-foreground hover:bg-elevated transition-colors duration-150 ease-smooth',
            isDesktop ? 'left-1.5 top-1/2 -translate-y-1/2 p-2' : 'left-3 top-3 p-2',
          )}
          title={isDesktop ? '展开菜单' : '打开菜单'}
        >
          {isDesktop
            ? <PanelLeftOpen className="h-[16px] w-[16px] shrink-0" />
            : <Menu className="h-[16px] w-[16px] shrink-0" />}
        </button>
      )}
      <aside
        onMouseLeave={() => { if (overlayPreview) setOverlayPreview(false) }}
        className={cn(
          'toc min-h-0 bg-base text-foreground',
          isDesktop
            ? cn('h-full', railMode && 'toc-rail', navState === 'hidden' && !overlayPreview && 'border-r-0')
            : cn(
                'fixed inset-y-0 left-0 z-50 w-[80vw] max-w-[320px] overflow-y-auto shadow-2xl',
                'transition-transform duration-200 ease-smooth',
                drawerOpen ? 'translate-x-0' : '-translate-x-full',
              ),
          overlayPreview && 'fixed inset-y-0 left-0 z-50 w-56 shadow-2xl',
        )}
      >
        {/* 题头: 展开形态不留品牌区 (设计稿目录从「每日」直接起头);
            仅图标条 (收起态) 与移动端抽屉保留品牌位与展开/关闭控件。 */}
        {(railMode || !isDesktop) && (
        <div className={cn('toc-brand shrink-0', railMode && 'toc-brand-rail')}>
          <div className={cn('flex items-center', railMode ? 'flex-col gap-1' : 'gap-2.5')}>
            <Logo size={railMode ? 22 : 24} className="shrink-0" style={{ color: BRAND }} />
            {!railMode && (
              <div className="flex min-w-0 flex-col leading-tight">
                <span className="whitespace-nowrap font-serif text-[13px] font-semibold tracking-[0.14em] text-foreground">
                  量化工作台
                </span>
                <span className="whitespace-nowrap font-mono text-[8px] uppercase tracking-[0.2em] text-muted">
                  Quant Workbench
                </span>
              </div>
            )}
            {/* 展开 (图标条) / 关闭菜单 (移动端抽屉) */}
            {railMode ? (
              <button
                onClick={toggleNavCollapsed}
                className="ctl ctl-sm ctl-ghost ctl-icon flex items-center justify-center text-muted duration-150 hover:text-foreground"
                title="展开菜单"
              >
                <PanelLeft className="h-3.5 w-3.5 shrink-0" />
              </button>
            ) : (
              <button
                onClick={() => setDrawerOpen(false)}
                className="ctl ctl-sm ctl-ghost ctl-icon ml-auto flex items-center text-muted duration-150 hover:text-foreground"
                title="关闭菜单"
              >
                <X className="h-[16px] w-[16px] shrink-0" />
              </button>
            )}
          </div>
          {/* 收起态控件: 皮肤 + 实时行情指示点 */}
          {railMode && (
            <div className="mt-1 flex flex-col items-center">
              <ThemeToggle compact />
              <button
                onClick={() => handleToggle(!realtimeEnabled)}
                disabled={realtimeToggleDisabled}
                aria-label={realtimeToggleTitle}
                aria-busy={toggleQuote.isPending}
                title={realtimeToggleTitle}
                className="toc-rail-link mt-1"
              >
                <span className={cn('h-[8px] w-[8px] rounded-full', realtimeIndicatorClass)} />
              </button>
            </div>
          )}
        </div>
        )}

        {/* 目录: 五组纯文字 (设计稿体例 — 分组标题 + 12px 条目) */}
        <nav aria-label="功能目录" className="toc-nav">
          {(() => {
            const itemByTo = new Map(visibleNavItems.map(n => [n.to, n] as const))
            const groups = NAV_GROUPS.map(g => ({
              name: g.name,
              items: g.paths.map(p => itemByTo.get(p)).filter((x): x is NavItem => !!x),
            }))
            // 未列在五组里的路径 (扩展页/声明式分析页) 归入「系统」组, 顺序沿用 navItems
            const sys = groups[groups.length - 1]
            const listed = new Set(NAV_GROUPS.flatMap(g => g.paths))
            for (const item of visibleNavItems) {
              if (!listed.has(item.to)) sys.items.push(item)
            }
            return groups.filter(g => g.items.length > 0 || (!railMode && g.name === '系统')).map(g => (
              <div key={g.name} className="toc-g">
                {!railMode && <div className="toc-g-name">{g.name}</div>}
                {g.items.map(item => renderNavItem(item))}
              </div>
            ))
          })()}
          <ExtensionSlot
            name="layout.navigation.extra"
            context={{ collapsed: railMode, pathname: location.pathname }}
            compact
          />
        </nav>

        {/* 尾部状态 (设计稿 .toc-tail 的 KV 体例) */}
        {!railMode && (
          <div className="toc-tail shrink-0">
            <DataSourceHealthBadge matrix={matrix} className="toc-rowline" />
            <AIConfigBadge
              configured={settingsState?.ai_configured ?? settingsState?.has_ai_key}
              model={settingsState?.ai_model}
              className="toc-rowline"
            />
            <span>沪深京<b>{universeCount != null ? universeCount.toLocaleString('zh-CN') : '—'}</b></span>
            {/* 版本行: 有新版本时版本号旁挂更新标记 (底部控制区宽度已满, 更新入口归位到版本旁) */}
            <span>
              版本
              <b className="inline-flex items-center gap-1.5">
                {version || '—'}
                {hasUpdate && (() => {
                  // 清单没给 notes_url 且未配置下载页时只提示不可点, 避免 href="" 空跳转
                  const href = update.info?.url || RELEASES_URL
                  const cls = 'inline-flex items-center text-amber-400 transition-transform hover:scale-110 animate-breath'
                  const title = `发现新版本 ${update.info?.latest ?? ''}，点击前往下载`
                  return href ? (
                    <a href={href} target="_blank" rel="noreferrer" className={cls}
                       title={title} aria-label="前往下载新版本">
                      <Download className="h-[11px] w-[11px]" />
                    </a>
                  ) : (
                    <span className={cls} title={title} aria-label="有新版本可更新">
                      <Download className="h-[11px] w-[11px]" />
                    </span>
                  )
                })()}
              </b>
            </span>
          </div>
        )}

        {/* 控件区: 实时开关 / 快捷入口 / 指数条 / 皮肤与设置 */}
        {!railMode && (
        <div className="toc-ctl shrink-0">
          {realtimeUnavailable && !realtimeProviderName ? (
            <div>
              <div className="toc-ctl-hd">
                <span>实时行情</span>
                <span>不可用</span>
              </div>
              <div className="toc-ctl-hint">
                当前数据源无实时行情权限,
                <button
                  type="button"
                  onClick={() => navigate('/settings?tab=data-sources&highlight=data-sources')}
                  className="toc-link"
                >
                  去配置数据源
                </button>
              </div>
            </div>
          ) : (
            /* 实时可用 — 状态 + 开关 (设置入口在「系统」组, 不再重复) */
            <div className="toc-ctl-row">
              <span className={cn('toc-dot shrink-0', realtimeIndicatorClass)} />
              <span className="toc-ctl-label">实时行情</span>
              <span
                className={`toc-ctl-val ${realtimeStatusClass}`}
                title={`${realtimeProviderName || realtimeModeLabel} · ${realtimeStatusLabel}`}
              >
                {realtimeStatusLabel}
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={realtimeEnabled}
                aria-label={realtimeToggleTitle}
                aria-busy={toggleQuote.isPending}
                onClick={() => handleToggle(!realtimeEnabled)}
                disabled={realtimeToggleDisabled}
                title={realtimeToggleTitle}
                className={cn('sw ml-auto', realtimeEnabled && 'on', realtimeToggleDisabled && 'dis')}
              >
                <i />
              </button>
            </div>
          )}

          {/* 状态提示 */}
          {realtimeEnabled
            && (!realtimeUnavailable || realtimeProviderName)
            && (isPaused || (isWatchlistMode && !dismissFreeHint && !realtimeProviderName))
            && (
              <div className="toc-ctl-hint">
                {isWatchlistMode && !dismissFreeHint && !realtimeProviderName && (
                  <div className="flex items-start gap-1 text-amber-400/80">
                    <span className="flex-1">自选实时模式监控前 5 只，全市场实时依赖数据源支持</span>
                    <button
                      onClick={() => setDismissFreeHint(true)}
                      className="text-amber-400/50 hover:text-amber-400 shrink-0 transition-colors"
                      title="关闭提示"
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </div>
                )}
                {isPaused && (
                  <div className="text-warning/80">数据同步运行中，实时行情已临时暂停</div>
                )}
              </div>
            )}

          {/* 快捷入口: 扩展快捷方式 (数据同步状态在导航项「数据」上) */}
          <div className="toc-quick">
            <ExtensionSlot name="layout.header.quick" context={{}} compact />
          </div>

          {!isWatchlistMode && (!realtimeUnavailable || !!realtimeProviderName) && (
            <SidebarIndexQuotes rows={sidebarIndexQuotes?.rows} items={sidebarIndexes} />
          )}

          {/* 皮肤 + 收起 (更新标记在「版本」行) */}
          <div className="toc-foot">
            <ThemeToggle />
            <button
              onClick={toggleNavCollapsed}
              className="toc-ctl-icon ml-auto"
              title="收起菜单"
              aria-label="收起菜单"
            >
              <PanelLeftClose className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        )}
      </aside>

      <motion.main
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
        className="page scroll-shell h-full overflow-auto scrollbar-gutter-stable"
      >
        {streamStatus === 'reconnecting' && (
          <div
            role="status"
            aria-live="polite"
            className="fixed bottom-4 left-1/2 z-[9998] flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-warning/30 bg-warning/10 px-2.5 py-1 text-[11px] font-medium text-warning shadow-lg backdrop-blur-md"
          >
            <WifiOff className="h-[12px] w-[12px] shrink-0 animate-pulse" />
            与服务连接已断开 · 正在重连
          </div>
        )}
        <DailyDataHealthNotice />
        <RailSlotContext.Provider value={railSlot}>
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-24">
                <Loader2 className="h-[20px] w-[20px] animate-spin text-muted" />
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </RailSlotContext.Provider>
      </motion.main>
      {/* 指标栏挂载点 — display:contents 让 portal 进来的指标栏直接成为壳层第三栏的网格项 */}
      <div ref={setRailHost} className="contents" />
      <ToastContainer />
      <AlertToastContainer />
      <AiAnalysisHost />
      <AiReportBubble />
      <StockAnalysisHost />
      <StockAnalysisBubble />
      <MarketAwareHost />
      <StockPreviewHost />
      {/* 入口闸门: 本地日K有未完成的修补/重算时, 阻塞到修完为止 */}
      <BootGate />

      {/* 开启实时行情 + 排队中的挖掘任务 → 冲突确认 */}
      {miningQueuedWarning != null && (
        <div {...miningWarnBackdrop} className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4">
          <div onClick={e => e.stopPropagation()} className="w-full max-w-sm rounded-2xl border border-border bg-surface p-5 shadow-2xl">
            <div className="text-sm font-semibold text-foreground">
              有挖掘任务正在排队
            </div>
            <p className="mt-2 text-xs leading-5 text-secondary">
              当前有 {miningQueuedWarning} 个挖掘任务排队等待执行。开启实时行情后盘中数据会持续落盘，
              排队中的任务启动时可能因数据更新校验而失败（需重新开始挖掘）；已开始运行的任务不受影响。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setMiningQueuedWarning(null)} className="ctl ctl-md ctl-btn px-2.5 hover:bg-elevated">取消</button>
              <button type="button" onClick={() => { setMiningQueuedWarning(null); void doEnableRealtime() }} className="ctl ctl-md ctl-btn pri px-2.5 font-semibold hover:opacity-90">仍要开启</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
