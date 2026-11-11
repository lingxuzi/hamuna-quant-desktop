/**
 * 右侧指标栏插槽 — 看板页把指标栏 portal 到壳层第三栏, 与设计稿 `.app.with-rail`
 * 的三栏结构(目录 / 主栏 / 指标栏)对齐: 指标栏必须与主栏平级, 才能贴住视口右缘、
 * 与主栏等高, 而不是挤在主栏内边距里。
 *
 * host: 壳层里的挂载点(由 Layout 提供); setActive: 页面声明「本路由是否占用第三栏」。
 */
import { createContext, useContext } from 'react'

export interface RailSlotValue {
  /** 壳层第三栏的挂载节点; null 表示壳层尚未就绪(首帧) */
  host: HTMLElement | null
  /** 登记/注销第三栏占用 */
  setActive: (active: boolean) => void
}

export const RailSlotContext = createContext<RailSlotValue>({
  host: null,
  setActive: () => {},
})

export function useRailSlot(): RailSlotValue {
  return useContext(RailSlotContext)
}
