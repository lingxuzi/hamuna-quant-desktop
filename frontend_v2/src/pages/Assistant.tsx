/**
 * AI 助手独立页 (design/v2/24) — 复用抽屉的会话、流式与工具确认,
 * 不用打开右缘抽屉; 全局悬浮球与 ⌘K 仍可用。
 */
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/PageHeader'
import { AssistantPage } from '@/custom/assistant/ui/AssistantDrawer'
import { fetchAssistantStatus, fetchAssistantSuggests } from '@/custom/assistant/client'
import { newSession, useAssistantStore } from '@/custom/assistant/store'

export function Assistant() {
  // 页头状态签与右栏状态同源, 同一端点走 react-query 缓存
  const status = useQuery({ queryKey: ['assistant-status'], queryFn: fetchAssistantStatus, staleTime: 60_000 })
  const suggests = useQuery({ queryKey: ['assistant-suggests'], queryFn: fetchAssistantSuggests, staleTime: 300_000 })
  const { sending } = useAssistantStore()
  const s = status.data
  const list = suggests.data ?? []
  const groupCount = new Set(list.map(item => item.group || '更多')).size

  return (
    <div className="flex min-h-0 flex-col">
      <PageHeader seal="助"
        title="AI 助手"
        subtitle="智能助手"
        titleExtra={
          <>
            <span className={`tb-chip${s?.configured ? '' : ' warn'}`}>{s?.configured ? '已连接' : '未配置'}</span>
            {s?.model && <span className="tb-chip">{s.model}</span>}
            <span className="tb-chip">{list.length} 条快捷指令</span>
          </>
        }
        right={
          <button type="button" className="ctl ctl-md ctl-btn" onClick={() => newSession()} disabled={sending}>
            新对话
          </button>
        }
      />
      <div className="ai min-h-0 flex-1">
        <AssistantPage />
        <aside>
          <div className="fig-h">连 接 状 态</div>
          <table className="lh-t">
            <colgroup><col /><col style={{ width: 120 }} /></colgroup>
            <tbody>
              <tr><td>状态</td><td className="up">{s?.configured ? '已配置' : '未配置'}</td></tr>
              <tr><td>供应商</td><td>{s?.provider ?? '—'}</td></tr>
              <tr><td>模型</td><td>{s?.model ?? '—'}</td></tr>
              <tr><td>工具调用</td><td className={s?.supports_tools ? 'up' : 'down'}>{s?.supports_tools ? '支持' : '不支持'}</td></tr>
              <tr><td>快捷指令</td><td>{list.length} 条 / {groupCount} 组</td></tr>
            </tbody>
          </table>

          <div className="fig-h">几 个 边 界</div>
          <table className="lh-t">
            <colgroup><col style={{ width: 76 }} /><col /></colgroup>
            <tbody>
              <tr><td>能读</td><td className="wrap">行情、财务、板块、策略、回测结果、数据状态</td></tr>
              <tr><td>能写</td><td className="wrap">建信号、下单、跑回测 —— 都要确认</td></tr>
              <tr><td>不碰</td><td className="wrap">不改你的密钥；不替你决定买卖</td></tr>
            </tbody>
          </table>

          <div className="fig-h">调 用 方 式</div>
          <p className="text-[11px] leading-[1.85] text-secondary">
            右下角浮标、顶部入口、或 ⌘K 都能呼出。抽屉可以左缘拖拽调宽，会话按标题存档，退出后还在。
          </p>
        </aside>
      </div>
    </div>
  )
}
