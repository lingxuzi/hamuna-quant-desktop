/**
 * 专家管理 — 分类体系 + 专家名录 (专家页唯一入口)。
 *
 * 一个专家 = 一个 skill: 正文在 <data_dir>/assistant/skills/<名称>/SKILL.md,
 * 分类归属与启用状态在 <data_dir>/assistant/experts.json (后端单一真相)。
 *
 * 版式: 卷首下一条工具条 (左: 分类筛选 + 批量选择, 右: 页内动作), 往下按分类
 * 分节, 每节一排专家卡。分类只出现一次 —— 筛选下拉给「看哪一类」, 分节题头给
 * 「这一组是谁」; 从前顶部页签与分节标题摆的是同一套分类, 读两遍。
 *
 * 样式走全站控件刻度 (.ctl-*): 列表行里的控件一律 24px (sm), 弹窗表单里的
 * 输入框与按钮一律 28px (md) —— 并排控件必须齐高。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  DndContext, KeyboardSensor, PointerSensor, closestCenter,
  useSensor, useSensors, type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext, arrayMove, sortableKeyboardCoordinates,
  useSortable, verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Download, FolderCog, GripVertical, Pencil, Plus, Trash2, Upload } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { EmptyState } from '@/components/EmptyState'
import { SectionHead } from '@/components/SectionHead'
import { Switch } from '@/components/Switch'
import { Toolbar } from '@/components/Toolbar'
import {
  BTN_GHOST_MD, BTN_GHOST_SM, BTN_SM, ICON_BTN_SM, TEXTAREA_MD, btnCls, inputCls, selectCls,
} from '@/components/controls'
import { cn } from '@/lib/cn'
import {
  createExpert,
  deleteExpert,
  exportExperts,
  fetchExpert,
  fetchExperts,
  importExperts,
  saveExpertLayout,
  updateExpert,
  type ExpertImportReport,
  type ExpertItem,
  type ExpertLayout,
} from '../client'

const UNCATEGORIZED = '未分类'

/** 筛选用的保留名: 「全部」是不筛选, 「未分类」是没有归属的专家 —— 分类不能叫这个。 */
const RESERVED_CATEGORIES = ['全部', UNCATEGORIZED]

/**
 * 卡片上给用户看的那一句描述。
 *
 * 内置专家的 description 是写给模型路由用的元数据: 开头常把标题重抄一遍, 末尾
 * 还挂着「触发词: …」清单, 中间偶尔带 Markdown 记号。这里只做显示层的归一 ——
 * 后端那一份不动 (它要喂给模型), 但界面上不该出现重复与路由黑话。
 */
function cardDescription(item: ExpertItem): string {
  let text = (item.description ?? '').replace(/\*\*/g, '').replace(/Skill/g, '专家')
  const title = (item.title || item.name).trim()
  if (title && text.startsWith(title)) text = text.slice(title.length)
  text = text.replace(/^[（(][^）)]*[）)]\s*/, '')   // 标题的括号注解
  text = text.replace(/^[。、,，:：—–\-·\s]+/, '')     // 分隔符
  text = text.replace(/^(?:智能体|框架|技能|系统|助手)(?:[。.、,，:：]\s*)?/, '')
  text = text.replace(/^是(?:一个|一种|一套)\s*/, '')
  const cut = text.indexOf('触发词：')
  if (cut > 0) text = text.slice(0, cut)
  return text.trim().replace(/。$/, '')
}

/** 布局保存的载荷: 每个专家的分类归属 + 启用状态 (后端整份保存)。 */
type Assignments = Record<string, { category: string; enabled: boolean }>

function assignmentsOf(layout: ExpertLayout): Assignments {
  const map: Assignments = {}
  for (const item of layout.experts) {
    map[item.name] = { category: item.category, enabled: item.enabled }
  }
  return map
}

