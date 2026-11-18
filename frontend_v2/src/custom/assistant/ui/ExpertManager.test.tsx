// @vitest-environment jsdom
/**
 * 专家管理 —— 批量启停。
 *
 * 盯一件容易错的事: 批量停用只改选中的那一个, 别的专家的启用状态不能被
 * 顺手改掉。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ExpertManager } from './ExpertManager'

const { fetchExperts, saveExpertLayout } = vi.hoisted(() => ({
  fetchExperts: vi.fn(),
  saveExpertLayout: vi.fn(),
}))

vi.mock('../client', async importOriginal => ({
  ...(await importOriginal<typeof import('../client')>()),
  fetchExperts,
  saveExpertLayout,
}))

let root: Root | undefined
let host: HTMLDivElement

const expert = (name: string) => ({
  name,
  title: `${name} 的中文名`,
  description: `${name} 的用途`,
  source: 'user' as const,
  builtin: false,
  category: '自选',
  enabled: true,
  references: [] as string[],
})

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.appendChild(host)
  fetchExperts.mockResolvedValue({
    categories: ['自选'],
    experts: [expert('alpha'), expert('beta')],
  })
  saveExpertLayout.mockImplementation(async (categories: string[], assignments: Record<string, unknown>) => ({
    categories,
    experts: Object.entries(assignments).map(([name, value]) => ({
      ...expert(name), ...(value as object),
    })),
  }))
})

afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = undefined
  host.remove()
  vi.clearAllMocks()
})

async function render() {
  root = createRoot(host)
  await act(async () => { root!.render(<ExpertManager scope="all" />) })
}

function buttonByText(text: string): HTMLButtonElement {
  const hit = Array.from(host.querySelectorAll('button'))
    .find(node => node.textContent?.trim() === text)
  if (!hit) throw new Error(`没找到按钮: ${text}`)
  return hit
}

it('批量停用只改选中的专家, 一次整份保存', async () => {
  await render()

  // 第一个复选框是「全选」, 后面每个专家一个
  const boxes = host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')
  expect(boxes.length).toBe(3)
  await act(async () => { boxes[1].click() })

  await act(async () => { buttonByText('批量停用').click() })

  expect(saveExpertLayout).toHaveBeenCalledTimes(1)
  const [categories, assignments] = saveExpertLayout.mock.calls[0]
  expect(categories).toEqual(['自选'])
  expect(assignments).toEqual({
    alpha: { category: '自选', enabled: false },
    beta: { category: '自选', enabled: true },
  })
})
