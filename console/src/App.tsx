import { useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Brain,
  Gauge,
  KeyRound,
  Radio,
  Satellite,
  Server,
} from 'lucide-react'
import { api, clearToken, getToken, setToken } from './api'
import TitleBar, { Logo } from './components/TitleBar'
import { Button, Input } from './components/ui'
import Alerts from './pages/Alerts'
import Channels from './pages/Channels'
import Dashboard from './pages/Dashboard'
import Insights from './pages/Insights'
import Keys from './pages/Keys'
import LiveTail from './pages/LiveTail'
import Subscription from './pages/Subscription'

type Tab = 'dashboard' | 'channels' | 'keys' | 'livetail' | 'insights' | 'alerts' | 'subscription'

const NAV: { key: Tab; label: string; icon: typeof Gauge; desc: string }[] = [
  { key: 'dashboard', label: '额度大盘', icon: Gauge, desc: '用量、成本与预算' },
  { key: 'channels', label: '渠道管理', icon: Server, desc: '上游渠道与熔断' },
  { key: 'keys', label: '虚拟 Key', icon: KeyRound, desc: '发放与配额' },
  { key: 'livetail', label: '实时请求流', icon: Radio, desc: '逐条请求追踪' },
  { key: 'insights', label: '智能层成绩单', icon: Brain, desc: '缓存与降级收益' },
  { key: 'alerts', label: '告警中心', icon: AlertTriangle, desc: '哨兵巡检事件' },
  { key: 'subscription', label: '订阅用量', icon: Satellite, desc: '本机客户端消耗' },
]

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [tab, setTab] = useState<Tab>('dashboard')
  const [alertCount, setAlertCount] = useState(0)

  useEffect(() => {
    async function boot() {
      // 已有 token 先试
      if (getToken()) {
        try {
          await api.channels()
          setAuthed(true)
          return
        } catch {
          /* token 失效,往下走自动获取 */
        }
      }
      // 本地(回环)访问免登录:直接向网关索取 token
      try {
        const res = await fetch('/bootstrap')
        if (res.ok) {
          const data = await res.json()
          if (data.auto && data.token) {
            setToken(data.token)
            await api.channels()
            setAuthed(true)
            return
          }
        }
      } catch {
        /* 拿不到就回落到手工输入 */
      }
      setAuthed(false)
    }
    boot()
  }, [])

  useEffect(() => {
    if (!authed) return
    const load = () => api.alerts().then((a) => setAlertCount(a.length)).catch(() => {})
    load()
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [authed, tab])

  if (authed === null) {
    return (
      <div className="flex h-full items-center justify-center bg-surface text-[13px] text-ink-mid">
        正在连接网关…
      </div>
    )
  }
  if (!authed) return <Login onSuccess={() => setAuthed(true)} />

  const active = NAV.find((n) => n.key === tab)!

  return (
    <div className="flex h-full flex-col bg-surface">
      <TitleBar status={<ConnectionPill />} />

      <div className="flex min-h-0 flex-1">
        {/* ——— 侧栏:图标 + 文字,激活项有竖条与白底 ——— */}
        <nav className="rail-wash flex w-[220px] flex-none flex-col border-r border-line">
          <div className="px-3 pb-2 pt-4">
            <div className="flex items-center gap-2.5 rounded-xl bg-surface-card px-3 py-2.5 shadow-sm">
              <Logo size={30} />
              <div className="min-w-0">
                <div className="truncate text-[13px] font-semibold text-ink-hi">LLM 网关</div>
                <div className="truncate text-[10.5px] text-ink-low">数据面 + 控制面</div>
              </div>
            </div>
          </div>

          <div className="px-4 pb-1.5 pt-3">
            <span className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink-low">
              监控
            </span>
          </div>
          <div className="space-y-0.5 px-2">
            {NAV.slice(0, 1).map((n) => (
              <NavItem key={n.key} n={n} active={tab === n.key} onClick={() => setTab(n.key)} />
            ))}
            {NAV.slice(3, 5).map((n) => (
              <NavItem key={n.key} n={n} active={tab === n.key} onClick={() => setTab(n.key)} />
            ))}
            <NavItem
              n={NAV[5]}
              active={tab === 'alerts'}
              onClick={() => setTab('alerts')}
              badge={alertCount}
            />
            <NavItem key="sub" n={NAV[6]} active={tab === 'subscription'} onClick={() => setTab('subscription')} />
          </div>

          <div className="px-4 pb-1.5 pt-5">
            <span className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink-low">
              配置
            </span>
          </div>
          <div className="space-y-0.5 px-2">
            {NAV.slice(1, 3).map((n) => (
              <NavItem key={n.key} n={n} active={tab === n.key} onClick={() => setTab(n.key)} />
            ))}
          </div>

          <div className="mt-auto border-t border-line px-2 py-3">
            <button
              onClick={() => {
                clearToken()
                location.reload()
              }}
              className="w-full rounded-lg px-3 py-2 text-left text-[12px] text-ink-low transition-colors hover:bg-surface-hover hover:text-ink"
            >
              退出登录
            </button>
          </div>
        </nav>

        {/* ——— 主区 ——— */}
        <main className="stage-wash min-h-0 flex-1 overflow-auto">
          <div className="mx-auto max-w-[1400px] px-7 py-6">
            <div className="mb-5 flex items-end justify-between gap-4">
              <div>
                <h1 className="text-[22px] font-bold tracking-tight text-ink-hi">{active.label}</h1>
                <p className="mt-1 text-[12.5px] text-ink-mid">{active.desc}</p>
              </div>
            </div>

            {tab === 'dashboard' && <Dashboard />}
            {tab === 'channels' && <Channels />}
            {tab === 'keys' && <Keys />}
            {tab === 'livetail' && <LiveTail />}
            {tab === 'insights' && <Insights />}
            {tab === 'alerts' && <Alerts />}
            {tab === 'subscription' && <Subscription />}
          </div>
        </main>
      </div>
    </div>
  )
}

