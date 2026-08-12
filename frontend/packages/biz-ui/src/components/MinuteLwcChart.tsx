import { useEffect, useRef } from 'react'

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

function getLW(): any {
  return (window as any)?.LightweightCharts || null
}

function addLineSeries(chart: any, LW: any, options: any) {
  if (typeof chart?.addLineSeries === 'function') return chart.addLineSeries(options)
  if (typeof chart?.addSeries === 'function' && LW?.LineSeries) return chart.addSeries(LW.LineSeries, options)
  throw new Error('Line series API not available')
}

function addHistogramSeries(chart: any, LW: any, options: any) {
  if (typeof chart?.addHistogramSeries === 'function') return chart.addHistogramSeries(options)
  if (typeof chart?.addSeries === 'function' && LW?.HistogramSeries) return chart.addSeries(LW.HistogramSeries, options)
  throw new Error('Histogram series API not available')
}

/** "0930" -> 本地当天 09:30 的绝对时间戳(秒) */
function hhmmToTs(t: string): number {
  const h = parseInt(t.slice(0, 2), 10)
  const m = parseInt(t.slice(2, 4), 10)
  const now = new Date()
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 0)
  return Math.floor(d.getTime() / 1000)
}

/**
 * Lightweight Charts 分时走势图(2026-08-12 替换 ECharts)。
 *
 * 标准金融分时样式: 价格线(红涨绿跌) + 均价线(黄, 个股) + 昨收基准虚线(±分界线)
 * + 下方成交量柱(红涨绿跌, pane 1)。与日K 同一库(Lightweight v5), 砍掉 ECharts 依赖。
 * 午休时段(11:30-13:00)无数据点, 时间戳连续 → 天然留空隙, 无需 whitespace。
 */
export default function MinuteLwcChart({ points, prevClose, isIndex }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const LW = getLW()
    const el = ref.current
    if (!LW || !el || !points.length) return

    const rootStyle = getComputedStyle(document.documentElement)
    const bg = rootStyle.getPropertyValue('--card').trim()
    const fg = rootStyle.getPropertyValue('--foreground').trim()

    const colorUp = '#f43f5e'
    const colorDown = '#10b981'
    const prevC = prevClose ?? points[0]?.price ?? 0

    const chart = LW.createChart(el, {
      width: el.clientWidth,
      height: 300,
      layout: {
        background: { color: `hsl(${bg})` },
        textColor: `hsl(${fg} / 0.85)`,
      },
      rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        fixRightEdge: true,
        rightOffset: 1,
        barSpacing: 6,
        minBarSpacing: 1,
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
      },
      crosshair: { mode: 1 },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    })

    // 价格线(红涨绿跌按首尾方向) + 均价线
    const last = points[points.length - 1]?.price ?? 0
    const first = points[0]?.price ?? 0
    const priceColor = last >= first ? colorUp : colorDown

    const priceSeries = addLineSeries(chart, LW, {
      color: priceColor,
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    priceSeries.setData(points.map(p => ({ time: hhmmToTs(p.t), value: p.price })))

    if (!isIndex) {
      const avgSeries = addLineSeries(chart, LW, {
        color: 'rgba(245, 158, 11, 0.95)',
        lineWidth: 1,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      })
      avgSeries.setData(points.map(p => ({ time: hhmmToTs(p.t), value: p.avg })))
    }

    // 昨收虚线 = ±分界线(红线上方=涨, 下方=跌)
    priceSeries.createPriceLine?.({
      price: prevC,
      color: 'rgba(148, 163, 184, 0.8)',
      lineWidth: 1,
      lineStyle: 2,
      title: '',
    })

    // 量能柱(pane 1)
    const volSeries = addHistogramSeries(chart, LW, {
      priceFormat: { type: 'volume' },
    })
    volSeries.setData(
      points.map(p => ({
        time: hhmmToTs(p.t),
        value: p.volume,
        color: p.price >= prevC ? 'rgba(239, 68, 68, 0.4)' : 'rgba(16, 185, 129, 0.4)',
      }))
    )
    // 分时只显示最近约 2/3 区间(留右侧空白)
    const total = points.length
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, total - Math.floor(total * 0.75)), to: total + 8 })

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth })
    })
    ro.observe(el)

    return () => {
      ro.disconnect()
      try {
        chart.remove()
      } catch {
        /* ignore */
      }
    }
  }, [points, prevClose, isIndex])

  return <div ref={ref} className="w-full h-[300px]" />
}
