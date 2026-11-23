// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StrategyBuilderDialog } from './StrategyBuilderDialog'
import { storage } from '@/lib/storage'
import { api } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  friendlyStreamError: (m: string) => m,
  api: {
    strategyAiStatus: vi.fn(async () => ({ configured: true })),
    strategyBuildStream: vi.fn(),
    strategySaveCodeV2: vi.fn(),
  },
}))

const CODE = 'META = {"id": "ai_x"}\nRULES = """1. a"""\n'
let root: Root
let container: HTMLDivElement

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  localStorage.clear()
  vi.clearAllMocks()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  vi.mocked(api.strategyBuildStream).mockImplementation(async function* () {
    yield { type: 'result', valid: true, code: CODE, meta: {}, error: null }
  } as any)
})

afterEach(() => { act(() => root.unmount()); container.remove() })

const render = async (open: boolean) => {
  await act(async () => {
    root.render(<StrategyBuilderDialog open={open} onClose={() => {}} existingStrategyIds={new Set()} />)
  })
}
const click = async (t: string) => {
  const b = [...container.querySelectorAll('button')].find(x => x.textContent?.includes(t))
  expect(b, t).toBeTruthy()
  await act(async () => b!.click())
}
const fill = (el: HTMLInputElement | HTMLTextAreaElement, v: string) => {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  act(() => {
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, v)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const nameInput = () =>
  [...container.querySelectorAll('input')].find(
    el => (el as HTMLInputElement).getAttribute('placeholder')?.includes('策略名称') ?? false,
  ) as HTMLInputElement | undefined
const nameValue = () => nameInput()?.value ?? ''

async function createAndSave(researchOnly: boolean) {
  vi.mocked(api.strategySaveCodeV2).mockResolvedValue({ strategy_id: 'ai_x', research_only: researchOnly } as any)
  await render(true)
  fill(nameInput()!, '测试策略')
  fill(container.querySelector('textarea') as HTMLTextAreaElement, '收盘价站上均线')
  await click('AI 生成策略')
  await click('保存策略')
  await act(async () => { await new Promise(r => setTimeout(r, 50)) })
}

it('research_only 策略保存后, 刷新重开必须是空状态', async () => {
  await createAndSave(true)
  // research_only 不进 /api/strategies, 所以 existingStrategyIds 永远是空 Set —— 旧逻辑据此判不出「已完成」
  await render(false)
  await render(true)
  await act(async () => { await new Promise(r => setTimeout(r, 30)) })
  expect(nameValue()).toBe('')
})

it('保存失败后重开仍应恢复草稿 (不能误清)', async () => {
  vi.mocked(api.strategySaveCodeV2).mockRejectedValue(new Error('实跑失败'))
  await render(true)
  fill(nameInput()!, '草稿名')
  fill(container.querySelector('textarea') as HTMLTextAreaElement, '收盘价站上均线')
  await click('AI 生成策略')
  await click('保存策略')
  await act(async () => { await new Promise(r => setTimeout(r, 50)) })
  await render(false)
  await render(true)
  await act(async () => { await new Promise(r => setTimeout(r, 30)) })
  // 保存失败 → 草稿必须留着; 恢复后回到第 2 步 (代码已生成)
  expect(storage.strategyDraft.get(null)?.name).toBe('草稿名')
  expect([...container.querySelectorAll('button')].some(b => b.textContent?.includes('上一步'))).toBe(true)
})

it('重开时若保存标记匹配, 即使草稿残留也判为已完成', async () => {
  // 模拟旧版本/异常路径留下的残留草稿
  storage.strategyDraft.set({
    name: '旧的', description: '', direction: 'long', executionBackend: 'auto',
    rules: '旧规则', code: CODE, step: 2, strategyId: 'ai_x', source: 'ai',
  } as any)
  storage.strategyDraftSaved.set('ai_x')
  await render(true)
  await act(async () => { await new Promise(r => setTimeout(r, 30)) })
  expect(nameValue()).toBe('')
  expect(storage.strategyDraft.get(null)).toBeNull()
})

it('「重新创建」清掉保存标记, 之后同名新草稿不会被误判完成', async () => {
  await createAndSave(true)
  await click('重新创建')
  expect(storage.strategyDraftSaved.get('')).toBe('')
})

it('保存请求途中刷新 (JS 上下文销毁) 后重开仍为空状态', async () => {
  // 保存含后端实跑, 可能耗时数秒; 此间刷新页面, clearDraft 永远跑不到
  vi.mocked(api.strategySaveCodeV2).mockImplementation(() => new Promise(() => {}))
  await render(true)
  fill(nameInput()!, '途中刷新')
  fill(container.querySelector('textarea') as HTMLTextAreaElement, '收盘价站上均线')
  await click('AI 生成策略')
  await click('保存策略')
  // pending 期间草稿必须已清, 否则刷新后必然复活
  expect(storage.strategyDraft.get(null)).toBeNull()

  act(() => root.unmount())
  root = createRoot(container)
  await render(true)
  await act(async () => { await new Promise(r => setTimeout(r, 30)) })
  expect(storage.strategyDraft.get(null)).toBeNull()
})
