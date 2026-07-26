import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { BreakerState, Channel } from '../types'
import { Badge, Button, Card, Empty, Input, Td, Th } from '../components/ui'

const breakerLabel: Record<string, string> = {
  closed: '正常',
  open: '熔断',
  half_open: '半开探测',
}

export default function Channels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [breakers, setBreakers] = useState<Record<number, BreakerState>>({})
  const [showCreate, setShowCreate] = useState(false)
  const [testResult, setTestResult] = useState<Record<number, string>>({})
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const [chs, brs] = await Promise.all([api.channels(), api.breakers()])
    setChannels(chs)
    setBreakers(Object.fromEntries(brs.map((b) => [b.channel_id, b])))
  }, [])

  useEffect(() => {
    load().catch(console.error)
    const timer = setInterval(() => load().catch(console.error), 5000)
    return () => clearInterval(timer)
  }, [load])

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
        [ch.id]: res.ok ? `✓ ${res.latency_ms}ms` : `✗ ${res.status_code ?? res.error}`,
      }))
    } catch (e) {
      setTestResult((r) => ({ ...r, [ch.id]: `✗ ${String(e)}` }))
    }
  }

  async function resetBreaker(channelId: number) {
    await api.resetBreaker(channelId)
    await load()
  }

  async function remove(ch: Channel) {
    if (!confirm(`删除渠道 ${ch.name}?计量历史保留,渠道配置不可恢复。`)) return
    await api.deleteChannel(ch.id)
    await load()
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-slate-500">
          静态优先级路由:数字越小越优先,同模型多渠道自动 failover
        </div>
        <Button kind="primary" onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? '收起' : '+ 添加渠道'}
        </Button>
      </div>

      {showCreate && (
        <CreateChannelForm
          onDone={() => {
            setShowCreate(false)
            load().catch(console.error)
          }}
          onError={setError}
        />
      )}
      {error && <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-400">{error}</div>}

      <Card>
        {channels.length === 0 ? (
          <Empty text="还没有渠道——添加一个你自己合法持有的 API key" />
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <Th>状态</Th><Th>名称</Th><Th>模型</Th><Th>优先级</Th><Th>熔断</Th><Th>操作</Th>
              </tr>
            </thead>
            <tbody>
              {channels.map((ch) => {
                const br = breakers[ch.id]
                return (
                  <tr key={ch.id} className="border-b border-slate-800/50">
                    <Td>
                      <button
                        onClick={() => toggle(ch)}
                        title={ch.enabled ? '点击停用' : '点击启用'}
                        className={`h-3 w-3 rounded-full ${
                          ch.enabled ? 'bg-emerald-400' : 'bg-slate-600'
                        }`}
                      />
                    </Td>
                    <Td className="text-slate-200">
                      <div>{ch.name}</div>
                      <div className="text-xs text-slate-600">{ch.base_url}</div>
                    </Td>
                    <Td>
                      <div className="flex max-w-64 flex-wrap gap-1">
                        {ch.models.map((m) => (
                          <span key={m} className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400">
                            {m}
                          </span>
                        ))}
                      </div>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-1">
                        <button className="text-slate-500 hover:text-slate-300" onClick={() => movePriority(ch, -1)}>▲</button>
                        <span className="w-8 text-center tabular-nums">{ch.priority}</span>
                        <button className="text-slate-500 hover:text-slate-300" onClick={() => movePriority(ch, 1)}>▼</button>
                      </div>
                    </Td>
                    <Td>
                      {br ? (
                        <div className="flex items-center gap-2">
                          <Badge kind={br.state}>{breakerLabel[br.state]}</Badge>
                          {br.state !== 'closed' && (
                            <Button kind="ghost" onClick={() => resetBreaker(ch.id)}>复位</Button>
                          )}
                          {br.window_requests > 0 && (
                            <span className="text-xs text-slate-600">
                              {(br.error_rate * 100).toFixed(0)}% 错误
                            </span>
                          )}
                        </div>
                      ) : (
                        <Badge kind="closed">正常</Badge>
                      )}
                    </Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <Button onClick={() => test(ch)}>测试</Button>
                        {testResult[ch.id] && (
                          <span className="text-xs text-slate-500">{testResult[ch.id]}</span>
                        )}
                        <Button kind="danger" onClick={() => remove(ch)}>删除</Button>
                      </div>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Card>
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

  async function submit() {
    onError('')
    try {
      const modelList = models.split(',').map((s) => s.trim()).filter(Boolean)
      const priceTable: Record<string, { input: number; output: number }> = {}
      // 价格格式:model=in/out,逗号分隔,如 deepseek-chat=0.27/1.1
      for (const part of prices.split(',').map((s) => s.trim()).filter(Boolean)) {
        const [m, pair] = part.split('=')
        const [inp, outp] = (pair ?? '').split('/')
        if (m && inp && outp) priceTable[m.trim()] = { input: Number(inp), output: Number(outp) }
      }
      await api.createChannel({
        name, base_url: baseUrl, api_key: apiKey, models: modelList,
        prices: priceTable, priority: Number(priority) || 10,
      })
      onDone()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Card title="添加渠道(key 加密存储,永不回显)">
      <div className="grid gap-3 lg:grid-cols-2">
        <Input placeholder="名称,如 deepseek" value={name} onChange={(e) => setName(e.target.value)} />
        <Input placeholder="Base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        <Input placeholder="API Key(自己合法持有的)" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        <Input placeholder="模型列表,逗号分隔:deepseek-chat, deepseek-reasoner" value={models} onChange={(e) => setModels(e.target.value)} />
        <Input placeholder="价格($/1M):deepseek-chat=0.27/1.1" value={prices} onChange={(e) => setPrices(e.target.value)} />
        <Input placeholder="优先级(小=优先)" value={priority} onChange={(e) => setPriority(e.target.value)} />
      </div>
      <div className="mt-3">
        <Button kind="primary" onClick={submit}>创建</Button>
      </div>
    </Card>
  )
}
