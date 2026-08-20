"""深度健康检查 + Prometheus /metrics (2026-08-17 v0.2.65 — Phase 1)

/health 端点 (deep health):
- 返回 PG 连接池 / Redis ping / 调度器存活 / 限流状态 / 服务版本
- 子项目异常时单独标记 unhealthy,不整体 500(便于 K8s / LB 区分)
- 返回 200 但 body 标 status:"degraded" 表示有组件故障(可配)

/metrics 端点 (Prometheus):
- 请求计数 (按 path+method+status)
- 请求耗时直方图
- 自定义业务 metric (预测命中率 / AI 调用次数)
"""

import time
import logging
import platform
from typing import Any

from fastapi import APIRouter, Response
try:
    from prometheus_client import (
        Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    # 定义占位, /metrics 端点返回 503
    def generate_latest():
        return b""
    CONTENT_TYPE_LATEST = "text/plain"
    Counter = Histogram = Gauge = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── Prometheus metrics (lazy init — 避免 prometheus_client 未装时崩) ───
class _Metrics:
    REQUEST_COUNT: Any = None
    REQUEST_DURATION: Any = None
    IN_FLIGHT: Any = None
    AI_CALLS: Any = None
    PREDICT_REQUESTS: Any = None
    NOTIFICATIONS_SENT: Any = None

_metrics = _Metrics()

def _init_metrics():
    if not _PROMETHEUS_AVAILABLE or _metrics.REQUEST_COUNT is not None:
        return
    _metrics.REQUEST_COUNT = Counter(
        "sida_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
    )
    _metrics.REQUEST_DURATION = Histogram(
        "sida_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "path"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    _metrics.IN_FLIGHT = Gauge(
        "sida_http_requests_in_flight",
        "Number of HTTP requests currently in flight",
    )
    _metrics.AI_CALLS = Counter(
        "sida_ai_calls_total",
        "AI/LLM calls by model and result",
        ["model", "scene", "result"],
    )
    _metrics.PREDICT_REQUESTS = Counter(
        "sida_predict_requests_total",
        "Forecast requests by symbol",
        ["symbol"],
    )
    _metrics.NOTIFICATIONS_SENT = Counter(
        "sida_notifications_sent_total",
        "Notifications sent by channel and result",
        ["channel", "result"],
    )


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus 抓取端点"""
    if not _PROMETHEUS_AVAILABLE:
        return Response(content=b"# prometheus_client not installed\n", media_type="text/plain", status_code=503)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/health")
async def health() -> dict[str, Any]:
    """深度健康检查 — 返回各组件状态

    返回结构:
    {
      "status": "ok" | "degraded" | "down",
      "version": "v0.2.65",
      "uptime_seconds": 1234,
      "components": {
        "database": {"status": "ok", "pool_size": 5},
        "redis": {"status": "ok", "url": "redis://..."},
        "scheduler": {"status": "ok", "schedulers": ["agent", "price_alert", "paper_trading"]},
        "rate_limit": {"enabled": true, "buckets": 12},
      },
      "service": {"name": "SIDA", "python": "3.11.4", "platform": "linux"}
    }
    """
    components = {}
    overall_ok = True

    # ─── DB 检查 ───
    try:
        from src.web.database import engine
        from sqlalchemy import text
        start = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = int((time.perf_counter() - start) * 1000)
        components["database"] = {
            "status": "ok",
            "latency_ms": latency_ms,
            "url": str(engine.url).split("@")[-1] if "@" in str(engine.url) else "sqlite",
        }
    except Exception as e:
        components["database"] = {"status": "down", "error": str(e)[:100]}
        overall_ok = False

    # ─── Redis 检查 ───
    try:
        from src.web.cache.redis_client import redis_client
        if not redis_client._enabled:
            components["redis"] = {"status": "disabled"}
        elif await redis_client.ping():
            from src.web.cache.redis_client import REDIS_URL
            components["redis"] = {"status": "ok", "url": REDIS_URL}
        else:
            components["redis"] = {"status": "down", "url": "n/a"}
            # Redis 降级 OK — 不影响 overall
    except Exception as e:
        components["redis"] = {"status": "down", "error": str(e)[:100]}

    # ─── 调度器存活检查 ───
    try:
        import server as srv_mod
        schedulers = []
        schedulers_status = {"running": 0, "shutdown": 0}
        for attr_name in ("scheduler", "price_alert_scheduler", "paper_trading_scheduler",
                          "context_maintenance_scheduler", "kline_backfill_scheduler"):
            sched = getattr(srv_mod, attr_name, None)
            if sched is not None:
                name = attr_name.replace("_scheduler", "")
                # 存活判定优先用内部 APScheduler 实例的 .running(真·运行状态)。
                # 注意: 封装类的 _running 是 job 重入锁(扫描开始置 True、结束置 False,
                # 平时恒为 False),不能作为调度器存活依据。
                inner = getattr(sched, "scheduler", None)
                if inner is not None and hasattr(inner, "running"):
                    running = bool(inner.running)
                elif hasattr(sched, "running"):
                    running = bool(sched.running)
                elif hasattr(sched, "_running"):
                    running = bool(sched._running)
                else:
                    running = False
                schedulers.append(name)
                if running:
                    schedulers_status["running"] += 1
                else:
                    schedulers_status["shutdown"] += 1
        components["scheduler"] = {
            "status": "ok" if schedulers_status["running"] >= 2 else "degraded",
            "schedulers": schedulers,
            **schedulers_status,
        }
        if schedulers_status["running"] < 2:
            overall_ok = False
    except Exception as e:
        components["scheduler"] = {"status": "down", "error": str(e)[:100]}
        overall_ok = False

    # ─── 限流状态 ───
    try:
        from src.web.middleware import get_rate_limit_stats
        components["rate_limit"] = get_rate_limit_stats()
    except Exception:
        components["rate_limit"] = {"enabled": False, "error": "not loaded"}

    # ─── service info ───
    import os
    try:
        with open("/app/VERSION") as f:
            version = f.read().strip()
    except Exception:
        version = os.getenv("APP_VERSION", "unknown")

    # ─── 整体状态 ───
    if overall_ok and components["redis"]["status"] in ("ok", "disabled"):
        overall = "ok"
    elif overall_ok:
        overall = "degraded"  # Redis 挂了但其他 OK
    else:
        overall = "down"

    return {
        "status": overall,
        "version": version,
        "components": components,
        "service": {
            "name": "SIDA",
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }