import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, RefreshCw, Share2, Sparkles, ScanSearch, ThumbsDown, ThumbsUp, Download } from 'lucide-react'
import {
  getToken,
  recommendationsApi,
  stocksApi,
  strategiesApi,
  tdxApi,
  type EntryCandidateItem,
  type ScanItem,
  type StrategyCatalogItem,
  type StrategyItem,
  type StrategySignalItem,
  type StrategyStatsResponse,
  type TdxAskResponse,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { useLocalStorage } from '@/lib/utils'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'
import FactorWeightsPanel from '@/components/FactorWeightsPanel'
import SignalScoreShareCard from '@/components/SignalScoreShareCard'

type SourceFilter = 'all' | 'market_scan' | 'watchlist' | 'mixed'
type HoldingFilter = 'all' | 'held' | 'unheld'
type RiskFilter = 'all' | 'low' | 'medium' | 'high'

type GroupedSignal = {
  key: string
  primary: StrategySignalItem
  members: StrategySignalItem[]
  strategyNames: string[]
  sourceAgents: string[]
  hasMarketScan: boolean
  topScore: number
}

const marketLabel = (m?: string) => {
  if (m === 'HK') return '港股'
  if (m === 'US') return '美股'
  return 'A股'
}

const sourceAgentLabelMap: Record<string, string> = {
  premarket_outlook: '盘前分析',
  intraday_monitor: '盘中监测',
  daily_report: '收盘复盘',
  news_digest: '新闻速递',
  market_scan: '市场扫描',
}

const sourceAgentLabel = (agent?: string) => {
  const key = (agent || '').trim()
  if (!key) return '--'
  return sourceAgentLabelMap[key] || key
}

const formatPlanPrice = (value: number | null | undefined) => {
  if (value == null || Number.isNaN(value)) return '--'
  const abs = Math.abs(value)
  const fixed = abs >= 100 ? 2 : abs >= 1 ? 3 : 4
  return Number(value).toFixed(fixed).replace(/\.?0+$/, '')
}

const toNumberOrNull = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const num = Number(value)
    if (Number.isFinite(num)) return num
  }
  return null
}

const sleep = (ms: number) => new Promise<void>((resolve) => {
  window.setTimeout(resolve, ms)
})

const formatMetric = (value: unknown, digits = 1) => {
  const n = toNumberOrNull(value)
  if (n == null) return '--'
  return n.toFixed(digits)
}

const DEFAULT_FILTERS = {
  market: 'ALL' as const,
  source: 'all' as const,
  holding: 'unheld' as const,
  strategy: 'all',
  risk: 'all' as const,
  minScore: '70',
}

const toneClass = (item: StrategySignalItem) => {
  const action = (item.action || '').toLowerCase()
  const score = Number(item.rank_score || item.score || 0)
  if (action === 'buy') {
    return 'border-l-2 border-l-rose-600/70'
  }
  if (action === 'add') {
    return 'border-l-2 border-l-emerald-600/70'
  }
  if (score >= 85) {
    return 'border-l-2 border-l-primary/70'
  }
  return ''
}

const actionBadgeClass = (action?: string) => {
  const key = (action || '').toLowerCase()
  if (key === 'buy') return 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/35'
  if (key === 'add') return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/35'
  if (key === 'hold') return 'bg-blue-500/15 text-blue-700 dark:text-blue-400 border border-blue-500/35'
  return 'bg-accent text-muted-foreground border border-border/50'
}

const displayActionLabel = (item: StrategySignalItem) => {
  const action = (item.action || '').toLowerCase()
  if (!item.is_holding_snapshot && action === 'hold') return '观望'
  if (!item.is_holding_snapshot && action === 'add') return '建仓'
  return item.action_label || item.action
}

const scoreOf = (item: StrategySignalItem) => Number(item.rank_score || item.score || 0)

const actionPriority = (item: StrategySignalItem) => {
  const key = (item.action || '').toLowerCase()
  if (key === 'buy') return 4
  if (key === 'add') return 3
  if (key === 'hold') return item.is_holding_snapshot ? 2 : 1
  return 0
}

const hasEntryPlan = (item: StrategySignalItem) => {
  const breakdown = item.score_breakdown || {}
  if (typeof breakdown.has_entry_plan === 'boolean') return breakdown.has_entry_plan
  return toNumberOrNull(item.entry_low) != null || toNumberOrNull(item.entry_high) != null
}

const itemTimestamp = (item: StrategySignalItem) => {
  const t = Date.parse(item.updated_at || item.created_at || '')
  return Number.isFinite(t) ? t : 0
}

const shouldReplacePrimary = (next: StrategySignalItem, current: StrategySignalItem) => {
  const activeDelta = Number((next.status || '').toLowerCase() === 'active') - Number((current.status || '').toLowerCase() === 'active')
  if (activeDelta !== 0) return activeDelta > 0
  const actionDelta = actionPriority(next) - actionPriority(current)
  if (actionDelta !== 0) return actionDelta > 0
  const entryDelta = Number(hasEntryPlan(next)) - Number(hasEntryPlan(current))
  if (entryDelta !== 0) return entryDelta > 0
  const scoreDelta = scoreOf(next) - scoreOf(current)
  if (Math.abs(scoreDelta) > 0.001) return scoreDelta > 0
  return itemTimestamp(next) > itemTimestamp(current)
}

const toSignalFromCandidate = (row: EntryCandidateItem): StrategySignalItem => {
  const source = row.candidate_source || 'watchlist'
  const sourceLabel = row.candidate_source_label || (source === 'market_scan' ? '市场池' : source === 'mixed' ? '市场+关注' : '关注池')
  const riskLevel: 'low' | 'medium' | 'high' = Number(row.score || 0) >= 85 ? 'high' : Number(row.score || 0) >= 70 ? 'medium' : 'low'
  const riskLabel = riskLevel === 'high' ? '高风险' : riskLevel === 'low' ? '低风险' : '中风险'
  return {
    id: Number(row.id || 0),
    snapshot_date: row.snapshot_date || '',
    stock_symbol: row.stock_symbol,
    stock_market: row.stock_market || 'CN',
    stock_name: row.stock_name || row.stock_symbol,
    strategy_code: (row.strategy_tags && row.strategy_tags[0]) || 'watchlist_agent',
    strategy_name: (row.strategy_labels && row.strategy_labels[0]) || '候选建议',
    strategy_version: 'v1',
    risk_level: riskLevel,
    risk_level_label: riskLabel,
    source_pool: source,
    source_pool_label: sourceLabel,
    score: Number(row.score || 0),
    rank_score: Number(row.score || 0),
    confidence: row.confidence ?? null,
    status: row.status || 'inactive',
    action: row.action || 'watch',
    action_label: row.action_label || '观望',
    signal: row.signal || '',
    reason: row.reason || '',
    evidence: row.evidence || [],
    holding_days: 3,
    entry_low: row.entry_low ?? null,
    entry_high: row.entry_high ?? null,
    stop_loss: row.stop_loss ?? null,
    target_price: row.target_price ?? null,
    invalidation: row.invalidation || '',
    plan_quality: row.plan_quality ?? 0,
    source_agent: row.source_agent || '',
    source_suggestion_id: row.source_suggestion_id ?? null,
    source_candidate_id: row.id ?? null,
    trace_id: '',
    is_holding_snapshot: !!row.is_holding_snapshot,
    context_quality_score: null,
    score_breakdown: {
      weighted_score: Number(row.score || 0),
      has_entry_plan: !!(row.entry_low != null || row.entry_high != null),
    },
    market_regime: {},
    cross_feature: {},
    news_metric: {},
    constrained: false,
    constraint_reasons: [],
    payload: {
      source_meta: {
        plan: row.plan || {},
      },
    },
    created_at: row.created_at || '',
    updated_at: row.updated_at || row.created_at || '',
  }
}

