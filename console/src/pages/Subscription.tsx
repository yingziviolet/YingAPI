import { useEffect, useState } from 'react'
import { FolderSearch, MessageSquare, Coins, Wallet } from 'lucide-react'
import { api, fmtTokens, fmtUsd } from '../api'
import type { SubscriptionUsage } from '../types'
import Chart, { barGradient, chartLegend, chartTheme, chartTooltip, PALETTE } from '../components/Chart'
import { Card, Empty, Segmented, StatCard, Table, Td, Tr } from '../components/ui'

export default function Subscription() {
  const [days, setDays] = useState(7)
  const [data, setData] = useState<SubscriptionUsage | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.subscriptionUsage(days).then(setData).catch(console.error).finally(() => setLoading(false))
  }, [days])

  if (loading && !data) {
    return <Card><Empty text="正在扫描本机会话记录…" /></Card>
  }
  if (!data?.available) {
    return (
      <Card>
        <Empty
          text="没有可解析的订阅用量数据"
          hint={data?.reason ?? '本机未找到 ~/.claude/projects 记录'}
        />
      </Card>
    )
  }

  const totals = data.totals!
  const allTokens =
    totals.input_tokens + totals.output_tokens + totals.cache_read_tokens + totals.cache_creation_tokens

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-4">
        <p className="text-[12.5px] leading-relaxed text-ink-mid">
          数据源:本机 <code className="rounded bg-surface-sunken px-1 py-0.5 text-[11.5px]">~/.claude</code> 会话记录
          <span className="text-ink-low">(只读本地文件,不调用厂商任何接口)· 已扫描 {data.files_scanned} 个文件</span>
        </p>
        <Segmented
          value={days}
          onChange={setDays}
          options={[
            { value: 7, label: '近 7 天' },
            { value: 30, label: '近 30 天' },
            { value: 90, label: '近 90 天' },
          ]}
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="消息数" value={totals.messages} tone="neutral"
          icon={<MessageSquare size={15} />} delay={0} />
        <StatCard label="总 Token" value={fmtTokens(allTokens)}
          sub={`出 ${fmtTokens(totals.output_tokens)} · 缓存读 ${fmtTokens(totals.cache_read_tokens)}`}
          tone="neutral" icon={<Coins size={15} />} delay={40} />
        <StatCard label="按 API 牌价折算" value={fmtUsd(data.est_api_cost_usd)}
          sub="这些用量若走 API 需要花的钱" tone="brand"
          icon={<Wallet size={15} />} delay={80} />
        <StatCard label="日均折算"
          value={fmtUsd((data.est_api_cost_usd ?? 0) / (data.window_days ?? 1))}
          tone="good" icon={<FolderSearch size={15} />} delay={120} />
      </div>

      <Card title="每日订阅用量" desc="柱状为消息数,折线为按牌价折算的成本" delay={160}>
        {!data.daily?.length ? (
          <Empty text="窗口内没有记录" />
        ) : (
          <Chart
            option={{
              ...chartTheme(),
              tooltip: chartTooltip(),
              legend: { data: ['消息数', '折算成本'], ...chartLegend() },
              xAxis: {
                type: 'category',
                data: data.daily.map((d) => d.date.slice(5)),
                axisTick: { show: false },
              },
              yAxis: [
                { type: 'value', splitLine: chartTheme().splitLine },
                { type: 'value', splitLine: { show: false } },
              ],
              series: [
                {
                  name: '消息数', type: 'bar', barMaxWidth: 42,
                  data: data.daily.map((d) => d.messages),
                  itemStyle: { color: barGradient(), borderRadius: [4, 4, 0, 0] },
                },
                {
                  name: '折算成本', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
                  data: data.daily.map((d) => d.est_cost_usd),
                  lineStyle: { color: PALETTE.warn, width: 2 },
                  itemStyle: { color: PALETTE.warn },
                },
              ],
            }}
          />
        )}
      </Card>

      <Card title="按模型" pad={false} delay={200}>
        {!data.by_model?.length ? (
          <Empty text="窗口内没有记录" />
        ) : (
          <Table head={['模型', '消息', '入', '出', '缓存读', '缓存写', '折算成本']}>
            {data.by_model.map((m) => (
              <Tr key={m.model}>
                <Td className="font-medium text-ink-hi">{m.model}</Td>
                <Td mono>{m.messages}</Td>
                <Td mono>{fmtTokens(m.input_tokens)}</Td>
                <Td mono>{fmtTokens(m.output_tokens)}</Td>
                <Td mono className="text-ink-mid">{fmtTokens(m.cache_read_tokens)}</Td>
                <Td mono className="text-ink-mid">{fmtTokens(m.cache_creation_tokens)}</Td>
                <Td mono>{fmtUsd(m.est_cost_usd)}</Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
