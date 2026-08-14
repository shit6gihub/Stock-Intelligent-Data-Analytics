import { fetchAPI } from './client'

// ── 扫码绑定个人微信(OpenClaw 渠道) ──
// 后端端点:
//   POST   /notify/wechat-bind/start   → { bind_id, qrcode_url }
//   GET    /notify/wechat-bind/status?bind=<bind_id> → { bind_id, status: 'waiting'|'success', ... }
//   DELETE /notify/wechat-bind/        → 解除绑定
//   GET    /notify/wechat-bind/        → 当前绑定信息(未绑定返回 account_id 为空)

export interface WechatBindStartResult {
  bind_id: string
  qrcode_url: string
  expires_in?: number
}

export interface WechatBindStatusResult {
  bind_id: string
  status: 'waiting' | 'success' | 'failed' | 'expired'
  user_id?: string | null
  account_id?: string | null
  message?: string
}

export interface WechatBindInfo {
  account_id?: string | null
  user_id?: string | null
  nickname?: string | null
  bound_at?: string | null
  [key: string]: unknown
}

/** 发起扫码绑定,返回 bind_id 与二维码链接(成功后后端自动保存 openclaw 渠道) */
export function wechatBindStart(): Promise<WechatBindStartResult> {
  return fetchAPI<WechatBindStartResult>('/notify/wechat-bind/start', { method: 'POST' })
}

/** 查询绑定状态(waiting / success)。轮询用,禁用 GET 缓存避免读到旧状态 */
export function wechatBindStatus(bindId: string): Promise<WechatBindStatusResult> {
  return fetchAPI<WechatBindStatusResult>(
    `/notify/wechat-bind/status?bind=${encodeURIComponent(bindId)}`,
    { cacheMode: false },
  )
}

/** 解除当前个人微信绑定 */
export function wechatBindUnbind(): Promise<{ message?: string }> {
  return fetchAPI<{ message?: string }>('/notify/wechat-bind/', { method: 'DELETE' })
}

/** 查询当前绑定信息(未绑定时 account_id 为空) */
export function wechatBindGet(): Promise<WechatBindInfo> {
  return fetchAPI<WechatBindInfo>('/notify/wechat-bind/', { cacheMode: false })
}
