/**
 * 页面管理 — 设计稿 26-settings-pages 的内建页签。
 *
 * 设计稿要求: h2「页面管理」+ lead, 三行 rowset (顺序/隐藏/自动挂载),
 * 「当前目录」表 (页面/来源/子页/说明)。v2 的扩展页编辑器 (ExtPages)
 * 是设计稿没有的功能, 以「扩展页面编辑器」小节平铺在本页下方保留。
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Eye, EyeOff, Trash2 } from 'lucide-react'
import { api, type AnalysisMenu } from '@/lib/api'
import { toast } from '@/components/Toast'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { getFrontendExtensionNavigation } from '@/extensions/registry'
import { compareByDefaultNav, defaultNavIndex } from '@/lib/navOrder'
import { SettingsExtPagesPanel } from './ExtPages'

// ── 目录行的数据模型 ──────────────────────────────────

interface CatalogRow {
  id: string          // nav_order / nav_hidden 用的 key (与 Layout 的 to/解析口径一致)
  path: string        // 表格第二行展示的单色小路径
  label: string
  source: '内建' | '扩展'
  subpages: number
  desc: string
  hidden: boolean
  extensionId?: string // 源码扩展: 可物理删除
}

/** 内建页 (Layout.tsx 的 nav 常量): 说明取设计稿 26 的口径, 子页数 = 设计稿标注 */
export const BUILTIN_CATALOG: Array<Omit<CatalogRow, 'hidden'>> = [
  { id: '/',                   path: '/',                label: '看板',     source: '内建', subpages: 5, desc: '市场全景、涨跌分布、情绪雷达' },
  { id: '/watchlist',          path: '/watchlist',       label: '自选',     source: '内建', subpages: 4, desc: '自定义分组与列' },
  { id: '/screener',           path: '/screener',        label: '策略选股', source: '内建', subpages: 3, desc: '按策略条件筛选' },
  { id: '/factors',            path: '/factors',         label: '因子',     source: '内建', subpages: 3, desc: '因子目录与 IC' },
  { id: '/backtest',           path: '/backtest',        label: '回测',     source: '内建', subpages: 6, desc: '策略 / 稳健 / 滚动 / 守护 / 因子 / 挖掘' },
  { id: '/regime',             path: '/regime',          label: '市场环境', source: '内建', subpages: 2, desc: '情绪周期与主线' },
  { id: '/indices',            path: '/indices',         label: '指数',     source: '内建', subpages: 1, desc: '指数与 ETF' },
  { id: '/data',               path: '/data',            label: '数据',     source: '内建', subpages: 1, desc: '行情表状态与调度' },
  { id: '/settings',           path: '/settings',        label: '设置',     source: '内建', subpages: 10, desc: '十个设置页签' },
  { id: '/branding',           path: '/branding',        label: '品牌',     source: '内建', subpages: 1, desc: '品牌方向预览' },
  { id: '/dev',                path: '/dev',             label: '开发',     source: '内建', subpages: 1, desc: '开发工具与演示数据' },
]

const EXT_DESC: Record<string, string> = {
  '/stock-analysis':    'AI 四维分析',
  '/limit-ladder':      '涨停梯队、封板强度',
  '/concept-analysis':  '概念维度排名',
  '/industry-analysis': '行业维度排名',
  '/abnormal':          '盘中异动',
  '/paper':             '账户与持仓',
  '/lots':              '批次持仓提醒',
  '/monitor':           '条件与策略监控',
  '/review':            '盘后复述与材料',
  '/financials':        '三表与指标',
  '/signals':           '内建与自定义信号',
}

// ── 可拖拽行 (整行作为排序单元, 内容按设计稿列排版) ─────

