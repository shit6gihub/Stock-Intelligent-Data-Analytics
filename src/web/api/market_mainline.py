"""市场主线识别 API(v0.3.0, 2026-08-24)。

端点(挂在 /api/market 前缀下, 与现有 market.py 同前缀不冲突):
    GET /api/market/mainline         → Top20 主线排名 + 各主线成分股

设计:
  - 数据源: MarketSentimentCollector().get_limit_up_pool()(wudao 优先, 东财兜底)
  - 聚合: src.core.market_mainline.aggregate_mainline
  - 缓存: 模块内 60s 进程内 dict(per spec), 避免每 30s 前端轮询都翻涨停池

与其他 market.* 接口解耦: 仅本路由聚合涨停池, 不动现有 indices/sparkline 缓存。
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter

from src.core.market_mainline import aggregate_mainline

logger = logging.getLogger(__name__)

router = APIRouter()

# ──────────── 60s 进程内缓存(per spec) ────────────
# key 固定为 "mainline:top20", 共享一份 Top20 排名(全市场视角)。
# 拉涨停池耗 5-15s, 60s 缓存压住前端 30s 轮询的并发翻页成本。
_CACHE_TTL_S = 60.0
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _fetch_mainline() -> dict:
    """拉涨停池 + 聚合。失败容错: 返回 aggregate_mainline([]) 空结构 + note。"""
    try:
        from src.collectors.market_sentiment_collector import MarketSentimentCollector

        collector = MarketSentimentCollector()
        pool = collector.get_limit_up_pool()
    except Exception as e:
        logger.warning("market_mainline: 涨停池拉取失败(%s), 返回空数据", e)
        pool = []

    result = aggregate_mainline(pool)
    # 增加一个 cache_ts(给前端读"最近一次拉取时间")
    result = dict(result)
    result["cache_ts"] = time.time()
    return result


@router.get("/mainline")
def get_market_mainline() -> dict:
    """市场主线 Top20 排名 + 成分股列表。

    返回结构见 src.core.market_mainline.aggregate_mainline 文档:
      {
        "total_groups", "ranked_groups": [...], "unranked": [...],
        "filter_stats": {broad_filtered, below_min, ranked},
        "note", "cache_ts"
      }

    缓存: 进程内 60s TTL。clear_market_mainline_cache() 可强制刷新(运维用)。
    """
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get("mainline:top20")
        if hit is not None and now - hit[0] < _CACHE_TTL_S:
            return hit[1]

    payload = _fetch_mainline()
    with _cache_lock:
        _cache["mainline:top20"] = (now, payload)
    return payload


def clear_market_mainline_cache() -> None:
    """清空进程内 60s 缓存(运维/测试用)。

    不暴露 HTTP 路由(其他业务无强需求);测试可在 conftest autouse 里调。
    """
    with _cache_lock:
        _cache.clear()