function NavItem({ n, active, onClick, badge }: {
  n: { label: string; icon: typeof Gauge }
  active: boolean
  onClick: () => void
  badge?: number
}) {
  const Icon = n.icon
  return (
    <button
      onClick={onClick}
      className={`relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] transition-all ${
        active
          ? 'nav-active font-medium text-ink-hi'
          : 'text-ink-mid hover:bg-surface-hover hover:text-ink'
      }`}
    >
      <Icon size={16} className={active ? 'text-brand' : ''} strokeWidth={active ? 2.2 : 1.8} />
      <span className="flex-1 truncate">{n.label}</span>
      {badge !== undefined && badge > 0 && (
        <span className="tnum rounded-full bg-alert px-1.5 py-[1px] text-[10px] font-semibold text-white">
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  )
}

function ConnectionPill() {
  const [ok, setOk] = useState(true)
  useEffect(() => {
    const check = () =>
      fetch('/healthz')
        .then((r) => setOk(r.ok))
        .catch(() => setOk(false))
    check()
    const timer = setInterval(check, 10000)
    return () => clearInterval(timer)
  }, [])
  return (
    <span className="mr-1 flex items-center gap-1.5 rounded-md bg-surface-sunken px-2 py-1 text-[11px] text-ink-mid">
      <Activity size={12} className={ok ? 'text-good' : 'text-alert'} />
      {ok ? '网关运行中' : '连接中断'}
    </span>
  )
}

function Login({ onSuccess }: { onSuccess: () => void }) {
  const [token, setTokenInput] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit() {
    setError('')
    setBusy(true)
    setToken(token.trim())
    try {
      await api.channels()
      onSuccess()
    } catch {
      clearToken()
      setError('token 无效,或网关未启动')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full flex-col bg-surface">
      <TitleBar />
      <div className="stage-wash flex flex-1 items-center justify-center px-6">
        <div className="card w-[400px] p-8">
          <div className="mb-6 flex items-center gap-3">
            <Logo size={40} />
            <div>
              <div className="text-[17px] font-bold tracking-tight text-ink-hi">LLM 网关控制台</div>
              <div className="text-[12px] text-ink-mid">数据面 + 控制面</div>
            </div>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              submit()
            }}
            className="space-y-3"
          >
            <label className="block text-[12.5px] font-medium text-ink">管理 token</label>
            <Input
              type="password"
              placeholder="GW_ADMIN_TOKEN"
              value={token}
              onChange={(e) => setTokenInput(e.target.value)}
              className="w-full"
              autoFocus
            />
            {error && <div className="text-[12px] text-alert">{error}</div>}
            <Button kind="primary" type="submit" disabled={!token.trim() || busy}>
              {busy ? '连接中…' : '连接网关'}
            </Button>
          </form>
          <p className="mt-5 border-t border-line-soft pt-4 text-[11.5px] leading-relaxed text-ink-low">
            单机版会自动填入 token。服务器部署时,token 即启动网关所用的{' '}
            <code className="rounded bg-surface-sunken px-1 py-0.5 text-ink-mid">GW_ADMIN_TOKEN</code>。
          </p>
        </div>
      </div>
    </div>
  )
}