function SortableRow({ row, index, onToggleHidden, onRemove }: {
  row: CatalogRow
  index: number
  onToggleHidden: (id: string) => void
  onRemove?: (row: CatalogRow) => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: row.id })
  return (
    <tr
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.6 : 1 }}
      className={row.hidden ? 'opacity-50' : undefined}
    >
      <td>
        <span className="inline-flex min-w-0 items-center gap-0 align-top">
          <button
            {...attributes}
            {...listeners}
            aria-label={`拖动调整 ${row.label} 顺序`}
            className="cursor-grab select-none px-1 font-mono text-[9px] text-[var(--ink-3)] hover:text-[var(--ink)] active:cursor-grabbing"
            title="拖动排序"
          >
            ⠿
          </button>
          <span className="min-w-0">
            <span className="font-mono text-[9px] text-[var(--ink-3)]">{String(index + 1).padStart(2, '0')}</span>
            <span className={row.hidden ? 'line-through' : undefined}>{row.label}</span>
            <span className="block pl-0 font-mono text-[9px] text-[var(--ink-3)]">{row.path}</span>
          </span>
        </span>
      </td>
      <td style={{ textAlign: 'left' }}>{row.source}</td>
      <td className="num">{row.subpages}</td>
      <td style={{ textAlign: 'left' }}>
        <span className="inline-flex items-center gap-1.5">
          {row.desc}
          {/* 行内动作 (v2 保留): 显隐 + 删除; 设计稿语义由 rowset 行承载 */}
          <button
            onClick={() => onToggleHidden(row.id)}
            className="rounded p-0.5 text-[var(--ink-3)] transition-colors hover:bg-accent/10 hover:text-accent"
            title={row.hidden ? '在左侧目录显示' : '从左侧目录隐藏'}
          >
            {row.hidden ? <EyeOff className="h-[12px] w-[12px]" /> : <Eye className="h-[12px] w-[12px]" />}
          </button>
          {onRemove && (
            <button
              onClick={() => onRemove(row)}
              className="ctl ctl-sm ctl-ghost ctl-icon text-[var(--ink-3)] hover:bg-danger/10 hover:text-danger"
              title={`删除源码扩展页 ${row.path} (自动备份)`}
            >
              <Trash2 className="h-[12px] w-[12px]" />
            </button>
          )}
        </span>
      </td>
    </tr>
  )
}

// ── 主面板 ────────────────────────────────────────────

