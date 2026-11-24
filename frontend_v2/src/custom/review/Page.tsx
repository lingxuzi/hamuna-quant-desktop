/**
 * AI 大盘复盘页 —— 以流式 LLM 复盘报告为主体的盘后复盘工作台。
 *
 * 设计定位:极简专注型。不复刻 Dashboard 的看板(KPI/雷达/板块排名),
 * 仅保留一行「市场摘要条」作为报告上下文参照;AI 报告 + 历史归档是页面主体。
 *  - 摘要数据:GET /api/overview/market
 *  - 报告流式:POST /api/market-recap/analyze
 *
 * 归属: 扩展域消费页 (官方), 经 custom/review/extension.tsx 注册路由与导航;
 * 对核心能力均为只读 import (api/QK/组件/状态库), 不承载核心域语义。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import {
  BookOpenCheck, RefreshCw, Sparkles, Trash2, ChevronRight,
  Wand2, Copy, Download, Clock, X, Check, Trophy, ChevronDown, ChevronUp,
} from 'lucide-react'

import { api, type OverviewMarket, type OverviewDimensionRankItem, type AiReviewReport, type DragonTigerStockItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { fmtPct, fmtVolume, priceColorClass } from '@/lib/format'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { boardTag } from '@/components/stock-table/primitives'
import { PageHeader } from '@/components/PageHeader'
import { DistFig } from '@/components/charts/DistFig'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { toast } from '@/components/Toast'
import { Switch } from '@/components/Switch'
import { usePreferences } from '@/lib/useSharedQueries'
import { useReviewState } from '@/lib/useReviewStore'
import {
  startReviewGeneration, resetReview, isReviewGenerating, cancelReviewGeneration,
  type ReviewPhase,
} from '@/lib/reviewStore'
import { cnToday } from '@/lib/cnDate'

// ================================================================
// 涨跌幅格式化(注意单位差异)
// overview 的 indices.change_pct / breadth.up_pct / seal_rate / *_pct / emotion.score
//   都是【已是百分比值】(如 1.2 表示 1.2%),直接 toFixed 即可,不要 *100。
// ================================================================
function fmtPctAlready(v: number | null | undefined, digits = 2, withSign = false): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = withSign && v > 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}
// A 股惯例: 强势=红, 弱式=绿(对齐 Dashboard scoreColor)
function scoreColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '#71717A'
  if (v >= 70) return '#F04438'
  if (v >= 55) return '#FB923C'
  if (v >= 45) return '#F59E0B'
  if (v >= 30) return '#84CC16'
  return '#12B76A'
}

// 归档时刻格式化:ISO → "MM-DD HH:mm"(用于历史列表显示复盘时间)
function fmtArchivedAt(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

// Phase 类型复用 store 的定义(单一来源)

export function ReviewPage() {
  const qc = useQueryClient()
  // 复盘日期:当前固定取最新交易日(后续如需日期选择可改回 useState)
  const asOf: string | undefined = undefined
  const [focus, setFocus] = useState('')
  // 生成状态走全局 store:切走页面流不中断,回来可恢复
  const { phase, content, error, meta } = useReviewState()
  const [viewing, setViewing] = useState<AiReviewReport | null>(null)  // 查看历史报告
  const reportEndRef = useRef<HTMLDivElement>(null)

  // 看板数据(与总览页同源)
  const marketQuery = useQuery<OverviewMarket>({
    queryKey: QK.overviewMarket(asOf),
    queryFn: () => api.overviewMarket(asOf),
    staleTime: 5_000,
    placeholderData: (prev) => prev,
  })

  // 历史报告
  const historyQuery = useQuery<{ reports: AiReviewReport[] }>({
    queryKey: QK.reviewReports,
    queryFn: () => api.reviewReportsList(),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.reviewReportDelete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.reviewReports })
      toast('已删除', 'success')
    },
    onError: () => { /* request() 已 toast */ },
  })

  // ===== 定时复盘 =====
  const [showSchedule, setShowSchedule] = useState(false)
  // 弹窗入口:schedule=定时与推送设置, prompt=只看复盘提示词(设计稿空态的「查看提示词」)
  const [scheduleTab, setScheduleTab] = useState<'schedule' | 'prompt'>('schedule')
  const prefs = usePreferences()
  const reviewSched = prefs.data?.review_schedule ?? { enabled: false, hour: 15, minute: 10 }
  const feishuConfigured = !!(prefs.data?.feishu_webhook_url)
  const wecomConfigured = !!(prefs.data?.wecom_webhook_url)
  const customConfigured = !!(prefs.data?.custom_webhook_url)
  const emailConfigured = !!(
    prefs.data?.email_smtp_config?.host
    && prefs.data.email_smtp_config.from_address
    && prefs.data.email_smtp_config.to_addresses.length
    && (!prefs.data.email_smtp_config.username || prefs.data.email_smtp_password_set)
  )
  // 推送渠道是独立的顶层偏好(多选), 与定时 / 实时行情无关, 常驻可单独设置
  // []=不推送; 可多选飞书、企微、第三方 Webhook 和邮件。
  const reviewPushChannels = prefs.data?.review_push_channels ?? []
  // 推送触发方式: auto=归档即推; manual=仅归档不自动外发.默认 manual。
  const reviewPushMode = prefs.data?.review_push_mode ?? 'manual'
  // 弹窗内的本地草稿: 开关和时间都在本地改, 点「保存」才真正提交(避免开关一拨就关弹窗)
  const [draft, setDraft] = useState(reviewSched)
  const openSchedule = useCallback(() => {
    setDraft(reviewSched)  // 每次打开同步最新服务端值
    setScheduleTab('schedule')
    setShowSchedule(true)
  }, [reviewSched])
  const reviewMut = useMutation({
    mutationFn: ({ enabled, hour, minute }: { enabled: boolean; hour: number; minute: number }) =>
      api.updateReviewSchedule(enabled, hour, minute),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: QK.preferences })
      setShowSchedule(false)
      toast(vars.enabled ? '已开启定时复盘' : '已关闭定时复盘', 'success')
    },
    onError: () => { /* request() 已 toast */ },
  })
  // 推送渠道(多选): 独立常驻, 即时生效(勾选渠道即开关, 改了立刻提交)
  const pushMut = useMutation({
    mutationFn: (channels: string[]) => api.updateReviewPush(channels),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: QK.preferences })
      toast(vars.length === 0 ? '已关闭复盘推送' : '已更新复盘推送渠道', 'success')
    },
    onError: () => { /* request() 已 toast */ },
  })
  const togglePushChannel = useCallback((ch: string) => {
    const next = reviewPushChannels.includes(ch)
      ? reviewPushChannels.filter(c => c !== ch)
      : [...reviewPushChannels, ch]
    pushMut.mutate(next)
  }, [reviewPushChannels, pushMut])
  // 推送触发方式(独立常驻): auto/manual 即时生效, 与渠道切换一致
  const pushModeMut = useMutation({
    mutationFn: (mode: 'auto' | 'manual') => api.updateReviewPush(reviewPushChannels, mode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK.preferences })
      toast('已更新推送触发方式', 'success')
    },
    onError: () => { /* request() 已 toast */ },
  })

  // ===== 自定义复盘提示词 =====
  // 常驻拉取(自定义值 + 默认全文); 草稿 null=未编辑, 跟随服务端值
  const promptQuery = useQuery({
    queryKey: ['market-recap-prompt'],
    queryFn: api.getMarketRecapPrompt,
    staleTime: 30_000,
  })
  const [promptDraft, setPromptDraft] = useState<string | null>(null)
  useEffect(() => { setPromptDraft(null) }, [promptQuery.data])
  const promptCustomized = !!promptQuery.data?.customized
  const promptText = promptDraft ?? promptQuery.data?.custom ?? ''
  const promptMut = useMutation({
    mutationFn: (prompt: string) => api.updateMarketRecapPrompt(prompt),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: QK.preferences })
      qc.invalidateQueries({ queryKey: ['market-recap-prompt'] })
      setPromptDraft(null)
      toast(vars.trim() ? '已保存复盘提示词' : '已恢复默认提示词', 'success')
    },
    onError: () => { /* request() 已 toast */ },
  })

  // 设计稿空态的「查看提示词」入口: 打开弹窗的提示词页
  const openPrompt = useCallback(() => {
    setScheduleTab('prompt')
    setShowSchedule(true)
  }, [])

  // 自动滚动到报告底部(streaming 时)
  useEffect(() => {
    if (phase === 'streaming') {
      reportEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [content, phase])

  // 当进入生成中(streaming)时, 清掉「查看历史」状态, 让主区域显示流内容。
  // 手动 generate 已自带 setViewing(null), 这里主要补定时 SSE 流的场景:
  // 用户若正看着历史报告, 定时触发生成时也要切回主区域显示流式内容。
  useEffect(() => {
    if (phase === 'streaming' && viewing) {
      setViewing(null)
    }
  }, [phase, viewing])

  // 自动归档(生成完成后台静默保存)—— 通过回调注入 store,避免 store 直接依赖 qc/marketQuery
  const onGenerationDone = useCallback(async (fullContent: string, doneMeta: { as_of?: string; summary?: string; emotion_score?: number; emotion_label?: string } | null) => {
    const reportAsOf = doneMeta?.as_of ?? marketQuery.data?.as_of ?? asOf ?? cnToday()
    try {
      await api.reviewReportSave({
        as_of: reportAsOf,
        focus,
        content: fullContent,
        summary: doneMeta?.summary,
        emotion_score: doneMeta?.emotion_score ?? null,
        emotion_label: doneMeta?.emotion_label ?? '',
      })
      qc.invalidateQueries({ queryKey: QK.reviewReports })
    } catch { /* 静默 */ }
  }, [focus, asOf, marketQuery.data, qc])

  // 主流程:生成复盘(委托给全局 store,流在后台独立运行)
  const generate = useCallback(() => {
    if (isReviewGenerating()) return
    setViewing(null)
    resetReview()
    startReviewGeneration(asOf, focus, (full, doneMeta) => {
      onGenerationDone(full, doneMeta).catch(() => { /* 静默 */ })
    })
  }, [asOf, focus, onGenerationDone])

  // 复制全文到剪贴板(viewing 优先,与主区域显示一致)
  const copyContent = useCallback(async () => {
    const text = viewing?.content ?? content
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      toast('已复制到剪贴板', 'success')
    } catch {
      toast('复制失败，请手动选择文本', 'error')
    }
  }, [content, viewing])

  // 下载为 .md 文件(viewing 优先)
  const downloadContent = useCallback(() => {
    const text = viewing?.content ?? content
    if (!text) return
    const reportDate = viewing?.as_of ?? meta?.as_of ?? asOf ?? cnToday()
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `复盘_${reportDate}.md`
    a.click()
    URL.revokeObjectURL(url)
  }, [content, viewing, meta, asOf])

  // 查看历史报告(不中断后台生成:仅临时把 viewing 覆盖到主区域,
  // 生成中的流仍在 store 里继续跑,点"生成中"项即可切回)
  const viewReport = useCallback((r: AiReviewReport) => {
    setViewing(r)
  }, [])

  const isGenerating = phase === 'loading' || phase === 'streaming'
  const displayDate = viewing?.as_of ?? meta?.as_of ?? marketQuery.data?.as_of ?? asOf ?? '最新'
  // 龙虎榜跟随复盘目标日 (历史复盘联动当期榜单); 非 ISO 日期(如"最新")取最近一期
  const dtDate = /^\d{4}-\d{2}-\d{2}$/.test(displayDate ?? '') ? displayDate : undefined
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const data = marketQuery.data
  // 主区域显示的内容:viewing(查看历史)优先于 store 的生成 content,
  // 这样点历史报告不会覆盖后台生成中的流。
  const displayContent = viewing?.content ?? content
  // 工具条状态签 (design/v2/22): 情绪分 / 已存报告 / 龙虎榜只数
  const emotionScore = data?.emotion?.score
  const reportCount = historyQuery.data?.reports.length ?? 0
  const lhb = useQuery({
    queryKey: ['dragon-tiger', dtDate ?? 'latest'],
    queryFn: () => api.dragonTiger(dtDate),
    staleTime: 5 * 60_000,
    retry: 1,
  })
  const lhbCount = lhb.data?.all?.stock_count ?? 0

  return (
    <>
      <PageHeader seal="复"
        title="复盘"
        titleExtra={
          <>
            {emotionScore != null && (
              <span className={`tb-chip${emotionScore <= 30 ? ' warn' : ''}`}>
                情绪 {Math.round(emotionScore)} {data?.emotion?.label ?? ''}
              </span>
            )}
            <span className={`tb-chip${reportCount ? '' : ' warn'}`}>报告 {reportCount} 篇</span>
            <span className="tb-chip">龙虎榜 {lhbCount} 只</span>
          </>
        }
        subtitle="MARKET RECAP"
        right={
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => { marketQuery.refetch() }}
              disabled={marketQuery.isFetching}
              className="ctl ctl-md ctl-btn"
              title="刷新市场数据"
            >
              <RefreshCw className={cn('mr-1 inline h-[12px] w-[12px]', marketQuery.isFetching && 'animate-spin')} />刷新
            </button>
            <button
              onClick={openSchedule}
              className={cn('ctl ctl-md ctl-btn', reviewSched.enabled && 'pri')}
              title={reviewSched.enabled ? `定时复盘已开启 · 每日 ${String(reviewSched.hour).padStart(2,'0')}:${String(reviewSched.minute).padStart(2,'0')}` : '定时复盘'}
            >
              <Clock className="mr-1 inline h-[12px] w-[12px]" />定时
            </button>
            <button
              onClick={generate}
              disabled={isGenerating}
              className={cn('ctl ctl-md ctl-btn', !isGenerating && 'pri')}
            >
              {isGenerating ? (
                <><RefreshCw className="mr-1 inline h-3.5 w-3.5 animate-spin" />生成中…</>
              ) : (
                <><Sparkles className="mr-1 inline h-3.5 w-3.5" />生成复盘</>
              )}
            </button>
          </div>
        }
      />

      <div className="min-h-full">
        <div className="space-y-3">

          {marketQuery.isLoading && !data ? (
            <div className="flex h-40 items-center justify-center">
              <div className="flex items-center gap-2 text-sm text-muted">
                <RefreshCw className="h-[16px] w-[16px] animate-spin" /> 加载市场数据…
              </div>
            </div>
          ) : !data || !data.as_of ? (
            <div className="flex flex-col items-center justify-center gap-4 border border-border bg-surface px-6 py-14 text-center">
              <span className="font-mono text-[9.5px] tracking-[0.2em] text-accent">NO MARKET DATA</span>
              <h2 className="font-serif text-[17px] font-medium text-foreground">暂无市场数据</h2>
              <p className="max-w-[42ch] text-[11.5px] leading-[1.8] text-secondary">复盘需要日 K 与指数，请先前往「数据」页同步</p>
              <Link
                to="/data"
                className="ctl ctl-md ctl-btn pri"
              >
                前往数据页同步 <ChevronRight className="inline h-3.5 w-3.5" />
              </Link>
            </div>
          ) : (
            <>
              {/* ===== 顶端读数带 (design/v2/22 的 .b4h) ===== */}
              <MarketSummaryBar data={data} />

              {/* ===== 主体双栏: 左论题 / 右须知 (design/v2/22 的 .rv) ===== */}
              <div className="mt-5 grid grid-cols-1 gap-6 border-t border-rule-strong pt-4 xl:grid-cols-[minmax(0,1fr)_330px]">
                <div className="min-w-0">
                  {/* ===== 关注点输入 ===== */}
                  <div className="flex items-center gap-2 border border-border bg-surface px-3 py-1.5 transition-colors focus-within:border-accent/40">
                    <Wand2 className="h-3.5 w-3.5 shrink-0 text-accent" />
                    <input
                      value={focus}
                      onChange={(e) => setFocus(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && !isGenerating) generate() }}
                      placeholder="可选：补充复盘关注点，如「半导体板块持续性如何」「量能是否持续」"
                      className="ctl ctl-md ctl-input flex-1 bg-transparent font-mono placeholder:text-muted/60"
                    />
                    {focus && (
                      <button onClick={() => setFocus('')} className="text-[11px] text-muted transition-colors hover:text-foreground">清除</button>
                    )}
                  </div>

                  <div className="mt-4">
                    <ReportPanel
                      phase={phase}
                      content={displayContent}
                      error={error}
                      isGenerating={isGenerating}
                      viewing={viewing}
                      onCopy={copyContent}
                      onDownload={downloadContent}
                      onRegenerate={generate}
                      onViewPrompt={openPrompt}
                      reportEndRef={reportEndRef}
                    />
                  </div>

                  {/* ===== 涨 跌 分 布 · 8 档 (design/v2/22 的 .dist) ===== */}
                  <DistributionFig rows={data.distribution} />

                  {/* ===== 龙虎榜 (fuyao 专有, 资金动向上下文; 复盘日联动) ===== */}
                  <DragonTigerCard date={dtDate} onOpenStock={setPreviewSymbol} />

                  {/* ===== 连 板 梯 队 (design/v2/22) ===== */}
                  <LimitLadderTable tiers={data.limit?.tiers ?? []} onOpenStock={setPreviewSymbol} />
                </div>

                <aside className="min-w-0 xl:border-l xl:border-border xl:pl-5">
                  <IndexTable data={data} />

                  {/* ===== 六 维 情 绪 / 均 线 站 位 / 领 涨 概 念 (design/v2/22 右栏) ===== */}
                  <EmotionRadar6 radar={data.radar} />
                  <MaPositionTable trend={data.trend} />
                  <LeadingConceptTable items={data.concept_rank?.leading ?? []} />

                  <div className="sec-h">历 史 复 盘</div>
                  <HistoryPanel
                    reports={historyQuery.data?.reports ?? []}
                    loading={historyQuery.isLoading}
                    viewingId={viewing?.id ?? null}
                    generating={isGenerating}
                    onView={viewReport}
                    onBackToGenerating={() => setViewing(null)}
                    onDelete={(id) => deleteMut.mutate(id)}
                  />
                </aside>
              </div>
            </>
          )}
        </div>
      </div>

      {/* ===== 定时复盘设置弹窗 ===== */}
      <AnimatePresence>
        {showSchedule && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
            onClick={() => setShowSchedule(false)}
          >
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              className="w-full max-w-md border border-border bg-surface shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {scheduleTab === 'prompt'
                    ? <Wand2 className="h-[16px] w-[16px] text-accent" />
                    : <Clock className="h-[16px] w-[16px] text-accent" />}
                  <h3 className="text-sm font-medium text-foreground">
                    {scheduleTab === 'prompt' ? '复盘提示词' : '定时复盘'}
                  </h3>
                </div>
                <button
                  onClick={() => setShowSchedule(false)}
                  className="ctl ctl-sm ctl-ghost ctl-icon text-muted hover:bg-elevated hover:text-foreground"
                >
                  <X className="h-[16px] w-[16px]" />
                </button>
              </div>

              {scheduleTab === 'prompt' ? (
                <>
                  <p className="mb-4 text-[11px] leading-relaxed text-muted">
                    留空使用内置默认提示词；也可点下方「填入默认模板」后在其基础上修改。保存后立即生效，用于下一次生成复盘。
                  </p>

                  <button type="button" onClick={() => setScheduleTab('schedule')} className="ctl-btn ctl-sm ctl-ghost mb-3">
                    定时与推送设置
                  </button>

                  <div className="rounded-btn bg-elevated/40 px-3 py-2.5">
                    <textarea
                      value={promptText}
                      onChange={e => setPromptDraft(e.target.value)}
                      spellCheck={false}
                      placeholder="留空使用内置默认提示词；也可点下方「填入默认模板」后在其基础上修改"
                      className="ctl-textarea ctl-textarea-sm h-44 w-full resize-y bg-base leading-relaxed placeholder:text-muted/60 focus:border-accent/50"
                    />
                    <div className="mt-1.5 flex items-center justify-between">
                      <span className="text-[9px] text-muted/70">
                        {promptText.length.toLocaleString()} / 32,000 字符
                        {promptCustomized && ' · 自定义后不再跟随版本默认模板更新'}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          disabled={promptQuery.isLoading || !promptQuery.data}
                          onClick={() => setPromptDraft(promptQuery.data?.default ?? '')}
                          className="ctl ctl-sm ctl-btn px-1.5 border-border/60 hover:text-foreground disabled:opacity-50 bg-base"
                        >
                          填入默认模板
                        </button>
                        <button
                          type="button"
                          disabled={promptMut.isPending || !promptCustomized}
                          onClick={() => promptMut.mutate('')}
                          className="ctl ctl-sm ctl-btn filled px-1.5 border-border/60 hover:text-foreground disabled:opacity-50"
                        >
                          恢复默认
                        </button>
                        <button
                          type="button"
                          disabled={promptMut.isPending || promptQuery.isLoading}
                          onClick={() => promptMut.mutate(promptText)}
                          className="ctl ctl-sm ctl-btn pri px-1.5 hover:bg-accent/90 disabled:opacity-50"
                        >
                          {promptMut.isPending ? '保存中…' : '保存'}
                        </button>
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <>
              <p className="mb-4 text-[11px] leading-relaxed text-muted">
                开启后，每个交易日到点自动生成大盘复盘报告并归档，静默执行。
                下次打开本页即可在历史列表看到新报告；也可选推送到飞书。
              </p>

              {/* 开关(只改本地草稿, 不提交) */}
              <label className="flex items-center justify-between rounded-btn bg-elevated/40 px-3 py-2.5">
                <span className="text-xs text-foreground">启用定时复盘</span>
                <Switch checked={draft.enabled} onChange={next => setDraft(d => ({ ...d, enabled: next }))} label="启用定时复盘" />
              </label>

              {/* 时间设置(仅开启时可编辑, 本地草稿) */}
              {draft.enabled && (
                <div className="mt-3 flex items-center gap-2 rounded-btn bg-elevated/40 px-3 py-2.5">
                  <span className="text-[11px] text-muted">每日</span>
                  <input
                    type="number" min={0} max={23} value={draft.hour}
                    onChange={e => setDraft(d => ({ ...d, hour: Math.max(0, Math.min(23, Number(e.target.value))) }))}
                    className="ctl ctl-sm ctl-input w-12 px-1.5 font-mono text-center focus:border-accent/50"
                  />
                  <span className="text-xs text-muted">:</span>
                  <input
                    type="number" min={0} max={59} value={draft.minute}
                    onChange={e => setDraft(d => ({ ...d, minute: Math.max(0, Math.min(59, Number(e.target.value))) }))}
                    className="ctl ctl-sm ctl-input w-12 px-1.5 font-mono text-center focus:border-accent/50"
                  />
                  <span className="text-[10px] text-muted/70">不早于 15:00 · 工作日执行</span>
                </div>
              )}

              {/* 推送渠道(多选, 独立常驻, 与定时无关, 即时生效) */}
              <div className="mt-3 rounded-btn bg-elevated/40 px-3 py-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-foreground">生成后推送完整报告</span>
                  <span className="text-[10px] text-muted/70">{reviewPushChannels.length === 0 ? '未开启' : `${reviewPushChannels.length} 个渠道`}</span>
                </div>
                <div className="mt-2 space-y-1.5">
                  {/* 飞书(可用, 多选) */}
                  <button
                    type="button"
                    disabled={pushMut.isPending}
                    onClick={() => togglePushChannel('feishu')}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-btn border px-2.5 py-1.5 text-left transition-colors disabled:opacity-50',
                      reviewPushChannels.includes('feishu')
                        ? 'border-accent/40 bg-accent/10'
                        : 'border-border/60 bg-base/40 hover:bg-base/60',
                    )}
                  >
                    <span className={cn('flex h-[12px] w-[12px] shrink-0 items-center justify-center rounded border', reviewPushChannels.includes('feishu') ? 'border-accent bg-accent text-white' : 'border-border')}>
                      {reviewPushChannels.includes('feishu') && <Check className="h-2.5 w-2.5" />}
                    </span>
                    <span className="text-[11px] text-foreground">飞书</span>
                    <span className="text-[9px] text-muted">群推送 Webhook</span>
                    <span className={cn('ml-auto text-[9px]', feishuConfigured ? 'text-emerald-500' : 'text-warning')}>
                      {feishuConfigured ? '已配置' : '未配置'}
                    </span>
                  </button>
                  {/* 企业微信(可用, 多选) */}
                  <button
                    type="button"
                    disabled={pushMut.isPending}
                    onClick={() => togglePushChannel('wecom')}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-btn border px-2.5 py-1.5 text-left transition-colors disabled:opacity-50',
                      reviewPushChannels.includes('wecom')
                        ? 'border-accent/40 bg-accent/10'
                        : 'border-border/60 bg-base/40 hover:bg-base/60',
                    )}
                  >
                    <span className={cn('flex h-[12px] w-[12px] shrink-0 items-center justify-center rounded border', reviewPushChannels.includes('wecom') ? 'border-accent bg-accent text-white' : 'border-border')}>
                      {reviewPushChannels.includes('wecom') && <Check className="h-2.5 w-2.5" />}
                    </span>
                    <span className="text-[11px] text-foreground">企业微信</span>
                    <span className="text-[9px] text-muted">群推送 Webhook</span>
                    <span className={cn('ml-auto text-[9px]', wecomConfigured ? 'text-emerald-500' : 'text-warning')}>
                      {wecomConfigured ? '已配置' : '未配置'}
                    </span>
                  </button>
                  <button
                    type="button"
                    disabled={pushMut.isPending}
                    onClick={() => togglePushChannel('custom')}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-btn border px-2.5 py-1.5 text-left transition-colors disabled:opacity-50',
                      reviewPushChannels.includes('custom')
                        ? 'border-accent/40 bg-accent/10'
                        : 'border-border/60 bg-base/40 hover:bg-base/60',
                    )}
                  >
                    <span className={cn('flex h-[12px] w-[12px] shrink-0 items-center justify-center rounded border', reviewPushChannels.includes('custom') ? 'border-accent bg-accent text-white' : 'border-border')}>
                      {reviewPushChannels.includes('custom') && <Check className="h-2.5 w-2.5" />}
                    </span>
                    <span className="text-[11px] text-foreground">第三方系统</span>
                    <span className="text-[9px] text-muted">JSON Webhook</span>
                    <span className={cn('ml-auto text-[9px]', customConfigured ? 'text-emerald-500' : 'text-warning')}>
                      {customConfigured ? '已配置' : '未配置'}
                    </span>
                  </button>
                  <button
                    type="button"
                    disabled={pushMut.isPending}
                    onClick={() => togglePushChannel('email')}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-btn border px-2.5 py-1.5 text-left transition-colors disabled:opacity-50',
                      reviewPushChannels.includes('email')
                        ? 'border-accent/40 bg-accent/10'
                        : 'border-border/60 bg-base/40 hover:bg-base/60',
                    )}
                  >
                    <span className={cn('flex h-[12px] w-[12px] shrink-0 items-center justify-center rounded border', reviewPushChannels.includes('email') ? 'border-accent bg-accent text-white' : 'border-border')}>
                      {reviewPushChannels.includes('email') && <Check className="h-2.5 w-2.5" />}
                    </span>
                    <span className="text-[11px] text-foreground">邮件</span>
                    <span className="text-[9px] text-muted">SMTP</span>
                    <span className={cn('ml-auto text-[9px]', emailConfigured ? 'text-emerald-500' : 'text-warning')}>
                      {emailConfigured ? '已配置' : '未配置'}
                    </span>
                  </button>
                </div>

                {/* 推送触发方式: auto=归档即推 / manual=仅归档不自动外发 */}
                <div className="mt-2 flex items-center justify-between">
                  <span className="text-[11px] text-foreground">推送触发方式</span>
                  <div className="flex items-center gap-0.5 rounded-btn bg-base p-0.5">
                    <button
                      type="button"
                      disabled={pushModeMut.isPending}
                      onClick={() => pushModeMut.mutate('auto')}
                      className={cn(
                        'ctl ctl-sm ctl-ghost px-1.5 disabled:opacity-50',
                        reviewPushMode === 'auto' ? 'bg-accent text-white' : 'text-muted hover:text-secondary',
                      )}
                    >
                      自动
                    </button>
                    <button
                      type="button"
                      disabled={pushModeMut.isPending}
                      onClick={() => pushModeMut.mutate('manual')}
                      className={cn(
                        'ctl ctl-sm ctl-ghost px-1.5 disabled:opacity-50',
                        reviewPushMode === 'manual' ? 'bg-accent text-white' : 'text-muted hover:text-secondary',
                      )}
                    >
                      手动确认
                    </button>
                  </div>
                </div>

                <p className="mt-1.5 text-[10px] leading-relaxed text-muted/70">
                  {reviewPushMode === 'auto'
                    ? '定时与手动生成的复盘归档后都会自动推送完整报告。'
                    : '复盘仅归档保存，不自动外发。'}
                  复用「设置 → 实时监控」的渠道配置。
                  {(
                    (reviewPushChannels.includes('feishu') && !feishuConfigured)
                    || (reviewPushChannels.includes('wecom') && !wecomConfigured)
                    || (reviewPushChannels.includes('custom') && !customConfigured)
                    || (reviewPushChannels.includes('email') && !emailConfigured)
                  ) && (
                    <Link to="/settings?tab=monitoring&highlight=webhooks" className="ml-1 text-accent hover:underline" onClick={() => setShowSchedule(false)}>
                      前往配置 →
                    </Link>
                  )}
                </p>
              </div>

              {/* 自定义复盘提示词: 另开一档查看/编辑 (设计稿空态的「查看提示词」入口) */}
              <button
                type="button"
                onClick={() => setScheduleTab('prompt')}
                className="mt-3 flex w-full cursor-pointer items-center justify-between rounded-btn bg-elevated/40 px-3 py-2.5 text-left transition-colors hover:bg-elevated/70"
              >
                <span className="flex items-center gap-1.5 text-xs text-foreground">
                  <Wand2 className="h-3.5 w-3.5 text-accent" />
                  自定义复盘提示词
                </span>
                <span className={cn('text-[9px]', promptCustomized ? 'text-accent' : 'text-muted/70')}>
                  {promptCustomized ? '已自定义' : '默认模板'}
                </span>
              </button>

              {!draft.enabled && (
                <p className="mt-3 text-[10px] text-muted/70">
                  当前：已关闭。开启后将按设定时间自动复盘。
                </p>
              )}
                </>
              )}

              {/* 操作区: 取消 + 保存(统一提交开关+时间) */}
              <div className="mt-5 flex justify-end gap-2">
                {scheduleTab === 'prompt' ? (
                  <button
                    onClick={() => setShowSchedule(false)}
                    className="ctl ctl-md ctl-ghost px-2.5 hover:text-foreground"
                  >
                    关闭
                  </button>
                ) : (
                  <>
                    <button
                      onClick={() => setShowSchedule(false)}
                      className="ctl ctl-md ctl-ghost px-2.5 hover:text-foreground"
                    >
                      取消
                    </button>
                    <button
                      onClick={() => reviewMut.mutate({ enabled: draft.enabled, hour: draft.hour, minute: draft.minute })}
                      disabled={reviewMut.isPending}
                      className="ctl ctl-md ctl-btn pri inline-flex items-center gap-1.5 px-2.5 hover:bg-accent/90 disabled:opacity-50"
                    >
                      {reviewMut.isPending ? '保存中…' : '保存'}
                    </button>
                  </>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 个股日 K 详情 (龙虎榜单点击打开) */}
      <StockPreviewDialog
        symbol={previewSymbol}
        triggerInfo={null}
        onClose={() => setPreviewSymbol(null)}
      />
    </>
  )
}

