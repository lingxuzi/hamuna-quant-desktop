// @vitest-environment node
// 告警来源中文名必须覆盖后端声明的全部 source。
//
// 踩过的坑: 前端曾把这份映射抄在三处 (弹窗 / 看板监控中心 / 监控中心页), 各处漏
// 不同的键; 漏键时 `?? ev.source` 直接把英文键画到界面上, 看着像乱码。这条用例
// 把「后端新增 source 而前端没补」变成红灯, 而不是等用户看到英文键才发现。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'
import { SOURCE_LABEL } from './alertSources'

const backend = readFileSync(
  fileURLToPath(new URL('../../../backend/app/services/quote_service.py', import.meta.url)),
  'utf8',
)

function backendSources(): string[] {
  const block = backend.match(/SOURCE_LABELS\s*=\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
  return [...block.matchAll(/"([a-z_]+)"\s*:/g)].map(m => m[1])
}

it('后端 SOURCE_LABELS 的每个键都有前端中文名', () => {
  const sources = backendSources()
  expect(sources.length).toBeGreaterThan(8)  // 解析失败时不要让断言空转
  for (const s of sources) expect(SOURCE_LABEL[s], `缺少 ${s} 的中文名`).toBeTruthy()
})
