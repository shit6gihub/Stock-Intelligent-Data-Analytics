import { useCallback, useEffect, useState } from 'react'
import { Users, Plus, Trash2, KeyRound, Ban, CheckCircle2, UserCog } from 'lucide-react'
import { authApi, UserInfo } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface Props {
  currentUser: UserInfo | null
}

export default function UserManagement({ currentUser }: Props) {
  const { toast } = useToast()
  const [users, setUsers] = useState<UserInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newPass, setNewPass] = useState('')
  const [newRole, setNewRole] = useState<'owner' | 'member'>('member')
  // 改密对话框
  const [resetTarget, setResetTarget] = useState<UserInfo | null>(null)
  const [resetPass, setResetPass] = useState('')

  const isOwner = currentUser?.role === 'owner'

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await authApi.listUsers()
      setUsers(data.users || [])
    } catch {
      /* 非 owner 无权限, 静默 */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isOwner) void load()
  }, [isOwner, load])

  const handleCreate = async () => {
    if (newName.trim().length < 2) return toast('用户名至少 2 位', 'error')
    if (newPass.length < 8) return toast('密码至少 8 位', 'error')
    try {
      await authApi.createUser({ username: newName.trim(), password: newPass, role: newRole })
      toast('子账号创建成功', 'success')
      setShowCreate(false)
      setNewName('')
      setNewPass('')
      void load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '创建失败', 'error')
    }
  }

  const handleToggleActive = async (u: UserInfo) => {
    try {
      await authApi.updateUser(u.id, { is_active: !u.is_active })
      toast(u.is_active ? `已禁用 ${u.username}` : `已启用 ${u.username}`, 'success')
      void load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    }
  }

  const handleResetPassword = async () => {
    if (!resetTarget) return
    if (resetPass.length < 8) return toast('密码至少 8 位', 'error')
    try {
      await authApi.updateUser(resetTarget.id, { password: resetPass })
      toast(`${resetTarget.username} 密码已重置`, 'success')
      setResetTarget(null)
      setResetPass('')
    } catch (e) {
      toast(e instanceof Error ? e.message : '重置失败', 'error')
    }
  }

  const handleDelete = async (u: UserInfo) => {
    if (!window.confirm(`确定删除用户 ${u.username}?其持仓/自选/渠道将一并删除`)) return
    try {
      await authApi.deleteUser(u.id)
      toast(`已删除 ${u.username}`, 'success')
      void load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error')
    }
  }

  if (!isOwner) {
    return (
      <div className="rounded-xl border border-border/50 bg-card p-6 text-center text-[13px] text-muted-foreground">
        <UserCog className="mx-auto mb-2 h-6 w-6 opacity-50" />
        仅管理员可管理用户
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-border/50 bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-[13px] font-semibold">
          <Users className="h-4 w-4 text-primary" /> 用户管理({users.length})
        </h3>
        <Button size="sm" variant="outline" onClick={() => setShowCreate(v => !v)}>
          <Plus className="mr-1 h-3.5 w-3.5" /> 新建子账号
        </Button>
      </div>

      {showCreate && (
        <div className="mb-3 space-y-2 rounded-lg border border-border/40 bg-accent/20 p-3">
          <input
            className="w-full rounded-md border border-border/60 bg-background px-2.5 py-1.5 text-[12px]"
            placeholder="用户名(如: 小李)"
            value={newName}
            onChange={e => setNewName(e.target.value)}
          />
          <input
            className="w-full rounded-md border border-border/60 bg-background px-2.5 py-1.5 text-[12px]"
            placeholder="密码(至少8位)"
            type="password"
            value={newPass}
            onChange={e => setNewPass(e.target.value)}
          />
          <div className="flex items-center gap-3 text-[12px]">
            <label className="flex items-center gap-1">
              <input type="radio" checked={newRole === 'member'} onChange={() => setNewRole('member')} /> 普通成员
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" checked={newRole === 'owner'} onChange={() => setNewRole('owner')} /> 管理员
            </label>
            <Button size="sm" onClick={handleCreate}>创建</Button>
          </div>
        </div>
      )}

      <div className="divide-y divide-border/40">
        {users.map(u => (
          <div key={u.id} className="flex items-center justify-between py-2 text-[12px]">
            <div className="flex items-center gap-2">
              <span className={`inline-block h-2 w-2 rounded-full ${u.is_active ? 'bg-emerald-500' : 'bg-muted'}`} />
              <span className="font-medium">{u.username}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${u.role === 'owner' ? 'bg-amber-500/15 text-amber-600' : 'bg-sky-500/15 text-sky-600'}`}>
                {u.role === 'owner' ? '管理员' : '成员'}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              {u.role !== 'owner' && (
                <>
                  <button
                    className="rounded p-1.5 text-muted-foreground hover:bg-accent"
                    title="重置密码"
                    onClick={() => setResetTarget(u)}
                  >
                    <KeyRound className="h-3.5 w-3.5" />
                  </button>
                  <button
                    className="rounded p-1.5 text-muted-foreground hover:bg-accent"
                    title={u.is_active ? '禁用' : '启用'}
                    onClick={() => handleToggleActive(u)}
                  >
                    {u.is_active ? <Ban className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                  </button>
                  <button
                    className="rounded p-1.5 text-rose-500/70 hover:bg-rose-500/10"
                    title="删除"
                    onClick={() => handleDelete(u)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
        {!users.length && !loading && (
          <div className="py-4 text-center text-[12px] text-muted-foreground">暂无用户</div>
        )}
      </div>

      {resetTarget && (
        <div className="mt-3 rounded-lg border border-border/40 bg-accent/20 p-3">
          <div className="mb-2 text-[12px] font-medium">重置 {resetTarget.username} 的密码</div>
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-md border border-border/60 bg-background px-2.5 py-1.5 text-[12px]"
              placeholder="新密码(至少8位)"
              type="password"
              value={resetPass}
              onChange={e => setResetPass(e.target.value)}
            />
            <Button size="sm" onClick={handleResetPassword}>确定</Button>
            <Button size="sm" variant="outline" onClick={() => setResetTarget(null)}>取消</Button>
          </div>
        </div>
      )}
    </div>
  )
}
