"""SIDA RBAC 权限模型(2026-08-15)。

角色 → 权限点映射 + 查询函数, 统一账号级权限判定入口。
替代散落的 username == "demo" 硬编码: 中间件(接口访问控制)、接口层
(数量上限)、LLM 授权(get_model_for_scene 用户级解析)均从此取。

角色:
  - owner: 全部权限(含所有 manage_*)
  - member: 浏览全部 + 自选/持仓编辑 + 预测 + 聊天 + 上传(默认无 manage_*)
  - guest: 仅浏览(view_*, 无 manage_*/edit/run/use/upload),
           外加 GUEST_STRATEGY 附加限制常量(自选上限 / GET 限流)

users.permissions JSON 列(复用): 两种形态
  - list:  ["manage_datasources", ...] 权限点字符串数组(预留格式, 白名单)
  - dict:  {"permissions": [...], "model_access": {"mode": ..., "model_ids": [...]}}
           model_access 供 LLM 用户级授权(get_model_for_scene)使用
"""

# ── 权限点定义 ──────────────────────────────────────────────────────
VIEW_PERMISSIONS = frozenset({
    "view_dashboard",
    "view_quotes",
    "view_forecast",
    "view_reports",
    "view_opportunities",
})

MANAGE_PERMISSIONS = frozenset({
    "manage_datasources",
    "manage_settings",
    "manage_ai_services",
    "manage_users",
    "manage_agents",
    "manage_strategies",
    "manage_shadow",
    "manage_paper_trading",
})

MEMBER_EXTRA_PERMISSIONS = frozenset({
    "edit_watchlist",
    "edit_portfolio",
    "run_prediction",
    "use_chat",
    "upload_files",
})

ALL_PERMISSIONS = VIEW_PERMISSIONS | MANAGE_PERMISSIONS | MEMBER_EXTRA_PERMISSIONS

# ── 角色 → 权限映射 ─────────────────────────────────────────────────
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMISSIONS,
    "member": VIEW_PERMISSIONS | MEMBER_EXTRA_PERMISSIONS,
    "guest": VIEW_PERMISSIONS,
}

# guest(demo) 附加限制常量(接口层/限流层读取)
GUEST_STRATEGY: dict[str, int] = {
    "watchlist_limit": 1,     # 自选股数量上限(只)
    "get_hourly_limit": 20,   # GET 请求每小时限流次数
}


def get_role_permissions(role: str | None) -> set[str]:
    """返回角色对应的权限点集合; 未知角色返回空集(最严, 宁可少放)。"""
    if not role:
        return set()
    return set(ROLE_PERMISSIONS.get(role, frozenset()))


def has_permission(role: str | None, perm: str) -> bool:
    """判断角色是否拥有某权限点。"""
    return perm in get_role_permissions(role)
