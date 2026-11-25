import { Modal } from '@/components/Modal'
import type { UpdateInfo } from '@/lib/updateCheck'

/**
 * 发现新版本时在应用内展示更新内容并直接下载。
 *
 * 不走 GitHub: 仓库在国内访问不了, 清单 latest.json 已内嵌 notes (commit 标题)
 * 与 channels 里各平台的安装包直链 (R2), 点「下载」直接拿安装包。
 * notes 为空 (老清单 / 首个版本) 时只显示版本号, 不留空标题。
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
  const url = info.downloadUrl
  return (
    <Modal
      onClose={onClose}
      labelledBy="update-dialog-title"
      panelClassName="w-[90vw] max-w-[420px] rounded-card border border-border bg-base p-6"
      overlayClassName="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
    >
      <h3 id="update-dialog-title" className="text-sm font-medium text-foreground">
        发现新版本 {info.latest}
      </h3>
      <p className="mt-1 text-xs text-muted">当前 {current || '—'}</p>

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

      <div className="mt-6 flex items-center justify-end gap-2">
        <button onClick={onClose} className="ctl ctl-md ctl-ghost bg-elevated px-2.5 hover:bg-elevated/80">
          以后再说
        </button>
        {url ? (
          <a
            href={url}
            onClick={onClose}
            className="ctl ctl-md ctl-btn pri px-2.5"
          >
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
