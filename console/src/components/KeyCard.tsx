import { useEffect, useState } from 'react'
import {
  Copy,
  FileText,
  Gauge,
  KeyRound,
  Power,
  RotateCw,
  ScrollText,
  Trash2,
} from 'lucide-react'
import { fmtUsd } from '../api'
import type { KeySpend, VirtualKey } from '../types'
import { Led } from './ui'

/** 距下个自然月开始的倒计时(预算重置时刻) */
function useMonthlyReset() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(timer)
  }, [])
  const d = new Date(now)
  const next = new Date(d.getFullYear(), d.getMonth() + 1, 1, 0, 0, 0, 0)
  const ms = next.getTime() - now
  const days = Math.floor(ms / 86400000)
  const hours = Math.floor((ms % 86400000) / 3600000)
  const mins = Math.floor((ms % 3600000) / 60000)
  return {
    text: `${days}d ${hours}h ${mins}m`,
    resetAt: `${String(next.getMonth() + 1).padStart(2, '0')}/${String(next.getDate()).padStart(2, '0')} 00:00`,
  }
}

export default function KeyCard({
  vkey,
  spend,
  onToggle,
  onRotate,
  onNote,
  onDelete,
  onLogs,
  onBudget,
}: {
  vkey: VirtualKey
  spend?: KeySpend
  onToggle: () => void
  onRotate: () => void
  onNote: () => void
  onDelete: () => void
  onLogs: () => void
  onBudget: () => void
}) {
  const reset = useMonthlyReset()
  const used = spend?.month_to_date_usd ?? 0
  const budget = vkey.monthly_budget_usd
  const ratio = budget && budget > 0 ? Math.min(1, used / budget) : 0
  const pct = Math.round(ratio * 100)
  const barTone = pct >= 90 ? 'bg-alert' : pct >= 70 ? 'bg-warn' : 'grad-brand'
  const pctTone = pct >= 90 ? 'text-alert' : pct >= 70 ? 'text-warn' : 'text-good'

  return (
    <div className="card flex flex-col p-5">
      {/* 标题行:名称 + 备注/轮换 */}
      <div className="flex flex-wrap items-center gap-2">
        <Led tone={vkey.enabled ? 'good' : 'off'} pulse={vkey.enabled} />
        <span className="text-[14px] font-semibold text-ink-hi">{vkey.name}</span>
        <button
          onClick={onNote}
          className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface-card px-2.5 py-1 text-[12px] text-ink-mid transition-colors hover:bg-surface-hover hover:text-ink-hi"
        >
          <FileText size={13} />
          {vkey.note ? '改备注' : '加备注'}
        </button>
        <button
          onClick={onRotate}
          title="轮换 key:旧 key 立即失效,预算与历史保留"
          className="inline-flex items-center gap-1 rounded-lg border border-brand-ring bg-brand-soft px-2.5 py-1 text-[12px] font-medium text-brand transition-colors hover:brightness-95"
        >
          <RotateCw size={13} />
          轮换 {vkey.rotated_count > 0 && <span className="tnum">{vkey.rotated_count}</span>}
        </button>
      </div>

      {/* 副行:备注 / key 掩码 */}
      <div className="mt-2 text-[12px] text-ink-mid">
        {vkey.note && <span className="text-ink">{vkey.note} · </span>}
        <span className="text-ink-low">Key: </span>
        <code className="text-[11.5px] text-ink-mid">{vkey.key_masked}</code>
      </div>

      {/* 预算进度 */}
      <div className="mt-4">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-mid">
            <Gauge size={14} />
            月度预算
          </span>
          <span className={`tnum text-[16px] font-bold ${budget ? pctTone : 'text-ink-low'}`}>
            {budget ? `${pct}%` : '不限额'}
          </span>
        </div>
        <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-sunken">
          <div
            className={`h-full rounded-full ${barTone} transition-[width] duration-500`}
            style={{ width: `${budget ? pct : 0}%` }}
          />
        </div>
        <div className="tnum mt-1.5 text-right text-[11.5px] text-ink-low">
          {fmtUsd(used)}
          {budget != null && ` / ${fmtUsd(budget)}`}
          {' · '}
          {reset.text} 后重置 ({reset.resetAt})
        </div>
      </div>

      {/* 限流状态块 */}
      <div className="mt-3 rounded-lg border border-good/25 bg-good/8 px-3 py-2.5">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-good">
            <KeyRound size={14} />
            {vkey.enabled ? '正常服务中' : '已停用'}
          </span>
          <span className="tnum text-[12px] text-ink-mid">
            {vkey.rpm_limit != null
              ? vkey.rpm_limit === 0
                ? '不限速'
                : `${vkey.rpm_limit} 次/分`
              : '默认限速'}
          </span>
        </div>
      </div>

      {/* 底部:创建时间 + 操作图标栏 */}
      <div className="mt-auto pt-4">
        <div className="tnum text-[11.5px] text-ink-low">
          {new Date(vkey.created_at).toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit',
          })}
        </div>
        <div className="mt-2.5 flex items-center gap-1.5 border-t border-line-soft pt-3">
          <CardAction icon={<Copy size={15} />} label="复制 Key 掩码"
            onClick={() => navigator.clipboard.writeText(vkey.key_masked)} />
          <CardAction icon={<ScrollText size={15} />} label="查看该 key 的请求日志" onClick={onLogs} />
          <CardAction icon={<Gauge size={15} />} label="调整预算与限速" onClick={onBudget} />
          <CardAction icon={<RotateCw size={15} />} label="轮换 key" onClick={onRotate} />
          <CardAction
            icon={<Power size={15} />}
            label={vkey.enabled ? '停用' : '启用'}
            onClick={onToggle}
            active={!vkey.enabled}
          />
          <CardAction icon={<Trash2 size={15} />} label="删除" onClick={onDelete} danger />
        </div>
      </div>
    </div>
  )
}

function CardAction({ icon, label, onClick, danger, active }: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  danger?: boolean
  active?: boolean
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`flex h-9 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken transition-all ${
        danger
          ? 'text-ink-low hover:border-alert/40 hover:bg-alert/10 hover:text-alert'
          : active
            ? 'border-warn/35 bg-warn/10 text-warn'
            : 'text-ink-mid hover:border-brand-ring hover:bg-brand-soft hover:text-brand'
      }`}
    >
      {icon}
    </button>
  )
}
