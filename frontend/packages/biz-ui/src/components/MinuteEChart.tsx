import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

export interface MinutePoint {
  t: string
  price: number
  avg: number
  volume: number
}

interface Props {
  points: MinutePoint[]
  prevClose: number | null
  isIndex: boolean
}

/** ECharts 分时走势图(2026-08-10 替换手写SVG)。
 *
 * 标准金融分时样式: 价格线(红涨绿跌) + 均价线(黄, 个股) + 昨收基准虚线(±分界线)
 * + 下方成交量柱(红涨绿跌) + 十字光标联动。
 */
export default function MinuteEChart({ points, prevClose, isIndex }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    chartRef.current = chart
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || points.length === 0) return

    const prevC = prevClose ?? points[0]?.price ?? 0
    // 时间轴: 0930 → 1500, 过滤午休 1131-1259 保持紧凑
    const times = points.map(p => p.t)
    const prices = points.map(p => p.price)
    const avgs = points.map(p => p.avg)
    const vols = points.map(p => p.volume)
    const maxVol = Math.max(...vols, 1)

    // y 轴范围: 以昨收为对称中心 ± max(当日幅度, 昨收2.5%)
    const dayPrices = prices
    const dayLo = Math.min(...dayPrices)
    const dayHi = Math.max(...dayPrices)
    const baseRange = Math.max(
      (dayHi - dayLo) / 2,
      Math.abs(dayHi - prevC),
      Math.abs(prevC - dayLo),
      prevC * 0.025,
    )
    const yLo = prevC - baseRange
    const yHi = prevC + baseRange

    const colorUp = '#f43f5e'
    const colorDown = '#10b981'

    // 分时价格颜色: 整体涨红跌绿
    const last = prices[prices.length - 1]
    const first = prices[0]
    const up = last >= first
    const priceColor = up ? colorUp : colorDown

    chart.setOption(
      {
        animation: false,
        grid: [
          { left: 52, right: 16, top: 10, height: '62%' }, // 价格区
          { left: 52, right: 16, top: '76%', height: '18%' }, // 量区
        ],
        tooltip: {
          trigger: 'axis',
          axisPointer: { type: 'cross', crossStyle: { color: '#999' } },
          backgroundColor: 'rgba(17,24,39,0.92)',
          borderColor: '#334155',
          textStyle: { color: '#e2e8f0', fontSize: 11 },
          formatter: (params: any[]) => {
            const i = params[0]?.dataIndex ?? 0
            const t = times[i]
            const p = prices[i]
            const a = avgs[i]
            const v = vols[i]
            const pct = prevC ? (((p - prevC) / prevC) * 100).toFixed(2) : '0.00'
            const sign = p >= prevC ? '+' : ''
            return [
              `<b>${t}</b>`,
              `价格: <span style="color:${p >= prevC ? colorUp : colorDown}">${p.toFixed(2)} (${sign}${pct}%)</span>`,
              !isIndex ? `均价: ${a.toFixed(2)}` : '',
              `成交量: ${(v / 10000).toFixed(2)}万手`,
            ].filter(Boolean).join('<br/>')
          },
        },
        xAxis: {
          type: 'category',
          data: times,
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#334155' } },
          axisLabel: {
            color: '#94a3b8',
            fontSize: 10,
            formatter: (v: string) => (v === '0930' || v === '1130' || v === '1300' || v === '1500' ? v : ''),
          },
          axisTick: { show: false },
        },
        yAxis: [
          {
            type: 'value',
            min: yLo,
            max: yHi,
            splitNumber: 4,
            axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v: number) => v.toFixed(2) },
            splitLine: { lineStyle: { color: '#1e293b', type: 'dashed' } },
            axisLine: { show: false },
          },
          {
            type: 'value',
            gridIndex: 1,
            min: 0,
            max: maxVol * 1.2,
            splitNumber: 1,
            axisLabel: { show: false },
            splitLine: { show: false },
            axisLine: { show: false },
          },
        ],
        dataZoom: [
          { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100, zoomOnMouseWheel: false },
        ],
        series: [
          // 昨收基准线(±分界线)
          {
            name: '昨收',
            type: 'line',
            data: times.map(() => prevC),
            symbol: 'none',
            lineStyle: { color: '#94a3b8', width: 1, type: 'dashed' },
            z: 1,
            silent: true,
            tooltip: { show: false },
          },
          // 价格线
          {
            name: '价格',
            type: 'line',
            data: prices,
            symbol: 'none',
            lineStyle: { color: priceColor, width: 1.6 },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: up ? 'rgba(244,63,94,0.18)' : 'rgba(16,185,129,0.18)' },
                { offset: 1, color: 'rgba(0,0,0,0)' },
              ]),
            },
            z: 3,
          },
          // 均价线(个股才有)
          ...(isIndex
            ? []
            : [
                {
                  name: '均价',
                  type: 'line',
                  data: avgs,
                  symbol: 'none',
                  lineStyle: { color: '#f59e0b', width: 1.2 },
                  z: 2,
                },
              ]),
          // 成交量(下方)
          {
            name: '成交量',
            type: 'bar',
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: vols.map((v, i) => ({
              value: v,
              itemStyle: {
                color: prices[i] >= (i > 0 ? prices[i - 1] : prices[i]) ? colorUp : colorDown,
                opacity: 0.55,
              },
            })),
            barWidth: '60%',
          },
        ],
        legend: {
          show: false,
        },
      },
      true,
    )
  }, [points, prevClose, isIndex])

  return <div ref={ref} className="w-full h-[380px]" />
}
