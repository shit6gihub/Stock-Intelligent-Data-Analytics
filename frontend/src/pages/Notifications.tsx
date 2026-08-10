import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  AlertCircle,
  AlertTriangle,
  BellRing,
  CheckCheck,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  Inbox,
  Info,
  RefreshCw,
  Send,
  Trash2,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface NotificationItem {
  id: number
  category: string
  level: 'info' | 'success' | 'warning' | 'error'
  title: string
  body: string
  link: string
  source: string
  trace_id: string
  push_status: string
  push_error: string
  read: boolean
  created_at: string
}

type FilterKey = 'all' | 'unread' | 'failed'

const CATEGORY_LABELS: Record<string, string> = {
  agent_run: 'Agent 任务',
  report: '报告',
  strategy: '策略',
  price_alert: '价格提醒',
  system: '系统',
}

const LEVEL_META = {
  success: { label: '成功', icon: CheckCircle2, className: 'text-emerald-500 bg-emerald-500/10' },
  error: { label: '失败', icon: AlertCircle, className: 'text-rose-500 bg-rose-500/10' },
  warning: { label: '警告', icon: AlertTriangle, className: 'text-amber-500 bg-amber-500/10' },
  info: { label: '信息', icon: Info, className: 'text-primary bg-primary/10' },
}

const PUSH_META: Record<string, { label: string; className: string }> = {
  sent: { label: '已外部推送', className: 'text-emerald-500 bg-emerald-500/10' },
  failed: { label: '外部推送失败', className: 'text-rose-500 bg-rose-500/10' },
  skipped: { label: '仅站内通知', className: 'text-muted-foreground bg-accent/60' },
  pending: { label: '正在推送', className: 'text-amber-500 bg-amber-500/10' },
}

function formatDateTime(iso: string): string {
  if (!iso) return '时间未知'
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return iso
  return value.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

function normalizeInternalLink(link: string): string {
  const value = String(link || '').trim()
  if (value === '/stocks' || value.startsWith('/stocks?')) {
    return `/portfolio${value.slice('/stocks'.length)}`
  }
  return value.startsWith('/') && !value.startsWith('//') ? value : ''
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="flex min-h-[280px] flex-col items-center justify-center px-6 text-center">
      <span className="mb-3 inline-flex h-12 w-12 items-center justify-center rounded-full bg-accent/60 text-muted-foreground">
        <Inbox className="h-5 w-5" />
      </span>
      <div className="text-[14px] font-medium text-foreground">{filtered ? '没有符合条件的通知' : '暂无通知'}</div>
      <div className="mt-1 text-[12px] text-muted-foreground">后台任务、策略与提醒的结果会集中显示在这里。</div>
    </div>
  )
}

