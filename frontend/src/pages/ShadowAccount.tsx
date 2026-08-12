import { useState } from 'react'
import { Upload, FileText, Download, TrendingUp, Activity, Target, Shield, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'
import { fetchAPI } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface ShadowResult {
  shadow_id: string
  profile: Record<string, any>
  behavior: Record<string, any> | null
  stats: Record<string, any> | null
  attribution: Record<string, any> | null
  report_html: string
  report_pdf: string | null
}

function fmt(v: any, suffix = ''): string {
  if (v === null || v === undefined || v === '') return '--'
  if (typeof v === 'number') {
    if (Math.abs(v) >= 10000) return `${(v / 10000).toFixed(2)}万${suffix}`
    if (Math.abs(v) >= 100) return v.toFixed(2) + suffix
    return String(v) + suffix
  }
  return String(v) + suffix
}

function StatCard({ icon: Icon, label, value, sub, color }: { icon: any; label: string; value: string; sub?: string; color: string }) {
  return (
    <div className="rounded-xl bg-accent/30 p-3.5">
      <div className="flex items-center gap-2 mb-1.5">
        <Icon className={`w-3.5 h-3.5 ${color}`} />
        <span className="text-[11px] text-muted-foreground">{label}</span>
      </div>
      <div className="text-[15px] font-semibold text-foreground">{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  )
}

export default function ShadowAccountPage() {
  const [result, setResult] = useState<ShadowResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [dragOver, setDragOver] = useState(false)

  const upload = async (file: File) => {
    setLoading(true)
    setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      const d = await fetchAPI<ShadowResult>('/shadow/analyze', {
        method: 'POST',
        body: form,
        timeoutMs: 180000, // 交割单解析+画像可能 60-120s(586笔实测75s), 默认20s不够
      })
      setResult((d as any)?.data ?? d)
    } catch (e: any) {
      setError(e?.message || '分析失败，请检查交割单格式')
    } finally {
      setLoading(false)
    }
  }

  const openReportHtml = async () => {
    if (!result) return
    try {
      const token = localStorage.getItem('token') || ''
      const resp = await fetch(result.report_html, { headers: { Authorization: `Bearer ${token}` } })
      const html = await resp.text()
      const w = window.open('', '_blank')
      if (w) { w.document.write(html); w.document.close() }
    } catch { window.open(result.report_html, '_blank') }
  }

  const downloadPdf = async () => {
    if (!result?.report_pdf) return
    try {
      const token = localStorage.getItem('token') || ''
      const resp = await fetch(result.report_pdf, { headers: { Authorization: `Bearer ${token}` } })
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `${result.shadow_id}.pdf`; a.click()
      URL.revokeObjectURL(url)
    } catch { window.open(result.report_pdf, '_blank') }
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      {/* 页头 */}
      <div>
        <h1 className="text-[17px] font-semibold text-foreground flex items-center gap-2">
          <Shield className="w-4.5 h-4.5 text-primary" />
          影子账户 Shadow Account
        </h1>
        <p className="text-[12px] text-muted-foreground mt-1">
          上传你的交易交割单（同花顺 / 东财 / 富途 / 通用 CSV），AI 提炼你的真实交易行为画像、盈利模式与风险习惯。
        </p>
      </div>

      {/* 上传区 */}
      <div
        className={`border-2 border-dashed rounded-2xl p-8 text-center transition-colors cursor-pointer ${
          dragOver ? 'border-primary bg-primary/5' : 'border-border bg-accent/10 hover:bg-accent/20'
        }`}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => {
          e.preventDefault(); setDragOver(false)
          const f = e.dataTransfer.files?.[0]
          if (f) upload(f)
        }}
        onClick={() => document.getElementById('shadow-file-input')?.click()}
      >
        <input
          id="shadow-file-input"
          type="file"
          accept=".csv,.xlsx,.xls,.pdf"
          className="hidden"
          onChange={e => { const f = e.target.files?.[0]; if (f) upload(f) }}
        />
        {loading ? (
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="w-8 h-8 text-primary animate-spin" />
            <span className="text-[13px] text-muted-foreground">正在分析交割单，生成行为画像...</span>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <Upload className="w-8 h-8 text-muted-foreground" />
            <span className="text-[13px] font-medium text-foreground">点击或拖拽上传交割单</span>
            <span className="text-[11px] text-muted-foreground">支持 .csv / .xlsx / .pdf（同花顺、国投、东财格式自动识别）</span>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-red-500/10 border border-red-500/20 p-3 text-[12px] text-red-500">
          <AlertTriangle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* 结果展示 */}
      {result && (
        <>
          {/* 行为画像 */}
          {result.profile && (
            <div className="space-y-4">
              <h2 className="text-[14px] font-semibold text-foreground flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-primary" /> 行为画像
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard icon={Activity} label="总交易回合" value={fmt(result.profile.total_roundtrips)} color="text-blue-500" />
                <StatCard icon={CheckCircle2} label="盈利回合" value={fmt(result.profile.profitable_roundtrips)} sub={result.profile.total_roundtrips ? `胜率 ${((result.profile.profitable_roundtrips / result.profile.total_roundtrips) * 100).toFixed(0)}%` : undefined} color="text-emerald-500" />
                <StatCard icon={Activity} label="平均持有时长" value={fmt(result.profile.typical_holding_days, '天')} color="text-violet-500" />
                <StatCard icon={Target} label="偏好市场" value={(result.profile.preferred_markets || []).join(', ') || '--'} color="text-amber-500" />
              </div>
              {result.profile.rules && result.profile.rules.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {result.profile.rules.map((r: string) => (
                    <span key={r} className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-1 text-[11px] text-primary">{r}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 行为诊断 */}
          {result.behavior && (
            <div className="space-y-4">
              <h2 className="text-[14px] font-semibold text-foreground flex items-center gap-2">
                <Target className="w-4 h-4 text-primary" /> 行为诊断
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {(Object.entries(result.behavior) as [string, any][]).filter(([k]) => k !== 'error').map(([k, v]) => (
                  <StatCard key={k} icon={Activity} label={k} value={fmt(v)} color="text-amber-500" />
                ))}
              </div>
            </div>
          )}

          {/* 归因结果 */}
          {result.attribution && (
            <div className="space-y-4">
              <h2 className="text-[14px] font-semibold text-foreground flex items-center gap-2">
                <Shield className="w-4 h-4 text-primary" /> 归因分析
              </h2>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <StatCard icon={TrendingUp} label="影子收益" value={fmt(result.attribution.shadow_total_pnl)} color="text-emerald-500" />
                <StatCard icon={Activity} label="实际收益" value={fmt(result.attribution.real_total_pnl)} color="text-blue-500" />
                <StatCard icon={CheckCircle2} label="差值" value={fmt(result.attribution.delta_pnl)} color="text-violet-500" />
                <StatCard icon={Target} label="归因项" value={fmt((result.attribution.attribution || []).length)} color="text-amber-500" />
              </div>
            </div>
          )}

          {/* 报告链接 */}
          <div className="flex flex-wrap gap-2 pt-2">
            <Button size="sm" onClick={openReportHtml}>
              <FileText className="w-3.5 h-3.5 mr-1.5" /> 查看完整报告
            </Button>
            {result.report_pdf && (
              <Button size="sm" variant="secondary" onClick={downloadPdf}>
                <Download className="w-3.5 h-3.5 mr-1.5" /> 下载 PDF
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
