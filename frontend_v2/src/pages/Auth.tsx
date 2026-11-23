/**
 * 访问认证页 — 复用同一组件处理「首次设密码」和「登录」两种状态。
 *
 * 根据后端 /api/auth/status 的 configured 字段决定显示:
 *   - configured=false → 显示「设置访问密码」(首次)
 *   - configured=true  → 显示「登录」
 *
 * 版式取自 design/v2/31-login.html: 卷首 + 工具条 + 左表单 / 右认证状态两栏。
 *
 * 安全:
 *   - 设密码接口后端限本机/内网; 公网用户设密码会被 403 拒绝, 页面据此提示。
 *   - 登录失败由后端限流(5次锁5分钟), 429 时前端显示等待提示。
 */
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Inscription } from '@/components/Inscription'
import { Toolbar } from '@/components/Toolbar'

/** 后端登录限流: 连续 5 次失败锁 5 分钟 (backend/app/api/auth.py 的 _MAX_FAILS/_LOCK_SECONDS) */
const FAIL_LIMIT = '5 次 / 5 分钟'

/** YYYY-MM-DD (浏览器本地时区) — 仅登录页卷首展示用; 行情真日期需登录后由行情接口提供 */
function localDate(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function Auth() {
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')  // 仅设密码时用
  const [showPwd, setShowPwd] = useState(false)
  const [localError, setLocalError] = useState('')

  // 取认证状态(是否已设密码 / 当前是否已登录)
  const [status, setStatus] = useState<{ configured: boolean; authenticated: boolean } | null>(null)
  useEffect(() => {
    api.authStatus().then(s => {
      setStatus(s)
      // 已登录的话直接进面板(避免登录页死循环)
      if (s.authenticated) navigate('/', { replace: true })
    }).catch(() => setStatus({ configured: false, authenticated: false }))
  }, [navigate])

  const isSetup = !status?.configured  // configured=false → 设密码模式

  // 登录 / 设密码 共用一个 mutation(按 isSetup 调不同接口)
  const submitMut = useMutation({
    mutationFn: async () => {
      if (isSetup) {
        return api.authSetup(password)
      }
      return api.authLogin(password)
    },
    onSuccess: () => {
      // 成功: 跳回原页面(或首页)
      const redirect = new URLSearchParams(window.location.search).get('redirect') || '/'
      navigate(redirect, { replace: true })
    },
    onError: (err: any) => {
      const msg = err?.message || (isSetup ? '设置失败' : '登录失败')
      // 设密码/登录失败必须显示: 401(密码错)/403(公网设密码被拒)/429(限流) 都要提示
      setLocalError(msg)
    },
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setLocalError('')
    if (isSetup) {
      if (password.length < 6) { setLocalError('密码至少 6 位'); return }
      if (password !== confirmPassword) { setLocalError('两次密码不一致'); return }
    }
    submitMut.mutate()
  }

  if (!status) {
    return (
      <div className="min-h-full bg-base grid place-items-center">
        <Loader2 className="h-[20px] w-[20px] animate-spin text-muted" />
      </div>
    )
  }

  return (
    <div className="page scroll-shell h-full overflow-auto scrollbar-gutter-stable">
      {/* 未登录时行情接口 401, 拿不到行情日期/收盘状态 → 用浏览器本地日期, 恒为非实时 */}
      <Inscription
        title="登录" subtitle="AUTH" seal="凭"
        date={localDate()} running={false}
      />

      <Toolbar
        left={
          <>
            <span className="tb-chip">{status.configured ? '已设密码' : '未设密码'}</span>
            <span className="tb-chip">{status.authenticated ? '当前已登录' : '当前未登录'}</span>
          </>
        }
      />

      <div className="auth">
        <div className="auth-card">
          <div className="auth-brand">H-Quant</div>
          <div className="auth-seal">凭</div>
          <h1>{isSetup ? '设置访问密码' : '登录访问'}</h1>
          <p>{isSetup ? '首次使用，请为面板设置访问密码' : '请输入访问密码以继续'}</p>

          <form className="auth-form" onSubmit={handleSubmit}>
            <label htmlFor="pw">访问密码</label>
            <div className="auth-pw">
              <input
                id="pw"
                className="ctl ctl-md ctl-input w-full tracking-[.18em]"
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoFocus
                autoComplete="current-password"
              />
              <button
                type="button"
                onClick={() => setShowPwd(s => !s)}
                tabIndex={-1}
                aria-label={showPwd ? '隐藏密码' : '显示密码'}
              >
                {showPwd ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>

            {isSetup && (
              <>
                <label htmlFor="pw2" className="mt-4">确认密码</label>
                <input
                  id="pw2"
                  className="ctl ctl-md ctl-input w-full tracking-[.18em]"
                  type={showPwd ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  placeholder="再次输入密码"
                  autoComplete="new-password"
                />
              </>
            )}

            {/* 401(密码错) / 403(公网设密码被拒) / 429(限流) 三类错误行内提示 */}
            {(localError || submitMut.error) && (
              <p className="mt-3 text-[11px] leading-relaxed text-danger">
                {localError || submitMut.error?.message}
              </p>
            )}

            <button
              type="submit"
              disabled={submitMut.isPending}
              className="ctl-btn ctl-sm pri mt-1 w-full"
              style={{ padding: '8px 0', fontSize: '11.5px' }}
            >
              {submitMut.isPending ? '处理中…' : isSetup ? '设置并进入' : '登录'}
            </button>
          </form>

          <p className="auth-foot">
            {isSetup ? (
              <>
                出于安全考虑, 首次设置密码需在服务器本机或内网访问时操作。公网环境下仅可登录。部署说明见随程序分发的 deploy-password 文档。
              </>
            ) : (
              <>密码由首次访问时设置。连续输错 5 次将锁定 5 分钟。</>
            )}
          </p>
        </div>

        <div className="auth-side">
          <div className="fig-h">认 证 状 态 ｜ /api/auth/status</div>
          <table className="dt">
            <colgroup><col /><col style={{ width: 110 }} /></colgroup>
            <thead><tr><th>项</th><th>值</th></tr></thead>
            <tbody>
              <tr><td>访问密码</td><td className={status.configured ? 'ok' : 'z'}>{status.configured ? '已设置' : '未设置'}</td></tr>
              <tr><td>当前会话</td><td className={status.authenticated ? 'ok' : 'z'}>{status.authenticated ? '已登录' : '未登录'}</td></tr>
              <tr><td>失败限流</td><td>{FAIL_LIMIT}</td></tr>
              <tr><td>首次设密通道</td><td>本机 / 内网</td></tr>
            </tbody>
          </table>
          <p className="mt-3 max-w-[40ch] text-[10.5px] leading-[1.8] text-secondary">
            同一页面同时服务两种状态：未设密码时显示「设置访问密码」，已设密码时显示「登录访问」。设密码接口只接受本机或内网请求，公网访问会被拒绝并在此页提示。
          </p>
        </div>
      </div>
    </div>
  )
}
