import type { ReactNode } from 'react'

/* 基础件:白底卡片 + 品牌渐变点缀,风格对齐现代桌面工具 */

export function Card({ title, desc, extra, children, className = '', pad = true, delay }: {
  title?: ReactNode
  desc?: ReactNode
  extra?: ReactNode
  children: ReactNode
  className?: string
  pad?: boolean
  delay?: number
}) {
  return (
    <section
      className={`card ${delay !== undefined ? 'rise' : ''} ${className}`}
      style={delay !== undefined ? { animationDelay: `${delay}ms` } : undefined}
    >
      {(title || extra) && (
        <header className="flex items-center justify-between gap-3 border-b border-line-soft px-5 py-3">
          <div>
            <h2 className="text-[14px] font-semibold tracking-tight text-ink-hi">{title}</h2>
            {desc && <p className="mt-0.5 text-[12px] text-ink-mid">{desc}</p>}
          </div>
          <div className="flex items-center gap-2">{extra}</div>
        </header>
      )}
      <div className={pad ? 'p-5' : ''}>{children}</div>
    </section>
  )
}

export function StatCard({ label, value, sub, tone = 'brand', icon, delay }: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'brand' | 'neutral' | 'warn' | 'alert' | 'good'
  icon?: ReactNode
  delay?: number
}) {
  const toneClass = {
    brand: 'grad-text',
    neutral: 'text-ink-hi',
    warn: 'text-warn',
    alert: 'text-alert',
    good: 'text-good',
  }[tone]
  return (
    <div
      className={`card px-5 py-4 ${delay !== undefined ? 'rise' : ''}`}
      style={delay !== undefined ? { animationDelay: `${delay}ms` } : undefined}
    >
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-ink-mid">{label}</span>
        {icon && <span className="text-ink-low">{icon}</span>}
      </div>
      <div className={`tnum mt-2 text-[28px] leading-none font-bold ${toneClass}`}>{value}</div>
      {sub && <div className="mt-2 text-[11.5px] leading-relaxed text-ink-low">{sub}</div>}
    </div>
  )
}

const tones: Record<string, { text: string; bg: string }> = {
  ok:        { text: 'text-good',  bg: 'bg-good/10 border-good/25' },
  good:      { text: 'text-good',  bg: 'bg-good/10 border-good/25' },
  closed:    { text: 'text-good',  bg: 'bg-good/10 border-good/25' },
  cache_hit: { text: 'text-brand-2', bg: 'bg-brand-2/10 border-brand-2/25' },
  error:     { text: 'text-alert', bg: 'bg-alert/10 border-alert/25' },
  open:      { text: 'text-alert', bg: 'bg-alert/10 border-alert/25' },
  critical:  { text: 'text-alert', bg: 'bg-alert/10 border-alert/25' },
  cancelled: { text: 'text-warn',  bg: 'bg-warn/10 border-warn/25' },
  half_open: { text: 'text-warn',  bg: 'bg-warn/10 border-warn/25' },
  warning:   { text: 'text-warn',  bg: 'bg-warn/10 border-warn/25' },
  info:      { text: 'text-flux',  bg: 'bg-flux/10 border-flux/25' },
  neutral:   { text: 'text-ink-mid', bg: 'bg-surface-sunken border-line' },
}

export function Led({ tone = 'neutral', pulse = false }: { tone?: string; pulse?: boolean }) {
  const t = tones[tone] ?? tones.neutral
  return (
    <span
      className={`led ${pulse ? 'led-pulse' : ''} ${t.text}`}
      style={{ backgroundColor: 'currentColor' }}
    />
  )
}

