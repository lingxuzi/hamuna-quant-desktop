// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// useVersion 仅被 useUpdateCheck hook 使用; 打断 react-query 导入链, 单测只测命令式 API
vi.mock('@/lib/useSharedQueries', () => ({
  useVersion: () => ({ data: undefined }),
}))

// 更新源是 Cloudflare R2 上的 latest.json (构建时由 VITE_UPDATE_MANIFEST_URL 注入)。
// import.meta.env 在模块加载时求值, 必须 hoist 到 import 之前 (vi.hoisted)。
vi.hoisted(() => {
  vi.stubEnv('VITE_UPDATE_MANIFEST_URL', 'https://cdn.example.com/latest.json')
})

import { checkForUpdate, compareVersion, getUpdateState } from './updateCheck'

const CACHE_KEY = 'update_check_cache'
const DAY = 24 * 60 * 60 * 1000

function seedCache(blob: Record<string, unknown>) {
  localStorage.setItem(CACHE_KEY, JSON.stringify(blob))
}

function readCacheRaw(): string | null {
  return localStorage.getItem(CACHE_KEY)
}

/** 模拟 R2 上的 latest.json 响应 */
function mockFetchManifest(version: string) {
  return vi.fn(async () => ({
    ok: true,
    json: async () => ({ version, tag: `v${version}`, notes_url: 'https://example.com/notes' }),
  }))
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('compareVersion', () => {
  it('按语义版本比较大小', () => {
    expect(compareVersion('0.3.2', '0.3.3')).toBe(-1)
    expect(compareVersion('0.4.0', '0.3.9')).toBe(1)
    expect(compareVersion('1.0.0', '1.0')).toBe(0)
  })

  it('容忍 v 前缀与非法输入 (按 0.0.0 处理)', () => {
    expect(compareVersion('0.3.2', 'v0.3.3')).toBe(-1)
    expect(compareVersion('v1.2.3', '1.2.3')).toBe(0)
    expect(compareVersion('', '0.0.1')).toBe(-1)
  })
})

describe('checkForUpdate', () => {
  it('同版本 24h 内命中缓存, 不发网络请求', async () => {
    seedCache({
      current: '0.3.2', latest: '0.3.3', url: 'https://github.com/r/rel/9',
      found: true, checkedAt: Date.now() - 1000,
    })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.3.2')

    expect(fetchMock).not.toHaveBeenCalled()
    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.latest).toBe('0.3.3')
    expect(getUpdateState().info?.url).toBe('https://github.com/r/rel/9')
  })

  it('缓存过期后重新请求并回写缓存', async () => {
    seedCache({
      current: '0.3.2', latest: '0.3.3', url: 'u',
      found: true, checkedAt: Date.now() - DAY - 1000,
    })
    vi.stubGlobal('fetch', mockFetchManifest('0.9.0'))

    await checkForUpdate('0.3.2')

    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.latest).toBe('0.9.0')
    const cached = JSON.parse(readCacheRaw() ?? '{}')
    expect(cached).toMatchObject({ current: '0.3.2', latest: '0.9.0', found: true })
  })

  it('当前版本已是最新时状态为 latest', async () => {
    vi.stubGlobal('fetch', mockFetchManifest('v0.3.2'))

    await checkForUpdate('0.3.2')

    expect(getUpdateState().status).toBe('latest')
  })

  it('版本变化后缓存失效, 重新请求', async () => {
    seedCache({
      current: '0.3.1', latest: '0.3.2', url: 'u',
      found: true, checkedAt: Date.now(),
    })
    const fetchMock = mockFetchManifest('0.3.2')
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.3.2') // 本地版本已升级, 与缓存的 current 不一致

    expect(fetchMock).toHaveBeenCalled()
    expect(getUpdateState().status).toBe('latest')
  })

  it('force 绕过新鲜缓存立即刷新', async () => {
    seedCache({
      current: '0.3.2', latest: '0.9.9', url: 'u',
      found: true, checkedAt: Date.now(),
    })
    const fetchMock = mockFetchManifest('0.3.2')
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('0.3.2', { force: true })

    expect(fetchMock).toHaveBeenCalled()
    expect(getUpdateState().status).toBe('latest')
  })

  it('清单请求失败时状态 error 且不落缓存 (下次启动重试)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))

    await checkForUpdate('0.3.2')

    expect(getUpdateState().status).toBe('error')
    expect(readCacheRaw()).toBeNull()
  })

  it('清单只带 tag 不带 version 时取 tag', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ tag: '1.0.0', notes_url: 'https://example.com/notes' }),
    })))

    await checkForUpdate('0.3.2')

    expect(getUpdateState().status).toBe('found')
    expect(getUpdateState().info?.latest).toBe('1.0.0')
    expect(getUpdateState().info?.url).toBe('https://example.com/notes')
  })

  it('版本未知时空操作 (不请求不落缓存)', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    await checkForUpdate('')

    expect(fetchMock).not.toHaveBeenCalled()
    expect(getUpdateState().status).not.toBe('checking')
  })
})
// 流水线变量缺失时 release.yml 的 format('{0}/latest.json', '') 会拼出相对路径
// "/latest.json" —— 这种值必须当作未配置, 否则每次静默检查都打到同源静态目录 404。
describe('MANIFEST_URL 注入校验', () => {
  it('相对路径视为未配置, 整体停用', async () => {
    vi.resetModules()
    vi.stubEnv('VITE_UPDATE_MANIFEST_URL', '/latest.json')
    try {
      const mod = await import('./updateCheck')
      const fetchMock = vi.fn()
      vi.stubGlobal('fetch', fetchMock)

      await mod.checkForUpdate('0.3.2', { force: true })

      expect(fetchMock).not.toHaveBeenCalled()
      expect(mod.getUpdateState().status).toBe('idle')
    } finally {
      vi.stubEnv('VITE_UPDATE_MANIFEST_URL', 'https://cdn.example.com/latest.json')
      vi.resetModules()
    }
  })
})
