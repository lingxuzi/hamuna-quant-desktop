/**
 * 统一设置页面 — Tab 切换外壳 (设计稿 26-settings-pages 的 .set .tabs)。
 *
 * 通过 URL query param ?tab=xxx 同步 Tab 状态。
 */
import { Suspense, lazy, useEffect, useState, type ComponentType } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Database, KeyRound, LayoutGrid, Library, Radio, Sparkles, Settings2, PanelLeftClose, PanelLeftOpen, Clock3, PackageOpen } from 'lucide-react'
import { toast } from '@/components/Toast'
// 面板按 tab 按需加载: 8 个面板源码 270KB+, 同步打包会让设置页 chunk 膨胀
const SettingsAIPanel = lazy(() => import('./settings/AI').then(m => ({ default: m.SettingsAIPanel })))
const SettingsApiTokensPanel = lazy(() => import('./settings/ApiTokens').then(m => ({ default: m.SettingsApiTokensPanel })))
const SettingsImportCenterPanel = lazy(() => import('./settings/ImportCenter').then(m => ({ default: m.SettingsImportCenterPanel })))
const SettingsDefinitionsPanel = lazy(() => import('./settings/Definitions').then(m => ({ default: m.SettingsDefinitionsPanel })))
const SettingsMonitoringPanel = lazy(() => import('./settings/Monitoring').then(m => ({ default: m.SettingsMonitoringPanel })))
const SettingsPageAdminPanel = lazy(() => import('./settings/PageAdmin').then(m => ({ default: m.SettingsPageAdminPanel })))
const SettingsTimeoutPanel = lazy(() => import('./settings/Timeout').then(m => ({ default: m.SettingsTimeoutPanel })))
const SettingsSystemPanel = lazy(() => import('./settings/System').then(m => ({ default: m.SettingsSystemPanel })))
const SettingsDataSourcesPanel = lazy(() => import('./settings/DataSources').then(m => ({ default: m.SettingsDataSourcesPanel })))
import { PageHeader } from '@/components/PageHeader'
import { cn } from '@/lib/cn'
import { setUiPref } from '@/lib/uiPrefs'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { getFrontendExtensionNavigation } from '@/extensions/registry'
import { BUILTIN_CATALOG } from './settings/PageAdmin'

// ===== Tab 定义 =====

type TabDef = {
  key: string
  label: string
  icon: ComponentType<{ className?: string }>
  panel: ComponentType<{ highlight?: string }>
  badge?: string
}

const TABS: readonly TabDef[] = [
  { key: 'data-sources', label: '数据源',     icon: Database,  panel: SettingsDataSourcesPanel },
  { key: 'ai',         label: 'AI 设置',    icon: Sparkles,  panel: SettingsAIPanel },
  { key: 'monitoring', label: '实时监控',   icon: Radio,      panel: SettingsMonitoringPanel },
  { key: 'pages',      label: '页面管理',   icon: LayoutGrid, panel: SettingsPageAdminPanel },
  { key: 'timeout',    label: '网络设置',   icon: Clock3,     panel: SettingsTimeoutPanel },
  { key: 'api-tokens', label: '开放接口',   icon: KeyRound,   panel: SettingsApiTokensPanel },
  { key: 'definitions', label: '定义目录',   icon: Library,    panel: SettingsDefinitionsPanel },
  { key: 'import',     label: '导入与备份', icon: PackageOpen, panel: SettingsImportCenterPanel },
  { key: 'system',     label: '系统设置',   icon: Settings2,  panel: SettingsSystemPanel },
]

// 「页面管理」归拢前的三个 tab key → 深链子区块, 历史链接零失效
const LEGACY_PAGE_TAB_SECTIONS: Record<string, string> = {
  menus: 'menus',
  'ext-pages': 'ext-pages',
}

type TabKey = (typeof TABS)[number]['key']

