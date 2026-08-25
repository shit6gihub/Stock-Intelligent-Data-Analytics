import { useEffect, useRef, useState } from 'react'
import echarts from '@panwatch/biz-ui/lib/echarts-core'
import { SIDA_THEME_NAME } from '@panwatch/biz-ui/lib/echarts-theme'
import { fetchAPI } from '@panwatch/api'

/**
 * 全市场涨跌分布双向柱状图(v0.4.7, Dashboard 大盘区)。
 *
 * 数据: GET /market-data/breadth-distribution
 *   → {count, total, items: [{bucket, count}...9档], note}
 * 60s 轮询(与后端 biz_cache TTL 对齐)。以 0 为中心左绿右红,
 * 桶顺序[跌停..< -5%..涨停], 条上显数值。
 */

interface BreadthItem {
  bucket: string
  count: number
}
interface BreadthResp {
  count: number
  total: number
  items: BreadthItem[]
  note?: string
}

/** 桶 → 颜色: 负向绿 / 正向红 / 平盘灰(A股惯例) */
function bucketColor(bucket: string): string {
  if (bucket === '-1~1%') return '#6b7280'
  const neg = ['跌停', '<-5%', '-5~-3%', '-3~-1%']
  return neg.includes(bucket) ? '#10b981' : '#ef4444'
}

export default function BreadthDistributionChart() {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const [items, setItems] = useState<BreadthItem[] | null>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const res = await fetchAPI<BreadthResp>('/market-data/breadth-distribution')
        if (!alive) return
        setItems(res?.items ?? [])
        setNote(res?.note ?? '')
      } catch {
        /* 接口失败静默 — 显示空态 */
        if (alive) setItems((prev) => prev ?? [])
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), 60000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!ref.current || !items || items.length === 0) return
    if (!chartRef.current) {
      chartRef.current = echarts.init(ref.current, SIDA_THEME_NAME)
    }
    const chart = chartRef.current
    // 类目轴从下到上 = 从跌停到涨停(数组反转使涨停在顶部)
    const ordered = [...items].reverse()
    chart.setOption({
      grid: { left: 64, right: 40, top: 4, bottom: 4 },
      xAxis: { type: 'value', show: false },
      yAxis: {
        type: 'category',
        data: ordered.map((i) => i.bucket),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { fontSize: 10, color: '#8e8e96' },
      },
      series: [
        {
          type: 'bar',
          data: ordered.map((i) => ({
            value: i.count,
            itemStyle: { color: bucketColor(i.bucket), borderRadius: 2 },
          })),
          barWidth: '55%',
          label: {
            show: true,
            position: 'right',
            fontSize: 10,
            fontFamily: 'monospace',
            color: '#c4c4cb',
          },
        },
      ],
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [items])

  useEffect(() => {
    // 卸载时销毁实例
    return () => {
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [])

  if (items === null) {
    return <div className="h-[160px] animate-pulse rounded-lg bg-accent/10" />
  }
  if (items.length === 0 || items.every((i) => i.count === 0)) {
    return (
      <div className="flex h-[160px] items-center justify-center text-[11px] text-muted-foreground">
        {note || '暂无分布数据'}
      </div>
    )
  }
  return (
    <div>
      <div ref={ref} className="h-[160px] w-full" />
    </div>
  )
}
