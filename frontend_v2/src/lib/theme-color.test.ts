// @vitest-environment jsdom
// 图表色必须是 zrender 能解析的颜色。
//
// zrender 的颜色解析器只认逗号式 hsl(h, s%, l%) / hsla(h, s%, l%, a);
// CSS Color 4 的空格式 hsl(h s% l% / a) 解析结果是 undefined —— 鼠标一进图表,
// ECharts 给 series 加 emphasis 态要提色, 拿到 undefined 就把整轮重绘带崩,
// 表现为「曲线和网格线一起消失」。这个坑在浏览器里看不出来 (浏览器两种都认),
// 只能靠约定守住。
import { beforeEach, expect, it } from 'vitest'
import { alpha, chartTheme, rampColors } from './theme'

/** zrender 的参数个数: hsl 恰好 3 个, hsla 恰好 4 个, 且必须逗号分隔。 */
const ZRENDER_HSL = /^hsla?\(\s*[\d.]+\s*,\s*[\d.]+%\s*,\s*[\d.]+%\s*(?:,\s*[\d.]+\s*)?\)$/

beforeEach(() => {
  document.documentElement.style.setProperty('--bull', '162 76% 27%')
  document.documentElement.style.setProperty('--bear', '0 45% 39%')
  document.documentElement.style.setProperty('--accent', '18 79% 34%')
  document.documentElement.style.setProperty('--fg-muted', '240 5% 44%')
})

it('调色板里的每个颜色都是 zrender 解析得了的逗号式 hsl', () => {
  const ct = chartTheme('paper')
  // 先确认 CSS 变量真的被读进来了 (否则下面的断言是空转)
  expect(ct.bull).toBe('hsl(162, 76%, 27%)')
  const colors = [
    ...Object.values(ct).flatMap(v => (Array.isArray(v) ? v : [v])),
    ...rampColors(5),
    ...rampColors(1),
  ]
  for (const c of colors) expect(c, c).toMatch(ZRENDER_HSL)
})

it('半透明填充保留色相, 只改透明度', () => {
  expect(alpha('hsl(162 76% 27%)', 0.4)).toBe('hsla(162, 76%, 27%, 0.4)')
  expect(alpha('hsl(162, 76%, 27%)', 0.4)).toBe('hsla(162, 76%, 27%, 0.4)')
  expect(alpha('hsla(162, 76%, 27%, 0.9)', 0.4)).toBe('hsla(162, 76%, 27%, 0.4)')
  expect(alpha('#FACC15', 0.4)).toBe('#FACC15')
})
