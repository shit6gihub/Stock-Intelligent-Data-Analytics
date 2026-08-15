import os

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.web.api import (
    stocks,
    agents,
    presets,
    settings,
    logs,
    providers,
    channels,
    datasources,
    subscriptions,
    accounts,
    history,
    news,
    market,
    reports,
    strategies,
    auth,
    suggestions,
    quotes,
    klines,
    templates,
    feedback,
    discovery,
    price_alerts,
    context,
    recommendations,
    dashboard,
    paper_trading,
    chat,
    forecast,
    calendar,
    market_data,
    tdx,
    shadow,
    ths,
    darkflow,
    chat_upload,
    my_ai_services,
    users,
    llm_usage,
    profile,
    export as export_data,
    audit,
)
from src.web.api import factors
from src.web.api import notifications
from src.web.api import health
from src.web.api import insights
from src.web.api import wechat_bind
from src.web.api.auth import get_current_user
from src.web.api.settings import get_app_version
from src.web.response import ResponseWrapperMiddleware

app = FastAPI(
    title="SIDA API",
    version="0.1.0",
    redirect_slashes=False,  # 避免重定向丢失 Authorization header
    # 安全: 生产关闭 API 文档(/docs /redoc /openapi.json), 防接口地图泄露(2026-08-15 公开 demo 后)
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# GZip 压缩(2026-08-10): 静态 JS 2.3MB 未压缩, 跨境弱网加载慢 → 压缩后 ~600KB
# ⚠️ 顺序关键: Starlette add_middleware 后加的在更外层(先执行)。
# 正确: ResponseWrapper 先 add(内层, 先拿到后端原始响应并包装),
#      GZip 后 add(外层, 最后压缩包装后的响应)。
# 之前顺序反了(GZip内层), wrapper 收到压缩字节流 → JSON 解析失败 → 返回裸数据(设置页"都没了")。
app.add_middleware(ResponseWrapperMiddleware)
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
_cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════════════
# 账号权限控制(2026-08-15 RBAC): 角色权限驱动, 替代 username==demo 硬编码
# 1) guest(demo) 隔离: 只读浏览 + 管理页 403 + GET 限流 + 自选增删例外(行为保持现状)
# 2) 管理区 RBAC: 管理区路径 → 对应 manage_* 权限点; owner 全过,
#    member 默认无 manage_* → 403(owner 可在 users.permissions 数组给 member
#    加白名单权限点, 向后兼容通道); /api/settings /api/providers 的 GET
#    允许浏览(敏感 key 已掩码)。
# 判定: JWT payload.username + payload.role → ROLE_PERMISSIONS。
# 登录/刷新在认证前(无 token), 不受影响。
# ════════════════════════════════════════════════════════════════════
_DEMO_ADMIN_PREFIXES = (
    "/api/datasources",
    "/api/settings",
    "/api/ai-services",
    "/api/agents",
    "/api/strategies",
    "/api/users",
    "/api/shadow",
    "/api/paper-trading",
    "/api/forecast/predict",
    "/api/upload",
    "/api/reports/generate",
    "/api/wechat",
)

# 管理区路径 → 所需权限点(2026-08-15 RBAC; /api/providers 原不在隔离列表,
# 现纳入管理区, GET 仍可浏览)
_ADMIN_PREFIX_PERMISSIONS = {
    "/api/datasources": "manage_datasources",
    "/api/settings": "manage_settings",
    "/api/ai-services": "manage_ai_services",
    "/api/providers": "manage_ai_services",
    "/api/agents": "manage_agents",
    "/api/strategies": "manage_strategies",
    "/api/users": "manage_users",
    "/api/shadow": "manage_shadow",
    "/api/paper-trading": "manage_paper_trading",
    "/api/forecast/predict": "run_prediction",
    "/api/upload": "upload_files",
    "/api/reports/generate": "manage_settings",
}
# 管理区中允许 GET 浏览的路径(敏感 key 已掩码, 只读无风险)
_READABLE_ADMIN_PREFIXES = ("/api/settings", "/api/providers")


def _resolve_user_auth(username: str) -> tuple[str | None, set[str]]:
    """查 DB 取用户 role + users.permissions 白名单权限点(失败返回 (None, 空集))。

    users.permissions 兼容两种形态:
      - list: ["manage_datasources", ...] 权限点字符串数组(预留格式, 白名单)
      - dict: {"permissions": [...], "model_access": {...}} 新版扩展格式
    """
    try:
        from src.web.database import SessionLocal
        from src.web.models import User

        db = SessionLocal()
        try:
            u = db.query(User).filter(User.username == username).first()
            if not u:
                return None, set()
            extra: set[str] = set()
            perms = u.permissions
            if isinstance(perms, list):
                extra = {p for p in perms if isinstance(p, str)}
            elif isinstance(perms, dict):
                extra = {p for p in perms.get("permissions", []) if isinstance(p, str)}
            role_val = u.role
            return (str(role_val) if role_val is not None else None), extra
        finally:
            db.close()
    except Exception:
        return None, set()


@app.middleware("http")
async def demo_isolation_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    # 非 API 路径(静态资源)直接放行
    if not path.startswith("/api/"):
        return await call_next(request)

    username = None
    payload = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from src.web.api.auth import decode_token
            payload = decode_token(auth[7:])
            if payload:
                username = payload.get("username")
        except Exception:
            pass

    # 未认证 / CORS 预检: 放行(各路由自行鉴权)
    if not username or method == "OPTIONS":
        return await call_next(request)

    from src.core.permissions import get_role_permissions

    # role 优先级: JWT payload.role → DB users.role → "member"(向后兼容老 token)
    db_role, extra_perms = _resolve_user_auth(username)
    role = (payload.get("role") if payload else None) or db_role or "member"

    # ── guest(demo) 隔离: 行为保持现状 ──────────────────────────────
    if username == "demo" or role == "guest":
        msg = "演示账号为只读浏览模式,不可修改数据或访问管理页面。请自行部署体验完整功能: https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics"
        # 0) GET 限流: 每小时 20 次 API 请求(防爬虫刷数据源配额)
        if method in ("GET", "HEAD"):
            from src.core.demo_limit import allow_api_get
            if not allow_api_get(str(payload.get("sub", ""))):
                return JSONResponse(status_code=429, content={"code": 429, "success": False, "message": "演示账号请求过于频繁(每小时限 20 次)。请稍后再试,或自行部署体验完整功能: https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics"})
        # demo 专属例外: 自选增删(自己的数据, user_id 隔离; 数量上限在接口层)
        is_own_watchlist_write = (
            (method == "POST" and path.rstrip("/") == "/api/stocks")
            or (method == "DELETE" and path.startswith("/api/stocks/"))
        )
        # 1) 写操作: 除自选增删外一律拒绝
        if method not in ("GET", "HEAD", "OPTIONS") and not is_own_watchlist_write:
            return JSONResponse(status_code=403, content={"code": 403, "success": False, "message": msg})
        # 2) 管理区页面隔离: 设置/服务商列表允许浏览(敏感 key 已掩码), 其余管理页仍不可见
        _DEMO_READABLE_PREFIXES = ("/api/settings", "/api/providers")
        if path.startswith(_DEMO_ADMIN_PREFIXES) and not path.startswith(_DEMO_READABLE_PREFIXES):
            return JSONResponse(status_code=403, content={"code": 403, "success": False, "message": msg})
        return await call_next(request)

    # ── 非 guest: 角色权限驱动 ──────────────────────────────────────
    perms = set(get_role_permissions(role))
    perms |= extra_perms  # owner 给 member 开的白名单权限点

    for prefix, required in _ADMIN_PREFIX_PERMISSIONS.items():
        if path.startswith(prefix):
            if required in perms:
                break
            # 只读浏览例外: settings/providers GET(敏感 key 已掩码)
            if method in ("GET", "HEAD") and prefix in _READABLE_ADMIN_PREFIXES:
                break
            return JSONResponse(
                status_code=403,
                content={"code": 403, "success": False, "message": "无权限访问该管理功能, 请联系管理员"},
            )
    return await call_next(request)

# 认证路由（无需登录）
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# 行情 WebSocket(2026-08-12): 独立 router, 无路由级 auth(WS 握手与 HTTPBearer 冲突)
from src.web.api.ws_quotes import router as ws_quotes_router

app.include_router(ws_quotes_router, prefix="/api", tags=["quotes-ws"])
# 市场指数（公共数据，无需登录）
app.include_router(market.router, prefix="/api/market", tags=["market"])
# TradingView Alert Webhook(2026-08-12): 免登录, secret 鉴权
from src.web.api import tradingview_webhook

app.include_router(tradingview_webhook.router, prefix="/api/webhooks", tags=["webhooks"])

# 需要登录的路由
protected = [Depends(get_current_user)]
app.include_router(
    stocks.router, prefix="/api/stocks", tags=["stocks"], dependencies=protected
)
app.include_router(
    quotes.router, prefix="/api/quotes", tags=["quotes"], dependencies=protected
)
app.include_router(
    klines.router, prefix="/api/klines", tags=["klines"], dependencies=protected
)
app.include_router(
    insights.router, prefix="/api/insights", tags=["insights"], dependencies=protected
)
app.include_router(
    accounts.router, prefix="/api", tags=["accounts"], dependencies=protected
)
app.include_router(
    agents.router, prefix="/api/agents", tags=["agents"], dependencies=protected
)
app.include_router(
    presets.router, prefix="/api/agents/presets", tags=["presets"], dependencies=protected
)
app.include_router(
    providers.router,
    prefix="/api/providers",
    tags=["providers"],
    dependencies=protected,
)
app.include_router(
    channels.router, prefix="/api/channels", tags=["channels"], dependencies=protected
)
app.include_router(
    subscriptions.router, prefix="/api/subscriptions", tags=["subscriptions"], dependencies=protected
)
app.include_router(
    notifications.router,
    prefix="/api/notifications",
    tags=["notifications"],
    dependencies=protected,
)
app.include_router(
    wechat_bind.router,
    tags=["wechat-bind"],
    dependencies=protected,
)
app.include_router(
    datasources.router,
    prefix="/api/datasources",
    tags=["datasources"],
    dependencies=protected,
)
app.include_router(
    settings.router, prefix="/api/settings", tags=["settings"], dependencies=protected
)
app.include_router(
    logs.router, prefix="/api/logs", tags=["logs"], dependencies=protected
)
app.include_router(
    history.router, prefix="/api", tags=["history"], dependencies=protected
)
app.include_router(
    context.router, prefix="/api", tags=["context"], dependencies=protected
)
# 权限体系(2026-08-15): BYOK 用户自定义服务商 + 用户模型授权管理
app.include_router(
    my_ai_services.router,
    prefix="/api/my-ai-services",
    tags=["my-ai-services"],
    dependencies=protected,
)
app.include_router(
    users.router,
    prefix="/api/users",
    tags=["users"],
    dependencies=protected,
)
app.include_router(
    llm_usage.router,
    prefix="/api",
    tags=["llm-usage"],
    dependencies=protected,
)
app.include_router(
    profile.router,
    prefix="/api/profile",
    tags=["profile"],
    dependencies=protected,
)
app.include_router(
    export_data.router,
    prefix="/api",
    tags=["export"],
    dependencies=protected,
)
app.include_router(
    audit.router,
    prefix="/api/audit",
    tags=["audit"],
    dependencies=protected,
)
app.include_router(
    news.router, prefix="/api/news", tags=["news"], dependencies=protected
)
app.include_router(
    suggestions.router,
    prefix="/api/suggestions",
    tags=["suggestions"],
    dependencies=protected,
)
app.include_router(
    templates.router,
    prefix="/api/templates",
    tags=["templates"],
    dependencies=protected,
)
app.include_router(
    feedback.router,
    prefix="/api/feedback",
    tags=["feedback"],
    dependencies=protected,
)

app.include_router(
    discovery.router,
    prefix="/api/discovery",
    tags=["discovery"],
    dependencies=protected,
)
app.include_router(
    price_alerts.router,
    prefix="/api/price-alerts",
    tags=["price-alerts"],
    dependencies=protected,
)
app.include_router(
    recommendations.router,
    prefix="/api/recommendations",
    tags=["recommendations"],
    dependencies=protected,
)
app.include_router(
    dashboard.router,
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=protected,
)
app.include_router(
    factors.router,
    prefix="/api/factors",
    tags=["factors"],
    dependencies=protected,
)
app.include_router(
    health.router,
    prefix="/api/health",
    tags=["health"],
    dependencies=protected,
)
app.include_router(
    forecast.router,
    prefix="/api",
    tags=["forecast"],
    dependencies=protected,
)
app.include_router(
    paper_trading.router,
    prefix="/api/paper-trading",
    tags=["paper-trading"],
    dependencies=protected,
)
app.include_router(
    chat.router,
    prefix="/api/chat",
    tags=["chat"],
    dependencies=protected,
)
# 对话助手附件上传/解析(2026-08-14): 图片 OCR / Excel / PDF / txt,md
app.include_router(
    chat_upload.router,
    prefix="/api/chat",
    tags=["chat-upload"],
    dependencies=protected,
)
app.include_router(
    reports.router,
    prefix="/api/reports",
    tags=["reports"],
    dependencies=protected,
)
app.include_router(
    strategies.router,
    prefix="/api/strategies",
    tags=["strategies"],
    dependencies=protected,
)
app.include_router(
    calendar.router,
    prefix="/api/calendar",
    tags=["calendar"],
    dependencies=protected,
)
app.include_router(
    market_data.router,
    prefix="/api/market-data",
    tags=["market-data"],
    dependencies=protected,
)
app.include_router(
    tdx.router,
    prefix="/api/tdx",
    tags=["tdx"],
    dependencies=protected,
)
app.include_router(
    shadow.router,
    prefix="/api/shadow",
    tags=["shadow"],
    dependencies=protected,
)
app.include_router(
    ths.router,
    prefix="/api/ths",
    tags=["ths"],
    dependencies=protected,
)
# 内盘外盘口诀 + 主力意图(分时卡片轻接口, 2026-08-13)
app.include_router(
    darkflow.router,
    prefix="/api/dark-flow",
    tags=["dark-flow"],
    dependencies=protected,
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def version():
    """获取应用版本号（公开接口）"""
    return {"version": get_app_version()}
