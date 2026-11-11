import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Search, AlertTriangle, FlaskConical, Bell } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import { QK } from '@/lib/queryKeys'
import { resetBadge } from '@/lib/monitorBadge'

/** 参数条里的一个「标签 + 输入」格 —— 三个工具共用同一套参数条排版 */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label>{label}</label>
      {children}
    </div>
  )
}

function isoDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const CN_NUM: Record<number, string> = { 1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '七', 8: '八', 9: '九', 10: '十' }

// ── 分钟K探测 (迁移自 MinuteDataProbe) ─────────────────
interface ProbeResult {
  date: string
  rows: number
  source: string
  ok: boolean
}

function MinuteProbePanel({ days, base, onDays, onBase, results, onResults, minuteFiles }: {
  days: number
  base: string
  onDays: (n: number) => void
  onBase: (d: string) => void
  results: ProbeResult[]
  onResults: (r: ProbeResult[]) => void
  minuteFiles: number
}) {
  const [symbol, setSymbol] = useState('600519.SH')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const runProbe = async () => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return
    setLoading(true)
    setError(null)
    onResults([])

    const dates: string[] = []
    const start = new Date(`${base}T00:00:00`)
    for (let i = 0; i < days; i++) {
      const d = new Date(start)
      d.setDate(d.getDate() - i)
      dates.push(isoDay(d))
    }

    const out: ProbeResult[] = []
    try {
      for (const date of dates) {
        const r = await api.klineMinute(sym, date)
        const rows = r.rows?.length ?? 0
        out.push({
          date,
          rows,
          source: r.source ?? (rows > 0 ? 'local' : 'none'),
          ok: rows > 0,
        })
        onResults([...out])
      }
    } catch (e: any) {
      setError(e?.message ?? String(e))
    } finally {
      setLoading(false)
    }
  }

  const total = results.length
  const hasData = results.filter((r) => r.ok).length
  const missing = results.filter((r) => !r.ok)

  return (
    <div>
      <div className="sec-h mt-6">分 钟 K 数 据 探 测</div>

      <div className="tool-a grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
        <Field label="标的">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="600519.SH"
            onKeyDown={(e) => e.key === 'Enter' && !loading && runProbe()}
          />
        </Field>
        <Field label="基准日">
          <input type="date" value={base} onChange={(e) => onBase(e.target.value || base)} />
        </Field>
        <Field label="回溯天数">
          <input
            type="number"
            min={1}
            max={30}
            value={days}
            onChange={(e) => onDays(Math.max(1, Math.min(30, Number(e.target.value) || 1)))}
          />
        </Field>
        <div>
          <button type="button" className="ctl-btn ctl-sm pri" onClick={runProbe} disabled={loading || !symbol.trim()}>
            {loading ? <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> : <Search className="mr-1 inline h-3.5 w-3.5" />}
            {loading ? '探测中…' : '开始探测'}
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-3 flex items-center gap-2 border-l-2 border-danger pl-3 text-[11px] text-danger">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {total > 0 && (
        <div className="band mt-4 grid grid-cols-3">
          <div>
            <span className="k">检 测 天 数</span>
            <div className="v">{total}</div>
          </div>
          <div>
            <span className="k">有 数 据</span>
            <div className="v text-bear">{hasData}</div>
          </div>
          <div>
            <span className="k">缺 失</span>
            <div className="v text-danger">{missing.length}</div>
          </div>
        </div>
      )}

      {results.length > 0 && (
        <table className="dt mt-4">
          <thead>
            <tr>
              <th scope="col">日期</th>
              <th scope="col">分钟K条数</th>
              <th scope="col">数据来源</th>
              <th scope="col">状态</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.date}>
                <td>{r.date}</td>
                <td>{r.rows}</td>
                <td>{r.source}</td>
                <td className={r.ok ? 'ok' : 'text-danger'}>{r.ok ? '有' : '缺失'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {results.length === 0 && !error && (
        <EmptyState
          eyebrow="PROBE · 尚未探测"
          title="还没有任何一天的探测结果"
          hint={minuteFiles > 0
            ? `探测逐日调用分钟K接口，打印每天的行数、来源与缺失日期。当前 minute_files = ${minuteFiles}，本地已有分钟分区，缺失的日期会落到「本地无数据」。`
            : '探测逐日调用分钟K接口，打印每天的行数、来源与缺失日期。当前 minute_files = 0，本地没有分钟分区，结果会全部落到「本地无数据」。'}
        />
      )}

      {missing.length > 0 && (
        <div className="mt-4 border-t border-rule-strong pt-3 text-[10.5px] leading-[1.75] text-secondary">
          <div className="mb-1 flex items-center gap-1.5 text-warning">
            <AlertTriangle className="h-3.5 w-3.5" /> 缺失日期的诊断
          </div>
          <p>
            缺失日期若为<span className="text-foreground">周末/节假日</span>属正常；
            若为<span className="text-foreground">停牌日</span>（成交量为 0）也属正常；
            若为<span className="text-foreground">正常交易日</span>（日K有成交量）却缺失分钟K，
            则是实时数据源未提供该日分钟数据。
          </p>
        </div>
      )}
    </div>
  )
}

