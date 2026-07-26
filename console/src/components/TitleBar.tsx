import { Minus, Moon, Square, Sun, X } from 'lucide-react'
import { currentTheme, toggleTheme } from '../theme'

/** 自定义标题栏:pywebview frameless 窗口下提供拖拽与窗口控制;
 *  浏览器打开时窗口按钮自动隐藏。 */
export default function TitleBar({ status }: { status?: React.ReactNode }) {
  const inApp = typeof (window as any).pywebview !== 'undefined'

  function win(action: 'minimize' | 'toggle' | 'close') {
    const api = (window as any).pywebview?.api
    if (!api) return
    if (action === 'minimize') api.minimize?.()
    if (action === 'toggle') api.toggle_maximize?.()
    if (action === 'close') api.close?.()
  }

  return (
    // pywebview-drag-region 是 pywebview 的官方拖拽区标记(WebView2 不认 app-region)
    <header className="pywebview-drag-region titlebar-drag flex h-11 flex-none items-center gap-2 border-b border-line bg-surface-card px-3">
      <Logo />
      <span className="text-[13px] font-semibold tracking-tight text-ink-hi">LLM 网关</span>
      <span className="rounded-md bg-surface-sunken px-1.5 py-0.5 text-[10px] font-medium text-ink-low">
        Gateway
      </span>

      <div className="titlebar-nodrag ml-auto flex items-center gap-1">
        {status}
        <button
          onClick={toggleTheme}
          title={currentTheme() === 'light' ? '切换夜间模式' : '切换白天模式'}
          className="rounded-lg p-1.5 text-ink-mid transition-colors hover:bg-surface-hover hover:text-ink-hi"
        >
          {currentTheme() === 'light' ? <Moon size={15} /> : <Sun size={15} />}
        </button>
        {inApp && (
          <div className="ml-1 flex items-center">
            <WinBtn onClick={() => win('minimize')} label="最小化"><Minus size={14} /></WinBtn>
            <WinBtn onClick={() => win('toggle')} label="最大化"><Square size={11} /></WinBtn>
            <WinBtn onClick={() => win('close')} label="关闭" danger><X size={14} /></WinBtn>
          </div>
        )}
      </div>
    </header>
  )
}

function WinBtn({ children, onClick, label, danger }: {
  children: React.ReactNode
  onClick: () => void
  label: string
  danger?: boolean
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`px-3 py-2.5 text-ink-mid transition-colors ${
        danger ? 'hover:bg-alert hover:text-white' : 'hover:bg-surface-hover hover:text-ink-hi'
      }`}
    >
      {children}
    </button>
  )
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <div
      className="grad-brand flex items-center justify-center rounded-lg text-white shadow-sm"
      style={{ width: size, height: size }}
    >
      <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 24 24" fill="none" aria-hidden>
        {/* 一进三出:网关的路由语义 */}
        <circle cx="4.5" cy="12" r="2.2" fill="currentColor" />
        <circle cx="19" cy="5" r="1.8" fill="currentColor" opacity="0.9" />
        <circle cx="19" cy="12" r="1.8" fill="currentColor" opacity="0.7" />
        <circle cx="19" cy="19" r="1.8" fill="currentColor" opacity="0.5" />
        <path
          d="M6.7 12h2.6c1.4 0 1.9-.7 2.9-2.4C13.2 7.9 13.9 7 15.3 7h1.9M6.7 12h10.5M6.7 12h2.6c1.4 0 1.9.7 2.9 2.4 1 1.7 1.7 2.6 3.1 2.6h1.9"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          opacity="0.85"
        />
      </svg>
    </div>
  )
}
