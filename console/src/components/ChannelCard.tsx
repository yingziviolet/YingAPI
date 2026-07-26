import { ChevronDown, ChevronUp, Power, RefreshCw, Trash2, Wallet, Zap } from 'lucide-react'
import type { BreakerState, Channel, ChannelBalance } from '../types'
import { Badge, Led } from './ui'

const breakerLabel: Record<string, string> = { closed: '正常', open: '熔断中', half_open: '半开探测' }

export default function ChannelCard({
  channel,
  breaker,
  balance,
  testResult,
  onToggle,
  onPriority,
  onTest,
  onRefreshBalance,
  onResetBreaker,
  onDelete,
}: {
  channel: Channel
  breaker?: BreakerState
  balance?: ChannelBalance
  testResult?: string
  onToggle: () => void
  onPriority: (delta: number) => void
  onTest: () => void
  onRefreshBalance: () => void
  onResetBreaker: () => void
  onDelete: () => void
}) {
  const state = breaker?.state ?? 'closed'
  const hasBalance = balance?.ok && balance.balance?.remaining != null
  const sym = balance?.balance?.currency === 'CNY' ? '¥' : '$'
  const total = balance?.balance?.total
  const remaining = balance?.balance?.remaining
  const usedRatio = total && remaining != null ? Math.max(0, 1 - remaining / total) : null
  const pct = usedRatio != null ? Math.round(usedRatio * 100) : null
  const barTone = pct != null && pct >= 90 ? 'bg-alert' : pct != null && pct >= 70 ? 'bg-warn' : 'grad-brand'

  return (
    <div className="card flex flex-col p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Led tone={channel.enabled ? 'good' : 'off'} pulse={channel.enabled} />
        <span className="text-[14px] font-semibold text-ink-hi">{channel.name}</span>
        <Badge kind={state}>{breakerLabel[state]}</Badge>
        <div className="ml-auto flex items-center gap-1 rounded-lg border border-line bg-surface-sunken px-1.5 py-0.5">
          <button className="rounded p-0.5 text-ink-low hover:text-brand" onClick={() => onPriority(-1)} title="提高优先级">
            <ChevronUp size={13} />
          </button>
          <span className="tnum w-5 text-center text-[12px] text-ink">{channel.priority}</span>
          <button className="rounded p-0.5 text-ink-low hover:text-brand" onClick={() => onPriority(1)} title="降低优先级">
            <ChevronDown size={13} />
          </button>
        </div>
      </div>

      <div className="mt-2 truncate text-[11.5px] text-ink-low" title={channel.base_url}>
        {channel.base_url}
      </div>

      <div className="mt-3 flex flex-wrap gap-1">
        {channel.models.map((m) => (
          <span key={m} className="rounded-md bg-surface-sunken px-2 py-0.5 text-[11.5px] text-ink-mid">
            {m}
          </span>
        ))}
        {channel.models.length === 0 && <span className="text-[11.5px] text-ink-low">未配置模型</span>}
      </div>

      {/* 余额 */}
      <div className="mt-4">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-mid">
            <Wallet size={14} />
            账户余额
          </span>
          <span className={`tnum text-[16px] font-bold ${hasBalance ? 'grad-text' : 'text-ink-low'}`}>
            {hasBalance ? `${sym}${Number(remaining).toFixed(2)}` : '未获取'}
          </span>
        </div>
        {pct != null && (
          <>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-sunken">
              <div className={`h-full rounded-full ${barTone} transition-[width] duration-500`} style={{ width: `${pct}%` }} />
            </div>
            <div className="tnum mt-1.5 text-right text-[11.5px] text-ink-low">
              已用 {pct}% · 总额 {sym}{Number(total).toFixed(2)}
            </div>
          </>
        )}
        {!hasBalance && balance && !balance.ok && (
          <div className="mt-1.5 text-[11px] text-ink-low">该渠道未提供公开余额接口,或需在编辑里指定</div>
        )}
      </div>

      {/* 熔断窗口 */}
      {breaker && breaker.window_requests > 0 && (
        <div
          className={`mt-3 rounded-lg border px-3 py-2.5 ${
            state === 'closed' ? 'border-good/25 bg-good/8' : 'border-alert/25 bg-alert/8'
          }`}
        >
          <div className="flex items-center justify-between text-[12px]">
            <span className={state === 'closed' ? 'font-medium text-good' : 'font-medium text-alert'}>
              窗口错误率 {(breaker.error_rate * 100).toFixed(0)}%
            </span>
            <span className="tnum text-ink-mid">
              {breaker.window_failures}/{breaker.window_requests} 失败
              {breaker.cooldown_remaining_s > 0 && ` · 冷却 ${breaker.cooldown_remaining_s}s`}
            </span>
          </div>
        </div>
      )}

      <div className="mt-auto pt-4">
        {testResult && <div className="mb-2 text-[11.5px] text-ink-mid">连通测试:{testResult}</div>}
        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <CardAction icon={<Zap size={15} />} label="连通性测试" onClick={onTest} />
          <CardAction icon={<Wallet size={15} />} label="刷新余额" onClick={onRefreshBalance} />
          {state !== 'closed' && (
            <CardAction icon={<RefreshCw size={15} />} label="复位熔断器" onClick={onResetBreaker} warn />
          )}
          <CardAction
            icon={<Power size={15} />}
            label={channel.enabled ? '停用渠道' : '启用渠道'}
            onClick={onToggle}
            active={!channel.enabled}
          />
          <CardAction icon={<Trash2 size={15} />} label="删除渠道" onClick={onDelete} danger />
        </div>
      </div>
    </div>
  )
}

function CardAction({ icon, label, onClick, danger, active, warn }: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  danger?: boolean
  active?: boolean
  warn?: boolean
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`flex h-9 flex-1 items-center justify-center rounded-lg border border-line bg-surface-sunken transition-all ${
        danger
          ? 'text-ink-low hover:border-alert/40 hover:bg-alert/10 hover:text-alert'
          : warn
            ? 'border-warn/35 bg-warn/10 text-warn hover:brightness-95'
            : active
              ? 'border-warn/35 bg-warn/10 text-warn'
              : 'text-ink-mid hover:border-brand-ring hover:bg-brand-soft hover:text-brand'
      }`}
    >
      {icon}
    </button>
  )
}
