import { useCallback, useEffect, useState } from 'react'
import { Check, CheckCheck, RefreshCw } from 'lucide-react'
import { api } from '../api'
import type { AlertItem } from '../types'
import { Badge, Button, Card, Empty, Table, Td, Tr } from '../components/ui'

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
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-4">
        <p className="text-[12.5px] leading-relaxed text-ink-mid">
          哨兵巡检:预算 80%/100%、渠道熔断、key 消耗异常突增(定位泄漏)、错误率突增、每日用量报告
          <br />
          <span className="text-ink-low">配置 GW_ALERT_WEBHOOK_URL 可推送到 Telegram / 企微桥接</span>
        </p>
        <div className="flex items-center gap-2">
          <label className="flex cursor-pointer items-center gap-1.5 text-[12px] text-ink-mid">
            <input
              type="checkbox"
              checked={includeAcked}
              onChange={(e) => setIncludeAcked(e.target.checked)}
              className="accent-brand"
            />
            含已确认
          </label>
          <Button onClick={runNow} disabled={running}>
            <RefreshCw size={14} className={running ? 'animate-spin' : ''} />
            {running ? '巡检中' : '立即巡检'}
          </Button>
          <Button onClick={() => api.ackAllAlerts().then(load)}>
            <CheckCheck size={14} />全部确认
          </Button>
        </div>
      </div>

      <Card pad={false}>
        {alerts.length === 0 ? (
          <Empty text="没有未确认的告警" hint="哨兵每分钟巡检一次,有异常会立刻出现在这里" />
        ) : (
          <Table head={['时间', '类型', '级别', '内容', '']}>
            {alerts.map((a) => (
              <Tr key={a.id}>
                <Td mono className="text-[11.5px] text-ink-low">
                  {new Date(a.created_at).toLocaleString()}
                </Td>
                <Td>
                  <span className="rounded-md bg-surface-sunken px-2 py-0.5 text-[11.5px] text-ink-mid">
                    {kindLabel[a.kind] ?? a.kind}
                  </span>
                </Td>
                <Td><Badge kind={a.severity}>{a.severity}</Badge></Td>
                <Td className={a.acknowledged ? 'opacity-50' : ''}>
                  <div className="font-medium text-ink-hi">{a.title}</div>
                  <div className="text-[11.5px] text-ink-mid">{a.detail}</div>
                </Td>
                <Td>
                  {!a.acknowledged && (
                    <Button kind="ghost" onClick={() => api.ackAlert(a.id).then(load)}>
                      <Check size={13} />确认
                    </Button>
                  )}
                </Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
