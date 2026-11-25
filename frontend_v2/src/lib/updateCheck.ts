/**
 * 应用更新检查 — 单例 store (useSyncExternalStore)。
 *
 * 侧栏左下角版本号徽标共用这一状态与网络请求 (徽标点击直接打开下载页)。
 * 静默检查结果按天缓存 localStorage: 同一版本 24h 内只发一次真实请求;
 * 手动检查 (force) 绕过缓存立即刷新。检查失败不落缓存, 下次启动仍会重试,
 * 但单次会话只静默检查一次。使用期间每小时静默复检 (silent+force):
 * 新版本发布后最长 1 小时亮 NEW; 复检失败不打掉已亮的 found 状态。
 *
 * 更新源 = Cloudflare R2 上的 latest.json (发布流水线 release.yml 生成)。
 * 不直连 GitHub/Gitee API: 仓库在 Gitee 且私有, GitHub 只是构建机,
 * 客户端都读不到; R2 固定 URL 是唯一对外更新契约 (可换 CDN 而不动客户端)。
 */
import { useEffect, useSyncExternalStore } from 'react'
import { useVersion } from '@/lib/useSharedQueries'

/**
 * 更新清单地址。构建时由 VITE_UPDATE_MANIFEST_URL 注入 (R2 公开桶 / 自定义域);
 * 未注入 (空, 或流水线变量缺失时拼出的相对路径 `/latest.json`) → 整体停用,
 * 不发出任何网络请求, 也不猜默认地址。
 */
const manifestRaw = (import.meta.env.VITE_UPDATE_MANIFEST_URL ?? '').trim()
const MANIFEST_URL = /^https?:\/\//i.test(manifestRaw) ? manifestRaw : ''
/** 下载页 — 清单缺失 notes_url 时的兜底跳转目标 */
export const RELEASES_URL = (import.meta.env.VITE_DOWNLOAD_PAGE_URL ?? '').trim()
const CACHE_KEY = 'update_check_cache'
const CACHE_TTL_MS = 24 * 60 * 60 * 1000

export type UpdateStatus = 'idle' | 'checking' | 'latest' | 'found' | 'error'

export interface UpdateInfo {
  latest: string
  /** 安装包直链 (清单 channels 里本平台那条; 取不到时为空) */
  downloadUrl: string
  /** 清单里登记的安装包大小与 sha256, 桌面版下载后据此校验 */
  size: number
  sha256: string
  /** 更新内容 (清单 notes, 每条一行); 老清单没有这个字段 → 空数组 */
  notes: string[]
  /** Release 页 —— 仅兜底, 正常情况下用户不需要去这里 */
  url: string
}

/** 本平台的渠道名, 与 release.yml 的 channels_cfg 对应 */
function currentChannel(): string {
  const p = (
    (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ||
    navigator.platform ||
    navigator.userAgent ||
    ''
  ).toLowerCase()
  if (p.includes('win')) return 'windows'
  if (p.includes('mac') || p.includes('darwin')) return 'macos-arm64'
  return 'linux-x64'
}

interface UpdateState {
  status: UpdateStatus
  info: UpdateInfo | null
  checkedAt: number
}

let state: UpdateState = { status: 'idle', info: null, checkedAt: 0 }
const listeners = new Set<() => void>()

function setState(next: Partial<UpdateState>): void {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getUpdateState(): UpdateState {
  return state
}

/** 语义版本比较: a<b → -1, 相等 → 0, a>b → 1; 无法解析按 0.0.0 处理 */
export function compareVersion(a: string, b: string): number {
  const parse = (v: string) =>
    (v.trim().replace(/^v/i, '').match(/\d+/g) ?? []).slice(0, 3).map(Number)
  const pa = parse(a)
  const pb = parse(b)
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (d !== 0) return Math.sign(d)
  }
  return 0
}

interface CacheBlob {
  current: string
  latest: string
  downloadUrl: string
  size: number
  sha256: string
  notes: string[]
  url: string
  found: boolean
  checkedAt: number
}

function readCache(): CacheBlob | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<CacheBlob>
    if (
      typeof v.current !== 'string' ||
      typeof v.latest !== 'string' ||
      typeof v.url !== 'string' ||
      typeof v.found !== 'boolean' ||
      typeof v.checkedAt !== 'number'
    ) {
      return null
    }
    // 老版本缓存的没有 downloadUrl/notes 字段, 补默认值而不是丢弃缓存
    // (丢弃会让升级后首次启动多一次请求, 无害但没必要)
    return {
      ...v,
      downloadUrl: typeof v.downloadUrl === 'string' ? v.downloadUrl : '',
      size: typeof v.size === 'number' ? v.size : 0,
      sha256: typeof v.sha256 === 'string' ? v.sha256 : '',
      notes: Array.isArray(v.notes) ? v.notes.filter((n): n is string => typeof n === 'string') : [],
    } as CacheBlob
  } catch {
    return null
  }
}

