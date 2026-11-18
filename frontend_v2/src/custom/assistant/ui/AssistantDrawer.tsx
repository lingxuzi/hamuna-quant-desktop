/**
 * AI 助手面板 — 从页面最右缘滑入(right: 0), 默认 720px 宽,
 * 左缘拖拽可调宽并持久化到 localStorage。
 *
 * 打开时默认在页面主体上盖一层毛玻璃遮罩, 点击遮罩关闭助手 (聚焦提问);
 * 抽屉标题栏「助手设置」可关闭遮罩, 恢复非模态体验 (边看行情边问)。
 * 拖拽调宽沿用仓库 60fps 手法: 拖动期间直接改 DOM style.width, 松手才
 * 提交 React state + 持久化。视觉全部走设计令牌; 动画用全站统一缓动。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowDown,
  History,
  Plus,
  Settings2,
  SlidersHorizontal,
  Square,
  Trash2,
  X,
} from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { cn } from '@/lib/cn'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { usePreferences } from '@/lib/useSharedQueries'
import { Switch } from '@/components/Switch'
import {
  fetchAssistantStatus,
  fetchAssistantSuggests,
  fetchExperts,
  type AssistantStatus,
  type ExpertLayout,
  type QuickSuggest,
} from '../client'
import {
  closeAssistant,
  deleteSession,
  newSession,
  retryLast,
  selectSession,
  sendMessage,
  stopSending,
  toggleMention,
  useAssistantStore,
  MAX_MENTIONS,
} from '../store'
import { AssistantMessageView } from './messages'
import { ExpertPicker } from './ExpertPicker'

const EASE_SMOOTH: [number, number, number, number] = [0.16, 1, 0.3, 1]

const WIDTH_STORAGE_KEY = 'assistant.width.v1'
const DEFAULT_WIDTH = 720
const MIN_WIDTH = 480

function loadWidth(): number {
  try {
    const raw = localStorage.getItem(WIDTH_STORAGE_KEY)
    const value = raw ? Number.parseInt(raw, 10) : Number.NaN
    return Number.isFinite(value) && value >= MIN_WIDTH ? value : DEFAULT_WIDTH
  } catch {
    return DEFAULT_WIDTH
  }
}

function saveWidth(width: number) {
  try {
    localStorage.setItem(WIDTH_STORAGE_KEY, String(Math.round(width)))
  } catch { /* 存储不可用时仅内存态 */ }
}

function clampWidth(width: number): number {
  // 右缘贴齐, 至少给页面主体留 64px 上下文。
  const maxWidth = Math.max(MIN_WIDTH, window.innerWidth - 64)
  return Math.min(Math.max(width, MIN_WIDTH), maxWidth)
}

/** 独立页「AI 助手」(design/v2/24): 与抽屉同一套会话/流式/工具, 内联铺满主区。 */
export function AssistantPage() {
  const { activeId, sending, sessions } = useAssistantStore()
  const active = sessions.find(s => s.id === activeId) ?? null
  return (
    <div className="drv">
      <DrawerPanel mode="page" sending={sending} messages={active?.messages ?? []} />
    </div>
  )
}

export function AssistantDrawer() {
  const { open, sending, sessions, activeId } = useAssistantStore()
  const { data: prefs } = usePreferences()
  const active = sessions.find(s => s.id === activeId) ?? null
  const messages = active?.messages ?? []
  // 毛玻璃遮罩默认开启; 偏好未加载完成前按默认渲染, 避免开合闪变。
  const maskEnabled = prefs?.assistant_mask_enabled ?? true

  return createPortal(
    <AnimatePresence>
      {open && maskEnabled && <DrawerBackdrop key="assistant-backdrop" />}
      {open && (
        <DrawerPanel key="assistant-drawer" sending={sending} messages={messages} />
      )}
    </AnimatePresence>,
    // #root (缩放层): 抽屉随界面缩放, 且与 Modal 同层叠上下文保持 z 序设计。
    document.getElementById('root') ?? document.body,
  )
}

type DrawerMessages = ReturnType<typeof useAssistantStore>['sessions'][number]['messages']

