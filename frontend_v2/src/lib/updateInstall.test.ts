// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { canAutoInstall, hasBridge, installUpdate, waitForBridge } from './updateInstall'

const info = {
  latest: '0.3.37',
  downloadUrl: 'https://r2.example.com/HQuant-Setup-x64-0.3.37.exe',
  sha256: 'abc',
  size: 123,
}

afterEach(() => {
  delete (window as Window & { pywebview?: unknown }).pywebview
})

describe('canAutoInstall', () => {
  it('浏览器里没有桌面桥 → false (UI 退回下载链接)', async () => {
    expect(await canAutoInstall()).toBe(false)
    expect(hasBridge()).toBe(false)
  })

  it('桥注入且平台支持 → true', async () => {
    window.pywebview = {
      api: { install_update: vi.fn(), can_auto_install: async () => true },
    }
    expect(hasBridge()).toBe(true)
    expect(await canAutoInstall()).toBe(true)
  })

  it('桥在但平台不支持 (mac 的 .dmg 无法静默装) → false', async () => {
    window.pywebview = {
      api: { install_update: vi.fn(), can_auto_install: async () => false },
    }
    expect(await canAutoInstall()).toBe(false)
  })

  it('平台能力查询异常时按不支持处理', async () => {
    window.pywebview = {
      api: {
        install_update: vi.fn(),
        can_auto_install: async () => { throw new Error('boom') },
      },
    }
    expect(await canAutoInstall()).toBe(false)
  })
})

describe('waitForBridge', () => {
  it('桥已注入时立即返回 true', async () => {
    window.pywebview = { api: { install_update: vi.fn() } }
    expect(await waitForBridge(50)).toBe(true)
  })

  it('浏览器里超时后返回 false, 不无限等', async () => {
    expect(await waitForBridge(50)).toBe(false)
  })
})

describe('installUpdate', () => {
  it('把清单里的链接/版本/sha256/大小原样交给桌面进程', async () => {
    const bridge = vi.fn(async () => ({ ok: true, error: '' }))
    window.pywebview = { api: { install_update: bridge } }

    const r = await installUpdate(info)

    expect(r).toEqual({ ok: true, error: '' })
    expect(bridge).toHaveBeenCalledWith(
      info.downloadUrl, '0.3.37', 'abc', 123,
    )
  })

  it('桌面进程返回失败时把原因透传给 UI, 不能静默', async () => {
    window.pywebview = {
      api: { install_update: async () => ({ ok: false, error: '安装包 sha256 校验失败' }) },
    }

    const r = await installUpdate(info)

    expect(r).toEqual({ ok: false, error: '安装包 sha256 校验失败' })
  })

  it('桥调用抛异常 (下载中断等) 也要变成可显示的错误', async () => {
    window.pywebview = {
      api: { install_update: async () => { throw new Error('network down') } },
    }

    const r = await installUpdate(info)

    expect(r.ok).toBe(false)
    expect(r.error).toBe('network down')
  })

  it('没有桥时提示重试而不假装成功', async () => {
    const r = await installUpdate(info)

    expect(r.ok).toBe(false)
    expect(r.retry).toBe(true)
  })
})