// ================================================================
// 市场摘要条 —— 复盘页的轻量上下文(非重复看板)
// 仅一行:三大指数涨跌 · 情绪分 · 涨停结构 · 成交额
// 仅一行:三大指数涨跌 · 情绪分 · 涨停结构 · 成交额
// ================================================================
// 指数简称映射:全称太长(上证指数/深证成指/创业板指/科创综指)摘要条放不下,统一缩成单字
const INDEX_SHORT: Record<string, string> = {
  '上证指数': '上', '深证成指': '深', '创业板指': '创', '科创综指': '科', '科创50': '科',
}

// 批量替换文本中的指数全称为简称(用于历史列表 summary 显示,
// 兼容存量旧报告 —— 它们存盘时 summary 还是全称)。
const _INDEX_FULL_RE = /上证指数|深证成指|创业板指|科创综指|科创50/g
function shortenIndexNames(text: string): string {
  return text.replace(_INDEX_FULL_RE, (m) => INDEX_SHORT[m] ?? m)
}

// 从 summary 的指数段(如「上-2.26%、深-3.44%、创-4.07%、科-2.02%」)
// 解析出 [{name, pctStr, pctNum}],供列表项按涨跌染色渲染。
const _INDEX_PCT_RE = /([上深创科])([+-]?\d+\.\d+%)/g
function parseIndexPcts(indexSegment: string): { name: string; pctStr: string; pctNum: number }[] {
  const out: { name: string; pctStr: string; pctNum: number }[] = []
  for (const m of indexSegment.matchAll(_INDEX_PCT_RE)) {
    out.push({ name: m[1], pctStr: m[2], pctNum: parseFloat(m[2]) })
  }
  return out
}

