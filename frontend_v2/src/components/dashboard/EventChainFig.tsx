import type { CSSProperties } from 'react'
import type { EventChain } from '@/lib/api'

/** 事件传导链粒子图 — 事实在左, 推断沿管道向右流动。
 *
 *  节点就是 `macro_path` 里 `→` 分隔的每一段, 末节点着强调色表示推断落到 A 股。
 *  纯 SVG + CSS 动画: 无 rAF、无 canvas, SSR 可直接出图, 动效偏好 reduce 时静止。
 */
const W = 620
const H = 78
const Y = 46
const PAD = 62

export function EventChainFig({ events }: { events: EventChain[] }) {
  return (
    <div className="evfig">
      {events.map(e => {
        const steps = e.inference.macro_path.split('→').map(s => s.trim()).filter(Boolean)
        const labels = [`事实 ${e.headline_count} 条`, ...steps, 'A 股映射']
        const last = labels.length - 1
        const gap = (W - PAD * 2) / last
        const x = (i: number) => PAD + i * gap
        const span = `${x(last) - x(0)}px`
        const evidence = e.facts.headlines.map(h => h.title).filter(Boolean).slice(0, 2).join('；')
        const reads = e.facts.watch
          .map(w => `${w.code} ${w.value ?? '—'}${w.unit ?? ''}`)
          .join(' / ')
        return (
          <div key={e.key} className="evfig-item">
            <div className="evfig-hd">
              <b>{e.label}</b>
              <span>{e.headline_count} 条标题</span>
            </div>

            <div className="evfig-scroll">
              <svg
                viewBox={`0 0 ${W} ${H}`} className="evfig-svg" role="img"
                aria-label={`${e.label}: 事实经 ${steps.join('、')} 传导到 ${e.inference.a_share_lens}`}
              >
                <line x1={x(0)} y1={Y} x2={x(last)} y2={Y} className="evfig-pipe" />
                {labels.map((t, i) => (
                  <g key={`${t}-${i}`} className={`evfig-node${i === 0 ? ' src' : i === last ? ' out' : ''}`}>
                    <circle cx={x(i)} cy={Y} r={i === 0 ? 3.5 : i === last ? 5 : 4} />
                    <text x={x(i)} y={Y + 17} textAnchor="middle">{t}</text>
                  </g>
                ))}
                {[0, 1, 2].map(k => (
                  <circle
                    key={k} cx={x(0)} cy={Y} r={1.7} className="evfig-pt"
                    style={{ '--evfig-span': span, animationDelay: `${-k * 1.4}s` } as CSSProperties}
                  />
                ))}
              </svg>
            </div>

            <p className="evfig-note">
              {evidence && <span>证据 · {evidence}</span>}
              {reads && <span>指标 · {reads}</span>}
              <span>落点 · {e.inference.a_share_lens}</span>
              <span className="warn">失效 · {e.inference.invalidation}</span>
            </p>
          </div>
        )
      })}
    </div>
  )
}
