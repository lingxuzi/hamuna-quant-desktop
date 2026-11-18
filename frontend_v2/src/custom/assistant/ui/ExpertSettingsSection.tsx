/**
 * 设置→系统「内置专家与分类」区 — 经 settings.system.section 插槽渲染。
 *
 * 内置专家的正文随程序分发 (只读), 这里能配的是分类归属与启用状态 ——
 * 改正文会在下次部署时被覆盖, 也不该把运行时改动脏进仓库。
 * 删掉助手扩展即整行消失, 核心零依赖。
 */
import { ExpertManager } from './ExpertManager'

export function ExpertSettingsSection() {
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="text-sm text-foreground">内置专家与分类</div>
        <div className="text-[11px] text-muted">内置专家正文随程序分发, 这里只配置分类归属与启用状态</div>
      </div>
      <ExpertManager scope="builtin" />
    </div>
  )
}
