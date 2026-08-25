/**
 * v0.4.8: 「终端感」ECharts 主题 — 对齐 SIDA 设计 token。
 * 用法: echarts.registerTheme('sida', sidaTheme) 后 init(dom, 'sida')。
 * 色值与 index.css 的 --border/--fg-muted 等语义 token 保持一致。
 */
export const SIDA_THEME = {
  backgroundColor: 'transparent',
  textStyle: { color: '#c4c4cb' },
  axisPointer: {
    lineStyle: { color: 'rgba(120,120,130,.4)' },
    crossStyle: { color: 'rgba(120,120,130,.4)' },
  },
  categoryAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: '#8e8e96', fontSize: 10 },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: '#8e8e96', fontSize: 10, fontFamily: 'monospace' },
    splitLine: { lineStyle: { color: 'rgba(120,120,130,.12)' } },
  },
  tooltip: {
    backgroundColor: 'rgba(24,24,27,.95)',
    borderColor: 'rgba(120,120,130,.3)',
    borderWidth: 1,
    textStyle: { color: '#fafafa', fontSize: 11 },
    extraCssText: 'backdrop-filter: blur(4px); border-radius: 6px;',
  },
}

export const SIDA_THEME_NAME = 'sida'