function writeCache(blob: CacheBlob): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(blob))
  } catch {
    // 隐私模式/存储满: 只影响下次启动多一次请求, 可忽略
  }
}

/** 拉取 R2 上的 latest.json (release.yml 的 manifest job 生成并上传)。 */
async function fetchLatest(): Promise<UpdateInfo> {
  const r = await fetch(MANIFEST_URL, { cache: 'no-store' })
  if (!r.ok) throw new Error(`manifest http ${r.status}`)
  const manifest = await r.json()
  const latest = String(manifest.version ?? manifest.tag ?? '').trim()
  if (!latest) throw new Error('no release info')
  // 安装包直链: 优先本平台渠道, 退化到清单里任意一条 (单平台发布时也能下)
  type Channel = { url?: string; size?: number; sha256?: string }
  const channels = (manifest.channels ?? {}) as Record<string, Channel>
  const ch = channels[currentChannel()] ?? Object.values(channels)[0]
  const downloadUrl = String(ch?.url ?? '')
  // 桌面版自动更新用这两项校验下载结果 (浏览器里只是普通链接, 用不到)
  const size = Number(ch?.size ?? 0) || 0
  const sha256 = String(ch?.sha256 ?? '')
  const notes = Array.isArray(manifest.notes)
    ? manifest.notes.map((n: unknown) => String(n).trim()).filter(Boolean)
    : []
  // notes_url 只是兜底: 国内访问不了 GitHub, 主入口是 downloadUrl
  return { latest, downloadUrl, size, sha256, notes, url: String(manifest.notes_url ?? '') || RELEASES_URL }
}

let inFlight = false

export async function checkForUpdate(
  current: string,
  opts: { force?: boolean; silent?: boolean } = {},
): Promise<void> {
  const cur = current.trim()
  // 未配置清单地址 = 该构建不提供在线更新检查, 静默停用
  if (!cur || !MANIFEST_URL || inFlight) return
  if (!opts.force) {
    const c = readCache()
    if (c && c.current === cur && Date.now() - c.checkedAt < CACHE_TTL_MS) {
      setState({
        status: c.found ? 'found' : 'latest',
        info: {
          latest: c.latest, downloadUrl: c.downloadUrl, size: c.size,
          sha256: c.sha256, notes: c.notes, url: c.url,
        },
        checkedAt: c.checkedAt,
      })
      return
    }
  }
  inFlight = true
  if (!opts.silent) setState({ status: 'checking' })
  const checkedAt = Date.now()
  try {
    const info = await fetchLatest()
    const found = compareVersion(cur, info.latest) < 0
    setState({ status: found ? 'found' : 'latest', info, checkedAt })
    writeCache({
      current: cur,
      latest: info.latest,
      downloadUrl: info.downloadUrl,
      size: info.size,
      sha256: info.sha256,
      notes: info.notes,
      url: info.url,
      found,
      checkedAt,
    })
  } catch {
    // 不落缓存: 下次启动重试 (静默检查每次会话最多一次, 不会打爆限额)。
    // 每小时复检 (silent) 失败时若已亮 NEW, 保留 found 不降级为 error。
    if (!(opts.silent && state.status === 'found')) {
      setState({ status: 'error', checkedAt })
    }
  } finally {
    inFlight = false
  }
}

let silentStarted = false
let hourlyStarted = false

/** 每小时静默复检间隔 (ms)。 */
const HOURLY_MS = 60 * 60 * 1000

/**
 * 侧栏版本号徽标用的更新检查 hook。
 * 版本号就绪后自动做一次静默检查 (每次会话最多一次);
 * 此后每小时静默复检 (silent+force 绕过 24h 缓存, 捕捉使用期间发布的新版本)。
 */
export function useUpdateCheck(): {
  status: UpdateStatus
  info: UpdateInfo | null
  checkedAt: number
} {
  const s = useSyncExternalStore(subscribe, getUpdateState)
  const { data: versionData } = useVersion()
  const current = (versionData?.version ?? '').trim()
  useEffect(() => {
    if (!current || silentStarted) return
    silentStarted = true
    void checkForUpdate(current)
  }, [current])
  // 每小时复检: 模块级单例 (Layout 常驻持有; 多消费者不重复起定时器)
  useEffect(() => {
    if (!current || hourlyStarted) return
    hourlyStarted = true
    const id = setInterval(() => {
      void checkForUpdate(current, { force: true, silent: true })
    }, HOURLY_MS)
    return () => clearInterval(id)
  }, [current])
  return { status: s.status, info: s.info, checkedAt: s.checkedAt }
}
