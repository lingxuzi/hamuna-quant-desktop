/**
 * 看板「添加组件」面板 — 内置组件列表 + 外部链接表单。
 *
 * 内置组件单实例: 已在布局中的禁用; 外部链接可加多个实例(URL 经消毒)。
 * 弹层 portal 到 body + fixed 定位锚定按钮下方 — 按钮位于页面头部时,
 * absolute 会被祖先容器的层叠上下文/裁剪压住, portal 后彻底脱离;
 * 左缘按视口钳位避免右置按钮的弹层溢出屏外。点击面板外/Esc/滚动 关闭。
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Plus } from 'lucide-react'
import { cn } from '@/lib/cn'
import { sanitizeExtUrl, type WidgetType } from './layout'
import { WIDGET_DEFS } from './registry'

const PANEL_W = 288 // w-72

export function AddWidgetPanel({
  placedTypes,
  onAdd,
}: {
  placedTypes: Set<WidgetType>
  onAdd: (t: WidgetType, props?: Record<string, string>) => void
}) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [urlError, setUrlError] = useState('')
  // 开合状态存 ref 同步给文档级事件: effect 只在挂载时绑定一次, 不随 open 重绑 —
  // 重渲染打断拖拽时会自动补发一次 mousedown, 若此时是「打开后重绑」, 新弹层会被立刻关掉
  const openRef = useRef(false)
  const anchorRef = useRef<{ left: number; top: number } | null>(null)
  // 内容渲染读 anchorRef, React 看不到它的变化; 位置算好升一次版本号补一帧
  const [anchorVersion, setAnchorVersion] = useState(0)
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // 锚定位置在打开时与视口尺寸变化时重算
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect()
      if (!r) return
      // 右缘钳位: 面板不溢出视口 (留 8px 余量)
      const left = Math.max(8, Math.min(r.left, window.innerWidth - PANEL_W - 8))
      anchorRef.current = { left, top: r.bottom + 6 }
      setAnchorVersion(v => v + 1)
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open])

  useEffect(() => { openRef.current = open }, [open])

  useEffect(() => {
    // 只认主键: 拖拽结束后浏览器补发的次键 mousedown 落在视口左上角, 会把刚打开的弹层立刻关掉
    const onDocMouseDown = (e: MouseEvent) => {
      if (!openRef.current || e.button > 0) return
      const t = e.target as Node
      if (btnRef.current?.contains(t) || panelRef.current?.contains(t)) return
      // 打断拖拽产生的合成 mousedown(目标不在文档树内 / 按钮已停用)不当作「点外部」
      if (!document.contains(t) || (btnRef.current?.disabled ?? false)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    // 视口滚动即关: 面板是 fixed 锚定, 页面滚动后位置失去意义;
    // 面板内部列表的滚动除外 (capture 捕获任意滚动容器)
    const onScroll = (e: Event) => {
      if (!openRef.current) return
      if (panelRef.current && e.target instanceof Node && panelRef.current.contains(e.target)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDocMouseDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [])

  const addExtLink = () => {
    const clean = sanitizeExtUrl(url)
    if (!clean) {
      setUrlError('仅支持 http(s) 链接, 且不能是本站地址')
      return
    }
    onAdd('ext-link', { title: title.trim() || new URL(clean).hostname, url: clean })
    setTitle('')
    setUrl('')
    setUrlError('')
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen(v => !v)}
        className={cn(
          'inline-flex items-center gap-1 rounded-btn border px-2 py-1 text-[11px] transition-colors',
          open
            ? 'border-accent/50 bg-accent/10 text-accent'
            : 'border-border bg-elevated text-secondary hover:text-foreground hover:border-accent/40',
        )}
      >
        <Plus className="h-3 w-3" />添加组件
      </button>

      {open && anchorRef.current && anchorVersion > 0 && createPortal(
        <div
          ref={panelRef}
          style={{ left: anchorRef.current.left, top: anchorRef.current.top, width: PANEL_W }}
          className="fixed z-[9999] leaf-cell p-2 shadow-2xl shadow-scrim/40"
        >
          <div className="mb-1.5 px-0.5 text-[10px] font-medium text-muted">内置组件</div>
          <div className="max-h-56 space-y-0.5 overflow-y-auto">
            {WIDGET_DEFS.filter(d => d.id !== 'ext-link').map(d => {
              // optIn 组件可多例, 不因已放置而禁用
              const placed = placedTypes.has(d.id) && !d.optIn
              return (
                <button
                  key={d.id}
                  type="button"
                  disabled={placed}
                  onClick={() => { onAdd(d.id); if (!d.optIn) setOpen(false) }}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors',
                    placed
                      ? 'cursor-default text-muted/50'
                      : 'cursor-pointer text-secondary hover:bg-elevated hover:text-foreground',
                  )}
                  title={placed ? '已在布局中' : undefined}
                >
                  <d.icon className="h-3.5 w-3.5 shrink-0 text-accent" />
                  <span className="flex-1 truncate">{d.label}</span>
                  {placed && <span className="text-[9px]">已添加</span>}
                </button>
              )
            })}
          </div>

          <div className="mt-2 border-t border-border/60 pt-2">
            <div className="mb-1.5 px-0.5 text-[10px] font-medium text-muted">外部链接(可加多个)</div>
            <div className="space-y-1.5">
              <input
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="标题, 如: 东财行情"
                maxLength={30}
                className="w-full rounded-btn bg-base px-2 py-1.5 text-xs text-foreground ring-1 ring-border/40 placeholder:text-muted/40 focus:outline-none focus:ring-2 focus:ring-accent/40"
              />
              <input
                value={url}
                onChange={e => { setUrl(e.target.value); setUrlError('') }}
                placeholder="https://… (仅 http/https)"
                className="w-full rounded-btn bg-base px-2 py-1.5 font-mono text-xs text-foreground ring-1 ring-border/40 placeholder:text-muted/40 focus:outline-none focus:ring-2 focus:ring-accent/40"
              />
              {urlError && <div className="text-[10px] text-danger">{urlError}</div>}
              <button
                type="button"
                onClick={addExtLink}
                className="w-full rounded-btn bg-accent/15 px-2 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/25"
              >
                添加到看板
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
