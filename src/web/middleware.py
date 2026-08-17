"""统一网关中间件 (2026-08-17 v0.2.65 — Phase 1)

提供:
- RequestLoggerMiddleware: 每个请求打日志(方法 + 路径 + 状态码 + 耗时)
- RateLimitMiddleware: 基于 IP+endpoint 的限流(Redis token bucket,降级内存)
- JWTDecodeMiddleware: 解码 JWT payload 存到 request.state(避免重复解码)

设计要点:
- 用 BaseHTTPMiddleware, FastAPI 0.104+ 标准
- 限流降级: Redis 不可用时用进程内 dict 仍然限流(只是不跨进程)
- 例外: /health /metrics /static 跳过限流(避免监控系统被自己限流)
"""

import json
import logging
import time
import os
from collections import defaultdict
from typing import Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


# ─── 全局开关 ───
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default

RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
RATE_LIMIT_DEFAULT = _env_int("RATE_LIMIT_DEFAULT", 60)  # 默认 60 req / minute / IP
RATE_LIMIT_WINDOW = 60  # 60 秒滑动窗口
RATE_LIMIT_BURST = _env_int("RATE_LIMIT_BURST", 10)  # 突发容忍 10 个

# 例外路径 (跳过限流和日志)
EXEMPT_PATHS = {
    "/health", "/metrics", "/api/health", "/favicon.ico",
}


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP (优先 X-Forwarded-For)"""
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if xff:
        return xff
    return request.client.host if request.client else "unknown"


def _is_exempt(path: str) -> bool:
    for p in EXEMPT_PATHS:
        if path == p or path.startswith(p + "/"):
            return True
    return False


# ─── 限流存储(降级内存) ───
class _InMemoryBucket:
    """进程内 token bucket(降级 — Redis 不可用时使用)"""

    def __init__(self):
        # key: (ip, endpoint) → [token_count, last_refill_ts]
        self._buckets: dict = defaultdict(lambda: [RATE_LIMIT_DEFAULT, time.time()])
        self._lock = None  # 进程内单线程够用(uvicorn 单 worker)

    def allow(self, key: Tuple[str, str], limit: int = RATE_LIMIT_DEFAULT) -> bool:
        now = time.time()
        tokens, last = self._buckets[key]
        # 补充 token: 每秒补 limit/60 个
        elapsed = now - last
        refill = elapsed * (limit / RATE_LIMIT_WINDOW)
        tokens = min(limit, tokens + refill)
        if tokens >= 1:
            tokens -= 1
            self._buckets[key] = [tokens, now]
            return True
        self._buckets[key] = [tokens, now]
        return False

    def stats(self) -> dict:
        return {"buckets": len(self._buckets), "limit_default": RATE_LIMIT_DEFAULT}


_in_memory_bucket = _InMemoryBucket()


# ─── JWT 解码中间件 ───
class JWTDecodeMiddleware(BaseHTTPMiddleware):
    """解码 JWT payload, 把 user_id/role/username 放到 request.state.user

    不强制鉴权 — 鉴权由各路由的 Depends(get_current_user) 决定
    这里是性能优化: 让依赖能直接读 request.state.user 避免重复解码
    """
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                # 使用 auth 模块的解码函数(共享配置 + 异常处理)
                from src.web.api.auth import decode_token as _decode_token
                payload = _decode_token(auth[7:])
                if payload:
                    request.state.user = {
                        "user_id": payload.get("user_id"),
                        "username": payload.get("username"),
                        "role": payload.get("role"),
                    }
            except Exception:
                pass  # 鉴权失败路由自己返回 401
        return await call_next(request)


# ─── 限流中间件 ───
class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 (IP, endpoint) 的 token bucket 限流

    例外: /health /metrics /静态资源
    Auth: 已登录用户按 user_id 限流(更宽松), 匿名按 IP
    Redis: 优先用 Redis incr+expire, 降级到内存
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if not RATE_LIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)

        # GET 严格, 写操作稍宽(避免误伤)
        method = request.method
        if method == "GET":
            limit = _env_int("RATE_LIMIT_GET", RATE_LIMIT_DEFAULT)  # 60 / min
        else:
            limit = _env_int("RATE_LIMIT_WRITE", RATE_LIMIT_DEFAULT // 2)  # 30 / min

        # Key: 已登录按 user_id, 否则 IP
        user = getattr(request.state, "user", None)
        if user and user.get("user_id"):
            bucket_key = f"u:{user['user_id']}"
        else:
            ip = _get_client_ip(request)
            bucket_key = f"i:{ip}"

        endpoint = f"{method}:{path}"

        # Redis 优先, 降级内存
        from src.web.cache.redis_client import redis_client
        allowed = True
        if redis_client.enabled:
            redis_key = f"rl:{bucket_key}:{endpoint}"
            # 用滑动窗口近似: incr + 60s expire
            count = await redis_client.incr(redis_key, ttl_seconds=RATE_LIMIT_WINDOW)
            if count is not None and count > limit:
                allowed = False
        else:
            allowed = _in_memory_bucket.allow((bucket_key, endpoint), limit=limit)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁, 请稍后再试", "limit": limit, "window": RATE_LIMIT_WINDOW},
                headers={
                    "Retry-After": str(RATE_LIMIT_WINDOW),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)

        # 响应头加 X-RateLimit
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Window"] = str(RATE_LIMIT_WINDOW)
        return response


# ─── 请求日志中间件 ───
class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """结构化请求日志 — 每条请求一行 JSON, 方便 Loki/Grafana 聚合

    包含字段: method, path, status, duration_ms, client_ip, user_agent, user_id
    """

    def __init__(self, app, sample_rate: float = 1.0):
        super().__init__(app)
        self._sample_rate = sample_rate

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status: int = 0  # 默认值, finally 块用到
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as e:
            status = 500
            logger.exception(f"请求异常 {request.method} {request.url.path}: {e}")
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            user = getattr(request.state, "user", None)
            user_id = user.get("user_id") if user else None
            # 结构化日志(INFO 级 — 让运维聚合)
            try:
                logger.info(json.dumps({
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": duration_ms,
                    "client_ip": _get_client_ip(request),
                    "user_id": user_id,
                    "ua": (request.headers.get("user-agent", "")[:60]),
                }, ensure_ascii=False))
            except Exception:
                pass


# ─── 健康快照(供 /health 端点读取) ───
def get_rate_limit_stats() -> dict:
    """给 /health 端点"""
    return {
        "enabled": RATE_LIMIT_ENABLED,
        "default_limit": RATE_LIMIT_DEFAULT,
        "window_seconds": RATE_LIMIT_WINDOW,
        "in_memory": _in_memory_bucket.stats(),
    }