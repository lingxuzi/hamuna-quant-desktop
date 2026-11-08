/**
 * 动效约定 — 全站唯一的缓动与入场曲线来源。
 *
 * 设计读法: 密度高的工作台, 动效只做「引导视线」不做「表演」:
 * 入场快 (≤250ms)、位移小 (≤8px)、不循环、不弹跳。
 * 与 CSS 侧 `ease-smooth` 同一条缓动曲线 (cubic-bezier(.16,1,.3,1))。
 */
import type { Variants } from 'framer-motion'

/** 与 tailwind `ease-smooth` / CSS transition 同源 */
export const EASE = [0.16, 1, 0.3, 1] as const

/** 页面入场: 上移 8px 淡入 */
export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.25, ease: EASE } },
}

/** 列表逐项入场 — 配合 stagger() 使用 */
export const riseItem: Variants = {
  hidden: { opacity: 0, y: 6 },
  show: { opacity: 1, y: 0, transition: { duration: 0.22, ease: EASE } },
}

/** 子项间隔 (秒) */
export const stagger = (step = 0.03): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: step, delayChildren: 0.02 } },
})

/** 用户开了「降低动效」时的替身: 直接呈现, 不做位移 */
export const staticVariants: Variants = {
  hidden: { opacity: 1, y: 0 },
  show: { opacity: 1, y: 0, transition: { duration: 0 } },
}
