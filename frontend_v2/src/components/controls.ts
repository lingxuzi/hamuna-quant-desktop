/**
 * 控件刻度 — 全站 select / input / button 的唯一尺寸与字体来源。
 *
 * 为什么要有: 全仓曾有 209 种 button 写法, 高度散在 h-5/h-6/h-7/h-8/h-9/h-11 六档,
 * 字号散在 10/11/11.5/12/14px 五档, 同一行筛选器里能并排放出三种高度。
 * 现在收敛成三档, 页面只从这三档里挑。
 *
 * 三档的用途:
 *   sm  24px  表格单元格内 / 极密集的筛选条
 *   md  28px  页面筛选器、工具条、表单 (默认)
 *   lg  36px  弹窗与引导页的主表单
 *
 * 字号跟着高度走: sm 11px / md 12px / lg 14px —— 数字一律等宽 (font-mono 在调用处加)。
 *
 * 控件形态一一对应, 不要混用:
 *   INPUT_*  文本输入 (发丝线 + 纸底)
 *   SELECT_* 原生 <select> (同输入框外壳, 正文字族)
 *   FIELD_*  按钮型字段外壳 (下拉触发器 / 日期选择器) —— 内容排成一行
 *   BTN_*    按钮 (描边次要 / .pri 赭石实心)
 *
 * 结构 (描边/圆角/聚焦环/禁用态) 与尺寸档都落在 index.css 的 .ctl-* 基类里,
 * 这里只导出类名, 不再重复一遍样式 —— 两处各写一份就会各自漂移。
 */
import { cn } from '@/lib/cn'

/** 尺寸档: 高度 + 左右内边距 + 字号 (三档绑定, 不允许单独改其中一项) */
export const CTL_SM = 'ctl ctl-sm'
export const CTL_MD = 'ctl ctl-md'
export const CTL_LG = 'ctl ctl-lg'

/** 文本输入: 发丝线 + 纸底 + 聚焦转赭石 */
export const INPUT_SM = `ctl-input ${CTL_SM}`
export const INPUT_MD = `ctl-input ${CTL_MD}`
export const INPUT_LG = `ctl-input ${CTL_LG}`

/** 原生下拉: 与输入框同壳同高, 但用正文字族 (等宽字体没有中文字形) */
export const SELECT_SM = `ctl-select ${CTL_SM}`
export const SELECT_MD = `ctl-select ${CTL_MD}`
export const SELECT_LG = `ctl-select ${CTL_LG}`

/** 按钮型字段外壳: 下拉触发器 / 日期选择器 —— 内容必须排成一行 */
export const FIELD_SM = `ctl-field ${CTL_SM}`
export const FIELD_MD = `ctl-field ${CTL_MD}`
export const FIELD_LG = `ctl-field ${CTL_LG}`

/** 按钮: 次要 (描边) 与主要 (赭石实心) 两版, 同尺寸成对 */
export const BTN_SM = `ctl-btn ${CTL_SM}`
export const BTN_MD = `ctl-btn ${CTL_MD}`
export const BTN_LG = `ctl-btn ${CTL_LG}`

/** 主要按钮 = 同档按钮 + .pri 赭石实心; 三档同值, 留成三个名字是为了与 BTN_* 成对读 */
export const BTN_PRIMARY = 'pri'

/** 图标按钮: 正方形, 边长跟文字按钮同档 (不靠 padding 撑, 免得大小随图标变) */
export const ICON_BTN_SM = `ctl-btn ctl-icon ${CTL_SM}`
export const ICON_BTN_MD = `ctl-btn ctl-icon ${CTL_MD}`
export const ICON_BTN_LG = `ctl-btn ctl-icon ${CTL_LG}`

/** 筹码 (多选标签): 比 sm 还矮的一档, 高度由内容定, 只统一字号与内距 */
export const CHIP =
  'inline-flex items-center rounded-btn border px-2 py-0.5 text-[11px] font-medium ' +
  'transition-colors cursor-pointer'

/** 按档取输入类控件样式 (档位是字面量, Tailwind 才能扫到) */
export function inputCls(size: 'sm' | 'md' | 'lg' = 'md', extra?: string) {
  const base = size === 'sm' ? INPUT_SM : size === 'lg' ? INPUT_LG : INPUT_MD
  return extra ? cn(base, extra) : base
}

export function selectCls(size: 'sm' | 'md' | 'lg' = 'md', extra?: string) {
  const base = size === 'sm' ? SELECT_SM : size === 'lg' ? SELECT_LG : SELECT_MD
  return extra ? cn(base, extra) : base
}

/** 按钮型字段 (日期选择器 / 下拉触发器) */
export function fieldCls(size: 'sm' | 'md' | 'lg' = 'md', extra?: string) {
  const base = size === 'sm' ? FIELD_SM : size === 'lg' ? FIELD_LG : FIELD_MD
  return extra ? cn(base, extra) : base
}

export function btnCls(variant: 'default' | 'primary' = 'default', size: 'sm' | 'md' | 'lg' = 'md', extra?: string) {
  const base = size === 'sm' ? BTN_SM : size === 'lg' ? BTN_LG : BTN_MD
  return extra ? cn(base, variant === 'primary' && BTN_PRIMARY, extra) : variant === 'primary' ? cn(base, BTN_PRIMARY) : base
}
