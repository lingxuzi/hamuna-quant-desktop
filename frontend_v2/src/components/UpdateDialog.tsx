import { useEffect, useState } from 'react'
import { Modal } from '@/components/Modal'
import type { UpdateInfo } from '@/lib/updateCheck'
import { canAutoInstall, installUpdate, waitForBridge } from '@/lib/updateInstall'

/**
 * 发现新版本时在应用内展示更新内容并直接更新。
 *
 * 桌面版: 点「立即更新」→ 应用内下载校验 → 自动退出 → 静默安装 → 自动重启。
 * 不出现保存对话框, 也不用去找下载下来的安装包。
 * 浏览器版: 没有桌面桥, 退回普通下载链接 (打开系统下载)。
 *
 * 不走 GitHub: 仓库在国内访问不了, 清单 latest.json 已内嵌 notes (commit 标题)
 * 与 channels 里各平台的安装包直链 (R2)。
 */
export function UpdateDialog({
  info,
  current,
  onClose,
}: {
  info: UpdateInfo
  current: string
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [auto, setAuto] = useState(false)
  const url = info.downloadUrl

  // 桥是异步注入的 (刚开窗时还没有), 且平台能力要问桌面进程 ——
  // 两步都满足才显示「立即更新」, 否则退回普通下载链接。
  useEffect(() => {
    let alive = true
    void waitForBridge()
      .then((ok) => (ok ? canAutoInstall() : false))
      .then((ok) => {
        if (alive) setAuto(ok)
      })
    return () => {
      alive = false
    }
  }, [])

  async function start() {
    setError('')
    setBusy(true)
    const r = await installUpdate(info)
    if (r.ok) {
      // 进程会在 1 秒内退出, 这里只负责让用户知道接下来是自动重启
      setDone(true)
      return
    }
    setBusy(false)
    setError(r.error)
  }

  return (
    <Modal
      onClose={busy ? () => undefined : onClose}
      labelledBy="update-dialog-title"
      panelClassName="w-[90vw] max-w-[420px] rounded-card border border-border bg-base p-6"
      overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
    >
      <h3 id="update-dialog-title" className="text-sm font-medium text-foreground">
        {done ? '正在安装更新' : `发现新版本 ${info.latest}`}
      </h3>
      <p className="mt-1 text-xs text-muted">
        {done ? `应用将自动重启到 ${info.latest}` : `当前 ${current || '—'}`}
      </p>

      {done ? (
        <p className="mt-4 text-xs leading-relaxed text-secondary">
          请不要手动关闭本窗口，安装完成后会自动重新打开。
        </p>
      ) : (
        <>
          {info.notes.length > 0 && (
            <ul className="mt-4 max-h-[220px] space-y-1.5 overflow-y-auto text-xs leading-relaxed text-secondary">
              {info.notes.map((n, i) => (
                <li key={i} className="flex gap-2">
                  <span aria-hidden="true" className="shrink-0 text-muted">·</span>
                  <span className="min-w-0 break-words">{n}</span>
                </li>
              ))}
            </ul>
          )}
          {busy && (
            <p className="mt-4 text-xs text-muted">正在下载安装包，请稍候…</p>
          )}
          {error && (
            <p className="mt-4 break-words text-xs text-danger">{error}</p>
          )}
        </>
      )}

      <div className="mt-6 flex items-center justify-end gap-2">
        {!done && (
          <button
            onClick={onClose}
            disabled={busy}
            className="ctl ctl-md ctl-ghost bg-elevated px-2.5 hover:bg-elevated/80 disabled:opacity-50"
          >
            以后再说
          </button>
        )}
        {done ? null : auto ? (
          <button
            onClick={start}
            disabled={busy || !url}
            className="ctl ctl-md ctl-btn pri px-2.5 disabled:opacity-50"
          >
            {busy ? '正在更新…' : '立即更新'}
          </button>
        ) : url ? (
          <a href={url} onClick={onClose} className="ctl ctl-md ctl-btn pri px-2.5">
            下载 {info.latest}
          </a>
        ) : (
          <span className="ctl ctl-md ctl-ghost px-2.5 text-muted" aria-disabled="true">
            暂无安装包
          </span>
        )}
      </div>
    </Modal>
  )
}