// ── 演示数据生成 ──────────────────────────────────────
function SeedPanel() {
  const qc = useQueryClient()
  const [count, setCount] = useState(12)
  const [recent, setRecent] = useState(true)
  const [msg, setMsg] = useState('')

  const seedMut = useMutation({
    mutationFn: () => api.alertSeed(count, recent),
    onSuccess: (data) => {
      setMsg(`已生成 ${data.generated} 条触发记录`)
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['alerts-total'] })
      setTimeout(() => setMsg(''), 4000)
    },
    onError: () => {
      setMsg('生成失败')
      setTimeout(() => setMsg(''), 4000)
    },
  })

  const clearMut = useMutation({
    mutationFn: () => api.alertsClear(),
    onSuccess: (data) => {
      setMsg(`已清空 ${data.cleared} 条触发记录`)
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['alerts-total'] })
      resetBadge()
      setTimeout(() => setMsg(''), 4000)
    },
  })

  const ruleSeedMut = useMutation({
    mutationFn: () => api.monitorRuleSeed(),
    onSuccess: (data) => {
      setMsg(`已生成 ${data.generated} 条监控规则`)
      qc.invalidateQueries({ queryKey: QK.monitorRules })
      setTimeout(() => setMsg(''), 4000)
    },
    onError: () => {
      setMsg('规则生成失败')
      setTimeout(() => setMsg(''), 4000)
    },
  })

  return (
    <div>
      <div className="sec-h mt-6">监 控 触 发 记 录 演 示 数 据</div>
      <p className="mb-2 max-w-[70ch] text-[10.5px] leading-[1.75] text-secondary">
        生成模拟的触发记录，用于测试监控中心页面的展示效果、未读徽标、新增闪烁。生成的数据可随时清空。
      </p>

      <div className="tool-a grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <Field label="规则条数">
          <input
            type="number"
            min={1}
            max={50}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
          />
        </Field>
        <Field label="时间戳">
          <select value={recent ? 'now' : 'real'} onChange={(e) => setRecent(e.target.value === 'now')}>
            <option value="now">刚刚 (测试闪烁)</option>
            <option value="real">按真实时间 (近 3 天)</option>
          </select>
        </Field>
        <div>
          <button type="button" className="ctl-btn ctl-sm" onClick={() => seedMut.mutate()} disabled={seedMut.isPending}>
            {seedMut.isPending ? <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> : <FlaskConical className="mr-1 inline h-3.5 w-3.5" />}
            生成
          </button>
          <button type="button" className="ctl-btn ctl-sm ctl-ghost" onClick={() => clearMut.mutate()} disabled={clearMut.isPending}>
            清空演示数据
          </button>
        </div>
      </div>

      {msg && (
        <div className="mt-3 border-l-2 border-accent pl-3 text-[11px] text-accent">{msg}</div>
      )}

      {/* 监控规则生成 */}
      <div className="sec-h">监 控 规 则</div>
      <div className="tool-a grid-cols-[minmax(0,1fr)_auto]">
        <Field label="规则类型">
          <input value="信号 / 价格 / 市场异动 / 策略变更" readOnly />
        </Field>
        <div>
          <button type="button" className="ctl-btn ctl-sm" onClick={() => ruleSeedMut.mutate()} disabled={ruleSeedMut.isPending}>
            {ruleSeedMut.isPending ? <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> : <FlaskConical className="mr-1 inline h-3.5 w-3.5" />}
            生成演示规则
          </button>
        </div>
      </div>
    </div>
  )
}

