// 主题管理 — 暗色(默认) / 亮色切换
//
// 机制:
//   - localStorage('tf-theme') 即时缓存 + 后端偏好副本，默认 dark
//   - 生效方式: html.dark class (index.css 的 CSS variables + Tailwind darkMode:class)
//   - index.html 里有预渲染内联脚本, 首屏前就设好 class, 避免闪烁 (FOUC)
//   - UI token (bg-surface/text-foreground 等) 自动跟随;
//     图表画布不吃 CSS 变量, 统一走 useChartTheme() 取调色板
import { useEffect, useState } from 'react'
import { hydrateUiPrefs, setUiPref } from '@/lib/uiPrefs'

const KEY = 'tf-theme'
const EVENT = 'tf-theme-change'

/** paper = 宣纸账簿(默认); dark/light 保留原皮肤。 */
export type Theme = 'dark' | 'light' | 'paper'

const THEMES: readonly Theme[] = ['paper', 'dark', 'light']

export function getTheme(): Theme {
  try {
    const raw = localStorage.getItem(KEY)
    return THEMES.includes(raw as Theme) ? (raw as Theme) : 'paper'
  } catch {
    return 'paper'
  }
}

/** 只改 class, 不派发事件 — 供事件监听回写用, 避免同步递归。 */
function syncThemeClass(theme: Theme) {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.classList.toggle('paper', theme === 'paper')
}

/** 恢复后端偏好时只应用主题，不把默认值重新写回后端。 */
export function applyTheme(theme: Theme) {
  syncThemeClass(theme)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: theme }))
}

export function setTheme(theme: Theme) {
  setUiPref(KEY, theme)
  applyTheme(theme)
}

/** 循环切换 纸 → 暗 → 亮 → 纸。 */
export function toggleTheme(): Theme {
  const next = THEMES[(THEMES.indexOf(getTheme()) + 1) % THEMES.length]
  setTheme(next)
  return next
}

/** 订阅当前主题 (本页切换 + 其他标签页切换均同步)。 */
export function useTheme(): Theme {
  const [theme, set] = useState<Theme>(getTheme)
  useEffect(() => {
    let active = true
    // 事件回写只更新 class 与 state; applyTheme 会再次 dispatch 同一事件, 不能用。
    const onChange = () => {
      if (!active) return
      syncThemeClass(getTheme())
      set(getTheme())
    }
    window.addEventListener(EVENT, onChange)
    window.addEventListener('storage', onChange)  // 跨标签页同步
    // 登录页在同一 SPA 内跳转，首次回填若为 401，挂载后仍须重新恢复主题。
    void hydrateUiPrefs().then(onChange)
    return () => {
      active = false
      window.removeEventListener(EVENT, onChange)
      window.removeEventListener('storage', onChange)
    }
  }, [])
  return theme
}

// ================================================================
// 图表调色板 — ECharts / lightweight-charts 画布不吃 CSS 变量,
// 所有图表组件统一从这里取色, 主题切换时依赖 useTheme 重建 option。
// bull/bear/accent 等语义色双主题一致, 不在此重复定义。
// ================================================================

export interface ChartTheme {
  /** 轴刻度/图例等常规文字 */
  text: string
  /** 信息条/图例里的强调文字 */
  textStrong: string
  /** 网格线 */
  grid: string
  /** 轴线/边框 */
  border: string
  /** 十字光标线 */
  crosshair: string
  /** 十字光标轴标签背景 */
  crosshairLabelBg: string
  /** tooltip 背景 */
  tooltipBg: string
  /** tooltip 边框 */
  tooltipBorder: string
  /** tooltip 文字 */
  tooltipText: string
  /** 半透明信息条背景 (K线图左上角 OHLC 条) */
  infoBarBg: string
  /** dataZoom 滑块填充 */
  zoomFill: string
  /** 分时图均价线以外的弱填充 */
  fillSubtle: string
  /** 语义色: 涨 / 跌 (A 股红涨绿跌), 与表格里的涨跌数字同源 */
  bull: string
  bear: string
  /** 强调色 (赭石): 只给结论与当前动作 */
  accent: string
  /** 警示 / 阈值线 */
  warning: string
  /** 中性参考线 (基准线、网格轴) */
  neutral: string
  /** 分类色板: 无序的多条序列 */
  series: string[]
  /** 顺序色阶: 分位、热度这类有序数据 */
  ramp: string[]
}

