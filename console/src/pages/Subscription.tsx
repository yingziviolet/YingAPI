import { useEffect, useState } from 'react'
import { api, fmtTokens, fmtUsd } from '../api'
import type { SubscriptionUsage } from '../types'
import Chart, { chartTheme } from '../components/Chart'
import { Card, Empty, StatCard, Td, Th } from '../components/ui'

export default function Subscription() {
  const [days, setDays] = useState(7)
  const [data, setData] = useState<SubscriptionUsage | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api
      .subscriptionUsage(days)
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [days])

  if (loading && !data) {
    return <div className="py-16 text-center text-slate-500">扫描本机 Claude Code 记录…</div>
  }
  if (!data?.available) {
    return (
      <Card>
        <Empty text={data?.reason ?? '本机没有可解析的订阅用量数据'} />
      </Card>
    )
  }

  const totals = data.totals!
  const allTokens =
    totals.input_tokens + totals.output_tokens + totals.cache_read_tokens + totals.cache_creation_tokens

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-lg px-3 py-1 text-xs ${
                days === d ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
              }`}
            >
              近 {d} 天
            </button>
          ))}
        </div>
        <div className="text-xs text-slate-600">
          数据源:本机 ~/.claude 会话记录(只读本地文件,不碰厂商接口)· 扫描 {data.files_scanned} 个文件
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="消息数" value={totals.messages} />
        <StatCard
          label="总 Token(含缓存)"
          value={fmtTokens(allTokens)}
          sub={`出 ${fmtTokens(totals.output_tokens)} / 缓存读 ${fmtTokens(totals.cache_read_tokens)}`}
        />
        <StatCard
          label="按 API 牌价折算"
          value={fmtUsd(data.est_api_cost_usd)}
          sub="订阅覆盖的这些用量,走 API 要花的钱"
          accent="text-amber-400"
        />
        <StatCard
          label="日均折算"
          value={fmtUsd((data.est_api_cost_usd ?? 0) / (data.window_days ?? 1))}
          accent="text-cyan-400"
        />
      </div>

      <Card title="每日订阅用量(折算 USD)">
        {!data.daily?.length ? (
          <Empty text="窗口内没有记录" />
        ) : (
          <Chart
            option={{
              ...chartTheme,
              tooltip: { trigger: 'axis' },
              legend: { data: ['消息数', '折算成本'], textStyle: { color: '#94a3b8' } },
              xAxis: { type: 'category', data: data.daily.map((d) => d.date.slice(5)) },
              yAxis: [
                { type: 'value', splitLine: chartTheme.splitLine },
                { type: 'value', splitLine: { show: false } },
              ],
              series: [
                {
                  name: '消息数', type: 'bar', data: data.daily.map((d) => d.messages),
                  itemStyle: { color: '#0891b2', borderRadius: [3, 3, 0, 0] },
                },
                {
                  name: '折算成本', type: 'line', yAxisIndex: 1, smooth: true,
                  data: data.daily.map((d) => d.est_cost_usd),
                  lineStyle: { color: '#f59e0b' }, itemStyle: { color: '#f59e0b' },
                },
              ],
            }}
          />
        )}
      </Card>

      <Card title="按模型">
        {!data.by_model?.length ? (
          <Empty text="窗口内没有记录" />
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <Th>模型</Th><Th>消息</Th><Th>入</Th><Th>出</Th><Th>缓存读</Th><Th>缓存写</Th><Th>折算</Th>
              </tr>
            </thead>
            <tbody>
              {data.by_model.map((m) => (
                <tr key={m.model} className="border-b border-slate-800/50">
                  <Td className="text-slate-300">{m.model}</Td>
                  <Td>{m.messages}</Td>
                  <Td>{fmtTokens(m.input_tokens)}</Td>
                  <Td>{fmtTokens(m.output_tokens)}</Td>
                  <Td>{fmtTokens(m.cache_read_tokens)}</Td>
                  <Td>{fmtTokens(m.cache_creation_tokens)}</Td>
                  <Td>{fmtUsd(m.est_cost_usd)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
