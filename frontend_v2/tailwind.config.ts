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

/** [档位, 明度] — 明度由 CSS 变量给出, 随皮肤走。
 *
 *  为什么不再写死明度: 纸底 (明度 93%) 上, 明度 68% 的饱和色只有 1.9:1 ——
 *  全仓 100 余处 text-emerald-400 / text-sky-400 读不出来。改由各皮肤自己给:
 *  浅底给墨色 (--led-ink 30%), 暗底给亮墨 (--led-ink 62%), 两边都过 AA。
 *
 *  300/400/500 合流为 --led-ink: 这三档在浅底上都是「能读的墨色」, 再分档只会
 *  互相打架 (全仓没有一处靠同族两档做层级, 已核过 71 处同文件多档用法)。
 *
 *  50/100/200 是浅底档 (淡彩底 + 近白字), 三套皮肤同值, 不进变量。
 *  600–950 各留一档: 深浅层级还在 (hover / 徽章的深字都靠它)。 */
const LEDGER_SHADE_L: Record<string, string> = {
  '50': '96%', '100': '92%', '200': '86%',
  '300': 'var(--led-ink)', '400': 'var(--led-ink)', '500': 'var(--led-ink)',
  '600': 'var(--led-600)', '700': 'var(--led-700)', '800': 'var(--led-800)',
  '900': 'var(--led-900)', '950': 'var(--led-950)',
}

/**
 * 警示族 (amber / yellow / orange) 的档位归属。
 *
 * 为什么 300 起走语义 token: 明度阶梯不看皮肤 —— amber-400 (明度 68%) 落在
 * 纸底 (明度 93%) 上只有 1.8:1, 全仓 84 处 text-amber-400 读不出来。
 * --warning 三套皮肤各取各的值 (纸底深赭 / 暗底亮金), 任何一档都落读。
 *
 * 留在阶梯上的:
 *   50/100/200 浅底档 —— 「浅底 + 深字」的老写法, 底与字必须不同色;
 *   orange 800/900/950 —— limit-ladder 那枚浅底标签的深字, 同理。
 */
const WARN_TOKEN_SHADES: Record<string, string[]> = {
  amber: ['300', '400', '500', '600', '700', '800', '900', '950'],
  yellow: ['300', '400', '500', '600', '700', '800', '900', '950'],
  orange: ['300', '400', '500', '600', '700'],
}

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
      Object.fromEntries(Object.entries(LEDGER_SHADE_L).map(([shade, l]) => [shade,
        WARN_TOKEN_SHADES[family]?.includes(shade)
          ? 'hsl(var(--warning) / <alpha-value>)'
          : `hsl(${h} 42% ${l} / <alpha-value>)`,
      ])),
    ]),
  ),
  ...Object.fromEntries(
    ['slate', 'gray', 'zinc', 'neutral', 'stone'].map(family => [
      family,
      Object.fromEntries(Object.entries(LEDGER_SHADE_L).map(([shade]) => [shade, `hsl(var(--${GREY_SHADE_TOKENS[shade]}) / <alpha-value>)`])),
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
        // 加重发丝线: 表格题头与分节题头下的分隔 (border 是常规发丝线)
        'rule-strong': 'hsl(var(--border-strong) / <alpha-value>)',
        foreground: 'hsl(var(--fg-primary) / <alpha-value>)',
        secondary:  'hsl(var(--fg-secondary) / <alpha-value>)',
        muted:      'hsl(var(--fg-muted) / <alpha-value>)',
        accent:     'hsl(var(--accent) / <alpha-value>)',
        // A 股语义色:仅用于价格 / K 线,不用于 UI 状态
        bull:       'hsl(var(--bull) / <alpha-value>)',
        bear:       'hsl(var(--bear) / <alpha-value>)',
        warning:    'hsl(var(--warning) / <alpha-value>)',
        // 实心彩色块上的字: 浅色皮肤用白字, 暗色皮肤用近黑字 (见 index.css --on-solid)
        'on-solid': 'hsl(var(--on-solid) / <alpha-value>)',
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
        // 影一致性锁: 账簿用「三级线」而非阴影分层, 浮层(菜单/弹窗/吐司)靠
        // border-border 与纸面分离, 影子只做一点点抬升。全站 40 处 shadow-lg/xl/2xl
        // 散在 30 个文件里, 逐个改是返工; 在主题层把整条刻度压成同一枚纸色微影。
        // 纸色 (--scrim) 而非纯黑: 纯黑影子落在暖白纸上会发灰脏。
        sm: '0 1px 2px hsl(var(--scrim) / 0.06)',
        DEFAULT: '0 1px 2px hsl(var(--scrim) / 0.06)',
        md: '0 1px 2px hsl(var(--scrim) / 0.06)',
        lg: '0 1px 2px hsl(var(--scrim) / 0.06)',
        xl: '0 1px 2px hsl(var(--scrim) / 0.06)',
        '2xl': '0 1px 2px hsl(var(--scrim) / 0.06)',
      },
      // 间距与尺寸一律用 Tailwind 默认 4px 网格 (gap-2 = 8px, p-4 = 16px,
      // w-16 = 64px) —— 全站一套刻度, 手写常数也能对上 (例:
      // custom/limit-ladder/Page.tsx 的 paddingLeft:72 = w-16(64) + gap-2(8))。
      // 控件高度另起一套名字明确的刻度, 写死像素:
      // sm 24px (表格内/密集筛选条) · md 28px (页面筛选器、表单) · lg 36px (弹窗主表单)。
      height: {
        'ctl-sm': '24px',
        'ctl-md': '28px',
        'ctl-lg': '36px',
      },
      transitionTimingFunction: {
        // §6.0.4 Linear/Vercel 同款缓动
        smooth: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [animate],
} satisfies Config