/* 画布不吃 CSS 变量, 但 index.css 的变量存的是 HSL 三元组(如 "0 45% 39%"),
   直接拼成 hsl() 就能用 —— 图表与 UI 因此永远同色, 换肤只改 index.css 一处。
   必须是逗号式 hsl(h, s%, l%): zrender 的颜色解析器只认逗号式, 碰上 CSS Color 4
   的空格式 hsl(h s% l% / a) 会解析成 undefined, emphasis 态提色时连带整轮重绘
   崩掉 —— 表现为图表一悬停, 曲线和网格线一起消失。 */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** 逗号式 hsl / hsla —— 浏览器和 zrender 都认这一种。 */
function hsl(name: string, alpha = 1): string {
  const parts = (cssVar(name) || '0 0% 50%').split(/\s+/).filter(Boolean)
  return alpha === 1 ? `hsl(${parts.join(', ')})` : `hsla(${parts.join(', ')}, ${alpha})`
}

function buildChartTheme(): ChartTheme {
  const ramp = [1, 2, 3, 4, 5].map(i => hsl(`--ramp-${i}`))
  const series = [1, 2, 3, 4, 5].map(i => hsl(`--series-${i}`))
  return {
    text: hsl('--fg-muted'),
    textStrong: hsl('--fg-primary'),
    grid: hsl('--border', 0.55),
    border: hsl('--border'),
    crosshair: hsl('--fg-primary', 0.3),
    crosshairLabelBg: hsl('--fg-secondary'),
    tooltipBg: hsl('--surface', 0.97),
    tooltipBorder: hsl('--border-strong', 0.18),
    tooltipText: hsl('--fg-primary'),
    infoBarBg: hsl('--elevated', 0.92),
    zoomFill: hsl('--fg-primary', 0.06),
    fillSubtle: hsl('--fg-primary', 0.04),
    bull: hsl('--bull'),
    bear: hsl('--bear'),
    accent: hsl('--accent'),
    warning: hsl('--warning'),
    neutral: hsl('--fg-muted'),
    series,
    ramp,
  }
}

/* 缓存按「主题 + 当前 --accent 值」作键: 首帧样式表可能还没注入,
   下一次渲染会拿到真值并重建, 不会把兜底色烤进缓存。 */
let cachedTheme: ChartTheme | null = null
let cachedKey = ''

export function chartTheme(_theme: Theme): ChartTheme {
  const key = cssVar('--accent')
  if (!cachedTheme || key !== cachedKey) {
    cachedTheme = buildChartTheme()
    cachedKey = key
  }
  return cachedTheme
}

function parseHsl(v: string): [number, number, number] {
  const m = v.match(/([\d.]+)\s+([\d.]+)%\s+([\d.]+)%/)
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [0, 0, 50]
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t

/** 逗号式 hsl 三元组 (zrender 只认这一种)。 */
const hslStr = (h: number, s: number, l: number) =>
  `hsl(${h.toFixed(0)}, ${s.toFixed(0)}%, ${l.toFixed(0)}%)`

/**
 * 有序色阶取色: 分位、热度这类有顺序的数据要一条由浅到深的单色阶,
 * 不能拿分类色板硬凑 (同色系看不出先后)。在 --ramp-1..5 之间插值出 n 档。
 */
export function rampColors(n: number): string[] {
  const stops = [1, 2, 3, 4, 5].map(i => parseHsl(cssVar(`--ramp-${i}`)))
  if (n <= 0) return []
  if (n === 1) return [hslStr(...stops[0])]
  return Array.from({ length: n }, (_, i) => {
    const pos = (i / (n - 1)) * (stops.length - 1)
    const lo = Math.floor(pos)
    const hi = Math.min(lo + 1, stops.length - 1)
    const t = pos - lo
    const c = stops[lo].map((v, k) => lerp(v, stops[hi][k], t))
    return hslStr(c[0], c[1], c[2])
  })
}

/**
 * 同色系半透明填充: 把 hsl(H S% L%) 变成 hsla(H, S%, L%, a)。
 * 图表里的面积渐变必须与线同色相, 另写 rgba 会与皮肤脱钩。
 * 走逗号式 hsla —— zrender 解析不了空格式 hsl(H S% L% / a)。
 */
export function alpha(color: string, a: number): string {
  const m = color.match(/^hsla?\(([^)]+)\)$/)
  if (!m) return color
  const p = m[1].split(/[\s,/]+/).filter(Boolean)
  if (p.length < 3) return color
  return `hsla(${p[0]}, ${p[1]}, ${p[2]}, ${a})`
}

/** hook: 当前主题的图表调色板 (主题切换自动触发重渲染)。 */
export function useChartTheme(): ChartTheme {
  return chartTheme(useTheme())
}
