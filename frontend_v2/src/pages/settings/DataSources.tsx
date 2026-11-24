import { useState } from 'react'
import { ExtensionSlot } from '@/extensions/ExtensionSlot'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import {
  AlertCircle,
  AlertTriangle,
  CandlestickChart as CandlestickIcon,
  Check,
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  FileWarning,
  KeyRound,
  Landmark,
  Loader2,
  Plus,
  Puzzle,
  Radio,
  RefreshCw,
  Save,
  Scale,
  Timer,
  Trash2,
  Zap,
} from 'lucide-react'
import {
  api,
  type CapabilityMatrix,
  type CapabilityRoute,
  type DataSourceItem,
  type PluginDataSourceItem,
  type Preferences,
  type ProviderField,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { AnchorWrap } from '@/lib/useCardFlash'
import { DEFAULT_ROUTING, buildProviderRouting, BUILTIN_PROVIDER, BUILTIN_PROVIDER_LABEL } from '@/lib/provider-routing'
import { toast } from '@/components/Toast'
import { PluginMetadata } from '@/components/data/PluginMetadata'
import { DataSourceEditor } from './DataSourceEditor'

/** 内置源名 (唯一内置源, 展示归「内置」组) */
const BUILTIN = BUILTIN_PROVIDER
/** 内置源展示名 (界面不暴露实现库名) */
const BUILTIN_LABEL = BUILTIN_PROVIDER_LABEL

const DATASET_LABEL: Record<string, string> = {
  realtime: '实时',
  daily: '日K',
  minute: '分钟',
  adj_factor: '除权',
  depth5: '五档',
  financial: '财务',
  full_minute: '全量分钟',
}

/** 能力图标 (纯展示;能力清单本身由后端注册表驱动) */
const CAP_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  realtime: Radio,
  daily: CandlestickIcon,
  minute: Timer,
  full_minute: Zap,
  adj_factor: Scale,
  financial: Landmark,
}

/** 展示名清洗: 去掉后端 display_name 尾部的括注 (第三方库名等实现细节) */
function displayName(raw: string): string {
  return raw.replace(/\s*\(.*\)$/, '')
}

/** 源名 → 展示名 (特殊值 + 候选/待就绪表查找, 找不到回退原始名) */
function displayOfName(matrix: CapabilityMatrix | undefined, name: string): string {
  if (name === BUILTIN) return BUILTIN_LABEL
  if (name === 'same_as_daily') return '跟随日K'
  for (const cap of matrix?.capabilities ?? []) {
    const hit = cap.candidates.find(c => c.name === name) ?? cap.pending.find(c => c.name === name)
    if (hit) return displayName(hit.display)
  }
  return name
}

/** 源的运行形态 (设计稿 .src 的「运行」行) */
function runtimeLabel(item: DataSourceItem, plugin?: PluginDataSourceItem): string {
  if (item.name === BUILTIN) return '本地直连'
  if (plugin) {
    if (plugin.runtime === 'python') return 'Python 进程'
    if (plugin.runtime === 'node') return 'Node 进程'
    return plugin.api_key_env ? 'HTTP 接口' : '随进程内置'
  }
  return 'HTTP 接口'
}

/** 乐观更新:把一组偏好字段变更应用到矩阵缓存 (每能力独立路由, current 即生效) */
function patchMatrix(
  matrix: CapabilityMatrix,
  changes: Partial<Record<ProviderField, string>>,
): CapabilityMatrix {
  const caps = matrix.capabilities.map(c => ({ ...c }))
  for (const cap of caps) {
    const value = cap.field != null ? changes[cap.field] : undefined
    if (value !== undefined) {
      cap.current = value
      cap.current_display = displayOfName({ capabilities: caps }, value)
      cap.effective = value
      cap.effective_display = cap.current_display
    }
  }
  return { ...matrix, capabilities: caps }
}

/** 能力路由行 (设计稿 .cap 行式, 非卡片): 能力 ← 生效源 / 候选 / 可用。
 *  candidates 只含当前可提供该能力的源; 未就绪源 (pending) 置灰提示;
 *  生效源无法提供该能力 (usable=False) 时显示琥珀警示。 */
