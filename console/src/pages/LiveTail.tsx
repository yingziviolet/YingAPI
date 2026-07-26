import { useEffect, useRef, useState } from 'react'
import { api, fmtUsd, liveTailUrl } from '../api'
import type { RequestLogItem } from '../types'
import { Badge, Card, Td, Th } from '../components/ui'

const MAX_ROWS = 200

export default function LiveTail() {
  const [rows, setRows] = useState<RequestLogItem[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    // 先加载最近历史,再接实时流
    api
      .logs(50)
      .then((logs) => setRows(logs))
      .catch(console.error)

    let closed = false
    let retryTimer: number | undefined

    function connect() {
      const ws = new WebSocket(liveTailUrl())
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data) as RequestLogItem
          setRows((prev) => [event, ...prev].slice(0, MAX_ROWS))
        } catch {
          /* ignore */
        }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retryTimer = window.setTimeout(connect, 2000)
      }
    }
    connect()
    return () => {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
      wsRef.current?.close()
    }
  }, [])

  return (
    <Card
      title="实时请求流"
      extra={
        <span className="flex items-center gap-2 text-xs text-slate-500">
          <span className={`h-2 w-2 rounded-full ${connected ? 'bg-emerald-400' : 'bg-rose-400'}`} />
          {connected ? 'WebSocket 已连接' : '重连中…'}
        </span>
      }
    >
      <div className="max-h-[70vh] overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-slate-900">
            <tr className="border-b border-slate-800">
              <Th>时间</Th><Th>状态</Th><Th>模型</Th><Th>缓存</Th><Th>流式</Th>
              <Th>Token 入/出</Th><Th>成本</Th><Th>延迟</Th><Th>首字</Th><Th>trace</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.trace_id}-${i}`} className="border-b border-slate-800/40">
                <Td className="text-xs text-slate-500">
                  {r.ts
                    ? new Date(r.ts * 1000).toLocaleTimeString()
                    : r.created_at
                      ? new Date(r.created_at).toLocaleTimeString()
                      : '—'}
                </Td>
                <Td><Badge kind={r.status}>{r.status}</Badge></Td>
                <Td className="text-slate-300">{r.model}</Td>
                <Td>{r.cache_hit ? <Badge kind="cache_hit">命中</Badge> : <span className="text-xs text-slate-600">—</span>}</Td>
                <Td className="text-xs text-slate-500">{r.stream ? 'SSE' : '—'}</Td>
                <Td className="tabular-nums text-xs">
                  {r.prompt_tokens ?? '—'} / {r.completion_tokens ?? '—'}
                </Td>
                <Td className="text-xs">{fmtUsd(r.cost_usd)}</Td>
                <Td className="tabular-nums text-xs">{r.latency_ms != null ? `${r.latency_ms}ms` : '—'}</Td>
                <Td className="tabular-nums text-xs">{r.first_token_ms != null ? `${r.first_token_ms}ms` : '—'}</Td>
                <Td><code className="text-xs text-slate-600">{r.trace_id?.slice(0, 8)}</code></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
