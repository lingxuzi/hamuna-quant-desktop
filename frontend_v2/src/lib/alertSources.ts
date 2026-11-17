/**
 * 告警来源 → 中文名。唯一真相在后端 `app/services/quote_service.py` 的
 * SOURCE_LABELS; 前端曾把这份映射抄在 AlertToast / 看板监控中心 / 监控中心页
 * 三处, 各自漏了不同的键, 漏键时 `?? ev.source` 直接把英文键画到界面上 (看着像乱码)。
 * 因此只留这一份, 新增 source 时先改后端再补这里。
 */
export const SOURCE_LABEL: Record<string, string> = {
  strategy: '策略',
  signal: '信号',
  price: '价格',
  market: '异动',
  sector: '板块',
  ladder: '连板梯队',
  abnormal: '异动边缘',
  volume_delta: '放量',
  date: '日期提醒',
  paper: '模拟盘',
  depth: '五档轮询',
}

/** 取不到就原样返回, 但调用方应把英文键当缺陷处理而不是可接受兜底。 */
export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source
}
