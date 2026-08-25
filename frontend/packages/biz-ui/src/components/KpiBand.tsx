import { useNavigate } from 'react-router-dom'
import { Activity, Crown } from 'lucide-react'
import { useEffect, useState } from 'react'
import { fetchAPI } from '@panwatch/api'

/**
 * 首页 KPI 带(v0.4.6, 借鉴 TSP tick-stock-panel 设计)。
 *
 * 6 格「数字优先」KPI: 涨/平/跌 · 主力净流入 · 成交额 · 情绪周期阶段 ·
 * 涨停/跌停(来自异动池当日) · 主线 Top1。
 * 数据全部复用首页已有接口(marketFlow / anomalies / mainline / phase),
 * 不新增后端调用 — phase/mainline 由子卡自身轮询, 这里通过 props 传入快照。
 */

export interface MainlineTop1 {
  name: string
  limit_up_count: number
  max_boards: number
  leader_name?: string
}

function Cell({
  label,
  value,
  sub,
  tone,
}: {
  label: React.ReactNode
  value: React.ReactNode
  sub?: string
  tone?: 'bull' | 'bear' | 'accent' | null
}) {
  const toneCls =
    tone === 'bull'
      ? 'text-red-600 dark:text-red-400'
      : tone === 'bear'
        ? 'text-emerald-600 dark:text-emerald-400'
        : tone === 'accent'
          ? 'text-primary'
          : 'text-foreground'
  return (
    <div className="min-w-0 px-2 py-1.5">
      <div className="truncate text-[10px] text-muted-foreground">{label}</div>
      <div className={`font-num tabular-nums text-[15px] font-semibold leading-tight ${toneCls}`}>
        {value}
      </div>
      {sub && <div className="truncate text-[9.5px] text-muted-foreground">{sub}</div>}
    </div>
  )
}

export default function KpiBand({
  upCount,
  downCount,
  mainFlowYi,
  amountYi,
  phaseLabel,
  phaseLoading,
  mainlineTop1,
  mainlineLoading,
}: {
  upCount: number | null
  downCount: number | null
  mainFlowYi: number | null
  amountYi: number | null
  phaseLabel: string | null
  phaseLoading: boolean
  mainlineTop1: MainlineTop1 | null
  mainlineLoading: boolean
}) {
  const navigate = useNavigate()
  const flowTone = mainFlowYi == null ? null : mainFlowYi >= 0 ? 'bull' : 'bear'

  return (
    <div className="card grid grid-cols-3 divide-x divide-border/40 md:grid-cols-6">
      <Cell
        label="涨 / 跌"
        value={
          <>
            <span className="text-red-600 dark:text-red-400">{upCount ?? '--'}</span>
            <span className="mx-0.5 text-muted-foreground">/</span>
            <span className="text-emerald-600 dark:text-emerald-400">{downCount ?? '--'}</span>
          </>
        }
      />
      <Cell
        label="主力净流入"
        value={mainFlowYi == null ? '--' : `${mainFlowYi >= 0 ? '+' : ''}${mainFlowYi.toFixed(0)}亿`}
        tone={flowTone as 'bull' | 'bear' | null}
      />
      <Cell label="两市成交额" value={amountYi == null ? '--' : `${amountYi.toFixed(0)}亿`} />
      <button
        type="button"
        className="cursor-pointer text-left transition-colors hover:bg-accent/20"
        onClick={() => navigate('/')}
        title="查看情绪周期详情"
      >
        <Cell
          label={
            <span className="inline-flex items-center gap-1">
              <Activity className="h-3 w-3" />情绪周期
            </span>
          }
          value={phaseLoading ? '…' : phaseLabel || '--'}
          tone="accent"
        />
      </button>
      <button
        type="button"
        className="cursor-pointer text-left transition-colors hover:bg-accent/20"
        onClick={() => navigate('/opportunities')}
        title="查看主线识别"
      >
        <Cell
          label={
            <span className="inline-flex items-center gap-1">
              <Crown className="h-3 w-3" />主线 Top1
            </span>
          }
          value={mainlineLoading ? '…' : mainlineTop1?.name || '--'}
          sub={mainlineTop1 ? `涨停${mainlineTop1.limit_up_count}家 · 高度${mainlineTop1.max_boards}板` : undefined}
          tone="accent"
        />
      </button>
      {/* 占位格: 与 phase 卡联动, 点击滚到情绪周期卡 */}
      <button
        type="button"
        className="hidden cursor-pointer text-left transition-colors hover:bg-accent/20 md:block"
        onClick={() => document.getElementById('market-phase-anchor')?.scrollIntoView({ behavior: 'smooth' })}
      >
        <Cell label="市场体检" value="↓" sub="查看下方环境详情" />
      </button>
    </div>
  )
}

/** 轻量拉取 phase 当前阶段标签(KpiBand 用; 完整卡在 MarketPhaseCard) */
export function usePhaseLabel(): { label: string | null; loading: boolean } {
  const [label, setLabel] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const res = await fetchAPI<{ available: boolean; current: { label: string } | null }>('/market/phase')
        if (alive) setLabel(res?.current?.label ?? null)
      } catch {
        /* 静默 — KPI 格显示 -- */
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])
  return { label, loading }
}


/** 轻量拉取主线 Top1(KpiBand 用; 完整榜在 MarketMainlineCard) */
export function useMainlineTop1(): { top: MainlineTop1 | null; loading: boolean } {
  const [top, setTop] = useState<MainlineTop1 | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const res = await fetchAPI<{ ranked_groups: MainlineTop1[] }>("/market/mainline")
        if (alive) setTop(res?.ranked_groups?.[0] ?? null)
      } catch {
        /* 静默 */
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [])
  return { top, loading }
}