export function ExpertManager() {
  const [layout, setLayout] = useState<ExpertLayout | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [catOpen, setCatOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [editing, setEditing] = useState<ExpertItem | null>(null)
  const [creating, setCreating] = useState(false)
  /** 批量操作选中项 —— 专家名集合 (跨分类筛选保留选中)。 */
  const [selected, setSelected] = useState<Set<string>>(() => new Set())

  const reload = useCallback(async () => {
    try {
      setLayout(await fetchExperts())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '专家列表加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  const categories = useMemo(() => layout?.categories ?? [], [layout])

  const allExperts = useMemo(() => layout?.experts ?? [], [layout])

  const experts = useMemo(() => {
    if (!filter) return allExperts
    return allExperts.filter(item =>
      filter === UNCATEGORIZED ? !item.category : item.category === filter)
  }, [allExperts, filter])

  /** 筛选下拉: 只列真正有专家的分类 (空分类点进去是空白, 反而误导), 带个数。 */
  const filterOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of allExperts) {
      const key = item.category || UNCATEGORIZED
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    const list = categories
      .filter(name => counts.has(name))
      .map(name => ({ value: name, label: `${name} (${counts.get(name)})` }))
    if (counts.has(UNCATEGORIZED)) {
      list.push({
        value: UNCATEGORIZED,
        label: `${UNCATEGORIZED} (${counts.get(UNCATEGORIZED)})`,
      })
    }
    return [{ value: '', label: `全部 (${allExperts.length})` }, ...list]
  }, [allExperts, categories])

  /** 分类归属/启用状态 —— 整份保存 (后端是唯一真相, 前端不另存一份)。 */
  const patchLayout = async (name: string, patch: { category?: string; enabled?: boolean }) => {
    if (!layout) return
    const assignments = assignmentsOf(layout)
    const target = assignments[name]
    if (!target) return
    if (patch.category !== undefined) target.category = patch.category
    if (patch.enabled !== undefined) target.enabled = patch.enabled
    const previousItem = layout.experts.find(item => item.name === name)
    // 乐观更新: 连点两个专家时不会用旧状态把前一次改动覆盖掉
    setLayout({
      ...layout,
      experts: layout.experts.map(item => item.name === name
        ? {
            ...item,
            ...(patch.category !== undefined ? { category: patch.category } : {}),
            ...(patch.enabled !== undefined ? { enabled: patch.enabled } : {}),
          }
        : item),
    })
    try {
      await saveExpertLayout(layout.categories, assignments)
      setError('')
    } catch (e) {
      // 只回滚这一个专家, 别把同时进行的另一次改动也退掉
      setLayout(current => current && previousItem
        ? {
            ...current,
            experts: current.experts.map(item => item.name === name ? previousItem : item),
          }
        : current)
      setError(e instanceof Error ? e.message : '保存失败')
    }
  }

  /** 批量启用/停用 —— 一次整份保存, 不逐个专家发请求。 */
  const batchEnable = async (enabled: boolean) => {
    if (!layout || !selected.size) return
    const assignments = assignmentsOf(layout)
    let changed = false
    selected.forEach(name => {
      const target = assignments[name]
      if (target && target.enabled !== enabled) {
        target.enabled = enabled
        changed = true
      }
    })
    if (!changed) return
    try {
      setLayout(await saveExpertLayout(layout.categories, assignments))
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '批量设置失败')
    }
  }

  const toggleSelect = (name: string) => {
    setSelected(current => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  /** 全选 = 当前列表全选/全不选 (只看得到的那批, 跨分类的选中保留)。 */
  const toggleSelectAll = () => {
    setSelected(current => {
      const next = new Set(current)
      const all = experts.length > 0 && experts.every(item => next.has(item.name))
      experts.forEach(item => { if (all) next.delete(item.name); else next.add(item.name) })
      return next
    })
  }

  const saveCategories = async (next: string[], renamed: Record<string, string>) => {
    if (!layout) return
    const assignments = assignmentsOf(layout)
    for (const item of layout.experts) {
      const moved = renamed[item.category]
      if (moved) assignments[item.name].category = moved
    }
    try {
      setLayout(await saveExpertLayout(next, assignments))
      setFilter('')
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '分类保存失败')
    }
  }

  const remove = async (name: string) => {
    try {
      setLayout(await deleteExpert(name))
      setSelected(current => {
        const next = new Set(current)
        next.delete(name)
        return next
      })
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    }
  }

  const downloadPack = async () => {
    try {
      const { filename, blob } = await exportExperts()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '导出失败')
    }
  }

  const saveExpert = async (draft: {
    existing?: string
    name: string
    description: string
    content: string
    category: string
  }) => {
    try {
      if (!draft.existing) {
        setLayout(await createExpert({
          name: draft.name,
          description: draft.description,
          content: draft.content,
          category: draft.category,
        }))
      } else {
        const next = await updateExpert(draft.existing, {
          description: draft.description,
          content: draft.content,
        })
        // 分类归属在 layout 接口 (与启用状态同一份配置), 变了才补一次保存
        const before = layout?.experts.find(item => item.name === draft.existing)?.category ?? ''
        if (before !== draft.category) {
          const assignments = assignmentsOf(next)
          if (assignments[draft.existing]) assignments[draft.existing].category = draft.category
          setLayout(await saveExpertLayout(next.categories, assignments))
        } else {
          setLayout(next)
        }
      }
      setEditing(null)
      setCreating(false)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    }
  }

  const allVisibleSelected = experts.length > 0 && experts.every(item => selected.has(item.name))

  return (
    <div className="flex flex-col gap-3">
      {/* 工具条: 左 = 分类筛选 + 批量选择, 右 = 页内动作。走共享 Toolbar 件,
          窄屏折行由 .toolbar 的窄屏规则管 (≤720px 两组各占一行)。 */}
      <Toolbar
        left={
          <>
            <select
              id="expert-filter"
              aria-label="按分类筛选专家"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              className={cn(selectCls('sm'), 'min-w-0 max-w-[12rem]')}
            >
              {filterOptions.map(option => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-secondary">
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleSelectAll}
                aria-label="全选当前列表"
              />
              全选
            </label>
            {/* 批量动作只在有选中时出现 —— 空按钮是噪音, 不是功能。
                数量写进按钮文案, 不另起一行读注。 */}
            {selected.size > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => void batchEnable(true)}
                  className={BTN_SM}
                >
                  启用 {selected.size} 个
                </button>
                <button
                  type="button"
                  onClick={() => void batchEnable(false)}
                  className={BTN_SM}
                >
                  停用 {selected.size} 个
                </button>
              </>
            )}
          </>
        }
        right={
          <>
            <button
              type="button"
              onClick={() => setImportOpen(true)}
              className={BTN_GHOST_SM}
            >
              <Upload className="h-3.5 w-3.5" />
              导入
            </button>
            <button
              type="button"
              onClick={() => void downloadPack()}
              className={BTN_GHOST_SM}
            >
              <Download className="h-3.5 w-3.5" />
              导出
            </button>
            <button
              type="button"
              onClick={() => setCatOpen(true)}
              className={BTN_GHOST_SM}
            >
              <FolderCog className="h-3.5 w-3.5" />
              管理分类
            </button>
            <button
              type="button"
              onClick={() => setCreating(true)}
              className={btnCls('primary', 'sm')}
            >
              <Plus className="h-3.5 w-3.5" />
              新建专家
            </button>
          </>
        }
      />

      {error && (
        <div className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-xs text-muted">加载中…</div>
      ) : experts.length === 0 ? (
        filter ? (
          <EmptyState
            eyebrow="NO EXPERT"
            title="这个分类下还没有专家"
            hint="换个分类看看, 或新建一个专家放进来"
          />
        ) : (
          <EmptyState
            eyebrow="NO EXPERT"
            title="还没有专家"
            hint="新建一个专家, 之后在对话里 @ 就能引用它"
            action={
              <button
                type="button"
                onClick={() => setCreating(true)}
                className={cn(btnCls('primary', 'md'), 'gap-1.5')}
              >
                <Plus className="h-3.5 w-3.5" />
                新建专家
              </button>
            }
          />
        )
      ) : (
        <div className="flex flex-col gap-5">
          {categories.concat(UNCATEGORIZED).map(category => {
            const rows = experts.filter(item =>
              category === UNCATEGORIZED ? !item.category : item.category === category)
            if (!rows.length) return null
            return (
              <section key={category} className="flex flex-col gap-3">
                <SectionHead
                  className="border-b border-border pb-2"
                  title={category}
                  right={
                    <span className="font-mono text-[11px] text-muted">{rows.length}</span>
                  }
                />
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {rows.map(item => (
                    <ExpertCard
                      key={item.name}
                      item={item}
                      selected={selected.has(item.name)}
                      categories={categories}
                      onToggleSelect={() => toggleSelect(item.name)}
                      onToggle={enabled => void patchLayout(item.name, { enabled })}
                      onCategory={category => void patchLayout(item.name, { category })}
                      onEdit={() => setEditing(item)}
                      onDelete={() => void remove(item.name)}
                    />
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      )}

      {catOpen && layout && (
        <CategoryDialog
          categories={categories}
          onClose={() => setCatOpen(false)}
          onSave={(next, renamed) => { setCatOpen(false); void saveCategories(next, renamed) }}
        />
      )}

      {importOpen && (
        <ImportDialog
          onClose={() => setImportOpen(false)}
          onImported={next => setLayout(next)}
        />
      )}

      {(creating || editing) && (
        <ExpertDialog
          expert={editing}
          categories={categories}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSubmit={draft => void saveExpert(draft)}
        />
      )}
    </div>
  )
}

function ExpertCard({
  item,
  selected,
  categories,
  onToggleSelect,
  onToggle,
  onCategory,
  onEdit,
  onDelete,
}: {
  item: ExpertItem
  selected: boolean
  categories: string[]
  onToggleSelect: () => void
  onToggle: (enabled: boolean) => void
  onCategory: (category: string) => void
  onEdit: () => void
  onDelete: () => void
}) {
  // 二次确认: 删除会连带正文一起没了, 按一次只把按钮换成「确认删除」
  const [confirming, setConfirming] = useState(false)
  const description = cardDescription(item)

  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-card border bg-surface p-3',
        // 选中态是一个来回动作, 描边换色太轻, 补一点缩放让手上有反馈。
        'transition-[border-color,transform] duration-200 ease-smooth',
        selected ? 'border-accent scale-[1.01]' : 'border-border',
      )}
    >
      <div className="flex items-start gap-2">
        <label className="flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggleSelect}
            aria-label={`选择专家 ${item.title}`}
          />
        </label>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <b
              className={cn(
                'line-clamp-2 min-w-0 text-sm font-medium leading-snug',
                item.enabled ? 'text-foreground' : 'text-secondary',
              )}
              title={item.title || item.name}
            >
              {item.title || item.name}
            </b>
            {/* 只有自定义专家挂签 —— 内置专家张张都写「内置」, 等于没写。
                签用弱化文字: 赭石留给主操作, 等宽状态签是给拉丁定长词设计的。 */}
            {!item.builtin && (
              <span className="shrink-0 text-[11px] text-muted">自定义</span>
            )}
          </div>
          {description && (
            <p
              className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted"
              title={description}
            >
              {description}
            </p>
          )}
        </div>
        {/* 启停是这张卡的主动作, 提到题名同层 —— 放脚注里只剩一个方块, 认不出是开关。 */}
        <Switch
          checked={item.enabled}
          onChange={onToggle}
          label={`启用专家 ${item.title}`}
        />
      </div>

      {/* 控件行贴卡片底部 (mt-auto): 描述长短不一时, 同行卡片的控件仍齐平。 */}
      <div className="mt-auto flex items-center gap-2 border-t border-border pt-2">
        {confirming ? (
          <div className="flex w-full items-center gap-2">
            <button
              type="button"
              onClick={onDelete}
              className={cn(BTN_SM, 'danger flex-1 shrink-0')}
            >
              确认删除
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className={cn(BTN_GHOST_SM, 'flex-1 shrink-0')}
            >
              取消
            </button>
          </div>
        ) : (
          <>
            {/* 分类下拉只报动作, 不报现状 —— 卡片所在分节已经说了这一组是谁,
                每张卡再抄一遍分类名, 37 张卡就是 37 次重复。
                「未分类」是保留名 (分类管理里禁建), 拿来当「移到未分类」的取值。 */}
            <select
              aria-label={`把 ${item.title} 移到其他分类`}
              value={''}
              onChange={e => {
                if (!e.target.value) return
                onCategory(e.target.value === UNCATEGORIZED ? '' : e.target.value)
              }}
              className={cn(selectCls('sm'), 'min-w-0 max-w-[11rem]')}
            >
              <option value={''}>移动到分类</option>
              <option value={UNCATEGORIZED}>{UNCATEGORIZED}</option>
              {categories.map(name => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
            {!item.builtin && (
              <div className="ml-auto flex items-center gap-2">
                <button
                  type="button"
                  onClick={onEdit}
                  className={cn(ICON_BTN_SM, 'shrink-0')}
                  title="编辑专家"
                  aria-label={`编辑专家 ${item.title}`}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(true)}
                  className={cn(ICON_BTN_SM, 'shrink-0 hover:bg-danger/10 hover:text-danger')}
                  title="删除专家"
                  aria-label={`删除专家 ${item.title}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/** 分类管理: 新增/改名/删除 + 拖拽排序。改名会把该分类下的专家一并带走。 */
function CategoryDialog({
  categories,
  onClose,
  onSave,
}: {
  categories: string[]
  onClose: () => void
  onSave: (next: string[], renamed: Record<string, string>) => void
}) {
  const [rows, setRows] = useState<CategoryRow[]>(
    categories.map(name => ({ id: name, name, original: name })),
  )
  const [input, setInput] = useState('')
  const [err, setErr] = useState('')

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const ids = rows.map(row => row.id)
    const from = ids.indexOf(String(active.id))
    const to = ids.indexOf(String(over.id))
    if (from < 0 || to < 0) return
    setRows(arrayMove(rows, from, to))
  }

  const add = () => {
    const name = input.trim()
    if (!name) return
    if (rows.some(row => row.name === name)) {
      setErr('分类名重复了')
      return
    }
    if (RESERVED_CATEGORIES.includes(name)) {
      setErr(`「${name}」是筛选用的保留名, 不能当分类名`)
      return
    }
    setRows([...rows, { id: newRowId(), name, original: '' }])
    setInput('')
    setErr('')
  }

  const submit = () => {
    const hit = rows.map(row => row.name.trim()).find(name => RESERVED_CATEGORIES.includes(name))
    if (hit) {
      setErr(`「${hit}」是筛选用的保留名, 不能当分类名`)
      return
    }
    const next = rows.map(row => row.name.trim()).filter(Boolean)
    const renamed: Record<string, string> = {}
    for (const row of rows) {
      const name = row.name.trim()
      if (row.original && name && name !== row.original) renamed[row.original] = name
    }
    onSave(next, renamed)
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="expert-category-title"
      panelClassName="w-[92vw] max-w-lg bg-surface border border-border rounded-card shadow-xl"
    >
      <div className="flex flex-col gap-4 p-5">
        <h2 id="expert-category-title" className="text-sm font-medium text-foreground">专家分类</h2>
        <p className="-mt-2 text-[11px] text-muted">拖动左侧手柄调整分类顺序</p>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={rows.map(row => row.id)} strategy={verticalListSortingStrategy}>
            <div className="flex flex-col gap-2">
              {rows.map((row, idx) => (
                <SortableCategoryRow
                  key={row.id}
                  row={row}
                  index={idx}
                  onChange={name => setRows(rows.map((r, i) => (i === idx ? { ...r, name } : r)))}
                  onRemove={() => setRows(rows.filter((_, i) => i !== idx))}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
        {rows.length === 0 && <div className="text-xs text-muted">还没有分类</div>}
        <div className="flex items-center gap-2">
          <input
            aria-label="新分类名称"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
            placeholder="新分类名称"
            className={cn(inputCls('md'), 'flex-1')}
          />
          <button type="button" onClick={add} className={cn(BTN_GHOST_MD, 'shrink-0')}>添加</button>
        </div>
        {err && <div className="text-[11px] text-danger">{err}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={BTN_GHOST_MD}>取消</button>
          <button type="button" onClick={submit} className={cn(btnCls('primary', 'md'), 'px-3.5')}>
            保存
          </button>
        </div>
      </div>
    </Modal>
  )
}

type CategoryRow = { id: string; name: string; original: string }

let rowSeq = 0
/** 新增分类的行没有原名, 给个稳定的 id 当拖拽排序的锚。 */
function newRowId(): string {
  rowSeq += 1
  return `new-${rowSeq}`
}

/** 可拖拽的分类行 —— 手柄 + 名称 + 删除, 三者同高 (28px)。 */
function SortableCategoryRow({
  row,
  index,
  onChange,
  onRemove,
}: {
  row: CategoryRow
  index: number
  onChange: (name: string) => void
  onRemove: () => void
}) {
  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id: row.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  }

  return (
    <div ref={setNodeRef} style={style} className="flex items-center gap-2">
      <button
        type="button"
        {...attributes}
        {...listeners}
        className="flex h-7 w-7 shrink-0 cursor-grab items-center justify-center text-muted transition-colors hover:text-foreground active:cursor-grabbing"
        aria-label={`拖动排序 ${row.name || `第 ${index + 1} 个分类`}`}
      >
        <GripVertical className="h-4 w-4" />
      </button>
      <input
        aria-label={`分类 ${index + 1}`}
        value={row.name}
        onChange={e => onChange(e.target.value)}
        className={cn(inputCls('md'), 'flex-1')}
      />
      <button
        type="button"
        onClick={onRemove}
        className={cn(BTN_GHOST_MD, 'shrink-0 hover:text-danger')}
        aria-label={`删除分类 ${row.name || index + 1}`}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

/** 导入专家包: 选文件 → 可选覆盖同名 → 导入, 结果给一份处置报告。 */
function ImportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: (layout: ExpertLayout) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [overwrite, setOverwrite] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [report, setReport] = useState<ExpertImportReport | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const run = async () => {
    if (!file) return
    setBusy(true)
    setErr('')
    try {
      const body = await importExperts(file, overwrite)
      setReport(body.report)
      onImported(body)
    } catch (e) {
      setErr(e instanceof Error ? e.message : '导入失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="expert-import-title"
      panelClassName="w-[92vw] max-w-lg bg-surface border border-border rounded-card shadow-xl"
    >
      <div className="flex flex-col gap-4 p-5">
        <h2 id="expert-import-title" className="text-sm font-medium text-foreground">导入专家包</h2>
        <p className="text-[11px] text-muted">
          选择此前导出的专家包文件。内置专家随程序分发, 不会出现在包里。
        </p>
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            aria-label="选择专家包文件"
            onChange={e => setFile(e.target.files?.[0] ?? null)}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className={cn(BTN_GHOST_MD, 'shrink-0')}
          >
            选择文件
          </button>
          <span className="min-w-0 flex-1 truncate text-[11px] text-muted">
            {file ? file.name : '还没选文件'}
          </span>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-[11px] text-secondary">
          <input
            type="checkbox"
            checked={overwrite}
            onChange={e => setOverwrite(e.target.checked)}
          />
          用包内版本替换本机同名专家
        </label>
        {err && <div className="text-[11px] text-danger">{err}</div>}
        {report && (
          <div className="rounded-card border border-border bg-elevated/40 px-3 py-2 text-[11px] text-secondary">
            <div>
              导入 {report.imported.length} 个 · 覆盖 {report.updated.length} 个 ·
              跳过 {report.skipped.length} 个
            </div>
            {report.categories_added.length > 0 && (
              <div className="mt-1">新增分类: {report.categories_added.join('、')}</div>
            )}
            {report.skipped.length > 0 && (
              <div className="mt-1 break-all">跳过: {report.skipped.join('、')}</div>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={BTN_GHOST_MD}>
            {report ? '完成' : '取消'}
          </button>
          {!report && (
            <button
              type="button"
              disabled={!file || busy}
              onClick={() => void run()}
              className={cn(btnCls('primary', 'md'), 'px-3.5 disabled:opacity-40')}
            >
              {busy ? '导入中…' : '导入'}
            </button>
          )}
        </div>
      </div>
    </Modal>
  )
}

/** 专家编辑: 名称/用途/分类/正文。内置专家只读 —— 由调用方不提供编辑入口。 */
function ExpertDialog({
  expert,
  categories,
  onClose,
  onSubmit,
}: {
  expert: ExpertItem | null
  categories: string[]
  onClose: () => void
  onSubmit: (draft: { existing?: string; name: string; description: string; content: string; category: string }) => void
}) {
  const [name, setName] = useState(expert?.name ?? '')
  const [description, setDescription] = useState(expert?.description ?? '')
  const [category, setCategory] = useState(expert?.category ?? '')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(!!expert)

  useEffect(() => {
    if (!expert) return
    let alive = true
    void fetchExpert(expert.name)
      .then(detail => {
        if (!alive) return
        setDescription(detail.description)
        setContent(detail.content)
        setCategory(detail.category)
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [expert])

  const canSave = name.trim().length > 0 && description.trim().length > 0 && !loading

  return (
    <Modal
      onClose={onClose}
      labelledBy="expert-editor-title"
      panelClassName="w-[92vw] max-w-2xl bg-surface border border-border rounded-card shadow-xl"
    >
      <div className="flex flex-col gap-4 p-5">
        <h2 id="expert-editor-title" className="text-sm font-medium text-foreground">
          {expert ? `编辑专家 · ${expert.title}` : '新建专家'}
        </h2>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-muted">名称</span>
          <input
            value={name}
            disabled={!!expert}
            onChange={e => setName(e.target.value)}
            placeholder="给专家起个名字"
            className={cn(inputCls('md'), expert && 'opacity-60')}
          />
          {!expert && (
            <span className="text-[11px] text-muted">
              只能用英文字母、数字、点、下划线和短横线; 界面显示名取正文开头的一级标题
            </span>
          )}
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-muted">用途</span>
          <input
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="什么情况下该用它"
            className={inputCls('md')}
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-muted">分类</span>
          <select value={category} onChange={e => setCategory(e.target.value)} className={selectCls('md')}>
            <option value="">{UNCATEGORIZED}</option>
            {categories.map(item => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs text-muted">正文</span>
          <textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            rows={12}
            placeholder="专家的工作流程与要点"
            className={cn(TEXTAREA_MD, 'font-mono text-xs leading-relaxed')}
          />
        </label>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className={BTN_GHOST_MD}>取消</button>
          <button
            type="button"
            disabled={!canSave}
            onClick={() => onSubmit({
              existing: expert?.name,
              name: name.trim(),
              description: description.trim(),
              content,
              category,
            })}
            className={cn(btnCls('primary', 'md'), 'px-3.5 disabled:opacity-40')}
          >
            保存
          </button>
        </div>
      </div>
    </Modal>
  )
}
