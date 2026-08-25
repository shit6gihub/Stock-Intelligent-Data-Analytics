import { useEffect, useRef } from 'react'
import echarts from '@panwatch/biz-ui/lib/echarts-core'
import { SIDA_THEME_NAME } from '@panwatch/biz-ui/lib/echarts-theme'

/**
 * 市场温度仪表盘(v0.4.7, Dashboard 情绪周期区)。
 *
 * 温度分 = 高度×15 + 晋级率×40 + 封板率×45 (0-100)。
 * 半圆 gauge, 指针色随情绪阶段(与 MarketPhaseCard PHASE_STYLE 一致)。
 */

export interface GaugeMetrics {
  max_height: number | null
  promo_rate: number | null // 0-1 小数
  seal_rate: number | null // 0-1 小数
}

/** 阶段 → 指针色(对齐 MarketPhaseCard 配色语义) */
const PHASE_COLOR: Record<string, string> = {
  ice: '#3b82f6',
  ignite: '#06b6d4',
  rally: '#ef4444',
  climax: '#7f1d1d',
  ebb: '#f97316',
  repair: '#6b7280',
  accumulating: '#64748b',
}

const PHASE_LABEL: Record<string, string> = {
  ice: '冰点',
  ignite: '启动',
  rally: '主升',
  climax: '高潮',
  ebb: '退潮',
  repair: '修复',
  accumulating: '积累中',
}

export default function SentimentGauge({
  phase,
  metrics,
}: {
  phase: string | null
  metrics: GaugeMetrics | null
}) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    if (!chartRef.current) {
      chartRef.current = echarts.init(ref.current, SIDA_THEME_NAME)
    }
    const chart = chartRef.current

    const h = metrics?.max_height ?? 0
    const promo = metrics?.promo_rate ?? 0
    const seal = metrics?.seal_rate ?? 0
    const score = Math.min(100, Math.round(h * 15 + promo * 40 + seal * 45))
    const pointerColor = PHASE_COLOR[phase ?? 'accumulating'] ?? '#64748b'
    const label = phase ? (PHASE_LABEL[phase] ?? phase) : '--'

    chart.setOption({
      series: [
        {
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          min: 0,
          max: 100,
          radius: '95%',
          center: ['50%', '78%'],
          axisLine: {
            lineStyle: {
              width: 10,
              color: [
                [0.2, '#3b82f6'],
                [0.4, '#06b6d4'],
                [0.6, '#f97316'],
                [0.85, '#ef4444'],
                [1, '#7f1d1d'],
              ],
            },
          },
          pointer: {
            length: '62%',
            width: 4,
            itemStyle: { color: pointerColor },
          },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          detail: {
            offsetCenter: [0, '-18%'],
            fontSize: 20,
            fontFamily: 'monospace',
            fontWeight: 'bold',
            formatter: `{v|${score}}\n{n|市场温度 · ${label}}`,
            rich: {
              v: { fontSize: 22, fontWeight: 'bold', color: pointerColor },
              n: { fontSize: 9, color: '#8e8e96', padding: [4, 0, 0, 0] },
            },
          },
          data: [{ value: score }],
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [phase, metrics])

  useEffect(
    () => () => {
      chartRef.current?.dispose()
      chartRef.current = null
    },
    [],
  )

  return <div ref={ref} className="h-[140px] w-full" />
}
