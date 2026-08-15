import { useState, useEffect } from 'react'
export { cn } from '@panwatch/base-ui'

/**
 * 持久化到 localStorage 的 useState
 * @param key localStorage 键名
 * @param defaultValue 默认值
 */
export function useLocalStorage<T>(key: string, defaultValue: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key)
      if (saved !== null) {
        return JSON.parse(saved)
      }
    } catch {
      // ignore
    }
    return defaultValue
  })

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // ignore
    }
  }, [key, value])

  return [value, setValue]
}

// ==================== 时间格式化工具 ====================

/**
 * 解析后端时间字符串为 Date。
 * 后端时间全部为 SQLite UTC(func.now(), 序列化时无时区标记),
 * 裸字符串直接 new Date() 会被当作浏览器本地时间 → 显示偏移 8 小时。
 * 无时区标记的统一按 UTC 解析, 再交给 toLocale* 转本地显示。
 */
export function parseServerTime(iso?: string | null): Date {
  if (!iso) return new Date(NaN)
  const s = iso.trim()
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  if (hasTz) return new Date(s)
  // 含时间部分(HH:MM)的裸字符串按 UTC 解析; 纯日期(YYYY-MM-DD) JS 规范本身按 UTC 解析, 不加 Z
  return new Date(/\d{2}:\d{2}/.test(s) ? `${s}Z` : s)
}

/**
 * 格式化 ISO 时间为本地时间（仅时间）
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "15:30"
 */
export function formatTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = parseServerTime(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

/**
 * 格式化 ISO 时间为本地日期时间
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "01/26 15:30"
 */
export function formatDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = parseServerTime(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

/**
 * 格式化 ISO 时间为完整本地日期时间
 * @param isoTime ISO 格式时间字符串
 * @returns 如 "2024-01-26 15:30:00"
 */
export function formatFullDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  try {
    const date = parseServerTime(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}
