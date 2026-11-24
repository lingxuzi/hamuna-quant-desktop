import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { EmptyState } from '@/components/EmptyState'
import { SectionHead } from '@/components/SectionHead'
import { QK } from '@/lib/queryKeys'

/** 对外开放 scope 清单 (与后端 api_tokens.SCOPES 同步) */
const SCOPES: { key: string; label: string; desc: string; default: boolean }[] = [
  { key: 'read:market', label: '行情读取', desc: '标的搜索 / 日K / 分时 / 指数 / 市场快照', default: true },
  { key: 'read:ext', label: '扩展数据读取', desc: '扩展表 rows / values / schema 查询', default: false },
  { key: 'write:ext', label: '扩展数据写入', desc: '向已配置的扩展表程序化写入行数据 (会进入策略/回测数据面, 谨慎授予)', default: false },
  { key: 'read:analysis', label: '分析结果读取', desc: '策略清单与结果 / 回测报告 / 市场环境 / 告警', default: false },
  { key: 'run:backtest', label: '触发回测', desc: '提交回测 / 策略 / 因子检验任务 (受并发约束)', default: false },
  { key: 'paper:trade', label: '模拟盘交易', desc: '模拟盘读取与下单/撤单 (写操作, 最高敏感)', default: false },
]

export function SettingsApiTokensPanel() {
  const qc = useQueryClient()
  const tokensQ = useQuery({ queryKey: QK.apiTokens, queryFn: api.apiTokensList })
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<string[]>(['read:market'])
  const [plaintext, setPlaintext] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')

  const createM = useMutation({
    mutationFn: () => api.apiTokenCreate(name, scopes),
    onSuccess: r => {
      setPlaintext(r.plaintext)
      setCreating(false)
      setName('')
      setScopes(['read:market'])
      setError('')
      qc.invalidateQueries({ queryKey: QK.apiTokens })
    },
    onError: e => setError(String((e as Error).message)),
  })

  const revokeM = useMutation({
    mutationFn: (id: string) => api.apiTokenRevoke(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: QK.apiTokens }),
  })

  const tokens = (tokensQ.data?.tokens ?? []).filter(t => !t.revoked)

  return (
    <div className="mx-auto max-w-3xl">
      <section>
        <SectionHead
          kicker="凭据"
          title="API Token"
          desc="外部调用方的第二认证通道 — 与面板密码并行, 互不影响。"
          right={
            <button
              onClick={() => setCreating(v => !v)}
              className="ctl-btn ctl-sm shrink-0"
            >
              <Plus className="h-[12px] w-[12px]" /> 新建 Token
            </button>
          }
          className="mb-4"
        />

        {/* 新建表单 */}
        {creating && (
          <div className="mt-4 border-t border-border-strong pt-1.5 space-y-3">
            <div className="rowset">
              <div className="r">
                <div className="lab">
                  <b>名称 (用途备注)</b>
                </div>
                <div className="ctl">
                  <input
                    value={name}
                    onChange={e => setName(e.target.value)}
                    placeholder="如: 我的行情看板"
                    className="ctl ctl-sm ctl-input min-w-0 px-1.5 focus:border-accent bg-base"
                  />
                </div>
              </div>
            </div>
            <div className="rowset">
              {SCOPES.map(s => (
                <div key={s.key} className="r">
                  <div className="lab">
                    <b>
                      {s.label}
                      <span className="ml-1.5 font-mono text-[10px] font-normal text-accent/80">{s.key}</span>
                      {s.default && <span className="ml-1.5 font-mono text-[9px] font-normal text-ochre">默认</span>}
                    </b>
                    <small>{s.desc}</small>
                  </div>
                  <div className="ctl">
                    <input
                      type="checkbox"
                      checked={scopes.includes(s.key)}
                      onChange={e => setScopes(prev => e.target.checked ? [...prev, s.key] : prev.filter(x => x !== s.key))}
                      className="h-3.5 w-3.5 shrink-0"
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="text-[10px] text-muted">
              扩展表的结构配置/上传/拉取、数据同步、设置等管理接口永不开放给 Token (行数据写入走 write:ext)。调用方式: <span className="font-mono">Authorization: Bearer hq_...</span>, 默认限流 120 次/分钟, 契约文档 <span className="font-mono text-accent/80">/api/openapi.json?tier=a</span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => name.trim() && scopes.length > 0 && createM.mutate()}
                disabled={!name.trim() || scopes.length === 0 || createM.isPending}
                className="ctl-btn ctl-sm pri"
              >
                {createM.isPending ? '创建中…' : '创建'}
              </button>
              <button onClick={() => setCreating(false)} className="ctl-btn ctl-sm">取消</button>
            </div>
            {error && <div className="border-l-2 border-danger px-2 py-1 text-[11px] text-danger">{error}</div>}
          </div>
        )}

        {/* 明文一次性展示 */}
        {plaintext && (
          <div className="mt-4 border-y border-warning/40 py-3">
            <div className="text-xs font-medium text-warning">令牌已创建 — 明文只显示这一次, 请立即保存</div>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate bg-elevated px-2 py-1.5 font-mono text-xs">{plaintext}</code>
              <button
                onClick={() => { navigator.clipboard?.writeText(plaintext); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
                className="ctl-btn ctl-sm shrink-0"
              >
                <Copy className="h-[12px] w-[12px]" /> {copied ? '已复制' : '复制'}
              </button>
              <button onClick={() => setPlaintext(null)} className="ctl-btn ctl-sm shrink-0">关闭</button>
            </div>
          </div>
        )}

        {/* 列表 */}
        <div className="mt-4">
          {tokensQ.isLoading ? (
            <div className="py-6 text-center text-xs text-muted">加载中…</div>
          ) : tokens.length === 0 ? (
            <EmptyState
              title="尚无 Token"
              hint="创建一个, 外部程序即可按 scope 调用开放接口。"
            />
          ) : (
            <div className="rowset">
              {tokens.map(t => (
                <div key={t.id} className="r">
                  <div className="lab min-w-0">
                    <b>
                      {t.name}
                      <span className="ml-1.5 font-mono text-[10px] font-normal text-muted">{t.id}</span>
                    </b>
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {t.scopes.map(s => (
                        <span key={s} className="font-mono text-[9px] text-accent/90">{s}</span>
                      ))}
                      <span className="text-[9px] text-muted">
                        创建 {t.created_at?.slice(0, 16).replace('T', ' ')}
                        {t.last_used_at ? ` · 最近使用 ${t.last_used_at.slice(11, 16)}` : ' · 未使用过'}
                      </span>
                    </div>
                  </div>
                  <div className="ctl">
                    <button
                      onClick={() => revokeM.mutate(t.id)}
                      disabled={revokeM.isPending}
                      className="ctl-btn ctl-sm text-danger"
                    >
                      吊销
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
