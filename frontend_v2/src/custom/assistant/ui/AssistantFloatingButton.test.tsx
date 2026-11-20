// @vitest-environment jsdom
/**
 * 悬浮球位置 —— 视口变了之后要回到该在的地方。
 *
 * 盯一件容易错的事: 视口缩小时球被夹进可视区, 视口再放大, 它得回到原位
 * (没拖过的贴当前视口右下角, 拖过的回到拖的地方), 不能卡在页面中间压住内容。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AssistantFloatingButton } from './AssistantFloatingButton'

vi.mock('../store', () => ({
  useAssistantStore: () => ({ open: false, sending: false, fabVisible: true }),
  toggleAssistant: vi.fn(),
}))

const SIZE = 38
const EDGE = 24

let root: Root | undefined

function setViewport(w: number, h: number) {
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true })
  Object.defineProperty(window, 'innerHeight', { value: h, configurable: true })
}

async function render() {
  root = createRoot(document.body)
  await act(async () => { root!.render(<AssistantFloatingButton />) })
}

function ballPos(): { left: string; top: string } {
  const el = document.querySelector<HTMLElement>('button[aria-label="AI 助手"]')
  if (!el) throw new Error('没找到悬浮球')
  return { left: el.style.left, top: el.style.top }
}

async function resizeTo(w: number, h: number) {
  setViewport(w, h)
  await act(async () => { window.dispatchEvent(new Event('resize')) })
}

beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.removeItem('assistant.fab.pos.v1')
  setViewport(1024, 768)
  await render()
})

afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = undefined
})

it('没拖过时贴当前视口右下角, 视口变大变小都跟着挪', async () => {
  expect(ballPos()).toEqual({ left: `${1024 - SIZE - EDGE}px`, top: `${768 - SIZE - EDGE}px` })

  await resizeTo(720, 450)
  expect(ballPos()).toEqual({ left: `${720 - SIZE - EDGE}px`, top: `${450 - SIZE - EDGE}px` })

  // 关键: 视口再放大, 球要回到右下角, 不能停在缩到一半时的位置。
  await resizeTo(1024, 768)
  expect(ballPos()).toEqual({ left: `${1024 - SIZE - EDGE}px`, top: `${768 - SIZE - EDGE}px` })
})
