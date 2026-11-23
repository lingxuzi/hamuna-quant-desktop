/**
 * 关于面板 — 版权与更新两个区块。
 */
import { useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ScrollText, ExternalLink, CheckCircle2, Download, RefreshCw, Sparkles, Scale, Rocket } from 'lucide-react'
import { useVersion } from '@/lib/useSharedQueries'
import { useUpdateCheck } from '@/lib/updateCheck'
import { SectionHead } from '@/components/SectionHead'
import { LicenseComplianceNotice } from '@/components/OssLicenseDialog'
import { PROJECT_LICENSE_NAME, PROJECT_REPO_URL } from '@/data/ossLicenses'

export function SettingsAboutPanel() {
  const { data: versionData } = useVersion()

  // ===== 检查更新 (共享单例 store: 与侧栏左下角 NEW 徽标同源, 手动检查绕过缓存) =====
  const {
    status: updateState,
    info: updateInfo,
    check: checkUpdate,
  } = useUpdateCheck()

  // ===== 侧栏下载图标深链 (?autoupdate=1): 滚动定位到本行并自动执行一次检查 =====
  const [searchParams, setSearchParams] = useSearchParams()
  const updateRowRef = useRef<HTMLDivElement>(null)
  const currentVersion = (versionData?.version ?? '').trim()
  useEffect(() => {
    if (!currentVersion || searchParams.get('autoupdate') !== '1') return
    updateRowRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    checkUpdate()
    const next = new URLSearchParams(searchParams)
    next.delete('autoupdate')
    setSearchParams(next, { replace: true })
  }, [currentVersion, searchParams, checkUpdate, setSearchParams])

  return (
    <div className="pb-8 flex flex-col gap-6">
      {/* ===== 版权 ===== */}
      <section className="rounded-card border border-border bg-surface p-5">
        <SectionHead
          icon={Scale}
          title="版权"
          className="mb-4"
          right={
            <span className="shrink-0 rounded border border-accent/30 bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
              {PROJECT_LICENSE_NAME}
            </span>
          }
        />

        {/* 版权署名 — 界面常驻, 避免仅存在于 LICENSE 文件时被整包删除 */}
        <div className="flex items-center justify-between gap-4 py-2">
          <div className="min-w-0">
            <div className="text-sm text-foreground">© 2026 tick-stock-panel contributors · H-Quant</div>
            <div className="text-[11px] text-muted truncate">基于 MIT 协议开源 · 遵照协议分发时须保留版权与许可声明</div>
          </div>
          <a
            href={PROJECT_REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1.5 px-3 py-1.5 rounded-btn text-xs
                       bg-elevated text-secondary hover:text-foreground transition-colors"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            仓库
          </a>
        </div>

        {/* 开源许可 — 点击弹窗查看完整内容 */}
        <div className="flex items-center justify-between gap-4 py-2">
          <div className="min-w-0">
            <div className="text-sm text-foreground">开源许可</div>
            <div className="text-[11px] text-muted truncate">本项目协议全文与第三方组件许可清单</div>
          </div>
          <a
            href={PROJECT_REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1.5 px-3 py-1.5 rounded-btn text-xs
                       bg-elevated text-secondary hover:text-foreground transition-colors"
          >
            <ScrollText className="h-3.5 w-3.5" />
            仓库内 LICENSE
          </a>
        </div>

        <div className="mt-3 border-t border-border/60 pt-3 text-[11px] leading-relaxed text-secondary">
          <p>
            图表组件：
            <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground">
              TradingView Lightweight Charts™
            </a>
          </p>
          <p className="mt-1">Copyright (с) 2025 TradingView, Inc.</p>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
            <a href="/third-party/lightweight-charts/LICENSE" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground">Apache-2.0 许可</a>
            <a href="/third-party/lightweight-charts/NOTICE" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground">NOTICE</a>
          </div>
        </div>

        {/* 许可合规声明 — 醒目警示 */}
        <LicenseComplianceNotice compact />
      </section>

      {/* ===== 更新 ===== */}
      <section className="rounded-card border border-border bg-surface p-5">
        <SectionHead icon={Rocket} title="更新" className="mb-4" />

        <div className="flex items-center justify-between gap-4 py-2">
          <div className="min-w-0">
            <div className="text-sm text-foreground">当前版本</div>
            <div className="text-[11px] text-muted truncate">已安装的应用版本</div>
          </div>
          <span className="font-mono text-xs text-secondary shrink-0">
            {versionData?.version ?? '—'}
          </span>
        </div>

        <div ref={updateRowRef} className="flex scroll-mt-24 items-center justify-between gap-4 py-2">
          <div className="min-w-0">
            <div className="text-sm text-foreground">检查更新</div>
            {updateState === 'found' && updateInfo ? (
              <div className="mt-0.5 inline-flex items-center gap-1.5 rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-[11px] text-accent">
                <Sparkles className="h-3 w-3" />
                发现新版本
                <span className="font-semibold">v{updateInfo.latest.replace(/^v/, '')}</span>
                <span className="text-accent/60">· 当前 v{versionData?.version?.replace(/^v/, '') ?? '—'}</span>
              </div>
            ) : (
              <div className="text-[11px] text-muted truncate">
                {updateState === 'latest'
                  ? `已是最新版本 (v${versionData?.version?.replace(/^v/, '') ?? '—'})`
                  : updateState === 'error'
                    ? '检查失败 (网络受限或更新源不可达)'
                    : '对比发布清单, 提示新版本'}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {updateState === 'found' && updateInfo?.url && (
              <a
                href={updateInfo.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn text-xs
                           bg-accent text-white hover:bg-accent/90 transition-colors"
              >
                <Download className="h-3.5 w-3.5" />
                前往下载 v{updateInfo.latest.replace(/^v/, '')}
              </a>
            )}
            <button
              onClick={checkUpdate}
              disabled={updateState === 'checking'}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-btn text-xs
                         bg-elevated text-secondary hover:text-foreground transition-colors
                         disabled:opacity-50"
            >
              {updateState === 'checking' ? (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
              ) : updateState === 'latest' ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {updateState === 'checking' ? '检查中…' : updateState === 'latest' ? '已检查' : '检查更新'}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
