/**
 * 专家管理 — 分类体系 + 专家列表 (专家页与设置页共用同一份实现)。
 *
 * 一个专家 = 一个 skill: 正文在 <data_dir>/assistant/skills/<名称>/SKILL.md,
 * 分类归属与启用状态在 <data_dir>/assistant/experts.json (后端单一真相)。
 *
 * scope='all'     专家页: 全部专家, 用户专家可新建/编辑/删除, 可导入导出。
 * scope='builtin' 设置页: 只管内置专家 —— 正文只读 (改了会被下次部署覆盖),
 *                 只能配分类归属与启用状态。
 *
 * 样式走全站控件刻度 (.ctl-*): 列表行里的控件一律 24px (sm), 弹窗表单里的
 * 输入框与按钮一律 28px (md) —— 并排控件必须齐高。选中标签用「墨底反白」
 * (同 .seg), 不与主按钮的赭石实心撞脸。
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
import { Switch } from '@/components/Switch'
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

type Scope = 'all' | 'builtin'

/** 布局保存的载荷: 每个专家的分类归属 + 启用状态 (后端整份保存)。 */
type Assignments = Record<string, { category: string; enabled: boolean }>

function assignmentsOf(layout: ExpertLayout): Assignments {
  const map: Assignments = {}
  for (const item of layout.experts) {
    map[item.name] = { category: item.category, enabled: item.enabled }
  }
  return map
}

export function ExpertManager({ scope }: { scope: Scope }) {
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

  /** 当前作用域内的全部专家 (设置页只算内置专家)。 */
  const scoped = useMemo(() => {
    const all = layout?.experts ?? []
    return scope === 'builtin' ? all.filter(item => item.builtin) : all
  }, [layout, scope])

  const experts = useMemo(() => {
    if (!filter) return scoped
    return scoped.filter(item =>
      filter === UNCATEGORIZED ? !item.category : item.category === filter)
  }, [scoped, filter])

  /** 筛选标签只列出真正有专家的分类 —— 空分类点进去是空白, 反而误导。 */
  const chips = useMemo(() => {
    const used = new Set(scoped.map(item => item.category))
    const list = categories.filter(name => used.has(name))
    if (scoped.some(item => !item.category)) list.push(UNCATEGORIZED)
    return ['', ...list]
  }, [scoped, categories])

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
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-muted">分类</span>
        {chips.map(name => (
          <button
            key={name || 'all'}
            type="button"
            onClick={() => setFilter(name)}
            aria-pressed={filter === name}
            className={cn(
              BTN_SM,
              'max-w-[10rem] truncate',
              filter === name
                ? 'border-foreground bg-foreground text-surface hover:bg-foreground hover:text-surface'
                : '',
            )}
          >
            {name || '全部'}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {scope === 'all' && (
            <>
              <button
                type="button"
                onClick={() => setImportOpen(true)}
                className={cn(BTN_GHOST_SM, 'gap-1.5')}
              >
                <Upload className="h-3.5 w-3.5" />
                导入
              </button>
              <button
                type="button"
                onClick={() => void downloadPack()}
                className={cn(BTN_GHOST_SM, 'gap-1.5')}
              >
                <Download className="h-3.5 w-3.5" />
                导出
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => setCatOpen(true)}
            className={cn(BTN_GHOST_SM, 'gap-1.5')}
          >
            <FolderCog className="h-3.5 w-3.5" />
            管理分类
          </button>
          {scope === 'all' && (
            <button
              type="button"
              onClick={() => setCreating(true)}
              className={cn(btnCls('primary', 'sm'), 'gap-1.5')}
            >
              <Plus className="h-3.5 w-3.5" />
              新建专家
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-xs text-muted">加载中…</div>
      ) : experts.length === 0 ? (
        <div className="rounded-card border border-border bg-surface px-4 py-8 text-center text-xs text-muted">
          {scope === 'builtin' ? '暂无内置专家' : '还没有专家, 点「新建专家」创建第一个'}
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <label className={cn(BTN_GHOST_SM, 'cursor-pointer gap-1.5')}>
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleSelectAll}
                aria-label="全选当前列表"
              />
              全选
            </label>
            <span className="text-[11px] text-muted">已选 {selected.size} 个</span>
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                disabled={!selected.size}
                onClick={() => void batchEnable(true)}
                className={BTN_GHOST_SM}
              >
                批量启用
              </button>
              <button
                type="button"
                disabled={!selected.size}
                onClick={() => void batchEnable(false)}
                className={BTN_GHOST_SM}
              >
                批量停用
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-5">
            {categories.concat(UNCATEGORIZED).map(category => {
              const rows = experts.filter(item =>
                category === UNCATEGORIZED ? !item.category : item.category === category)
              if (!rows.length) return null
              return (
                <section key={category} className="flex flex-col gap-2">
                  <div className="flex items-baseline gap-2">
                    <h3 className="text-[13px] font-medium text-foreground">{category}</h3>
                    <span className="font-mono text-[11px] text-muted">{rows.length}</span>
                  </div>
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
        </>
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

  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-card border p-3',
        selected ? 'border-accent/60 bg-surface' : 'border-border bg-surface',
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
          <div className="flex items-start gap-1.5">
            <b
              className="line-clamp-2 min-w-0 text-[12.5px] font-medium leading-snug text-foreground"
              title={item.title || item.name}
            >
              {item.title || item.name}
            </b>
            <span
              className={cn(
                'tb-chip inline-block shrink-0',
                !item.builtin && 'border-accent/40 text-accent',
              )}
            >
              {item.builtin ? '内置' : '自定义'}
            </span>
          </div>
          <p
            className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted"
            title={item.description}
          >
            {item.description}
          </p>
        </div>
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
            <select
              aria-label={`${item.title} 的分类`}
              value={item.category}
              onChange={e => onCategory(e.target.value)}
              className={cn(selectCls('sm'), 'min-w-0 flex-1 truncate')}
            >
              <option value="">{UNCATEGORIZED}</option>
              {categories.map(name => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
            <Switch
              size="md"
              checked={item.enabled}
              onChange={onToggle}
              label={`启用专家 ${item.title}`}
            />
            {!item.builtin && (
              <>
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
              </>
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
