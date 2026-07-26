import { useEffect, useState } from 'react'
import { Database, ExternalLink, Sparkles, TrendingDown, Zap } from 'lucide-react'
import { api, fmtUsd } from '../api'
import type { BreakerState, CacheStats, Overview } from '../types'
import { Badge, Button, Card, Empty, StatCard, Table, Td, Tr } from '../components/ui'

const breakerLabel: Record<string, string> = { closed: '正常', open: '熔断中', half_open: '半开探测' }

export default function Insights() {
  const [cache, setCache] = useState<CacheStats | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [breakers, setBreakers] = useState<BreakerState[]>([])

  async function load() {
    const [c, o, b] = await Promise.all([api.statsCache(), api.overview(30), api.breakers()])
    setCache(c); setOverview(o); setBreakers(b)
  }

  useEffect(() => {
    load().catch(console.error)
    const timer = setInterval(() => load().catch(console.error), 10000)
    return () => clearInterval(timer)
  }, [])

  const totalHits = (cache?.exact.total_hits ?? 0) + (cache?.semantic.total_hits ?? 0)
  const avgCost =
    overview && overview.requests - overview.cache_hits > 0
      ? overview.cost_usd / (overview.requests - overview.cache_hits)
      : 0
  const saved = totalHits * avgCost

  return (
    <div className="space-y-5">
      <p className="text-[12.5px] text-ink-mid">
        缓存命中率、省钱金额、降级次数全部来自真实流量计量,不是压测数据
      </p>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="精确缓存命中" value={cache?.exact.total_hits ?? '—'}
          sub={`当前 ${cache?.exact.entries ?? 0} 条有效缓存`} tone="brand"
          icon={<Database size={15} />} delay={0} />
        <StatCard label="语义缓存命中" value={cache?.semantic.total_hits ?? '—'}
          sub={`当前 ${cache?.semantic.entries ?? 0} 条向量`} tone="good"
          icon={<Sparkles size={15} />} delay={40} />
        <StatCard label="近 30 天命中率"
          value={overview ? `${(overview.cache_hit_rate * 100).toFixed(1)}%` : '—'}
          tone="good" icon={<Zap size={15} />} delay={80} />
        <StatCard label="估算节省" value={fmtUsd(saved)}
          sub="命中次数 × 平均每请求成本" tone="warn"
          icon={<TrendingDown size={15} />} delay={120} />
      </div>

      {overview?.downgraded ? (
        <Card title="难度感知路由" desc="简单请求自动降级到便宜模型,成本按实际路由模型计价" delay={160}>
          <div className="flex items-baseline gap-3">
            <span className="tnum text-[28px] font-bold grad-text">{overview.downgraded}</span>
            <span className="text-[13px] text-ink-mid">
              次请求在近 30 天内被降级(占总量 {((overview.downgraded / Math.max(1, overview.requests)) * 100).toFixed(1)}%)
            </span>
          </div>
        </Card>
      ) : null}

      <Card title="渠道熔断状态" desc="滑动窗口错误率 → OPEN → 半开探测 → 恢复" pad={false} delay={200}>
        {breakers.length === 0 ? (
          <Empty text="所有渠道尚未产生熔断窗口数据" hint="有请求流经后这里会显示实时错误率" />
        ) : (
          <Table head={['渠道', '状态', '窗口请求', '窗口失败', '错误率', '历史熔断', '冷却剩余', '']}>
            {breakers.map((b) => (
              <Tr key={b.channel_id}>
                <Td className="font-medium text-ink-hi">{b.channel_name}</Td>
                <Td><Badge kind={b.state}>{breakerLabel[b.state]}</Badge></Td>
                <Td mono>{b.window_requests}</Td>
                <Td mono>{b.window_failures}</Td>
                <Td mono className={b.error_rate > 0.3 ? 'text-alert' : ''}>
                  {(b.error_rate * 100).toFixed(1)}%
                </Td>
                <Td mono>{b.opened_count}</Td>
                <Td mono className="text-ink-mid">
                  {b.cooldown_remaining_s > 0 ? `${b.cooldown_remaining_s}s` : '—'}
                </Td>
                <Td>
                  {b.state !== 'closed' && (
                    <Button onClick={() => api.resetBreaker(b.channel_id).then(load)}>复位</Button>
                  )}
                </Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="基础设施指标" delay={240}>
        <div className="flex items-start justify-between gap-4">
          <p className="text-[12.5px] leading-relaxed text-ink-mid">
            请求量、延迟分布、首字延迟、token、成本、熔断状态都以 Prometheus 格式暴露,接 Grafana 即用。
            <br />
            业务操作在本控制台,基础设施观测走 Grafana——该自研自研、该用现成用现成。
          </p>
          <a href="/metrics" target="_blank" rel="noreferrer">
            <Button><ExternalLink size={14} />打开 /metrics</Button>
          </a>
        </div>
      </Card>
    </div>
  )
}