function MarketSummaryBar({ data }: { data: OverviewMarket }) {
  const score = data.emotion?.score ?? null
  const emoColor = scoreColor(score)
  const b = data.breadth
  const lim = data.limit
  const flatPct = b.total > 0 ? (b.flat / b.total) * 100 : 0
  const upPct = b.total > 0 ? (b.up / b.total) * 100 : 0
  const downPct = b.total > 0 ? (b.down / b.total) * 100 : 0

  return (
    // 顶端读数带 (design/v2/22-review.html 的 .b4h): 四格等宽读数, 格线是 1px 发丝线
    <div className="band grid grid-cols-2 md:grid-cols-4">
      <div>
        <span className="k">情 绪 分</span>
        <div className="v" style={{ color: emoColor }}>{score ?? '—'}</div>
        <small>{data.emotion?.label ?? '情绪'} · 六维加权</small>
      </div>
      <div>
        <span className="k">涨 跌 家 数</span>
        <div className="v">
          <span className="text-bull">{b.up}</span>
          <span className="text-muted"> : </span>
          <span className="text-bear">{b.down}</span>
        </div>
        {/* 涨跌条: 涨 / 平 / 跌 三段 */}
        <div className="mt-1.5 flex h-[5px] w-full overflow-hidden">
          <i className="bg-bull" style={{ width: `${upPct}%` }} />
          <i className="bg-border" style={{ width: `${flatPct}%` }} />
          <i className="bg-bear" style={{ width: `${downPct}%` }} />
        </div>
        <small>平盘 {b.flat} 只 · 上涨占比 {upPct.toFixed(1)}%</small>
      </div>
      <div>
        <span className="k">涨 停 / 跌 停</span>
        <div className="v">
          <span className="text-bull">{lim.limit_up}</span>
          <span className="text-muted"> : </span>
          <span className="text-bear">{lim.limit_down}</span>
        </div>
        <small>炸板 {lim.broken} · 封板率 {lim.seal_rate != null ? `${lim.seal_rate.toFixed(0)}%` : '—'} · 最高 {lim.max_boards} 板</small>
      </div>
      <div>
        <span className="k">成 交 额</span>
        <div className="v">{data.amount?.total != null ? (data.amount.total / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 0 }) : '—'}</div>
        <small>亿元 · 均 {data.amount?.avg != null ? (data.amount.avg / 1e8).toFixed(2) : '—'} 亿/只</small>
      </div>
    </div>
  )
}

