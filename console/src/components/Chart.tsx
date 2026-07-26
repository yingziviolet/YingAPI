import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { isDark } from '../theme'

export default function Chart({ option, height = 280 }: {
  option: echarts.EChartsOption
  height?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(ref.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={ref} style={{ height }} />
}

/** 图表配色随主题取值。grid 上下留位避免图例压住轴标签。 */
export function chartTheme() {
  const dark = isDark()
  return {
    grid: { left: 52, right: 20, top: 46, bottom: 34 },
    textStyle: { color: dark ? '#94a3b8' : '#64748b', fontFamily: 'Sora Variable, sans-serif' },
    axisLine: { lineStyle: { color: dark ? '#262f3f' : '#e2e8f2' } },
    splitLine: { lineStyle: { color: dark ? '#1e2532' : '#eef1f7', type: 'dashed' as const } },
  }
}

export function chartLegend() {
  return {
    top: 2,
    itemWidth: 10,
    itemHeight: 10,
    itemGap: 16,
    textStyle: { color: isDark() ? '#94a3b8' : '#64748b', fontSize: 11.5 },
  }
}

export function chartTooltip() {
  const dark = isDark()
  return {
    trigger: 'axis' as const,
    backgroundColor: dark ? '#171c26' : '#ffffff',
    borderColor: dark ? '#262f3f' : '#e2e8f2',
    borderWidth: 1,
    padding: [8, 12] as [number, number],
    textStyle: { color: dark ? '#f1f5f9' : '#0f172a', fontSize: 12 },
    extraCssText: 'box-shadow: 0 8px 24px rgba(15,23,42,0.12); border-radius: 10px;',
  }
}

/** 品牌渐变柱:蓝→青 */
export function barGradient(from = '#2563eb', to = '#06b6d4') {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: from },
    { offset: 1, color: to },
  ])
}

export const PALETTE = {
  brand: '#2563eb',
  brand2: '#06b6d4',
  good: '#10b981',
  warn: '#f59e0b',
  alert: '#ef4444',
  flux: '#8b5cf6',
}
