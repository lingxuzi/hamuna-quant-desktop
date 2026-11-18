// @vitest-environment jsdom
/**
 * 助手扩展的注册契约。
 *
 * 注册表在 finalize 阶段做校验 (导航必须指向本扩展的路由、路由不能撞核心路径),
 * 校验不过会把整个扩展静默摘掉 —— 页面和菜单直接消失, 没有任何报错。
 * 所以在这里把「专家页路由 + 导航 + 设置插槽」钉死。
 */
import { expect, it } from 'vitest'
import extension from './extension'

it('专家页注册路由与导航, 设置页挂了内置专家配置区', async () => {
  const registry = await import('@/extensions/registry')
  await registry.loadFrontendExtensions({ assistant: async () => ({ default: extension }) })
  registry.finalizeFrontendExtensions(new Set(['/', '/assistant', '/settings']))

  expect(registry.getFrontendExtensionLoadErrors()).toEqual([])
  expect(registry.getFrontendExtensionRoutes().map(route => route.path)).toContain('/experts')
  expect(registry.getFrontendExtensionNavigation().map(item => item.label)).toContain('专家')
  expect(
    registry.getFrontendSlotRegistrations('settings.system.section').map(slot => slot.id),
  ).toContain('assistant-experts')
})
