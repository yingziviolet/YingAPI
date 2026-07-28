import { useCallback, useEffect, useState } from 'react'
import {
  Copy,
  HelpCircle,
  KeyRound,
  LayoutGrid,
  List,
  Plus,
  Power,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { api, fmtUsd } from '../api'
import type { KeySpend, VirtualKey } from '../types'
import KeyCard from '../components/KeyCard'
import { Badge, Button, Card, Empty, IconButton, Input, Table, Td, Tr } from '../components/ui'

type View = 'grid' | 'list'

export default function Keys() {
  const [keys, setKeys] = useState<VirtualKey[]>([])
  const [spends, setSpends] = useState<Record<number, KeySpend>>({})
  const [view, setView] = useState<View>(
    () => (localStorage.getItem('gw_keys_view') as View) || 'grid',
  )
  const [showCreate, setShowCreate] = useState(false)
  const [showWhat, setShowWhat] = useState(false)
  const [newName, setNewName] = useState('')
  const [newNote, setNewNote] = useState('')
  const [newBudget, setNewBudget] = useState('')
  const [newRpm, setNewRpm] = useState('')
  const [revealed, setRevealed] = useState<{ name: string; key: string; rotated?: boolean } | null>(null)
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

  function switchView(v: View) {
    setView(v)
    localStorage.setItem('gw_keys_view', v)
  }

  async function create() {
    setError('')
    try {
      const body: Record<string, unknown> = { name: newName }
      if (newNote.trim()) body.note = newNote.trim()
      if (newBudget) body.monthly_budget_usd = Number(newBudget)
      if (newRpm) body.rpm_limit = Number(newRpm)
      const created = await api.createKey(body)
      setRevealed({ name: created.name, key: created.key })
      setNewName(''); setNewNote(''); setNewBudget(''); setNewRpm('')
      setShowCreate(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function rotate(k: VirtualKey) {
    if (!confirm(`轮换「${k.name}」的 key?\n\n旧 key 立即失效,用它的客户端需要换成新 key。\n预算与计量历史保留。`)) return
    const res = await api.rotateKey(k.id)
    setRevealed({ name: res.name, key: res.key, rotated: true })
    await load()
  }

  async function editNote(k: VirtualKey) {
    const note = prompt(`「${k.name}」的备注(留空清除):`, k.note ?? '')
    if (note === null) return
    await api.updateKey(k.id, { note: note.trim() || null })
    await load()
  }

  async function editBudget(k: VirtualKey) {
    const budget = prompt(
      `「${k.name}」月度预算(美元,留空=不限额):`,
      k.monthly_budget_usd != null ? String(k.monthly_budget_usd) : '',
    )
    if (budget === null) return
    const rpm = prompt(`每分钟请求上限(0=不限速,留空=用全局默认):`, k.rpm_limit != null ? String(k.rpm_limit) : '')
    if (rpm === null) return
    await api.updateKey(k.id, {
      monthly_budget_usd: budget.trim() ? Number(budget) : null,
      rpm_limit: rpm.trim() ? Number(rpm) : null,
    })
    await load()
  }

  async function remove(k: VirtualKey) {
    if (!confirm(`删除 key「${k.name}」?使用它的客户端将立即失效。`)) return
    await api.deleteKey(k.id)
    await load()
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12.5px] text-ink-mid">
          发给客户端使用的替身 key,你的真 key 永远不出网关
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowWhat((v) => !v)}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-[12px] text-ink-mid transition-colors hover:bg-surface-hover hover:text-ink-hi"
          >
            <HelpCircle size={14} />
            这是干什么的?
          </button>
          <ViewToggle view={view} onChange={switchView} />
          <Button kind="primary" onClick={() => setShowCreate((v) => !v)}>
            <Plus size={14} />发放 Key
          </Button>
        </div>
      </div>

      {showWhat && <WhatIsVirtualKey onClose={() => setShowWhat(false)} />}

      {showCreate && (
        <Card title="发放虚拟 Key" desc="原文只在创建时显示一次,只存哈希">
          <div className="flex flex-wrap items-end gap-3">
            <Labeled label="名称">
              <Input placeholder="my-ide / 记账agent" value={newName} onChange={(e) => setNewName(e.target.value)} className="w-48" />
            </Labeled>
            <Labeled label="备注(选填)">
              <Input placeholder="发给谁 / 用在哪" value={newNote} onChange={(e) => setNewNote(e.target.value)} className="w-56" />
            </Labeled>
            <Labeled label="月预算 (USD)">
              <Input placeholder="不限" className="w-28" value={newBudget} onChange={(e) => setNewBudget(e.target.value)} />
            </Labeled>
            <Labeled label="每分钟上限">
              <Input placeholder="默认" className="w-28" value={newRpm} onChange={(e) => setNewRpm(e.target.value)} />
            </Labeled>
            <Button kind="primary" onClick={create} disabled={!newName}><Plus size={14} />发放</Button>
          </div>
          {error && <div className="mt-3 text-[12px] text-alert">{error}</div>}
        </Card>
      )}

      {revealed && (
        <div className="card border-brand-ring bg-brand-soft p-4">
          <div className="flex items-center gap-2 text-[12.5px] font-medium text-brand">
            <KeyRound size={14} />
            「{revealed.name}」{revealed.rotated ? '已轮换' : '创建成功'} —— 原文只显示这一次,请立即保存
          </div>
          <div className="mt-2.5 flex items-center gap-2">
            <code className="flex-1 select-all break-all rounded-md border border-line bg-surface-card px-3 py-2 text-[12px] text-ink-hi">
              {revealed.key}
            </code>
            <Button
              onClick={() => {
                navigator.clipboard.writeText(revealed.key)
                setCopied(true)
                setTimeout(() => setCopied(false), 1600)
              }}
            >
              <Copy size={14} />{copied ? '已复制' : '复制'}
            </Button>
            <Button kind="ghost" onClick={() => setRevealed(null)}>知道了</Button>
          </div>
        </div>
      )}

      {keys.length === 0 ? (
        <Card>
          <Empty
            text="还没有虚拟 Key"
            hint="发一把给你的 IDE 或脚本,流量就能进网关了"
            action={<Button kind="primary" onClick={() => setShowCreate(true)}><Plus size={14} />发放 Key</Button>}
          />
        </Card>
      ) : view === 'grid' ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {keys.map((k) => (
            <KeyCard
              key={k.id}
              vkey={k}
              spend={spends[k.id]}
              onToggle={() => api.updateKey(k.id, { enabled: !k.enabled }).then(load)}
              onRotate={() => rotate(k)}
              onNote={() => editNote(k)}
              onBudget={() => editBudget(k)}
              onLogs={() => alert(`「${k.name}」的请求记录请到「实时请求流」页查看`)}
              onDelete={() => remove(k)}
            />
          ))}
        </div>
      ) : (
        <Card pad={false}>
          <Table head={['状态', '名称', 'Key', '本月花费', '预算', '限流', '轮换', '操作']}>
            {keys.map((k) => (
              <Tr key={k.id}>
                <Td><Badge kind={k.enabled ? 'ok' : 'neutral'}>{k.enabled ? '启用' : '停用'}</Badge></Td>
                <Td className="font-medium text-ink-hi">
                  {k.name}
                  {k.note && <div className="text-[11px] font-normal text-ink-low">{k.note}</div>}
                </Td>
                <Td><code className="text-[11.5px] text-ink-low">{k.key_masked}</code></Td>
                <Td mono>{fmtUsd(spends[k.id]?.month_to_date_usd)}</Td>
                <Td mono className="text-ink-mid">{k.monthly_budget_usd != null ? fmtUsd(k.monthly_budget_usd) : '不限'}</Td>
                <Td mono className="text-ink-mid">
                  {k.rpm_limit != null ? (k.rpm_limit === 0 ? '不限' : `${k.rpm_limit}/min`) : '默认'}
                </Td>
                <Td mono className="text-ink-mid">{k.rotated_count || '—'}</Td>
                <Td>
                  <div className="flex gap-1.5">
                    <IconButton title="轮换 key" onClick={() => rotate(k)}><KeyRound size={14} /></IconButton>
                    <IconButton title={k.enabled ? '停用' : '启用'} onClick={() => api.updateKey(k.id, { enabled: !k.enabled }).then(load)}>
                      <Power size={14} />
                    </IconButton>
                    <IconButton title="删除" danger onClick={() => remove(k)}><Trash2 size={14} /></IconButton>
                  </div>
                </Td>
              </Tr>
            ))}
          </Table>
        </Card>
      )}
    </div>
  )
}

function WhatIsVirtualKey({ onClose }: { onClose: () => void }) {
  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck size={17} className="text-brand" />
            <h3 className="text-[14px] font-semibold text-ink-hi">虚拟 Key 是做什么的</h3>
          </div>

          <div className="mb-4 rounded-lg border border-line bg-surface-sunken p-4">
            <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
              <Box label="你的客户端" sub="IDE / 脚本 / Claude Code" />
              <Arrow text="虚拟 key" tone="brand" />
              <Box label="Ying 网关" sub="真 key 加密存这里" highlight />
              <Arrow text="真实 key" tone="mid" />
              <Box label="上游厂商" sub="DeepSeek / Kimi …" />
            </div>
          </div>

          <ul className="space-y-2.5 text-[12.5px] leading-relaxed text-ink">
            <Item title="真 key 不外泄">
              客户端只拿到 <code className="rounded bg-surface-sunken px-1">sk-gw-</code> 开头的替身。
              给同事、给某个不太放心的程序用,都不用交出真 key。
            </Item>
            <Item title="花的钱能分清是谁">
              每个客户端一把 key,大盘上就能看出 IDE 花了多少、某个 agent 花了多少。
            </Item>
            <Item title="逐把限额限速">
              给实验性脚本配 5 美元月预算和每分钟 10 次,跑飞了也烧不穿。
            </Item>
            <Item title="泄漏了只换一把">
              某把 key 泄漏,点「轮换」立刻作废重发——真 key 不用动,其他客户端照常工作。
            </Item>
          </ul>
        </div>
        <button onClick={onClose} className="rounded-lg p-1.5 text-ink-mid hover:bg-surface-hover hover:text-ink-hi">
          <X size={16} />
        </button>
      </div>
    </Card>
  )
}

