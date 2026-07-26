import { useCallback, useEffect, useState } from 'react'
import { api, fmtUsd } from '../api'
import type { KeySpend, VirtualKey } from '../types'
import { Badge, Button, Card, Empty, Input, Td, Th } from '../components/ui'

export default function Keys() {
  const [keys, setKeys] = useState<VirtualKey[]>([])
  const [spends, setSpends] = useState<Record<number, KeySpend>>({})
  const [newName, setNewName] = useState('')
  const [newBudget, setNewBudget] = useState('')
  const [newRpm, setNewRpm] = useState('')
  const [createdKey, setCreatedKey] = useState<{ name: string; key: string } | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const ks = await api.keys()
    setKeys(ks)
    const spendRows = await Promise.all(ks.map((k) => api.keySpend(k.id)))
    setSpends(Object.fromEntries(spendRows.map((s) => [s.key_id, s])))
  }, [])

  useEffect(() => {
    load().catch(console.error)
  }, [load])

  async function create() {
    setError('')
    try {
      const body: Record<string, unknown> = { name: newName }
      if (newBudget) body.monthly_budget_usd = Number(newBudget)
      if (newRpm) body.rpm_limit = Number(newRpm)
      const created = await api.createKey(body)
      setCreatedKey({ name: created.name, key: created.key })
      setNewName('')
      setNewBudget('')
      setNewRpm('')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function toggle(k: VirtualKey) {
    await api.updateKey(k.id, { enabled: !k.enabled })
    await load()
  }

  async function remove(k: VirtualKey) {
    if (!confirm(`删除 key ${k.name}?使用它的客户端将立即失效。`)) return
    await api.deleteKey(k.id)
    await load()
  }

  return (
    <div className="space-y-4">
      <Card title="发放虚拟 key(每个客户端独立 key + 独立预算/限流,泄漏可定位)">
        <div className="flex flex-wrap items-center gap-3">
          <Input placeholder="名称,如 my-ide / 记账agent" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Input placeholder="月预算 USD(可空)" className="w-36" value={newBudget} onChange={(e) => setNewBudget(e.target.value)} />
          <Input placeholder="每分钟请求上限(可空)" className="w-40" value={newRpm} onChange={(e) => setNewRpm(e.target.value)} />
          <Button kind="primary" onClick={create} disabled={!newName}>发放</Button>
        </div>
        {error && <div className="mt-2 text-xs text-rose-400">{error}</div>}
        {createdKey && (
          <div className="mt-3 rounded-lg border border-cyan-800 bg-cyan-500/10 p-3">
            <div className="text-xs text-cyan-300">
              key「{createdKey.name}」已创建——原文只显示这一次,请立即保存:
            </div>
            <div className="mt-1 flex items-center gap-2">
              <code className="select-all break-all rounded bg-slate-900 px-2 py-1 text-xs text-cyan-200">
                {createdKey.key}
              </code>
              <Button onClick={() => navigator.clipboard.writeText(createdKey.key)}>复制</Button>
              <Button kind="ghost" onClick={() => setCreatedKey(null)}>知道了</Button>
            </div>
          </div>
        )}
      </Card>

      <Card>
        {keys.length === 0 ? (
          <Empty text="还没有虚拟 key" />
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <Th>状态</Th><Th>名称</Th><Th>key</Th><Th>本月花费</Th><Th>预算</Th><Th>限流</Th><Th>操作</Th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className="border-b border-slate-800/50">
                  <Td>
                    <Badge kind={k.enabled ? 'ok' : 'neutral'}>{k.enabled ? '启用' : '停用'}</Badge>
                  </Td>
                  <Td className="text-slate-200">{k.name}</Td>
                  <Td><code className="text-xs text-slate-500">{k.key_masked}</code></Td>
                  <Td>{fmtUsd(spends[k.id]?.month_to_date_usd)}</Td>
                  <Td>{k.monthly_budget_usd != null ? fmtUsd(k.monthly_budget_usd) : '不限'}</Td>
                  <Td>{k.rpm_limit != null ? `${k.rpm_limit}/min` : '不限'}</Td>
                  <Td>
                    <div className="flex gap-2">
                      <Button onClick={() => toggle(k)}>{k.enabled ? '停用' : '启用'}</Button>
                      <Button kind="danger" onClick={() => remove(k)}>删除</Button>
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
