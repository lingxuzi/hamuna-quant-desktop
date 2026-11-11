import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Settings2, Trash2 } from 'lucide-react'
import { api, type CustomSignal, type CustomSignalCondition } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { type SignalKind } from '@/lib/signals'
import { CustomSignalDialog } from '@/components/signals/CustomSignalDialog'
import { Skeleton } from '@/components/data/Skeleton'
import { PageHeader } from '@/components/PageHeader'
import { AnchorWrap } from '@/lib/useCardFlash'
import { ResourceTransferButton } from '@/components/ResourceTransferDialog'

const KIND_LABEL: Record<SignalKind, string> = { entry: '入场', exit: '出场', both: '双向' }
/** 设计稿: 表内最多列 14 条, 其余在页脚计数里说明。 */
const ROW_CAP = 14

/** 信号库独立页: 全部信号均为用户定义 (csg_* / 迁移的 signal_*), 策略/回测/监控统一取用。 */
export function Signals() {
  const [searchParams] = useSearchParams()
  const highlight = searchParams.get('highlight') ?? ''

  return (
    <div>
      <PageHeader
        seal="信"
        title="信号库"
        subtitle="SIGNAL LIBRARY"
        right={<ResourceTransferButton kind="signal" />}
      />
      <SignalsBody highlight={highlight} />
    </div>
  )
}

