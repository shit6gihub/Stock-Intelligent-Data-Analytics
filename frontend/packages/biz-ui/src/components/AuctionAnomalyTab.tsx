import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, UserPlus, Eye } from 'lucide-react'
import { fetchAPI, stocksApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

/**
 * 竞价异动池 Tab(2026-08-20, v0.3.0)
 * 挂在机会页 Opportunities 的竞价异动 Tab 内。
 * 数据 GET /api/auction/anomaly?market=CN (fetchAPI 自动补 /api 前缀)。
 *
 * 口径:
 *   - gap_pct       竞价相对昨收涨幅%(thsdk 集合竞价快照)。
 *   - withdraw_rate 撤单率%(竞价撤单占委托比例)。
 *   - volume_ratio  竞价量比(竞价成交量相对近期均量)。
 *   - 数据为每日 09:25 集合竞价拍出的异动快照(后台 cron 落库), 顶部标注同步时间。
 * 行色: 高开 ≥+3% 染红, 低开 ≤-3% 染绿(红=上涨, 绿=下跌, A股口径)。
 * 交互: 行点击“查看详情”(onOpenDetail 回调, 由宿主注入); "+自选" 调 stocksApi.create。
 * 30s 轮询刷新。
 */

export interface AuctionAnomalyItem {
  code?: string
  symbol?: string
  name?: string
  gap_pct?: number | null
  withdraw_rate?: number | null
  volume_ratio?: number | null
  note?: string
  [key: string]: unknown
}

export interface AuctionAnomalyResp {
  available: boolean
  count: number
  records: AuctionAnomalyItem[]
  note?: string
}

interface AuctionAnomalyTabProps {
  market?: string
  /** 点击行“查看详情”回调(宿主注入, 挂载 StockInsightModal) */
  onOpenDetail?: (symbol: string, market: string, name?: string) => void
}

function fmtPct(v: number | null | undefined, plus = true): string {
  if (v == null || !Number.isFinite(v)) return '--'
  const sign = plus ? (v > 0 ? '+' : '') : ''
  return `${sign}${v.toFixed(2)}%`
}

function gapRowClass(gap: number | null | undefined): string {
  if (gap == null || !Number.isFinite(gap)) return ''
  if (gap >= 3) return 'bg-[rgba(239,68,68,0.08)]'
  if (gap <= -3) return 'bg-emerald-500/10'
  return ''
}

function gapColor(gap: number | null | undefined): string {
  if (gap == null || !Number.isFinite(gap)) return 'text-muted-foreground'
  return gap > 0 ? 'text-rose-600 dark:text-rose-400' : gap < 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-muted-foreground'
}

export default function AuctionAnomalyTab({ market = 'CN', onOpenDetail }: AuctionAnomalyTabProps) {
  const { toast } = useToast()
  const [data, setData] = useState<AuctionAnomalyResp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState<Set<string>>(new Set())
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    setError('')
    try {
      const res = await fetchAPI<AuctionAnomalyResp>(`/auction/anomaly?market=${encodeURIComponent(market)}`, {
        cacheMode: 'reload',
      })
      if (mountedRef.current) setData(res)
      if (mountedRef.current) setUpdatedAt(new Date())
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : '竞价异动池加载失败')
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [market])

  useEffect(() => {
    mountedRef.current = true
    setLoading(true)
    void load()
    const timer = window.setInterval(() => void load(), 30000) // 30s 刷新
    return () => {
      mountedRef.current = false
      window.clearInterval(timer)
    }
  }, [load])

  const addToWatchlist = useCallback(
    async (item: AuctionAnomalyItem) => {
      const code = String(item.symbol || item.code || '').trim()
      if (!code) return
      if (adding.has(code)) return
      const next = new Set(adding)
      next.add(code)
      setAdding(next)
      try {
        await stocksApi.create({ symbol: code, name: String(item.name || code).trim(), market })
        toast(`已加入自选 ${item.name || code}`, 'success')
      } catch (e) {
        toast(e instanceof Error ? e.message : '加入自选失败', 'error')
      } finally {
        setAdding((prev) => {
          const n = new Set(prev)
          n.delete(code)
          return n
        })
      }
    },
    [adding, market, toast]
  )

  const records = data?.records ?? []
  const total = data?.count ?? records.length
  const syncAt = updatedAt
    ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false })
    : '--'

  return (
    <div className="card p-3 md:p-4">
      {/* 顶栏: 09:25 同步时间 + 总数 + 刷新 */}
      <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Eye className="w-3.5 h-3.5" />
          <span>
            09:25 集合竞价异动池 · 同步 <span className="font-mono text-foreground">{syncAt}</span>
          </span>
          <span className="opacity-40">|</span>
          <span>
            共 <span className="font-mono text-foreground">{total}</span> 只
          </span>
          <span className="text-[10px] text-muted-foreground/70">口径: thsdk 竞价</span>
        </div>
        <Button variant="secondary" size="sm" className="h-7 text-[12px]" onClick={() => { setLoading(true); void load() }} disabled={loading}>
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </Button>
      </div>

      {error && <div className="text-[11px] text-amber-600 dark:text-amber-500 mb-2">{error}</div>}
      {data && !data.available && <div className="text-[11px] text-muted-foreground mb-2">{data.note || '竞价异动数据暂不可用'}</div>}

      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[11px] text-muted-foreground border-b border-border/60">
              <th className="text-left py-1.5 pr-2 w-9">#</th>
              <th className="text-left py-1.5 pr-2">代码</th>
              <th className="text-left py-1.5 pr-2">名称</th>
              <th className="text-right py-1.5 pr-2">竞价涨幅</th>
              <th className="text-right py-1.5 pr-2">撤单率</th>
              <th className="text-right py-1.5 pr-2">竞价量能</th>
              <th className="text-right py-1.5">操作</th>
            </tr>
          </thead>
          <tbody>
            {records.map((r, i) => {
              const code = String(r.symbol || r.code || '')
              const name = String(r.name || code || '')
              const gap = typeof r.gap_pct === 'number' ? r.gap_pct : null
              const withdraw = typeof r.withdraw_rate === 'number' ? r.withdraw_rate : null
              const vol = typeof r.volume_ratio === 'number' ? r.volume_ratio : null
              const isAdding = !!code && adding.has(code)
              return (
                <tr
                  key={code || i}
                  className={`border-b border-border/30 hover:bg-accent/40 ${gapRowClass(gap)}`}
                >
                  <td className="py-1.5 pr-2 text-[10px] text-muted-foreground">{i + 1}</td>
                  <td
                    className="py-1.5 pr-2 font-mono text-muted-foreground cursor-pointer hover:text-primary"
                    title="查看详情"
                    onClick={() => code && onOpenDetail?.(code, market, name)}
                  >
                    {code}
                  </td>
                  <td
                    className="py-1.5 pr-2 font-medium text-foreground cursor-pointer hover:text-primary"
                    title="查看详情"
                    onClick={() => code && onOpenDetail?.(code, market, name)}
                  >
                    {name}
                  </td>
                  <td className={`py-1.5 pr-2 text-right font-mono tabular-nums ${gapColor(gap)}`}>{fmtPct(gap)}</td>
                  <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-muted-foreground">{withdraw != null && Number.isFinite(withdraw) ? `${withdraw.toFixed(1)}%` : '--'}</td>
                  <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-foreground">{vol != null && Number.isFinite(vol) ? `${vol.toFixed(2)}x` : '--'}</td>
                  <td className="py-1.5 text-right">
                    {code ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-1.5 text-[10px]"
                        disabled={isAdding}
                        onClick={() => void addToWatchlist(r)}
                      >
                        {isAdding ? (
                          <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                        ) : (
                          <UserPlus className="w-3 h-3" />
                        )}
                        自选
                      </Button>
                    ) : (
                      <span className="inline-block w-6" />
                    )}
                  </td>
                </tr>
              )
            })}
            {records.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="py-8 text-center text-[11px] text-muted-foreground">
                  暂无竞价异动 (非交易时段 / 数据源未接入 / 无高波动标的)
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {loading && records.length === 0 && (
        <div className="mt-3 h-[120px] rounded-xl border border-border/50 animate-pulse bg-accent/20" />
      )}
    </div>
  )
}
