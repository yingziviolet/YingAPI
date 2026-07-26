import { useEffect, useState } from 'react'
import { Activity, ArrowDownRight, CircleDollarSign, Coins, Database, TriangleAlert, Wallet } from 'lucide-react'
import { api, fmtTokens, fmtUsd } from '../api'
import type { ChannelStat, DailyStat, KeySpend, ModelStat, Overview, VirtualKey } from '../types'
import Chart, { barGradient, chartLegend, chartTheme, chartTooltip, PALETTE } from '../components/Chart'
import { Card, Empty, Meter, Segmented, StatCard, Table, Td, Tr } from '../components/ui'

export default function Dashboard() {
  const [days, setDays] = useState(7)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [daily, setDaily] = useState<DailyStat[]>([])
  const [byChannel, setByChannel] = useState<ChannelStat[]>([])
  const [byModel, setByModel] = useState<ModelStat[]>([])
  const [spends, setSpends] = useState<(KeySpend & { key: VirtualKey })[]>([])
  const [balances, setBalances] = useState<any[]>([])

  useEffect(() => {
    let cancelled = false
    async function load() {
      const [ov, d, ch, m, keys] = await Promise.all([
        api.overview(days),
        api.statsDaily(days),
        api.statsChannels(days),
        api.statsModels(days),
        api.keys(),
      ])
      const spendRows = await Promise.all(keys.map(async (k) => ({ ...(await api.keySpend(k.id)), key: k })))
      if (cancelled) return
      setOverview(ov)
      setDaily(d)
      setByChannel(ch)
      setByModel(m)
      setSpends(spendRows)
    }
    load().catch(console.error)
    const timer = setInterval(() => load().catch(console.error), 15000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [days])

  useEffect(() => {
    api.balances().then(setBalances).catch(() => {})
  }, [])

  function depletion(spend: KeySpend): string {
    if (spend.monthly_budget_usd == null) return '不限额'
    const perDay = overview && overview.window_days > 0 ? overview.cost_usd / overview.window_days : 0
    if (perDay <= 0) return '尚无消耗'
    const remaining = spend.monthly_budget_usd - spend.month_to_date_usd
    if (remaining <= 0) return '已耗尽'
    return `按当前速率约 ${Math.floor(remaining / perDay)} 天后耗尽`
  }

  const withBalance = balances.filter((b) => b.ok && b.balance?.remaining != null)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <Segmented
          value={days}
          onChange={setDays}
          options={[
            { value: 1, label: '今天' },
            { value: 7, label: '近 7 天' },
            { value: 30, label: '近 30 天' },
          ]}
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="请求数" value={overview?.requests ?? '—'} tone="neutral"
          icon={<Activity size={15} />} delay={0} />
        <StatCard label="Token 消耗"
          value={fmtTokens((overview?.prompt_tokens ?? 0) + (overview?.completion_tokens ?? 0))}
          sub={`入 ${fmtTokens(overview?.prompt_tokens)} · 出 ${fmtTokens(overview?.completion_tokens)}`}
          tone="neutral" icon={<Coins size={15} />} delay={40} />
        <StatCard label="成本" value={fmtUsd(overview?.cost_usd)} tone="brand"
          icon={<CircleDollarSign size={15} />} delay={80} />
        <StatCard label="缓存命中率"
          value={overview ? `${(overview.cache_hit_rate * 100).toFixed(1)}%` : '—'}
          sub={`${overview?.cache_hits ?? 0} 次命中,省下等量上游调用`}
          tone="good" icon={<Database size={15} />} delay={120} />
        <StatCard label="错误率"
          value={overview ? `${(overview.error_rate * 100).toFixed(1)}%` : '—'}
          sub={`${overview?.errors ?? 0} 次失败${overview?.downgraded ? ` · ${overview.downgraded} 次降级` : ''}`}
          tone={overview && overview.error_rate > 0.05 ? 'alert' : 'neutral'}
          icon={<TriangleAlert size={15} />} delay={160} />
      </div>

      <Card title="每日请求与成本" desc="柱状为请求量,折线为当日成本" delay={200}>
        {daily.length === 0 ? (
          <Empty text="暂无数据" hint="把日常 LLM 调用切到网关后,这里会立刻有曲线" />
        ) : (
          <Chart
            option={{
              ...chartTheme(),
              tooltip: chartTooltip(),
              legend: { data: ['请求数', '缓存命中', '成本 (USD)'], ...chartLegend() },
              xAxis: {
                type: 'category',
                data: daily.map((d) => d.date.slice(5)),
                axisTick: { show: false },
              },
              yAxis: [
                { type: 'value', splitLine: chartTheme().splitLine },
                { type: 'value', splitLine: { show: false } },
              ],
              series: [
                {
                  name: '请求数', type: 'bar', barMaxWidth: 42,
                  data: daily.map((d) => d.requests),
                  itemStyle: { color: barGradient(), borderRadius: [4, 4, 0, 0] },
                },
                {
                  name: '缓存命中', type: 'bar', barMaxWidth: 42,
                  data: daily.map((d) => d.cache_hits),
                  itemStyle: { color: barGradient('#10b981', '#34d399'), borderRadius: [4, 4, 0, 0] },
                },
                {
                  name: '成本 (USD)', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 6,
                  data: daily.map((d) => Number(d.cost_usd.toFixed(6))),
                  lineStyle: { color: PALETTE.warn, width: 2 },
                  itemStyle: { color: PALETTE.warn },
                },
              ],
            }}
          />
        )}
      </Card>

      {withBalance.length > 0 && (
        <Card title="渠道余额" desc="用各渠道自有 key 查询其公开余额接口"
          extra={<Wallet size={15} className="text-ink-low" />} delay={240}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {withBalance.map((b) => (
              <div key={b.channel_id} className="rounded-lg border border-line bg-surface-sunken px-4 py-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-[13px] font-medium text-ink-hi">{b.channel_name}</span>
                  <span className="text-[10.5px] text-ink-low">{b.source}</span>
                </div>
                <div className="tnum mt-1.5 text-[20px] font-bold grad-text">
                  {b.balance.currency === 'CNY' ? '¥' : '$'}
                  {Number(b.balance.remaining).toFixed(2)}
                </div>
                {b.balance.total != null && (
                  <div className="mt-2">
                    <Meter ratio={1 - b.balance.remaining / b.balance.total} />
                    <div className="mt-1 text-[11px] text-ink-low">
                      共 {b.balance.currency === 'CNY' ? '¥' : '$'}{Number(b.balance.total).toFixed(2)}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="按渠道" pad={false} delay={280}>
          {byChannel.length === 0 ? (
            <Empty text="暂无数据" />
          ) : (
            <Table head={['渠道', '请求', 'Token', '成本', '均延迟']}>
              {byChannel.map((c, i) => (
                <Tr key={i}>
                  <Td className="font-medium text-ink-hi">{c.channel_name}</Td>
                  <Td mono>{c.requests}</Td>
                  <Td mono>{fmtTokens(c.total_tokens)}</Td>
                  <Td mono>{fmtUsd(c.cost_usd)}</Td>
                  <Td mono className="text-ink-mid">{c.avg_latency_ms.toFixed(0)} ms</Td>
                </Tr>
              ))}
            </Table>
          )}
        </Card>

        <Card title="按模型" pad={false} delay={320}>
          {byModel.length === 0 ? (
            <Empty text="暂无数据" />
          ) : (
            <Table head={['模型', '请求', 'Token', '成本']}>
              {byModel.map((m) => (
                <Tr key={m.model}>
                  <Td className="font-medium text-ink-hi">{m.model}</Td>
                  <Td mono>{m.requests}</Td>
                  <Td mono>{fmtTokens(m.total_tokens)}</Td>
                  <Td mono>{fmtUsd(m.cost_usd)}</Td>
                </Tr>
              ))}
            </Table>
          )}
        </Card>
      </div>

      <Card title="虚拟 Key 预算" desc="按当前消耗速率预测耗尽时间"
        extra={<ArrowDownRight size={15} className="text-ink-low" />} delay={360}>
        {spends.length === 0 ? (
          <Empty text="还没有虚拟 key" hint="到「虚拟 Key」页发放一个给你的客户端" />
        ) : (
          <div className="space-y-4">
            {spends.map((s) => (
              <div key={s.key_id}>
                <div className="mb-1.5 flex items-baseline justify-between gap-3">
                  <span className="text-[13px] font-medium text-ink-hi">{s.name}</span>
                  <span className="tnum text-[12px] text-ink-mid">
                    {fmtUsd(s.month_to_date_usd)}
                    {s.monthly_budget_usd != null && (
                      <span className="text-ink-low"> / {fmtUsd(s.monthly_budget_usd)}</span>
                    )}
                    <span className="ml-2 font-sans text-[11px] text-ink-low">{depletion(s)}</span>
                  </span>
                </div>
                <Meter
                  ratio={
                    s.monthly_budget_usd && s.monthly_budget_usd > 0
                      ? s.month_to_date_usd / s.monthly_budget_usd
                      : 0
                  }
                />
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
