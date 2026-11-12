// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cnAddDays, cnAddMonths, cnDateStr, cnHm, cnToday } from './cnDate'

describe('北京时间日历日', () => {
  afterEach(() => { vi.useRealTimers() })

  it('凌晨 0-8 点仍算今天, 不退化成昨天 (UTC 切片的原 bug)', () => {
    vi.useFakeTimers()
    // 北京 2026-11-12 04:00 = UTC 2026-11-11 20:00 → toISOString 会给 11-11
    vi.setSystemTime(new Date('2026-11-11T20:00:00Z'))
    expect(cnToday()).toBe('2026-11-12')
    expect(new Date().toISOString().slice(0, 10)).toBe('2026-11-11')
  })

  it('午后与跨日边界按北京日历日切', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-12T02:30:00Z'))   // 北京 10:30
    expect(cnToday()).toBe('2026-11-12')
    vi.setSystemTime(new Date('2026-11-12T16:30:00Z'))   // 北京次日 00:30
    expect(cnToday()).toBe('2026-11-13')
    vi.setSystemTime(new Date('2026-11-12T15:59:59Z'))   // 北京 23:59:59
    expect(cnToday()).toBe('2026-11-12')
  })

  it('任意时刻 → 北京日历日', () => {
    expect(cnDateStr(new Date('2026-11-11T16:00:00Z'))).toBe('2026-11-12')
    expect(cnDateStr(new Date('2026-11-12T15:59:59Z'))).toBe('2026-11-12')
  })

  it('日历日加减跨月跨年, 不受时区影响', () => {
    expect(cnAddDays('2026-11-12', -1)).toBe('2026-11-11')
    expect(cnAddDays('2026-03-01', -1)).toBe('2026-02-28')
    expect(cnAddDays('2024-03-01', -1)).toBe('2024-02-29')
    expect(cnAddDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(cnAddMonths('2026-11-12', -6)).toBe('2026-05-12')
    expect(cnAddMonths('2026-08-31', -3)).toBe('2026-05-31')
    expect(cnAddMonths('2026-01-31', 1)).toBe('2026-03-03')
  })

  it('北京时间 时:分, 24 小时制', () => {
    expect(cnHm(new Date('2026-11-12T01:31:00Z'))).toBe('09:31')  // 北京 09:31
    expect(cnHm(new Date('2026-11-12T07:30:00Z'))).toBe('15:30')  // 北京 15:30
    expect(cnHm(new Date('2026-11-11T16:00:00Z'))).toBe('00:00')  // 北京次日 00:00
  })
})
