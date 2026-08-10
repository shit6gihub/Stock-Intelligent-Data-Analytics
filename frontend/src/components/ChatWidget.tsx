import { useCallback, useEffect, useRef, useState } from 'react'
import { MessageCircle, X, Plus, Trash2, Send, ChevronLeft, XCircle, Settings2, Check } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { chatApi, type ChatConversation, type ChatMessage } from '@panwatch/api'

interface StockContext {
  symbol: string
  market: string
  stockName: string
  pageContext?: string
}

type DesktopChatSize = 'compact' | 'standard' | 'large' | 'wide'
type DesktopChatPosition = 'left' | 'center' | 'right'

const CHAT_SIZE_STORAGE_KEY = 'panwatch_chat_desktop_size'
const CHAT_POSITION_STORAGE_KEY = 'panwatch_chat_desktop_position'

const DESKTOP_SIZE_OPTIONS: Array<{ value: DesktopChatSize; label: string; detail: string }> = [
  { value: 'compact', label: '紧凑', detail: '360 × 480' },
  { value: 'standard', label: '标准', detail: '420 × 600' },
  { value: 'large', label: '大窗口', detail: '560 × 720' },
  { value: 'wide', label: '宽屏', detail: '720 × 760' },
]

const DESKTOP_POSITION_OPTIONS: Array<{ value: DesktopChatPosition; label: string }> = [
  { value: 'left', label: '左下' },
  { value: 'center', label: '底部居中' },
  { value: 'right', label: '右下' },
]

const DESKTOP_SIZE_CLASSES: Record<DesktopChatSize, string> = {
  compact: 'md:w-[360px] md:h-[480px]',
  standard: 'md:w-[420px] md:h-[600px]',
  large: 'md:w-[560px] md:h-[720px]',
  wide: 'md:w-[720px] md:h-[760px]',
}

const DESKTOP_POSITION_CLASSES: Record<DesktopChatPosition, string> = {
  left: 'md:left-5 md:right-auto md:translate-x-0',
  center: 'md:left-1/2 md:right-auto md:-translate-x-1/2',
  right: 'md:left-auto md:right-5 md:translate-x-0',
}

function readDesktopChatSize(): DesktopChatSize {
  if (typeof window === 'undefined') return 'standard'
  try {
    const value = window.localStorage.getItem(CHAT_SIZE_STORAGE_KEY)
    if (value === 'compact' || value === 'standard' || value === 'large' || value === 'wide') return value
  } catch {
    // localStorage may be unavailable in privacy-restricted browsers.
  }
  return 'standard'
}

