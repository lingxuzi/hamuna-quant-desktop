/**
 * 统一定义目录面板 — "一切皆扩展"的消费面 (P4)。
 *
 * 跨 kind (信号/因子) 的统一目录 + 逐条导入冒烟。定义 (信号/因子/策略/
 * 数据集) 都走同一条 契约校验 → 编译 → 依赖可解析 链路; 这里是它们的
 * 总目录与体检入口, 也是配置备份导入后核对冒烟结果的落点。
 */
import { DefinitionPicker } from '@/components/DefinitionPicker'
import { SectionHead } from '@/components/SectionHead'
import { PositionPoliciesPanel } from '@/components/settings/PositionPoliciesPanel'

export function SettingsDefinitionsPanel() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PositionPoliciesPanel />
      <section>
        <SectionHead
          kicker="目录"
          title="统一定义目录"
          desc={
            <>
              自定义信号与因子的统一视图 (语言 / 依赖 / 预热 / 状态)。
              「冒烟」当场执行 校验 → 编译 → 依赖可解析, 拼错列名 (W001) 无需跑回测即可发现;
              导入配置备份时同一检查自动执行。
            </>
          }
        />
        <div className="mt-4">
          <DefinitionPicker kinds={['signal', 'factor']} />
        </div>
      </section>
    </div>
  )
}
