/** 北京时间日历日工具 — 与后端 `app/market_time.py` 的 CN_TZ (UTC+8) 同口径。
 *
 * 为什么不能用 `new Date().toISOString().slice(0, 10)`:
 * toISOString() 是 UTC 日期, 北京时间 00:00–08:00 之间比北京日历日少一天
 * (北京 00:30 = UTC 前一天 16:30) —— "今天"会算成昨天, 且每天有 8 小时是错的。
 * 同理 `new Date()` 的本地字段取决于浏览器时区, 浏览器不在东八区就对不上。
 */

/** 北京时区 */
const CN_TIME_ZONE = 'Asia/Shanghai'

/** 格式化器提到模块级复用: 构造 Intl.DateTimeFormat 约 47µs, 本模块被排序比较调用 O(n·log n) 次。 */
const CN_DATE = new Intl.DateTimeFormat('en-CA', {
  timeZone: CN_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
})

/** 任意时刻 → 北京时间日历日 YYYY-MM-DD */
export function cnDateStr(d: Date = new Date()): string {
  return CN_DATE.format(d)
}

const CN_HM = new Intl.DateTimeFormat('en-GB', {
  timeZone: CN_TIME_ZONE, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

/** 任意时刻 → 北京时间 时:分 (24 小时制, "HH:MM") */
export function cnHm(d: Date): string {
  return CN_HM.format(d)
}

const CN_HMS = new Intl.DateTimeFormat('en-GB', {
  timeZone: CN_TIME_ZONE, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})

/** 任意时刻 → 北京时间 时:分:秒 (24 小时制, "HH:MM:SS") — 日志时间戳用 */
export function cnHms(d: Date): string {
  return CN_HMS.format(d)
}

/** 北京时间今天 YYYY-MM-DD */
export function cnToday(): string {
  return cnDateStr(new Date())
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function ymd(y: number, m: number, d: number): string {
  return `${y}-${pad2(m)}-${pad2(d)}`
}

/** 日历日 + N 天 (纯日历运算, 不经时区; base 必须是 YYYY-MM-DD) */
export function cnAddDays(base: string, days: number): string {
  const t = new Date(Date.UTC(Number(base.slice(0, 4)), Number(base.slice(5, 7)) - 1, Number(base.slice(8, 10)) + days))
  return ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate())
}

/** 日历日 + N 月 (月末溢出同 setMonth: 1/31 + 1 月 → 3/3) */
export function cnAddMonths(base: string, months: number): string {
  const t = new Date(Date.UTC(Number(base.slice(0, 4)), Number(base.slice(5, 7)) - 1 + months, Number(base.slice(8, 10))))
  return ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate())
}
