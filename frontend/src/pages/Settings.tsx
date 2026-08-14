import { useState, useEffect, useRef } from 'react'
import { Check, Eye, EyeOff, Plus, Pencil, Trash2, Star, Send, Cpu, Play, Download, Upload, FileJson, BarChart3, User, Radar, RefreshCw, QrCode, MonitorUp, MailCheck, Copy } from 'lucide-react'
import { fetchAPI, listSceneBindings, setSceneBinding, wechatBindStart, wechatBindStatus, wechatBindUnbind, wechatBindGet, type AIService, type AIModel, type NotifyChannel, type SceneBinding, type UserInfo, type SubscriptionItem, type WechatBindStartResult, type WechatBindInfo, authApi } from '@panwatch/api'
import { QRCodeSVG } from 'qrcode.react'
import UserManagement from '@/components/UserManagement'
import { useAvatar, saveAvatar, fileToAvatarDataUrl } from '@/hooks/use-avatar'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import {
  browserNotificationsEnabled,
  browserNotificationsSupported,
  requestBrowserNotificationPermission,
  setBrowserNotificationsEnabled,
  showBrowserNotification,
} from '@/lib/browser-notifications'

interface Setting {
  key: string
  value: string
  description: string
}

interface KeyDataSource {
  id: number
  name: string
  type: string
  provider: string
  enabled: boolean
  key_count?: number
}

interface TemplatePayload {
  version: number
  exported_at?: string
  settings?: Record<string, string>
  agents?: any[]
  stocks?: any[]
}

interface FeedbackStats {
  range_days: number
  total: number
  useful: number
  useless: number
  useful_rate: number
  by_day: Array<{ day: string; total: number; useful: number; useless: number; useful_rate: number }>
  by_agent: Array<{ agent_name: string; total: number; useful: number; useless: number; useful_rate: number }>
}

interface AgentsHealth {
  timezone: string
  summary: {
    next_24h_count: number
    recent_failed_count: number
  }
}

interface ServiceForm {
  name: string
  base_url: string
  api_key: string
}

interface ModelForm {
  name: string
  service_id: number | null
  model: string
}

interface ChannelForm {
  name: string
  type: string
  config: Record<string, string>
}

interface ChannelFieldDef {
  key: string
  label: string
  placeholder: string
  secret?: boolean
  required?: boolean
}

const CHANNEL_TYPE_FIELDS: Record<string, { label: string; fields: ChannelFieldDef[] }> = {
  telegram: {
    label: 'Telegram',
    fields: [
      { key: 'bot_token', label: 'Bot Token', placeholder: '123456:ABC-DEF...', secret: true, required: true },
      { key: 'chat_id', label: 'Chat ID', placeholder: '-100123456789', required: true },
      { key: 'proxy', label: '代理', placeholder: 'http://192.168.1.1:7890 或 socks5://...' },
    ],
  },
  bark: {
    label: 'Bark',
    fields: [
      { key: 'device_key', label: 'Device Key', placeholder: '你的 Bark Device Key', required: true },
      { key: 'server_url', label: '服务器地址', placeholder: '默认 api.day.app，自建可填' },
    ],
  },
  dingtalk: {
    label: '钉钉机器人',
    fields: [
      { key: 'token', label: 'Webhook Token', placeholder: 'access_token 值', secret: true, required: true },
      { key: 'secret', label: '加签密钥', placeholder: 'SEC... (选填)', secret: true },
      { key: 'phones', label: '@手机号', placeholder: '逗号分隔，如 13800138000,13900139000' },
      { key: 'keyword', label: '关键字', placeholder: '若群机器人启用“关键字”，填入以自动附加' },
    ],
  },
  wecom: {
    label: '企业微信机器人',
    fields: [
      { key: 'webhook_key', label: 'Webhook Key', placeholder: 'Webhook URL 中 key= 后的值', secret: true, required: true },
    ],
  },
  lark: {
    label: '飞书机器人',
    fields: [
      { key: 'webhook_token', label: 'Webhook Token', placeholder: 'hook/ 后面的 token', secret: true, required: true },
    ],
  },
  serverchan: {
    label: 'Server酱',
    fields: [
      { key: 'sendkey', label: 'SendKey', placeholder: 'SCT...', secret: true, required: true },
    ],
  },
  pushplus: {
    label: 'PushPlus',
    fields: [
      { key: 'token', label: 'Token', placeholder: '你的 PushPlus Token', secret: true, required: true },
      { key: 'topic', label: '群组编码', placeholder: '选填，群组推送时填写' },
    ],
  },
  openclaw: {
    label: '个人微信(iLink 直连)',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', placeholder: 'http://<hermes地址>:8644/webhooks/<订阅名>', required: true },
      { key: 'secret', label: 'HMAC 密钥', placeholder: '订阅创建时返回的 Secret', secret: true, required: true },
    ],
  },
  discord: {
    label: 'Discord',
    fields: [
      { key: 'webhook_id', label: 'Webhook ID', placeholder: 'Webhook URL 中的 ID', required: true },
      { key: 'webhook_token', label: 'Webhook Token', placeholder: 'Webhook URL 中的 Token', secret: true, required: true },
    ],
  },
  pushover: {
    label: 'Pushover',
    fields: [
      { key: 'user_key', label: 'User Key', placeholder: '用户 Key', required: true },
      { key: 'app_token', label: 'App Token', placeholder: '应用 Token', secret: true, required: true },
    ],
  },
}

const emptyServiceForm: ServiceForm = { name: '', base_url: '', api_key: '' }
const emptyModelForm: ModelForm = { name: '', service_id: null, model: '' }
const emptyChannelForm: ChannelForm = { name: '', type: 'telegram', config: {} }

// 敏感设置 key:值不回显(后端已掩码为 ********),输入框用密码态,掩码值不参与编辑
const SECRET_SETTING_KEYS = new Set(['wudao_mcp_token', 'zhitu_token', 'tdx_api_key'])
const SECRET_MASK = '********'

