// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('@/lib/uiPrefs', () => ({ hydrateUiPrefs: () => Promise.resolve(), setUiPref: vi.fn() }))

let requests: Array<{ messages: Array<Record<string, unknown>>; experts?: string[] }>
let replies: Array<Array<Record<string, unknown>>>

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  localStorage.clear()
  requests = []
  replies = []
  vi.stubGlobal('fetch', vi.fn(async (url, init) => {
    if (url === '/api/custom/assistant/chat') {
      requests.push(JSON.parse(init.body))
      return new Response((replies.shift() ?? []).map(e => JSON.stringify(e)).join('\n'))
    }
    return new Response(JSON.stringify({ sessions: [], activeId: '' }))
  }))
})
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

it.each(['模型推理', ''])('preserves reasoning across tools, reload, and the next question (%s)', async reasoning => {
  replies.push([
    { type: 'delta', content: '查询中' },
    { type: 'assistant_context', reasoning_content: reasoning, reasoning_context: 'profile' },
    { type: 'tool_call', call_id: 'one', name: 'list_strategies', args: {} },
    { type: 'tool_result', call_id: 'one', name: 'list_strategies', ok: true, summary: 'ok', elapsed_ms: 1 },
    { type: 'delta', content: '完成' },
    { type: 'assistant_context', reasoning_content: reasoning + '最终', reasoning_context: 'profile' },
    { type: 'done' },
  ])
  let store = await import('./store')
  store.sendMessage('查询策略')
  await vi.waitFor(() => expect(store.activeSession()?.messages.some(m => m.role === 'assistant' && m.streaming)).toBe(false))
  const saved = JSON.parse(localStorage.getItem('assistant.sessions.v1')!)
  const answers = saved.sessions[0].messages.filter((m: { role: string }) => m.role === 'assistant')
  expect(answers.map((m: { reasoningContent: string }) => m.reasoningContent)).toEqual([reasoning, reasoning + '最终'])
  expect(answers.map((m: { content: string }) => m.content)).toEqual(['查询中', '完成'])

  vi.clearAllTimers()
  vi.resetModules()
  store = await import('./store')
  replies.push([{ type: 'delta', content: '继续回答' }, { type: 'done' }])
  store.sendMessage('继续解释')
  await vi.waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1].messages).toEqual([
    { role: 'user', content: '查询策略' },
    { role: 'assistant', content: '查询中', reasoning_content: reasoning, reasoning_context: 'profile' },
    { role: 'assistant', content: '完成', reasoning_content: reasoning + '最终', reasoning_context: 'profile' },
    { role: 'user', content: '继续解释' },
  ])
})

it('keeps plain-model and old-session history compatible', async () => {
  localStorage.setItem('assistant.sessions.v1', JSON.stringify({ activeId: 'old', sessions: [{
    id: 'old', title: '旧会话', createdAt: 1, messages: [
      { id: 'u', role: 'user', content: '旧问题', ts: 1 },
      { id: 'a', role: 'assistant', content: '旧回答', ts: 2, streaming: false },
    ],
  }] }))
  replies.push([{ type: 'delta', content: '新回答' }, { type: 'done' }])
  const store = await import('./store')
  store.sendMessage('继续')
  await vi.waitFor(() => expect(requests).toHaveLength(1))
  expect(requests[0].messages).toEqual([
    { role: 'user', content: '旧问题' }, { role: 'assistant', content: '旧回答' },
    { role: 'user', content: '继续' },
  ])
})

/** 等上一轮生成结束 (assistant 段不再 streaming), 否则 sendMessage 会被 sending 挡掉。 */
async function waitIdle(store: typeof import('./store')) {
  await vi.waitFor(() => expect(
    store.activeSession()?.messages.some(m => m.role === 'assistant' && m.streaming),
  ).toBe(false))
}

it('把 @ 引用的专家随请求发出去, 发送后自动清除引用状态', async () => {
  replies.push([{ type: 'done' }])
  const store = await import('./store')
  store.openAssistant()
  store.toggleMention('limit-up')
  store.toggleMention('fund-flow')
  store.sendMessage('看今天涨停')
  await vi.waitFor(() => expect(requests).toHaveLength(1))
  await waitIdle(store)
  expect(requests[0].experts).toEqual(['limit-up', 'fund-flow'])
  // 发出即清空: chip 消失, 下一条消息默认不带专家
  expect(store.activeSession()?.mentions).toEqual([])

  replies.push([{ type: 'done' }])
  store.sendMessage('再看看')
  await vi.waitFor(() => expect(requests).toHaveLength(2))
  await waitIdle(store)
  expect(requests[1].experts).toEqual([])

  // 重新选中的专家下一轮生效
  store.toggleMention('limit-up')
  replies.push([{ type: 'done' }])
  store.sendMessage('再来一次')
  await vi.waitFor(() => expect(requests).toHaveLength(3))
  await waitIdle(store)
  expect(requests[2].experts).toEqual(['limit-up'])
  expect(store.activeSession()?.mentions).toEqual([])
})

it('重试沿用这条消息当初引用的专家', async () => {
  replies.push([{ type: 'done' }])
  const store = await import('./store')
  store.openAssistant()
  store.toggleMention('limit-up')
  store.sendMessage('看今天涨停')
  await vi.waitFor(() => expect(requests).toHaveLength(1))
  await waitIdle(store)
  expect(store.activeSession()?.mentions).toEqual([])

  replies.push([{ type: 'done' }])
  store.retryLast()
  await vi.waitFor(() => expect(requests).toHaveLength(2))
  await waitIdle(store)
  expect(requests[1].experts).toEqual(['limit-up'])
})

it('引用数超上限时不再加, 避免把超量请求打回后端', async () => {
  const store = await import('./store')
  store.openAssistant()
  for (let i = 0; i < 12; i += 1) store.toggleMention(`expert-${i}`)

  expect(store.activeSession()?.mentions).toHaveLength(store.MAX_MENTIONS)
})
