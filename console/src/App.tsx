import { useEffect, useState } from 'react'
import { api, clearToken, getToken, setToken } from './api'
import Channels from './pages/Channels'
import Dashboard from './pages/Dashboard'
import Insights from './pages/Insights'
import Keys from './pages/Keys'
import LiveTail from './pages/LiveTail'
import { Button, Input } from './components/ui'

type Tab = 'dashboard' | 'channels' | 'keys' | 'livetail' | 'insights'

const tabs: { key: Tab; label: string; icon: string }[] = [
  { key: 'dashboard', label: '额度大盘', icon: '📊' },
  { key: 'channels', label: '渠道面板', icon: '🔀' },
  { key: 'keys', label: '虚拟 Key', icon: '🔑' },
  { key: 'livetail', label: '实时请求流', icon: '📡' },
  { key: 'insights', label: '智能层成绩单', icon: '🧠' },
]

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [tab, setTab] = useState<Tab>('dashboard')

  useEffect(() => {
    if (!getToken()) {
      setAuthed(false)
      return
    }
    api
      .channels()
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false))
  }, [])

  if (authed === null) {
    return <div className="flex h-screen items-center justify-center text-slate-500">连接网关…</div>
  }
  if (!authed) {
    return <Login onSuccess={() => setAuthed(true)} />
  }

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-52 flex-col border-r border-slate-800 bg-slate-900/40 p-4">
        <div className="mb-6 px-2">
          <div className="text-lg font-semibold text-slate-100">LLM 网关</div>
          <div className="text-xs text-slate-600">数据面 + 控制面</div>
        </div>
        <nav className="flex-1 space-y-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                tab === t.key
                  ? 'bg-cyan-600/15 text-cyan-300'
                  : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
              }`}
            >
              <span>{t.icon}</span>
              {t.label}
            </button>
          ))}
        </nav>
        <button
          onClick={() => {
            clearToken()
            location.reload()
          }}
          className="rounded-lg px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-800 hover:text-slate-400"
        >
          退出登录
        </button>
      </aside>
      <main className="flex-1 overflow-auto p-6">
        <h1 className="mb-4 text-xl font-semibold text-slate-100">
          {tabs.find((t) => t.key === tab)?.label}
        </h1>
        {tab === 'dashboard' && <Dashboard />}
        {tab === 'channels' && <Channels />}
        {tab === 'keys' && <Keys />}
        {tab === 'livetail' && <LiveTail />}
        {tab === 'insights' && <Insights />}
      </main>
    </div>
  )
}

function Login({ onSuccess }: { onSuccess: () => void }) {
  const [token, setTokenInput] = useState('')
  const [error, setError] = useState('')

  async function submit() {
    setError('')
    setToken(token.trim())
    try {
      await api.channels()
      onSuccess()
    } catch {
      clearToken()
      setError('token 无效或网关未启动')
    }
  }

  return (
    <div className="flex h-screen items-center justify-center">
      <div className="w-96 rounded-2xl border border-slate-800 bg-slate-900/60 p-8">
        <div className="mb-1 text-xl font-semibold text-slate-100">LLM 网关控制台</div>
        <div className="mb-6 text-sm text-slate-500">输入管理 token(GW_ADMIN_TOKEN)</div>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
          className="space-y-3"
        >
          <Input
            type="password"
            placeholder="管理 token"
            value={token}
            onChange={(e) => setTokenInput(e.target.value)}
            className="w-full"
            autoFocus
          />
          {error && <div className="text-xs text-rose-400">{error}</div>}
          <Button kind="primary" type="submit" disabled={!token.trim()}>
            连接
          </Button>
        </form>
      </div>
    </div>
  )
}
