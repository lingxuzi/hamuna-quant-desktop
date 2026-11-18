/**
 * AI 助手自包含 HTTP 客户端 — NDJSON 流式解析 + 状态/快捷指令查询。
 *
 * 解耦取舍: 项目约定核心页面统一走 `lib/api.ts`, 但扩展模块以"不修改核心
 * 文件"为更高优先级, 故在此内建同款约定的最小客户端(同源 fetch + 统一
 * 错误文案), 不复用也不修改 api.ts。协议与后端 `app/custom/assistant/__init__.py`
 * 的注释一一对应; 未知事件类型(如 ping)静默忽略。
 */

export interface AssistantStatus {
  configured: boolean
  provider: string
  model: string
  supports_tools: boolean
}

export interface QuickSuggest {
  id: string
  label: string
  prompt: string
  /** 分组标题 — 空会话页按组分区展示 */
  group?: string
}

/** 工具附带的可绘图数据 — 来自工具真实返回, 非模型生成(结果可核对)。 */
export type AssistantChart =
  | {
      kind: 'daily_kline'
      symbol: string
      name?: string
      /** [date, open, high, low, close, volume] 按日期升序, 后端封顶 120 根 */
      points: [string, number, number, number, number, number][]
    }
  | {
      kind: 'intraday'
      symbol: string
      name?: string
      /** 昨收基准价(反推值), 用于画基准虚线与涨跌着色 */
      prev_close?: number
      /** [HH:MM, price, volume] 按时间升序, 后端封顶 240 点 */
      points: [string, number, number][]
    }

export type AssistantEvent =
  | { type: 'notice'; message: string }
  | { type: 'tool_call'; call_id: string; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; call_id: string; name: string; ok: boolean; summary: string; elapsed_ms: number; charts?: AssistantChart[] }
  | { type: 'action_confirm'; call_id: string; name: string; label: string; risk: string; expires_in: number }
  | { type: 'rounds_confirm'; call_id: string; reached: number; expires_in: number }
  | { type: 'delta'; content: string }
  | { type: 'assistant_context'; reasoning_content: string; reasoning_context: string }
  | { type: 'error'; kind: string; message: string; hint?: string }
  | { type: 'done' }
  | { type: 'ping' }

export interface ChatHistoryMessage {
  role: 'user' | 'assistant'
  content: string
  reasoning_content?: string
  reasoning_context?: string
}

export interface AssistantContext {
  page?: string
  symbol?: string
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw new Error(await errorDetail(res))
  return res.json() as Promise<T>
}

/** 失败响应里的后端文案 —— 没有就退回状态码说明。 */
async function errorDetail(res: Response): Promise<string> {
  let detail = ''
  try {
    const parsed = JSON.parse(await res.text()) as { detail?: string; message?: string }
    detail = parsed.detail ?? parsed.message ?? ''
  } catch { /* 忽略解析失败, 走状态码文案 */ }
  return detail || `AI 助手接口请求失败: ${res.status}`
}

export function fetchAssistantStatus(): Promise<AssistantStatus> {
  return requestJson<AssistantStatus>('/api/custom/assistant/status')
}

export function fetchAssistantSuggests(): Promise<QuickSuggest[]> {
  return requestJson<{ suggests: QuickSuggest[] }>('/api/custom/assistant/suggests')
    .then(body => body.suggests)
}

