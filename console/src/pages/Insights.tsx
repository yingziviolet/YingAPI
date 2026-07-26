import { useEffect, useState } from 'react'
import { api, fmtUsd } from '../api'
import type { BreakerState, CacheStats, Overview } from '../types'
import { Badge, Button, Card, Empty, StatCard, Td, Th } from '../components/ui'

const breakerLabel: Record<string, string> = {
  closed: '正常',
  open: '熔断中',
  half_open: '半开探测',
}

export default function Insights() {
  const [cache, setCache] = useState<CacheStats | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [breakers, setBreakers] = useState<BreakerState[]>([])

  async function load() {
    const [c, o, b] = await Promise.all([api.statsCache(), api.overview(30), api.breakers()])
    setCache(c)
    setOverview(o)
    setBreakers(b)
  }

  useEffect(() => {
    load().catch(console.error)
    const timer = setInterval(() => load().catch(console.error), 10000)
    return () => clearInterval(timer)
  }, [])

  const totalHits = (cache?.exact.total_hits ?? 0) + (cache?.semantic.total_hits ?? 0)
  // 省钱估算:近 30 天平均每请求成本 x 命中数(命中 = 免掉一次上游调用)
  const avgCost =
    overview && overview.requests - overview.cache_hits > 0
      ? overview.cost_usd / (overview.requests - overview.cache_hits)
      : 0
  const saved = totalHits * avgCost

  return (
    <div className="space-y-4">
      <div className="text-sm text-slate-500">
        智能调度层成绩单——缓存命中率与省钱金额来自真实流量计量,不是压测编的
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="精确缓存"
          value={cache ? `${cache.exact.total_hits} 次命中` : '—'}
          sub={`${cache?.exact.entries ?? 0} 条缓存`}
          accent="text-cyan-400"
        />
        <StatCard
          label="语义缓存"
          value={cache ? `${cache.semantic.total_hits} 次命中` : '—'}
          sub={`${cache?.semantic.entries ?? 0} 条向量`}
          accent="text-emerald-400"
        />
        <StatCard
          label="缓存命中率(30天)"
          value={overview ? `${(overview.cache_hit_rate * 100).toFixed(1)}%` : '—'}
          accent="text-emerald-400"
        />
        <StatCard
          label="估算省下"
          value={fmtUsd(saved)}
          sub="命中数 × 平均每请求成本"
          accent="text-amber-400"
        />
      </div>

      <Card title="渠道熔断状态(滑动窗口错误率 → OPEN → 半开探测 → 恢复)">
        {breakers.length === 0 ? (
          <Empty text="所有渠道还没有产生熔断窗口数据" />
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-slate-800">
                <Th>渠道</Th><Th>状态</Th><Th>窗口请求</Th><Th>窗口失败</Th>
                <Th>错误率</Th><Th>历史熔断</Th><Th>冷却剩余</Th><Th></Th>
              </tr>
            </thead>
            <tbody>
              {breakers.map((b) => (
                <tr key={b.channel_id} className="border-b border-slate-800/50">
                  <Td className="text-slate-300">{b.channel_name}</Td>
                  <Td><Badge kind={b.state}>{breakerLabel[b.state]}</Badge></Td>
                  <Td>{b.window_requests}</Td>
                  <Td>{b.window_failures}</Td>
                  <Td className={b.error_rate > 0.3 ? 'text-rose-400' : ''}>
                    {(b.error_rate * 100).toFixed(1)}%
                  </Td>
                  <Td>{b.opened_count} 次</Td>
                  <Td>{b.cooldown_remaining_s > 0 ? `${b.cooldown_remaining_s}s` : '—'}</Td>
                  <Td>
                    {b.state !== 'closed' && (
                      <Button onClick={() => api.resetBreaker(b.channel_id).then(load)}>复位</Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Prometheus">
        <div className="text-sm text-slate-500">
          基础设施指标在{' '}
          <a href="/metrics" target="_blank" className="text-cyan-400 hover:underline">
            /metrics
          </a>{' '}
          端点(请求量/延迟分布/首字延迟/token/成本/熔断状态),接 Grafana 即用——业务操作在本控制台,
          基础设施观测走 Grafana,该自研自研、该用现成用现成。
        </div>
      </Card>
    </div>
  )
}
