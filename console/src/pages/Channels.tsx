import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, Database, Plus, Trash2, Wallet, Zap } from 'lucide-react'
import { api } from '../api'
import type { BreakerState, Channel, ChannelBalance } from '../types'
import ChannelCard from '../components/ChannelCard'
import ImportDialog from '../components/ImportDialog'
import { ViewToggle } from './Keys'
import { Badge, Button, Card, Empty, IconButton, Input, Led, Table, Td, Tr } from '../components/ui'

const breakerLabel: Record<string, string> = { closed: '正常', open: '熔断', half_open: '半开探测' }

export default function Channels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [breakers, setBreakers] = useState<Record<number, BreakerState>>({})
  const [balances, setBalances] = useState<Record<number, ChannelBalance>>({})
  const [view, setView] = useState<'grid' | 'list'>(
    () => (localStorage.getItem('gw_channels_view') as 'grid' | 'list') || 'grid',
  )
  const [showCreate, setShowCreate] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [testResult, setTestResult] = useState<Record<number, string>>({})
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const [chs, brs] = await Promise.all([api.channels(), api.breakers()])
    setChannels(chs)
    setBreakers(Object.fromEntries(brs.map((b) => [b.channel_id, b])))
  }, [])

  const loadBalances = useCallback(async () => {
    try {
      const list = await api.balances()
      setBalances(Object.fromEntries(list.map((b) => [b.channel_id, b])))
    } catch {
      /* 余额是附加信息,失败不打扰 */
    }
  }, [])

  useEffect(() => {
    load().catch(console.error)
    loadBalances()
    const timer = setInterval(() => load().catch(console.error), 5000)
    return () => clearInterval(timer)
  }, [load, loadBalances])

  async function toggle(ch: Channel) {
    await api.updateChannel(ch.id, { enabled: !ch.enabled })
    await load()
  }
  async function movePriority(ch: Channel, delta: number) {
    await api.updateChannel(ch.id, { priority: Math.max(1, ch.priority + delta) })
    await load()
  }
  async function test(ch: Channel) {
    setTestResult((r) => ({ ...r, [ch.id]: '测试中…' }))
    try {
      const res = await api.testChannel(ch.id)
      setTestResult((r) => ({
        ...r,
        [ch.id]: res.ok ? `连通 ${res.latency_ms}ms` : `失败 ${res.status_code ?? res.error}`,
      }))
    } catch (e) {
      setTestResult((r) => ({ ...r, [ch.id]: '失败' }))
    }
  }
  async function remove(ch: Channel) {
    if (!confirm(`删除渠道「${ch.name}」?计量历史保留,渠道配置不可恢复。`)) return
    await api.deleteChannel(ch.id)
    await load()
  }

  function balanceText(id: number) {
    const b = balances[id]
    if (!b) return null
    if (!b.ok || b.balance?.remaining == null) return <span className="text-ink-low">—</span>
    const sym = b.balance.currency === 'CNY' ? '¥' : '$'
    return (
      <span className="font-medium text-ink-hi">
        {sym}
        {Number(b.balance.remaining).toFixed(2)}
      </span>
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-4">
        <p className="text-[12.5px] text-ink-mid">
          静态优先级路由:数字越小越优先,同模型多渠道自动 failover;熔断渠道会被跳过
        </p>
        <div className="flex items-center gap-2">
          <ViewToggle
            view={view}
            onChange={(v) => {
              setView(v)
              localStorage.setItem('gw_channels_view', v)
            }}
          />
          <Button onClick={loadBalances}><Wallet size={14} />刷新余额</Button>
          <Button onClick={() => setShowImport(true)}><Database size={14} />批量导入</Button>
          <Button kind="primary" onClick={() => setShowCreate((v) => !v)}>
            <Plus size={14} />添加渠道
          </Button>
        </div>
      </div>

      {showImport && (
        <ImportDialog
          onClose={() => setShowImport(false)}
          onDone={() => {
            load().catch(console.error)
            loadBalances()
          }}
        />
      )}

      {showCreate && (
        <CreateChannelForm
          onDone={() => {
            setShowCreate(false)
            load().catch(console.error)
          }}
          onError={setError}
        />
      )}
      {error && (
        <div className="rounded-lg border border-alert/25 bg-alert/8 px-4 py-2.5 text-[12.5px] text-alert">
          {error}
        </div>
      )}

      {channels.length === 0 ? (
        <Card>
          <Empty
            text="还没有配置渠道"
            hint="添加一个你自己合法持有的 API key,网关就能开始转发"
            action={
              <div className="flex gap-2">
                <Button kind="primary" onClick={() => setShowCreate(true)}><Plus size={14} />添加渠道</Button>
                <Button onClick={() => setShowImport(true)}><Database size={14} />批量导入</Button>
              </div>
            }
          />
        </Card>
      ) : view === 'grid' ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {channels.map((ch) => (
            <ChannelCard
              key={ch.id}
              channel={ch}
              breaker={breakers[ch.id]}
              balance={balances[ch.id]}
              testResult={testResult[ch.id]}
              onToggle={() => toggle(ch)}
              onPriority={(d) => movePriority(ch, d)}
              onTest={() => test(ch)}
              onRefreshBalance={loadBalances}
              onResetBreaker={() => api.resetBreaker(ch.id).then(load)}
              onDelete={() => remove(ch)}
            />
          ))}
        </div>
      ) : (
        <Card pad={false}>
          <Table head={['', '渠道', '模型', '余额', '优先级', '熔断状态', '操作']}>
            {channels.map((ch) => {
              const br = breakers[ch.id]
              return (
                <Tr key={ch.id}>
                  <Td>
                    <button onClick={() => toggle(ch)} title={ch.enabled ? '点击停用' : '点击启用'}>
                      <Led tone={ch.enabled ? 'good' : 'off'} pulse={ch.enabled} />
                    </button>
                  </Td>
                  <Td>
                    <div className="font-medium text-ink-hi">{ch.name}</div>
                    <div className="text-[11px] text-ink-low">{ch.base_url}</div>
                  </Td>
                  <Td>
                    <div className="flex max-w-[240px] flex-wrap gap-1">
                      {ch.models.map((m) => (
                        <span key={m} className="rounded-md bg-surface-sunken px-1.5 py-0.5 text-[11px] text-ink-mid">
                          {m}
                        </span>
                      ))}
                    </div>
                  </Td>
                  <Td mono>{balanceText(ch.id)}</Td>
                  <Td>
                    <div className="flex items-center gap-1">
                      <button className="rounded p-0.5 text-ink-low hover:text-brand" onClick={() => movePriority(ch, -1)}>
                        <ChevronUp size={14} />
                      </button>
                      <span className="tnum w-6 text-center text-[13px]">{ch.priority}</span>
                      <button className="rounded p-0.5 text-ink-low hover:text-brand" onClick={() => movePriority(ch, 1)}>
                        <ChevronDown size={14} />
                      </button>
                    </div>
                  </Td>
                  <Td>
                    <div className="flex items-center gap-2">
                      <Badge kind={br?.state ?? 'closed'}>{breakerLabel[br?.state ?? 'closed']}</Badge>
                      {br && br.state !== 'closed' && (
                        <Button kind="ghost" onClick={() => api.resetBreaker(ch.id).then(load)}>复位</Button>
                      )}
                      {br && br.window_requests > 0 && br.state === 'closed' && (
                        <span className="tnum text-[11px] text-ink-low">
                          {(br.error_rate * 100).toFixed(0)}% 错误
                        </span>
                      )}
                    </div>
                  </Td>
                  <Td>
                    <div className="flex items-center gap-1.5">
                      <IconButton onClick={() => test(ch)} title="连通性测试"><Zap size={14} /></IconButton>
                      <IconButton onClick={() => remove(ch)} title="删除渠道" danger><Trash2 size={14} /></IconButton>
                      {testResult[ch.id] && (
                        <span className="text-[11px] text-ink-low">{testResult[ch.id]}</span>
                      )}
                    </div>
                  </Td>
                </Tr>
              )
            })}
          </Table>
        </Card>
      )}
    </div>
  )
}