/** 毛玻璃遮罩: 盖住页面主体, 点击任意处关闭助手。
 *  与抽屉同 portal (随界面缩放), z 序低于抽屉一级 (z-[59] < z-[60])。 */
function DrawerBackdrop() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.26, ease: EASE_SMOOTH }}
      onClick={closeAssistant}
      className="fixed inset-0 z-[59] bg-black/40 backdrop-blur-sm"
      aria-hidden="true"
    />
  )
}

function DrawerPanel({
  sending,
  messages,
  mode = 'drawer',
}: {
  sending: boolean
  messages: DrawerMessages
  /** page: 独立页内联铺满 (design/v2/24); drawer: 右缘滑出面板 */
  mode?: 'drawer' | 'page'
}) {
  const { sessions, activeId } = useAssistantStore()
  const active = sessions.find(s => s.id === activeId) ?? null
  const [status, setStatus] = useState<AssistantStatus | null>(null)
  const [suggests, setSuggests] = useState<QuickSuggest[]>([])
  const isPage = mode === 'page'
  const [width, setWidth] = useState(() => clampWidth(loadWidth()))
  const panelRef = useRef<HTMLElement>(null)
  const draggingRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    fetchAssistantStatus().then(s => { if (!cancelled) setStatus(s) }).catch(() => {})
    fetchAssistantSuggests().then(list => { if (!cancelled) setSuggests(list) }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  // 窗口缩放后重新夹取宽度, 保证面板不越出视口。
  useEffect(() => {
    const onResize = () => setWidth(prev => clampWidth(prev))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 拖拽期间直接改 DOM(60fps), 松手才提交 state + 持久化。
  const onHandlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    draggingRef.current = true
  }
  const onHandlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return
    const el = panelRef.current
    if (el) el.style.width = `${clampWidth(window.innerWidth - event.clientX)}px`
  }
  const onHandlePointerEnd = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return
    draggingRef.current = false
    const finalWidth = clampWidth(window.innerWidth - event.clientX)
    setWidth(finalWidth)
    saveWidth(finalWidth)
  }

  return (
    <motion.aside
      ref={panelRef}
      initial={isPage ? { opacity: 0 } : { x: 32, opacity: 0 }}
      animate={isPage ? { opacity: 1 } : { x: 0, opacity: 1 }}
      exit={isPage ? { opacity: 0 } : { x: 32, opacity: 0 }}
      transition={{ duration: 0.26, ease: EASE_SMOOTH }}
      style={isPage ? undefined : { right: 0, width }}
      className={cn(
        'flex min-w-0 flex-col',
        isPage ? 'min-h-0 flex-1' : 'drv fixed inset-y-0 z-[60] shadow-2xl',
      )}
      role="complementary"
      aria-label="AI 助手"
    >
      <DrawerHeader status={status} sessions={sessions} activeId={activeId} sending={sending} hideClose={isPage} />
      <MessageList messages={messages} sending={sending} status={status} suggests={suggests} />
      <InputArea
        sending={sending}
        blocked={status ? !status.supports_tools : false}
        isPage={isPage}
        mentions={active?.mentions ?? []}
      />

      {/* 左缘拖拽调宽 (抽屉模式) */}
      {!isPage && <div
        role="separator"
        aria-orientation="vertical"
        aria-label="调整宽度"
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={onHandlePointerEnd}
        onLostPointerCapture={onHandlePointerEnd}
        className="group/handle absolute inset-y-0 left-0 z-10 w-1.5 cursor-col-resize touch-none"
        title="拖拽调整宽度"
      >
        <span className="absolute left-1/2 top-1/2 h-10 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/50 opacity-0 transition-opacity duration-150 ease-smooth group-hover/handle:opacity-100" />
      </div>}
    </motion.aside>
  )
}

