/**
 * AI 对话助手前端扩展 — 完全解耦模块。
 *
 * 常驻挂载点: layout.navigation.extra 插槽(可拖动悬浮球 + 右缘滑入抽屉),
 * 另有 layout.header.quick 入口与设置页的开关行。
 * 专家页 (/experts) 是完整页面, 按二次开发规范走路由 + 导航注册, 不塞进插槽。
 * 删除本目录即整体卸载, 不修改任何核心文件; 复用的核心能力均为只读 import
 * (MarkdownRenderer、cn、设计令牌、BrandIconProps)。自包含 HTTP 客户端见
 * client.ts 的取舍说明。
 */
import { lazy, type ReactNode } from 'react'
import type { FrontendExtension } from '@/extensions/types'
import type { BrandIconProps } from '@/components/BrandIcons'
import { AssistantLauncher } from './AssistantLauncher'
import { AssistantHeaderEntry } from './ui/AssistantHeaderEntry'
import { FabVisibilitySetting } from './ui/FabVisibilitySetting'

export const Experts = lazy(() => import('./ExpertsPage').then(m => ({ default: m.Experts })))

/** 专家 — 侧栏品牌图标族同口径 (24 网格 / 2px 线幅 / 直角), 自带一份不动核心。 */
function IconExperts({ size = 24, ...rest }: BrandIconProps): ReactNode {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="butt"
      strokeLinejoin="miter"
      aria-hidden="true"
      {...rest}
    >
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6" />
    </svg>
  )
}

const extension: FrontendExtension = {
  id: 'assistant.chat',
  apiVersion: 1,
  routes: [
    { id: 'experts', path: '/experts', component: Experts },
  ],
  navigation: [
    { id: 'experts', routeId: 'experts', label: '专家', icon: IconExperts },
  ],
  slots: [
    {
      name: 'layout.navigation.extra',
      id: 'assistant-entry',
      order: 10,
      component: AssistantLauncher,
    },
    {
      name: 'layout.header.quick',
      id: 'assistant-header-entry',
      order: 10,
      component: AssistantHeaderEntry,
    },
    {
      name: 'settings.system.section',
      id: 'assistant-fab-visibility',
      order: 10,
      component: FabVisibilitySetting,
    },
  ],
}

export default extension
