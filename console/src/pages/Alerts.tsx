import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { AlertItem } from '../types'
import { Badge, Button, Card, Empty, Td, Th } from '../components/ui'

const severityBadge: Record<string, string> = {
  info: 'neutral',
  warning: 'cancelled',
  critical: 'error',
}

const kindLabel: Record<string, string> = {
  budget: '预算',
  breaker: '熔断',
  anomaly: '异常消耗',
  error_rate: '错误率',
  daily_report: '日报',
}

export default function Alerts() {
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [includeAcked, setIncludeAcked] = useState(false)
  const [running, setRunning] = useState(false)

  const load = useCallback(async () => {
    setAlerts(await api.alerts(includeAcked))
  }, [includeAcked])

  useEffect(() => {
    load().catch(console.error)
    const timer = setInterval(() => load().catch(console.error), 10000)
    return () => clearInterval(timer)
  }, [load])

  async function runNow() {
    setRunning(true)
    try {
      await api.runSentinel()
      await load()
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-slate-500">
          哨兵巡检:预算 80%/100%、渠道熔断、key 异常消耗、错误率突增、每日用量报告
          {'  '}·{'  '}可配 GW_ALERT_WEBHOOK_URL 推送到 Telegram/企微桥接
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-slate-500">
            <input
              type="checkbox"
              checked={includeAcked}
              onChange={(e) => setIncludeAcked(e.target.checked)}
            />
            含已确认
          </label>
          <Button onClick={runNow} disabled={running}>
            {running ? '巡检中…' : '立即巡检'}
          </Button>
          <Button onClick={() => api.ackAllAlerts().then(load)}>全部确认</Button>
        </div>
      </div>

      <Card>
        {alerts.length === 0 ? (
          <Empty text="没有未确认的告警——一切正常" />
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <Th>时间</Th><Th>类型</Th><Th>级别</Th><Th>内容</Th><Th></Th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr key={a.id} className={`border-b border-slate-800/50 ${a.acknowledged ? 'opacity-50' : ''}`}>
                  <Td className="text-xs text-slate-500">
                    {new Date(a.created_at).toLocaleString()}
                  </Td>
                  <Td><Badge kind="neutral">{kindLabel[a.kind] ?? a.kind}</Badge></Td>
                  <Td><Badge kind={severityBadge[a.severity] ?? 'neutral'}>{a.severity}</Badge></Td>
                  <Td>
                    <div className="text-slate-200">{a.title}</div>
                    <div className="text-xs text-slate-500">{a.detail}</div>
                  </Td>
                  <Td>
                    {!a.acknowledged && (
                      <Button kind="ghost" onClick={() => api.ackAlert(a.id).then(load)}>
                        确认
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