function DrawerHeader({
  status,
  sessions,
  activeId,
  sending,
  hideClose = false,
}: {
  status: AssistantStatus | null
  sessions: ReturnType<typeof useAssistantStore>['sessions']
  activeId: string
  sending: boolean
  /** 独立页模式: 没有抽屉可关, 不显示关闭按钮 */
  hideClose?: boolean
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  return (
    <div className="drv-hd">
      <b>AI 助手</b>
      {status?.model && (
        <span className="mdl" title={`模型: ${status.model}\n供应商: ${status.provider ?? '-'}`}>
          {status.model}
        </span>
      )}
      <span className={`ok${status?.configured ? '' : ' off'}`}>{status?.configured ? '● 已连接' : '● 未连接'}</span>
      <div className="flex shrink-0 items-center gap-0.5">
        <div className="relative">
          <IconButton title="助手设置" onClick={() => { setSettingsOpen(v => !v); setMenuOpen(false) }} disabled={sending}>
            <SlidersHorizontal className="h-3.5 w-3.5" />
          </IconButton>
          {settingsOpen && <AssistantSettingsMenu onDone={() => setSettingsOpen(false)} />}
        </div>
        <div className="relative">
          <IconButton title="历史会话" onClick={() => { setMenuOpen(v => !v); setSettingsOpen(false) }} disabled={sending}>
            <History className="h-3.5 w-3.5" />
          </IconButton>
          {menuOpen && <SessionMenu sessions={sessions} activeId={activeId} onDone={() => setMenuOpen(false)} />}
        </div>
        {/* 独立页的「新对话」在页头工具条上, 面板内不再重复 */}
        {!hideClose && (
          <IconButton title="新对话" onClick={() => { newSession(); setMenuOpen(false); setSettingsOpen(false) }} disabled={sending}>
            <Plus className="h-3.5 w-3.5" />
          </IconButton>
        )}
        {!hideClose && (
          <IconButton title="关闭 (Esc)" onClick={closeAssistant}>
            <X className="h-3.5 w-3.5" />
          </IconButton>
        )}
      </div>
    </div>
  )
}

/** 助手设置: 毛玻璃遮罩 + 动作工具免确认开关。
 *
 * 毛玻璃遮罩默认开启: 打开时页面盖遮罩, 点击遮罩关闭助手; 关闭后恢复
 * 非模态 (边看行情边问)。免确认打开后 create_signal_strategy /
 * create_strategy / run_backtest / add_to_watchlist / sync_data 不再逐次
 * 弹确认卡, 直接执行。偏好存后端 preferences, 与设置页同源。
 */
function AssistantSettingsMenu({ onDone }: { onDone: () => void }) {
  const prefs = usePreferences()
  const qc = useQueryClient()
  const enabled = prefs.data?.assistant_auto_approve_actions ?? false
  const save = useMutation({
    mutationFn: (next: boolean) => api.updateAssistantAutoApprove(next),
    onSuccess: () => { qc.invalidateQueries({ queryKey: QK.preferences }) },
  })
  const maskEnabled = prefs.data?.assistant_mask_enabled ?? true
  const saveMask = useMutation({
    mutationFn: (next: boolean) => api.updateAssistantMask(next),
    onSuccess: () => { qc.invalidateQueries({ queryKey: QK.preferences }) },
  })

  return (
    <div className="absolute right-0 top-9 z-20 w-72 bg-surface border border-border p-3 shadow-xl">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-medium text-foreground">动作工具免确认</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted">
            打开后创建信号/策略、回测、加自选、数据补全不再逐次弹确认卡, 直接执行。
          </div>
        </div>
        <Switch
          className="mt-0.5"
          checked={enabled}
          onChange={next => save.mutate(next)}
          disabled={save.isPending}
          label="动作工具免确认"
        />
      </div>
      {enabled && (
        <div className="mt-2 rounded-btn bg-warning/10 px-2 py-1.5 text-[10px] leading-relaxed text-warning">
          已开启: 助手会不经确认直接写入。轮次检查点仍然有效 (防止单轮无限调用)。
        </div>
      )}
      <div className="mt-3 flex items-start justify-between gap-2 border-t border-border/50 pt-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-foreground">毛玻璃遮罩</div>
          <div className="mt-1 text-[11px] leading-relaxed text-muted">
            打开时页面盖毛玻璃遮罩, 点击遮罩处关闭助手; 关闭后非模态, 可边看行情边问。
          </div>
        </div>
        <Switch
          className="mt-0.5"
          checked={maskEnabled}
          onChange={next => saveMask.mutate(next)}
          disabled={saveMask.isPending}
          label="毛玻璃遮罩"
        />
      </div>
      <div className="mt-2 flex items-center justify-between border-t border-border/50 pt-2">
        <span className="text-[10px] text-muted">更多 AI 配置在设置页</span>
        <button
          type="button"
          onClick={onDone}
          className="ctl ctl-sm ctl-ghost px-1.5 hover:bg-elevated hover:text-foreground"
        >
          关闭
        </button>
      </div>
    </div>
  )
}

/** 会话时间: 今天/昨天 + HH:MM, 年内 MM-DD HH:MM, 跨年带年份 */
function formatSessionTime(ts: number): string {
  const d = new Date(ts)
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`
  const yesterday = new Date(now.getTime() - 86_400_000)
  if (d.toDateString() === yesterday.toDateString()) return `昨天 ${hm}`
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return d.getFullYear() === now.getFullYear() ? `${md} ${hm}` : `${d.getFullYear()}-${md}`
}

function SessionMenu({
  sessions,
  activeId,
  onDone,
}: {
  sessions: ReturnType<typeof useAssistantStore>['sessions']
  activeId: string
  onDone: () => void
}) {
  return (
    <>
      <div className="fixed inset-0 z-[61]" onClick={onDone} />
      <div className="absolute right-0 top-full z-[62] mt-1 max-h-72 w-60 overflow-y-auto bg-surface border border-border p-1 shadow-xl">
        {sessions.length === 0 && (
          <div className="px-2 py-1.5 text-xs text-muted">暂无历史会话</div>
        )}
        {sessions.map(session => (
          <div
            key={session.id}
            className={cn(
              'group flex items-center gap-1 rounded-btn px-2 py-1.5 text-xs transition-colors duration-150 ease-smooth',
              session.id === activeId ? 'bg-elevated text-foreground' : 'text-secondary hover:bg-elevated/60 hover:text-foreground',
            )}
          >
            <button
              type="button"
              className="min-w-0 flex-1 cursor-pointer text-left"
              onClick={() => { selectSession(session.id); onDone() }}
              title={session.title}
            >
              <span className="block truncate">{session.title || '新对话'}</span>
              <span className="block text-[10px] leading-tight text-muted">
                {formatSessionTime(session.createdAt)}
              </span>
            </button>
            <button
              type="button"
              className="hidden cursor-pointer text-muted transition-colors duration-150 ease-smooth hover:text-danger group-hover:block"
              onClick={() => deleteSession(session.id)}
              title="删除会话"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </>
  )
}

function IconButton({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string
  onClick: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className="ctl ctl-sm ctl-ghost flex w-[24px] px-0 items-center justify-center duration-150 ease-smooth hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  )
}

function MessageList({
  messages,
  sending,
  status,
  suggests,
}: {
  messages: DrawerMessages
  sending: boolean
  status: AssistantStatus | null
  suggests: QuickSuggest[]
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  const [showJump, setShowJump] = useState(false)

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [messages])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    pinnedRef.current = distance < 80
    setShowJump(distance >= 240)
  }

  const jumpToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scrollRef} onScroll={onScroll} className="drv-bd h-full space-y-4 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState status={status} suggests={suggests} />
        ) : (
          messages.map(message => (
            <AssistantMessageView key={message.id} message={message} onRetry={sending ? undefined : retryLast} />
          ))
        )}
      </div>
      <AnimatePresence>
        {showJump && (
          <motion.button
            type="button"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.15, ease: EASE_SMOOTH }}
            onClick={jumpToBottom}
            className="absolute bottom-3 left-1/2 flex h-[28px] w-[28px] -translate-x-1/2 cursor-pointer items-center justify-center rounded-full border border-border bg-elevated text-secondary shadow-md transition-colors duration-150 ease-smooth hover:text-foreground"
            title="回到底部"
          >
            <ArrowDown className="h-3.5 w-3.5" />
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  )
}

/** 空会话建议分组 (后端 suggests 的 group 字段驱动, 未分组的归入「更多」)。 */
function EmptyState({ status, suggests }: { status: AssistantStatus | null; suggests: QuickSuggest[] }) {
  const navigate = useNavigate()

  if (status && !status.configured) {
    return (
      <div className="rounded-card border border-warning/30 bg-warning/5 p-4 text-sm">
        <div className="flex items-center gap-1.5 font-medium text-warning">
          <Settings2 className="h-[16px] w-[16px]" />
          AI 未配置
        </div>
        <p className="mt-1.5 text-secondary">AI 助手需要先配置 AI 供应商和 API Key 才能对话。</p>
        <button
          type="button"
          onClick={() => navigate('/settings?tab=ai')}
          className="ctl ctl-md ctl-btn pri mt-2 px-2.5 duration-150 ease-smooth hover:bg-accent/90"
        >
          去设置页配置
        </button>
      </div>
    )
  }

  if (status && !status.supports_tools) {
    return (
      <div className="rounded-card border border-warning/30 bg-warning/5 p-4 text-sm">
        <div className="flex items-center gap-1.5 font-medium text-warning">
          <Settings2 className="h-[16px] w-[16px]" />
          当前供应商不支持工具调用
        </div>
        <p className="mt-1.5 text-secondary">
          AI 助手依赖工具调用能力({status.provider} 不支持), 请在设置页切换为 OpenAI 兼容模型。
        </p>
        <button
          type="button"
          onClick={() => navigate('/settings?tab=ai')}
          className="ctl ctl-md ctl-btn pri mt-2 px-2.5 duration-150 ease-smooth hover:bg-accent/90"
        >
          去设置页调整
        </button>
      </div>
    )
  }

  if (!suggests.length) {
    return (
      <p className="pt-8 text-center text-xs text-muted">
        问我任何关于行情、个股、策略、扩展数据或数据能力的问题。
      </p>
    )
  }

  // 按后端 group 字段分区, 保持原始顺序; 未分组的建议归入「更多」。
  const groups = new Map<string, QuickSuggest[]>()
  for (const suggest of suggests) {
    const key = suggest.group || '更多'
    groups.set(key, [...(groups.get(key) ?? []), suggest])
  }

  return (
    <div>
      <p className="hello">问一句，它自己去查。</p>
      <p className="hello-sub">
        助手能调用本地数据与工具：拉行情、查财务、跑回测、看数据完整性。执行有副作用的操作（下单、建策略）会先弹确认卡，你点了才动。
      </p>

      {[...groups.entries()].map(([group, items]) => (
        <div key={group} className="sg-g">
          <h4>{group}</h4>
          <div className="sg-list">
            {items.map(suggest => (
              <button
                key={suggest.id}
                type="button"
                title={suggest.prompt}
                onClick={() => sendMessage(suggest.prompt)}
              >
                {suggest.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function InputArea({
  sending,
  blocked,
  isPage,
  mentions,
}: {
  sending: boolean
  blocked: boolean
  isPage: boolean
  /** 本条消息引用的专家 (chip) —— 发出即清空, 下一条要继续用得重新选。 */
  mentions: string[]
}) {
  const [text, setText] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [catalog, setCatalog] = useState<ExpertLayout | null>(null)
  const [limitHit, setLimitHit] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  /** 选完专家后光标要落回 @ 所在处 —— 受控输入重写值会把光标弹到末尾。 */
  const caretRef = useRef<number | null>(null)

  useLayoutEffect(() => {
    const el = textareaRef.current
    const at = caretRef.current
    if (!el || at === null) return
    caretRef.current = null
    el.focus()
    el.setSelectionRange(at, at)
  }, [text])

  const canSend = !sending && !blocked && text.trim().length > 0

  // 专家目录: 第一次打开选择面板 (或已有引用) 时才拉, 不常驻请求。
  // 已有引用的会话要目录才显示中文名 —— 引用存的是 ASCII 标识。
  useEffect(() => {
    if ((!pickerOpen && !mentions.length) || catalog) return
    let cancelled = false
    fetchExperts()
      .then(layout => { if (!cancelled) setCatalog(layout) })
      .catch(() => { if (!cancelled) setCatalog({ categories: [], experts: [] }) })
    return () => { cancelled = true }
  }, [pickerOpen, mentions.length, catalog])

  /** 专家的中文显示名; 目录还没到就退回标识, 不能空着。 */
  const titleOf = (name: string) => catalog?.experts.find(e => e.name === name)?.title || name

  /** 光标前最近一个 @ (其后不能有空白) —— 命中就开选择面板。 */
  const detectMention = (value: string, caret: number): string | null => {
    const before = value.slice(0, caret)
    const at = before.lastIndexOf('@')
    if (at < 0) return null
    const typed = before.slice(at + 1)
    return /\s/.test(typed) ? null : typed
  }

  const submit = () => {
    if (!canSend) return
    sendMessage(text)
    setText('')
    setPickerOpen(false)
    setQuery('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape' && pickerOpen) {
      e.preventDefault()
      setPickerOpen(false)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  const autoGrow = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`
  }

  /** 选中专家: 抹掉输入里的 @xxx, 换成输入区上方的 chip。 */
  const pick = (name: string) => {
    const el = textareaRef.current
    const caret = el?.selectionStart ?? text.length
    const before = text.slice(0, caret)
    const at = before.lastIndexOf('@')
    if (at >= 0) {
      const next = text.slice(0, at) + text.slice(caret)
      caretRef.current = at
      setText(next)
    }
    setPickerOpen(false)
    setQuery('')
    if (!mentions.includes(name) && mentions.length >= MAX_MENTIONS) {
      setLimitHit(true)
      return
    }
    setLimitHit(false)
    toggleMention(name)
  }

  return (
    <div className="drv-ft">
      {mentions.length > 0 && (
        <div className="drv-mentions basis-full">
          {mentions.map(name => (
            <span key={name} className="drv-mention">
              <span>{titleOf(name)}</span>
              <button
                type="button"
                onClick={() => toggleMention(name)}
                title={`取消引用 ${titleOf(name)}`}
                aria-label={`取消引用 ${titleOf(name)}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          {limitHit && <span className="drv-mention-limit">最多同时引用 {MAX_MENTIONS} 个专家</span>}
        </div>
      )}
      {pickerOpen && catalog && (
        <div onMouseDown={e => e.preventDefault()}>
          <ExpertPicker
            categories={catalog.categories}
            experts={catalog.experts}
            query={query}
            mentions={mentions}
            onPick={pick}
          />
        </div>
      )}
      <textarea
        ref={textareaRef}
        value={text}
        rows={1}
        disabled={blocked}
        placeholder={blocked ? '请先在设置页配置 AI' : '问点什么，或者选上面一条；输入 @ 可引用专家'}
        onChange={e => {
          setText(e.target.value)
          autoGrow()
          const typed = detectMention(e.target.value, e.target.selectionStart)
          setQuery(typed ?? '')
          setPickerOpen(typed !== null)
          setLimitHit(false)
        }}
        onBlur={() => setPickerOpen(false)}
        onKeyDown={onKeyDown}
        className="inp max-h-36"
      />
      {sending ? (
        <button
          type="button"
          onClick={stopSending}
          title="停止生成"
          aria-label="停止生成"
          className="send flex shrink-0 items-center justify-center px-3"
        >
          <Square className="h-[12px] w-[12px] fill-current" />
        </button>
      ) : (
        <button type="button" onClick={submit} disabled={!canSend} className="send shrink-0">
          发 送
        </button>
      )}
      <kbd className="shrink-0">⌘K</kbd>
      <p className="basis-full text-[10px] leading-relaxed text-muted">
        回答基于本地数据, 不构成投资建议{isPage ? '' : ' · 左缘拖拽调宽'}
      </p>
    </div>
  )
}