export function Settings() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab') as TabKey | null
  // 旧 tab (menus/ext-pages/page-packs) 已并入「页面管理」: 渲染按 pages 处理,
  // 再用 replace 把 URL 归一化并透传 section, 保持深链与历史记录干净
  const legacySection = tabParam ? LEGACY_PAGE_TAB_SECTIONS[tabParam] : undefined
  const legacyImport = tabParam === 'backup' || tabParam === 'page-packs' || (tabParam === 'pages' && searchParams.get('section') === 'page-packs')
  const activeTab = TABS.find((t) => t.key === (legacyImport ? 'import' : legacySection ? 'pages' : tabParam)) ?? TABS[0]
  const highlight = searchParams.get('highlight') ?? ''

  useEffect(() => {
    if (legacyImport) {
      const next = new URLSearchParams(searchParams)
      next.set('tab', 'import')
      next.set('view', tabParam === 'backup' ? 'backup' : 'installed')
      next.delete('section')
      setSearchParams(next, { replace: true })
      return
    }
    if (!legacySection) return
    const next = new URLSearchParams(searchParams)
    next.set('tab', 'pages')
    next.set('section', legacySection)
    setSearchParams(next, { replace: true })
  }, [legacySection, legacyImport, tabParam, searchParams, setSearchParams])

  // 设置菜单收起状态 — 持久化到 localStorage
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('tf-settings-nav-collapsed') === '1' } catch { return false }
  })
  const toggleCollapsed = () => {
    setCollapsed(prev => {
      const next = !prev
      try { setUiPref('tf-settings-nav-collapsed', next ? '1' : '0') } catch {}
      return next
    })
  }

  // design/v2/26: 页面管理子页有独立卷首; 其它子页沿用「设置」
  const pagesTab = activeTab.key === 'pages'

  // ===== 工具条 (设计稿 26): 状态签 + 保存 =====
  // 目录计数与 PageAdmin 同口径: 内建目录常量 + analysisMenus + 源码扩展注册表
  const { data: prefs } = usePreferences()
  const menus = useQuery({ queryKey: QK.analysisMenus, queryFn: api.analysisMenus })
  const builtinCount = BUILTIN_CATALOG.length
  const extCount = getFrontendExtensionNavigation().length + (menus.data?.items?.length ?? 0)
  const catalogCount = builtinCount + extCount

  // 保存: 各面板改动均即时落库, 这里把当前目录顺序/显隐再持久化一次 (幂等),
  // 与设计稿工具条的「保存」语义对齐, 也覆盖个别浏览器丢弃未完成请求的边缘情况
  const [saving, setSaving] = useState(false)
  const handleSave = async () => {
    setSaving(true)
    try {
      await api.saveNavOrder(prefs?.nav_order ?? [])
      await api.saveNavHidden(prefs?.nav_hidden ?? [])
      toast('设置已保存', 'success')
    } catch (e) {
      toast((e as Error).message || '保存失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <PageHeader seal="设"
        title={pagesTab ? '设置 · 页面管理' : '设置'}
        subtitle={pagesTab ? 'SETTINGS / PAGES' : 'SETTINGS'}
        titleExtra={
          <>
            <span className="tb-chip">{TABS.length} 个设置页</span>
            <span className="tb-chip">{catalogCount} 页目录</span>
            <span className="tb-chip">内建 {builtinCount} · 扩展 {extCount}</span>
          </>
        }
        right={
          <button className="ctl-btn ctl-sm pri" disabled={saving} onClick={handleSave}>
            {saving ? '保存中…' : '保存'}
          </button>
        }
        className="mb-0"
      />

      <div className="pt-1">
        <div className="flex flex-col gap-6 md:flex-row md:items-stretch">
          <label className="flex items-center gap-3 text-xs text-secondary md:hidden">
            设置分类
            <select aria-label="设置分类" value={activeTab.key} onChange={e => setSearchParams({ tab: e.target.value }, { replace: true })} className="ctl ctl-lg ctl-select min-w-0 flex-1">
              {TABS.map(tab => <option key={tab.key} value={tab.key}>{tab.label}</option>)}
            </select>
          </label>
          {/* ===== 竖向 Tab 侧栏 (design/v2/26 .set .tabs: 176px 纯文字 + 左竖条) ===== */}
          <nav aria-label="设置分类" className={cn('hidden shrink-0 transition-all duration-200 ease-smooth md:block', collapsed ? 'w-10' : 'w-44')}>
            <div className="flex flex-col justify-center min-h-[60vh] sticky top-6 border-r border-border pr-3 pt-2.5 pb-6">
              {/* 收起/展开 按钮 */}
              <button
                onClick={toggleCollapsed}
                className={cn(
                  'group relative flex items-center gap-2 text-muted transition-colors duration-150 ease-smooth mb-1',
                  collapsed ? 'ctl ctl-sm ctl-ghost justify-center px-1.5' : 'px-2.5 py-2 text-[11px]',
                  'hover:bg-foreground/[0.04] hover:text-foreground',
                )}
                title={collapsed ? '展开菜单' : '收起菜单'}
              >
                {collapsed
                  ? <PanelLeftOpen className="h-3.5 w-3.5 shrink-0" />
                  : <PanelLeftClose className="h-3.5 w-3.5 shrink-0" />
                }
                {!collapsed && <span>收起菜单</span>}
              </button>

              {/* Tab 按钮列表 — 收起时只显示图标 */}
              {TABS.map(({ key, label, icon: Icon, badge }) => (
                <button
                  key={key}
                  onClick={() => setSearchParams({ tab: key }, { replace: true })}
                  title={collapsed ? label : undefined}
                  className={cn(
                    'group relative flex items-center leading-[1.4] transition-colors duration-150 ease-smooth',
                    collapsed ? 'ctl ctl-sm ctl-ghost justify-center px-1.5' : 'ctl ctl-md ctl-ghost pl-2.5 pr-1 text-left border-l-2',
                    activeTab.key === key
                      ? 'border-accent text-accent bg-accent/[0.055]'
                      : 'border-transparent text-secondary hover:text-foreground',
                  )}
                >
                  <Icon className={cn('h-[13px] w-[13px] shrink-0 transition-colors mr-2', activeTab.key === key ? 'text-accent' : 'text-muted group-hover:text-foreground')} />
                  {!collapsed && <span>{label}</span>}
                  {!collapsed && badge && (
                    <span className="ml-auto inline-flex items-center rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-amber-400 shrink-0">
                      {badge}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </nav>

          {/* ===== Tab 内容 ===== */}
          <motion.div
            key={activeTab.key}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.15 }}
            className="min-w-0 flex-1 pl-6 pt-3.5"
          >
            <Suspense fallback={<div className="py-12 text-center text-xs text-muted">加载中…</div>}>
              {activeTab.key === 'monitoring'
                ? <SettingsMonitoringPanel highlight={highlight} />
                : <activeTab.panel highlight={highlight} />}
            </Suspense>
          </motion.div>
        </div>
      </div>
    </>
  )
}