function readDesktopChatPosition(): DesktopChatPosition {
  if (typeof window === 'undefined') return 'right'
  try {
    const value = window.localStorage.getItem(CHAT_POSITION_STORAGE_KEY)
    if (value === 'left' || value === 'center' || value === 'right') return value
  } catch {
    // localStorage may be unavailable in privacy-restricted browsers.
  }
  return 'right'
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [activeConvId, setActiveConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [view, setView] = useState<'list' | 'chat'>('list')
  const [stockContext, setStockContext] = useState<StockContext | null>(null)
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([])
  const [desktopSize, setDesktopSize] = useState<DesktopChatSize>(readDesktopChatSize)
  const [desktopPosition, setDesktopPosition] = useState<DesktopChatPosition>(readDesktopChatPosition)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const settingsRef = useRef<HTMLDivElement>(null)

  const loadConversations = useCallback(async () => {
    try {
      const list = await chatApi.listConversations(30)
      setConversations(list)
    } catch {
      // ignore
    }
  }, [])

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const detail = await chatApi.getConversation(convId)
      setMessages(detail.messages)
    } catch {
      // ignore
    }
  }, [])

  const loadSuggestedQuestions = useCallback(async (symbol: string, market: string) => {
    try {
      const res = await chatApi.getSuggestedQuestions(symbol, market)
      setSuggestedQuestions(res.questions || [])
    } catch {
      setSuggestedQuestions([])
    }
  }, [])

  // Listen for stock context events from stock insight modal
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as StockContext
      if (!detail?.symbol) return
      setOpen(true)
      setStockContext(detail)
      setSuggestedQuestions([])

      // Create a new conversation bound to this stock, with page context
      chatApi.createConversation({
        stock_symbol: detail.symbol,
        stock_market: detail.market,
        initial_context: detail.pageContext,
      }).then((conv) => {
        setActiveConvId(conv.id)
        setMessages([])
        setView('chat')
        setConversations((prev) => [conv, ...prev])
        loadSuggestedQuestions(detail.symbol, detail.market)
      }).catch(() => {
        // fallback: just open chat
        setView('chat')
      })
    }
    window.addEventListener('panwatch-open-chat', handler)
    return () => window.removeEventListener('panwatch-open-chat', handler)
  }, [loadSuggestedQuestions])

  useEffect(() => {
    if (open) {
      loadConversations()
    }
  }, [open, loadConversations])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    try {
      window.localStorage.setItem(CHAT_SIZE_STORAGE_KEY, desktopSize)
    } catch {
      // Keep the current session setting even when persistence is unavailable.
    }
  }, [desktopSize])

  useEffect(() => {
    try {
      window.localStorage.setItem(CHAT_POSITION_STORAGE_KEY, desktopPosition)
    } catch {
      // Keep the current session setting even when persistence is unavailable.
    }
  }, [desktopPosition])

  useEffect(() => {
    if (!settingsOpen) return

    const handlePointerDown = (event: MouseEvent) => {
      if (!settingsRef.current?.contains(event.target as Node)) setSettingsOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSettingsOpen(false)
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [settingsOpen])

  const openConversation = useCallback(async (conv: ChatConversation) => {
    setActiveConvId(conv.id)
    setView('chat')
    setSuggestedQuestions([])
    if (conv.stock_symbol && conv.stock_market) {
      setStockContext({ symbol: conv.stock_symbol, market: conv.stock_market, stockName: '' })
      loadSuggestedQuestions(conv.stock_symbol, conv.stock_market)
    } else {
      setStockContext(null)
    }
    await loadMessages(conv.id)
  }, [loadMessages, loadSuggestedQuestions])

  const createNewConversation = useCallback(async () => {
    try {
      const conv = await chatApi.createConversation()
      setActiveConvId(conv.id)
      setMessages([])
      setView('chat')
      setStockContext(null)
      setSuggestedQuestions([])
      setConversations((prev) => [conv, ...prev])
    } catch {
      // ignore
    }
  }, [])

  const deleteConversation = useCallback(async (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await chatApi.deleteConversation(convId)
      setConversations((prev) => prev.filter((c) => c.id !== convId))
      if (activeConvId === convId) {
        setActiveConvId(null)
        setMessages([])
        setView('list')
        setStockContext(null)
        setSuggestedQuestions([])
      }
    } catch {
      // ignore
    }
  }, [activeConvId])

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent || input).trim()
    if (!content || sending) return

    let convId = activeConvId
    if (!convId) {
      try {
        const conv = await chatApi.createConversation(
          stockContext ? { stock_symbol: stockContext.symbol, stock_market: stockContext.market } : undefined
        )
        convId = conv.id
        setActiveConvId(conv.id)
        setConversations((prev) => [conv, ...prev])
        setView('chat')
      } catch {
        return
      }
    }

    setInput('')
    setSending(true)
    setSuggestedQuestions([]) // hide after first send

    const tempUserMsg: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, tempUserMsg])

    try {
      const reply = await chatApi.sendMessage(convId, content)
      setMessages((prev) => [...prev, reply])
      setConversations((prev) =>
        prev.map((c) => c.id === convId ? { ...c, title: c.title || content.slice(0, 20) } : c)
      )
    } catch (e) {
      const errMsg: ChatMessage = {
        id: Date.now() + 1,
        role: 'assistant',
        content: `请求失败：${e instanceof Error ? e.message : '未知错误'}`,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, errMsg])
    } finally {
      setSending(false)
    }
  }, [input, sending, activeConvId, stockContext])

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className={`fixed bottom-20 right-4 md:bottom-5 z-40 w-12 h-12 rounded-full bg-primary text-primary-foreground shadow-lg flex items-center justify-center hover:bg-primary/90 transition-all hover:scale-105 ${DESKTOP_POSITION_CLASSES[desktopPosition]}`}
        title="打开 AI 助手"
        aria-label="打开 AI 助手"
      >
        <MessageCircle className="w-5 h-5" />
      </button>
    )
  }

  return (
    <>
      <button
        type="button"
        onClick={() => { setSettingsOpen(false); setOpen(false) }}
        className="fixed inset-0 z-[55] hidden bg-black/25 backdrop-blur-[2px] md:block dark:bg-black/45"
        aria-label="关闭 AI 助手背景遮罩"
      />
      <div className={`fixed bottom-0 right-0 z-[60] w-full h-full md:bottom-5 md:max-w-[calc(100vw-2.5rem)] md:max-h-[calc(100vh-2.5rem)] md:rounded-xl bg-background border border-border/60 shadow-2xl md:border-primary/30 md:ring-1 md:ring-white/10 md:shadow-[0_24px_90px_rgba(0,0,0,0.75)] flex flex-col overflow-hidden ${DESKTOP_SIZE_CLASSES[desktopSize]} ${DESKTOP_POSITION_CLASSES[desktopPosition]}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40 bg-accent/20">
        <div className="flex items-center gap-2">
          {view === 'chat' && (
            <button
              onClick={() => { setView('list'); setStockContext(null); setSuggestedQuestions([]); loadConversations() }}
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          )}
          <span className="text-[14px] font-semibold text-foreground">AI 助手</span>
          {view === 'chat' && stockContext && (
            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-primary/10 text-primary">
              {stockContext.market}:{stockContext.symbol}
              {stockContext.stockName && ` ${stockContext.stockName}`}
              <button
                onClick={() => { setStockContext(null); setSuggestedQuestions([]) }}
                className="hover:text-primary/70 transition-colors"
              >
                <XCircle className="w-3 h-3" />
              </button>
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {view === 'list' && (
            <button
              onClick={createNewConversation}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
              title="新建对话"
            >
              <Plus className="w-4 h-4" />
            </button>
          )}
          <div ref={settingsRef} className="relative hidden md:block">
            <button
              onClick={() => setSettingsOpen((current) => !current)}
              className={`p-1.5 rounded-md transition-colors ${settingsOpen ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'}`}
              title="窗口设置"
              aria-label="窗口设置"
              aria-expanded={settingsOpen}
            >
              <Settings2 className="w-4 h-4" />
            </button>
            {settingsOpen && (
              <div className="absolute right-0 top-9 z-20 w-[280px] rounded-xl border border-border/70 bg-background p-3 shadow-2xl">
                <div className="mb-2 text-[11px] font-medium text-muted-foreground">窗口大小</div>
                <div className="grid grid-cols-2 gap-1.5">
                  {DESKTOP_SIZE_OPTIONS.map((option) => {
                    const active = desktopSize === option.value
                    return (
                      <button
                        key={option.value}
                        onClick={() => setDesktopSize(option.value)}
                        className={`flex min-w-0 items-center justify-between rounded-lg border px-2.5 py-2 text-left transition-colors ${active ? 'border-primary/60 bg-primary/10 text-primary' : 'border-border/50 bg-accent/20 text-foreground hover:bg-accent/50'}`}
                      >
                        <span className="min-w-0">
                          <span className="block text-[12px] font-medium">{option.label}</span>
                          <span className="block text-[10px] text-muted-foreground">{option.detail}</span>
                        </span>
                        {active && <Check className="ml-1 h-3.5 w-3.5 shrink-0" />}
                      </button>
                    )
                  })}
                </div>

                <div className="mb-2 mt-3 text-[11px] font-medium text-muted-foreground">停靠位置</div>
                <div className="grid grid-cols-3 gap-1.5">
                  {DESKTOP_POSITION_OPTIONS.map((option) => {
                    const active = desktopPosition === option.value
                    return (
                      <button
                        key={option.value}
                        onClick={() => setDesktopPosition(option.value)}
                        className={`flex items-center justify-center gap-1 rounded-lg border px-2 py-2 text-[11px] transition-colors ${active ? 'border-primary/60 bg-primary/10 text-primary' : 'border-border/50 bg-accent/20 text-foreground hover:bg-accent/50'}`}
                      >
                        {active && <Check className="h-3 w-3 shrink-0" />}
                        {option.label}
                      </button>
                    )
                  })}
                </div>
                <p className="mt-2.5 text-[10px] leading-relaxed text-muted-foreground">设置保存在当前浏览器，悬浮按钮会同步移动。</p>
              </div>
            )}
          </div>
          <button
            onClick={() => { setSettingsOpen(false); setOpen(false) }}
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
            aria-label="关闭 AI 助手"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* List view */}
      {view === 'list' && (
        <div className="flex-1 overflow-y-auto scrollbar">
          {conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-3">
              <MessageCircle className="w-8 h-8 opacity-30" />
              <p>暂无对话</p>
              <button
                onClick={createNewConversation}
                className="text-[12px] px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                开始新对话
              </button>
            </div>
          ) : (
            conversations.map((conv) => (
              <button
                key={conv.id}
                onClick={() => openConversation(conv)}
                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-accent/30 transition-colors border-b border-border/20"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] text-foreground truncate">
                    {conv.title || '新对话'}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {conv.stock_symbol ? `${conv.stock_market}:${conv.stock_symbol} · ` : ''}
                    {new Date(conv.created_at).toLocaleDateString()}
                  </div>
                </div>
                <button
                  onClick={(e) => deleteConversation(conv.id, e)}
                  className="p-1 rounded text-muted-foreground/50 hover:text-rose-400 transition-colors shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </button>
            ))
          )}
        </div>
      )}

      {/* Chat view */}
      {view === 'chat' && (
        <>
          <div className="flex-1 overflow-y-auto scrollbar px-4 py-3 space-y-3">
            {/* Suggested questions */}
            {messages.length === 0 && suggestedQuestions.length > 0 && (
              <div className="flex flex-col gap-2">
                <span className="text-[11px] text-muted-foreground">推荐问题</span>
                <div className="flex flex-wrap gap-2">
                  {suggestedQuestions.map((q) => (
                    <button
                      key={q}
                      className="text-[11px] px-3 py-1.5 rounded-full bg-primary/10 text-primary hover:bg-primary/20 transition-colors text-left"
                      onClick={() => handleSend(q)}
                      disabled={sending}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.length === 0 && suggestedQuestions.length === 0 && !sending && (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-2">
                <MessageCircle className="w-6 h-6 opacity-30" />
                <p>输入问题开始对话</p>
              </div>
            )}
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`min-w-0 max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-accent/60 text-foreground'
                  }`}
                >
                  {msg.role === 'assistant' ? (
                    <div className="prose prose-sm dark:prose-invert max-w-none min-w-0 break-words [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px] [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:text-[11px] [&_code]:break-words">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          table: ({ children }) => (
                            <div className="my-2 max-w-full overflow-x-auto rounded-lg border border-border/60">
                              <table className="m-0 w-max min-w-full border-collapse text-[11px]">{children}</table>
                            </div>
                          ),
                          th: ({ children }) => (
                            <th className="whitespace-nowrap border-b border-r border-border/60 bg-background/60 px-2 py-1.5 text-left font-semibold last:border-r-0">
                              {children}
                            </th>
                          ),
                          td: ({ children }) => (
                            <td className="min-w-[88px] border-b border-r border-border/40 px-2 py-1.5 align-top last:border-r-0">
                              {children}
                            </td>
                          ),
                          a: ({ children, href }) => (
                            <a href={href} target="_blank" rel="noopener noreferrer" className="break-all text-primary underline underline-offset-2">
                              {children}
                            </a>
                          ),
                        }}
                      >
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    msg.content
                  )}
                </div>
              </div>
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="bg-accent/60 rounded-xl px-3 py-2 text-[13px] text-muted-foreground flex items-center gap-2">
                  <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                  思考中...
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Input */}
          <div className="flex items-center gap-2 px-4 py-3 border-t border-border/40">
            <input
              type="text"
              className="flex-1 h-9 px-3 rounded-lg bg-accent/40 text-[13px] text-foreground placeholder:text-muted-foreground outline-none focus:ring-1 focus:ring-primary/30"
              placeholder="输入问题..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              disabled={sending}
            />
            <button
              className="h-9 w-9 rounded-lg bg-primary text-primary-foreground flex items-center justify-center hover:bg-primary/90 transition-colors disabled:opacity-50"
              onClick={() => handleSend()}
              disabled={sending || !input.trim()}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </>
      )}
      </div>
    </>
  )
}
