// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { keyboardActivate, useEscapeClose } from './a11y'

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

/** 表格行: 点击与键盘都触发, 行内嵌套控件不抢键盘行为 */
function RowHarness({ onOpen }: { onOpen: () => void }) {
  return (
    <table>
      <tbody>
        <tr onClick={onOpen} {...keyboardActivate(onOpen)} data-row>
          <td>
            <button type="button" onClick={e => e.stopPropagation()} data-inner>领涨股</button>
          </td>
        </tr>
      </tbody>
    </table>
  )
}

it('Enter / Space 触发行, 其他键不触发', async () => {
  const onOpen = vi.fn()
  await act(async () => root.render(<RowHarness onOpen={onOpen} />))
  const row = host.querySelector<HTMLElement>('[data-row]')!

  await act(async () => row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
  expect(onOpen).toHaveBeenCalledTimes(1)

  await act(async () => row.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true })))
  expect(onOpen).toHaveBeenCalledTimes(2)

  await act(async () => row.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true })))
  expect(onOpen).toHaveBeenCalledTimes(2)
})

it('行内嵌套控件按 Enter 时不触发行自身的处理', async () => {
  const onOpen = vi.fn()
  await act(async () => root.render(<RowHarness onOpen={onOpen} />))
  const inner = host.querySelector<HTMLElement>('[data-inner]')!

  await act(async () => inner.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
  expect(onOpen).not.toHaveBeenCalled()
})

function EscapeHarness({ open, onEscape }: { open: boolean; onEscape: () => void }) {
  useEscapeClose(open, onEscape)
  return <div>浮层</div>
}

it('ESC 关闭浮层: 打开时触发, 关闭时不触发', async () => {
  const onEscape = vi.fn()
  await act(async () => root.render(<EscapeHarness open onEscape={onEscape} />))
  await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })) })
  expect(onEscape).toHaveBeenCalledTimes(1)

  await act(async () => root.render(<EscapeHarness open={false} onEscape={onEscape} />))
  await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })) })
  expect(onEscape).toHaveBeenCalledTimes(1)
})

/** 浮层内容改变时不应重新订阅 (onEscape 用 ref 兜住) */
function CounterHarness({ onEscape }: { onEscape: () => void }) {
  const [n, setN] = useState(0)
  useEscapeClose(true, onEscape)
  return <button type="button" onClick={() => setN(n + 1)}>{n}</button>
}

it('浮层内容变化不影响 ESC 绑定', async () => {
  const onEscape = vi.fn()
  await act(async () => root.render(<CounterHarness onEscape={onEscape} />))
  const btn = host.querySelector('button')!
  await act(async () => btn.click())
  await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })) })
  expect(onEscape).toHaveBeenCalledTimes(1)
})
