"""竞价异动池 API(v0.3.0)。

GET  /api/auction/anomaly?market=CN          → 最新竞价异动池(fetch_auction_anomaly)
GET  /api/auction/anomaly/{symbol}/history?days=5 → 某股近 N 天竞价异动历史(DB)
POST /api/auction/sync                        → 触发热拉 + 落库(内部用, cron 也用)

⚠️ 模块名用 auction_pool 而非 auction: src/web/api/auction.py 已被并行子任务占用
(竞价快照 /api/auction-snapshot), 避免撞名。本模块路由注册在 /api/auction 前缀下。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from src.core.auction_pool import (
    fetch_auction_anomaly,
    get_anomaly_history,
    sync_auction_to_db,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _validate_symbol(raw: str) -> str:
    code = (raw or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, f"非法股票代码: {raw!r}(需要6位A股代码)")
    return code


@router.get("/anomaly")
def anomaly(market: str = Query("CN", description="CN/SH/SZ/BJ/ALL 或 thsdk 代码(USHA等)")):
    """最新竞价异动池。数据源不可用时 available=false 且如实说明, 不伪造。"""
    records = fetch_auction_anomaly(market)
    if not records:
        return {
            "available": False,
            "count": 0,
            "records": [],
            "note": "thsdk 竞价异动数据暂不可用(数据源未接入/非交易时段/拉取失败)",
        }
    return {"available": True, "count": len(records), "records": records, "note": ""}


@router.get("/anomaly/{symbol}/history")
def history(
    symbol: str,
    days: int = Query(5, ge=1, le=90, description="查询近 N 天(默认5)"),
):
    """某股近 N 天竞价异动历史(DB 落库追踪)。"""
    code = _validate_symbol(symbol)
    rows = get_anomaly_history(code, days=days)
    return {"symbol": code, "days": days, "count": len(rows), "records": rows}


@router.post("/sync")
def sync(market: str = Query("CN", description="同步的市场(默认 CN 沪深沪A)")):
    """触发热拉竞价异动并落库(内部用, 由工作日 09:25 cron 与手动运维触发)。"""
    try:
        records = fetch_auction_anomaly(market)
        n = sync_auction_to_db(records)
        return {"synced": n, "fetched": len(records), "market": market}
    except Exception as e:  # noqa: BLE001
        logger.error("[auction-sync] 竞价异动同步失败: %r", e)
        raise HTTPException(502, f"竞价异动同步失败: {e!r}")
