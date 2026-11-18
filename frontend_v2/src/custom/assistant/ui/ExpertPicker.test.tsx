// @vitest-environment jsdom
/**
 * @ 引用专家的选择面板 —— 按分类分组、只列启用中的专家、已引用的要能看出来。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ExpertItem } from '../client'
import { ExpertPicker } from './ExpertPicker'

let root: Root | undefined
let host: HTMLDivElement

const expert = (name: string, category: string, enabled = true): ExpertItem => ({
  name,
  title: `${name} 的中文名`,
  description: `${name} 的用途`,
  source: 'user',
  builtin: false,
  category,
  enabled,
  references: [],
})

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = undefined
  host.remove()
})

async function render(props: React.ComponentProps<typeof ExpertPicker>) {
  root = createRoot(host)
  await act(async () => { root!.render(<ExpertPicker {...props} />) })
}

it('双栏: 左列分类, 右列只列当前分类的专家', async () => {
  await render({
    categories: ['自选', '基本面'],
    experts: [
      expert('alpha', '自选'),
      expert('beta', '基本面'),
      expert('off', '基本面', false),
      expert('loose', ''),
    ],
    query: '',
    mentions: [],
    onPick: vi.fn(),
  })

  const cats = Array.from(host.querySelectorAll('.drv-pick-cat'))
    .map(node => node.querySelector('span')?.textContent)
  expect(cats).toEqual(['自选', '基本面', '未分类'])
  // 停用的专家不占位, 默认停在第一个有专家的分类。
  const items = Array.from(host.querySelectorAll('.drv-pick-card'))
    .map(node => node.querySelector('span')?.textContent)
  expect(items).toEqual(['alpha 的中文名'])
})

it('点左列分类换右列内容', async () => {
  await render({
    categories: ['自选', '基本面'],
    experts: [expert('alpha', '自选'), expert('beta', '基本面')],
    query: '',
    mentions: [],
    onPick: vi.fn(),
  })

  const cat = host.querySelectorAll<HTMLButtonElement>('.drv-pick-cat')[1]
  await act(async () => { cat.click() })

  const items = Array.from(host.querySelectorAll('.drv-pick-card'))
    .map(node => node.querySelector('span')?.textContent)
  expect(items).toEqual(['beta 的中文名'])
})

it('搜索时当前分类被搜空, 自动落到还有结果的分类', async () => {
  await render({
    categories: ['自选', '基本面'],
    experts: [expert('alpha', '自选'), expert('beta', '基本面')],
    query: 'beta',
    mentions: [],
    onPick: vi.fn(),
  })

  const cats = Array.from(host.querySelectorAll('.drv-pick-cat'))
    .map(node => node.querySelector('span')?.textContent)
  expect(cats).toEqual(['基本面'])
  const items = Array.from(host.querySelectorAll('.drv-pick-card'))
    .map(node => node.querySelector('span')?.textContent)
  expect(items).toEqual(['beta 的中文名'])
})

it('关键字过滤名称与用途, 已引用的标为选中', async () => {
  await render({
    categories: ['自选'],
    experts: [expert('alpha', '自选'), expert('beta', '自选')],
    query: 'alpha',
    mentions: ['alpha'],
    onPick: vi.fn(),
  })

  const options = Array.from(host.querySelectorAll('[role="option"]'))
  expect(options).toHaveLength(1)
  expect(options[0].getAttribute('aria-selected')).toBe('true')
})

it('点选回调专家名', async () => {
  const onPick = vi.fn()
  await render({
    categories: ['自选'],
    experts: [expert('alpha', '自选')],
    query: '',
    mentions: [],
    onPick,
  })

  const option = host.querySelector<HTMLButtonElement>('[role="option"]')!
  await act(async () => { option.click() })
  expect(onPick).toHaveBeenCalledWith('alpha')
})
