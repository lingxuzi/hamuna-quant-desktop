// @vitest-environment node
// 控件刻度守门 — 全站 input / select / button / textarea 的尺寸只能落在 sm/md/lg 三档。
//
// 为什么要有: .ctl-input / .ctl-select / .ctl-btn 只管描边与底色, 高度和字号由档位类
// (ctl-sm / ctl-md / ctl-lg) 给。少写档位类, 控件就没高度 (原生 input 塌成 ~20px),
// 同一行里能和邻居差出 8px —— 这正是「控件大小很乱」最常见的来源。
// 这里把两类漂移挡在 CI 之外: 凭空多出来的档位名, 以及漏掉档位的变体控件。
import { readFileSync, readdirSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'

const SRC = fileURLToPath(new URL('../', import.meta.url))

/** 形态类 (只管结构, 不定尺寸) */
const VARIANTS = ['btn', 'ghost', 'input', 'select', 'field', 'icon', 'textarea']
/** 尺寸档 */
const TIERS = ['sm', 'md', 'lg']
/** 多行框的档位 (带 textarea- 前缀, 与 CTL_* 分开 —— .ctl 会锁死高度) */
const TEXTAREA_TIERS = ['textarea-sm', 'textarea-md', 'textarea-lg']
/** 自带档位的常量名 (拼出来的类串里出现它, 就算带了档位) */
const TIER_CONST = [
  'INPUT_SM', 'INPUT_MD', 'INPUT_LG',
  'SELECT_SM', 'SELECT_MD', 'SELECT_LG',
  'FIELD_SM', 'FIELD_MD', 'FIELD_LG',
  'BTN_SM', 'BTN_MD', 'BTN_LG',
  'BTN_GHOST_SM', 'BTN_GHOST_MD', 'BTN_GHOST_LG',
  'ICON_BTN_SM', 'ICON_BTN_MD', 'ICON_BTN_LG',
  'CTL_SM', 'CTL_MD', 'CTL_LG',
  'TEXTAREA_SM', 'TEXTAREA_MD', 'TEXTAREA_LG',
]

/** 只认独立的 ctl-* 记号 —— toc-ctl-icon 这类同后缀的名字不算 */
const TOKEN = /(?<![\w-])ctl-([a-z0-9-]+)/g
/** 抓类串: 双引号 / 单引号 / 模板串 */
const STRING = /"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(e => {
    const p = join(dir, e.name)
    return e.isDirectory() ? walk(p) : [p]
  })
}

const files = walk(SRC).filter(f => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))

/** 逐文件列出「类串 → 位置」 */
function classStrings(): { file: string; line: number; cls: string }[] {
  const out: { file: string; line: number; cls: string }[] = []
  for (const f of files) {
    const txt = readFileSync(f, 'utf8')
    for (const m of txt.matchAll(STRING)) {
      const cls = m[1] ?? m[2] ?? m[3] ?? ''
      if (!cls.includes('ctl')) continue
      out.push({ file: relative(SRC, f), line: txt.slice(0, m.index).split('\n').length, cls })
    }
  }
  return out
}

it('控件刻度只有 sm / md / lg 三档, 没有自造档位', () => {
  const known = new Set([...VARIANTS, ...TIERS, ...TEXTAREA_TIERS])
  const bad: string[] = []
  for (const { file, line, cls } of classStrings()) {
    for (const m of cls.matchAll(TOKEN)) {
      if (!known.has(m[1])) bad.push(`${file}:${line}  ctl-${m[1]}  ← ${cls.slice(0, 60)}`)
    }
  }
  expect(bad, `自造的控件档位:\n${bad.join('\n')}`).toEqual([])
})

it('形态控件必须带档位 —— 少了档位类就没有高度', () => {
  const bad: string[] = []
  for (const { file, line, cls } of classStrings()) {
    const tokens = [...cls.matchAll(TOKEN)].map(m => m[1])
    const used = VARIANTS.filter(v => tokens.includes(v))
    if (!used.length) continue
    const hasTier =
      tokens.some(t => TIERS.includes(t) || TEXTAREA_TIERS.includes(t)) ||
      TIER_CONST.some(c => cls.includes(c))
    if (!hasTier) bad.push(`${file}:${line}  ${used.join('+')} 无档位  ← ${cls.slice(0, 60)}`)
  }
  expect(bad, `缺档位的控件:\n${bad.join('\n')}`).toEqual([])
})

it('CSS 里三档的高度与字号是唯一定义', () => {
  const css = readFileSync(join(SRC, 'index.css'), 'utf8')
  for (const tier of TIERS) {
    const rule = css.match(new RegExp(`\\.ctl-${tier},[^\\{]*\\{([^}]*)\\}`))?.[1] ?? ''
    expect(rule, `.ctl-${tier} 没找到刻度定义`).toMatch(/--ctl-h:\s*\d+px/)
    expect(rule).toMatch(/--ctl-px:\s*\d+px/)
    expect(rule).toMatch(/--ctl-fs:\s*\d+px/)
  }
})
