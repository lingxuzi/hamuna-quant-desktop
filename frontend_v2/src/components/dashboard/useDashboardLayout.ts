/**
 * 看板布局持久化 hook — 后端偏好加载 + 本地改动防抖落盘。
 *
 * 返回 { items, setItems }:
 * - items: 本地有改动取本地, 否则取后端保存布局(规范化), 再退内置默认。
 * - setItems: 本地改动入口; 800ms 防抖保存到 PUT /preferences/dashboard-layout,
 *   与内置默认布局完全一致时存 null(清除), 让代码内置默认可随版本演进;
 *   防抖未到期就卸载(如编辑完点「完成」后立刻跳页)时立即落盘, 否则最后一次改动丢失。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { usePreferences } from '@/lib/useSharedQueries'
import { DEFAULT_LAYOUT } from './registry'
import { normalizeDashboardLayout } from './DashboardGrid'
import { toBlob, type DashboardItem } from './layout'

const SAVE_DEBOUNCE_MS = 800

export function useDashboardLayout(): {
  items: DashboardItem[]
  setItems: (next: DashboardItem[]) => void
} {
  const { data: prefs } = usePreferences()
  const saved = useMemo(
    () => normalizeDashboardLayout(prefs?.dashboard_layout ?? undefined),
    [prefs?.dashboard_layout],
  )
  const [local, setLocal] = useState<DashboardItem[] | null>(null)

  const pendingRef = useRef<DashboardItem[] | null>(null)
  const save = (items: DashboardItem[]) => {
    const blob = toBlob(items)
    const isDefault = JSON.stringify(blob.items) === JSON.stringify(DEFAULT_LAYOUT)
    return api.saveDashboardLayout(isDefault ? null : blob)
      .catch(() => { /* 保存失败静默: 布局仍在本会话生效, 下次刷新回退上次保存 */ })
  }

  useEffect(() => {
    if (local === null) return
    pendingRef.current = local
    const timer = window.setTimeout(() => { pendingRef.current = null; void save(local) }, SAVE_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [local])

  // 防抖未到期即卸载 → 立刻补存; 刷新前关页(pagehide)同理
  useEffect(() => {
    const flush = () => {
      const items = pendingRef.current
      if (!items) return
      pendingRef.current = null
      void save(items)
    }
    window.addEventListener('pagehide', flush)
    return () => { window.removeEventListener('pagehide', flush); flush() }
  }, [])

  return { items: local ?? saved, setItems: setLocal }
}
