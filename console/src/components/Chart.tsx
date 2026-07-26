import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { isLight } from '../theme'

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

// 图表颜色按当前主题取值(渲染时调用)
export function chartTheme() {
  const light = isLight()
  return {
    // top 给图例留位,bottom 给 X 轴标签留位,二者不再互相挡
    grid: { left: 48, right: 16, top: 44, bottom: 36 },
    textStyle: { color: light ? '#475569' : '#94a3b8' },
    axisLine: { lineStyle: { color: light ? '#cbd5e1' : '#334155' } },
    splitLine: { lineStyle: { color: light ? '#e8ecf1' : '#1e293b' } },
  }
}

// 图例统一固定在图表顶部(默认位置在数据少时会飘到轴标签上,挡字)
export function chartLegend() {
  return { top: 0, textStyle: { color: isLight() ? '#475569' : '#94a3b8' } }
}
