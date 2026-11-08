// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Modal } from './Modal'

let root: Root
let container: HTMLDivElement
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.useFakeTimers()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})
afterEach(() => { act(() => root.unmount()); container.remove(); vi.useRealTimers() })

/** 关闭请求先跑退场动画, onClose 在动画收尾时才回调。 */
async function flushClose() {
  await act(async () => { await vi.advanceTimersByTimeAsync(200) })
}

it('an inactive parent is hidden from accessibility and does not consume the child Escape', async () => {
  const parentClose = vi.fn()
  const childClose = vi.fn()
  act(() => root.render(<><Modal active={false} onClose={parentClose} ariaLabel="父窗口"><button>导出</button></Modal><Modal onClose={childClose} ariaLabel="资源传输"><button>关闭</button></Modal></>))
  expect(container.querySelector('[aria-label="父窗口"]')?.getAttribute('aria-hidden')).toBe('true')
  act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  expect(parentClose).not.toHaveBeenCalled()
  await flushClose()
  expect(childClose).toHaveBeenCalledTimes(1)
})

it('the parent resumes its shortcuts after the child closes', async () => {
  const close = vi.fn()
  act(() => root.render(<Modal active={false} onClose={close} ariaLabel="父窗口"><button>导出</button></Modal>))
  act(() => root.render(<Modal active onClose={close} ariaLabel="父窗口"><button>导出</button></Modal>))
  act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
  await flushClose()
  expect(close).toHaveBeenCalledTimes(1)
  expect(container.querySelector('[aria-label="父窗口"]')?.hasAttribute('aria-hidden')).toBe(false)
})
