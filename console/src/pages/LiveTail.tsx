import { useEffect, useRef, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { api, fmtUsd, liveTailUrl } from '../api'
import type { RequestLogItem } from '../types'
import { Badge, Button, Card, Empty, Led, Table, Td, Tr } from '../components/ui'

const MAX_ROWS = 200

export default function LiveTail() {
  const [rows, setRows] = useState<RequestLogItem[]>([])
  const [connected, setConnected] = useState(false)
  const [paused, setPaused] = useState(false)
  const pausedRef = useRef(false)
  pausedRef.current = paused

  useEffect(() => {
    api.logs(50).then(setRows).catch(console.error)

    let closed = false
    let retry: number | undefined
    function connect() {
      const ws = new WebSocket(liveTailUrl())
      ws.onopen = () => setConnected(true)
      ws.onmessage = (msg) => {
        if (pausedRef.current) return
        try {
          const event = JSON.parse(msg.data) as RequestLogItem
          setRows((prev) => [event, ...prev].slice(0, MAX_ROWS))
        } catch { /* ignore */ }
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = window.setTimeout(connect, 2000)
      }
      return ws
    }
    const ws = connect()
    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      ws.close()
    }
  }, [])

  return (
    <Card
      title="实时请求流"
      desc="计量落库的同时旁路推送,不影响数据面"
      pad={false}
      extra={
        <div className="flex items-center gap-2.5">
          <span className="flex items-center gap-1.5 text-[11.5px] text-ink-mid">
            <Led tone={connected ? 'good' : 'error'} pulse={connected} />
            {connected ? 'WebSocket 已连接' : '重连中…'}
          </span>
          <Button onClick={() => setPaused((p) => !p)}>
            {paused ? <><Play size={13} />继续</> : <><Pause size={13} />暂停</>}
          </Button>
        </div>
      }
    >
      {rows.length === 0 ? (
        <Empty text="等待请求" hint="发一条请求到网关,这里会立刻出现" />
      ) : (
        <div className="max-h-[62vh] overflow-auto">
          <Table head={['时间', '状态', '模型', '缓存', '流式', 'Token 入/出', '成本', '延迟', '首字', 'Trace']}>
            {rows.map((r, i) => (
              <Tr key={`${r.trace_id}-${i}`}>
                <Td mono className="text-[11.5px] text-ink-low">
                  {r.ts
                    ? new Date(r.ts * 1000).toLocaleTimeString()
                    : r.created_at
                      ? new Date(r.created_at).toLocaleTimeString()
                      : '—'}
                </Td>
                <Td><Badge kind={r.status}>{r.status}</Badge></Td>
                <Td className="text-ink-hi">
                  {r.model}
                  {r.downgraded_to && (
                    <span className="ml-1.5 rounded bg-brand-soft px-1 py-0.5 text-[10px] text-brand">
                      ↓{r.downgraded_to}
                    </span>
                  )}
                </Td>
                <Td>{r.cache_hit ? <Badge kind="cache_hit">命中</Badge> : <span className="text-ink-low">—</span>}</Td>
                <Td className="text-[11.5px] text-ink-mid">{r.stream ? 'SSE' : '—'}</Td>
                <Td mono className="text-[12px]">{r.prompt_tokens ?? '—'} / {r.completion_tokens ?? '—'}</Td>
                <Td mono className="text-[12px]">{fmtUsd(r.cost_usd)}</Td>
                <Td mono className="text-[12px]">{r.latency_ms != null ? `${r.latency_ms}ms` : '—'}</Td>
                <Td mono className="text-[12px] text-ink-mid">{r.first_token_ms != null ? `${r.first_token_ms}ms` : '—'}</Td>
                <Td><code className="text-[11px] text-ink-low">{r.trace_id?.slice(0, 8)}</code></Td>
              </Tr>
            ))}
          </Table>
        </div>
      )}
    </Card>
  )
}