const formatEntryDisplay = (action: string | undefined, entryLow: number | null, entryHigh: number | null) => {
  if (entryLow != null || entryHigh != null) {
    return `${formatPlanPrice(entryLow)} ~ ${formatPlanPrice(entryHigh)}`
  }
  const key = (action || '').toLowerCase()
  if (key === 'buy' || key === 'add') return '待补充入场位'
  return '当前不建议开仓'
}

const regimeToneClass = (regime?: string) => {
  if (regime === 'bullish') return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/30'
  if (regime === 'bearish') return 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border border-rose-500/30'
  return 'bg-amber-500/12 text-amber-700 dark:text-amber-300 border border-amber-500/25'
}

export default function OpportunitiesPage() {
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  // 防重复提交锁:提交/轮询期间置 true,轮询结束或同步失败后复位
  const refreshingRef = useRef(false)
  const [error, setError] = useState('')
  const [items, setItems] = useState<StrategySignalItem[]>([])
  const [stats, setStats] = useState<StrategyStatsResponse | null>(null)
  const [strategyCatalog, setStrategyCatalog] = useState<StrategyCatalogItem[]>([])
  const [watchlist, setWatchlist] = useState<Set<string>>(new Set())

  const [market, setMarket] = useLocalStorage<'ALL' | 'CN' | 'HK' | 'US'>('panwatch_opportunities_market_v3', DEFAULT_FILTERS.market)
  const [source, setSource] = useLocalStorage<SourceFilter>('panwatch_opportunities_source_v3', DEFAULT_FILTERS.source)
  const [holding, setHolding] = useLocalStorage<HoldingFilter>('panwatch_opportunities_holding_v3', DEFAULT_FILTERS.holding)
  const [strategy, setStrategy] = useLocalStorage('panwatch_opportunities_strategy_v3', DEFAULT_FILTERS.strategy)
  const [risk, setRisk] = useLocalStorage<RiskFilter>('panwatch_opportunities_risk_v3', DEFAULT_FILTERS.risk)
  const [minScore, setMinScore] = useLocalStorage('panwatch_opportunities_min_score_v3', DEFAULT_FILTERS.minScore)
  const [sector, setSector] = useLocalStorage('panwatch_opportunities_sector_v3', '')
  const [sectorOpen, setSectorOpen] = useState(false)
  const [sectorQuery, setSectorQuery] = useState('')
  const [sectorResults, setSectorResults] = useState<{ code: string; name: string }[]>([])
  const [snapshotDate, setSnapshotDate] = useState('')

  const [insightOpen, setInsightOpen] = useState(false)
  const [insightSymbol, setInsightSymbol] = useState('')
  const [insightMarket, setInsightMarket] = useState('CN')
  const [insightName, setInsightName] = useState<string | undefined>(undefined)
  const [insightHasPosition, setInsightHasPosition] = useState(false)

  // 个股 AI 评分分享卡:当前分享的信号
  const [shareSignal, setShareSignal] = useState<StrategySignalItem | null>(null)

  // ── 候选反馈(有用/没用) ──
  // key: `${stock_market}:${stock_symbol}` → 最新一次反馈的 useful 值
  const { toast } = useToast()
  const [feedbackMap, setFeedbackMap] = useState<Record<string, boolean>>({})
  const [feedbackPending, setFeedbackPending] = useState<Set<string>>(new Set())

  const loadFeedback = useCallback(async (snapDate: string, rows: StrategySignalItem[]) => {
    if (!snapDate || rows.length === 0) return
    try {
      const res = await recommendationsApi.listEntryCandidateFeedback({ snapshot_date: snapDate, limit: 500 })
      const map: Record<string, boolean> = {}
      for (const fb of res.items || []) {
        const key = `${fb.stock_market || 'CN'}:${fb.stock_symbol}`
        map[key] = !!fb.useful
      }
      setFeedbackMap(map)
    } catch {
      // 反馈状态加载失败不阻塞页面
    }
  }, [])

  const handleCandidateFeedback = useCallback(async (item: StrategySignalItem, useful: boolean) => {
    const key = `${item.stock_market || 'CN'}:${item.stock_symbol}`
    if (feedbackMap[key] === useful) return // 已反馈相同值, 忽略重复提交
    if (feedbackPending.has(key)) return
    const nextPending = new Set(feedbackPending)
    nextPending.add(key)
    setFeedbackPending(nextPending)
    try {
      const res = await recommendationsApi.feedbackEntryCandidate({
        snapshot_date: item.snapshot_date || '',
        stock_symbol: item.stock_symbol,
        stock_market: item.stock_market || 'CN',
        useful,
        candidate_source: item.source_pool || 'watchlist',
        strategy_tags: item.strategy_code ? [item.strategy_code] : [],
      })
      if (res.ok) {
        setFeedbackMap((prev) => ({ ...prev, [key]: useful }))
        toast(useful ? '已标记为有用' : '已标记为没用', 'success')
      } else {
        toast('反馈提交失败，请稍后重试', 'error')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '反馈提交失败，请稍后重试', 'error')
    } finally {
      setFeedbackPending((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    }
  }, [feedbackMap, feedbackPending, toast])

  // ── 策略选股(策略库批量扫描) ──
  const [scanStrategies, setScanStrategies] = useState<StrategyItem[]>([])
  const [scanStrategyId, setScanStrategyId] = useState('')
  const [scanUniverse, setScanUniverse] = useState<'all' | 'watchlist'>('all')
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState<{ items: ScanItem[]; total: number; scanned: number; quoted: number } | null>(null)
  const [scanError, setScanError] = useState('')

  const loadScanStrategies = useCallback(async () => {
    try {
      const res = await strategiesApi.list()
      setScanStrategies(res.items || [])
    } catch {
      setScanStrategies([])
    }
  }, [])

  useEffect(() => { void loadScanStrategies() }, [loadScanStrategies])

  const doScan = useCallback(async () => {
    if (!scanStrategyId) return
    setScanning(true)
    setScanError('')
    setScanResult(null)
    try {
      const res = await strategiesApi.scan({
        strategy_id: scanStrategyId,
        market: market === 'ALL' ? 'CN' : market,
        limit: 50,
        universe: scanUniverse,
        min_score: 0,
      })
      setScanResult(res)
    } catch (e) {
      setScanError(e instanceof Error ? e.message : '策略扫描失败')
    } finally {
      setScanning(false)
    }
  }, [scanStrategyId, market, scanUniverse])

  const openInsight = useCallback((item: StrategySignalItem) => {
    setInsightSymbol(item.stock_symbol)
    setInsightMarket(item.stock_market || 'CN')
    setInsightName(item.stock_name)
    setInsightHasPosition(!!item.is_holding_snapshot)
    setInsightOpen(true)
  }, [])

  const loadWatchlist = useCallback(async () => {
    try {
      const rows = await stocksApi.list()
      const set = new Set<string>((rows || []).map((s) => `${s.market}:${s.symbol}`))
      setWatchlist(set)
    } catch {
      setWatchlist(new Set())
    }
  }, [])

  // 通达信问小达投研精选(用户主动按板块查询,避免每次进页面自动消耗 tdx ask 配额)
  const [tdxQuery, setTdxQuery] = useState('')
  const [tdxActiveQuery, setTdxActiveQuery] = useState<string | null>(null)
  const [tdxData, setTdxData] = useState<TdxAskResponse | null>(null)
  const [tdxLoading, setTdxLoading] = useState(false)

  const loadTdx = useCallback(async (query: string) => {
    const trimmed = query.trim()
    if (!trimmed) return
    setTdxLoading(true)
    setTdxActiveQuery(trimmed)
    try {
      const res = await tdxApi.ask(trimmed, 10)
      setTdxData(res)
    } catch {
      setTdxData(null)
    } finally {
      setTdxLoading(false)
    }
  }, [])

  const loadStats = useCallback(async () => {
    try {
      const s = await recommendationsApi.getStrategyStats(45)
      setStats(s)
    } catch {
      setStats(null)
    }
  }, [])

  const loadCatalog = useCallback(async () => {
    try {
      const res = await recommendationsApi.listStrategyCatalog(true)
      setStrategyCatalog(res.items || [])
    } catch {
      setStrategyCatalog([])
    }
  }, [])

  // 题材搜索(防抖 250ms)— 486 个概念板块,输入即搜
  const searchSector = useCallback(async (q: string) => {
    try {
      const res = await recommendationsApi.searchOpportunitySectors(q, 20)
      setSectorResults(res.items || [])
    } catch {
      setSectorResults([])
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void searchSector(sectorQuery)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [sectorQuery, searchSector])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const req = {
        status: 'active' as const,
        source_pool: source,
        holding,
        market: market === 'ALL' ? '' : market,
        strategy_code: strategy === 'all' ? '' : strategy,
        risk_level: risk,
        min_score: Number(minScore) || 0,
        sector: sector || '',
        limit: 120,
        include_payload: false,
      }
      let data: Awaited<ReturnType<typeof recommendationsApi.listStrategySignals>>
      try {
        data = await recommendationsApi.listStrategySignals({
          ...req,
          timeoutMs: 45000,
        })
      } catch (firstErr) {
        const msg = firstErr instanceof Error ? firstErr.message : ''
        if (!msg.includes('超时')) throw firstErr
        try {
          // Retry once for transient DB lock/contention.
          data = await recommendationsApi.listStrategySignals({
            ...req,
            timeoutMs: 90000,
          })
        } catch (secondErr) {
          const secondMsg = secondErr instanceof Error ? secondErr.message : ''
          if (!secondMsg.includes('超时')) throw secondErr
          const fallback = await recommendationsApi.listEntryCandidates({
            market: req.market,
            status: 'active',
            min_score: req.min_score,
            limit: req.limit,
            snapshot_date: '',
            source: source === 'all' ? 'all' : source,
            holding: req.holding,
            timeoutMs: 90000,
          })
          data = {
            snapshot_date: fallback.snapshot_date || '',
            count: fallback.count || 0,
            items: (fallback.items || []).map(toSignalFromCandidate),
          }
          setError('策略层请求超时，已降级展示候选快照')
        }
      }
      if ((!data.items || data.items.length === 0) && market !== 'ALL') {
        const fallback = await recommendationsApi.listStrategySignals({
          ...req,
          market: '',
          timeoutMs: 45000,
        })
        if (fallback.items && fallback.items.length > 0) {
          setError(`当前${marketLabel(market)}暂无满足条件机会，已展示全市场结果`)
          data = fallback
        }
      }
      setItems(data.items || [])
      setSnapshotDate(data.snapshot_date || '')
      void loadFeedback(data.snapshot_date || '', data.items || [])
      if (!data.snapshot_date) {
        setError('暂无机会快照，请点击“刷新”生成一次')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [holding, loadFeedback, market, minScore, risk, sector, source, strategy])

  useEffect(() => {
    load()
    loadStats()
    loadCatalog()
    loadWatchlist()
  }, [load, loadCatalog, loadStats, loadWatchlist])

  const pollRefreshCompletion = useCallback(async () => {
    // 每 10s 轮询一次任务状态,最多 12 次(约 2 分钟)
    const maxPolls = 12
    try {
      for (let i = 0; i < maxPolls; i += 1) {
        try {
          const state = await recommendationsApi.getStrategyRefreshStatus()
          if (!state.running) {
            if (state.last_error) {
              setError(`后台刷新失败: ${state.last_error}`)
              toast(`后台刷新失败: ${state.last_error}`, 'error')
            } else {
              setError('')
              toast('刷新完成,机会列表已更新', 'success')
            }
            await Promise.all([load(), loadStats()])
            return
          }
        } catch {
          // 轮询瞬时错误忽略,继续下一轮
        }
        await sleep(10000)
      }
      // 2 分钟仍未完成:提示任务仍在后台,恢复按钮让用户稍后手动刷新
      await Promise.all([load(), loadStats()])
      setError((prev) => prev || '刷新任务仍在后台执行,全市场扫描约需 1-3 分钟,请稍后手动刷新查看')
      toast('刷新任务仍在后台执行,预计 1-3 分钟完成', 'info')
    } finally {
      // 轮询结束:无论成功/失败/超时都恢复按钮,允许再次提交
      refreshingRef.current = false
      setRefreshing(false)
    }
  }, [load, loadStats, toast])

  const handleRefresh = async () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    setError('')
    let backgroundQueued = false
    try {
      const resp = await recommendationsApi.refreshStrategySignals({
        rebuild_candidates: true,
        max_inputs: 500,
        market_scan_limit: 80,
        max_kline_symbols: 60,
        limit_candidates: 2000,
        wait: false,
      })
      if (resp.queued) {
        // 后台任务已接受:保持"刷新中"状态,轮询完成后自动重载列表
        backgroundQueued = true
        setError('')
        void pollRefreshCompletion()
        return
      }
      await Promise.all([load(), loadStats()])
    } catch (e) {
      const msg = e instanceof Error ? e.message : '刷新失败'
      if (msg.includes('超时')) {
        setError('刷新任务耗时较长,已在后台继续执行,请稍后再点刷新')
        toast('刷新任务已在后台继续执行', 'info')
        await load()
      } else {
        setError(msg)
        toast(`刷新失败: ${msg}`, 'error')
      }
    } finally {
      // 同步路径(未走后台轮询)才在此恢复按钮;后台轮询由 pollRefreshCompletion 统一恢复
      if (!backgroundQueued) {
        refreshingRef.current = false
        setRefreshing(false)
      }
    }
  }

  // 导出机会候选 CSV(/api/export/opportunities, 带 token 直接 fetch blob)
  const exportOpportunities = async () => {
    try {
      const token = getToken()
      const res = await fetch('/api/export/opportunities', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `机会候选_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast('机会候选已导出', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '导出失败', 'error')
    }
  }

  const resetFilters = useCallback(() => {
    setMarket(DEFAULT_FILTERS.market)
    setSource(DEFAULT_FILTERS.source)
    setHolding(DEFAULT_FILTERS.holding)
    setStrategy(DEFAULT_FILTERS.strategy)
    setRisk(DEFAULT_FILTERS.risk)
    setMinScore(DEFAULT_FILTERS.minScore)
    setSector('')
    setSectorQuery('')
  }, [setHolding, setMarket, setMinScore, setRisk, setSector, setSource, setStrategy])

  const strategyOptions = useMemo(() => {
    return strategyCatalog.map((row) => ({ value: row.code, label: row.name || row.code }))
  }, [strategyCatalog])

  const groupedItems = useMemo<GroupedSignal[]>(() => {
    const grouped = new Map<string, { primary: StrategySignalItem; members: StrategySignalItem[] }>()
    for (const row of items) {
      const key = `${row.stock_market || 'CN'}:${row.stock_symbol}`
      const prev = grouped.get(key)
      if (!prev) {
        grouped.set(key, { primary: row, members: [row] })
        continue
      }
      prev.members.push(row)
      if (shouldReplacePrimary(row, prev.primary)) {
        prev.primary = row
      }
    }

    const out: GroupedSignal[] = []
    for (const [key, val] of grouped.entries()) {
      const strategyNames = Array.from(new Set(val.members.map((x) => x.strategy_name || x.strategy_code).filter(Boolean)))
      const sourceAgents = Array.from(new Set(val.members.map((x) => sourceAgentLabel(x.source_agent)).filter((x) => x && x !== '--')))
      const hasMarketScan = val.members.some((x) => x.source_pool === 'market_scan' || x.source_pool === 'mixed')
      const topScore = Math.max(...val.members.map(scoreOf))
      out.push({
        key,
        primary: val.primary,
        members: val.members,
        strategyNames,
        sourceAgents,
        hasMarketScan,
        topScore,
      })
    }
    out.sort((a, b) => {
      const sourceDelta = Number(b.hasMarketScan) - Number(a.hasMarketScan)
      if (sourceDelta !== 0) return sourceDelta
      const scoreDelta = b.topScore - a.topScore
      if (Math.abs(scoreDelta) > 0.001) return scoreDelta
      return actionPriority(b.primary) - actionPriority(a.primary)
    })
    return out
  }, [items])

  const filteredSummary = useMemo(() => {
    const total = groupedItems.length
    const unheld = groupedItems.filter((x) => !x.primary.is_holding_snapshot).length
    const marketPool = groupedItems.filter((x) => x.hasMarketScan).length
    return { total, unheld, marketPool }
  }, [groupedItems])

  const globalCoverage = stats?.coverage || null
  const factorStats = stats?.factor_stats || null
  const constraintStats = stats?.constraints || null

  const outcome3d = useMemo(() => {
    const rows = (stats?.by_strategy || []).filter((x) => Number(x.horizon_days) === 3)
    if (!rows.length) return null
    let sample = 0
    let wins = 0
    for (const r of rows) {
      sample += Number(r.sample_size || 0)
      wins += Number(r.wins || 0)
    }
    if (!sample) return null
    return {
      total: sample,
      win_rate: (wins / sample) * 100,
    }
  }, [stats])

  const regimeSummary = useMemo(() => {
    return (stats?.regimes || []).map((r) => ({
      market: r.market,
      label: r.regime_label || r.regime || '震荡',
      regime: r.regime || 'neutral',
      confidence: Number(r.confidence || 0),
      score: Number(r.regime_score || 0),
    }))
  }, [stats])

  const riskSummary = useMemo(() => {
    return (stats?.portfolio_risk || []).map((r) => ({
      market: r.market,
      riskLevel: r.risk_level || 'medium',
      concentration: Number(r.concentration_top5 || 0),
      highRiskRatio: Number(r.high_risk_ratio || 0),
    }))
  }, [stats])

  return (
    <div className="page-container pb-10">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mb-4">
        <div>
          <h1 className="text-[20px] md:text-[22px] font-bold text-foreground tracking-tight flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-primary" />
            机会页
          </h1>
          <p className="text-[12px] text-muted-foreground mt-1">
            市场池优先，候选必须具备可执行入场计划
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground">{snapshotDate || '最新快照'}</span>
          <Button
            variant="secondary"
            size="sm"
            className="h-8 text-[12px]"
            onClick={exportOpportunities}
          >
            <Download className="w-3.5 h-3.5 mr-1" />
            导出
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-8 text-[12px]"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? (
              <>
                <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin mr-1" />
                刷新中…
              </>
            ) : (
              <>
                <RefreshCw className="w-3.5 h-3.5 mr-1" />
                刷新
              </>
            )}
          </Button>
        </div>
      </div>

      {/* 后台刷新任务进行中提示条:让用户知道任务在跑、预期多久 */}
      {refreshing && (
        <div className="card p-3 mb-4 flex items-center gap-2 text-[12px] text-primary">
          <span className="w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full animate-spin shrink-0" />
          <div className="flex-1">
            <span className="font-medium">后台刷新中…</span>
            <span className="text-muted-foreground ml-1">全市场扫描约需 1-3 分钟,完成后将自动更新列表</span>
          </div>
          <span className="text-[11px] text-muted-foreground">已提交,请稍候</span>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div className="card relative overflow-hidden p-3 border-l-2 border-l-primary">
          <div className="text-[11px] font-semibold text-foreground/80">当前候选(全局)</div>
          <div className="text-[24px] font-bold mt-1 font-num tabular-nums">{globalCoverage?.total_signals ?? '--'}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            可执行: {globalCoverage?.active_signals ?? '--'}，观察: {(globalCoverage?.total_signals != null && globalCoverage?.active_signals != null) ? Math.max(0, globalCoverage.total_signals - globalCoverage.active_signals) : '--'}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-muted-foreground">市场池占比</div>
          <div className="text-[18px] font-bold mt-1">{globalCoverage?.market_scan_share_pct != null ? `${globalCoverage.market_scan_share_pct.toFixed(1)}%` : '--'}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            市场池: {globalCoverage?.market_scan_signals ?? '--'}，关注池: {globalCoverage?.watchlist_signals ?? '--'}，融合: {globalCoverage?.mixed_signals ?? '--'}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-muted-foreground">本次筛选结果</div>
          <div className="text-[18px] font-bold mt-1">{filteredSummary.total}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            未持仓: {filteredSummary.unheld}，市场池: {filteredSummary.marketPool}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-muted-foreground">3日胜率(自动评估)</div>
          <div className="text-[18px] font-bold mt-1">{outcome3d ? `${outcome3d.win_rate.toFixed(1)}%` : '--'}</div>
          <div className="text-[10px] text-muted-foreground mt-1">
            自动样本: {outcome3d ? `${outcome3d.total}` : '--'}
          </div>
        </div>
      </div>

      {/* 通达信问小达投研精选(用户主动按板块查询,避免每次进页面自动消耗 tdx ask 配额) */}
            <div className="mb-4">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-[14px] font-semibold text-foreground flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary" />
                  通达信问小达 · 投研精选
                </h2>
              </div>
              <div className="card p-3">
                <form
                  className="flex items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    void loadTdx(tdxQuery)
                  }}
                >
                  <Input
                    value={tdxQuery}
                    onChange={(e) => setTdxQuery(e.target.value)}
                    placeholder="输入板块/概念/选股条件,如:半导体、商业航天、今日涨幅前10的医药"
                    className="flex-1"
                    disabled={tdxLoading}
                  />
                  <Button
                    type="submit"
                    size="sm"
                    disabled={tdxLoading || !tdxQuery.trim()}
                  >
                    {tdxLoading ? (
                      <>
                        <RefreshCw className="w-3 h-3 mr-1 animate-spin" />
                        查询中…
                      </>
                    ) : (
                      '查询'
                    )}
                  </Button>
                </form>
                <div className="mt-3">
                  {!tdxActiveQuery ? (
                    <div className="text-[11px] text-muted-foreground py-6 text-center">
                      输入板块或选股条件,点击查询(每次查询消耗 1 次 tdx ask 配额)
                    </div>
                  ) : tdxData == null ? (
                    <div className="text-[11px] text-red-600 py-3">查询失败,请稍后重试</div>
                  ) : (
                    <>
                      <div className="text-[11px] text-muted-foreground mb-2">
                        查询: <span className="text-foreground font-medium">{tdxActiveQuery}</span>
                        {' · '}
                        {tdxData.rows?.length || 0} 条结果
                      </div>
                      <div className="flex flex-col gap-1.5 max-h-[400px] overflow-y-auto pr-1">
                        {(tdxData.rows || []).slice(0, 10).map((r: Record<string, unknown>, i: number) => {
                          const code = String(r.sec_code ?? r.code ?? '')
                          const name = String(r.sec_name ?? r.name ?? '')
                          const chg = String(r.chg ?? r.change_pct ?? '')
                          const mainNet = Object.entries(r).find(([k]) => k.includes('主力净额') || k.includes('主力净'))?.[1]
                          const clickable = !!code
                          return (
                            <button
                              key={`${code}-${i}`}
                              type="button"
                              disabled={!clickable}
                              onClick={() => clickable && openInsight({
                                stock_symbol: code,
                                stock_market: 'CN',
                                stock_name: name,
                                action: 'watch',
                                action_label: '观望',
                                is_holding_snapshot: false,
                                rank_score: 0,
                                score: 0,
                                status: 'inactive',
                                source_pool: 'watchlist',
                                source_pool_label: '关注池',
                                risk_level: 'low',
                                risk_level_label: '低风险',
                                source_agent: 'market_scan',
                                strategy_code: 'tdx_wenda',
                                strategy_name: '通达信问小达',
                                strategy_version: 'v1',
                                confidence: null,
                                signal: '',
                                reason: `通达信问小达: ${tdxActiveQuery}`,
                                evidence: [],
                                holding_days: 3,
                                entry_low: null,
                                entry_high: null,
                                stop_loss: null,
                                target_price: null,
                                invalidation: '',
                                plan_quality: 0,
                                source_suggestion_id: null,
                                source_candidate_id: null,
                                trace_id: '',
                                context_quality_score: null,
                                score_breakdown: { weighted_score: 0, has_entry_plan: false },
                                market_regime: {},
                                cross_feature: {},
                                news_metric: {},
                                constrained: false,
                                constraint_reasons: [],
                                payload: { source_meta: { plan: {} } },
                                created_at: '',
                                updated_at: '',
                              } as unknown as StrategySignalItem)}
                              className={`text-left text-[11px] rounded px-2 py-1.5 flex items-center justify-between gap-2 ${
                                clickable ? 'hover:bg-accent cursor-pointer' : 'cursor-default'
                              }`}
                            >
                              <span className="truncate">
                                <span className="text-muted-foreground mr-1">{code}</span>
                                <span className="font-medium text-foreground">{name}</span>
                              </span>
                              <span className="flex items-center gap-1.5 shrink-0">
                                {chg && (
                                  <span
                                    className={
                                      String(chg).startsWith('-')
                                        ? 'text-emerald-700 dark:text-emerald-400'
                                        : 'text-rose-700 dark:text-rose-400'
                                    }
                                  >
                                    {chg}%
                                  </span>
                                )}
                                {mainNet != null && (
                                  <span className="text-[10px] text-primary">主力{String(mainNet)}</span>
                                )}
                              </span>
                            </button>
                          )
                        })}
                        {(tdxData.rows || []).length === 0 && (
                          <div className="text-[11px] text-muted-foreground py-3 text-center">
                            暂无数据(试试简化查询词,如「半导体」)
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>

      {(factorStats || constraintStats) && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">平均Alpha因子</div>
            <div className="text-[18px] font-bold mt-1">{factorStats ? factorStats.avg_alpha_score.toFixed(1) : '--'}</div>
            <div className="text-[10px] text-muted-foreground mt-1">样本 {factorStats?.sample_size ?? '--'}</div>
          </div>
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">平均事件催化</div>
            <div className="text-[18px] font-bold mt-1">{factorStats ? factorStats.avg_catalyst_score.toFixed(1) : '--'}</div>
            <div className="text-[10px] text-muted-foreground mt-1">
              拥挤惩罚 {factorStats ? factorStats.avg_crowd_penalty.toFixed(1) : '--'}
            </div>
          </div>
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">平均质量/风险</div>
            <div className="text-[18px] font-bold mt-1">
              {factorStats ? `${factorStats.avg_quality_score.toFixed(1)} / ${factorStats.avg_risk_penalty.toFixed(1)}` : '--'}
            </div>
            <div className="text-[10px] text-muted-foreground mt-1">质量分越高越好</div>
          </div>
          <div className="card p-3">
            <div className="text-[11px] text-muted-foreground">组合约束降级</div>
            <div className="text-[18px] font-bold mt-1">{constraintStats?.constrained_top20 ?? 0}</div>
            <div className="text-[10px] text-muted-foreground mt-1">Top20 被风控降级数量</div>
          </div>
        </div>
      )}

      {(regimeSummary.length > 0 || riskSummary.length > 0) && (
        <div className="card p-3 mb-4">
          <div className="text-[11px] text-muted-foreground mb-2">市场状态与组合风险</div>
          <div className="flex flex-wrap gap-2">
            {regimeSummary.map((r) => (
              <span key={`regime-${r.market}`} className={`text-[11px] px-2.5 py-1 rounded ${regimeToneClass(r.regime)}`}>
                {marketLabel(r.market)}: {r.label} · 置信 {Math.round(r.confidence * 100)}%
              </span>
            ))}
            {riskSummary.map((r) => (
              <span key={`risk-${r.market}`} className="text-[11px] px-2.5 py-1 rounded bg-accent/70 text-muted-foreground border border-border/60">
                {marketLabel(r.market)}风险: {r.riskLevel} · 集中度{(r.concentration * 100).toFixed(0)}% · 高风险占比{(r.highRiskRatio * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card p-3 md:p-4 mb-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-8 gap-2">
          <Select value={market} onValueChange={(v) => setMarket(v as 'ALL' | 'CN' | 'HK' | 'US')}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">全部市场</SelectItem>
              <SelectItem value="CN">A股</SelectItem>
              <SelectItem value="HK">港股</SelectItem>
              <SelectItem value="US">美股</SelectItem>
            </SelectContent>
          </Select>
          <Select value={source} onValueChange={(v) => setSource(v as SourceFilter)}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部来源</SelectItem>
              <SelectItem value="market_scan">市场池</SelectItem>
              <SelectItem value="mixed">融合池</SelectItem>
              <SelectItem value="watchlist">关注池</SelectItem>
            </SelectContent>
          </Select>
          <Select value={holding} onValueChange={(v) => setHolding(v as HoldingFilter)}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部持仓状态</SelectItem>
              <SelectItem value="unheld">仅未持仓</SelectItem>
              <SelectItem value="held">仅持仓中</SelectItem>
            </SelectContent>
          </Select>
          <Select value={strategy} onValueChange={setStrategy}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部策略</SelectItem>
              {strategyOptions.map((op) => (
                <SelectItem key={op.value} value={op.value}>{op.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={risk} onValueChange={(v) => setRisk(v as RiskFilter)}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部风险等级</SelectItem>
              <SelectItem value="low">低风险</SelectItem>
              <SelectItem value="medium">中风险</SelectItem>
              <SelectItem value="high">高风险</SelectItem>
            </SelectContent>
          </Select>
          <Select value={minScore} onValueChange={setMinScore}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="90">评分90+</SelectItem>
              <SelectItem value="80">评分80+</SelectItem>
              <SelectItem value="70">评分70+</SelectItem>
              <SelectItem value="60">评分60+</SelectItem>
              <SelectItem value="50">评分50+</SelectItem>
              <SelectItem value="0">评分不过滤</SelectItem>
            </SelectContent>
          </Select>
          <Button size="sm" className="h-8 text-[12px]" onClick={load} disabled={loading}>
            {loading ? '加载中...' : '应用筛选'}
          </Button>
          <Button variant="ghost" size="sm" className="h-8 text-[12px]" onClick={resetFilters}>
            清空筛选
          </Button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-2 mt-2">
          <div className="relative">
            <Input
              value={sector}
              placeholder="题材:输入搜索(如 商业航天/低空经济)"
              className="h-8 text-[12px]"
              onFocus={() => { setSectorOpen(true); if (sectorResults.length === 0) void searchSector('') }}
              onBlur={() => window.setTimeout(() => setSectorOpen(false), 200)}
              onChange={(e) => { setSector(e.target.value); setSectorQuery(e.target.value) }}
              onKeyDown={(e) => { if (e.key === 'Enter') { setSectorOpen(false); void load() } }}
            />
            {sectorOpen && (
              <div className="absolute z-50 mt-1 w-full max-h-52 overflow-y-auto rounded-md border border-border/60 bg-popover p-1 shadow-lg">
                {sectorResults.length === 0 && (
                  <div className="px-2 py-1.5 text-[11px] text-muted-foreground">无匹配题材</div>
                )}
                {sectorResults.map((b) => (
                  <button
                    key={b.code}
                    type="button"
                    className="w-full text-left px-2 py-1.5 rounded text-[12px] hover:bg-accent"
                    onMouseDown={(e) => { e.preventDefault(); setSector(b.name); setSectorQuery(b.name); setSectorOpen(false) }}
                  >
                    {b.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div className="card p-3 mb-4 text-[12px] text-amber-700 dark:text-amber-500 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      {/* ── 策略选股(策略库批量扫描) ── */}
      <div className="card p-4 mb-4">
        <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
          <div className="flex items-center gap-2">
            <ScanSearch className="w-4 h-4 text-primary" />
            <h2 className="text-[14px] font-semibold text-foreground">策略选股</h2>
            <span className="text-[11px] text-muted-foreground">用策略库规则批量扫描全市场, 按分数排序</span>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Select value={scanStrategyId} onValueChange={setScanStrategyId}>
            <SelectTrigger className="h-8 text-[12px] w-[220px]">
              <SelectValue placeholder="选择策略" />
            </SelectTrigger>
            <SelectContent>
              {scanStrategies.map((s) => (
                <SelectItem key={s.id} value={s.id}>{s.display_name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={scanUniverse} onValueChange={(v) => setScanUniverse(v as 'all' | 'watchlist')}>
            <SelectTrigger className="h-8 text-[12px] w-[130px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全市场</SelectItem>
              <SelectItem value="watchlist">自选+种子池</SelectItem>
            </SelectContent>
          </Select>
          <Button
            size="sm"
            className="h-8 text-[12px]"
            onClick={doScan}
            disabled={scanning || !scanStrategyId}
          >
            {scanning ? <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" /> : <ScanSearch className="w-3.5 h-3.5 mr-1" />}
            {scanning ? '扫描中...' : '批量选股'}
          </Button>
          {scanResult && (
            <span className="text-[11px] text-muted-foreground">
              扫描 {scanResult.scanned} 只 → 命中 {scanResult.total} 只
            </span>
          )}
        </div>
        {scanError && (
          <div className="mt-2 text-[12px] text-amber-700 dark:text-amber-500 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> {scanError}
          </div>
        )}
        {scanResult && scanResult.items.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-[11px] text-muted-foreground border-b border-border/50">
                  <th className="text-left py-1.5 pr-2">代码</th>
                  <th className="text-left py-1.5 pr-2">名称</th>
                  <th className="text-right py-1.5 pr-2">评分</th>
                  <th className="text-right py-1.5 pr-2">现价</th>
                  <th className="text-right py-1.5 pr-2">PE</th>
                  <th className="text-right py-1.5 pr-2">PB</th>
                  <th className="text-right py-1.5">市值(亿)</th>
                </tr>
              </thead>
              <tbody>
                {scanResult.items.map((it) => {
                  const d = it.current_data || {}
                  const num = (v: unknown) => (v == null || Number.isNaN(Number(v)) ? '--' : Number(v).toFixed(2))
                  return (
                    <tr key={it.symbol} className="border-b border-border/30 hover:bg-accent/40 cursor-pointer" onClick={() => openInsight({
                      stock_symbol: it.symbol,
                      stock_market: (it.market || 'CN') as 'CN',
                      stock_name: it.name,
                      rank_score: it.score,
                      is_holding_snapshot: false,
                    } as unknown as StrategySignalItem)}>
                      <td className="py-1.5 pr-2 font-mono text-muted-foreground">{it.symbol}</td>
                      <td className="py-1.5 pr-2 font-medium text-foreground">{it.name}</td>
                      <td className="py-1.5 pr-2 text-right font-semibold text-primary">{it.score.toFixed(1)}</td>
                      <td className="py-1.5 pr-2 text-right font-mono">{num(d.current_price)}</td>
                      <td className="py-1.5 pr-2 text-right font-mono">{num(d.pe_ttm)}</td>
                      <td className="py-1.5 pr-2 text-right font-mono">{num(d.pb_ratio)}</td>
                      <td className="py-1.5 text-right font-mono text-muted-foreground">{num(d.market_cap)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {scanResult && scanResult.items.length === 0 && (
          <div className="mt-3 text-[12px] text-muted-foreground">
            没有股票通过该策略的硬过滤条件
            <span className="block mt-1 text-[11px] text-muted-foreground/70">
              {new Date().getHours() < 9 || new Date().getHours() >= 15
                ? '💡 当前为非交易时段, 腾讯行情中涨跌幅/量比/换手为 0, 依赖量能条件的策略(资金热度/放量突破)会筛不出票。建议交易时段使用, 或改选估值类策略(双低/低波质量)。'
                : '可尝试放宽条件或切换为「自选+种子池」范围'}
            </span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {groupedItems.map((group) => {
          const item = group.primary
          const payload = item.payload && typeof item.payload === 'object' ? item.payload as Record<string, unknown> : {}
          const sourceMeta = payload.source_meta && typeof payload.source_meta === 'object' ? payload.source_meta as Record<string, unknown> : {}
          const sourcePlan = sourceMeta.plan && typeof sourceMeta.plan === 'object' ? sourceMeta.plan as Record<string, unknown> : {}
          const entryLow = toNumberOrNull(item.entry_low) ?? toNumberOrNull(sourcePlan.entry_low)
          const entryHigh = toNumberOrNull(item.entry_high) ?? toNumberOrNull(sourcePlan.entry_high)
          const stopLoss = toNumberOrNull(item.stop_loss) ?? toNumberOrNull(sourcePlan.stop_loss)
          const targetPrice = toNumberOrNull(item.target_price) ?? toNumberOrNull(sourcePlan.target_price)
          const stateKey = `${item.snapshot_date}:${group.key}`
          const inWatchlist = watchlist.has(group.key)
          const breakdown = item.score_breakdown || {}
          const marketRegime = item.market_regime || {}
          const crossFeature = item.cross_feature || {}
          const newsMetric = item.news_metric || {}
          const strategyHead = group.strategyNames.slice(0, 2).join(' / ') || (item.strategy_name || item.strategy_code)
          const strategyTailCount = Math.max(0, group.strategyNames.length - 2)
          const sourceAgentHead = group.sourceAgents[0] || sourceAgentLabel(item.source_agent)
          const sourceAgentTailCount = Math.max(0, group.sourceAgents.length - 1)
          const eventScore = toNumberOrNull(newsMetric.event_score)
          const eventCount = Number(newsMetric.news_count || 0)
          const sourceFlags: string[] = []
          if (group.hasMarketScan) sourceFlags.push('市场候选')
          if (inWatchlist) sourceFlags.push('已关注标的')
          if (sourceFlags.length <= 0) sourceFlags.push('关注池')
          const sourcePoolLabel = group.hasMarketScan
            ? (group.members.some((x) => x.source_pool === 'mixed') ? '市场+关注' : '市场池')
            : (item.source_pool_label || '关注池')
          return (
            <div key={stateKey} className={`card p-3 sm:p-4 transition-colors ${toneClass(item)}`}>
              <button className="w-full text-left" onClick={() => openInsight(item)}>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold truncate">{item.stock_name || item.stock_symbol}</div>
                    <div className="text-[11px] text-muted-foreground font-mono">{item.stock_market}:{item.stock_symbol}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px]">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] ${actionBadgeClass(item.action)}`}>
                        {displayActionLabel(item)}
                      </span>
                    </div>
                    <div className={`text-[13px] font-bold font-mono mt-1.5 ${Number(item.rank_score || item.score || 0) >= 80 ? 'text-primary' : 'text-muted-foreground'}`}>
                      评分 {Math.round(item.rank_score || item.score || 0)}
                    </div>
                    {item.ai_score != null && (
                      <div className="mt-1 flex items-center justify-end gap-1">
                        <span className="text-[10px] text-muted-foreground">AI</span>
                        <span className={`inline-flex items-center justify-center min-w-[18px] px-1.5 py-0.5 rounded text-[11px] font-semibold ${item.ai_score >= 8 ? 'bg-green-500/20 text-green-700 dark:text-green-400' : item.ai_score >= 6 ? 'bg-primary/20 text-primary' : item.ai_score >= 4 ? 'bg-amber-500/20 text-amber-700 dark:text-amber-400' : 'bg-red-500/20 text-red-700 dark:text-red-400'}`}>
                          {item.ai_score}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
                <div className="mt-1.5 text-[12px] leading-5 text-foreground line-clamp-2">{item.signal || item.reason || '--'}</div>
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] leading-4 text-muted-foreground">
                  <div className="font-medium text-foreground/90">入场: {formatEntryDisplay(item.action, entryLow, entryHigh)}</div>
                  <div>止损: {formatPlanPrice(stopLoss)}</div>
                  <div>目标: {formatPlanPrice(targetPrice)}</div>
                  <div>失效: {item.invalidation || '--'}</div>
                  <div>
                    策略: {strategyHead}
                    {strategyTailCount > 0 ? ` +${strategyTailCount}` : ''}
                  </div>
                  <div>来源池: {sourcePoolLabel}</div>
                  <div>
                    来源Agent: {sourceAgentHead}
                    {sourceAgentTailCount > 0 ? ` +${sourceAgentTailCount}` : ''}
                  </div>
                  <div>风险: {item.risk_level_label || item.risk_level || '--'}</div>
                  <div>市场状态: {marketRegime.regime_label || marketRegime.regime || '--'}</div>
                  <div>持仓: {item.is_holding_snapshot ? '持仓中' : '未持仓'}</div>
                  <div>市场: {marketLabel(item.stock_market)}</div>
                </div>
                <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] leading-4 text-muted-foreground">
                  <div>Alpha: {formatMetric(breakdown.alpha_score)}</div>
                  <div>催化: {formatMetric(breakdown.catalyst_score)}</div>
                  <div>质量: {formatMetric(breakdown.quality_score)}</div>
                  <div>风险惩罚: {formatMetric(breakdown.risk_penalty)}</div>
                  <div>相对强弱: {crossFeature.relative_strength_pct != null ? `${Number(crossFeature.relative_strength_pct).toFixed(0)}分位` : '--'}</div>
                  <div className="font-medium text-foreground/90">事件催化: {eventScore != null ? eventScore.toFixed(1) : '--'}{eventCount > 0 ? `（${eventCount}条）` : '（无命中）'}</div>
                </div>
                {item.factor_explain && (((item.factor_explain.positive?.length ?? 0) > 0) || ((item.factor_explain.negative?.length ?? 0) > 0)) && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {(item.factor_explain.positive ?? []).map((f) => (
                      <span key={`p-${f.factor}`} className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-green-500/15 text-green-700 dark:text-green-400">
                        {f.label} +{Math.abs(f.contribution).toFixed(1)}
                      </span>
                    ))}
                    {(item.factor_explain.negative ?? []).map((f) => (
                      <span key={`n-${f.factor}`} className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-red-500/15 text-red-700 dark:text-red-400">
                        {f.label} {f.contribution.toFixed(1)}
                      </span>
                    ))}
                  </div>
                )}
                {item.constrained && (
                  <div className="mt-1.5 text-[10px] text-amber-700 dark:text-amber-400">
                    组合约束: {(item.constraint_reasons || []).join('；') || '已自动降级'}
                  </div>
                )}
              </button>

              <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
                <div className="text-[10px] text-muted-foreground">
                  来源: {sourceFlags.join(' + ')}
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-0.5 text-[10px]">
                    <button
                      type="button"
                      disabled={feedbackPending.has(group.key)}
                      onClick={() => handleCandidateFeedback(item, true)}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors disabled:opacity-40 ${
                        feedbackMap[group.key] === true
                          ? 'bg-green-500/15 text-green-700 dark:text-green-400'
                          : 'text-muted-foreground hover:text-green-700 dark:hover:text-green-400'
                      }`}
                      title="这个候选建议有用"
                    >
                      <ThumbsUp className="h-3 w-3" />
                      有用
                    </button>
                    <button
                      type="button"
                      disabled={feedbackPending.has(group.key)}
                      onClick={() => handleCandidateFeedback(item, false)}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors disabled:opacity-40 ${
                        feedbackMap[group.key] === false
                          ? 'bg-red-500/15 text-red-700 dark:text-red-400'
                          : 'text-muted-foreground hover:text-red-700 dark:hover:text-red-400'
                      }`}
                      title="这个候选建议没用"
                    >
                      <ThumbsDown className="h-3 w-3" />
                      没用
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShareSignal(item)}
                    className="inline-flex items-center gap-1 text-[10px] text-muted-foreground transition-colors hover:text-primary"
                    title="生成 AI 评分分享图"
                  >
                    <Share2 className="h-3 w-3" />
                    分享图
                  </button>
                  <div className="text-[10px] text-muted-foreground">评估: 自动后验</div>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {!loading && groupedItems.length === 0 && (
        <div className="card p-8 text-center text-[12px] text-muted-foreground mt-4">暂无满足条件的机会</div>
      )}

      <details className="mt-6 group">
        <summary className="cursor-pointer list-none flex items-center gap-2 text-[12px] font-medium text-muted-foreground hover:text-foreground transition-colors">
          <span className="text-[11px] opacity-60 transition-transform group-open:rotate-90">▶</span>
          因子权重与战绩
        </summary>
        <div className="mt-3">
          <FactorWeightsPanel />
        </div>
      </details>

      <StockInsightModal
        open={insightOpen}
        onOpenChange={setInsightOpen}
        symbol={insightSymbol}
        market={insightMarket}
        stockName={insightName}
        hasPosition={insightHasPosition}
      />

      {shareSignal && (
        <SignalScoreShareCard
          open={!!shareSignal}
          onClose={() => setShareSignal(null)}
          item={shareSignal}
        />
      )}
    </div>
  )
}
