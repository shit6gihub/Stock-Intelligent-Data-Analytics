"""市场数据代理 API: 把 marketdata 包的关键方法暴露成只读 HTTP 端点。

设计原则(与 calendar.py 一致):
- 直接调用 marketdata 包的 vendor/Engine, **不重写数据源逻辑**
- config=None → vendor 自动从容器 DB 的 data_sources 表读 UI 维护的 key
  (即「设置 → 接口Key」配置的凭证, 改了立即生效, 无需重启)
- 供 8010 预测引擎在宿主机调用(宿主机无 marketdata 包, 经 8000 HTTP 取数)

暴露:
- GET /api/market-data/dragon-tiger/{date}  龙虎榜(ftshare vendor)
- GET /api/market-data/capital-flow/{symbol}  资金流(经 MarketData Engine, 走 UI 配置 vendor)
- GET /api/market-data/fundamentals-detail/{symbol}  个股基本面明细合并端点(龙虎榜/两融/股东户数/分红/事件日历)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/dragon-tiger/{trade_date}")
async def dragon_tiger_proxy(
    trade_date: str,
    market: str = Query("CN", description="市场"),
):
    """龙虎榜(经 marketdata dragon_tiger vendor, 实际用 ftshare)。

    trade_date: YYYYMMDD
    key 来自「设置→接口Key」配置的 data_sources(type=dragon_tiger), 实时生效。
    """
    try:
        from src.core.marketdata_client import get_market_data
        md = get_market_data()
        rows = md.dragon_tiger(date=trade_date, market=market)
        return {
            "trade_date": trade_date,
            "market": market,
            "count": len(rows) if rows else 0,
            "items": [
                {
                    "trade_date": getattr(i, "trade_date", trade_date),
                    "symbol": getattr(i, "symbol", ""),
                    "name": getattr(i, "name", ""),
                    "close": getattr(i, "close", None),
                    "change_pct": getattr(i, "change_pct", None),
                    "net_buy": getattr(i, "net_buy", None),
                    "buy_amt": getattr(i, "buy_amt", None),
                    "sell_amt": getattr(i, "sell_amt", None),
                    "reason": getattr(i, "reason", None),
                }
                for i in (rows or [])
            ],
        }
    except Exception as e:
        logger.warning(f"龙虎榜代理失败 [{trade_date}]: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")


@router.get("/capital-flow/{symbol}")
async def capital_flow_proxy(
    symbol: str,
    market: str = Query("CN", description="市场"),
):
    """资金流(经 MarketData Engine, 走 UI 配置的 capital_flow vendor, 默认 sina/eastmoney)。

    key 来自「设置→接口Key」配置的 data_sources(type=capital_flow), 实时生效。
    """
    try:
        from src.core.marketdata_client import get_market_data
        md = get_market_data()
        cf = md.capital_flow(symbol, market=market)
        if cf is None:
            return {"symbol": symbol, "market": market, "error": "no_data"}
        return {
            "symbol": symbol,
            "market": market,
            "main_net_inflow": cf.main_net_inflow,
            "main_net_inflow_pct": cf.main_net_inflow_pct,
            "super_net_inflow": cf.super_net_inflow,
            "big_net_inflow": cf.big_net_inflow,
            "mid_net_inflow": cf.mid_net_inflow,
            "small_net_inflow": cf.small_net_inflow,
            "main_net_5d": cf.main_net_5d,
        }
    except Exception as e:
        logger.warning(f"资金流代理失败 [{symbol}]: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")


@router.get("/board-capital-flow")
async def board_capital_flow_proxy(
    board_type: str = Query("industry", description="industry 行业 / concept 概念"),
):
    """板块资金流向(同花顺行业/概念资金,免登录免费源)。

    返回按净额降序的板块资金列表(流入/流出/净额,单位亿)。
    """
    try:
        from src.core.marketdata_client import get_market_data
        md = get_market_data()
        boards = md.board_capital_flow(board_type=board_type)
        return {
            "board_type": board_type,
            "count": len(boards),
            "items": [
                {
                    "board_name": b.board_name,
                    "board_type": b.board_type,
                    "index_value": b.index_value,
                    "change_pct": b.change_pct,
                    "inflow": b.inflow,
                    "outflow": b.outflow,
                    "net_inflow": b.net_inflow,
                    "stock_count": b.stock_count,
                    "leader_name": b.leader_name,
                    "leader_change_pct": b.leader_change_pct,
                    "leader_price": b.leader_price,
                    "rank": b.rank,
                }
                for b in boards
            ],
        }
    except Exception as e:
        logger.warning(f"板块资金代理失败: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")


@router.get("/market-capital-flow")
async def market_capital_flow_proxy():
    """大盘资金(对齐同花顺APP口径: 两市主力净流入 + 总成交额 + 涨跌家数 + 板块明细)。

    2026-08-10 重构: 之前用同花顺 hyzjl 行业资金求和(总流入2611亿口径不对),
    改为国内网关东财两市主力净流入(超大单+大单汇总, 与APP一致)。
    """
    try:
        import requests as _req
        # 1. 国内网关: 两市主力净流入 + 成交额 + 涨跌家数
        ov = _req.get(
            "http://115.190.177.213:8100/cn/market-overview", timeout=6
        ).json()
        if ov.get("error"):
            return {"error": ov["error"]}
        # 2. 板块资金明细(同花顺 hyzjl 行业, 流入/流出榜)
        from src.core.marketdata_client import get_market_data
        md = get_market_data()
        boards = md.board_capital_flow(board_type="industry") or []
        boards_sorted = sorted(
            boards, key=lambda b: (b.net_inflow or 0.0), reverse=True
        )
        inflow_boards = [
            {
                "name": b.board_name,
                "net_inflow": round(b.net_inflow or 0.0, 2),  # 亿
                "change_pct": b.change_pct,
            }
            for b in boards_sorted[:10]
            if (b.net_inflow or 0.0) > 0
        ]
        outflow_boards = [
            {
                "name": b.board_name,
                "net_inflow": round(b.net_inflow or 0.0, 2),  # 亿(负=流出)
                "change_pct": b.change_pct,
            }
            for b in reversed(boards_sorted[-10:])
            if (b.net_inflow or 0.0) < 0
        ]
        return {
            # 两市主力净流入(对齐同花顺APP)
            "total_main_flow": ov.get("total_main_flow"),      # 亿
            "sh_flow": (ov.get("sh") or {}).get("main_flow"),  # 沪市主力
            "sz_flow": (ov.get("sz") or {}).get("main_flow"),  # 深市主力
            "cyb_flow": (ov.get("cyb") or {}).get("main_flow"),
            # 市场统计(同花顺APP盘面)
            "total_amount": ov.get("total_amount"),            # 两市成交额亿
            "up_count": ov.get("up_count"),
            "down_count": ov.get("down_count"),
            "flat_count": ov.get("flat_count"),
            "sh": ov.get("sh"), "sz": ov.get("sz"), "cyb": ov.get("cyb"),
            # 板块明细
            "inflow_boards": inflow_boards,
            "outflow_boards": outflow_boards,
            "source": "eastmoney_push2delay_cn",
            "timestamp": None,
        }
    except Exception as e:
        logger.warning(f"大盘资金代理失败: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")


# ──────────────── 个股基本面明细合并(龙虎榜/两融/股东户数/分红/事件日历) ────────────────


def fetch_fundamentals_detail(symbol: str, market: str = "CN", dt_days: int = 10) -> dict:
    """个股基本面明细合并取数(纯函数, 供 HTTP 端点与对话助手共用)。

    - dragon_tiger: 市场级按日接口, 回溯最近 dt_days 个自然日并按 symbol 过滤
    - margin / shareholders / dividend / events: 按 symbol 批量接口
    每类独立容错: 单类 vendor 失败只记日志、该类别返回空数组, 不拖垮整体。
    """
    from datetime import date, timedelta

    from src.core.marketdata_client import get_market_data

    md = get_market_data()
    out: dict = {
        "symbol": symbol,
        "market": market,
        "dragon_tiger": [],
        "margin": [],
        "shareholders": [],
        "dividend": [],
        "events": [],
    }

    # 1) 龙虎榜(市场级按日, 回溯 dt_days 天按 symbol 过滤; 引擎内存缓存, 重复日期不重复抓)
    scanned = max(1, min(int(dt_days), 30))
    d = date.today()
    for _ in range(scanned):
        ds = d.strftime("%Y%m%d")
        d -= timedelta(days=1)
        try:
            rows = md.dragon_tiger(date=ds, market=market) or []
        except Exception as e:
            logger.warning(f"基本面明细-龙虎榜[{ds}]查询失败(跳过): {e}")
            continue
        for i in rows:
            if getattr(i, "symbol", "") != symbol:
                continue
            out["dragon_tiger"].append(
                {
                    "trade_date": getattr(i, "trade_date", ds),
                    "symbol": getattr(i, "symbol", symbol),
                    "name": getattr(i, "name", ""),
                    "reason": getattr(i, "reason", None),
                    "close": getattr(i, "close", None),
                    "change_pct": getattr(i, "change_pct", None),
                    "net_buy": getattr(i, "net_buy", None),
                    "buy_amt": getattr(i, "buy_amt", None),
                    "sell_amt": getattr(i, "sell_amt", None),
                    "turnover_pct": getattr(i, "turnover_pct", None),
                }
            )
    # 龙虎榜按交易日倒序(新→旧)
    out["dragon_tiger"].sort(key=lambda r: r["trade_date"] or "", reverse=True)

    # 2) 融资融券(按 symbol, 取最新快照)
    try:
        for i in md.margin([symbol], market=market) or []:
            out["margin"].append(
                {
                    "date": getattr(i, "date", ""),
                    "symbol": getattr(i, "symbol", symbol),
                    "rz_balance": getattr(i, "rz_balance", None),
                    "rz_buy": getattr(i, "rz_buy", None),
                    "rz_repay": getattr(i, "rz_repay", None),
                    "rq_balance": getattr(i, "rq_balance", None),
                    "rq_sell_vol": getattr(i, "rq_sell_vol", None),
                    "rq_repay_vol": getattr(i, "rq_repay_vol", None),
                    "total_balance": getattr(i, "total_balance", None),
                }
            )
    except Exception as e:
        logger.warning(f"基本面明细-两融[{symbol}]查询失败: {e}")

    # 3) 股东户数(按 symbol, 取最新一期)
    try:
        for i in md.shareholders([symbol], market=market) or []:
            out["shareholders"].append(
                {
                    "report_date": getattr(i, "report_date", ""),
                    "symbol": getattr(i, "symbol", symbol),
                    "holder_num": getattr(i, "holder_num", None),
                    "change_num": getattr(i, "change_num", None),
                    "change_ratio": getattr(i, "change_ratio", None),
                    "avg_shares": getattr(i, "avg_shares", None),
                }
            )
    except Exception as e:
        logger.warning(f"基本面明细-股东户数[{symbol}]查询失败: {e}")

    # 4) 分红(按 symbol, 全部历史, 按除权日倒序)
    try:
        for i in md.dividend([symbol], market=market) or []:
            out["dividend"].append(
                {
                    "ex_date": getattr(i, "ex_date", ""),
                    "symbol": getattr(i, "symbol", symbol),
                    "dividend_per_share": getattr(i, "dividend_per_share", None),
                    "transfer_ratio": getattr(i, "transfer_ratio", None),
                    "bonus_ratio": getattr(i, "bonus_ratio", None),
                    "progress": getattr(i, "progress", ""),
                }
            )
        out["dividend"].sort(key=lambda r: r["ex_date"] or "", reverse=True)
    except Exception as e:
        logger.warning(f"基本面明细-分红[{symbol}]查询失败: {e}")

    # 5) 事件日历(按 symbol, 近 since_days=7 日公告/业绩)
    try:
        for i in md.events([symbol], market=market, since_days=7) or []:
            ts = getattr(i, "publish_time", None)
            out["events"].append(
                {
                    "source": getattr(i, "source", ""),
                    "external_id": getattr(i, "external_id", ""),
                    "event_type": getattr(i, "event_type", ""),
                    "title": getattr(i, "title", ""),
                    "publish_time": ts.isoformat() if ts else None,
                    "importance": getattr(i, "importance", 0),
                    "url": getattr(i, "url", ""),
                }
            )
        out["events"].sort(
            key=lambda r: r["publish_time"] or "", reverse=True
        )
    except Exception as e:
        logger.warning(f"基本面明细-事件[{symbol}]查询失败: {e}")

    return out


@router.get("/fundamentals-detail/{symbol}")
async def fundamentals_detail_proxy(
    symbol: str,
    market: str = Query("CN", description="市场"),
    dt_days: int = Query(10, ge=1, le=30, description="龙虎榜回溯天数(自然日)"),
):
    """个股基本面明细合并端点: 龙虎榜/融资融券/股东户数/分红/事件日历。

    每类独立容错, 无数据返回空数组; 单类 vendor 失败不影响其余四类。
    key 来自「设置→接口Key」配置的 data_sources(type=dragon_tiger/margin/...), 实时生效。
    """
    try:
        return fetch_fundamentals_detail(symbol, market=market, dt_days=dt_days)
    except Exception as e:
        logger.warning(f"基本面明细代理失败 [{symbol}]: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")
