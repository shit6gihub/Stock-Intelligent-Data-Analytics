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
# demo 账号隔离(2026-08-15): 公开 demo 后, 访客只读浏览 + 不可见管理页面
# 1) 只读: 非 GET 请求一律 403(防修改数据源/设置/持仓等任何写操作)
# 2) 页面隔离: 管理区路径 GET 也 403(数据源/设置/AI服务商/Agent/策略/用户等)
# 登录/刷新在认证前(无 token), 不受影响。判定: JWT payload.username == "demo"。
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


@app.middleware("http")
async def demo_isolation_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    # 非 API 路径(静态资源)直接放行
    if not path.startswith("/api/"):
        return await call_next(request)

    username = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from src.web.api.auth import decode_token
            payload = decode_token(auth[7:])
            if payload:
                username = payload.get("username")
        except Exception:
            pass

    if username == "demo":
        msg = "演示账号为只读浏览模式,不可修改数据或访问管理页面。请自行部署体验完整功能: https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics"
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