function CapabilityRow({ cap, pendingKey, onSelect }: {
  cap: CapabilityRoute
  pendingKey: string | null
  onSelect: (field: ProviderField, provider: string) => void
}) {
  const busy = (provider: string) => pendingKey === `${cap.field}:${provider}`
  // 能力中立判定:usable=False 即当前路由的源供不了 (源未就绪同待遇)
  const unmet = !cap.usable
  // field=null → 不可路由能力 (无偏好字段): 渲染为静态标签, 保持高亮但不可点
  const routable = cap.field != null
  const isDefault = cap.current === cap.default
  return (
    <div className="cap" title={cap.desc}>
      <b>{cap.label}</b>

      {/* 当前生效提供方 */}
      <div className="cur">
        {unmet ? (
          <span
            className="inline-flex items-center gap-1 text-warning"
            title={`「${cap.label}」当前路由的源无法提供该能力 — 可切换右侧可用源, 或在数据源区接入其他源`}
          >
            <AlertTriangle className="h-[12px] w-[12px] shrink-0" />
            能力不可用
          </span>
        ) : (
          <>
            <span>{displayName(cap.effective_display)}</span>
            {isDefault && <i>默认</i>}
          </>
        )}
      </div>

      {/* 候选: 点谁该能力就由谁提供 */}
      <div className="cands">
        {cap.candidates.map(c => {
          const active = cap.current === c.name
          if (!routable) {
            return (
              <u key={c.name} className={active ? 'ok' : undefined} title={`「${cap.label}」仅 ${displayName(c.display)} 提供 (不可路由)`}>
                {displayName(c.display)}
              </u>
            )
          }
          return (
            <button
              key={c.name}
              type="button"
              disabled={pendingKey != null}
              onClick={() => cap.field != null && onSelect(cap.field, c.name)}
              title={`「${cap.label}」由 ${displayName(c.display)} 提供`}
              className={active ? 'on' : 'ok'}
            >
              {busy(c.name) && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
              {displayName(c.display)}
            </button>
          )
        })}
        {/* 未就绪源: 声明了该能力但依赖/Key 未配好, 置灰并说明原因 */}
        {cap.pending.map(c => (
          <u key={c.name} title={c.note || c.status || '该源当前不可用'}>{c.display}</u>
        ))}
        {cap.candidates.length === 0 && cap.pending.length === 0 && (
          <span className="hint">暂无可用提供方</span>
        )}
      </div>

      {unmet ? <span className="no">不可用</span> : <span className="ok">可用</span>}
    </div>
  )
}

/** 能力路由区 (页面主视图): 每个能力一张卡, 点候选标签即刻切换 (乐观更新) */
function CapabilityRoutingSection() {
  const qc = useQueryClient()
  const matrix = useQuery({ queryKey: QK.capabilityMatrix, queryFn: api.capabilityMatrix })
  const [pendingKey, setPendingKey] = useState<string | null>(null)

  const invalidateRouting = () => {
    qc.invalidateQueries({ queryKey: QK.capabilityMatrix })
    qc.invalidateQueries({ queryKey: QK.preferences })
    qc.invalidateQueries({ queryKey: QK.capabilities })
    qc.invalidateQueries({ queryKey: QK.quoteStatus })
  }

  /** 切换前先把变更写进矩阵/偏好缓存, 界面零延迟响应;失败回滚 */
  const applyOptimistic = (changes: Partial<Record<ProviderField, string>>) => {
    const prevMatrix = qc.getQueryData<CapabilityMatrix>(QK.capabilityMatrix)
    const prevPrefs = qc.getQueryData<Preferences>(QK.preferences)
    if (prevMatrix) qc.setQueryData(QK.capabilityMatrix, patchMatrix(prevMatrix, changes))
    if (prevPrefs) qc.setQueryData(QK.preferences, { ...prevPrefs, ...changes })
    return { prevMatrix, prevPrefs }
  }

  const rollback = (ctx: { prevMatrix?: CapabilityMatrix; prevPrefs?: Preferences } | undefined) => {
    if (ctx?.prevMatrix) qc.setQueryData(QK.capabilityMatrix, ctx.prevMatrix)
    if (ctx?.prevPrefs) qc.setQueryData(QK.preferences, ctx.prevPrefs)
  }

  const routeMut = useMutation({
    mutationFn: ({ field, provider }: { field: ProviderField; provider: string }) =>
      // 动态键经运行时字段名收敛为合法偏好键 (字段名来自后端注册表)
      api.updateDataProviders({ [field]: provider } as Partial<Pick<Preferences, ProviderField>>),
    onMutate: async (v) => {
      setPendingKey(`${v.field}:${v.provider}`)
      await qc.cancelQueries({ queryKey: QK.capabilityMatrix })
      await qc.cancelQueries({ queryKey: QK.preferences })
      return applyOptimistic({ [v.field]: v.provider })
    },
    onSuccess: (_d, v) => {
      const cap = matrix.data?.capabilities.find(c => c.field === v.field)
      toast(`「${cap?.label || v.field}」已切换为 ${displayOfName(matrix.data, v.provider)}`, 'success')
    },
    onError: (e: Error, _v, ctx) => {
      rollback(ctx)
      toast(`路由切换失败：${e.message}`, 'error')
    },
    onSettled: () => {
      setPendingKey(null)
      invalidateRouting()
    },
  })

  const resetMut = useMutation({
    mutationFn: () => api.updateDataProviders(DEFAULT_ROUTING),
    onMutate: async () => {
      await qc.cancelQueries({ queryKey: QK.capabilityMatrix })
      await qc.cancelQueries({ queryKey: QK.preferences })
      return applyOptimistic(DEFAULT_ROUTING)
    },
    onSuccess: () => toast('能力路由已恢复默认', 'success'),
    onError: (e: Error, _v, ctx) => {
      rollback(ctx)
      toast(`恢复失败：${e.message}`, 'error')
    },
    onSettled: invalidateRouting,
  })

  const list = matrix.data?.capabilities ?? []
  const anyCustom = list.some(c => c.current !== c.default)

  return (
    <section>
      <div className="sec-h">
        <span>能力路由</span>
        <span className="note">{list.length} 个能力 · 每个能力独立选择提供方，点标签即刻生效</span>
        <span className="sp" />
        {anyCustom && (
          <button
            onClick={() => resetMut.mutate()}
            disabled={resetMut.isPending}
            className="ctl ctl-sm ctl-ghost inline-flex items-center gap-1.5 px-1.5 hover:text-foreground hover:bg-elevated disabled:opacity-50 shrink-0"
          >
            <RefreshCw className={`h-[12px] w-[12px] ${resetMut.isPending ? 'animate-spin' : ''}`} />
            恢复默认
          </button>
        )}
      </div>

      {matrix.isError ? (
        <div className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-btn border border-danger/20 bg-danger/5">
          <div className="flex items-center gap-2 text-xs text-danger min-w-0">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">能力矩阵加载失败：{(matrix.error as Error)?.message || '未知错误'}</span>
          </div>
          <button
            onClick={() => matrix.refetch()}
            className="text-xs text-muted hover:text-foreground shrink-0"
          >
            重试
          </button>
        </div>
      ) : matrix.isLoading || list.length === 0 ? (
        <div>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-9 border-b border-border/40 bg-elevated/20 animate-pulse" />
          ))}
        </div>
      ) : (
        <div>
          {list.map(cap => (
            <CapabilityRow
              key={cap.id}
              cap={cap}
              pendingKey={pendingKey}
              onSelect={(field: ProviderField, provider: string) => routeMut.mutate({ field, provider })}
            />
          ))}
        </div>
      )}
    </section>
  )
}

