import type { CapabilityRoute, ProviderField } from '@/lib/api'

/** 内置数据源: id 是内部路由键, 展示名对外只叫「内部数据源」(不暴露实现库名)。 */
export const BUILTIN_PROVIDER = 'easytdx'
export const BUILTIN_PROVIDER_LABEL = '内部数据源'

/** 全部可路由偏好字段的默认生效源。
 *
 *  行情类 (日K/除权/分钟/全量分钟/五档/实时) 跟随内置源,
 *  财务跟随同花顺官方插件 —— 与后端 capabilities 注册表同口径。
 */
export const DEFAULT_ROUTING: Record<ProviderField, string> = {
  daily_data_provider: BUILTIN_PROVIDER,
  adj_factor_provider: BUILTIN_PROVIDER,
  minute_data_provider: BUILTIN_PROVIDER,
  full_minute_data_provider: BUILTIN_PROVIDER,
  depth5_data_provider: BUILTIN_PROVIDER,
  realtime_data_provider: BUILTIN_PROVIDER,
  financial_data_provider: 'fuyao',
}

/**
 * 计算「一键套用」的目标路由: 该源声明了哪个数据集, 就接管对应能力;
 * 未声明的能力回落默认源, 避免残留上一个数据源的配置。
 *
 * 能力清单取自后端能力矩阵 (dataset id 与能力 field 的单一权威), 不在前端
 * 手写字段列表 —— 手写清单会漏掉新增能力 (depth5 / full_minute 就这样被漏过,
 * 点「套用」后这两个能力仍停在旧数据源)。
 *
 * 矩阵未加载时 fail-closed 抛错, 而不是把全部路由重置成默认源。
 */
export function buildProviderRouting(
  provider: string,
  datasets: readonly string[],
  capabilities: readonly Pick<CapabilityRoute, 'id' | 'field'>[],
): Record<ProviderField, string> {
  if (!capabilities.length) {
    throw new Error('能力矩阵未加载, 无法套用数据源, 请稍后重试')
  }
  const supported = new Set(datasets)
  const routing: Record<ProviderField, string> = { ...DEFAULT_ROUTING }
  for (const cap of capabilities) {
    if (!cap.field) continue // 不可路由能力 (无偏好字段)
    routing[cap.field] = supported.has(cap.id) ? provider : DEFAULT_ROUTING[cap.field]
  }
  return routing
}
