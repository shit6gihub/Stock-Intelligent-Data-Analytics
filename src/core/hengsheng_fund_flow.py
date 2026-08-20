"""恒生个股资金流向封装 v0.4.0。

get_hs_fund_flow(symbol, days=10): 调恒生 AStockCashFlow(官方 DDE 口径)
+ RealStockFundFlow(实时口径), 返回对齐同花顺口径的结构:
  - main_net: 元 (主力口径, 按文档取 totalvalue)
  - big_net_dde: 元 (largenetbuyvaluedde, 大单净额 DDE)
  - big_net_dde_ratio: % (largenetbuyvaluedderatio, 资金比)
  - rising_up_days: 天 (risingupdays, 连红天数)

- 30s 进程内缓存(同 main_flow_compare / dark_flow 思路)。
- 容错: 恒生接口异常 -> available=False + note, 不 panic。
"""
from __future__ import annotations

import datetime
import logging
import time

from src.core.hengsheng_client import HengshengUnavailableError, get_default_client

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, dict]] = {}


def clear_cache() -> None:
    """清空进程内缓存(测试/运维用)。"""
    _cache.clear()


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    f = _num(v)
    return None if f is None else int(round(f))


def _to_hs_object(code: str) -> str | None:
    """6 位代码 -> 恒生聚源对象(如 002361.SZ / 600519.SH)。"""
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    if code.startswith(("00", "30", "2")):
        return f"{code}.SZ"
    return None


def _extract_rows(api_id: str, data) -> list[dict]:
    """把 call_api 返回尽力归一化为 list[dict](交易日行)。"""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("data", "result", "rows", "list", "hisData", "records"):
            val = data.get(key)
            if isinstance(val, list):
                rows = [d for d in val if isinstance(d, dict)]
                if rows:
                    return rows
        if "tradingday" in data or "totalvalue" in data:
            return [data]
    return []


def get_hs_fund_flow(symbol: str, days: int = 10) -> dict:
    """恒生个股资金流向。

    :param symbol: 6 位 A 股代码(如 002361)
    :param days: 拉取最近 N 个交易日
    :return: {available, stockObject, days, latest_dde_ratio,
              latest_rising_up_days, source, note}
             异常时 available=False + note。
    """
    code = (symbol or "").strip()
    cached = _cache.get(code)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    hs_object = _to_hs_object(code)
    if not hs_object:
        result = {
            "available": False, "stockObject": None, "days": [],
            "latest_dde_ratio": None, "latest_rising_up_days": None,
            "source": "hengsheng",
            "note": f"非法 A 股代码: {symbol!r}",
        }
        _cache[code] = (now, result)
        return result

    today = datetime.date.today()
    end = today.strftime("%Y%m%d")
    begin = (today - datetime.timedelta(days=int(days) * 2)).strftime("%Y%m%d")

    days_rows: list[dict] = []
    note = None
    try:
        client = get_default_client()
        data = client.call_api(
            "AStockCashFlow",
            params={"stockObject": [hs_object], "beginDate": begin, "endDate": end},
        )
        raw = _extract_rows("AStockCashFlow", data)
        if not raw:
            note = "恒生 AStockCashFlow 无数据"
        for r in raw:
            days_rows.append({
                "date": str(r.get("tradingday") or ""),
                "main_net": _num(r.get("totalvalue")),
                "big_net_dde": _num(r.get("largenetbuyvaluedde")),
                "big_net_dde_ratio": _num(r.get("largenetbuyvaluedderatio")),
                "rising_up_days": _int(r.get("risingupdays")),
                "super_large_net": _num(r.get("hugenetbuyvalue")),
                "large_net": _num(r.get("largenetbuyvalue")),
                "medium_net": _num(r.get("mediumnetbuyvalue")),
                "small_net": _num(r.get("smallnetbuyvalue")),
                "change_pct": _num(r.get("changepct")),
                "close": _num(r.get("closeprice")),
            })
    except HengshengUnavailableError as e:
        return {
            "available": False, "stockObject": hs_object, "days": [],
            "latest_dde_ratio": None, "latest_rising_up_days": None,
            "source": "hengsheng", "note": f"恒生数据暂不可用: {e!r}",
        }
    except Exception as e:  # noqa: BLE001 - 数据源异常统一降级
        logger.warning("[hengsheng_fund_flow] %s 拉取异常: %r", code, e)
        return {
            "available": False, "stockObject": hs_object, "days": [],
            "latest_dde_ratio": None, "latest_rising_up_days": None,
            "source": "hengsheng", "note": f"恒生数据暂不可用: {e!r}",
        }

    # 实时资金流(RealStockFundFlow)尽力补充, 失败不影响主链路
    try:
        client = get_default_client()
        real = client.call_api(
            "RealStockFundFlow", params={"stockObject": [hs_object]},
        )
        _ = real  # 预留: 未来可合并实时主力净额到 latest
    except Exception:  # noqa: BLE001 - 实时口径非必需
        pass

    if not note:
        days_rows = [d for d in days_rows if d.get("date")]
        days_rows.sort(key=lambda d: d["date"])
    else:
        days_rows = []

    latest_ratio = None
    latest_up = None
    if days_rows:
        latest = days_rows[-1]
        latest_ratio = latest.get("big_net_dde_ratio")
        latest_up = latest.get("rising_up_days")

    result = {
        "available": True if (days_rows and note is None) else False,
        "stockObject": hs_object,
        "days": days_rows[-int(days):] if days_rows else [],
        "latest_dde_ratio": latest_ratio,
        "latest_rising_up_days": latest_up,
        "source": "hengsheng",
        "note": note,
    }
    _cache[code] = (now, result)
    return result