export function Badge({ kind = 'neutral', children }: { kind?: string; children: ReactNode }) {
  const t = tones[kind] ?? tones.neutral
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-[3px] text-[11.5px] font-medium ${t.bg} ${t.text}`}
    >
      <Led tone={kind} />
      {children}
    </span>
  )
}

export function Button({ children, onClick, kind = 'default', disabled, type = 'button', title }: {
  children: ReactNode
  onClick?: () => void
  kind?: 'default' | 'primary' | 'danger' | 'ghost'
  disabled?: boolean
  type?: 'button' | 'submit'
  title?: string
}) {
  const base =
    'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12.5px] font-medium transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-40'
  const styles = {
    default:
      'border border-line bg-surface-card text-ink hover:bg-surface-hover hover:text-ink-hi',
    primary:
      'grad-brand text-white shadow-sm shadow-brand/25 hover:brightness-110 hover:shadow-md hover:shadow-brand/30',
    danger:
      'border border-alert/30 bg-alert/8 text-alert hover:bg-alert/15',
    ghost:
      'text-ink-mid hover:bg-surface-hover hover:text-ink-hi',
  }[kind]
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick} className={`${base} ${styles}`}>
      {children}
    </button>
  )
}

export function IconButton({ children, onClick, title, danger }: {
  children: ReactNode
  onClick?: () => void
  title?: string
  danger?: boolean
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`rounded-lg border border-line bg-surface-card p-1.5 text-ink-mid transition-colors hover:bg-surface-hover ${
        danger ? 'hover:border-alert/40 hover:text-alert' : 'hover:text-ink-hi'
      }`}
    >
      {children}
    </button>
  )
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`rounded-lg border border-line bg-surface-card px-3 py-2 text-[13px] text-ink-hi outline-none transition-all placeholder:text-ink-low focus:border-brand focus:ring-2 focus:ring-brand-ring/50 ${props.className ?? ''}`}
    />
  )
}

export function Segmented<T extends string | number>({ value, options, onChange }: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-sunken p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1 text-[12.5px] font-medium transition-all ${
            value === o.value
              ? 'bg-surface-card text-brand shadow-sm'
              : 'text-ink-mid hover:text-ink-hi'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-surface-sunken">
            {head.map((h, i) => (
              <th
                key={i}
                className={`whitespace-nowrap px-4 py-2.5 text-left text-[11.5px] font-semibold text-ink-mid ${
                  i === 0 ? 'rounded-l-lg' : ''
                } ${i === head.length - 1 ? 'rounded-r-lg' : ''}`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export function Tr({ children }: { children: ReactNode }) {
  return (
    <tr className="border-b border-line-soft transition-colors last:border-0 hover:bg-surface-sunken/70">
      {children}
    </tr>
  )
}

export function Td({ children, className = '', mono = false }: {
  children?: ReactNode
  className?: string
  mono?: boolean
}) {
  return (
    <td className={`whitespace-nowrap px-4 py-3 text-[13px] ${mono ? 'tnum' : ''} ${className}`}>
      {children}
    </td>
  )
}

export const Th = ({ children }: { children?: ReactNode }) => <>{children}</>

export function Empty({ text, hint, action }: { text: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16">
      <div className="text-[14px] font-medium text-ink">{text}</div>
      {hint && <div className="text-[12px] text-ink-low">{hint}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

export function Meter({ ratio, tone }: { ratio: number; tone?: 'brand' | 'warn' | 'alert' }) {
  const pct = Math.max(0, Math.min(100, ratio * 100))
  const auto = pct > 85 ? 'alert' : pct > 60 ? 'warn' : 'brand'
  const cls = { brand: 'grad-brand', warn: 'bg-warn', alert: 'bg-alert' }[tone ?? auto]
  return (
    <div className="h-2 overflow-hidden rounded-full bg-surface-sunken">
      <div className={`h-full rounded-full ${cls} transition-[width] duration-500`} style={{ width: `${pct}%` }} />
    </div>
  )
}

export function Hint({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-brand-ring/60 bg-brand-soft px-4 py-3 text-[12.5px] leading-relaxed text-ink">
      {children}
    </div>
  )
}
