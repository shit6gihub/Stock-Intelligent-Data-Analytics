/**
 * 数据源失败显式标识横幅(2026-08-17)
 *
 * 用途: API 调用失败时,显式告诉用户是哪个数据源/服务挂了,
 * 而不只是统一 "部分数据加载失败"。
 *
 * 用法:
 *   const [errors, setErrors] = useState<SourceError[]>([])
 *   ...
 *   api.x().catch((e) => {
 *     setErrors(prev => [...prev, { source: '同花顺资金流', message: String(e), retry: load }])
 *   })
 *   ...
 *   <ErrorBanner errors={errors} onDismiss={(i) => setErrors(...)} />
 */

import { useState } from 'react'
import { AlertTriangle, X, RotateCw } from 'lucide-react'

export interface SourceError {
  /** 数据源/服务名, 用于展示 */
  source: string
  /** 错误消息(简短) */
  message: string
  /** 重试回调(可选) */
  retry?: () => void
  /** 是否自动消失(默认 true,3秒) */
  auto_dismiss?: boolean
}

interface ErrorBannerProps {
  errors: SourceError[]
  onDismiss?: (idx: number) => void
  /** 是否允许一键重试全部 */
  retryAll?: () => void
}

export function ErrorBanner({ errors, onDismiss, retryAll }: ErrorBannerProps) {
  if (errors.length === 0) return null

  return (
    <div
      className="mb-3 space-y-1.5"
      role="alert"
      aria-live="polite"
      data-testid="error-banner"
    >
      {errors.map((err, idx) => (
        <ErrorItem
          key={`${err.source}-${idx}`}
          err={err}
          onDismiss={onDismiss ? () => onDismiss(idx) : undefined}
        />
      ))}
      {retryAll && errors.some(e => e.retry) && (
        <div className="flex items-center justify-end gap-2 text-[11px]">
          <button
            type="button"
            onClick={retryAll}
            className="flex items-center gap-1 rounded-md px-2 py-0.5 text-primary transition-colors hover:text-primary/80"
          >
            <RotateCw className="h-3 w-3" />
            全部重试
          </button>
        </div>
      )}
    </div>
  )
}

export default ErrorBanner

function ErrorItem({ err, onDismiss }: { err: SourceError; onDismiss?: () => void }) {
  // 自动消失 3s(可选)
  const [hidden, setHidden] = useState(false)
  if (err.auto_dismiss !== false && err.auto_dismiss !== undefined) {
    // 不在这里自动消失 — 用 onDismiss
  }

  if (hidden) return null

  // 截断 message 到 80 字
  const shortMsg = (err.message || '服务不可用').slice(0, 80)

  return (
    <div className="flex items-center gap-2 rounded-lg border border-amber-300/40 bg-amber-50/60 dark:bg-amber-950/20 px-3 py-2 text-[12px]">
      <AlertTriangle className="h-3.5 w-3.5 text-amber-600 shrink-0" />
      <div className="min-w-0 flex-1">
        <span className="font-medium text-foreground/90">{err.source}</span>
        <span className="mx-1.5 text-muted-foreground">·</span>
        <span className="text-muted-foreground" title={err.message}>{shortMsg}</span>
      </div>
      {err.retry && (
        <button
          type="button"
          onClick={() => err.retry?.()}
          className="flex items-center gap-0.5 rounded-md px-2 py-0.5 text-[11px] text-primary transition-colors hover:text-primary/80"
          title="重试"
        >
          <RotateCw className="h-3 w-3" />
          重试
        </button>
      )}
      {onDismiss && (
        <button
          type="button"
          onClick={() => {
            setHidden(true)
            onDismiss()
          }}
          className="rounded-md p-0.5 text-muted-foreground transition-colors hover:text-foreground"
          title="忽略"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </div>
  )
}