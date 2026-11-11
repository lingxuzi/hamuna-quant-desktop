// @vitest-environment node
// token 对比度守门 — 浅色皮肤上的正文/辅助字不得低于 WCAG AA (4.5:1)。
// 踩过的坑: 纸底的 --fg-muted 原本 3.6:1, 警示色原本 1.8:1 (amber 阶梯不看皮肤),
// 账簿色族 (emerald/sky/...) 原本整条阶梯写死 → 纸底上 text-emerald-400 只有 1.9:1。
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'

const css = readFileSync(fileURLToPath(new URL('../index.css', import.meta.url)), 'utf8')

/** 某皮肤块的 CSS 原文 (如 "html.paper { … }") */
function scopeBlock(scope: string): string {
  return css.match(new RegExp(`${scope}\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1] ?? ''
}

/** 取某皮肤块里的 --token 三元组 (如 "32 72% 40%"); 未定义 = 继承 :root */
function token(scope: string, name: string): string {
  const found = scopeBlock(scope).match(new RegExp(`--${name}:\\s*([\\d.]+)\\s+([\\d.]+)%\\s+([\\d.]+)%`))
  if (found) return `${found[1]} ${found[2]}% ${found[3]}%`
  if (scope !== ':root') return token(':root', name)
  throw new Error(`index.css 里没找到 ${scope} 的 --${name}`)
}

/** 取明度型 token (如 --led-ink: 30%); 未定义 = 继承 :root */
function lightness(scope: string, name: string): string {
  const found = scopeBlock(scope).match(new RegExp(`--${name}:\\s*([\\d.]+)%`))
  if (found) return `${found[1]}%`
  if (scope !== ':root') return lightness(':root', name)
  throw new Error(`index.css 里没找到 ${scope} 的 --${name}`)
}

function luminance(triplet: string): number {
  const [h, s, l] = triplet.split(' ').map(v => parseFloat(v))
  const c = (1 - Math.abs(2 * (l / 100) - 1)) * (s / 100)
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l / 100 - c / 2
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  const lin = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
  return 0.2126 * lin(r + m) + 0.7152 * lin(g + m) + 0.0722 * lin(b + m)
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const SKINS = [':root', 'html.paper', 'html.dark'] as const
/** 账簿色族的四个色相 (见 tailwind.config.ts LEDGER_HUE): 墨绿 / 墨红 / 黛 / 绛 */
const LEDGER_HUES = [162, 0, 205, 338]

it('每套皮肤的辅助字与警示字都落在 AA 对比度之上', () => {
  for (const skin of SKINS) {
    const warning = token(skin, 'warning')
    const onSolid = token(skin, 'on-solid')
    const muted = token(skin, 'fg-muted')
    const secondary = token(skin, 'fg-secondary')
    for (const bg of ['base', 'surface', 'elevated']) {
      const surface = token(skin, bg)
      expect(contrast(muted, surface), `${skin} --fg-muted 对 --${bg}`).toBeGreaterThanOrEqual(4.5)
      expect(contrast(secondary, surface), `${skin} --fg-secondary 对 --${bg}`).toBeGreaterThanOrEqual(4.5)
      expect(contrast(warning, surface), `${skin} --warning 对 --${bg}`).toBeGreaterThanOrEqual(4.5)
    }
    expect(contrast(onSolid, warning), `${skin} --on-solid 对 --warning`).toBeGreaterThanOrEqual(4.5)
  }
})

it('账簿色族作字色时, 每套皮肤都过 AA', () => {
  for (const skin of SKINS) {
    const ink = lightness(skin, 'led-ink')
    for (const bg of ['base', 'surface', 'elevated']) {
      const surface = token(skin, bg)
      for (const h of LEDGER_HUES) {
        expect(contrast(`${h} 42% ${ink}`, surface), `${skin} 账簿色 h${h} L${ink} 对 --${bg}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  }
})

it('实心彩色块上的字 (--on-solid) 与块本身过 AA', () => {
  for (const skin of SKINS) {
    const onSolid = token(skin, 'on-solid')
    const ink = lightness(skin, 'led-ink')
    const fills = [
      ['--accent', token(skin, 'accent')],
      ['--danger', token(skin, 'danger')],
      ['--bull', token(skin, 'bull')],
      ['--bear', token(skin, 'bear')],
      ['--warning', token(skin, 'warning')],
      ...LEDGER_HUES.map(h => [`账簿色 h${h}`, `${h} 42% ${ink}`] as const),
    ] as const
    for (const [label, fill] of fills) {
      expect(contrast(onSolid, fill), `${skin} --on-solid 对 ${label}`).toBeGreaterThanOrEqual(4.5)
    }
  }
})
