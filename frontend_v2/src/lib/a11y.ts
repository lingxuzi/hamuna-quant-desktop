import { useEffect, useRef, type KeyboardEvent } from 'react'

/**
 * 让非 button 元素也能被键盘触发。
 *
 * 场景: 表格行 / 卡片 / 芯片这类"整块可点"的元素用 onClick 实现动作,
 * 鼠标能点但键盘触达不到 (WCAG 2.1.1)。这里补上 tabIndex + Enter/Space,
 * 行为与 onClick 一致。
 *
 * 用法:
 *   <tr onClick={open} {...keyboardActivate(open)}>
 *
 * 表格行不要加 role="button" —— 那会覆盖 tr 的 row 语义, 破坏表格结构。
 * 非表格元素 (卡片 / 芯片) 另配 role="button" 让读屏软件播报可点击。
 */
export function keyboardActivate(
  handler: () => void,
  options: { stopPropagation?: boolean } = {},
) {
  return {
    tabIndex: 0,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key !== 'Enter' && e.key !== ' ') return
      // 事件来自行内的嵌套控件 (按钮/输入框) 时不抢它的键盘行为。
      if (e.target !== e.currentTarget) return
      e.preventDefault()
      if (options.stopPropagation) e.stopPropagation()
      handler()
    },
  }
}

/**
 * ESC 关闭浮层 (气泡 / 提示条 / 下拉)。
 *
 * 用法:
 *   useEscapeClose(open, () => setOpen(false))
 *
 * open 为 false 时不监听。onEscape 用 ref 兜住, 传内联箭头也不会反复重订阅。
 */
export function useEscapeClose(open: boolean, onEscape: () => void) {
  const ref = useRef(onEscape)
  useEffect(() => { ref.current = onEscape }, [onEscape])

  useEffect(() => {
    if (!open) return
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') ref.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
}