export default function NotificationsPage() {
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [items, setItems] = useState<NotificationItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [filter, setFilter] = useState<FilterKey>('all')
  const [category, setCategory] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await fetchAPI<{ items: NotificationItem[]; unread: number }>('/notifications?limit=200')
      const next = result?.items || []
      setItems(next)
      const requested = Number(searchParams.get('id'))
      setSelectedId(current => {
        if (Number.isFinite(requested) && next.some(item => item.id === requested)) return requested
        if (current && next.some(item => item.id === current)) return current
        return next[0]?.id ?? null
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : '通知加载失败')
    } finally {
      setLoading(false)
    }
  }, [searchParams])

  useEffect(() => { void load() }, [load])

  const unread = useMemo(() => items.filter(item => !item.read).length, [items])
  const failed = useMemo(() => items.filter(item => item.push_status === 'failed').length, [items])
  const categories = useMemo(() => Array.from(new Set(items.map(item => item.category).filter(Boolean))), [items])
  const filtered = useMemo(() => items.filter(item => {
    if (filter === 'unread' && item.read) return false
    if (filter === 'failed' && item.push_status !== 'failed') return false
    if (category && item.category !== category) return false
    return true
  }), [category, filter, items])
  const selected = items.find(item => item.id === selectedId) || null

  const selectItem = useCallback(async (item: NotificationItem) => {
    setSelectedId(item.id)
    setSearchParams({ id: String(item.id) }, { replace: true })
    if (item.read) return
    setItems(current => current.map(value => value.id === item.id ? { ...value, read: true } : value))
    try {
      await fetchAPI(`/notifications/${item.id}/read`, { method: 'POST' })
    } catch {
      setItems(current => current.map(value => value.id === item.id ? { ...value, read: false } : value))
    }
  }, [setSearchParams])

  const markAll = async () => {
    try {
      await fetchAPI('/notifications/read-all', { method: 'POST' })
      setItems(current => current.map(item => ({ ...item, read: true })))
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    }
  }

  const clearRead = async () => {
    if (!window.confirm('确认清空所有已读通知？未读通知会保留。')) return
    try {
      await fetchAPI('/notifications/clear', { method: 'DELETE' })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '清空失败')
    }
  }

  const linkedPage = selected ? normalizeInternalLink(selected.link) : ''
  const selectedLevel = selected ? (LEVEL_META[selected.level] || LEVEL_META.info) : LEVEL_META.info
  const SelectedLevelIcon = selectedLevel.icon
  const selectedPush = selected ? (PUSH_META[selected.push_status] || null) : null

  return (
    <div className="mx-auto w-full max-w-[1480px] space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-[20px] font-bold tracking-tight text-foreground md:text-[22px]">
            <BellRing className="h-5 w-5 text-primary" />
            通知管理中心
          </h1>
          <p className="mt-1 text-[12px] text-muted-foreground">集中查看站内消息、外部推送结果与任务详细信息。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void markAll()} disabled={unread === 0}>
            <CheckCheck className="h-3.5 w-3.5" />
            全部已读
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void clearRead()} disabled={!items.some(item => item.read)}>
            <Trash2 className="h-3.5 w-3.5" />
            清空已读
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-[12px] text-rose-500">{error}</div>
      )}

      <div className="grid grid-cols-3 gap-2 md:max-w-xl md:gap-3">
        {([
          ['all', '全部通知', items.length],
          ['unread', '未读', unread],
          ['failed', '推送失败', failed],
        ] as [FilterKey, string, number][]).map(([key, label, value]) => (
          <button
            key={key}
            type="button"
            onClick={() => setFilter(key)}
            className={`rounded-xl border px-3 py-3 text-left transition-colors ${
              filter === key ? 'border-primary/35 bg-primary/10' : 'border-border/50 bg-card hover:bg-accent/40'
            }`}
          >
            <div className="text-[11px] text-muted-foreground">{label}</div>
            <div className="mt-0.5 text-[19px] font-semibold text-foreground">{value}</div>
          </button>
        ))}
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        <button
          type="button"
          onClick={() => setCategory('')}
          className={`whitespace-nowrap rounded-full px-3 py-1.5 text-[11px] transition-colors ${!category ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:text-foreground'}`}
        >
          全部类型
        </button>
        {categories.map(value => (
          <button
            key={value}
            type="button"
            onClick={() => setCategory(value)}
            className={`whitespace-nowrap rounded-full px-3 py-1.5 text-[11px] transition-colors ${category === value ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:text-foreground'}`}
          >
            {CATEGORY_LABELS[value] || value}
          </button>
        ))}
      </div>

      <div className="grid min-h-[560px] overflow-hidden rounded-2xl border border-border/50 bg-card lg:grid-cols-[minmax(320px,0.82fr)_minmax(0,1.45fr)]">
        <section className="border-b border-border/50 lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between border-b border-border/40 px-4 py-3">
            <span className="text-[12px] font-medium text-foreground">通知列表</span>
            <span className="text-[11px] text-muted-foreground">{filtered.length} 条</span>
          </div>
          <div className="max-h-[420px] overflow-y-auto lg:max-h-[620px]">
            {loading ? (
              <div className="flex min-h-[280px] items-center justify-center text-[12px] text-muted-foreground">
                <RefreshCw className="mr-2 h-4 w-4 animate-spin" />加载中…
              </div>
            ) : filtered.length === 0 ? (
              <EmptyState filtered={items.length > 0} />
            ) : filtered.map(item => {
              const meta = LEVEL_META[item.level] || LEVEL_META.info
              const Icon = meta.icon
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => void selectItem(item)}
                  className={`flex w-full gap-3 border-b border-border/30 px-4 py-3.5 text-left transition-colors last:border-0 ${selectedId === item.id ? 'bg-primary/8' : 'hover:bg-accent/35'}`}
                >
                  <span className={`mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${meta.className}`}>
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      {!item.read && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />}
                      <span className="truncate text-[12.5px] font-medium text-foreground">{item.title || '未命名通知'}</span>
                    </span>
                    <span className="mt-1 block line-clamp-2 text-[11px] leading-5 text-muted-foreground">{item.body || '无正文'}</span>
                    <span className="mt-1.5 flex items-center gap-2 text-[10px] text-muted-foreground/70">
                      <span>{formatDateTime(item.created_at)}</span>
                      {item.push_status && <span className={item.push_status === 'failed' ? 'text-rose-500' : item.push_status === 'sent' ? 'text-emerald-500' : ''}>{PUSH_META[item.push_status]?.label || item.push_status}</span>}
                    </span>
                  </span>
                  <ChevronRight className="mt-2 h-4 w-4 shrink-0 text-muted-foreground/50" />
                </button>
              )
            })}
          </div>
        </section>

        <section className="min-w-0">
          {!selected ? (
            <EmptyState filtered={false} />
          ) : (
            <div className="p-5 md:p-6">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border/40 pb-5">
                <div className="flex min-w-0 gap-3">
                  <span className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${selectedLevel.className}`}>
                    <SelectedLevelIcon className="h-5 w-5" />
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-[17px] font-semibold text-foreground">{selected.title || '未命名通知'}</h2>
                      <span className="rounded-full bg-accent/60 px-2 py-0.5 text-[10px] text-muted-foreground">{CATEGORY_LABELS[selected.category] || selected.category || '系统'}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] ${selected.read ? 'bg-accent/60 text-muted-foreground' : 'bg-rose-500/10 text-rose-500'}`}>{selected.read ? '已读' : '未读'}</span>
                    </div>
                    <div className="mt-1 text-[11px] text-muted-foreground">{formatDateTime(selected.created_at)}</div>
                  </div>
                </div>
                {linkedPage && (
                  <Button size="sm" onClick={() => nav(linkedPage)}>
                    <ExternalLink className="h-3.5 w-3.5" />
                    查看关联页面
                  </Button>
                )}
              </div>

              <div className="grid gap-3 border-b border-border/40 py-5 sm:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">站内状态</div>
                  <div className="mt-1 flex items-center gap-1.5 text-[12px] font-medium text-foreground"><CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />已送达消息中心</div>
                </div>
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">外部推送</div>
                  <div className={`mt-1 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ${selectedPush?.className || 'bg-accent text-muted-foreground'}`}>
                    <Send className="h-3 w-3" />{selectedPush?.label || '未记录'}
                  </div>
                </div>
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">来源</div>
                  <div className="mt-1 truncate text-[12px] font-medium text-foreground" title={selected.source}>{selected.source || '系统'}</div>
                </div>
                <div className="rounded-xl bg-accent/30 p-3">
                  <div className="text-[10px] text-muted-foreground">级别</div>
                  <div className="mt-1 text-[12px] font-medium text-foreground">{selectedLevel.label}</div>
                </div>
              </div>

              {selected.push_error && (
                <div className="mt-5 rounded-xl border border-rose-500/25 bg-rose-500/8 p-4">
                  <div className="flex items-center gap-2 text-[12px] font-medium text-rose-500"><AlertCircle className="h-4 w-4" />推送失败详情</div>
                  <div className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-rose-400">{selected.push_error}</div>
                </div>
              )}

              <div className="mt-5">
                <div className="mb-2 text-[11px] font-medium text-muted-foreground">通知正文</div>
                {selected.body ? (
                  <div className="overflow-x-auto rounded-xl border border-border/40 bg-background/40 p-4 text-[13px] leading-6 text-foreground">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        table: ({ children }) => <table className="my-3 min-w-full border-collapse text-[12px]">{children}</table>,
                        th: ({ children }) => <th className="border border-border/50 bg-accent/50 px-3 py-2 text-left font-medium">{children}</th>,
                        td: ({ children }) => <td className="border border-border/50 px-3 py-2 align-top">{children}</td>,
                        a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">{children}</a>,
                        code: ({ children }) => <code className="rounded bg-accent/70 px-1 py-0.5 text-[12px]">{children}</code>,
                        ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
                        ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
                      }}
                    >
                      {selected.body}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <div className="rounded-xl border border-dashed border-border/60 px-4 py-8 text-center text-[12px] text-muted-foreground">该通知没有正文。</div>
                )}
              </div>

              {(selected.trace_id || linkedPage) && (
                <details className="mt-5 rounded-xl border border-border/40 bg-accent/20 px-4 py-3">
                  <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">技术详情</summary>
                  <dl className="mt-3 grid gap-2 text-[11px] sm:grid-cols-[88px_1fr]">
                    {selected.trace_id && <><dt className="text-muted-foreground">Trace ID</dt><dd className="break-all font-mono text-foreground">{selected.trace_id}</dd></>}
                    {linkedPage && <><dt className="text-muted-foreground">关联地址</dt><dd className="break-all font-mono text-foreground">{linkedPage}</dd></>}
                  </dl>
                </details>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