/** 插件 API Key 配置区块 (嵌入插件详情卡, 不再独立成卡)。
 *  先探后存: 后端用候选 Key 实探一次, 无效不落盘; secrets.json 优先于 .env。 */
export function PluginKeyConfig({ plugin }: { plugin: PluginDataSourceItem }) {
  const qc = useQueryClient()
  const [keyInput, setKeyInput] = useState('')
  const [revealing, setRevealing] = useState(false)
  const [saved, setSaved] = useState(false)

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: QK.dataSources })
    qc.invalidateQueries({ queryKey: QK.capabilityMatrix })
    qc.invalidateQueries({ queryKey: QK.capabilities })
    qc.invalidateQueries({ queryKey: QK.quoteStatus })
  }

  const save = useMutation({
    mutationFn: () => api.savePluginKey(plugin.name, keyInput.trim()),
    onSuccess: (data) => {
      invalidate()
      if (data.ok) {
        setKeyInput('')
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      }
    },
    onError: (e: Error) => toast(`保存失败：${e.message}`, 'error'),
  })

  const clear = useMutation({
    mutationFn: () => api.clearPluginKey(plugin.name),
    onSuccess: (data) => {
      invalidate()
      if (data.ok) {
        toast(
          data.plugin_available
            ? '已清除界面配置的 Key(.env 中的同名变量仍然生效)'
            : 'Key 已清除，插件不再可用',
          'success',
        )
      }
    },
    onError: (e: Error) => toast(`清除失败：${e.message}`, 'error'),
  })

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <KeyRound className="h-3.5 w-3.5 text-secondary" />
        <h4 className="text-xs font-medium text-foreground">API Key</h4>
        <span className="break-all font-mono text-[11px] text-secondary">{plugin.api_key_env}</span>
      </div>

      <p className="text-xs text-secondary leading-relaxed mb-4">
        向该数据源官方申请 API Key。
        Key 保存在本地，验证和取数时发送给该数据源用于认证。环境变量可作为后备配置。
      </p>

      {/* 积分计费警示框 (manifest billing/billing_note 声明) */}
      {plugin.billing === 'points' && (
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-warning/35 bg-warning/[0.07] px-3 py-2.5">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
          <div className="text-[11px] leading-relaxed text-warning/90">
            <span className="font-medium">该数据源按积分计费。</span>
            {plugin.billing_note
              || '各能力取数均消耗积分；拉取越频繁、范围越大，消耗越高，具体以商城「积分明细」为准。'}
          </div>
        </div>
      )}
      {/* 当前状态 */}
      <div className="flex items-center justify-between mb-4">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-widest text-muted">状态</div>
          <div className="mt-1 flex items-center gap-2 min-w-0">
            {plugin.available ? (
              <>
                <CheckCircle2 className="h-[16px] w-[16px] text-bear shrink-0" />
                <span className="text-sm font-medium shrink-0">已配置</span>
                {/* 生效 Key 脱敏串 (secrets.json 优先, .env 兜底) — 常驻显示, 便于确认当前生效的是哪一把 */}
                {plugin.api_key_masked && (
                  <span className="font-mono text-xs text-secondary truncate" title={plugin.api_key_masked}>
                    {plugin.api_key_masked}
                  </span>
                )}
              </>
            ) : (
              <>
                <AlertCircle className="h-[16px] w-[16px] text-muted shrink-0" />
                <span className="text-sm font-medium text-muted shrink-0">未配置</span>
                <span className="text-xs text-muted/70 truncate" title={plugin.status}>{plugin.status}</span>
              </>
            )}
          </div>
        </div>
        {plugin.available && (
          <button
            onClick={() => clear.mutate()}
            disabled={clear.isPending}
            className="ctl ctl-sm ctl-ghost inline-flex items-center gap-1.5 px-1.5 hover:text-danger duration-150 ease-smooth disabled:opacity-50 shrink-0 bg-elevated"
          >
            <Trash2 className="h-[12px] w-[12px]" />
            清除
          </button>
        )}
      </div>

      {/* 输入 */}
      <form
        onSubmit={(e) => { e.preventDefault(); if (keyInput.trim()) save.mutate() }}
        className="space-y-2"
      >
        <div className="relative">
          <input
            aria-label={`${plugin.display_name} API Key`}
            type={revealing ? 'text' : 'password'}
            placeholder={plugin.available ? '粘贴新 Key 替换当前' : `粘贴 ${plugin.display_name} API Key`}
            value={keyInput}
            onChange={(e) => { setKeyInput(e.target.value); if (saved) setSaved(false) }}
            autoComplete="off"
            className="ctl ctl-md ctl-input w-full px-2.5 pr-9 font-mono focus:border-accent duration-150 ease-smooth bg-base"
          />
          <button
            type="button"
            onClick={() => setRevealing((v) => !v)}
            className="ctl ctl-md ctl-ghost absolute right-1.5 top-1/2 inline-flex h-ctl-md w-[24px] px-0 -translate-y-1/2 items-center justify-center text-muted hover:text-foreground duration-150 ease-smooth"
            tabIndex={-1}
            aria-label={revealing ? '隐藏' : '显示'}
          >
            {revealing ? <EyeOff className="h-[16px] w-[16px]" /> : <Eye className="h-[16px] w-[16px]" />}
          </button>
        </div>
        <button
          type="submit"
          disabled={save.isPending || (!keyInput.trim() && !saved)}
          className="ctl ctl-lg ctl-btn pri w-full font-semibold flex items-center justify-center gap-2 hover:bg-accent/90 disabled:opacity-40 transition-all"
        >
          {save.isPending ? <Loader2 className="h-[16px] w-[16px] animate-spin" /> : saved ? <Check className="h-[16px] w-[16px]" /> : <Save className="h-[16px] w-[16px]" />}
          {save.isPending ? '验证中...' : saved ? '已保存' : '保存并检测'}
        </button>
      </form>

      {/* 无效 Key —— 先探后存: 探测失败时不存储 */}
      {save.data && !save.data.ok && (
        <div className="mt-3 text-xs text-danger flex items-center gap-1.5">
          <AlertCircle className="h-[12px] w-[12px] shrink-0" />
          {save.data.error || 'Key 无效，未保存'}
        </div>
      )}
      {save.isError && (
        <div className="mt-3 text-xs text-danger">
          保存失败：{String((save.error as Error).message)}
        </div>
      )}
    </div>
  )
}

