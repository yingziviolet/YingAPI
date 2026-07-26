import { useCallback, useEffect, useState } from 'react'
import { Copy, KeyRound, Plus, Power, Trash2 } from 'lucide-react'
import { api, fmtUsd } from '../api'
import type { KeySpend, VirtualKey } from '../types'
import { Badge, Button, Card, Empty, IconButton, Input, Table, Td, Tr } from '../components/ui'

export default function Keys() {
  const [keys, setKeys] = useState<VirtualKey[]>([])
  const [spends, setSpends] = useState<Record<number, KeySpend>>({})
  const [newName, setNewName] = useState('')
  const [newBudget, setNewBudget] = useState('')
  const [newRpm, setNewRpm] = useState('')
  const [createdKey, setCreatedKey] = useState<{ name: string; key: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const ks = await api.keys()
    setKeys(ks)
    const rows = await Promise.all(ks.map((k) => api.keySpend(k.id)))
    setSpends(Object.fromEntries(rows.map((s) => [s.key_id, s])))
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
      setNewName(''); setNewBudget(''); setNewRpm('')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="space-y-5">
      <Card
        title="发放虚拟 Key"
        desc="每个客户端一把独立 key,配独立预算与限流——泄漏时能精确定位到具体 key"
      >
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-[12.5px] font-medium text-ink">名称</span>
            <Input placeholder="my-ide / 记账agent" value={newName} onChange={(e) => setNewName(e.target.value)} className="w-52" />
          </label>
          <label className="block">
            <span className="mb-1 block text-[12.5px] font-medium text-ink">月预算 (USD)</span>
            <Input placeholder="选填" className="w-32" value={newBudget} onChange={(e) => setNewBudget(e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1 block text-[12.5px] font-medium text-ink">每分钟上限</span>
            <Input placeholder="选填" className="w-32" value={newRpm} onChange={(e) => setNewRpm(e.target.value)} />
          </label>
          <Button kind="primary" onClick={create} disabled={!newName}>
            <Plus size={14} />发放
          </Button>
        </div>
        {error && <div className="mt-3 text-[12px] text-alert">{error}</div>}

        {createdKey && (
          <div className="mt-4 rounded-lg border border-brand-ring bg-brand-soft p-4">
            <div className="flex items-center gap-2 text-[12.5px] font-medium text-brand">
              <KeyRound size={14} />
              「{createdKey.name}」创建成功 —— 原文只显示这一次,请立即保存
            </div>
            <div className="mt-2.5 flex items-center gap-2">
              <code className="flex-1 select-all break-all rounded-md border border-line bg-surface-card px-3 py-2 text-[12px] text-ink-hi">
                {createdKey.key}
              </code>
              <Button
                onClick={() => {
                  navigator.clipboard.writeText(createdKey.key)
                  setCopied(true)
                  setTimeout(() => setCopied(false), 1600)
                }}
              >
                <Copy size={14} />{copied ? '已复制' : '复制'}
              </Button>
              <Button kind="ghost" onClick={() => setCreatedKey(null)}>知道了</Button>
            </div>
          </div>
        )}
      </Card>

      <Card pad={false}>
        {keys.length === 0 ? (
          <Empty text="还没有虚拟 Key" hint="发一把给你的 IDE 或脚本,流量就能进网关了" />
        ) : (
          <Table head={['状态', '名称', 'Key', '本月花费', '预算', '限流', '操作']}>
            {keys.map((k) => (
              <Tr key={k.id}>
                <Td><Badge kind={k.enabled ? 'ok' : 'neutral'}>{k.enabled ? '启用' : '停用'}</Badge></Td>
                <Td className="font-medium text-ink-hi">{k.name}</Td>
                <Td><code className="text-[11.5px] text-ink-low">{k.key_masked}</code></Td>
                <Td mono>{fmtUsd(spends[k.id]?.month_to_date_usd)}</Td>
                <Td mono className="text-ink-mid">
                  {k.monthly_budget_usd != null ? fmtUsd(k.monthly_budget_usd) : '不限'}
                </Td>
                <Td mono className="text-ink-mid">
                  {k.rpm_limit != null ? (k.rpm_limit === 0 ? '不限' : `${k.rpm_limit}/min`) : '默认'}
                </Td>
                <Td>
                  <div className="flex gap-1.5">
                    <IconButton
                      title={k.enabled ? '停用' : '启用'}
                      onClick={() => api.updateKey(k.id, { enabled: !k.enabled }).then(load)}
                    >
                      <Power size={14} />
                    </IconButton>
                    <IconButton
                      title="删除"
                      danger
                      onClick={() => {
                        if (confirm(`删除 key「${k.name}」?使用它的客户端将立即失效。`))
                          api.deleteKey(k.id).then(load)
                      }}
                    >
                      <Trash2 size={14} />
                    </IconButton>
                  </div>
                </Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