// ── 封单监控模拟触发 ──────────────────────────────────
function LadderTestPanel({ ladderCount }: { ladderCount: number }) {
  const [result, setResult] = useState<Awaited<ReturnType<typeof api.monitorRuleTestLadder>> | null>(null)
  const [error, setError] = useState('')
  const [pushMsg, setPushMsg] = useState('')

  const testMut = useMutation({
    mutationFn: () => api.monitorRuleTestLadder(),
    onSuccess: (data) => { setResult(data); setError('') },
    onError: (e: any) => { setError(e?.message ?? String(e)); setResult(null) },
  })

  const triggerMut = useMutation({
    mutationFn: () => api.monitorRuleTriggerLadder(),
    onSuccess: (data) => {
      setPushMsg(`✅ 已真实触发 ${data.triggered} 条预警 (落盘 + 飞书 + SSE)`)
      setTimeout(() => setPushMsg(''), 6000)
    },
    onError: (e: any) => {
      setPushMsg(`❌ 触发失败: ${e?.message ?? String(e)}`)
      setTimeout(() => setPushMsg(''), 6000)
    },
  })

  const fmtVal = (v: number | null | undefined, metric: string) => {
    if (v == null) return '—'
    if (metric === 'sealed_amount') return `${(v / 1e8).toFixed(4)} 亿`
    return `${v.toLocaleString()} 手`
  }

  return (
    <div>
      <div className="sec-h mt-6">封 单 监 控 模 拟 触 发</div>
      <p className="mb-2 max-w-[70ch] text-[10.5px] leading-[1.75] text-secondary">
        用当日五档封单数据 + 最新增强表，评估所有连板梯队规则。
      </p>

      <div className="tool-a grid-cols-[minmax(0,1fr)_auto]">
        <Field label="封单额阈值">
          <input value="1.0 亿" readOnly />
        </Field>
        <div>
          <button type="button" className="ctl-btn ctl-sm" onClick={() => testMut.mutate()} disabled={testMut.isPending}>
            {testMut.isPending ? <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" /> : <Bell className="mr-1 inline h-3.5 w-3.5" />}
            运行评估
          </button>
          <button
            type="button"
            className="ctl-btn ctl-sm"
            onClick={() => {
              if (confirm('将真实推送飞书 + 写入监控中心 + 触发 SSE 通知。确认?')) {
                triggerMut.mutate()
              }
            }}
            disabled={triggerMut.isPending}
          >
            真实触发预警
          </button>
        </div>
      </div>

      {pushMsg && (
        <div className="mt-3 border-l-2 border-accent pl-3 text-[11px] text-accent">{pushMsg}</div>
      )}

      {error && (
        <div className="mt-3 flex items-center gap-2 border-l-2 border-danger pl-3 text-[11px] text-danger">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {result && result.triggered.length > 0 && (
        <div className="mt-5">
          <div className="figcap">会 触 发 ({result.triggered.length})</div>
          {result.triggered.map((ev) => (
            <div key={ev.rule_id} className="border-b border-border py-2.5 text-[11px]">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-foreground">{ev.symbol}</span>
                {ev.name && <span className="text-secondary">{ev.name}</span>}
                <span className="ml-auto font-mono text-[9.5px] text-muted">{ev.type}</span>
              </div>
              <div className="text-secondary">{ev.message}</div>
              <div className="mt-1 text-muted tabular-nums">
                当前封单: {fmtVal(ev.sealed_metric === 'sealed_amount' ? ev.current_sealed_amount : ev.current_sealed_vol, ev.sealed_metric)}
              </div>
            </div>
          ))}
        </div>
      )}

      {result && result.not_triggered.length > 0 && (
        <div className="mt-5">
          <div className="figcap">未 触 发 ({result.not_triggered.length})</div>
          {result.not_triggered.map((r) => (
            <div key={r.rule_id} className="border-b border-border py-2.5 text-[11px]">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-foreground">{r.symbol}</span>
                <span className="text-secondary">{r.rule_name}</span>
              </div>
              <div className="mt-1 text-muted tabular-nums">
                阈值 {fmtVal(r.threshold, r.metric)} · 当前 {fmtVal(r.current_value, r.metric)} · {r.reason}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 空态常驻: 没有封单规则时, 结果区始终是这句提示 (设计稿 29) */}
      {(!result || (result.triggered.length === 0 && result.not_triggered.length === 0)) && (
        <EmptyState
          eyebrow={ladderCount > 0 ? 'DEPTH · 待评估' : `DEPTH · ${ladderCount} 条规则`}
          title={ladderCount === 0
            ? '连板梯队里还没有监控规则'
            : result ? '没有规则被命中' : '还没有运行评估'}
          hint={ladderCount === 0
            ? '「会触发 / 未触发」两组结果需要至少一条封单规则。先在连板梯队页建规则，再回来评估。'
            : result
              ? '所有连板梯队规则的封单条件都未达到阈值。'
              : '点「运行评估」用当日五档封单数据评估所有连板梯队规则。'}
          action={ladderCount === 0 ? (
            <Link to="/limit-ladder" className="ctl-btn ctl-sm">去连板梯队</Link>
          ) : undefined}
        />
      )}
    </div>
  )
}

// ── Dev 主页面 ────────────────────────────────────────
export function Dev() {
  const navigate = useNavigate()
  // 页头状态签 (design/v2/29): 分钟K 分区 / 监控规则条数, 均为已有接口
  const rules = useQuery({ queryKey: QK.monitorRules, queryFn: api.monitorRulesList, staleTime: 30_000 })
  const status = useQuery({ queryKey: QK.dataStatus, queryFn: api.dataStatus, staleTime: 30_000 })
  const minuteFiles = status.data?.storage?.minute_files ?? 0
  const ruleCount = rules.data?.rules?.length ?? 0
  const ladderCount = rules.data?.rules?.filter(r => r.type === 'ladder').length ?? 0

  // 探测状态提到页面层: 顶部读数带要显示「最近 N 日探测」跑过没有
  const [probeDays, setProbeDays] = useState(5)
  const [probeBase, setProbeBase] = useState(() => isoDay(new Date()))
  const [probeResults, setProbeResults] = useState<ProbeResult[]>([])
  const probedDays = probeResults.length
  const probedHit = probeResults.filter(r => r.ok).length

  return (
    <div className="flex flex-col h-full">
      <PageHeader seal="诊"
        title="开发工具"
        subtitle="DEVELOPER"
        titleExtra={
          <>
            <span className={`tb-chip${minuteFiles > 0 ? '' : ' warn'}`}>{minuteFiles > 0 ? '分钟K 已就绪' : '分钟K 本地为空'}</span>
            <span className="tb-chip">规则 {ruleCount} 条</span>
            <span className="tb-chip">演示数据可用</span>
          </>
        }
        right={<button className="ctl-btn ctl-sm" onClick={() => navigate('/')}>返回面板</button>}
      />
      <div className="flex-1 overflow-auto">
        <div className="pb-10">
          {/* 6 列栅格只填 4 格 — 右侧两格留白 (design/v2/29-dev.html) */}
          <div className="readout mt-2">
            <div className={cn(minuteFiles === 0 && 'down')}>
              <span className="k">分 钟 K 分 区</span>
              <div className="v">{minuteFiles}</div>
              <small>本地存储 · {minuteFiles} 个分区</small>
            </div>
            <div>
              <span className="k">最 近 {CN_NUM[probeDays] ?? probeDays} 日 探 测</span>
              <div className="v">{probedDays > 0 ? '已 执 行' : '未 执 行'}</div>
              <small>{probedDays > 0 ? `${probedDays} 天 · 有数据 ${probedHit}` : '点「开始探测」逐日拉 1m'}</small>
            </div>
            <div>
              <span className="k">监 控 规 则</span>
              <div className="v">{ruleCount}</div>
              <small>可用于生成演示触发</small>
            </div>
            <div>
              <span className="k">封 单 规 则</span>
              <div className="v">{ladderCount}</div>
              <small>depth 数据已定版</small>
            </div>
          </div>

          <MinuteProbePanel
            days={probeDays}
            base={probeBase}
            onDays={setProbeDays}
            onBase={setProbeBase}
            results={probeResults}
            onResults={setProbeResults}
            minuteFiles={minuteFiles}
          />
          <SeedPanel />
          <LadderTestPanel ladderCount={ladderCount} />
        </div>
      </div>
    </div>
  )
}
