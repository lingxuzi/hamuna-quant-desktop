/**
 * 桌面版「一键更新」—— 下载、校验、退出、安装、重启全自动。
 *
 * 客户端不再出现「另存为」对话框: 下载由桌面进程 (pywebview 的 js_api) 完成,
 * 落到临时目录并校验 size/sha256, 然后自己停后端、起安装器、退出进程。
 * 安装器以 /SILENT 跑, 装完由 [Run] 段拉起新版本 (见 packaging/hquant.iss)。
 *
 * 浏览器里没有 pywebview 对象, canAutoInstall() 为 false, UI 退回普通下载链接。
 */

interface UpdateApi {
  can_auto_install: () => Promise<boolean> | boolean
  install_update: (
    urls: string,
    version: string,
    sha256: string,
    size: number,
  ) => Promise<{ ok: boolean; error: string }>
}

declare global {
  interface Window {
    pywebview?: { api?: Partial<UpdateApi> }
  }
}

export interface InstallResult {
  ok: boolean
  error: string
  /** 桌面桥还没注入 (窗口刚起来时的极短窗口), 让用户重试 */
  retry?: boolean
}

function api(): Partial<UpdateApi> | undefined {
  return typeof window === 'undefined' ? undefined : window.pywebview?.api
}

/** 桥是否已注入 (不等于平台支持: mac 的 .dmg 无法静默安装)。 */
export function hasBridge(): boolean {
  return typeof api()?.install_update === 'function'
}

/**
 * 是否支持应用内自动安装。桌面进程按平台回答 (目前只有 Windows 的
 * Setup.exe 能静默装完自动重启), 浏览器里没有桥 → false, 退回下载链接。
 */
export async function canAutoInstall(): Promise<boolean> {
  const bridge = api()
  if (typeof bridge?.install_update !== 'function') return false
  try {
    return Boolean(await bridge.can_auto_install?.())
  } catch {
    return false
  }
}

/**
 * 等桌面桥注入完成 (最多 timeoutMs)。
 *
 * window.pywebview.api 由 pywebview 在前端脚本之后异步注入; 窗口刚起来就
 * 判断会得到 false, 弹窗于是永远只显示普通下载链接。浏览器里永远等不到,
 * 超时后照常返回 false —— 两种环境都不卡界面。
 */
export function waitForBridge(timeoutMs = 5000): Promise<boolean> {
  if (hasBridge()) return Promise.resolve(true)
  return new Promise((resolve) => {
    const started = Date.now()
    const id = setInterval(() => {
      if (hasBridge()) {
        clearInterval(id)
        resolve(true)
      } else if (Date.now() - started >= timeoutMs) {
        clearInterval(id)
        resolve(false)
      }
    }, 100)
  })
}

/**
 * 触发应用内更新。成功时进程随后会自行退出并重启, 页面不需要做任何跳转 ——
 * 只显示「正在安装, 应用将自动重启」。
 */
export async function installUpdate(info: {
  latest: string
  downloadUrl: string
  sha256: string
  size: number
}): Promise<InstallResult> {
  const bridge = api()
  if (typeof bridge?.install_update !== 'function') {
    return { ok: false, error: '当前环境不支持应用内更新', retry: true }
  }
  try {
    const r = await bridge.install_update(
      info.downloadUrl,
      info.latest,
      info.sha256,
      info.size,
    )
    if (r?.ok) return { ok: true, error: '' }
    return { ok: false, error: r?.error || '更新失败' }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}
