import type { Config } from 'tailwindcss'
import animate from 'tailwindcss-animate'

/** 宣纸账簿色系: 把 Tailwind 内置彩色族只换「色相」, 保留明度阶梯。
 *
 *  为什么在主题层做而不是逐页改: 全仓 47 个文件用了 180 余处
 *  emerald/sky/amber/purple 等内置色 —— 这些高饱和彩块正是 AI 风的来源。
 *  逐页改是返工且必然遗漏; 这里一次把色相换成账簿语汇, 页面代码零改动。
 *
 *  为什么只换色相不塌成单一 token: 现有写法大量是「浅底 + 深字」
 *  (bg-orange-100/80 + text-orange-800)。若把 100 和 800 都指向同一 token,
 *  底色与文字会变成同一个颜色 → 不可读。保留明度阶梯才安全。
 *  饱和度压到 42% 以下, 让彩块褪成纸上的墨色。 */
const LEDGER_HUE = {
  // 绿 → 墨绿 162 (跌色, 兼「正常 / 通过 / 已启用」)
  emerald: 162, green: 162, teal: 162, lime: 162,
  // 红 → 墨红 0 (涨色, 兼 danger)
  red: 0, rose: 0,
  // 黄橘 → 暗金 34 (warning)
  amber: 34, yellow: 34, orange: 34,
  // 蓝青 → 黛 205 (中性信息标签)
  sky: 205, cyan: 205, blue: 205, indigo: 205,
  // 紫 → 绛 338 (AI / 复合策略)
  purple: 338, violet: 338, fuchsia: 338,
} as const

/** [档位, 明度] — 明度沿用 Tailwind 内置阶梯, 只把饱和度压到纸上能落读的程度 */
const LEDGER_STEPS: Array<[string, number]> = [
  ['50', 96], ['100', 92], ['200', 86], ['300', 78], ['400', 68], ['500', 58],
  ['600', 49], ['700', 41], ['800', 33], ['900', 26], ['950', 19],
]

// 灰族映射到主题中性层级: 浅档当底/线, 中档当次要字, 深档当主字
const GREY_SHADE_TOKENS: Record<string, string> = {
  50: 'surface', 100: 'elevated', 200: 'border', 300: 'border',
  400: 'fg-muted', 500: 'fg-muted', 600: 'fg-secondary', 700: 'fg-secondary',
  800: 'fg-secondary', 900: 'fg-primary', 950: 'fg-primary',
}

const ledgerColors = {
  ...Object.fromEntries(
    Object.entries(LEDGER_HUE).map(([family, h]) => [
      family,
      Object.fromEntries(LEDGER_STEPS.map(([shade, l]) => [shade, `hsl(${h} 42% ${l}% / <alpha-value>)`])),
    ]),
  ),
  ...Object.fromEntries(
    ['slate', 'gray', 'zinc', 'neutral', 'stone'].map(family => [
      family,
      Object.fromEntries(LEDGER_STEPS.map(([shade]) => [shade, `hsl(var(--${GREY_SHADE_TOKENS[shade]}) / <alpha-value>)`])),
    ]),
  ),
}

// 设计语言: 宣纸账簿 — 纸底 + 墨字 + 唯一赭石强调, 涨墨红 / 跌墨绿
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: { center: true, padding: '1rem' },
    extend: {
      colors: {
        // 内置彩色族 → 账簿语义色 (见文件顶部 LEDGER_HUE)
        ...ledgerColors,
        // §6.0.1 色板 — CSS variables 见 src/index.css
        // 注意: base 与字号 token 撞名, text-base 会生成 color 规则 (文字变背景色)。
        // 字号 16px 一律写 text-[16px] leading-6, 禁用 text-base。
        base:      'hsl(var(--base) / <alpha-value>)',
        surface:   'hsl(var(--surface) / <alpha-value>)',
        elevated:  'hsl(var(--elevated) / <alpha-value>)',
        border:    'hsl(var(--border) / <alpha-value>)',
        foreground: 'hsl(var(--fg-primary) / <alpha-value>)',
        secondary:  'hsl(var(--fg-secondary) / <alpha-value>)',
        muted:      'hsl(var(--fg-muted) / <alpha-value>)',
        accent:     'hsl(var(--accent) / <alpha-value>)',
        // A 股语义色:仅用于价格 / K 线,不用于 UI 状态
        bull:       'hsl(var(--bull) / <alpha-value>)',
        bear:       'hsl(var(--bear) / <alpha-value>)',
        warning:    'hsl(var(--warning) / <alpha-value>)',
        danger:     'hsl(var(--danger) / <alpha-value>)',
        // 影子色: 浮层若直接写 shadow-black/NN 会绕过 boxShadow 的纸色锁,
        // 纯黑落在暖白纸上发灰脏。供 shadow-scrim/NN 用, 随主题换色相。
        scrim:      'hsl(var(--scrim) / <alpha-value>)',
      },
      fontFamily: {
        // 字族统一由 index.css 的 --font-* 提供, CSS 与原子类不会各写一份
        sans: ['var(--font-sans)'],
        // 宣纸账簿: 标题用宋体, 与正文无衬线形成层级差; 数字一律等宽
        serif: ['var(--font-serif)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: {
        // 近直角: 账簿没有圆角卡片
        card: '2px',
        btn: '2px',
        input: '2px',
        dialog: '4px',
        // 形状一致性锁: 整个应用只有一个圆角刻度。
        // 旧的 rounded-lg/xl/2xl 散落在 60+ 文件里 (340 处), 逐个改是返工;
        // 在主题层压平成 2px, 一处改动全局对齐, 且不影响 rounded-full 的药丸形。
        sm: '2px',
        DEFAULT: '2px',
        md: '2px',
        lg: '2px',
        xl: '2px',
        '2xl': '2px',
        '3xl': '2px',
        '4xl': '2px',
      },
      boxShadow: {
        // 影一致性锁: 账簿用「三级线」而非阴影分层, 但浮层(菜单/弹窗/吐司)
        // 仍需与纸面分离。全站 30 处 shadow-lg/xl/2xl 散在 24 个文件里,
        // 逐个改是返工; 在主题层把整条刻度压成同一枚纸色微影, 一处改动全局对齐。
        // 纸色 (--scrim) 而非纯黑: 纯黑影子落在暖白纸上会发灰脏。
        sm: '0 1px 2px hsl(var(--scrim) / 0.06)',
        DEFAULT: '0 1px 2px hsl(var(--scrim) / 0.06)',
        md: '0 2px 6px -2px hsl(var(--scrim) / 0.12)',
        lg: '0 2px 6px -2px hsl(var(--scrim) / 0.12)',
        xl: '0 4px 12px -4px hsl(var(--scrim) / 0.16)',
        '2xl': '0 4px 12px -4px hsl(var(--scrim) / 0.16)',
      },
      transitionTimingFunction: {
        // §6.0.4 Linear/Vercel 同款缓动
        smooth: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [animate],
} satisfies Config
