import { useState } from 'react'
import { Download, Loader2, X } from 'lucide-react'
import { toast } from '@/components/Toast'
import { Modal } from '@/components/Modal'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'

type EntryFill = 'open_t+1' | 'close_t'

interface QmtForm {
  amount: number
  maxPositions: number
  entryFill: EntryFill
  universe: string
  accountId: string
  liveTrade: boolean
}

const DEFAULTS: QmtForm = {
  amount: 10000, maxPositions: 5, entryFill: 'open_t+1',
  universe: '沪深A股', accountId: '', liveTrade: false,
}

const UNIVERSES = ['沪深A股', '沪深京A股', '沪深300', '中证500', '中证1000']

/** 把当前策略导出为可直接拷进 QMT 运行的单文件脚本（含参数面板）。 */
export function QmtExportButton({ strategyId, disabled = false, disabledHint }: {
  strategyId: string; disabled?: boolean; disabledHint?: string
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<QmtForm>(DEFAULTS)

  const update = <K extends keyof QmtForm>(key: K, value: QmtForm[K]) =>
    setForm(prev => ({ ...prev, [key]: value }))

  const download = async () => {
    if (busy) return
    setBusy(true)
    try {
      const blob = await api.strategyQmtExport(strategyId, form)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${strategyId}.qmt.py`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setOpen(false)
    } catch (error) {
      toast(error instanceof Error ? error.message : 'QMT 脚本生成失败，请重试', 'error')
    } finally { setBusy(false) }
  }

  return (
    <>
      <button type="button" aria-label="导出 QMT 脚本" disabled={disabled}
        title={disabled && disabledHint ? disabledHint : '导出可在 QMT 中直接运行的单文件策略'}
        onClick={() => setOpen(true)}
        className="ctl ctl-md ctl-btn inline-flex shrink-0 items-center gap-1.5 px-2.5 bg-surface">
        <Download className="h-3.5 w-3.5" />QMT
      </button>

      {open && (
      <Modal onClose={() => { if (!busy) setOpen(false) }}
        labelledBy="qmt-export-title"
        overlayClassName="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-sm"
        panelClassName="w-[520px] max-w-[92vw] bg-surface/95 border border-border/50 rounded-2xl shadow-2xl flex flex-col overflow-hidden">

        <div className="flex items-center justify-between border-b border-border/50 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <Download className="h-[16px] w-[16px] text-accent" />
            <span id="qmt-export-title" className="text-sm font-semibold text-foreground">导出 QMT 脚本</span>
          </div>
          <button aria-label="关闭" disabled={busy} onClick={() => setOpen(false)}
            className="ctl ctl-sm ctl-ghost ctl-icon hover:bg-elevated disabled:opacity-40">
            <X className="h-[16px] w-[16px] text-muted" />
          </button>
        </div>

        <div className="flex flex-col gap-3 px-5 py-4">
          <Row label="单笔金额">
            <input type="number" min={1000} step={1000} value={form.amount}
              onChange={e => update('amount', Number(e.target.value) || 0)}
              className="ctl ctl-md ctl-input flex-1 bg-base font-mono text-right" />
            <span className="w-6 shrink-0 text-[11px] text-muted">元</span>
          </Row>

          <Row label="最大持仓">
            <input type="number" min={1} max={200} step={1} value={form.maxPositions}
              onChange={e => update('maxPositions', Number(e.target.value) || 1)}
              className="ctl ctl-md ctl-input flex-1 bg-base font-mono text-right" />
            <span className="w-6 shrink-0 text-[11px] text-muted">只</span>
          </Row>

          <Row label="买入时点">
            <select value={form.entryFill}
              onChange={e => update('entryFill', e.target.value as EntryFill)}
              className="ctl ctl-md ctl-select flex-1 bg-base">
              <option value="open_t+1">次日开盘</option>
              <option value="close_t">当日收盘</option>
            </select>
          </Row>

          <Row label="股票池">
            <select value={form.universe}
              onChange={e => update('universe', e.target.value)}
              className="ctl ctl-md ctl-select flex-1 bg-base">
              {UNIVERSES.map(item => <option key={item} value={item}>{item}</option>)}
            </select>
          </Row>

          <Row label="账户号">
            <input type="text" value={form.accountId} placeholder="留空则用 QMT 配置的账号"
              onChange={e => update('accountId', e.target.value)}
              className="ctl ctl-md ctl-input flex-1 bg-base font-mono" />
          </Row>

          <Row label="允许实盘下单">
            <button type="button" role="switch" aria-checked={form.liveTrade}
              onClick={() => update('liveTrade', !form.liveTrade)}
              className={cn('relative flex h-6 w-11 shrink-0 items-center rounded-full px-0.5 transition-colors',
                form.liveTrade ? 'bg-accent' : 'bg-border')}>
              <span className={cn('h-5 w-5 rounded-full bg-white shadow transition-transform',
                form.liveTrade ? 'translate-x-5' : 'translate-x-0')} />
            </button>
            <span className={cn('shrink-0 text-[11px]', form.liveTrade ? 'text-danger' : 'text-muted')}>
              {form.liveTrade ? '会真实下单' : '仅打印信号'}
            </span>
          </Row>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border/50 bg-surface/50 px-5 py-3">
          <button onClick={() => setOpen(false)} disabled={busy}
            className="ctl ctl-md ctl-btn px-2.5 bg-surface disabled:opacity-50">取消</button>
          <button onClick={() => void download()} disabled={busy}
            className="ctl ctl-md ctl-btn pri inline-flex items-center gap-1.5 px-2.5 font-semibold hover:bg-accent/90 disabled:opacity-50">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
            {busy ? '生成中…' : '下载脚本'}
          </button>
        </div>
      </Modal>
    )}
    </>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span className="w-[76px] shrink-0 text-[11px] text-muted">{label}</span>
      <div className="flex min-w-0 flex-1 items-center gap-2">{children}</div>
    </div>
  )
}