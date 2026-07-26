import { useEffect, useState } from 'react'
import { api, fmtTokens, fmtUsd } from '../api'
import type { ChannelStat, DailyStat, KeySpend, ModelStat, Overview, VirtualKey } from '../types'
import Chart, { chartLegend, chartTheme } from '../components/Chart'
import { Card, Empty, StatCard, Td, Th } from '../components/ui'

export default function Dashboard() {
  const [days, setDays] = useState(7)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [daily, setDaily] = useState<DailyStat[]>([])
  const [byChannel, setByChannel] = useState<ChannelStat[]>([])
  const [byModel, setByModel] = useState<ModelStat[]>([])
  const [spends, setSpends] = useState<(KeySpend & { key: VirtualKey })[]>([])

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
      const spendRows = await Promise.all(
        keys.map(async (k) => ({ ...(await api.keySpend(k.id)), key: k })),
      )
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

  // 按当前窗口日均成本预测预算耗尽天数
  function depletionDays(spend: KeySpend): string {
    if (spend.monthly_budget_usd == null) return '不限'
    const daily = overview && overview.window_days > 0 ? overview.cost_usd / overview.window_days : 0
    if (daily <= 0) return '∞'
    const remaining = spend.monthly_budget_usd - spend.month_to_date_usd
    if (remaining <= 0) return '已耗尽'
    return `≈ ${Math.floor(remaining / daily)} 天后耗尽`
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {[1, 7, 30].map((d) => (
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

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="请求数" value={overview?.requests ?? '—'} />
        <StatCard
          label="Token 消耗"
          value={fmtTokens((overview?.prompt_tokens ?? 0) + (overview?.completion_tokens ?? 0))}
          sub={`入 ${fmtTokens(overview?.prompt_tokens)} / 出 ${fmtTokens(overview?.completion_tokens)}`}
        />
        <StatCard label="成本" value={fmtUsd(overview?.cost_usd)} accent="text-cyan-400" />
        <StatCard
          label="缓存命中率"
          value={overview ? `${(overview.cache_hit_rate * 100).toFixed(1)}%` : '—'}
          sub={`${overview?.cache_hits ?? 0} 次命中`}
          accent="text-emerald-400"
        />
        <StatCard
          label="错误率"
          value={overview ? `${(overview.error_rate * 100).toFixed(1)}%` : '—'}
          sub={`${overview?.errors ?? 0} 次错误`}
          accent={overview && overview.error_rate > 0.05 ? 'text-rose-400' : 'text-slate-100'}
        />
      </div>

      <Card title="每日请求 / 成本">
        {daily.length === 0 ? (
          <Empty text="暂无数据——把流量切过来就有了" />
        ) : (
          <Chart
            option={{
              ...chartTheme(),
              tooltip: { trigger: 'axis' },
              legend: { data: ['请求数', '缓存命中', '成本 (USD)'], ...chartLegend() },
              xAxis: { type: 'category', data: daily.map((d) => d.date.slice(5)) },
              yAxis: [
                { type: 'value', splitLine: chartTheme().splitLine },
                { type: 'value', splitLine: { show: false } },
              ],
              series: [
                {
                  name: '请求数', type: 'bar', barMaxWidth: 48,
                  data: daily.map((d) => d.requests),
                  itemStyle: { color: '#0891b2', borderRadius: [3, 3, 0, 0] },
                },
                {
                  name: '缓存命中', type: 'bar', barMaxWidth: 48,
                  data: daily.map((d) => d.cache_hits),
                  itemStyle: { color: '#10b981', borderRadius: [3, 3, 0, 0] },
                },
                {
                  name: '成本 (USD)', type: 'line', yAxisIndex: 1, smooth: true,
                  data: daily.map((d) => Number(d.cost_usd.toFixed(6))),
                  lineStyle: { color: '#f59e0b' }, itemStyle: { color: '#f59e0b' },
                },
              ],
            }}
          />
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="按渠道">
          {byChannel.length === 0 ? (
            <Empty text="暂无数据" />
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-800">
                  <Th>渠道</Th><Th>请求</Th><Th>Token</Th><Th>成本</Th><Th>均延迟</Th>
                </tr>
              </thead>
              <tbody>
                {byChannel.map((c, i) => (
                  <tr key={i} className="border-b border-slate-800/50">
                    <Td className="text-slate-300">{c.channel_name}</Td>
                    <Td>{c.requests}</Td>
                    <Td>{fmtTokens(c.total_tokens)}</Td>
                    <Td>{fmtUsd(c.cost_usd)}</Td>
                    <Td>{c.avg_latency_ms.toFixed(0)} ms</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="按模型">
          {byModel.length === 0 ? (
            <Empty text="暂无数据" />
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-800">
                  <Th>模型</Th><Th>请求</Th><Th>Token</Th><Th>成本</Th>
                </tr>
              </thead>
              <tbody>
                {byModel.map((m) => (
                  <tr key={m.model} className="border-b border-slate-800/50">
                    <Td className="text-slate-300">{m.model}</Td>
                    <Td>{m.requests}</Td>
                    <Td>{fmtTokens(m.total_tokens)}</Td>
                    <Td>{fmtUsd(m.cost_usd)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="虚拟 key 预算">
        {spends.length === 0 ? (
          <Empty text="还没有虚拟 key" />
        ) : (
          <div className="space-y-3">
            {spends.map((s) => {
              const pct =
                s.monthly_budget_usd && s.monthly_budget_usd > 0
                  ? Math.min(100, (s.month_to_date_usd / s.monthly_budget_usd) * 100)
                  : 0
              return (
                <div key={s.key_id}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="text-slate-300">{s.name}</span>
                    <span className="text-slate-500">
                      {fmtUsd(s.month_to_date_usd)}
                      {s.monthly_budget_usd != null && ` / ${fmtUsd(s.monthly_budget_usd)}`}
                      <span className="ml-2 text-slate-600">{depletionDays(s)}</span>
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className={`h-full rounded-full ${
                        pct > 80 ? 'bg-rose-500' : pct > 50 ? 'bg-amber-500' : 'bg-cyan-500'
                      }`}
                      style={{ width: `${s.monthly_budget_usd != null ? pct : 0}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>
    </div>
  )
}
