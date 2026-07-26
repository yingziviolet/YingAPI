import { useRef, useState } from 'react'
import {
  CheckCircle2,
  ClipboardPaste,
  Database,
  FileUp,
  Loader2,
  X,
  XCircle,
} from 'lucide-react'
import { api } from '../api'
import { Button, Hint } from './ui'

type Tab = 'keys' | 'billing'

interface ParsedItem {
  api_key: string
  api_key_masked: string
  name: string
  provider: string
  label: string
  base_url: string
  models: string[]
  prices: Record<string, { input?: number; output?: number }>
}

interface VerifyResult {
  reachable: boolean
  status_code?: number
  error?: string
  balance?: { remaining?: number; total?: number; currency?: string }
}

export default function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [tab, setTab] = useState<Tab>('keys')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6 backdrop-blur-sm">
      <div className="card flex max-h-[86vh] w-[820px] flex-col overflow-hidden">
        <header className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="text-[17px] font-bold tracking-tight text-ink-hi">批量导入</h2>
          <button onClick={onClose} className="rounded-lg p-1.5 text-ink-mid hover:bg-surface-hover hover:text-ink-hi">
            <X size={18} />
          </button>
        </header>

        <div className="border-b border-line px-6 py-3">
          <div className="inline-flex rounded-lg border border-line bg-surface-sunken p-0.5">
            {([
              { v: 'keys' as Tab, label: 'API Key', icon: <Database size={14} /> },
              { v: 'billing' as Tab, label: '历史账单', icon: <FileUp size={14} /> },
            ]).map((o) => (
              <button
                key={o.v}
                onClick={() => setTab(o.v)}
                className={`inline-flex items-center gap-1.5 rounded-md px-4 py-1.5 text-[12.5px] font-medium transition-all ${
                  tab === o.v ? 'bg-surface-card text-brand shadow-sm' : 'text-ink-mid hover:text-ink-hi'
                }`}
              >
                {o.icon}
                {o.label}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-6 py-5">
          {tab === 'keys' ? <KeyImport onDone={onDone} /> : <BillingImport onDone={onDone} />}
        </div>
      </div>
    </div>
  )
}

/* ---------------- API Key 导入 ---------------- */

function KeyImport({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState('')
  const [items, setItems] = useState<ParsedItem[]>([])
  const [checked, setChecked] = useState<Record<number, boolean>>({})
  const [verifying, setVerifying] = useState(false)
  const [verified, setVerified] = useState<Record<number, VerifyResult>>({})
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<{ created: any[]; skipped: any[] } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function parse(input: string) {
    setText(input)
    setResult(null)
    setVerified({})
    if (!input.trim()) {
      setItems([])
      return
    }
    const res = await api.importKeysPreview(input)
    setItems(res.items)
    setChecked(Object.fromEntries(res.items.map((_: any, i: number) => [i, true])))
  }

  async function readFile(file: File) {
    parse(await file.text())
  }

  async function verify() {
    setVerifying(true)
    try {
      const results = await api.importKeysVerify(items)
      setVerified(Object.fromEntries(results.map((r: VerifyResult, i: number) => [i, r])))
    } finally {
      setVerifying(false)
    }
  }

  async function doImport() {
    setImporting(true)
    try {
      const picked = items.filter((_, i) => checked[i])
      const res = await api.importKeys(picked)
      setResult(res)
      onDone()
    } finally {
      setImporting(false)
    }
  }

  const pickedCount = items.filter((_, i) => checked[i]).length

  return (
    <div className="space-y-4">
      <Hint>
        支持三种输入:<b>JSON 数组</b>、<b>CSV(含 key 列)</b>、<b>一行一个 key 的纯文本</b>。
        系统会自动识别厂商并预填 Base URL、模型与价格,你可以先验证连通性和余额再决定导入哪些。
      </Hint>

      <div className="flex items-center gap-2">
        <Button onClick={() => fileRef.current?.click()}>
          <FileUp size={14} />选择文件
        </Button>
        <Button
          onClick={async () => {
            try {
              parse(await navigator.clipboard.readText())
            } catch {
              alert('无法读取剪贴板,请直接粘贴到下方文本框')
            }
          }}
        >
          <ClipboardPaste size={14} />从剪贴板粘贴
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,.csv,.txt"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && readFile(e.target.files[0])}
        />
      </div>

      <textarea
        value={text}
        onChange={(e) => parse(e.target.value)}
        placeholder={'粘贴内容,例如:\nsk-xxxxxxxxxxxxxxxx\nsk-yyyyyyyyyyyyyyyy\n\n或 JSON:[{"name":"deepseek","api_key":"sk-...","base_url":"https://api.deepseek.com/v1"}]'}
        rows={6}
        className="w-full rounded-lg border border-line bg-surface-sunken px-3 py-2.5 font-mono text-[12px] text-ink-hi outline-none transition-all placeholder:text-ink-low focus:border-brand focus:ring-2 focus:ring-brand-ring/50"
        onDrop={async (e) => {
          e.preventDefault()
          const file = e.dataTransfer.files?.[0]
          if (file) readFile(file)
        }}
        onDragOver={(e) => e.preventDefault()}
      />

      {items.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <span className="text-[12.5px] text-ink-mid">
              解析出 <b className="text-ink-hi">{items.length}</b> 个 key,已选 {pickedCount} 个
            </span>
            <Button onClick={verify} disabled={verifying}>
              {verifying ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
              {verifying ? '验证中…' : '验证连通性与余额'}
            </Button>
          </div>

          <div className="max-h-[280px] overflow-auto rounded-lg border border-line">
            <table className="w-full">
              <thead className="sticky top-0 bg-surface-sunken">
                <tr>
                  <th className="w-10 px-3 py-2"></th>
                  <th className="px-3 py-2 text-left text-[11.5px] font-semibold text-ink-mid">名称</th>
                  <th className="px-3 py-2 text-left text-[11.5px] font-semibold text-ink-mid">识别为</th>
                  <th className="px-3 py-2 text-left text-[11.5px] font-semibold text-ink-mid">Key</th>
                  <th className="px-3 py-2 text-left text-[11.5px] font-semibold text-ink-mid">验证</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, i) => {
                  const v = verified[i]
                  return (
                    <tr key={i} className="border-t border-line-soft">
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={!!checked[i]}
                          onChange={(e) => setChecked((c) => ({ ...c, [i]: e.target.checked }))}
                          className="accent-brand"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          value={item.name}
                          onChange={(e) => {
                            const next = [...items]
                            next[i] = { ...item, name: e.target.value }
                            setItems(next)
                          }}
                          className="w-28 rounded border border-line bg-surface-card px-1.5 py-0.5 text-[12px] text-ink-hi outline-none focus:border-brand"
                        />
                      </td>
                      <td className="px-3 py-2 text-[12px]">
                        <div className="text-ink-hi">{item.label}</div>
                        <div className="text-[10.5px] text-ink-low">{item.base_url || '需手工填写'}</div>
                      </td>
                      <td className="px-3 py-2">
                        <code className="text-[11px] text-ink-low">{item.api_key_masked}</code>
                      </td>
                      <td className="px-3 py-2 text-[11.5px]">
                        {!v ? (
                          <span className="text-ink-low">未验证</span>
                        ) : v.reachable ? (
                          <span className="inline-flex items-center gap-1 text-good">
                            <CheckCircle2 size={13} />
                            连通
                            {v.balance?.remaining != null && (
                              <span className="tnum ml-1 text-ink-mid">
                                {v.balance.currency === 'CNY' ? '¥' : '$'}
                                {Number(v.balance.remaining).toFixed(2)}
                              </span>
                            )}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-alert" title={v.error}>
                            <XCircle size={13} />
                            {v.status_code ?? '失败'}
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-2">
            <Button kind="primary" onClick={doImport} disabled={pickedCount === 0 || importing}>
              {importing ? <Loader2 size={14} className="animate-spin" /> : <Database size={14} />}
              导入选中的 {pickedCount} 个
            </Button>
          </div>
        </>
      )}

      {result && (
        <div className="rounded-lg border border-good/25 bg-good/8 px-4 py-3 text-[12.5px]">
          <div className="font-medium text-good">
            成功导入 {result.created.length} 个渠道
            {result.skipped.length > 0 && `,跳过 ${result.skipped.length} 个`}
          </div>
          {result.created.length > 0 && (
            <div className="mt-1 text-ink-mid">{result.created.map((c: any) => c.name).join('、')}</div>
          )}
          {result.skipped.length > 0 && (
            <div className="mt-1 text-ink-low">
              跳过:{result.skipped.map((s: any) => `${s.name}(${s.reason})`).join('、')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ---------------- 历史账单导入 ---------------- */

function BillingImport({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function doImport() {
    setBusy(true)
    try {
      const res = await api.importBilling(text)
      setResult(res)
      onDone()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Hint>
        把厂商后台导出的消费记录(CSV / JSON)贴进来,补齐<b>切到网关之前</b>的历史数据,大盘曲线不断档。
        <br />
        识别列:日期/时间、模型、请求数、输入/输出 token、成本(中英文列名都认)。
        导入的记录标记为 <code className="rounded bg-surface-sunken px-1">imported</code>,与网关自身计量区分。
      </Hint>

      <div className="flex items-center gap-2">
        <Button onClick={() => fileRef.current?.click()}><FileUp size={14} />选择账单文件</Button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,.csv"
          className="hidden"
          onChange={async (e) => {
            const f = e.target.files?.[0]
            if (f) setText(await f.text())
          }}
        />
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={'date,model,requests,input_tokens,output_tokens,cost\n2026-06-01,deepseek-chat,120,45000,8000,0.32\n2026-06-02,deepseek-chat,98,38000,6500,0.27'}
        rows={8}
        className="w-full rounded-lg border border-line bg-surface-sunken px-3 py-2.5 font-mono text-[12px] text-ink-hi outline-none transition-all placeholder:text-ink-low focus:border-brand focus:ring-2 focus:ring-brand-ring/50"
        onDrop={async (e) => {
          e.preventDefault()
          const f = e.dataTransfer.files?.[0]
          if (f) setText(await f.text())
        }}
        onDragOver={(e) => e.preventDefault()}
      />

      <Button kind="primary" onClick={doImport} disabled={!text.trim() || busy}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <FileUp size={14} />}
        导入账单
      </Button>

      {result && (
        <div
          className={`rounded-lg border px-4 py-3 text-[12.5px] ${
            result.imported ? 'border-good/25 bg-good/8' : 'border-warn/25 bg-warn/8'
          }`}
        >
          {result.imported ? (
            <>
              <div className="font-medium text-good">
                成功导入 {result.imported} 条记录,合计 ${Number(result.total_cost_usd).toFixed(4)}
              </div>
              <div className="mt-1 text-ink-mid">
                已并入额度大盘。导错了可以撤销:
                <code className="ml-1 rounded bg-surface-sunken px-1 text-[11px]">{result.trace_id}</code>
              </div>
            </>
          ) : (
            <div className="text-warn">{result.reason}</div>
          )}
        </div>
      )}
    </div>
  )
}