/** 能力芯片:两态 — 服务中(高亮+勾) / 已适配(灰)。
 *  paid (插件 billing="points"): 每个芯片尾标注「积分」角标 — 该能力按积分计费。 */
function CapabilityChips({ caps, servingSet, paid = false }: {
  caps: CapabilityRoute[]
  servingSet: Set<string>
  paid?: boolean
}) {
  if (caps.length === 0) return <span className="text-[10px] text-muted/40">未声明能力</span>
  return (
    <div className="flex flex-wrap gap-1">
      {caps.map(cap => {
        const servingNow = servingSet.has(cap.id)
        const cls = servingNow
          ? 'bg-accent/15 text-accent'
          : 'bg-elevated/60 text-muted/70'
        const title = servingNow
          ? `正在提供「${cap.label}」${paid ? ' · 积分计费' : ''}`
          : `已适配「${cap.label}」${paid ? ' · 积分计费' : ''}`
        return (
          <span
            key={cap.id}
            title={title}
            className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] leading-none ${cls}`}
          >
            {servingNow && <Check className="h-2.5 w-2.5" />}
            {DATASET_LABEL[cap.id] || cap.id}
            {paid && (
              <span className="ml-0.5 rounded bg-warning/15 px-1 py-px text-[9px] font-medium leading-none text-warning">积分</span>
            )}
          </span>
        )
      })}
    </div>
  )
}

export function SettingsDataSourcesPanel({ highlight }: { highlight?: string } = {}) {
  const qc = useQueryClient()
  const prefs = usePreferences()
  const sources = useQuery({ queryKey: QK.dataSources, queryFn: api.dataSources })
  const matrix = useQuery({ queryKey: QK.capabilityMatrix, queryFn: api.capabilityMatrix })
  const [selected, setSelected] = useState<string>(BUILTIN) // 当前在下方配置的源 name
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const builtin: DataSourceItem[] = sources.data?.builtin ?? []
  const allPlugins: PluginDataSourceItem[] = sources.data?.plugins ?? []
  // 内置源同时带插件清单 (装依赖用), 展示归「内置」组, 不在插件组里重复
  const pluginList = allPlugins.filter(p => p.name !== BUILTIN)
  const customList: DataSourceItem[] = sources.data?.custom ?? []
  const errors = sources.data?.errors ?? []
  const activeName = prefs.data?.daily_data_provider || BUILTIN

  const pluginItems: DataSourceItem[] = pluginList.map(p => ({
    name: p.name, display_name: p.display_name, datasets: p.datasets,
  }))
  const allItems = [
    ...builtin,
    ...pluginItems,
    ...customList,
  ]
  const customNames = new Set(customList.map(c => c.name))

  const selectedCustom = customList.find(s => s.name === selected)

  // ===== 各能力当前的有效提供方 (除权兼容旧值 same_as_daily = 跟随日K) =====
  // 用于能力芯片"服务中"态与详情卡标识:路由切换在上方能力路由区, 这里只读展示
  const dailyPref = prefs.data?.daily_data_provider || DEFAULT_ROUTING.daily_data_provider
  const adjPref = prefs.data?.adj_factor_provider || DEFAULT_ROUTING.adj_factor_provider
  const effProvider: Record<string, string> = {
    daily: dailyPref,
    adj_factor: adjPref === 'same_as_daily' ? dailyPref : adjPref,
    minute: prefs.data?.minute_data_provider || DEFAULT_ROUTING.minute_data_provider,
    full_minute: prefs.data?.full_minute_data_provider || DEFAULT_ROUTING.full_minute_data_provider,
    realtime: prefs.data?.realtime_data_provider || DEFAULT_ROUTING.realtime_data_provider,
    depth5: prefs.data?.depth5_data_provider || DEFAULT_ROUTING.depth5_data_provider,
    financial: prefs.data?.financial_data_provider || DEFAULT_ROUTING.financial_data_provider,
  }
  const servingDatasets = (name: string) =>
    Object.entries(effProvider).filter(([, v]) => v === name).map(([k]) => k)
  const servingSetOf = (name: string) => new Set(servingDatasets(name))

  const matrixCaps = matrix.data?.capabilities ?? []

  // 数据源集合/插件可用性变化都会改变能力候选集 → 统一连带失效
  const invalidateSources = () => {
    qc.invalidateQueries({ queryKey: QK.dataSources })
    qc.invalidateQueries({ queryKey: QK.capabilityMatrix })
    qc.invalidateQueries({ queryKey: QK.preferences })
    qc.invalidateQueries({ queryKey: QK.capabilities })
    qc.invalidateQueries({ queryKey: QK.quoteStatus })
  }

  const reload = useMutation({
    mutationFn: api.reloadDataSources,
    onSuccess: () => {
      invalidateSources()
      toast('配置已重新加载', 'success')
    },
  })

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteDataSource(name),
    onSuccess: () => {
      invalidateSources()
      setSelected(BUILTIN)
      setConfirmDelete(null)
      toast('数据源已删除', 'success')
    },
  })

  const switchProvider = useMutation({
    mutationFn: (name: string) => {
      // 一键套用: 该源适配了哪些数据集就接管哪些, 其余回默认
      return api.updateDataProviders(
        buildProviderRouting(
          name,
          allItems.find(s => s.name === name)?.datasets ?? [],
          matrix.data?.capabilities ?? [],
        ),
      )
    },
    onSuccess: (_d, name) => {
      invalidateSources()
      const display = displayName(allItems.find(s => s.name === name)?.display_name || name)
      toast(`已让「${display}」接管其适配的能力`, 'success')
    },
    onError: (e: Error) => toast(`切换失败：${e.message}`, 'error'),
  })

  const editExisting = useMutation({
    mutationFn: (name: string) => api.dataSource(name),
    onSuccess: (_data, name) => setSelected(name),
  })

  const installMut = useMutation({
    mutationFn: (name: string) => api.installPlugin(name),
    onSuccess: (data) => {
      invalidateSources()
      if (data.install_ok) {
        toast('插件依赖安装成功', 'success')
      } else {
        toast(data.install_message || '安装失败', 'error')
      }
    },
    onError: (e: Error) => toast(`安装失败：${e.message}`, 'error'),
  })

  const uninstallMut = useMutation({
    mutationFn: (name: string) => api.uninstallPlugin(name),
    onSuccess: (data) => {
      invalidateSources()
      if (data.uninstall_ok) {
        toast(data.uninstall_message || '已卸载', 'success')
      } else {
        toast(data.uninstall_message || '卸载失败', 'error')
      }
    },
    onError: (e: Error) => toast(`卸载失败：${e.message}`, 'error'),
  })

  // 插件 name → 状态 (供卡片渲染时判断 available/installing 等; 含内置源的清单)
  const pluginMap = new Map(allPlugins.map(p => [p.name, p]))

  // ===== 读数带三格 (设计稿 .readout) =====
  // 「已接通」= 源自身健康可用 (内置源恒可用); 未接入的插件不计入。
  const connectedSources = allItems.filter(s => pluginMap.get(s.name)?.available !== false)
  const usableCaps = matrixCaps.filter(c => c.usable).length
  const pendingSources = Array.from(
    new Map(
      matrixCaps.flatMap(c => c.pending).map(p => [p.name, p]),
    ).values(),
  )

  return (
    <div className="max-w-5xl">
      <h2 className="text-[16px] font-medium text-foreground">数据源</h2>
      <p className="mb-3.5 mt-1 max-w-[64ch] text-[11px] leading-7 text-secondary">
        七项能力各自选一个数据源。路由按能力生效，换源只影响该能力，其他能力保持不动。
      </p>

      {/* ===== 现状速览: 三格读数带 ===== */}
      <div className="readout">
        <div>
          <span className="k">已 接 通 数 据 源</span>
          <span className="v">{connectedSources.length}</span>
          <small>{connectedSources.map(s => displayName(s.display_name)).join(' · ') || '无'}</small>
        </div>
        <div>
          <span className="k">可 用 能 力</span>
          <span className="v">{usableCaps} / {matrixCaps.length}</span>
          <small>{usableCaps === matrixCaps.length ? '全部已分配到可用源' : '部分能力未就绪'}</small>
        </div>
        <div className={pendingSources.length ? 'down' : undefined}>
          <span className="k">待 接 入</span>
          <span className="v">{pendingSources.length}</span>
          <small>
            {pendingSources.length
              ? pendingSources.map(p => `${p.display} · ${p.status?.split('(')[0] ?? '待接入'}`).join(' · ')
              : '无'}
          </small>
        </div>
      </div>

      {/* ===== 上区: 能力路由 (能力为主视图, 点标签切换提供方) ===== */}
      <CapabilityRoutingSection />

      {/* ===== 下区: 数据源 (源为主视图, 配置/接入) ===== */}
      <AnchorWrap highlight={highlight} anchor="data-sources">
      <section>
        <div className="sec-h">
          <span>数据源</span>
          <span className="note">已注册的 {allItems.length} 个提供方</span>
          <span className="sp" />
          <div className="flex shrink-0 items-center gap-2">
            <ExtensionSlot name="settings.data-sources.toolbar" context={{ refresh: invalidateSources, selectSource: setSelected }} compact />
            <button
              type="button"
              onClick={() => reload.mutate()}
              disabled={reload.isPending}
              className="ctl ctl-md ctl-btn inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap border-border/70 bg-surface px-2.5 hover:border-border hover:bg-elevated hover:text-foreground focus-visible:ring-2 focus-visible:ring-accent/60 focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:pointer-events-none disabled:opacity-50"
            >
              <RefreshCw aria-hidden="true" className={`h-3.5 w-3.5 ${reload.isPending ? 'animate-spin' : ''}`} />
              重新加载
            </button>
          </div>
        </div>

        {/* 开发说明是辅助信息, 警告色留给需要处理的异常状态。 */}
        <div className="mb-4 flex items-start gap-2 rounded-btn bg-elevated/40 px-3 py-2.5">
          <Puzzle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
          <div className="min-w-0 break-words text-[11px] leading-relaxed text-secondary">
            <span className="text-secondary">数据源已插件化</span>
            ,接入自有行情?把文档发给 AI 即可自动接入：
            <span className="mx-0.5 rounded bg-elevated/70 px-1 py-px font-mono text-[10px] text-secondary">docs/custom-data-source.md</span>
            (自有 HTTP 接口) ·
            <span className="mx-0.5 rounded bg-elevated/70 px-1 py-px font-mono text-[10px] text-secondary">docs/plugin-development.md</span>
            (插件开发)
          </div>
        </div>

        {/* 数据源网格 - 1px 发丝线格 (设计稿 .srcs): 每格自报 提供/运行/操作 */}
        <div className="srcs grid grid-cols-1 lg:grid-cols-2">
          {allItems.map(item => {
            const servingSet = servingSetOf(item.name)
            const isBuiltin = item.name === BUILTIN
            const servingCount = servingSet.size
            const isSelected = selected === item.name
            const plugin = pluginMap.get(item.name)
            const pluginUnavailable = plugin && !plugin.available
            const dimmed = !!pluginUnavailable && !plugin?.api_key_env && plugin?.origin !== 'external'
            const installing = installMut.isPending && installMut.variables === item.name
            const uninstalling = uninstallMut.isPending && uninstallMut.variables === item.name
            const declared = new Set(item.datasets)
            // 各源按自身声明的数据集展示能力 (内置源不声明财务等未接入能力)
            const chipCaps = matrixCaps.filter(c => declared.has(c.id))
            return (
              <div
                key={item.name}
                onClick={() => {
                  // 外部插件始终可打开管理详情;内置插件沿用 Key 配置入口。
                  if (pluginUnavailable && !plugin?.api_key_env && plugin?.origin !== 'external') return
                  setSelected(item.name)
                  // 只有用户自定义源 (YAML) 才进编辑器; 内置源和插件不可编辑
                  if (customNames.has(item.name)) {
                    editExisting.mutate(item.name)
                  }
                }}
                data-selected={!dimmed && isSelected ? '' : undefined}
                className={`src cursor-pointer transition-colors ${dimmed ? 'is-unavailable' : ''}`}
              >
                {/* 名称行: 服务点 + 名称 + 内置/第三方标识 + 连通状态 */}
                <div className="hd">
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                        pluginUnavailable ? 'bg-muted/30' : servingCount > 0 ? 'bg-accent' : 'bg-transparent border border-muted/40'
                      }`}
                    />
                    <b className="truncate">{displayName(item.display_name)}</b>
                    {!customNames.has(item.name) && !isBuiltin && <span className="st">第三方</span>}
                  </span>
                  {/* 设计稿 27: 内置源就绪时恒为「内置」; 连通看源自身健康(available), 未路由能力不算失联;
                      插件缺 Key 时如实说「缺少 API Key」, 不用笼统的「未就绪」 */}
                  <span className={`st ${pluginUnavailable ? 'no' : (isBuiltin || plugin || servingCount > 0) ? 'ok' : ''}`}>
                    {pluginUnavailable
                      ? (plugin?.api_key_env ? '缺少 API Key' : '未就绪')
                      : isBuiltin
                        ? '内置'
                        : plugin
                          ? '已连通'
                          : servingCount > 0
                            ? '已连通'
                            : '未启用'}
                  </span>
                </div>

                {/* 提供的能力: 服务中 / 已适配 / 积分计费角标 */}
                <div className="meta">
                  <span>提供</span>
                  <em>
                    <CapabilityChips caps={chipCaps} servingSet={servingSet} paid={plugin?.billing === 'points'} />
                  </em>
                </div>

                {/* 运行形态 */}
                <div className="meta">
                  <span>运行</span>
                  <em>{runtimeLabel(item, plugin)}</em>
                </div>

                {/* 底部: 状态提示 + 操作 */}
                <div className="ft">
                  <span className="min-w-0 truncate text-[10px] text-muted/50">
                    {servingCount > 0
                      ? `服务中 ${servingCount} 项能力`
                      : pluginUnavailable
                        ? (plugin?.runtime === 'none' && plugin?.api_key_env
                            ? '点击配置 Key'
                            : isBuiltin
                              ? '缺少依赖，点「安装」补齐'
                              : (plugin?.install_hint || plugin?.status || ''))
                        : ''}
                  </span>
                  <div className="ml-auto flex shrink-0 items-center gap-1">
                    {pluginUnavailable ? (
                      // 安装/卸载按钮只对"纯依赖型"插件(runtime≠none 且无 api_key_env)显示;
                      // 带 Key 配置的插件不可用时, 卡片本身可点击进详情配 Key(如 fuyao 缺 Key 时)。
                      plugin?.origin !== 'external' && plugin?.runtime !== 'none' && !plugin?.api_key_env && (
                        installing ? (
                          <span className="inline-flex items-center gap-1 text-[10px] text-accent">
                            <RefreshCw className="h-2.5 w-2.5 animate-spin" /> 安装中...
                          </span>
                        ) : (
                          <button
                            onClick={(e) => { e.stopPropagation(); installMut.mutate(item.name) }}
                            disabled={installMut.isPending}
                            className="ctl ctl-sm ctl-btn pri px-1.5 disabled:opacity-50"
                          >
                            安装
                          </button>
                        )
                      )
                    ) : (
                      <>
                        <button
                          onClick={(e) => { e.stopPropagation(); switchProvider.mutate(item.name) }}
                          disabled={switchProvider.isPending}
                          className="ctl ctl-sm ctl-btn pri px-1.5 disabled:opacity-50"
                        >
                          套用
                        </button>
                        {/* 内置源是应用自带的行情底座, 不提供卸载入口 */}
                        {plugin && !isBuiltin && plugin?.origin !== 'external' && plugin?.runtime !== 'none' && !plugin?.api_key_env && (
                          uninstalling ? (
                            <RefreshCw className="h-2.5 w-2.5 animate-spin text-muted" />
                          ) : (
                            <button
                              onClick={(e) => { e.stopPropagation(); uninstallMut.mutate(item.name) }}
                              disabled={uninstallMut.isPending}
                              className="ctl ctl-sm ctl-ghost inline-flex items-center px-1.5 text-muted/60 hover:text-danger disabled:opacity-40"
                              title="卸载依赖"
                            >
                              卸载
                            </button>
                          )
                        )}
                      </>
                    )}
                  </div>
                </div>
              </div>
            )
          })}

        </div>

        {/* 新增数据源: 独立在网格之外, 不占发丝线格 */}
        <button
          onClick={() => setSelected('__new__')}
          className={`ctl ctl-sm ctl-btn mt-2 w-full border-dashed px-1.5 flex items-center justify-center gap-1.5 ${
            selected === '__new__'
              ? 'border-accent/50 bg-accent/5 text-accent'
              : 'border-border/50 text-muted hover:text-foreground hover:border-border hover:bg-elevated/30'
          }`}
        >
          <Plus className="h-3.5 w-3.5" />
          新增数据源
        </button>

        {/* 错误提示 */}
        {errors.length > 0 && (
          <div className="mt-3 flex items-start gap-1.5 px-3 py-2 rounded-lg bg-danger/5 border border-danger/20">
            <FileWarning className="h-3.5 w-3.5 text-danger shrink-0 mt-0.5" />
            <div className="text-[11px] text-danger/80 leading-relaxed space-y-0.5">
              {errors.map((err, idx) => (
                <div key={idx}>
                  <span className="font-mono">{err.name || err.path}</span>: {err.errors.join('; ')}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-3 flex items-center gap-3 text-[10px] text-muted/50 flex-wrap">
          <span>芯片：高亮=服务中 · 灰=已适配</span>
          <span className="text-muted/30">·</span>
          <span>单击卡片查看介绍与配置, 点「套用」让该源接管其适配的全部能力</span>
        </div>

      </section>
      </AnchorWrap>

      {/* ===== 下方: 选中源的介绍 + 配置 (单一卡片布局) ===== */}
      <AnimatePresence mode="wait">
        <motion.div
          key={selected}
          className="mt-4"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.15 }}
        >
          {selected === BUILTIN ? (
            <BuiltinDetail
              item={builtin.find(b => b.name === BUILTIN)}
              plugin={pluginMap.get(BUILTIN)}
              active={servingDatasets(BUILTIN).length > 0}
              matrixCaps={matrixCaps}
              servingSet={servingSetOf(BUILTIN)}
            />
          ) : selected === '__new__' || customNames.has(selected) ? (
            selected === '__new__' ? (
              <DataSourceEditor
                key={selected}
                initial={null}
                existingName={undefined}
                onCancel={() => setSelected(BUILTIN)}
                onSaved={() => {
                  invalidateSources()
                  setSelected(BUILTIN)
                }}
                activeName={activeName}
                onActivate={(name) => switchProvider.mutate(name)}
              />
            ) : (
              <DataSourceEditor
                key={selected}
                initial={null}
                existingName={selected}
                onCancel={() => setSelected(BUILTIN)}
                onSaved={() => {
                  invalidateSources()
                  // 强制清除该源的详情缓存, 下次编辑重新拉取最新配置
                  qc.removeQueries({ queryKey: ['data-source-detail', selected] })
                }}
                activeName={activeName}
                onActivate={(name) => switchProvider.mutate(name)}
                onDelete={selectedCustom ? () => setConfirmDelete(selected) : undefined}
              />
            )
          ) : pluginList.find(x => x.name === selected) ? (
            <PluginDetail
              plugin={pluginList.find(x => x.name === selected)!}
              isActive={servingDatasets(selected).length > 0}
              matrixCaps={matrixCaps}
              servingSet={servingSetOf(selected)}
              refresh={invalidateSources}
            />
          ) : null}
        </motion.div>
      </AnimatePresence>

      {/* 删除确认弹窗 */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setConfirmDelete(null)}
          />
          <div className="relative w-[90vw] max-w-[380px] rounded-card border border-border bg-base p-6">
            <h3 className="text-sm font-medium text-foreground mb-2">删除数据源</h3>
            <p className="text-xs text-secondary mb-5">
              确认删除「{customList.find(s => s.name === confirmDelete)?.display_name || confirmDelete}」? 该数据源的配置文件将被移除，此操作不可撤销。
            </p>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="ctl ctl-md ctl-ghost px-2.5 hover:bg-elevated/80 bg-elevated"
              >
                取消
              </button>
              <button
                onClick={() => remove.mutate(confirmDelete)}
                disabled={remove.isPending}
                className="ctl ctl-md ctl-btn soft px-2.5 hover:bg-danger/25 disabled:opacity-50 bg-danger/15"
              >
                {remove.isPending ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** 能力适配表: 全部能力 × 某源的适配状态 (服务中 / 已适配 / —) */
function CapabilityTable({ matrixCaps, declared, servingSet, available = true, paid = false }: {
  matrixCaps: CapabilityRoute[]
  declared: Set<string>
  servingSet: Set<string>
  available?: boolean
  paid?: boolean
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-elevated/20 divide-y divide-border/50">
      {matrixCaps.map(cap => {
        const Icon = CAP_ICON[cap.id] ?? Database
        const serving = servingSet.has(cap.id)
        const declaredCap = declared.has(cap.id)
        return (
          <div key={cap.id} className="flex items-center gap-2.5 px-3 py-1.5">
            <Icon className="h-3.5 w-3.5 text-secondary shrink-0" />
            <div className="flex-1 min-w-0 truncate">
              <span className="text-xs text-foreground">{cap.label}</span>
              {paid && declaredCap && (
                <span className="ml-1.5 rounded bg-warning/15 px-1 py-px text-[9px] font-medium leading-none text-warning">积分</span>
              )}
              <span className="ml-1.5 text-[10px] text-muted/60">{cap.desc}</span>
            </div>
            {serving ? (
              <span className="w-[56px] text-right text-[10px] text-bear inline-flex items-center justify-end gap-0.5 shrink-0">
                <Check className="h-2.5 w-2.5" />服务中
              </span>
            ) : declaredCap ? (
              <span className={`w-[56px] text-right text-[10px] shrink-0 ${available ? 'text-secondary' : 'text-warning/80'}`}>
                {available ? '已适配' : '未就绪'}
              </span>
            ) : (
              <span className="w-[56px] text-right text-[10px] text-muted/30 shrink-0">—</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** 插件详情: 介绍 + 适配能力 + Key 配置合并为单一卡片 */
function PluginDetail({ plugin, isActive, matrixCaps, servingSet, refresh }: {
  refresh: () => void
  plugin: PluginDataSourceItem
  isActive: boolean
  matrixCaps: CapabilityRoute[]
  servingSet: Set<string>
}) {
  const declared = new Set(plugin.datasets)
  return (
    <section className="mt-10 first:mt-0">
      {/* 介绍 */}
      <div className="flex items-start gap-4">
        <div className="h-[44px] w-[44px] rounded-xl bg-accent/10 flex items-center justify-center shrink-0">
          <Zap className="h-[20px] w-[20px] text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <h3 className="text-[16px] leading-6 font-semibold text-foreground">{displayName(plugin.display_name)}</h3>
            <span className="text-[10px] text-muted/50 uppercase tracking-wider">插件 · {plugin.runtime}</span>
            <span className="rounded bg-warning/15 px-1 py-0.5 text-[9px] font-medium leading-none text-warning">第三方</span>
            {isActive && (
              <span className="inline-flex items-center gap-1 text-[10px] text-accent bg-accent/10 px-2 py-1 rounded">
                <Check className="h-2.5 w-2.5" /> 服务中
              </span>
            )}
          </div>
          <PluginMetadata plugin={{ ...plugin, display_name: displayName(plugin.display_name) }} />
          <div className="mt-2">
            <CapabilityChips
              caps={matrixCaps.filter(c => declared.has(c.id))}
              servingSet={servingSet}
              paid={plugin.billing === 'points'}
            />
          </div>
        </div>
      </div>

      <ExtensionSlot name="settings.data-sources.detail-actions" context={{ plugin, refresh }} />

      {/* 主体: 操作 + Key 配置 (左) | 能力适配表 (右) */}
      <div className="mt-5 pt-5 border-t border-border grid grid-cols-1 lg:grid-cols-[1fr_1.15fr] gap-6 items-start">
        <div className="min-w-0">
          {/* 独立状态行仅用于无 Key 配置区的插件; 有 Key 区时「状态」行已展示, 避免重复 */}
          {!plugin.available && !plugin.api_key_env && (
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted">{plugin.status}</span>
            </div>
          )}

          {/* API Key 配置 (声明了 api_key_env 的插件) */}
          {plugin.api_key_env && plugin.enabled !== false && (
            <div className={plugin.available ? 'mt-4' : ''}>
              <PluginKeyConfig plugin={plugin} />
            </div>
          )}
        </div>

        {/* 能力适配表: 全部能力 × 该源适配状态 */}
        <div className="min-w-0">
          <CapabilityTable
            matrixCaps={matrixCaps}
            declared={declared}
            servingSet={servingSet}
            available={plugin.available}
            paid={plugin.billing === 'points'}
          />
        </div>
      </div>
    </section>
  )
}

/** 内置源详情: 介绍 + 能力清单 (内置源随应用分发, 无需 Key 与配置) */
function BuiltinDetail({ item, plugin, active, matrixCaps, servingSet }: {
  item?: DataSourceItem
  plugin?: PluginDataSourceItem
  active: boolean
  matrixCaps: CapabilityRoute[]
  servingSet: Set<string>
}) {
  const declared = new Set(item?.datasets ?? [])
  return (
    <section className="mt-10 first:mt-0">
      {/* 介绍 */}
      <div className="flex items-start gap-4">
        <div className="h-[44px] w-[44px] rounded-xl bg-accent/10 flex items-center justify-center shrink-0">
          <Database className="h-[20px] w-[20px] text-accent" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <h3 className="text-[16px] leading-6 font-semibold text-foreground">{BUILTIN_LABEL}</h3>
            <span className="rounded bg-elevated px-1.5 py-0.5 text-[10px] font-medium text-secondary">内置</span>
            {active && (
              <span className="inline-flex items-center gap-1 text-[10px] text-accent bg-accent/10 px-2 py-1 rounded">
                <Check className="h-2.5 w-2.5" /> 服务中
              </span>
            )}
          </div>
          {plugin && <PluginMetadata plugin={{ ...plugin, display_name: displayName(plugin.display_name) }} />}
          <div className="mt-2">
            <CapabilityChips caps={matrixCaps.filter(c => declared.has(c.id))} servingSet={servingSet} />
          </div>
        </div>
      </div>

      {/* 能力清单: 内置源无需配置, 只列它提供哪些能力 */}
      <div className="mt-5 pt-5 border-t border-border">
        <CapabilityTable
          matrixCaps={matrixCaps}
          declared={declared}
          servingSet={servingSet}
          available={plugin?.available ?? true}
        />
      </div>
    </section>
  )
}