function CreateChannelForm({ onDone, onError }: { onDone: () => void; onError: (e: string) => void }) {
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com/v1')
  const [apiKey, setApiKey] = useState('')
  const [models, setModels] = useState('')
  const [prices, setPrices] = useState('')
  const [priority, setPriority] = useState('10')
  const [balanceUrl, setBalanceUrl] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit() {
    onError('')
    setBusy(true)
    try {
      const modelList = models.split(',').map((s) => s.trim()).filter(Boolean)
      const priceTable: Record<string, { input: number; output: number }> = {}
      for (const part of prices.split(',').map((s) => s.trim()).filter(Boolean)) {
        const [m, pair] = part.split('=')
        const [inp, outp] = (pair ?? '').split('/')
        if (m && inp && outp) priceTable[m.trim()] = { input: Number(inp), output: Number(outp) }
      }
      await api.createChannel({
        name, base_url: baseUrl, api_key: apiKey, models: modelList,
        prices: priceTable, priority: Number(priority) || 10,
        balance_url: balanceUrl.trim() || null,
      })
      onDone()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="添加渠道" desc="API key 用 Fernet 加密落库,永不回显">
      <div className="grid gap-4 lg:grid-cols-2">
        <Field label="渠道名称" hint="控制台内的标识,如 deepseek">
          <Input placeholder="deepseek" value={name} onChange={(e) => setName(e.target.value)} className="w-full" />
        </Field>
        <Field label="Base URL" hint="OpenAI 兼容端点,通常以 /v1 结尾">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="w-full" />
        </Field>
        <Field label="API Key" hint="你自己合法持有的 key">
          <Input type="password" placeholder="sk-..." value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="w-full" />
        </Field>
        <Field label="模型列表" hint="逗号分隔,这些是对外暴露的模型名">
          <Input placeholder="deepseek-chat, deepseek-reasoner" value={models} onChange={(e) => setModels(e.target.value)} className="w-full" />
        </Field>
        <Field label="价格表" hint="格式 模型=输入价/输出价(美元每 1M token)">
          <Input placeholder="deepseek-chat=0.27/1.1" value={prices} onChange={(e) => setPrices(e.target.value)} className="w-full" />
        </Field>
        <Field label="优先级" hint="数字越小越优先被选中">
          <Input value={priority} onChange={(e) => setPriority(e.target.value)} className="w-full" />
        </Field>
        <Field label="余额接口(选填)" hint="留空则自动探测常见中转站与厂商的余额端点">
          <Input placeholder="https://中转站/api/user/balance" value={balanceUrl} onChange={(e) => setBalanceUrl(e.target.value)} className="w-full" />
        </Field>
      </div>
      <div className="mt-5 flex gap-2">
        <Button kind="primary" onClick={submit} disabled={!name || !apiKey || busy}>
          {busy ? '创建中…' : '创建渠道'}
        </Button>
      </div>
    </Card>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12.5px] font-medium text-ink">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-ink-low">{hint}</span>}
    </label>
  )
}