/** 四大指数一览 (design/v2/22-review.html 的右栏「四 大 指 数」) */
function IndexTable({ data }: { data: OverviewMarket }) {
  const indices = (data.indices ?? []).slice(0, 4)
  if (!indices.length) return null
  return (
    <>
      <div className="sec-h">四 大 指 数</div>
      <table className="dt">
        <colgroup>
          <col />
          <col className="w-[62px]" />
          <col className="w-[62px]" />
          <col className="w-[72px]" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">指数</th>
            <th scope="col">涨跌幅</th>
            <th scope="col">涨跌</th>
            <th scope="col">收</th>
          </tr>
        </thead>
        <tbody>
          {indices.map(idx => (
            <tr key={idx.symbol}>
              <td>{idx.name ?? idx.symbol}</td>
              <td className={priceColorClass(idx.change_pct ?? null)}>
                {fmtPctAlready(idx.change_pct, 2, true)}
              </td>
              <td className={priceColorClass(idx.change_amount ?? null)}>
                {idx.change_amount == null ? '—' : idx.change_amount.toFixed(2)}
              </td>
              <td>{idx.close != null ? idx.close.toFixed(2) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

// ================================================================
// 复盘材料模块 (design/v2/22-review.html 的左主区与右栏)
// 数据全部来自本页已请求的 /api/overview/market, 不额外调接口。
// ================================================================

/** 涨 跌 分 布 · 8 档 (design/v2/22 的 .dist: 8 列底对齐柱 + 计数 + 档位标签)
 *  柱形与颜色走共享的 .dist (与仪表盘同一张图), 不在这里另画一套。 */
function DistributionFig({ rows }: { rows: OverviewMarket['distribution'] }) {
  if (!rows?.length) return null
  return (
    <>
      <div className="sec-h">涨 跌 分 布 · 8 档</div>
      <DistFig rows={rows} labels="bands" counts />
    </>
  )
}

/** 连 板 梯 队 (design/v2/22: 高度 / 家数 / 领头 / 成交(亿)) */
function LimitLadderTable({ tiers, onOpenStock }: {
  tiers: OverviewMarket['limit']['tiers']
  onOpenStock: (s: string) => void
}) {
  if (!tiers?.length) return null
  return (
    <>
      <div className="sec-h">连 板 梯 队</div>
      <table className="dt">
        <colgroup>
          <col className="w-[56px]" />
          <col className="w-[56px]" />
          <col />
          <col className="w-[80px]" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">高度</th>
            <th scope="col">家数</th>
            <th scope="col">领头</th>
            <th scope="col">成交(亿)</th>
          </tr>
        </thead>
        <tbody>
          {tiers.map(t => {
            const lead = t.stocks?.[0]
            return (
              <tr key={t.boards}>
                <td>{t.boards} 板</td>
                <td>{t.count}</td>
                <td>
                  {lead?.symbol ? (
                    <button
                      type="button"
                      onClick={() => onOpenStock(lead.symbol!)}
                      className="cursor-pointer text-left transition-colors hover:text-accent"
                      title={`查看 ${lead.name ?? lead.symbol} 日K`}
                    >
                      {lead.name ?? lead.symbol}
                    </button>
                  ) : (
                    '—'
                  )}
                </td>
                <td>{lead?.amount != null ? (lead.amount / 1e8).toFixed(1) : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

/** 六 维 情 绪 (design/v2/22 的 .radar6: 3 列 × 2 行, 每格一根分值条) */
function EmotionRadar6({ radar }: { radar: OverviewMarket['radar'] }) {
  if (!radar?.length) return null
  return (
    <>
      <div className="sec-h">六 维 情 绪</div>
      <div className="grid grid-cols-3 gap-px bg-border">
        {radar.map(d => (
          <div key={d.key} className="bg-surface px-2 py-1.5">
            <span className="block text-[9px] tracking-[0.1em] text-muted">{d.label}</span>
            <span className="my-0.5 block font-mono text-[15px] text-foreground">{d.value}</span>
            <span className="block h-[3px] bg-border">
              <span className="block h-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, d.value))}%` }} />
            </span>
          </div>
        ))}
      </div>
    </>
  )
}

/** 均 线 站 位 (design/v2/22 右栏:站上 5/20/60 日线 + 创 60 日新高/新低) */
function MaPositionTable({ trend }: { trend: OverviewMarket['trend'] }) {
  if (!trend) return null
  const rows: [string, number, string][] = [
    ['站上 5 日线', trend.above_ma5, `${trend.above_ma5_pct.toFixed(1)}%`],
    ['站上 20 日线', trend.above_ma20, `${trend.above_ma20_pct.toFixed(1)}%`],
    ['站上 60 日线', trend.above_ma60, `${trend.above_ma60_pct.toFixed(1)}%`],
    ['创 60 日新高', trend.new_high, '—'],
    ['创 60 日新低', trend.new_low, '—'],
  ]
  return (
    <>
      <div className="sec-h">均 线 站 位</div>
      <table className="dt">
        <colgroup>
          <col />
          <col className="w-[62px]" />
          <col className="w-[56px]" />
        </colgroup>
        <tbody>
          {rows.map(([k, v, p]) => (
            <tr key={k}>
              <td>{k}</td>
              <td>{v.toLocaleString()}</td>
              <td>{p}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

/** 领 涨 概 念 (design/v2/22 右栏:概念 / 均涨幅 / 只) */
function LeadingConceptTable({ items }: { items: OverviewDimensionRankItem[] }) {
  if (!items?.length) return null
  return (
    <>
      <div className="sec-h">领 涨 概 念</div>
      <table className="dt">
        <colgroup>
          <col />
          <col className="w-[64px]" />
          <col className="w-[44px]" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">概念</th>
            <th scope="col">均涨幅</th>
            <th scope="col">只</th>
          </tr>
        </thead>
        <tbody>
          {items.slice(0, 5).map(c => (
            <tr key={c.name}>
              <td>{c.name}</td>
              {/* avg_pct 是成分股 change_pct(小数制) 的均值, 必须 ×100; 用 fmtPct 而非 fmtPctAlready */}
              <td className={priceColorClass(c.avg_pct)}>{fmtPct(c.avg_pct)}</td>
              <td>{c.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

// ================================================================
// 报告面板(流式 + 错误 + 历史/完成态)
// ================================================================
function ReportPanel({
  phase, content, error, isGenerating, viewing, onCopy, onDownload, onRegenerate, onViewPrompt, reportEndRef,
}: {
  phase: ReviewPhase
  content: string
  error: string
  isGenerating: boolean
  viewing: AiReviewReport | null
  onCopy: () => void
  onDownload: () => void
  onRegenerate: () => void
  onViewPrompt: () => void
  reportEndRef: React.RefObject<HTMLDivElement>
}) {
  if (phase === 'error') {
    return (
      <div className="flex flex-col items-center justify-center gap-3 border border-border bg-surface px-6 py-12 text-center">
        <span className="font-mono text-[9.5px] tracking-[0.2em] text-danger">REPORT FAILED</span>
        <div className="font-serif text-[16px] font-medium text-foreground">复盘失败</div>
        <div className="max-w-md text-[11.5px] leading-[1.75] text-secondary">{error || '请检查 AI 配置后重试'}</div>
        <button
          onClick={onRegenerate}
          className="mt-1 ctl ctl-md ctl-btn pri"
        >
          重新生成
        </button>
      </div>
    )
  }

  if (phase === 'idle' && !content) {
    return (
      // 空态:纸面浅格 + 等宽小字题 + 生成/看提示词两个动作 (design/v2/22 的 .empty-rv)
      <div className="border border-border bg-surface px-6 py-12 text-center">
        <span className="font-mono text-[9.5px] tracking-[0.2em] text-accent">NO REPORT</span>
        <h2 className="mt-2.5 font-serif text-[17px] font-medium text-foreground">还没有生成过复盘</h2>
        <p className="mx-auto mt-2 max-w-[42ch] text-[11.5px] leading-[1.8] text-secondary">
          复盘以当日的指数、涨跌分布、连板梯队、龙虎榜与板块轮动为材料，交给大模型写成一份中立的市场记录。下面这些材料今天就绪了，等一次生成。
        </p>
        <div className="mt-4 flex items-center justify-center gap-2">
          <button onClick={onRegenerate} className="ctl ctl-md ctl-btn pri">
            <Sparkles className="mr-1 inline h-3.5 w-3.5" />生成今日复盘
          </button>
          <button onClick={onViewPrompt} className="ctl ctl-md ctl-btn">
            <Wand2 className="mr-1 inline h-3.5 w-3.5" />查看提示词
          </button>
        </div>
      </div>
    )
  }

  // 仅当显示生成内容(非查看历史)且正在生成时,才显示流式光标
  const showCursor = isGenerating && !viewing
  // 查看历史时(即使后台在生成)也能复制/下载该历史报告
  const showActions = !!content && (!isGenerating || !!viewing)
  const showViewingTag = !!viewing
  const isLoading = phase === 'loading' && !content

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="border border-border bg-surface"
    >
      {/* 题头: 等宽小字 (design/v2 的 .fig-h 语汇) */}
      <div className="flex items-center justify-between border-b border-rule-strong px-4 py-2">
        <div className="flex items-center gap-1.5">
          {isGenerating ? <RefreshCw className="h-3.5 w-3.5 animate-spin text-accent" /> : <BookOpenCheck className="h-3.5 w-3.5 text-accent" />}
          <span className="font-mono text-[9.5px] tracking-[0.14em] text-muted">
            {showViewingTag ? `历 史 复 盘 · ${viewing!.as_of}` : isGenerating ? 'AI 正在复盘…' : '今 日 复 盘 报 告'}
          </span>
        </div>
        {showActions && (
          <div className="flex items-center gap-1">
            <button onClick={onCopy} className="ctl-btn ctl-sm" title="复制全文">
              <Copy className="inline h-[12px] w-[12px]" /> 复制
            </button>
            <button onClick={onDownload} className="ctl-btn ctl-sm" title="下载为 Markdown">
              <Download className="inline h-[12px] w-[12px]" /> 下载
            </button>
          </div>
        )}
      </div>
      <div className="max-h-[calc(100vh-20rem)] overflow-y-auto px-5 py-4">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16">
            <div className="relative">
              <div className="grid h-[44px] w-[44px] place-items-center rounded-full bg-gradient-to-br from-accent/20 to-accent/15 border border-accent/30">
                <Sparkles className="h-[20px] w-[20px] animate-pulse text-accent" />
              </div>
              <RefreshCw className="absolute -inset-1 h-[52px] w-[52px] animate-spin text-accent/30" style={{ animationDuration: '3s' }} />
            </div>
            <div className="text-sm text-foreground">AI 正在复盘今日盘面…</div>
            <div className="text-xs text-secondary">分析指数结构 · 连板梯队 · 板块轮动 · 资金情绪</div>
            <button
              type="button"
              onClick={cancelReviewGeneration}
              className="mt-1 ctl-btn ctl-sm"
            >
              <X className="inline h-[12px] w-[12px]" /> 取消生成
            </button>
          </div>
        ) : (
          <div className="prose prose-invert max-w-none">
            <MarkdownRenderer content={content} />
            {showCursor && (
              <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-accent align-middle" />
            )}
          </div>
        )}
        <div ref={reportEndRef} />
      </div>
    </motion.div>
  )
}

// ================================================================
// 历史面板
// ================================================================
function HistoryPanel({
  reports, loading, viewingId, generating, onView, onBackToGenerating, onDelete,
}: {
  reports: AiReviewReport[]
  loading: boolean
  viewingId: string | null
  generating: boolean
  onView: (r: AiReviewReport) => void
  onBackToGenerating: () => void
  onDelete: (id: string) => void
}) {
  const empty = !generating && reports.length === 0
  return (
    <div>
      {loading ? (
        <div className="grid h-20 place-items-center"><RefreshCw className="h-[16px] w-[16px] animate-spin text-muted" /></div>
      ) : empty ? (
        <div className="flex flex-col items-center justify-center gap-2 border border-border bg-surface px-3 py-8 text-center">
          <span className="font-mono text-[9.5px] tracking-[0.2em] text-accent">NO REPORT</span>
          <div className="font-serif text-[15px] font-medium text-foreground">还没有生成过复盘</div>
          <div className="text-[11px] leading-[1.75] text-muted">生成完成后自动归档, 在此按日回看</div>
        </div>
      ) : (
        <div className="divide-y divide-border border-y border-border">
          {/* 生成中占位项:列表顶部,点击回到正在生成的流式内容 */}
          {generating && (
            <button
              type="button"
              className={cn(
                'flex w-full items-center gap-2 py-2 text-left transition-colors',
                viewingId === null ? 'bg-accent/[0.06]' : 'hover:bg-elevated/50',
              )}
              onClick={onBackToGenerating}
            >
              <span className="w-9 shrink-0 text-center font-mono text-[10px] text-accent">
                <RefreshCw className="ml-auto h-3.5 w-3.5 animate-spin" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[11px] font-medium text-accent">生成中…</span>
                <span className="mt-0.5 block truncate text-[10px] text-secondary">AI 正在复盘今日盘面</span>
              </span>
            </button>
          )}
          {reports.map((r) => {
            const color = scoreColor(r.emotion_score)
            return (
              <div
                key={r.id}
                className={cn(
                  'group flex cursor-pointer items-center gap-2 py-2 transition-colors',
                  viewingId === r.id ? 'bg-accent/[0.06]' : 'hover:bg-elevated/50',
                )}
                onClick={() => onView(r)}
              >
                <div
                  className="w-9 shrink-0 text-center font-mono text-[12px] tabular-nums"
                  style={{ color }}
                >
                  {r.emotion_score ?? '—'}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[11.5px] text-foreground">{r.emotion_label ?? '—'}</span>
                    <span className="font-mono text-[10px] text-muted">{r.as_of}</span>
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    {r.summary
                      ? (() => {
                          const pcts = parseIndexPcts(shortenIndexNames(r.summary).split('|')[0])
                          if (pcts.length === 0) {
                            return <span className="truncate text-[10px] text-secondary">{r.content.slice(0, 40)}</span>
                          }
                          return pcts.map((p) => (
                            <span key={p.name} className="inline-flex items-center gap-0.5 text-[10px]">
                              <span className="text-secondary">{p.name}</span>
                              <span className={cn('font-mono tabular-nums', priceColorClass(p.pctNum))}>{p.pctStr}</span>
                            </span>
                          ))
                        })()
                      : <span className="truncate text-[10px] text-secondary">{r.content.slice(0, 40)}</span>}
                  </div>
                  {r.created_at && (
                    <div className="mt-0.5 font-mono text-[9px] text-muted">{fmtArchivedAt(r.created_at)}</div>
                  )}
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); onDelete(r.id) }}
                  className="ctl ctl-sm ctl-ghost ctl-icon shrink-0 text-muted opacity-0 transition-all hover:text-bear group-hover:opacity-100"
                  title="删除"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ================================================================
// 龙虎榜卡片 (fuyao 专有) —— 资金动向上下文, 市场摘要条之下
// 视觉对齐 MarketSummaryBar: 8x8 徽章 + rounded-card + surface 底。
// 收起态: 奖杯徽章头部 + 净买/机构 Top3 排名药丸;
// 展开态: 分段式三榜 tab, 股票表净买额带比例条, 游资席位带奖牌。
// 状态: 当日未发布自动显示上一期 (fallback_prev); fuyao 未配置降级提示。
// ================================================================
type _DtTabKey = 'all' | 'org' | 'hot_money'
const _DT_TABS: { key: _DtTabKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'org', label: '机构' },
  { key: 'hot_money', label: '游资' },
]

function _dtSorted(items: DragonTigerStockItem[], key: 'net_value' | 'org_net_value', asc = false): DragonTigerStockItem[] {
  const s = [...items].sort((a, b) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity))
  return asc ? s.reverse() : s
}

/** 榜单标的代码: 后端已归一非空, 此处兜底旧缓存/异常数据源的 null thscode。 */
function _dtCode(i: DragonTigerStockItem): string {
  const code = i.thscode
  if (typeof code === 'string' && code.trim()) return code
  return i.ticker ?? ''
}

/** 榜单标的展示名: 名称优先, 无名称时退代码/ticker。 */
function _dtLabel(i: DragonTigerStockItem): string {
  return i.name ?? (_dtCode(i) || '—')
}

/** 排名序号色: 前三用暖色系奖牌感, 其余弱化 */
function _rankCls(idx: number): string {
  if (idx === 0) return 'bg-amber-500/20 text-amber-400'
  if (idx === 1) return 'bg-zinc-400/15 text-zinc-300'
  if (idx === 2) return 'bg-orange-700/20 text-orange-400/90'
  return 'bg-elevated text-muted/70'
}

/** 可排序列 key — 与 DragonTigerStockItem 数值字段对应 */
type _DtSortKey = 'change' | 'net_value' | 'net_rate' | 'org_net_value' | 'buy_value' | 'sell_value'

/** 排序表头: 点击切换列/方向, 箭头指示当前排序 (hover 才显隐未激活箭头) */
function _DtTh({ label, sortKey, sort, onSort, className }: {
  label: string
  sortKey: _DtSortKey
  sort: { key: _DtSortKey; desc: boolean }
  onSort: (k: _DtSortKey) => void
  className?: string
}) {
  const active = sort.key === sortKey
  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className={cn('inline-flex items-center gap-0.5 transition-colors hover:text-foreground', active && 'text-foreground', className)}
    >
      {label}
      {active ? (
        sort.desc
          ? <ChevronDown className="h-2.5 w-2.5 opacity-80" />
          : <ChevronUp className="h-2.5 w-2.5 opacity-80" />
      ) : (
        <ChevronDown className="h-2.5 w-2.5 opacity-0 transition-opacity group-hover:opacity-40" />
      )}
    </button>
  )
}

function _DtStockTable({ items, tab, onOpenStock }: {
  items: DragonTigerStockItem[]
  tab: _DtTabKey
  onOpenStock: (s: string) => void
}) {
  const isOrg = tab === 'org'
  const [sort, setSort] = useState<{ key: _DtSortKey; desc: boolean }>({ key: isOrg ? 'org_net_value' : 'net_value', desc: true })
  const onSort = (k: _DtSortKey) =>
    setSort(s => (s.key === k ? { key: k, desc: !s.desc } : { key: k, desc: true }))
  const sorted = [...items].sort((a, b) => {
    const av = a[sort.key] ?? null
    const bv = b[sort.key] ?? null
    if (av == null && bv == null) return 0
    if (av == null) return 1 // 空值恒垫底, 不随方向翻转
    if (bv == null) return -1
    return (av - bv) * (sort.desc ? -1 : 1)
  })
  const maxAbs = Math.max(1e-12, ...items.map(i => Math.abs(i.net_value ?? 0)))
  return (
    <div className="border-y border-rule-strong">
      {/* 表头 */}
      <div className={cn(
        'group flex items-center gap-2 px-2.5 pb-1.5 text-[9px] tracking-[0.1em] text-muted',
      )}>
        <span className="w-5 shrink-0 text-center">#</span>
        <span className="w-14 shrink-0"><_DtTh label="涨跌幅" sortKey="change" sort={sort} onSort={onSort} /></span>
        <span className="min-w-0 flex-1">股票</span>
        <span className="w-24 shrink-0"><_DtTh label="净买额" sortKey="net_value" sort={sort} onSort={onSort} className="w-full justify-end" /></span>
        <span className="w-14 shrink-0"><_DtTh label="占比" sortKey="net_rate" sort={sort} onSort={onSort} className="w-full justify-end" /></span>
        {isOrg && <span className="w-20 shrink-0"><_DtTh label="机构净买" sortKey="org_net_value" sort={sort} onSort={onSort} className="w-full justify-end" /></span>}
        <span className="w-20 shrink-0"><_DtTh label="买入额" sortKey="buy_value" sort={sort} onSort={onSort} className="w-full justify-end" /></span>
        <span className="w-20 shrink-0"><_DtTh label="卖出额" sortKey="sell_value" sort={sort} onSort={onSort} className="w-full justify-end" /></span>
        <span className="w-11 shrink-0 text-right">榜期</span>
      </div>
      {sorted.map((i, idx) => {
        const barPct = Math.max(2, Math.min(100, (Math.abs(i.net_value ?? 0) / maxAbs) * 100))
        const positive = (i.net_value ?? 0) >= 0
        const code = _dtCode(i)
        const label = _dtLabel(i)
        return (
          <button
            key={`${code}-${i.range_days ?? 1}`}
            type="button"
            onClick={() => code && onOpenStock(code)}
            className="flex w-full items-center gap-2 border-b border-border px-2.5 py-2 text-left transition-colors hover:bg-accent/[0.05]"
            title={`查看 ${label} 详情`}
          >
            <span className={cn('w-5 shrink-0 text-center font-mono text-[9px] tabular-nums', idx < 3 ? 'text-amber-400/90' : 'text-muted/50')}>
              {idx + 1}
            </span>
            <span className={cn('w-14 shrink-0 font-mono tabular-nums', priceColorClass(i.change ?? null))}>
              {fmtPct(i.change ?? null, 1)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="text-foreground">{label}</span>
              <span className="ml-1.5 font-mono text-[9px] text-muted">{i.ticker ?? code}</span>
              {(() => { const b = boardTag(code); return b && (
                <span className={`ml-1 inline-flex items-center rounded border px-1 text-[8px] font-bold leading-tight ${b.color}`}>
                  {b.label}
                </span>
              ) })()}
              {i.hot_rank != null && i.hot_rank > 0 && i.hot_rank <= 99 && (
                <span
                  className={cn(
                    'ml-1.5 inline-flex items-center rounded px-1 text-[8px] font-medium leading-tight',
                    i.hot_rank <= 10
                      ? 'border border-amber-500/30 bg-amber-500/10 text-amber-500'
                      : 'border border-border bg-elevated/50 text-muted/80',
                  )}
                  title={`同花顺人气第 ${i.hot_rank} 名`}
                >
                  人气{i.hot_rank}
                </span>
              )}
            </span>
            {/* 净买额: 比例条 + 数值 (红买绿卖, 长度=绝对值占榜首比例) */}
            <span className="relative w-24 shrink-0">
              <span
                aria-hidden
                className={cn(
                  'absolute inset-y-[3px] right-0 rounded-[3px]',
                  positive ? 'bg-bull/10' : 'bg-bear/10',
                )}
                style={{ width: `${barPct}%` }}
              />
              <span className={cn('relative block text-right font-mono tabular-nums', priceColorClass(i.net_value ?? null))}>
                {fmtVolume(i.net_value ?? null)}
              </span>
            </span>
            <span className="w-14 shrink-0 text-right font-mono tabular-nums text-secondary">
              {i.net_rate == null ? '—' : `${(i.net_rate * 100).toFixed(1)}%`}
            </span>
            {isOrg && (
              <span className={cn('w-20 shrink-0 text-right font-mono tabular-nums', priceColorClass(i.org_net_value ?? null))}>
                {fmtVolume(i.org_net_value ?? null)}
              </span>
            )}
            <span className="w-20 shrink-0 text-right font-mono tabular-nums text-muted">{fmtVolume(i.buy_value ?? null)}</span>
            <span className="w-20 shrink-0 text-right font-mono tabular-nums text-muted">{fmtVolume(i.sell_value ?? null)}</span>
            <span className="w-11 shrink-0 text-right">
              <span className={cn(
                'inline-flex rounded px-1 text-[8px] leading-tight',
                i.range_days === 3
                  ? 'border border-sky-500/25 bg-sky-500/10 text-sky-400/90'
                  : 'border border-border/60 bg-elevated/50 text-muted/80',
              )}>
                {i.range_days === 3 ? '3日' : '当日'}
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

function _DtSeatList({ seats, onOpenStock }: {
  seats: { name?: string | null; buying?: number | null; rows?: DragonTigerStockItem[] | null }[]
  onOpenStock: (s: string) => void
}) {
  if (!seats.length) {
    return <p className="py-4 text-center text-[11px] text-muted">本期无游资上榜数据</p>
  }
  return (
    <div className="divide-y divide-border">
      {seats.map((s, idx) => (
        <div
          key={`${s.name}-${idx}`}
          className="flex items-start gap-2.5 py-2.5 transition-colors"
        >
          <span className={cn('grid h-[20px] w-[20px] shrink-0 place-items-center rounded-full font-mono text-[9px] font-bold leading-none', _rankCls(idx))}>
            {idx + 1}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
              <span className="text-[11px] font-medium text-foreground" title={s.name ?? ''}>{s.name ?? '—'}</span>
              <span className={cn('font-mono text-[10px] tabular-nums', priceColorClass(s.buying ?? null))}>
                {fmtVolume(s.buying ?? null)}
              </span>
            </div>
            <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1.5">
              {(s.rows ?? []).map((r, ri) => {
                const code = _dtCode(r)
                return (
                <button
                  key={`${s.name}-${code}-${ri}`}
                  type="button"
                  onClick={() => code && onOpenStock(code)}
                  className="inline-flex items-baseline gap-1.5 border border-border bg-surface px-2 py-0.5 text-[10px] transition-colors hover:border-accent/40 hover:text-accent"
                  title={`查看 ${_dtLabel(r)} 详情`}
                >
                  <span>{_dtLabel(r)}</span>
                  {(() => { const b = boardTag(code); return b && (
                    <span className={`inline-flex items-center self-center rounded border px-1 text-[8px] font-bold leading-tight ${b.color}`}>
                      {b.label}
                    </span>
                  ) })()}
                  <span className={cn('font-mono text-[9px] tabular-nums', priceColorClass(r.hot_money_item_net_value ?? r.net_value ?? null))}>
                    {fmtVolume(r.hot_money_item_net_value ?? r.net_value ?? null)}
                  </span>
                </button>
                )
              })}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

/** 追高风险阈值: 60日回测高开≥5%子集当日开盘买 -1.97% (温和高开才是名单 alpha 来源) */

export function DragonTigerCard({ date, onOpenStock }: {
  date?: string
  onOpenStock: (symbol: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [tab, setTab] = useState<_DtTabKey>('all')
  const q = useQuery({
    queryKey: ['dragon-tiger', date ?? 'latest'],
    queryFn: () => api.dragonTiger(date),
    staleTime: 5 * 60_000,
    retry: 1,
  })
  const d = q.data

  if (q.isLoading) {
    return (
      <div className="flex items-center gap-3 border border-border bg-surface px-4 py-3">
        <span className="grid h-[28px] w-[28px] shrink-0 animate-pulse place-items-center border border-border bg-elevated">
          <Trophy className="h-3.5 w-3.5 text-muted/50" />
        </span>
        <div className="flex items-center gap-2">
          <span className="h-3 w-14 animate-pulse bg-elevated/80" />
          <span className="h-3 w-24 animate-pulse bg-elevated/60" />
          <span className="h-3 w-20 animate-pulse bg-elevated/40" />
        </div>
      </div>
    )
  }

  // fuyao 未配置:显式降级提示 (不用死区块, 给配置入口)
  if (d?.state === 'source_unavailable') {
    return (
      <div className="flex items-center gap-3 border border-dashed border-border bg-surface/50 px-4 py-3">
        <span className="grid h-[28px] w-[28px] shrink-0 place-items-center border border-border bg-elevated/60">
          <Trophy className="h-3.5 w-3.5 text-muted/50" />
        </span>
        <span className="text-[11px] text-muted">龙虎榜需要同花顺接口 (同花顺特色数据)</span>
        <Link to="/settings?tab=data-sources" className="ml-auto inline-flex items-center gap-0.5 text-[10px] text-accent hover:underline">
          前往配置 <ChevronRight className="h-[12px] w-[12px]" />
        </Link>
      </div>
    )
  }

  // 拉取失败:轻提示 + 重试 (不占复盘页视觉重量)
  if (!d || d.state === 'no_data') {
    return (
      <div className="flex items-center gap-3 border border-border bg-surface/50 px-4 py-3">
        <span className="grid h-[28px] w-[28px] shrink-0 place-items-center border border-border bg-elevated/60">
          <Trophy className="h-3.5 w-3.5 text-muted/50" />
        </span>
        <span className="text-[11px] text-muted">龙虎榜暂不可用{d?.message ? ` (${d.message.slice(0, 40)})` : ''}</span>
        <button onClick={() => q.refetch()} className="ctl-btn ctl-sm ctl-ghost ml-auto">重试</button>
      </div>
    )
  }

  const allItems = d.all?.stock_items ?? []
  const orgItems = d.org?.stock_items ?? []
  const seats = d.hot_money?.hot_money_items ?? []
  const isFallback = d.state === 'fallback_prev'
  const stockCount = d.all?.stock_count ?? allItems.length
  // 常驻表: 按同花顺人气升序取前 12 (design/v2/22 的龙虎榜表就是按人气排的)
  const preview = [...allItems]
    .sort((a, b) => (a.hot_rank ?? Number.MAX_SAFE_INTEGER) - (b.hot_rank ?? Number.MAX_SAFE_INTEGER))
    .slice(0, 12)

  return (
    <>
      {/* 题头: 龙 虎 榜 · 日期 · 只数; 右侧按钮切换三榜明细 */}
      <div className="sec-h">
        <span>龙 虎 榜 · {d.trade_date} · {stockCount} 只</span>
        {isFallback && (
          <span
            className="rounded border border-warning/30 bg-warning/10 px-1.5 py-px text-[9px] leading-tight text-warning"
            title="当日榜单未发布(约17:00后), 已自动显示上一期"
          >
            当日未发布 · 显示上一期
          </span>
        )}
        <span className="sp" />
        <button type="button" onClick={() => setExpanded(v => !v)} className="ctl-btn ctl-sm ctl-ghost">
          {expanded ? '收起三榜明细' : '查看三榜明细'}
        </button>
      </div>

      {/* 常驻表: 标的 / 上榜概念 / 涨跌幅 / 净买入(亿) / 净买占比 / 人气 */}
      <table className="dt">
        <colgroup>
          <col className="w-[104px]" />
          <col />
          <col className="w-[66px]" />
          <col className="w-[72px]" />
          <col className="w-[62px]" />
          <col className="w-[40px]" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">标的</th>
            <th scope="col" className="text-left">上榜概念</th>
            <th scope="col">涨跌幅</th>
            <th scope="col">净买入(亿)</th>
            <th scope="col">净买占比</th>
            <th scope="col">人气</th>
          </tr>
        </thead>
        <tbody>
          {preview.map(i => {
            const code = _dtCode(i)
            const label = _dtLabel(i)
            const concepts = (i.concept_list ?? []).map(c => c.name).filter(Boolean).join('、')
            return (
              <tr key={`${code}-${i.range_days ?? 1}`}>
                <td>
                  <button
                    type="button"
                    onClick={() => code && onOpenStock(code)}
                    className="flex w-full min-w-0 cursor-pointer items-baseline gap-1.5 text-left transition-colors hover:text-accent"
                    title={`查看 ${label} 详情`}
                  >
                    <span className="truncate">{label}</span>
                    <span className="shrink-0 font-mono text-[9px] text-muted">{i.ticker ?? code}</span>
                  </button>
                </td>
                <td className="text-left font-sans text-[10px] leading-snug text-muted" title={concepts}>
                  <span className="line-clamp-2 whitespace-normal">{concepts || '—'}</span>
                </td>
                <td className={priceColorClass(i.change ?? null)}>{fmtPct(i.change ?? null, 2)}</td>
                <td className={priceColorClass(i.net_value ?? null)}>
                  {i.net_value == null ? '—' : (i.net_value / 1e8).toFixed(2)}
                </td>
                <td>{i.net_rate == null ? '—' : `${(i.net_rate * 100).toFixed(2)}%`}</td>
                <td>{i.hot_rank ?? '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {/* 三榜明细(全部 / 机构 / 游资) */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            <div className="pt-3">
              {/* 分段式 tab */}
              <div className="mb-3 inline-flex border border-border">
                {_DT_TABS.map(t => (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setTab(t.key)}
                    className={cn(
                      'ctl ctl-sm ctl-ghost px-1.5',
                      t !== _DT_TABS[0] && 'border-l border-border',
                      tab === t.key
                        ? 'bg-foreground text-surface'
                        : 'text-secondary hover:bg-foreground/5 hover:text-foreground',
                    )}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {tab === 'hot_money' ? (
                <_DtSeatList seats={seats} onOpenStock={onOpenStock} />
              ) : (
                <_DtStockTable
                  key={tab}
                  items={_dtSorted(tab === 'org' ? orgItems : allItems, tab === 'org' ? 'org_net_value' : 'net_value')}
                  tab={tab}
                  onOpenStock={onOpenStock}
                />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
