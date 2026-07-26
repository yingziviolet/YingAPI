import type { ReactNode } from 'react'

export function Card({ title, extra, children, className = '' }: {
  title?: ReactNode
  extra?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`rounded-xl border border-slate-800 bg-slate-900/60 ${className}`}>
      {(title || extra) && (
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="text-sm font-medium text-slate-300">{title}</div>
          <div>{extra}</div>
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  )
}

export function StatCard({ label, value, sub, accent = 'text-slate-100' }: {
  label: string
  value: ReactNode
  sub?: ReactNode
  accent?: string
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${accent}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

const badgeStyles: Record<string, string> = {
  ok: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  cache_hit: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
  error: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
  cancelled: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  closed: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  open: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
  half_open: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  neutral: 'bg-slate-500/10 text-slate-400 border-slate-500/30',
}

export function Badge({ kind = 'neutral', children }: { kind?: string; children: ReactNode }) {
  const style = badgeStyles[kind] ?? badgeStyles.neutral
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${style}`}>
      {children}
    </span>
  )
}

export function Button({ children, onClick, kind = 'default', disabled, type = 'button' }: {
  children: ReactNode
  onClick?: () => void
  kind?: 'default' | 'primary' | 'danger' | 'ghost'
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  const styles = {
    default: 'border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-200',
    primary: 'border-cyan-600 bg-cyan-600/90 hover:bg-cyan-500 text-white',
    danger: 'border-rose-700 bg-rose-700/80 hover:bg-rose-600 text-white',
    ghost: 'border-transparent bg-transparent hover:bg-slate-800 text-slate-400',
  }[kind]
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40 ${styles}`}
    >
      {children}
    </button>
  )
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-sm text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-600 ${props.className ?? ''}`}
    />
  )
}

export function Th({ children }: { children?: ReactNode }) {
  return (
    <th className="whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-slate-500">
      {children}
    </th>
  )
}

export function Td({ children, className = '' }: { children?: ReactNode; className?: string }) {
  return <td className={`whitespace-nowrap px-3 py-2 text-sm ${className}`}>{children}</td>
}

export function Empty({ text }: { text: string }) {
  return <div className="py-10 text-center text-sm text-slate-600">{text}</div>
}
