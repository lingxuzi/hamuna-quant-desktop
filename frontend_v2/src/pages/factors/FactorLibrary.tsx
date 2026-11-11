import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, FlaskConical, PenLine, Search, Sparkles, Trash2, X } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { toast } from '@/components/Toast'
import { api, type FactorLibraryItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { GenerateFactorStrategyDialog } from './GenerateFactorStrategyDialog'
import { INPUT_MD, SELECT_MD } from '@/components/controls'
import { cn } from '@/lib/cn'

const INPUT_CLS = INPUT_MD

const KIND_META: Record<FactorLibraryItem['kind'], { label: string; cls: string }> = {
  base: { label: '基础', cls: 'bg-elevated text-secondary' },
  virtual: { label: '虚拟', cls: 'bg-accent/10 text-accent' },
  composite: { label: '复合', cls: 'bg-bull/10 text-bull' },
  custom: { label: '自定义', cls: 'bg-amber-400/10 text-amber-500' },
}

/** 生命周期状态 (仅自定义/复合因子有): 表格状态列、筛选与详情弹窗共用。 */
const STATUS_META: Record<string, { label: string; cls: string }> = {
  active: { label: '激活', cls: 'bg-bull/10 text-bull' },
  watch: { label: '观察', cls: 'bg-amber-400/10 text-amber-500' },
  draft: { label: '草稿', cls: 'bg-base text-muted' },
  retired: { label: '退役', cls: 'bg-danger/10 text-danger/80' },
}
const BUILTIN_STATUS = { label: '内置', cls: 'bg-elevated/60 text-muted' }
const statusOf = (item: FactorLibraryItem) =>
  (item.status && STATUS_META[item.status]) || BUILTIN_STATUS

/** 名录表列 (设计稿 .fac): 「因子」列恒显, 其余由页头「列设置」开关 */
const COLS = [
  { key: 'label', label: '因子', width: '', cls: 'px-2 py-[7px] text-xs text-foreground' },
  { key: 'group', label: '维度', width: 'w-[88px]', cls: 'px-2 text-[10.5px] text-muted' },
  { key: 'kind', label: '类型', width: 'w-[64px]', cls: 'px-2 font-mono text-[9.5px] text-muted' },
  { key: 'formula', label: '公式 / 定义', width: '', cls: 'px-2 font-mono text-[10px] text-muted' },
  { key: 'status', label: '', width: 'w-[110px]', cls: 'px-2 text-right' },
] as const

type ColKey = typeof COLS[number]['key']

function cellOf(item: FactorLibraryItem, key: ColKey) {
  switch (key) {
    case 'label': return item.label
    case 'group': return item.group
    case 'kind': return item.kind
    case 'formula': return item.formula || '—'
    case 'status': return (
      <span className={`text-[9.5px] ${item.status ? 'text-secondary' : 'text-muted/70'}`}>{statusOf(item).label}</span>
    )
  }
}

export function FactorLibrary({ onInspect, onEdit, hiddenCols }: {
  onInspect: (factorId: string) => void
  onEdit?: (factorId: string) => void
  /** 页头「列设置」关掉的列 (因子列恒显) */
  hiddenCols?: string[]
}) {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('all')
  const [group, setGroup] = useState('all')
  const [status, setStatus] = useState('all')
  const [detail, setDetail] = useState<FactorLibraryItem | null>(null)

  const lib = useQuery({
    queryKey: QK.factorLibrary('all'),
    queryFn: () => api.factorLibrary(),
    // 注册表近乎静态 (只在保存/删除自定义因子时变化, 届时主动 invalidate), 切 tab 不再闪 loading
    staleTime: 60_000,
  })
  const factors = lib.data?.factors ?? []

  const groups = useMemo(
    () => Array.from(new Set(factors.map(item => item.group))).sort((a, b) => a.localeCompare(b, 'zh')),
    [factors],
  )

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return factors.filter(item => {
      if (kind !== 'all' && item.kind !== kind) return false
      if (group !== 'all' && item.group !== group) return false
      if (status !== 'all') {
        if (status === 'builtin') {
          if (item.status != null) return false
        } else if (item.status !== status) {
          return false
        }
      }
      if (keyword && !`${item.id} ${item.label} ${item.formula}`.toLowerCase().includes(keyword)) return false
      return true
    })
  }, [factors, query, kind, group, status])

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-1.5 pb-2.5">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-[12px] w-[12px] -translate-y-1/2 text-muted" />
          <input
            type="text"
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="搜索因子"
            className={cn(INPUT_CLS, 'w-52 pl-7')}
          />
        </div>
        <select value={kind} onChange={event => setKind(event.target.value)} className={cn(SELECT_MD, 'w-24')} aria-label="因子类型">
          <option value="all">全部类型</option>
          <option value="base">基础</option>
          <option value="virtual">虚拟</option>
          <option value="composite">复合</option>
          <option value="custom">自定义</option>
        </select>
        <select value={group} onChange={event => setGroup(event.target.value)} className={cn(SELECT_MD, 'w-28')} aria-label="因子分组">
          <option value="all">全部分组</option>
          {groups.map(name => <option key={name} value={name}>{name}</option>)}
        </select>
        <select value={status} onChange={event => setStatus(event.target.value)} className={cn(SELECT_MD, 'w-24')} aria-label="生命周期状态">
          <option value="all">全部状态</option>
          <option value="builtin">内置</option>
          <option value="active">激活</option>
          <option value="watch">观察</option>
          <option value="draft">草稿</option>
          <option value="retired">退役</option>
        </select>
        <span className="ml-auto text-[10px] text-muted">{lib.isLoading ? '加载中…' : `${filtered.length} / ${factors.length} 个因子`}</span>
      </div>

      {/* 名录表 (design/v2/05 的 .fac): 一行一个因子, 点行看详情 */}
      <div className="min-h-0 overflow-y-auto border-t border-border">
        {lib.isError && (
          <div className="m-3 rounded-btn border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
            {String((lib.error as Error).message)}
          </div>
        )}
        {lib.isLoading && <div className="py-10 text-center text-xs text-muted">因子库加载中…</div>}
        {!lib.isLoading && !lib.isError && filtered.length === 0 && (
          <div className="py-10 text-center text-xs text-muted">无匹配因子</div>
        )}
        {!lib.isLoading && filtered.length > 0 && (
          <>
            <table className="w-full table-fixed text-[11.5px]">
              <colgroup>
                {COLS.filter(col => col.key === 'label' || !hiddenCols?.includes(col.key)).map(col => (
                  <col key={col.key} className={col.width} />
                ))}
              </colgroup>
              <thead>
                <tr>
                  {COLS.filter(col => col.key === 'label' || !hiddenCols?.includes(col.key)).map(col => (
                    <th key={col.key} scope="col" className="whitespace-nowrap border-b border-border pb-[7px] px-2 text-left text-[9.5px] font-normal tracking-[.1em] text-muted">{col.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map(item => (
                  <tr
                    key={item.id}
                    onClick={() => setDetail(item)}
                    title={item.formula || item.label}
                    className="cursor-pointer transition-colors duration-150 hover:bg-accent/[0.06]"
                  >
                    {COLS.filter(col => col.key === 'label' || !hiddenCols?.includes(col.key)).map(col => (
                      <td key={col.key} className={`truncate ${col.cls}`}>{cellOf(item, col.key)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex justify-between border-t border-border px-2 pt-2.5 text-[10px] text-muted">
              <span>
                {factors.length} 个因子 · 基础 {factors.filter(f => f.kind === 'base').length} · 派生 {factors.filter(f => f.kind !== 'base').length}
              </span>
              <span>{groups.length} 个维度</span>
            </div>
          </>
        )}
      </div>

      {detail && <FactorDetailModal item={detail} onClose={() => setDetail(null)} onInspect={onInspect} onEdit={onEdit} />}
    </div>
  )
}

function FactorDetailModal({
  item,
  onClose,
  onInspect,
  onEdit,
}: {
  item: FactorLibraryItem
  onClose: () => void
  onInspect: (factorId: string) => void
  onEdit?: (factorId: string) => void
}) {
  const queryClient = useQueryClient()
  const setStatus = useMutation({
    mutationFn: (status: 'draft' | 'active' | 'watch' | 'retired') => api.factorSetStatus(item.id, status),
    onSuccess: data => {
      toast(`${item.label} 状态已更新为 ${data.status}`, 'success')
      void queryClient.invalidateQueries({ queryKey: ['factors-library'] })
      onClose()
    },
    onError: (error: Error) => toast(`状态更新失败 · ${error.message}`, 'error'),
  })
  const isDynamic = item.kind === 'custom' || item.kind === 'composite'
  const [groupDraft, setGroupDraft] = useState(item.group)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [blockedRefs, setBlockedRefs] = useState<string[] | null>(null)
  const [generateOpen, setGenerateOpen] = useState(false)
  const removeFactor = useMutation({
    mutationFn: (force: boolean) => api.factorDelete(item.id, force),
    onSuccess: data => {
      const refs = data.removed_references ?? []
      toast(
        refs.length > 0
          ? `${item.label} 已强制删除 · ${refs.length} 处引用需手动调整 (${refs.join('、')})`
          : `${item.label} 已删除`,
        'success',
      )
      void queryClient.invalidateQueries({ queryKey: ['factors-library'] })
      onClose()
    },
    onError: (error: Error) => {
      // 409 时 detail 为 {message, references} → request() 已 stringify 成 JSON 文本
      try {
        const parsed = JSON.parse(error.message) as { references?: string[] }
        if (Array.isArray(parsed.references) && parsed.references.length > 0) {
          setBlockedRefs(parsed.references)
          return
        }
      } catch { /* 非 JSON 报错, 走通用提示 */ }
      toast(`删除失败 · ${error.message}`, 'error')
    },
  })
  const setGroup = useMutation({
    mutationFn: () => api.factorSetGroup(item.id, groupDraft.trim()),
    onSuccess: data => {
      toast(`${item.label} 分组已改为 ${data.group}`, 'success')
      void queryClient.invalidateQueries({ queryKey: ['factors-library'] })
      onClose()
    },
    onError: (error: Error) => toast(`分组修改失败 · ${error.message}`, 'error'),
  })
  const rows: [string, string][] = [
    ['标识', item.id],
    ['名称', item.label],
    ['分组', item.group],
    ['类型', KIND_META[item.kind].label],
    ['公式', item.formula],
    ['版本', `v${item.version}`],
    ['预热', item.warmup_bars > 1 ? `${item.warmup_bars} 个交易日` : '无需滚动窗口'],
    ['点时数据', item.pit ? '是（按公告日口径，不泄露未公告数据）' : '否'],
    ['适用资产', item.asset_types.join(' / ')],
    ['跨标的可比', item.scale_free ? '是（可直接截面排序）' : '否（原值受价格尺度影响）'],
    ['依赖列', item.dependencies.length > 0 ? item.dependencies.join(', ') : '已物化列'],
  ]
  return (
    <Modal
      onClose={onClose}
      labelledBy="factor-detail-title"
      panelClassName="flex max-h-[86vh] w-[92vw] max-w-lg flex-col overflow-hidden border border-border bg-surface shadow-xl rounded-card"
    >
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FlaskConical className="h-[16px] w-[16px] shrink-0 text-accent" />
            <span id="factor-detail-title" className="truncate text-sm font-semibold text-foreground">{item.label}</span>
            <span className={`inline-flex shrink-0 rounded-btn px-1.5 py-0.5 text-[10px] font-medium ${KIND_META[item.kind].cls}`}>
              {KIND_META[item.kind].label}
            </span>
            <span className={`inline-flex shrink-0 rounded-btn px-1.5 py-0.5 text-[10px] font-medium ${statusOf(item).cls}`}>
              {statusOf(item).label}
            </span>
          </div>
          <div className="mt-1 font-mono text-[11px] text-muted">{item.id}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ctl ctl-md ctl-ghost inline-flex w-[28px] px-0 shrink-0 items-center justify-center hover:text-foreground"
          aria-label="关闭"
        >
          <X className="h-[16px] w-[16px]" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        <dl className="space-y-2">
          {rows.map(([key, value]) => (
            <div key={key} className="flex items-start gap-3 text-xs">
              <dt className="w-20 shrink-0 text-muted">{key}</dt>
              <dd className="min-w-0 flex-1 break-words text-secondary">{value}</dd>
            </div>
          ))}
        </dl>
        {isDynamic && (
          <div className="mt-4 rounded-btn border border-border bg-base/40 p-3">
            <div className="text-[11px] font-medium text-secondary">管理</div>
            <div className="mt-2 flex items-center gap-1.5">
              <span className="w-10 shrink-0 text-[11px] text-muted" title="仅自定义/复合因子可改; 内置因子分组固定">分组</span>
              <input
                type="text"
                value={groupDraft}
                maxLength={24}
                onChange={event => setGroupDraft(event.target.value)}
                className="ctl ctl-sm ctl-input w-36 bg-base/60 px-1.5 focus:border-accent"
                aria-label="修改分组"
              />
              <button
                type="button"
                onClick={() => setGroup.mutate()}
                disabled={setGroup.isPending || !groupDraft.trim() || groupDraft.trim() === item.group}
                className="ctl ctl-sm ctl-btn px-1.5 hover:border-accent/40 hover:text-accent disabled:opacity-40"
              >
                {setGroup.isPending ? '保存中…' : '保存'}
              </button>
            </div>
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <span className="w-10 shrink-0 text-[11px] text-muted">状态</span>
              {(['active', 'watch', 'draft', 'retired'] as const).map(statusKey => (
                <button
                  key={statusKey}
                  type="button"
                  onClick={() => setStatus.mutate(statusKey)}
                  disabled={setStatus.isPending}
                  className={`ctl ctl-sm ctl-btn px-1.5 disabled:opacity-40 ${(item.status ?? '') === statusKey
                    ? 'border-accent/60 bg-accent/10 font-medium text-accent'
                    : 'border-border text-secondary hover:border-accent/40 hover:text-accent'
                  }`}
                >
                  {statusKey === 'active' ? '激活' : statusKey === 'watch' ? '观察' : statusKey === 'retired' ? '退役' : '回草稿'}
                </button>
              ))}
              <span className="text-[10px] text-muted" title="生命周期语义">编辑公式后自动回草稿</span>
            </div>
          </div>
        )}
        <div className="mt-4 rounded-btn border border-border bg-base/40 px-3 py-2 text-[11px] leading-relaxed text-muted">
          方向说明：因子方向不预填，以最近一次检验的 IC 符号为准（IC 为正 = 值大看多）。到「发现」页运行后可查看。
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
        <button
          type="button"
          onClick={() => setGenerateOpen(true)}
          className="ctl ctl-md ctl-btn inline-flex items-center gap-1 border-accent/40 px-2.5 hover:bg-accent/10"
          title="按该因子生成单因子排名策略, 保存到自定义策略后可直接回测"
        >
          <Sparkles className="h-[12px] w-[12px]" />
          生成策略
        </button>
        {item.kind === 'custom' && onEdit && (
          <button
            type="button"
            onClick={() => { onClose(); onEdit(item.id) }}
            className="ctl ctl-md ctl-btn inline-flex items-center gap-1 border-accent/40 px-2.5 hover:bg-accent/10"
            title="在编辑器中打开该公式, 修改后保存为新版本"
          >
            <PenLine className="h-[12px] w-[12px]" />
            编辑公式
          </button>
        )}
        {isDynamic && (
          confirmDelete ? (
            <>
              <button
                type="button"
                onClick={() => removeFactor.mutate(false)}
                disabled={removeFactor.isPending}
                className="ctl ctl-md ctl-btn danger inline-flex shrink-0 items-center gap-1 px-2.5 hover:bg-danger/90 disabled:opacity-40"
              >
                {removeFactor.isPending ? '删除中…' : '确认删除'}
              </button>
              <button
                type="button"
                onClick={() => { setConfirmDelete(false); setBlockedRefs(null) }}
                className="ctl ctl-md ctl-btn inline-flex shrink-0 items-center px-2.5 hover:text-foreground"
              >
                取消
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="ctl ctl-md ctl-btn inline-flex shrink-0 items-center gap-1 border-danger/40 px-2.5 hover:bg-danger/10"
              title="删除该自定义/复合因子; 被策略或复合因子引用时需强制删除"
            >
              <Trash2 className="h-[12px] w-[12px]" />
              删除
            </button>
          )
        )}
        <button
          type="button"
          onClick={() => { onClose(); onInspect(item.id) }}
          className="ctl ctl-md ctl-btn pri ml-auto inline-flex shrink-0 items-center gap-1 px-2.5 hover:bg-accent/90"
        >
          检验此因子 <ArrowRight className="h-[12px] w-[12px]" />
        </button>
        {blockedRefs && (
          <div className="w-full rounded-btn border border-danger/40 bg-danger/10 px-3 py-2 text-[11px] leading-relaxed text-danger">
            <div className="font-medium">该因子仍被以下对象引用，删除后相关策略/复合因子将无法计算：</div>
            <div className="mt-1 font-mono text-[10px] break-all text-danger/90">{blockedRefs.join('、')}</div>
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={() => removeFactor.mutate(true)}
                disabled={removeFactor.isPending}
                className="ctl ctl-sm ctl-btn danger px-1.5 hover:bg-danger/90 disabled:opacity-40"
              >
                {removeFactor.isPending ? '删除中…' : '仍要强制删除'}
              </button>
              <button
                type="button"
                onClick={() => setBlockedRefs(null)}
                className="text-[11px] text-secondary underline-offset-2 transition-colors hover:text-foreground hover:underline"
              >
                收起
              </button>
            </div>
          </div>
        )}
      </div>
      {generateOpen && (
        <GenerateFactorStrategyDialog item={item} onClose={() => setGenerateOpen(false)} />
      )}
    </Modal>
  )
}
