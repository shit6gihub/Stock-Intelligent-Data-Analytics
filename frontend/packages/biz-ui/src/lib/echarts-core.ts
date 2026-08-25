import * as echarts from 'echarts/core'
import { BarChart, GaugeChart, LineChart, CandlestickChart, HeatmapChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, MarkLineComponent,
  LegendComponent, DataZoomComponent, VisualMapComponent, TitleComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

// v0.4.8: ECharts 按需注册(全量引入 ~1MB → 按需 ~400KB)
// 新图表类型/组件在这里追加注册
echarts.use([
  BarChart, GaugeChart, LineChart, CandlestickChart, HeatmapChart,
  GridComponent, TooltipComponent, MarkLineComponent,
  LegendComponent, DataZoomComponent, VisualMapComponent, TitleComponent,
  CanvasRenderer,
])

export default echarts