export default function SettingsPage() {
  const [settings, setSettings] = useState<Setting[]>([])
  const [keyDataSources, setKeyDataSources] = useState<KeyDataSource[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [version, setVersion] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [health, setHealth] = useState<AgentsHealth | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [edited, setEdited] = useState<Record<string, string>>({})
  // 多用户(2026-08-10 阶段5): 当前用户 + 订阅
  const [currentUser, setCurrentUser] = useState<UserInfo | null>(null)
  const [subscriptions, setSubscriptions] = useState<SubscriptionItem[]>([])
  const [subLoading, setSubLoading] = useState(false)
  // 同花顺登录态
  const [thsSession, setThsSession] = useState<any>(null)
  const [thsQr, setThsQr] = useState<string>('')
  const [thsLoading, setThsLoading] = useState(false)

  const [systemQuery, setSystemQuery] = useState('')

  // Service dialog
  const [serviceDialogOpen, setServiceDialogOpen] = useState(false)
  const [serviceForm, setServiceForm] = useState<ServiceForm>(emptyServiceForm)
  const [editServiceId, setEditServiceId] = useState<number | null>(null)
  const [serviceKeyVisible, setServiceKeyVisible] = useState(false)

  // Model dialog
  const [modelDialogOpen, setModelDialogOpen] = useState(false)
  const [modelForm, setModelForm] = useState<ModelForm>(emptyModelForm)
  const [editModelId, setEditModelId] = useState<number | null>(null)

  // 批量选择嗅探到的模型
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchServiceId, setBatchServiceId] = useState<number | null>(null)
  const [batchCandidates, setBatchCandidates] = useState<string[]>([])
  const [batchChecked, setBatchChecked] = useState<Set<string>>(new Set())
  const [batchDefault, setBatchDefault] = useState<string>('')
  const [submittingBatch, setSubmittingBatch] = useState(false)
  const [discoveringService, setDiscoveringService] = useState<number | null>(null)

  // 场景分配(统一 LLM 配置中心): 6 场景 × 模型绑定
  const [sceneBindings, setSceneBindings] = useState<SceneBinding[]>([])
  const [sceneBindingsLoading, setSceneBindingsLoading] = useState(true)
  const [bindingSaving, setBindingSaving] = useState<string | null>(null)

  // Channel dialog
  const [channelDialogOpen, setChannelDialogOpen] = useState(false)
  const [channelForm, setChannelForm] = useState<ChannelForm>(emptyChannelForm)
  const [editChannelId, setEditChannelId] = useState<number | null>(null)
  const [channelKeyVisible, setChannelKeyVisible] = useState(false)
  const [testing, setTesting] = useState<number | null>(null)
  const [testingModel, setTestingModel] = useState<number | null>(null)
  const [browserPushEnabled, setBrowserPushEnabled] = useState(browserNotificationsEnabled)
  const [browserPushTesting, setBrowserPushTesting] = useState(false)

  // 扫码绑定个人微信(iLink 渠道)
  const [wechatBindInfo, setWechatBindInfo] = useState<WechatBindInfo | null>(null)
  const [wechatBindStarting, setWechatBindStarting] = useState(false)
  const [wechatUnbinding, setWechatUnbinding] = useState(false)
  const [wechatQrOpen, setWechatQrOpen] = useState(false)
  const [wechatQr, setWechatQr] = useState<WechatBindStartResult | null>(null)
  const [wechatQrStatus, setWechatQrStatus] = useState<'waiting' | 'success' | 'scaned' | 'expired'>('waiting')
  const wechatPollRef = useRef<number | null>(null)

  // 头像
  const avatar = useAvatar()
  const avatarFileRef = useRef<HTMLInputElement | null>(null)
  const [avatarSaving, setAvatarSaving] = useState(false)

  // Templates (config pack)
  const [importMode, setImportMode] = useState<'merge' | 'replace'>('merge')
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)

  // Feedback stats
  const [fbStats, setFbStats] = useState<FeedbackStats | null>(null)
  const [fbLoading, setFbLoading] = useState(false)

  const importFileRef = useRef<HTMLInputElement | null>(null)

  const { toast } = useToast()

  const builtinTemplates: Array<{ name: string; desc: string; payload: TemplatePayload }> = [
    {
      name: '保守',
      desc: '低打扰：盘中更严格触发，静默时段建议开启',
      payload: {
        version: 1,
        settings: {
          notify_quiet_hours: '23:00-07:00',
          notify_retry_attempts: '2',
          notify_retry_backoff_seconds: '2',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '30 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '30 15 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/10 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 4.0, volume_alert_ratio: 2.5, throttle_minutes: 45 } },
        ],
      },
    },
    {
      name: '均衡',
      desc: '默认推荐：兼顾覆盖与打扰',
      payload: {
        version: 1,
        settings: {
          notify_retry_attempts: '2',
          notify_retry_backoff_seconds: '2',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '30 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '30 15 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/5 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 3.0, volume_alert_ratio: 2.0, throttle_minutes: 30 } },
        ],
      },
    },
    {
      name: '激进',
      desc: '更高频：更早捕捉变化，适合短线盯盘',
      payload: {
        version: 1,
        settings: {
          notify_retry_attempts: '3',
          notify_retry_backoff_seconds: '1',
        },
        agents: [
          { name: 'premarket_outlook', enabled: true, schedule: '10 8 * * 1-5', execution_mode: 'batch' },
          { name: 'daily_report', enabled: true, schedule: '10 15 * * 1-5', execution_mode: 'batch' },
          { name: 'intraday_monitor', enabled: true, schedule: '*/3 9-15 * * 1-5', execution_mode: 'single', config: { event_only: true, price_alert_threshold: 2.0, volume_alert_ratio: 1.8, throttle_minutes: 20 } },
        ],
      },
    },
  ]

  const load = async () => {
    try {
      const [settingsData, keyDataSourcesData, servicesData, channelsData, versionData, healthData, sceneBindingsData] = await Promise.all([
        fetchAPI<Setting[]>('/settings'),
        fetchAPI<KeyDataSource[]>('/datasources'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
        fetchAPI<{ version: string }>('/settings/version'),
        fetchAPI<AgentsHealth>('/agents/health'),
        listSceneBindings(),
      ])
      setSettings(settingsData)
      setKeyDataSources(keyDataSourcesData)
      setServices(servicesData)
      setChannels(channelsData)
      setVersion(versionData.version)
      setHealth(healthData)
      setSceneBindings(sceneBindingsData)
      setSceneBindingsLoading(false)
      // 同花顺登录态(静默加载,失败不阻塞)
      try {
        const ths = await fetchAPI<any>('/ths/session')
        setThsSession(ths?.data ?? ths)
      } catch { /* 静默 */ }
      // 个人微信绑定状态(静默加载,失败不阻塞)
      void loadWechatBind()
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  // 场景分配: 下拉选中 → 绑定/解绑模型(None=回落默认模型)
  // Radix Select 不允许空字符串 value, 用哨兵值表示"默认模型"(解绑)
  const SCENE_DEFAULT_VALUE = '__default__'
  const handleSceneChange = async (scene: string, value: string) => {
    setBindingSaving(scene)
    try {
      const updated = await setSceneBinding(scene, value === SCENE_DEFAULT_VALUE ? null : Number(value))
      setSceneBindings(prev => prev.map(b => (b.scene === scene ? updated : b)))
      toast(value === SCENE_DEFAULT_VALUE ? '已解绑，该场景回落默认模型' : `已绑定: ${updated.model_name || ''}`, 'success')
    } catch (e) {
      toast(e instanceof Error ? `绑定失败: ${e.message}` : '绑定失败，请重试', 'error')
    } finally {
      setBindingSaving(null)
    }
  }

  const downloadJson = (name: string, obj: any) => {
    try {
      const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = name
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      // ignore
    }
  }

  const exportTemplate = async () => {
    setExporting(true)
    try {
      const data = await fetchAPI<TemplatePayload>('/templates/export')
      const date = new Date().toISOString().slice(0, 10)
      downloadJson(`panwatch-config-${date}.json`, data)
      toast('配置包已导出', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '导出失败', 'error')
    } finally {
      setExporting(false)
    }
  }

  // 同花顺扫码登录
  const thsGetQrcode = async () => {
    setThsLoading(true)
    setThsQr('')
    try {
      const d = await fetchAPI<any>('/ths/qrcode', { method: 'POST' })
      const data = d?.data ?? d
      setThsQr(data?.img_base64 ?? '')
      // 轮询扫码结果(每 3 秒,最多 2 分钟)
      const qrid = data?.qrid
      if (!qrid) { toast('未获取到二维码', 'error'); return }
      for (let i = 0; i < 40; i++) {
        await new Promise(r => setTimeout(r, 3000))
        try {
          const r = await fetchAPI<any>(`/ths/qrcode/${qrid}`)
          const rd = r?.data ?? r
          if (rd?.logged_in) {
            setThsSession(rd)
            setThsQr('')
            toast('同花顺登录成功', 'success')
            return
          }
        } catch { /* 继续轮询 */ }
      }
      toast('扫码超时,请重新生成', 'error')
    } catch (e) {
      toast(e instanceof Error ? e.message : '生成二维码失败', 'error')
    } finally {
      setThsLoading(false)
    }
  }

  const thsRefresh = async () => {
    try {
      const d = await fetchAPI<any>('/ths/session')
      setThsSession(d?.data ?? d)
      toast('已刷新登录态', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '刷新失败', 'error')
    }
  }

  const importTemplate = async (payload: TemplatePayload) => {
    setImporting(true)
    try {
      const resp = await fetchAPI<any>(`/templates/import?mode=${importMode}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      toast('配置包已导入', 'success')
      // refresh
      await load()
      return resp
    } catch (e) {
      toast(e instanceof Error ? e.message : '导入失败', 'error')
      return null
    } finally {
      setImporting(false)
    }
  }

  const loadFeedbackStats = async () => {
    setFbLoading(true)
    try {
      const stats = await fetchAPI<FeedbackStats>('/feedback/stats?days=14')
      setFbStats(stats)
    } catch (e) {
      console.error(e)
      setFbStats(null)
    } finally {
      setFbLoading(false)
    }
  }

  useEffect(() => { load(); loadFeedbackStats() }, [])

  // 卸载时停止扫码轮询
  useEffect(() => () => stopWechatPoll(), [])

  // 多用户: 当前用户 + 订阅(2026-08-10 阶段5)
  useEffect(() => {
    try {
      const raw = localStorage.getItem('user')
      if (raw) setCurrentUser(JSON.parse(raw) as UserInfo)
    } catch { /* ignore */ }
    // 刷新用户信息(后端为准)
    authApi.me().then(d => {
      setCurrentUser(d.user)
      localStorage.setItem('user', JSON.stringify(d.user))
    }).catch(() => {})
    // 订阅
    setSubLoading(true)
    authApi.listSubscriptions().then(d => {
      setSubscriptions(d.subscriptions || [])
    }).catch(() => {}).finally(() => setSubLoading(false))
  }, [])

  const toggleSubscription = async (reportType: string) => {
    const target = subscriptions.find(s => s.report_type === reportType)
    if (!target) return
    const next = !target.enabled
    setSubscriptions(prev => prev.map(s => s.report_type === reportType ? { ...s, enabled: next } : s))
    try {
      await authApi.updateSubscription(reportType, next)
    } catch (e) {
      setSubscriptions(prev => prev.map(s => s.report_type === reportType ? { ...s, enabled: !next } : s))
      toast(e instanceof Error ? e.message : '更新失败', 'error')
    }
  }

  const onPickAvatar = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = '' // 允许重复选择同一文件
    if (!file) return
    setAvatarSaving(true)
    try {
      const dataUrl = await fileToAvatarDataUrl(file)
      await saveAvatar(dataUrl)
      toast('头像已更新', 'success')
    } catch (err) {
      toast(err instanceof Error ? err.message : '头像保存失败', 'error')
    } finally {
      setAvatarSaving(false)
    }
  }


  const handleSave = async (key: string) => {
    setSaving(key)
    try {
      const existing = settings.find(s => s.key === key)?.value
      const next = edited[key] ?? existing
      // 敏感 key:留空 = 不修改(防止覆盖已配置的 token)
      if (SECRET_SETTING_KEYS.has(key) && !next) {
        const newEdited = { ...edited }
        delete newEdited[key]
        setEdited(newEdited)
        setSaved(key)
        setTimeout(() => setSaved(null), 2000)
        return
      }
      await fetchAPI(`/settings/${key}`, {
        method: 'PUT',
        body: JSON.stringify({ value: next }),
      })
      const newEdited = { ...edited }
      delete newEdited[key]
      setEdited(newEdited)
      setSaved(key)
      setTimeout(() => setSaved(null), 2000)
      load()
    } catch {
      toast('保存失败', 'error')
    } finally {
      setSaving(null)
    }
  }

  // Service CRUD
  const openServiceDialog = (svc?: AIService) => {
    if (svc) {
      setServiceForm({ name: svc.name, base_url: svc.base_url, api_key: svc.api_key })
      setEditServiceId(svc.id)
    } else {
      setServiceForm(emptyServiceForm)
      setEditServiceId(null)
    }
    setServiceKeyVisible(false)
    setServiceDialogOpen(true)
  }

  const saveService = async () => {
    try {
      let serviceId = editServiceId
      if (editServiceId) {
        await fetchAPI(`/providers/services/${editServiceId}`, { method: 'PUT', body: JSON.stringify(serviceForm) })
      } else {
        const created = await fetchAPI<AIService>('/providers/services', { method: 'POST', body: JSON.stringify(serviceForm) })
        serviceId = created.id
      }
      setServiceDialogOpen(false)
      await load()
      if (!editServiceId && serviceId) {
        try {
          const res = await fetchAPI<{ models: string[] }>(
            `/providers/services/${serviceId}/discover-models`,
            { method: 'POST' },
          )
          const found = res.models.filter(Boolean)
          if (found.length > 0) {
            setBatchServiceId(serviceId)
            setBatchCandidates(found)
            setBatchChecked(new Set())
            setBatchDefault('')
            setBatchOpen(true)
          } else {
            toast('服务商已保存，未自动发现模型，可手动添加', 'info')
          }
        } catch (e) {
          toast(
            e instanceof Error
              ? `服务商已保存，自动嗅探失败：${e.message}，可手动添加模型`
              : '服务商已保存，该服务商暂不支持自动嗅探，可手动添加模型',
            'info',
          )
        }
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    }
  }

  // 手动对某服务商嗅探并打开批量选择框(排除已添加的模型)
  const discoverForService = async (serviceId: number) => {
    setDiscoveringService(serviceId)
    try {
      const res = await fetchAPI<{ models: string[] }>(
        `/providers/services/${serviceId}/discover-models`,
        { method: 'POST' },
      )
      const svc = services.find(s => s.id === serviceId)
      const added = new Set((svc?.models || []).map(m => m.model))
      const found = res.models.filter(Boolean).filter(id => !added.has(id))
      if (found.length === 0) {
        toast('未发现可新增的模型', 'info')
        return
      }
      setBatchServiceId(serviceId)
      setBatchCandidates(found)
      setBatchChecked(new Set())
      setBatchDefault('')
      setBatchOpen(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : '该服务商暂不支持自动嗅探', 'error')
    } finally {
      setDiscoveringService(null)
    }
  }

  const submitBatchModels = async () => {
    if (!batchServiceId) return
    const models = Array.from(batchChecked).map(m => ({
      name: '',
      model: m,
      is_default: m === batchDefault,
    }))
    if (models.length === 0) { setBatchOpen(false); return }
    setSubmittingBatch(true)
    try {
      await fetchAPI(`/providers/services/${batchServiceId}/models/batch`, {
        method: 'POST',
        body: JSON.stringify({ models }),
      })
      setBatchOpen(false)
      toast(`已添加 ${models.length} 个模型`, 'success')
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '批量添加失败', 'error')
    } finally {
      setSubmittingBatch(false)
    }
  }

  const deleteService = async (id: number) => {
    if (!confirm('删除服务商将同时删除其下所有模型，确定？')) return
    try {
      await fetchAPI(`/providers/services/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  // Model CRUD
  const openModelDialog = (serviceId?: number, model?: AIModel) => {
    if (model) {
      setModelForm({ name: model.name, service_id: model.service_id, model: model.model })
      setEditModelId(model.id)
    } else {
      setModelForm({ ...emptyModelForm, service_id: serviceId ?? null })
      setEditModelId(null)
    }
    setModelDialogOpen(true)
  }

  const saveModel = async () => {
    try {
      if (editModelId) {
        await fetchAPI(`/providers/models/${editModelId}`, { method: 'PUT', body: JSON.stringify(modelForm) })
      } else {
        await fetchAPI('/providers/models', { method: 'POST', body: JSON.stringify(modelForm) })
      }
      setModelDialogOpen(false)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    }
  }

  const deleteModel = async (id: number) => {
    if (!confirm('确定删除此模型？')) return
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  const setDefaultModel = async (id: number) => {
    try {
      await fetchAPI(`/providers/models/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast('设置失败', 'error')
    }
  }

  const testModel = async (id: number) => {
    setTestingModel(id)
    try {
      await fetchAPI(`/providers/models/${id}/test`, { method: 'POST' })
      toast('模型测试成功', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '测试失败', 'error')
    } finally {
      setTestingModel(null)
    }
  }

  // Channel CRUD
  const openChannelDialog = (channel?: NotifyChannel) => {
    if (channel) {
      setChannelForm({
        name: channel.name,
        type: channel.type,
        config: channel.config ? { ...channel.config } : {},
      })
      setEditChannelId(channel.id)
    } else {
      setChannelForm(emptyChannelForm)
      setEditChannelId(null)
    }
    setChannelKeyVisible(false)
    setChannelDialogOpen(true)
  }

  const saveChannel = async () => {
    const payload = {
      name: channelForm.name,
      type: channelForm.type,
      config: channelForm.config,
    }
    try {
      let savedChannel: NotifyChannel
      if (editChannelId) {
        savedChannel = await fetchAPI<NotifyChannel>(`/channels/${editChannelId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        savedChannel = await fetchAPI<NotifyChannel>('/channels', { method: 'POST', body: JSON.stringify(payload) })
      }
      setTesting(savedChannel.id)
      try {
        const result = await fetchAPI<{ message?: string }>(`/channels/${savedChannel.id}/test`, { method: 'POST' })
        toast(result?.message || '渠道已保存并通过测试', 'success')
      } catch (e) {
        setEditChannelId(savedChannel.id)
        load()
        toast(`渠道已保存，但测试失败：${e instanceof Error ? e.message : '未知错误'}`, 'error')
        return
      } finally {
        setTesting(null)
      }
      setChannelDialogOpen(false)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    }
  }

  const isChannelFormValid = () => {
    if (!channelForm.name) return false
    const typeDef = CHANNEL_TYPE_FIELDS[channelForm.type]
    if (!typeDef) return false
    return typeDef.fields
      .filter(f => f.required)
      .every(f => !!channelForm.config[f.key]?.trim())
  }

  const deleteChannel = async (id: number) => {
    if (!confirm('确定删除此通知渠道？')) return
    try {
      await fetchAPI(`/channels/${id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  const setDefaultChannel = async (id: number) => {
    try {
      await fetchAPI(`/channels/${id}`, { method: 'PUT', body: JSON.stringify({ is_default: true }) })
      load()
    } catch {
      toast('设置失败', 'error')
    }
  }

  const toggleChannelEnabled = async (channel: NotifyChannel) => {
    try {
      await fetchAPI(`/channels/${channel.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !channel.enabled }) })
      load()
    } catch {
      toast('操作失败', 'error')
    }
  }

  const testChannel = async (id: number) => {
    setTesting(id)
    try {
      const result = await fetchAPI<{ message?: string }>(`/channels/${id}/test`, { method: 'POST' })
      toast(result?.message || '测试通知已发送', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '测试失败', 'error')
    } finally {
      setTesting(null)
    }
  }

  // ── 扫码绑定个人微信(iLink 渠道) ──
  const loadWechatBind = async () => {
    try {
      const info = await wechatBindGet()
      setWechatBindInfo(info)
    } catch { /* 后端未实现/未绑定时不阻塞设置页 */ }
  }

  const stopWechatPoll = () => {
    if (wechatPollRef.current !== null) {
      window.clearInterval(wechatPollRef.current)
      wechatPollRef.current = null
    }
  }

  const startWechatPoll = (qrcode: string) => {
    stopWechatPoll()
    wechatPollRef.current = window.setInterval(async () => {
      try {
        const st = await wechatBindStatus(qrcode)
        setWechatQrStatus(st.status)
        if (st.status === 'success') {
          stopWechatPoll()
          setWechatQrOpen(false)
          toast('微信绑定成功', 'success')
          void loadWechatBind()
          load()
        } else if (st.status === 'expired') {
          stopWechatPoll()
        }
      } catch { /* 网络抖动忽略，下轮重试 */ }
    }, 3000)
  }

  const startWechatBind = async () => {
    setWechatBindStarting(true)
    try {
      const res = await wechatBindStart()
      setWechatQr(res)
      setWechatQrStatus('waiting')
      setWechatQrOpen(true)
      startWechatPoll(res.qrcode)
    } catch (e) {
      toast(e instanceof Error ? e.message : '发起绑定失败，请稍后重试', 'error')
    } finally {
      setWechatBindStarting(false)
    }
  }

  const closeWechatQr = () => {
    stopWechatPoll()
    setWechatQrOpen(false)
  }

  const copyWechatLink = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url)
      toast('链接已复制，请在微信中打开', 'success')
    } catch {
      toast('复制失败，请手动复制链接', 'error')
    }
  }

  const unbindWechat = async () => {
    if (!confirm('确定解除个人微信绑定？解除后将无法通过微信接收通知。')) return
    setWechatUnbinding(true)
    try {
      await wechatBindUnbind()
      toast('已解除微信绑定', 'success')
      setWechatBindInfo(null)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '解除绑定失败', 'error')
    } finally {
      setWechatUnbinding(false)
    }
  }

  const toggleBrowserPush = async (enabled: boolean) => {
    if (!enabled) {
      setBrowserNotificationsEnabled(false)
      setBrowserPushEnabled(false)
      toast('电脑 Web 推送已关闭', 'info')
      return
    }
    if (!browserNotificationsSupported()) {
      toast('当前浏览器或访问地址不支持系统通知，请使用 HTTPS 或 localhost', 'error')
      return
    }
    const permission = await requestBrowserNotificationPermission()
    if (permission !== 'granted') {
      setBrowserNotificationsEnabled(false)
      setBrowserPushEnabled(false)
      toast('浏览器未授予通知权限，请在网站权限中允许通知', 'error')
      return
    }
    try {
      const latest = await fetchAPI<{ items: Array<{ id: number }> }>('/notifications?limit=1')
      const baselineId = latest?.items?.[0]?.id || 0
      setBrowserNotificationsEnabled(true, baselineId)
      setBrowserPushEnabled(true)
      await showBrowserNotification({
        id: Date.now(),
        title: 'SIDA 电脑推送已开启',
        body: '页面打开或在后台运行时，新消息会直接显示为电脑系统通知。',
        link: '/settings',
      })
      toast('电脑 Web 推送已开启', 'success')
    } catch (e) {
      setBrowserNotificationsEnabled(false)
      setBrowserPushEnabled(false)
      toast(e instanceof Error ? e.message : '电脑 Web 推送开启失败', 'error')
    }
  }

  const testBrowserPush = async () => {
    setBrowserPushTesting(true)
    try {
      const shown = await showBrowserNotification({
        id: Date.now(),
        title: 'SIDA 电脑推送测试',
        body: '如果你看到这条系统通知，说明 Web 推送已正常工作。',
        link: '/settings',
      })
      toast(shown ? '电脑测试通知已发送' : '浏览器通知权限不可用', shown ? 'success' : 'error')
    } finally {
      setBrowserPushTesting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="w-5 h-5 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  const allModels = services.flatMap(s => s.models || [])
  const defaultModel = allModels.find(m => m.is_default)
  const defaultChannel = channels.find(c => c.is_default)
  const enabledChannels = channels.filter(c => c.enabled)
  // 场景分配下拉选项: 模型池全部模型(模型名 + 服务商名)
  const sceneModelOptions = services.flatMap(svc =>
    (svc.models || []).map(m => ({ id: m.id, label: `${m.name} · ${svc.name}` })),
  )

  const filteredSettings = settings.filter(s => {
    // 敏感接口 key 由独立"接口 Key"区块管理,系统区块不重复展示
    if (SECRET_SETTING_KEYS.has(s.key)) return false
    const q = systemQuery.trim().toLowerCase()
    if (!q) return true
    return (s.description || '').toLowerCase().includes(q) || (s.key || '').toLowerCase().includes(q)
  })

  // 按“重要性”排序：常用优先，低频靠后
  const jumpItems: Array<{ id: string; label: string; hint?: string }> = [
    { id: 'sec-ai', label: 'AI', hint: `${services.length} 服务 / ${allModels.length} 模型` },
    { id: 'sec-notify', label: '通知', hint: `${enabledChannels.length}/${channels.length} 启用` },
    { id: 'sec-keys', label: '接口Key', hint: `${settings.filter(s => SECRET_SETTING_KEYS.has(s.key) && s.value === SECRET_MASK).length}/${SECRET_SETTING_KEYS.size} 已配` },
    { id: 'sec-system', label: '系统', hint: health?.timezone ? `TZ ${health.timezone}` : undefined },
    { id: 'sec-pack', label: '配置包' },
    { id: 'sec-feedback', label: '反馈' },
  ]

  const scrollTo = (id: string) => {
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div>
      {/* Hero */}
      <div className="card relative overflow-hidden p-5 md:p-7">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/10 via-transparent to-accent/30" />
        <div className="relative flex flex-col md:flex-row md:items-end md:justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <input ref={avatarFileRef} type="file" accept="image/*" className="hidden" onChange={onPickAvatar} />
              <button
                type="button"
                onClick={() => avatarFileRef.current?.click()}
                disabled={avatarSaving}
                title="点击上传头像"
                className="group relative h-9 w-9 rounded-full overflow-hidden bg-gradient-to-br from-primary to-primary/70 text-white shadow-sm flex items-center justify-center ring-1 ring-border/40 hover:ring-primary/40 transition-shadow shrink-0"
              >
                {avatar ? (
                  <img src={avatar} alt="头像" className="w-full h-full object-cover" />
                ) : (
                  <User className="w-4 h-4" />
                )}
                <span className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity">
                  <Upload className="w-3.5 h-3.5 text-white" />
                </span>
              </button>
              <span className="mx-1 hidden h-4 w-px bg-border/50 sm:block" />
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{services.length}</span> 服务商
              </div>
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{allModels.length}</span> 模型
              </div>
              <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                <span className="font-mono text-foreground/90">{enabledChannels.length}</span>/<span className="font-mono">{channels.length}</span> 渠道启用
              </div>
              {defaultModel ? (
                <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                  默认模型 <span className="font-mono text-foreground/90">{defaultModel.model}</span>
                </div>
              ) : null}
              {defaultChannel ? (
                <div className="px-2.5 py-1 rounded-full bg-background/70 border border-border/50 text-[11px] text-muted-foreground">
                  默认通知 <span className="text-foreground/90">{defaultChannel.name}</span>
                </div>
              ) : null}
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-2">
            <Button variant="secondary" size="sm" className="h-9" onClick={exportTemplate} disabled={exporting}>
              <Download className="w-3.5 h-3.5" /> 导出配置包
            </Button>
            <Button size="sm" className="h-9" onClick={() => scrollTo('sec-ai')}>
              <Cpu className="w-3.5 h-3.5" /> 配置 AI
            </Button>
          </div>
        </div>

        {/* Jump pills */}
        <div className="relative mt-4 flex flex-wrap gap-2">
          {jumpItems.map(it => (
            <button
              key={it.id}
              onClick={() => scrollTo(it.id)}
              className="group flex items-center gap-2 rounded-full border border-border/50 bg-background/70 px-3 py-1.5 text-[11px] text-muted-foreground hover:text-foreground hover:border-primary/30 transition-colors"
            >
              <span className="font-medium text-foreground/90 group-hover:text-foreground">{it.label}</span>
              {it.hint ? <span className="opacity-60">{it.hint}</span> : null}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* AI Services + Models Section */}
        <section id="sec-ai" className="card p-4 md:p-6 lg:col-span-7">
          <div className="flex items-start justify-between mb-4 md:mb-5 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">AI 服务商 & 模型</h3>
              <p className="text-[11px] text-muted-foreground mt-1">连接你的 AI 服务并设置默认模型</p>
            </div>
            <Button size="sm" className="h-8" onClick={() => openServiceDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">添加服务商</span>
            </Button>
          </div>
          {services.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">暂无 AI 服务商，点击"添加服务商"创建</p>
          ) : (
            <div className="space-y-4">
              {services.map(svc => (
                <div key={svc.id} className="rounded-xl bg-accent/30 overflow-hidden">
                  {/* Service header */}
                  <div className="flex items-center justify-between p-3.5">
                    <div className="min-w-0">
                      <span className="text-[13px] font-medium text-foreground">{svc.name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5 truncate font-mono">{svc.base_url}</p>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <Button size="sm" variant="ghost" className="h-7 text-[11px]" onClick={() => openModelDialog(svc.id)}>
                        <Plus className="w-3 h-3" /> 模型
                      </Button>
                      <Button
                        variant="ghost" size="icon" className="h-7 w-7"
                        title="嗅探模型（自动发现可用模型）"
                        disabled={discoveringService === svc.id}
                        onClick={() => discoverForService(svc.id)}
                      >
                        <Radar className={`w-3.5 h-3.5 ${discoveringService === svc.id ? 'animate-pulse' : ''}`} />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openServiceDialog(svc)}>
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => deleteService(svc.id)}>
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                  {/* Models under this service */}
                  {svc.models.length > 0 && (
                    <div className="px-3.5 pb-3.5 space-y-1.5">
                      {svc.models.map(m => (
                        <div key={m.id} className="flex items-center justify-between px-3 py-2 rounded-lg bg-background/60">
                          <div className="flex items-center gap-2">
                            {m.is_default && <Star className="w-3 h-3 text-amber-500" />}
                            <Cpu className="w-3 h-3 text-muted-foreground" />
                            <span className="text-[12px] font-medium text-foreground">{m.name}</span>
                            <span className="text-[11px] text-muted-foreground font-mono">{m.model}</span>
                          </div>
                          <div className="flex items-center gap-0.5">
                            <Button
                              variant="ghost" size="icon" className="h-6 w-6"
                              onClick={() => testModel(m.id)}
                              disabled={testingModel === m.id}
                              title="测试模型"
                            >
                              {testingModel === m.id ? (
                                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                              ) : (
                                <Play className="w-3 h-3" />
                              )}
                            </Button>
                            {!m.is_default && (
                              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setDefaultModel(m.id)} title="设为默认">
                                <Star className="w-3 h-3" />
                              </Button>
                            )}
                            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => openModelDialog(svc.id, m)}>
                              <Pencil className="w-3 h-3" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-6 w-6 hover:text-destructive" onClick={() => deleteModel(m.id)}>
                              <Trash2 className="w-3 h-3" />
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* 场景分配(统一 LLM 配置中心): 每个使用点绑定模型池里的模型 */}
          <div className="mt-5 pt-4 border-t">
            <div className="mb-3">
              <h4 className="text-[12px] font-semibold text-foreground">场景分配</h4>
              <p className="text-[11px] text-muted-foreground mt-0.5">各 AI 使用点绑定的模型，未绑定则使用默认模型</p>
            </div>
            {sceneBindingsLoading ? (
              <p className="text-[11px] text-muted-foreground text-center py-3">加载中...</p>
            ) : sceneBindings.length === 0 ? (
              <p className="text-[11px] text-muted-foreground text-center py-3">暂无场景数据</p>
            ) : (
              <div className="space-y-2">
                {sceneBindings.map(b => (
                  <div key={b.scene} className="flex items-center justify-between gap-3 rounded-lg bg-accent/30 px-3 py-2">
                    <div className="min-w-0">
                      <span className="text-[12px] font-semibold text-foreground">{b.display_name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5 truncate">{b.description}</p>
                    </div>
                    <div className="flex-shrink-0 w-[220px] sm:w-[260px]">
                      <Select
                        value={b.model_id != null ? String(b.model_id) : SCENE_DEFAULT_VALUE}
                        onValueChange={v => handleSceneChange(b.scene, v)}
                        disabled={bindingSaving === b.scene}
                      >
                        <SelectTrigger className="h-8 text-[12px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={SCENE_DEFAULT_VALUE}>默认模型</SelectItem>
                          {sceneModelOptions.map(opt => (
                            <SelectItem key={opt.id} value={String(opt.id)}>{opt.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* Notify Channel Section */}
        <section id="sec-notify" className="card p-4 md:p-6 lg:col-span-5">
          <div className="flex items-start justify-between mb-4 md:mb-5 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">通知渠道</h3>
              <p className="text-[11px] text-muted-foreground mt-1">推送到 Telegram/Bark 等渠道</p>
            </div>
            <Button size="sm" className="h-8" onClick={() => openChannelDialog()}>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">添加</span>
            </Button>
          </div>
          <div className="mb-3 rounded-xl border border-border/50 bg-accent/20 p-3.5">
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                <MonitorUp className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                <div>
                  <div className="text-[12px] font-medium text-foreground">电脑 Web 推送</div>
                  <p className="mt-0.5 text-[10.5px] text-muted-foreground">页面打开或在后台运行时，新消息直接弹出电脑系统通知。需要 HTTPS 或 localhost。</p>
                </div>
              </div>
              <Switch
                checked={browserPushEnabled}
                disabled={!browserNotificationsSupported()}
                onCheckedChange={value => void toggleBrowserPush(value)}
              />
            </div>
            {browserPushEnabled && (
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 h-7 px-2 text-[11px]"
                disabled={browserPushTesting}
                onClick={() => void testBrowserPush()}
              >
                <Send className="h-3.5 w-3.5" />
                {browserPushTesting ? '测试中…' : '测试电脑通知'}
              </Button>
            )}
          </div>
          {/* 个人微信(iLink): 扫码绑定 */}
          <div className="mb-3 rounded-xl border border-border/50 bg-accent/20 p-3.5">
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-start gap-2.5">
                <QrCode className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                <div className="min-w-0">
                  <div className="text-[12px] font-medium text-foreground">个人微信(iLink)</div>
                  {wechatBindInfo?.account_id ? (
                    <p className="mt-0.5 text-[10.5px] text-muted-foreground">
                      已绑定：
                      <span className="font-mono text-foreground">{wechatBindInfo.user_id || wechatBindInfo.account_id}</span>
                      {wechatBindInfo.nickname ? `（${wechatBindInfo.nickname}）` : ''}
                    </p>
                  ) : (
                    <p className="mt-0.5 text-[10.5px] text-muted-foreground">扫码绑定个人微信，绑定成功后自动创建通知渠道</p>
                  )}
                </div>
              </div>
              {wechatBindInfo?.account_id ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 shrink-0 px-2 text-[11px] hover:text-destructive"
                  onClick={() => void unbindWechat()}
                  disabled={wechatUnbinding}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  {wechatUnbinding ? '解除中…' : '解除绑定'}
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="h-7 shrink-0 px-2 text-[11px]"
                  onClick={() => void startWechatBind()}
                  disabled={wechatBindStarting}
                >
                  <QrCode className="h-3.5 w-3.5" />
                  {wechatBindStarting ? '发起中…' : '绑定个人微信'}
                </Button>
              )}
            </div>
          </div>
          {channels.length === 0 ? (
            <p className="text-[13px] text-muted-foreground text-center py-6">暂无通知渠道，点击"添加"创建</p>
          ) : (
            <div className="space-y-3">
              {channels.map(ch => (
                <div key={ch.id} className="flex items-center justify-between p-3.5 rounded-xl bg-accent/30 hover:bg-accent/50 transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    {ch.is_default && <Star className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" />}
                    <div className="min-w-0">
                      <span className="text-[13px] font-medium text-foreground">{ch.name}</span>
                      <p className="text-[11px] text-muted-foreground mt-0.5">{CHANNEL_TYPE_FIELDS[ch.type]?.label || ch.type}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button
                      variant="ghost" size="sm" className="h-7 px-2 text-[11px]"
                      onClick={() => testChannel(ch.id)}
                      disabled={testing === ch.id || !ch.enabled}
                      title="发送测试"
                    >
                      {testing === ch.id ? (
                        <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <><Send className="w-3.5 h-3.5" />测试</>
                      )}
                    </Button>
                    {!ch.is_default && (
                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setDefaultChannel(ch.id)} title="设为默认">
                        <Star className="w-3.5 h-3.5" />
                      </Button>
                    )}
                    <Switch checked={ch.enabled} onCheckedChange={() => toggleChannelEnabled(ch)} />
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openChannelDialog(ch)}>
                      <Pencil className="w-3.5 h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => deleteChannel(ch.id)}>
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* 多用户: 定时报告订阅 + 用户管理(2026-08-10 阶段5) */}
        <section id="sec-subscriptions" className="card p-4 md:p-6 lg:col-span-12">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <MailCheck className="h-4 w-4 text-primary" />
              定时报告订阅
            </h2>
            <span className="text-[11px] text-muted-foreground">选择你要接收的定时推送</span>
          </div>
          {subLoading ? (
            <div className="py-3 text-[12px] text-muted-foreground">加载中…</div>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {subscriptions.map(s => (
                <div key={s.report_type} className="flex items-center justify-between rounded-lg border border-border/40 bg-accent/20 px-3 py-2.5">
                  <div>
                    <div className="text-[12px] font-medium">{s.label}</div>
                    <div className="text-[10px] text-muted-foreground">{s.report_type}</div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={s.enabled}
                    onClick={() => toggleSubscription(s.report_type)}
                    className={`relative h-5 w-9 rounded-full transition-colors ${s.enabled ? 'bg-primary' : 'bg-muted'}`}
                  >
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${s.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>

        {currentUser?.role === 'owner' && (
          <section id="sec-users" className="card p-4 md:p-6 lg:col-span-12">
            <UserManagement currentUser={currentUser} />
          </section>
        )}

        {/* General Settings */}
        {settings.length > 0 && (
          <>
          {/* 接口 Key 区块(数据源凭证维护) */}
          <section id="sec-keys" className="card p-4 md:p-6 lg:col-span-12">
            <div className="flex items-start justify-between mb-4 gap-3">
              <div>
                <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">接口 Key</h3>
                <p className="text-[11px] text-muted-foreground mt-1">数据源接口凭证，保存在本机数据库，修改后立即生效（无需重启）。</p>
                <div className="flex flex-wrap gap-2 mt-2">
                  {keyDataSources.filter(s => (s.key_count ?? 0) > 0).map(s => (
                    <span key={s.id} className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary" title={`${s.name} · ${s.provider}`}>
                      {s.provider} · {s.key_count} 个 Key
                    </span>
                  ))}
                  {keyDataSources.every(s => (s.key_count ?? 0) === 0) && (
                    <span className="text-[10px] text-muted-foreground">当前接口 Key 为单 Key 配置</span>
                  )}
                </div>
              </div>
            </div>
            <div className="space-y-4">
              {/* 数据源 Token 组 */}
              <div className="text-[11px] font-medium text-muted-foreground mt-1">数据源 Token</div>
              {settings.filter(s => s.key === 'wudao_mcp_token' || s.key === 'zhitu_token' || s.key === 'tdx_api_key').map(setting => {
                const isChanged = setting.key in edited
                return (
                  <div key={setting.key} className="rounded-xl bg-accent/30 p-3.5">
                    <Label className="text-[12px]">{setting.description || setting.key}</Label>
                    <div className="flex items-center gap-2.5 mt-2">
                      <div className="flex-1 relative">
                        <Input
                          type="password"
                          value={isChanged ? (edited[setting.key] ?? '') : ''}
                          onChange={e => setEdited({ ...edited, [setting.key]: e.target.value })}
                          className={`font-mono ${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}
                          placeholder={setting.value === SECRET_MASK ? '已配置（输入新 Key 可替换，留空不变）' : '未配置，输入接口 Key'}
                        />
                        {!isChanged && setting.value === SECRET_MASK && (
                          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-emerald-500">已配置</span>
                        )}
                      </div>
                      <button
                        onClick={() => handleSave(setting.key)}
                        disabled={!isChanged || saving === setting.key}
                        className={`w-10 h-10 rounded-lg flex items-center justify-center transition-colors ${
                          saved === setting.key
                            ? 'bg-emerald-500/10 text-emerald-600'
                            : isChanged
                              ? 'bg-primary text-white'
                              : 'text-muted-foreground/30'
                        }`}
                      >
                        {saving === setting.key ? (
                          <span className="w-4 h-4 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                        ) : (
                          <Check className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-2">
                      {setting.key === 'wudao_mcp_token'
                        ? '悟道 MCP Token（竞价/题材数据）'
                        : setting.key === 'tdx_api_key'
                        ? '通达信问小达 MCP Token（自然语言投研/选股数据源）'
                        : '智兔数据接口 Token（分红/股东数据，200次/天）'}。读取优先级：设置页 &gt; 环境变量 &gt; 内置默认。
                    </p>
                  </div>
                )
              })}
            </div>
          </section>
          {/* 同花顺登录区块 */}
          <section id="sec-ths" className="card p-4 md:p-6 lg:col-span-12">
            <div className="flex items-start justify-between mb-4 gap-3">
              <div>
                <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">同花顺登录</h3>
                <p className="text-[11px] text-muted-foreground mt-1">扫码登录获取登录态,自动续期,用于解锁同花顺数据源。</p>
              </div>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="secondary" className="h-8 text-[12px]" onClick={thsRefresh} disabled={thsLoading}>
                  <RefreshCw className="w-3.5 h-3.5 mr-1" />
                  刷新
                </Button>
                <Button size="sm" className="h-8 text-[12px]" onClick={thsGetQrcode} disabled={thsLoading}>
                  <QrCode className="w-3.5 h-3.5 mr-1" />
                  {thsLoading ? '等待扫码...' : (thsSession?.logged_in ? '重新扫码' : '扫码登录')}
                </Button>
              </div>
            </div>
            <div className="rounded-xl bg-accent/30 p-3.5">
              {thsSession?.logged_in ? (
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[12px]">
                  <div><span className="text-muted-foreground">账号:</span> <span className="font-mono">{thsSession.account}</span></div>
                  <div><span className="text-muted-foreground">UserID:</span> <span className="font-mono">{thsSession.userid}</span></div>
                  <div><span className="text-muted-foreground">过期:</span> <span className="font-mono">{thsSession.expires?.replace('T', ' ').slice(0, 19)}</span></div>
                  <div><span className="text-emerald-500">✓ 已登录 · 自动续期</span></div>
                </div>
              ) : (
                <div className="text-[12px] text-muted-foreground">未登录。点击「扫码登录」后用手机同花顺 APP 扫描二维码。</div>
              )}
              {thsQr && (
                <div className="mt-3 flex items-center gap-4">
                  <img src={`data:image/png;base64,${thsQr}`} alt="同花顺扫码登录" className="w-40 h-40 rounded-lg border border-border/50" />
                  <div className="text-[11px] text-muted-foreground">
                    <p>用手机同花顺 APP 扫描二维码</p>
                    <p className="mt-1">有效期约 3 分钟,扫码后自动登录</p>
                  </div>
                </div>
              )}
            </div>
          </section>
          <section id="sec-system" className="card p-4 md:p-6 lg:col-span-12">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 mb-4 md:mb-5">
              <div>
                <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">系统</h3>
                <p className="text-[11px] text-muted-foreground mt-1">偏好与高级选项。修改后立即生效。</p>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  value={systemQuery}
                  onChange={e => setSystemQuery(e.target.value)}
                  placeholder="搜索设置项（描述 / key）"
                  className="h-9 w-full md:w-[320px]"
                />
                {health?.timezone ? (
                  <div className="hidden md:flex px-2.5 h-9 items-center rounded-lg border border-border/50 bg-accent/20 text-[11px] text-muted-foreground">
                    TZ <span className="ml-1 font-mono text-foreground/90">{health.timezone}</span>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="space-y-5">
              {filteredSettings.map(setting => {
                const currentValue = edited[setting.key] ?? setting.value
                const isChanged = setting.key in edited
                const STOCK_LINK_OPTIONS: Record<string, string> = { xueqiu: '雪球' }
                return (
                  <div key={setting.key}>
                    <Label>{setting.description || setting.key}</Label>
                    <div className="flex items-center gap-2.5">
                      {setting.key === 'stock_link_platform' ? (
                        <Select
                          value={currentValue || 'xueqiu'}
                          onValueChange={v => setEdited({ ...edited, [setting.key]: v })}
                        >
                          <SelectTrigger className={`${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(STOCK_LINK_OPTIONS).map(([val, label]) => (
                              <SelectItem key={val} value={val}>{label}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                      <div className="flex-1 relative">
                        <Input
                          type={SECRET_SETTING_KEYS.has(setting.key) ? 'password' : 'text'}
                          value={SECRET_SETTING_KEYS.has(setting.key) && !isChanged ? (setting.value === SECRET_MASK ? '' : currentValue) : currentValue}
                          onChange={e => setEdited({ ...edited, [setting.key]: e.target.value })}
                          className={`font-mono ${isChanged ? 'ring-2 ring-primary/20 border-primary/30' : ''}`}
                          placeholder={SECRET_SETTING_KEYS.has(setting.key)
                            ? (setting.value === SECRET_MASK ? '已配置(留空保存则不变)' : setting.key)
                            : setting.key}
                        />
                        {SECRET_SETTING_KEYS.has(setting.key) && setting.value === SECRET_MASK && !isChanged && (
                          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-emerald-500">已配置</span>
                        )}
                      </div>
                      )}
                      <button
                        onClick={() => handleSave(setting.key)}
                        disabled={!isChanged || saving === setting.key}
                        className={`w-10 h-10 rounded-lg flex items-center justify-center transition-colors ${
                          saved === setting.key
                            ? 'bg-emerald-500/10 text-emerald-600'
                            : isChanged
                              ? 'bg-primary text-white'
                              : 'text-muted-foreground/30'
                        }`}
                      >
                        {saving === setting.key ? (
                          <span className="w-4 h-4 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                        ) : (
                          <Check className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
          </>
        )}

        {/* Config Pack (Templates) */}
        <section id="sec-pack" className="card p-4 md:p-6 lg:col-span-7">
          <div className="flex items-start justify-between mb-4 gap-3">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">配置包</h3>
              <p className="text-[11px] text-muted-foreground mt-1">一键导入/导出 Agent、关注列表与系统设置</p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" className="h-8" onClick={exportTemplate} disabled={exporting}>
                <Download className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">导出</span>
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8"
                onClick={() => importFileRef.current?.click()}
                disabled={importing}
              >
                <Upload className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">导入</span>
              </Button>
            </div>
          </div>

          <div className="flex items-center gap-2 mb-4">
            <div className="text-[11px] text-muted-foreground">导入模式</div>
            <Select value={importMode} onValueChange={(v) => setImportMode(v as any)}>
              <SelectTrigger className="h-8 w-[160px] text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="merge">合并更新（推荐）</SelectItem>
                <SelectItem value="replace">替换（仅覆盖配置包包含项）</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <input
            ref={importFileRef}
            type="file"
            accept="application/json"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (!file) return
              try {
                const text = await file.text()
                const payload = JSON.parse(text)
                await importTemplate(payload)
              } catch (err) {
                toast('配置包解析失败', 'error')
              }
            }}
          />

          <div className="rounded-xl border border-border/40 bg-accent/20 p-3">
            <div className="flex items-center gap-2 text-[12px] font-semibold text-foreground">
              <FileJson className="w-4 h-4 text-muted-foreground" />
              官方模板
            </div>
            <div className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-2">
              {builtinTemplates.map(t => (
                <div key={t.name} className="rounded-lg border border-border/40 bg-background/30 p-3">
                  <div className="flex items-center justify-between">
                    <div className="text-[12px] font-semibold text-foreground">{t.name}</div>
                    <Button
                      size="sm"
                      className="h-7"
                      onClick={() => importTemplate(t.payload)}
                      disabled={importing}
                    >
                      <span className="text-[12px]">应用</span>
                    </Button>
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">{t.desc}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Feedback Stats */}
        <section id="sec-feedback" className="card p-4 md:p-6 lg:col-span-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground">建议反馈</h3>
              <p className="text-[11px] text-muted-foreground mt-1">用于评估推送质量与策略迭代</p>
            </div>
            <Button variant="secondary" size="sm" className="h-8" onClick={loadFeedbackStats} disabled={fbLoading}>
              <BarChart3 className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">刷新</span>
            </Button>
          </div>

          {fbStats ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
                <span>近 {fbStats.range_days} 天</span>
                <span className="opacity-50">|</span>
                <span>反馈: <span className="font-mono text-foreground/90">{fbStats.total}</span></span>
                <span className="opacity-50">|</span>
                <span>有用: <span className="font-mono text-emerald-600">{fbStats.useful}</span></span>
                <span className="opacity-50">|</span>
                <span>没用: <span className="font-mono text-rose-600">{fbStats.useless}</span></span>
                <span className="opacity-50">|</span>
                <span>有用率: <span className="font-mono text-foreground/90">{Math.round(fbStats.useful_rate * 100)}%</span></span>
              </div>

              {fbStats.by_agent?.length ? (
                <div className="rounded-xl border border-border/40 bg-accent/20 p-3">
                  <div className="text-[12px] font-semibold text-foreground">按 Agent</div>
                  <div className="mt-2 space-y-1">
                    {fbStats.by_agent.slice(0, 6).map(a => (
                      <div key={a.agent_name} className="flex items-center justify-between text-[11px]">
                        <span className="font-mono text-muted-foreground">{a.agent_name}</span>
                        <span className="font-mono text-muted-foreground">
                          {a.useful}/{a.total} ({Math.round(a.useful_rate * 100)}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-[12px] text-muted-foreground">暂无反馈数据</div>
              )}
            </div>
          ) : (
            <div className="text-[12px] text-muted-foreground">暂无反馈数据</div>
          )}
        </section>

      </div>

      {/* Service Dialog */}
      <Dialog open={serviceDialogOpen} onOpenChange={setServiceDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editServiceId ? '编辑 AI 服务商' : '添加 AI 服务商'}</DialogTitle>
            <DialogDescription>配置 AI 服务商的 API 连接信息</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>名称</Label>
              <Input
                value={serviceForm.name}
                onChange={e => setServiceForm({ ...serviceForm, name: e.target.value })}
                placeholder="如 OpenAI、智谱、DeepSeek"
              />
            </div>
            <div>
              <Label>Base URL</Label>
              <Input
                value={serviceForm.base_url}
                onChange={e => setServiceForm({ ...serviceForm, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1"
                className="font-mono"
              />
            </div>
            <div>
              <Label>API Key</Label>
              <div className="relative">
                <Input
                  type={serviceKeyVisible ? 'text' : 'password'}
                  value={serviceForm.api_key}
                  onChange={e => setServiceForm({ ...serviceForm, api_key: e.target.value })}
                  placeholder="sk-..."
                  className="font-mono pr-10"
                />
                <Button
                  type="button" variant="ghost" size="icon"
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                  onClick={() => setServiceKeyVisible(!serviceKeyVisible)}
                >
                  {serviceKeyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </Button>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setServiceDialogOpen(false)}>取消</Button>
              <Button onClick={saveService} disabled={!serviceForm.name || !serviceForm.base_url}>
                {editServiceId ? '保存' : '创建'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Model Dialog */}
      <Dialog open={modelDialogOpen} onOpenChange={setModelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editModelId ? '编辑模型' : '添加模型'}</DialogTitle>
            <DialogDescription>配置 AI 模型</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>所属服务商</Label>
              <Select
                value={modelForm.service_id?.toString() ?? ''}
                onValueChange={val => setModelForm({ ...modelForm, service_id: val ? parseInt(val) : null })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择服务商" />
                </SelectTrigger>
                <SelectContent>
                  {services.map(s => (
                    <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>显示名称 <span className="text-muted-foreground font-normal">(选填，默认同模型标识)</span></Label>
              <Input
                value={modelForm.name}
                onChange={e => setModelForm({ ...modelForm, name: e.target.value })}
                placeholder="不填则使用模型标识"
              />
            </div>
            <div>
              <Label>模型标识 <span className="text-muted-foreground font-normal">(可用服务商上的「嗅探」批量发现)</span></Label>
              <Input
                value={modelForm.model}
                disabled={!modelForm.service_id}
                onChange={e => setModelForm({ ...modelForm, model: e.target.value })}
                placeholder={modelForm.service_id ? 'gpt-4o / glm-4-flash' : '请先选择服务商'}
                className="font-mono"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setModelDialogOpen(false)}>取消</Button>
              <Button onClick={saveModel} disabled={!modelForm.model || !modelForm.service_id}>
                {editModelId ? '保存' : '创建'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 批量选择嗅探到的模型 */}
      <Dialog open={batchOpen} onOpenChange={setBatchOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>发现 {batchCandidates.length} 个模型</DialogTitle>
            <DialogDescription>勾选要添加的模型，并可指定一个默认模型</DialogDescription>
          </DialogHeader>
          <div className="mt-3 flex items-center justify-between px-0.5 text-xs text-muted-foreground">
            <span>已选 <span className="font-mono text-foreground">{batchChecked.size}</span> / {batchCandidates.length}</span>
            <button
              type="button"
              className="hover:text-foreground"
              onClick={() => setBatchChecked(
                batchChecked.size === batchCandidates.length ? new Set() : new Set(batchCandidates),
              )}
            >
              {batchChecked.size === batchCandidates.length ? '取消全选' : '全选'}
            </button>
          </div>
          <div className="mt-1.5 max-h-80 space-y-1.5 overflow-y-auto scrollbar pr-1">
            {batchCandidates.map(id => {
              const checked = batchChecked.has(id)
              const isDefault = batchDefault === id
              return (
                <div
                  key={id}
                  onClick={() => {
                    const next = new Set(batchChecked)
                    if (checked) { next.delete(id); if (isDefault) setBatchDefault('') }
                    else next.add(id)
                    setBatchChecked(next)
                  }}
                  className={`flex cursor-pointer items-center justify-between gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                    checked ? 'border-primary/60 bg-primary/10' : 'border-border/50 hover:border-border hover:bg-muted/40'
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                      checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/40'
                    }`}>
                      {checked && <Check className="h-3 w-3" strokeWidth={3} />}
                    </span>
                    <span className="truncate font-mono text-sm">{id}</span>
                  </div>
                  <button
                    type="button"
                    onClick={e => {
                      e.stopPropagation()
                      if (isDefault) { setBatchDefault('') }
                      else {
                        setBatchDefault(id)
                        if (!checked) { const next = new Set(batchChecked); next.add(id); setBatchChecked(next) }
                      }
                    }}
                    className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition-colors ${
                      isDefault ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    <Star className={`h-3 w-3 ${isDefault ? 'fill-current' : ''}`} />
                    {isDefault ? '默认' : '设默认'}
                  </button>
                </div>
              )
            })}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setBatchOpen(false)}>跳过</Button>
            <Button onClick={submitBatchModels} disabled={batchChecked.size === 0 || submittingBatch}>
              {submittingBatch ? '添加中…' : `添加 ${batchChecked.size} 个`}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 微信扫码绑定弹窗 */}
      <Dialog open={wechatQrOpen} onOpenChange={open => { if (!open) closeWechatQr() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>绑定个人微信</DialogTitle>
            <DialogDescription>使用微信「扫一扫」扫描二维码，在手机上确认绑定</DialogDescription>
          </DialogHeader>
          <div className="mt-2 flex flex-col items-center gap-3">
            {wechatQr && (
              <>
                <div className="rounded-xl border border-border/50 bg-white p-3">
                  <QRCodeSVG value={wechatQr.qrcode_url} size={200} />
                </div>
                <p className="text-[11px] text-muted-foreground text-center">
                  请用微信「扫一扫」扫描二维码(约 3 分钟内有效)
                </p>
                <button
                  type="button"
                  onClick={() => void copyWechatLink(wechatQr.qrcode_url)}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border/50 bg-accent/30 px-2.5 py-1.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                  title="点击复制链接"
                >
                  <Copy className="h-3 w-3 flex-shrink-0" />
                  <span className="truncate font-mono">{wechatQr.qrcode_url}</span>
                </button>
                {wechatQrStatus === 'waiting' && (
                  <p className="flex items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="h-3 w-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                    等待扫码确认…
                  </p>
                )}
                {wechatQrStatus === 'expired' && (
                  <p className="text-[11px] text-destructive">二维码已过期，请关闭后重新发起绑定</p>
                )}
              </>
            )}
            <div className="flex w-full justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={closeWechatQr}>关闭</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Channel Dialog */}
      <Dialog open={channelDialogOpen} onOpenChange={setChannelDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editChannelId ? '编辑通知渠道' : '添加通知渠道'}</DialogTitle>
            <DialogDescription>配置通知推送方式</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            {channelForm.type !== 'openclaw' && (
              <div>
                <Label>名称</Label>
                <Input
                  value={channelForm.name}
                  onChange={e => setChannelForm({ ...channelForm, name: e.target.value })}
                  placeholder="如 我的 Telegram"
                />
              </div>
            )}
            <div>
              <Label>类型</Label>
              <Select
                value={channelForm.type}
                onValueChange={val => setChannelForm({ ...channelForm, type: val, config: {} })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(CHANNEL_TYPE_FIELDS).map(([key, def]) => (
                    <SelectItem key={key} value={key}>{def.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {channelForm.type === 'openclaw' ? (
              <div className="rounded-xl border border-border/50 bg-accent/20 p-4">
                <div className="flex items-start gap-2.5">
                  <QrCode className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                  <div>
                    <div className="text-[12px] font-medium text-foreground">扫码绑定个人微信</div>
                    <p className="mt-0.5 text-[10.5px] text-muted-foreground">
                      无需填写地址与密钥。点击下方按钮，用微信扫码确认后自动创建「个人微信」渠道。
                    </p>
                  </div>
                </div>
                <Button className="mt-3" size="sm" onClick={() => void startWechatBind()} disabled={wechatBindStarting}>
                  <QrCode className="h-3.5 w-3.5" />
                  {wechatBindStarting ? '发起中…' : '绑定个人微信'}
                </Button>
              </div>
            ) : (
              CHANNEL_TYPE_FIELDS[channelForm.type]?.fields.map(field => (
                <div key={field.key}>
                  <Label>{field.label}{!field.required && <span className="text-muted-foreground font-normal"> (选填)</span>}</Label>
                  <div className="relative">
                    <Input
                      type={field.secret && !channelKeyVisible ? 'password' : 'text'}
                      value={channelForm.config[field.key] || ''}
                      onChange={e => setChannelForm({
                        ...channelForm,
                        config: { ...channelForm.config, [field.key]: e.target.value },
                      })}
                      placeholder={field.placeholder}
                      className={`font-mono ${field.secret ? 'pr-10' : ''}`}
                    />
                    {field.secret && (
                      <Button
                        type="button" variant="ghost" size="icon"
                        className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                        onClick={() => setChannelKeyVisible(!channelKeyVisible)}
                      >
                        {channelKeyVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </Button>
                    )}
                  </div>
                </div>
              ))
            )}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setChannelDialogOpen(false)}>取消</Button>
              {channelForm.type === 'openclaw' ? (
                <Button onClick={() => void startWechatBind()} disabled={wechatBindStarting}>
                  {wechatBindStarting ? '发起中…' : '绑定个人微信'}
                </Button>
              ) : (
                <Button onClick={saveChannel} disabled={!isChannelFormValid() || testing !== null}>
                  {testing !== null ? '测试中…' : (editChannelId ? '保存并测试' : '创建并测试')}
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Version Footer */}
      {version && (
        <div className="mt-8 text-center text-[11px] text-muted-foreground/60">
          数智分析 v{version}
        </div>
      )}
    </div>
  )
}