export function SettingsPageAdminPanel() {
  const qc = useQueryClient()
  const { data: prefs } = usePreferences()
  const menus = useQuery({ queryKey: QK.analysisMenus, queryFn: api.analysisMenus })

  // 目录全集 = 内建 + 自定义分析页 (analysisMenus) + 源码扩展 (registry)
  const catalog = useMemo<CatalogRow[]>(() => {
    const hidden = new Set(prefs?.nav_hidden ?? [])
    const rows: CatalogRow[] = BUILTIN_CATALOG.map(r => ({ ...r, hidden: hidden.has(r.id) }))
    const menuItems: AnalysisMenu[] = menus.data?.items ?? []
    for (const m of menuItems) {
      rows.push({
        id: m.id,
        path: `/analysis/${m.id}`,
        label: m.label,
        source: '扩展',
        subpages: 1,
        desc: m.template === 'url' ? '网页嵌入页' : '扩展数据源分析页',
        hidden: hidden.has(m.id),
      })
    }
    for (const item of getFrontendExtensionNavigation()) {
      rows.push({
        id: item.route.path,
        path: item.route.path,
        label: item.label,
        source: '扩展',
        subpages: 1,
        desc: EXT_DESC[item.route.path] ?? '源码扩展页',
        hidden: hidden.has(item.route.path),
        extensionId: item.extensionId,
      })
    }
    // 排序口径与 Layout 一致: nav_order 优先, 未保存按默认基准
    const order = prefs?.nav_order ?? []
    if (order.length === 0) {
      return rows.sort((a, b) => compareByDefaultNav(a.id, b.id))
    }
    const byId = new Map(rows.map(r => [r.id, r]))
    const out: CatalogRow[] = []
    const seen = new Set<string>()
    for (const id of order) {
      const r = byId.get(id) ?? byId.get(`/analysis/${id}`)
      if (r) { out.push(r); seen.add(r.id) }
    }
    for (const r of rows) {
      if (seen.has(r.id)) continue
      const di = defaultNavIndex(r.id)
      let anchor = -1
      if (Number.isFinite(di) && di > 0) {
        for (let i = di - 1; i >= 0 && anchor < 0; i -= 1) {
          anchor = out.findIndex(o => defaultNavIndex(o.id) === i)
        }
      }
      if (anchor >= 0) out.splice(anchor + 1, 0, r)
      else if (di === 0) out.unshift(r)
      else out.push(r)
    }
    return out
  }, [prefs?.nav_order, prefs?.nav_hidden, menus.data])

  const extCount = catalog.filter(r => r.source === '扩展').length
  const builtinCount = catalog.length - extCount

  const saveNavOrder = useMutation({
    mutationFn: (order: string[]) => api.saveNavOrder(order),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.preferences }),
  })
  const saveNavHidden = useMutation({
    mutationFn: (hidden: string[]) => api.saveNavHidden(hidden),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.preferences }),
  })

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const ids = catalog.map(r => r.id)
    const reordered = arrayMove(ids, ids.indexOf(active.id as string), ids.indexOf(over.id as string))
    saveNavOrder.mutate(reordered)
  }

  const toggleHidden = (id: string) => {
    const next = new Set(prefs?.nav_hidden ?? [])
    if (next.has(id)) next.delete(id)
    else next.add(id)
    saveNavHidden.mutate([...next])
  }

  // 「隐藏已停用的页面」总开关: off = 全部显示
  const allHidden = catalog.length > 0 && catalog.every(r => r.hidden)
  const toggleHideAll = () => {
    saveNavHidden.mutate(allHidden ? [] : catalog.map(r => r.id))
  }

  // 「扩展页自动挂载」开关: off = 隐藏全部扩展页; on = 从隐藏列表移除扩展页
  const extPaths = catalog.filter(r => r.source === '扩展').map(r => r.id)
  const extAllHidden = extPaths.length > 0 && extPaths.every(p => (prefs?.nav_hidden ?? []).includes(p))
  const toggleExtMount = () => {
    const current = prefs?.nav_hidden ?? []
    if (extAllHidden) {
      saveNavHidden.mutate(current.filter(p => !extPaths.includes(p)))
    } else {
      const next = new Set(current)
      for (const p of extPaths) next.add(p)
      saveNavHidden.mutate([...next])
    }
  }

  // 恢复默认: 清空自定义排序, 隐藏列表回到仅扩展域种子口径之外的全显示
  const resetOrder = () => {
    saveNavOrder.mutate([])
    saveNavHidden.mutate([])
    toast('已恢复默认目录', 'success')
  }

  // 删除源码扩展页 (双重确认, 与原 MenuSettings 行为一致)
  const [deletedBackup, setDeletedBackup] = useState<{ ns: string; backup: string } | null>(null)
  const deleteExtension = useMutation({
    mutationFn: (ns: string) => api.pageAdminDeleteSourceExtension(ns),
    onSuccess: (r) => {
      setDeletedBackup({ ns: r.deleted, backup: r.backup })
      toast(`已删除 ${r.deleted} (已备份), 刷新页面后从菜单与路由消失`, 'success')
    },
    onError: (e: Error) => toast(e.message || '删除失败', 'error'),
  })
  const handleRemoveExtension = (row: CatalogRow) => {
    const ns = row.extensionId
    if (!ns) return
    if (!confirm(`删除源码扩展页「${row.label}」?\n\n将物理删除 frontend/src/custom/${ns}/ 目录, 刷新后该页面从菜单和路由中消失。`)) return
    if (!confirm(`二次确认: 删除前会自动备份为 zip (可下载恢复)。确定删除 ${ns}?`)) return
    deleteExtension.mutate(ns)
  }

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor),
  )

  return (
    <div className="max-w-5xl">
      <h2 className="font-serif text-[16px] font-medium tracking-[0.02em] text-foreground">页面管理</h2>
      <p className="mt-1 max-w-[64ch] text-[11px] leading-[1.75] text-secondary">
        左侧菜单由内建页面与已安装扩展共同组成。这里调整顺序、隐藏不需要的页；扩展页随插件安装出现或消失，不会被这里的手动设置覆盖。
      </p>

      {/* 三行设置项 (设计稿 26 rowset) */}
      <div className="rowset mt-3">
        <div className="r">
          <div className="lab">
            <b>调整左侧菜单顺序</b>
            <small>拖动条目改变顺序，与左侧目录实时同步；内建页与扩展页可混排</small>
          </div>
          <div className="ctl">
            <button className="ctl-btn ctl-sm" onClick={resetOrder}>恢复默认</button>
          </div>
        </div>
        <div className="r">
          <div className="lab">
            <b>隐藏已停用的页面</b>
            <small>被隐藏的页仍可直接访问，只是不出现在目录里</small>
          </div>
          <div className="ctl">
            <button
              type="button"
              role="switch"
              aria-checked={!allHidden}
              className={`sw ${!allHidden ? 'on' : ''}`}
              onClick={toggleHideAll}
              disabled={saveNavHidden.isPending}
            ><i /></button>
          </div>
        </div>
        <div className="r">
          <div className="lab">
            <b>扩展页自动挂载</b>
            <small>安装扩展后新页面自动加入对应分组；卸载时一并移除</small>
          </div>
          <div className="ctl">
            <button
              type="button"
              role="switch"
              aria-checked={!extAllHidden}
              className={`sw ${!extAllHidden ? 'on' : ''}`}
              onClick={toggleExtMount}
              disabled={saveNavHidden.isPending}
            ><i /></button>
          </div>
        </div>
      </div>

      {/* 当前目录表 (设计稿 26 .dt) */}
      <div className="figcap mt-6 border-b border-[var(--rule-strong)] pb-[5px] text-[9.5px] tracking-[0.14em]">
        当 前 目 录 ｜ {catalog.length} 页 · 内 建 {builtinCount} · 扩 展 {extCount}
      </div>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={catalog.map(r => r.id)} strategy={verticalListSortingStrategy}>
          <table className="dt">
            <colgroup><col style={{ width: 190 }} /><col style={{ width: 64 }} /><col style={{ width: 56 }} /><col /></colgroup>
            <thead>
              <tr><th>页面</th><th style={{ textAlign: 'left' }}>来源</th><th>子页</th><th style={{ textAlign: 'left' }}>说明</th></tr>
            </thead>
            <tbody>
              {catalog.map((r, i) => (
                <SortableRow
                  key={r.id}
                  row={r}
                  index={i}
                  onToggleHidden={toggleHidden}
                  onRemove={r.extensionId ? handleRemoveExtension : undefined}
                />
              ))}
            </tbody>
          </table>
        </SortableContext>
      </DndContext>
      {menus.isLoading && (
        <div className="py-8 text-center text-sm text-muted">正在加载目录…</div>
      )}

      {deletedBackup && (
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border border-border bg-elevated/40 p-4 text-sm text-secondary">
          <span>
            已删除源码扩展页 <span className="font-mono text-foreground">{deletedBackup.ns}</span>，刷新页面后生效。
          </span>
          <a
            href={api.pageAdminBackupUrl(deletedBackup.backup)}
            download
            className="border border-border px-2 py-1 text-xs text-accent transition-colors hover:bg-accent/10"
          >
            下载备份 {deletedBackup.backup}
          </a>
        </div>
      )}

      {/* 扩展页面编辑器 — v2 保留功能 (设计稿没有, 平铺在此页下方) */}
      <div className="sec-h mt-8">扩 展 页 面 编 辑 器 ｜ 把 扩 展 数 据 配 置 成 左 侧 分 析 菜 单</div>
      <SettingsExtPagesPanel />
    </div>
  )
}
