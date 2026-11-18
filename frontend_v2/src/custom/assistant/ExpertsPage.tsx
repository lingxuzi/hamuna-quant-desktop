/**
 * 专家页 — 分类体系 + 专家清单 (一个专家 = 一个 skill)。
 *
 * 正文在 <data_dir>/assistant/skills/<名称>/SKILL.md, 分类归属与启用状态在
 * <data_dir>/assistant/experts.json (后端是唯一真相, 见 ui/ExpertManager)。
 */
import { PageHeader } from '@/components/PageHeader'
import { ExpertManager } from './ui/ExpertManager'

export function Experts() {
  return (
    <div className="flex min-h-0 flex-col">
      <PageHeader seal="专" title="专家" subtitle="EXPERTS" />
      <div className="mt-4">
        <ExpertManager scope="all" />
      </div>
    </div>
  )
}