function Box({ label, sub, highlight }: { label: string; sub: string; highlight?: boolean }) {
  return (
    <div
      className={`rounded-lg border px-3 py-2 ${
        highlight ? 'border-brand-ring bg-brand-soft' : 'border-line bg-surface-card'
      }`}
    >
      <div className={`font-medium ${highlight ? 'text-brand' : 'text-ink-hi'}`}>{label}</div>
      <div className="text-[10.5px] text-ink-low">{sub}</div>
    </div>
  )
}

function Arrow({ text, tone }: { text: string; tone: 'brand' | 'mid' }) {
  return (
    <div className="flex flex-col items-center px-1">
      <span className={`text-[10.5px] ${tone === 'brand' ? 'text-brand' : 'text-ink-low'}`}>{text}</span>
      <span className={tone === 'brand' ? 'text-brand' : 'text-ink-low'}>→</span>
    </div>
  )
}

function Item({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span className="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-brand" />
      <span>
        <b className="text-ink-hi">{title}</b>
        <span className="text-ink-mid"> —— {children}</span>
      </span>
    </li>
  )
}

export function ViewToggle({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-sunken p-0.5">
      {([
        { v: 'grid' as View, icon: <LayoutGrid size={15} />, label: '卡片视图' },
        { v: 'list' as View, icon: <List size={15} />, label: '列表视图' },
      ]).map((o) => (
        <button
          key={o.v}
          onClick={() => onChange(o.v)}
          title={o.label}
          className={`rounded-md px-2.5 py-1.5 transition-all ${
            view === o.v ? 'bg-surface-card text-brand shadow-sm' : 'text-ink-mid hover:text-ink-hi'
          }`}
        >
          {o.icon}
        </button>
      ))}
    </div>
  )
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12.5px] font-medium text-ink">{label}</span>
      {children}
    </label>
  )
}