/** 动作工具确认卡回调: approve=false 拒绝执行(后端超时同样视为拒绝)。 */
export async function decideAssistantAction(callId: string, approve: boolean): Promise<void> {
  const res = await fetch(`/api/custom/assistant/actions/${callId}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approve }),
  })
  if (!res.ok) throw new Error(`确认请求失败: ${res.status}`)
}

/** 对话主入口: POST NDJSON, 逐行 yield 事件; signal 支持中断。 */
export async function* assistantChatStream(
  body: { messages: ChatHistoryMessage[]; context?: AssistantContext; experts?: string[] },
  signal?: AbortSignal,
): AsyncGenerator<AssistantEvent> {
  const res = await fetch('/api/custom/assistant/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    let detail = ''
    try {
      const parsed = JSON.parse(await res.text()) as { detail?: string; message?: string }
      detail = parsed.detail ?? parsed.message ?? ''
    } catch { /* 忽略解析失败, 走状态码文案 */ }
    throw new Error(detail || `AI 助手请求失败: ${res.status}`)
  }
  if (!res.body) throw new Error('响应无 body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() ?? ''
    for (const line of lines) {
      const event = parseEventLine(line)
      if (event) yield event
    }
  }
  const tail = parseEventLine(buf)
  if (tail) yield tail
}

function parseEventLine(line: string): AssistantEvent | null {
  const text = line.trim()
  if (!text) return null
  try {
    return JSON.parse(text) as AssistantEvent
  } catch {
    return null
  }
}

// ── 专家 (skill) 管理 ──
// 一个专家 = 一个 skill: 正文在 <data_dir>/assistant/skills/<name>/SKILL.md,
// 分类归属与启用状态在 <data_dir>/assistant/experts.json (后端单一真相)。

export interface ExpertItem {
  /** ASCII 标识 (目录名 / 接口入参) —— 不直接展示给用户。 */
  name: string
  /** 界面显示名 (通常取 SKILL.md 的一级标题, 即中文名)。 */
  title: string
  description: string
  source: 'builtin' | 'user'
  builtin: boolean
  category: string
  enabled: boolean
  references: string[]
}

export interface ExpertLayout {
  categories: string[]
  experts: ExpertItem[]
}

export interface ExpertDraft {
  name: string
  description: string
  content: string
  category: string
}

export interface ExpertDetail extends ExpertDraft {
  builtin: boolean
  enabled: boolean
}

export function fetchExperts(): Promise<ExpertLayout> {
  return requestJson<ExpertLayout>('/api/custom/assistant/experts')
}

export function fetchExpert(name: string): Promise<ExpertDetail> {
  return requestJson<ExpertDetail>(`/api/custom/assistant/experts/${encodeURIComponent(name)}`)
}

export function createExpert(draft: ExpertDraft): Promise<ExpertLayout> {
  return requestJson<ExpertLayout>('/api/custom/assistant/experts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(draft),
  })
}

export function updateExpert(
  name: string,
  patch: { description?: string; content?: string },
): Promise<ExpertLayout> {
  return requestJson<ExpertLayout>(`/api/custom/assistant/experts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

export function deleteExpert(name: string): Promise<ExpertLayout> {
  return requestJson<ExpertLayout>(`/api/custom/assistant/experts/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

/** 整份保存分类体系 + 专家的分类归属/启用状态。 */
export function saveExpertLayout(
  categories: string[],
  assignments: Record<string, { category: string; enabled: boolean }>,
): Promise<ExpertLayout> {
  return requestJson<ExpertLayout>('/api/custom/assistant/experts/layout', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ categories, assignments }),
  })
}

/** 导入处置报告: 导入 / 覆盖 / 跳过各是哪些。 */
export interface ExpertImportReport {
  imported: string[]
  updated: string[]
  skipped: string[]
  categories_added: string[]
}

/** 导出专家包 —— 文件名由后端给 (带日期)。 */
export async function exportExperts(): Promise<{ filename: string; blob: Blob }> {
  const res = await fetch('/api/custom/assistant/experts/export')
  if (!res.ok) throw new Error(await errorDetail(res))
  const match = /filename="([^"]+)"/.exec(res.headers.get('content-disposition') || '')
  return { filename: match?.[1] || 'experts.zip', blob: await res.blob() }
}

/** 导入专家包 (zip) —— 同名专家: overwrite=false 跳过, true 用包内版本替换。 */
export function importExperts(
  file: File,
  overwrite: boolean,
): Promise<ExpertLayout & { report: ExpertImportReport }> {
  const form = new FormData()
  form.append('file', file)
  form.append('overwrite', overwrite ? 'true' : 'false')
  return requestJson<ExpertLayout & { report: ExpertImportReport }>(
    '/api/custom/assistant/experts/import',
    { method: 'POST', body: form },
  )
}
