import { useState } from 'react'
import { motion } from 'framer-motion'
import { PageHeader } from '@/components/PageHeader'
import { cn } from '@/lib/cn'

interface Variant {
  id: string
  name: string
  tagline: string
  hint: string
  nameStyle: string                   // 名字的字体语言: 字族 / 字重 / 字距
  glow: string                        // 强调色 (色块与发光线条)
}

// 同一个名字 "H-Quant" 在 4 种风格语言里的呈现
// 强调色由侧栏色块与发光线条承载; 名字本身只改字体语言, 保持墨色以免在纸面上失读
const VARIANTS: Variant[] = [
  {
    id: 'pulsar',
    name: 'H-Quant',
    tagline: 'A 股 · 信号终端',
    hint: '脉冲星、雷达波纹 — 青绿强调色，字重黑体，中等字距',
    nameStyle: 'font-sans font-extrabold tracking-[0.10em]',
    glow: '#3DD68C',
  },
  {
    id: 'vanta',
    name: 'H-Quant',
    tagline: '市场 · 智能研判',
    hint: 'Vantablack — 纯白单色，字重最重，字距最宽，单色高级感',
    nameStyle: 'font-sans font-black tracking-[0.18em]',
    glow: '#FAFAFA',
  },
  {
    id: 'helix',
    name: 'H-Quant',
    tagline: '量化 · 终端',
    hint: 'DNA 螺旋 — 紫色强调，等宽字体，赛博朋克经典意象',
    nameStyle: 'font-mono font-bold tracking-[0.08em]',
    glow: '#B7410E',
  },
  {
    id: 'aurora',
    name: 'H-Quant',
    tagline: 'A 股 · 行情看板',
    hint: '极光 — 青色强调，细字优雅，适中字距，与涨跌语义色不冲突',
    nameStyle: 'font-sans font-light tracking-[0.12em]',
    glow: '#22D3EE',
  },
]

const MOCK_NAV = ['自选', 'K 线', '策略', '回测', '信号']

export function Branding() {
  // 选定方向只落在本页 (设计稿同款: 未落库), 选中即高亮对应预览卡
  const [selected, setSelected] = useState('helix')
  const current = VARIANTS.find(v => v.id === selected) ?? VARIANTS[0]

  const exportPreview = () => {
    const spec = {
      id: current.id,
      name: current.name,
      tagline: current.tagline,
      hint: current.hint,
      nameStyle: current.nameStyle,
      accent: current.glow,
    }
    const url = URL.createObjectURL(new Blob([JSON.stringify(spec, null, 2)], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `branding-${current.id}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    // pb-24 / pr-16: 给右下角常驻的「助」悬浮球 (视口右下 24px, 距底 152px) 留出空位
    <div className="pb-24">
      <PageHeader seal="牌"
        title="品牌"
        subtitle="品牌外观"
        titleExtra={
          <>
            <span className="tb-chip">{VARIANTS.length} 个方向</span>
            <span className="tb-chip">{VARIANTS.map(v => v.id).join(' / ')}</span>
          </>
        }
        right={<button className="ctl-btn ctl-sm" onClick={exportPreview}>导出预览</button>}
      />

      <div className="figcap mt-0.5 border-t border-rule-strong pt-2.5">
        四 个 方 向 ｜ 同 一 个 名 字 在 四 种 视 觉 语 言 里 的 呈 现
      </div>

      <div className="grid grid-cols-1 gap-3.5 md:grid-cols-2">
        {VARIANTS.map((v) => (
          <Sample key={v.id} v={v} active={v.id === selected} />
        ))}
      </div>

      <div className="rowset mt-5 pr-16">
        <div className="r">
          <div className="lab">
            <b>选定方向</b>
            <small>选定后，字体、字重、字距、强调色与发光线条会应用到真实侧栏</small>
          </div>
          <div className="seg">
            {VARIANTS.map(v => (
              <button key={v.id} className={cn(v.id === selected && 'on')} aria-pressed={v.id === selected} onClick={() => setSelected(v.id)}>
                {v.id}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function Sample({ v, active }: { v: Variant; active: boolean }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        'grid min-h-[236px] grid-cols-[186px_minmax(0,1fr)] overflow-hidden border bg-base',
        active ? 'border-foreground' : 'border-border',
      )}
    >
      {/* 迷你侧栏: 墨底纸字, 预览主题落到真实侧栏后的样子 */}
      <div className="flex min-w-0 flex-col bg-foreground p-3.5 text-[var(--paper)]">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="grid h-[18px] w-[18px] shrink-0 place-items-center"
            style={{ background: v.glow, boxShadow: `0 0 12px ${v.glow}55` }}
          />
          <b className={`truncate text-[11px] leading-none ${v.nameStyle}`}>{v.name}</b>
        </div>
        <div className="mt-2 truncate font-mono text-[8px] uppercase tracking-[0.16em] opacity-60">
          {v.tagline}
        </div>
        <div
          className="my-2.5 h-px"
          style={{ background: `linear-gradient(90deg, ${v.glow}66, transparent)` }}
        />
        <div className="text-[8.5px] opacity-60">
          档位 · <b className="font-normal opacity-100">专业版</b>
        </div>
        <ul className="mt-2.5 list-none border-t border-[hsl(var(--base)/0.14)] p-0">
          {MOCK_NAV.map((label, i) => (
            <li
              key={label}
              className={cn(
                'border-l-2 px-2 py-1 text-[9.5px] opacity-70',
                i === 0
                  ? 'border-l-current bg-[hsl(var(--base)/0.07)] opacity-100'
                  : 'border-transparent',
              )}
            >
              {label}
            </li>
          ))}
        </ul>
      </div>

      {/* 右侧: 方向名 + 说明 + 底部大字名 */}
      <div className="flex min-w-0 flex-col p-3.5 pb-2.5">
        <div className="font-mono text-[8.5px] tracking-[0.2em] text-muted">{v.id}</div>
        <p className="mt-1.5 text-[10px] leading-[1.7] text-secondary">{v.hint}</p>
        <div className="mt-auto border-t border-border pt-3">
          <span className={`block text-[13px] leading-[1.5] text-foreground ${v.nameStyle}`}>
            {v.name}
          </span>
        </div>
      </div>
    </motion.div>
  )
}