function SignalsBody({ highlight }: { highlight: string }) {
  const qc = useQueryClient()
  const list = useQuery({ queryKey: QK.customSignals, queryFn: api.customSignalsList, staleTime: 60_000 })
  const options = useQuery({ queryKey: QK.customSignalsOptions, queryFn: api.customSignalsOptions, staleTime: 60_000 })

  const [showForm, setShowForm] = useState(false)
  const [filter, setFilter] = useState<'all' | 'on' | 'off'>('all')
  const [editing, setEditing] = useState<CustomSignal | null>(null)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const resetDeleteTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fields = options.data?.fields ?? []
  const signals = list.data?.signals ?? []
  const enabledCustomSignals = signals.filter(sig => sig.enabled).length

  useEffect(() => () => {
    if (resetDeleteTimer.current) clearTimeout(resetDeleteTimer.current)
  }, [])

  const clearDeleteConfirm = () => {
    if (resetDeleteTimer.current) clearTimeout(resetDeleteTimer.current)
    resetDeleteTimer.current = null
    setConfirmingDeleteId(null)
  }

  const openNew = () => {
    setEditing(null)
    clearDeleteConfirm()
    setShowForm(true)
  }
  const openEdit = (sig: CustomSignal) => {
    setEditing(sig)
    clearDeleteConfirm()
    setShowForm(true)
  }
  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
  }

  const del = useMutation({
    mutationFn: api.customSignalDelete,
    onSuccess: () => {
      clearDeleteConfirm()
      qc.invalidateQueries({ queryKey: QK.customSignals })
    },
  })

  const toggleEnabled = (sig: CustomSignal) => {
    api.customSignalSave({ ...sig, enabled: !sig.enabled }).then(() => qc.invalidateQueries({ queryKey: QK.customSignals }))
  }

  const handleDeleteClick = (sig: CustomSignal) => {
    if (confirmingDeleteId === sig.id) {
      clearDeleteConfirm()
      del.mutate(sig.id)
      return
    }
    setConfirmingDeleteId(sig.id)
    if (resetDeleteTimer.current) clearTimeout(resetDeleteTimer.current)
    resetDeleteTimer.current = setTimeout(() => setConfirmingDeleteId(null), 3000)
  }

  const disabledCount = signals.length - enabledCustomSignals
  const visible = signals.filter(sig => filter === 'all' || (filter === 'on' ? sig.enabled : !sig.enabled))
  // 设计稿: 表内最多列 14 条, 页脚写明「显示前 14 条, 共 N 条」
  const shown = visible.slice(0, ROW_CAP)

  return (
    <div className="space-y-0">
      {/* 工具条 1: 总量 + 字段规模 */}
      <div className="toolbar">
        <div className="tb-left">
          <span className="tb-chip">{signals.length} 个信号</span>
          <span className="tb-note">全部启用 · 条件字段 {fields.length} 个</span>
        </div>
        <div className="tb-right" />
      </div>

      {/* 工具条 2: 三档过滤 + 新建 */}
      <div className="toolbar" style={{ borderTop: 0 }}>
        <div className="tb-left">
          <div className="seg">
            <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部 {signals.length}</button>
            <button aria-pressed={filter === 'on'} onClick={() => setFilter('on')}>已启用 {enabledCustomSignals}</button>
            <button aria-pressed={filter === 'off'} onClick={() => setFilter('off')}>已停用 {disabledCount}</button>
          </div>
          <span className="tb-note">条件字段 {fields.length} 个 · 运算符 6 种 · 最长回溯 60 日</span>
        </div>
        <div className="tb-right">
          <button onClick={openNew} className="ctl ctl-md ctl-btn">+ 新建信号</button>
        </div>
      </div>

      <AnchorWrap highlight={highlight} anchor="signals">
        <table className="sig" style={{ borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th scope="col">信号名</th>
              <th scope="col">列名</th>
              <th scope="col">方向 / 周期</th>
              <th scope="col">条件</th>
              <th scope="col">启用</th>
              <th scope="col" className="w-px" />
            </tr>
          </thead>
          <tbody>
            {shown.map(sig => (
              <tr key={sig.id} className="group">
                <td className="n">{sig.name}</td>
                <td className="c">{sig.column ?? `csg_${sig.id}`}</td>
                <td className="c">{KIND_LABEL[sig.kind]} · 日线</td>
                <td>
                  {sig.conditions.map((c, i) => (
                    <span key={i} className="cond">{rawCondition(c)}</span>
                  ))}
                </td>
                <td>
                  <button
                    role="switch"
                    aria-checked={sig.enabled}
                    aria-label={sig.enabled ? '停用信号' : '启用信号'}
                    onClick={() => toggleEnabled(sig)}
                    className={`sw${sig.enabled ? ' on' : ''}`}
                  >
                    <i />
                  </button>
                </td>
                <td className="whitespace-nowrap">
                  <span className="inline-flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <button onClick={() => openEdit(sig)} title="编辑" className="ctl ctl-sm ctl-ghost ctl-icon text-muted hover:text-accent">
                      <Settings2 className="h-3.5 w-3.5" />
                    </button>
                    {confirmingDeleteId === sig.id ? (
                      <button
                        onClick={() => handleDeleteClick(sig)}
                        disabled={del.isPending}
                        title="再次点击确认删除"
                        className="ctl ctl-sm ctl-btn soft inline-flex items-center gap-1 border-danger/30 px-1.5 disabled:opacity-50 bg-danger/15"
                      >
                        <Trash2 className="h-2.5 w-2.5" />确认
                      </button>
                    ) : (
                      <button
                        onClick={() => handleDeleteClick(sig)}
                        disabled={del.isPending}
                        className="ctl ctl-sm ctl-ghost ctl-icon text-muted hover:text-danger disabled:opacity-50"
                        title="删除"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </span>
                </td>
              </tr>
            ))}
            {list.isLoading &&
              Array.from({ length: 4 }).map((_, i) => (
                <tr key={`sk-${i}`}>
                  <td colSpan={6} className="py-2"><Skeleton h="h-4" /></td>
                </tr>
              ))}
          </tbody>
        </table>
        {!list.isLoading && visible.length === 0 && (
          <div className="py-10 text-center text-[12px] text-muted">
            {signals.length === 0
              ? '信号库为空。可用「字段 + 运算符 + 值」组合条件创建, 或从检验页因子行一键生成。'
              : '当前筛选下没有信号。'}
          </div>
        )}
      </AnchorWrap>

      <div className="sig-foot">
        <span>
          {visible.length > ROW_CAP ? `显示前 ${ROW_CAP} 条` : `显示 ${visible.length} 条`}，共 <span className="num">{signals.length}</span> 条
        </span>
        <span>改动即时生效，随下一次选股运行写入结果列</span>
      </div>

      <CustomSignalDialog open={showForm} signal={editing} onClose={closeForm} />
    </div>
  )
}

/** 条件表达式原样展示: 字段键 + 运算符 + 值(字段引用去掉 field: 前缀)。 */
function rawCondition(c: CustomSignalCondition): string {
  const left = withDays(c.left, c.leftDays)
  const right = typeof c.right === 'string' && c.right.startsWith('field:')
    ? withDays(c.right.slice(6), c.rightDays)
    : c.right
  return `${left} ${normalizeOp(c.op)} ${right}`
}

/** 回溯天数用 [-N] 标注(0/未填 = 当日)。 */
function withDays(key: string, days: number | undefined): string {
  return days && days > 0 ? `${key}[-${days}]` : key
}

/** 运算符按设计稿排版: >= → ≥, <= → ≤。 */
function normalizeOp(op: string): string {
  if (op === '>=') return '≥'
  if (op === '<=') return '≤'
  return op
}
