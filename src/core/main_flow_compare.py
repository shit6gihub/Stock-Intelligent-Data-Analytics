"""主力意图双源对比(腾讯逐笔 vs thsdk L2) v0.3.0。

compare_main_flow(symbol): 同时拉取两路主力净额并比对一致性:
  - tencent: src.core.dark_flow.compute_dark_flow(腾讯逐笔口径)
    - 主力净额 main_net(元, ≥20万, 剔除竞价), 超大单 big_net(元, ≥100万)
  - thsdk : data_source.thsdk_l2.compute_main_flow(同花顺 L2 口径)
    - 主买主卖净额 net_wan(万元, 全量主动买-主动卖), 大单净额 big_net_wan(万元)

一致性 consistency(0-100) = 1 - |tencent - thsdk| / max(|tencent|,|thsdk|,1), 再 ×100 截断 [0,100]。
delta_pct = |tencent - thsdk| / max(|tencent|,|thsdk|,1) × 100(发散幅度%, 与一致性互补, 恒 = 100 - consistency)。

- 30s 进程内缓存(与 dark_flow._fetch_all_ticks 同思路, 避免每轮监控重复拉取)。
- 异常容错: thsdk 失败只返回 tencent + note="thsdk 数据暂不可用..."; 两路都失败返回空结果。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# 30s 进程内缓存: {symbol -> (ts, result)}。盘中每轮监控窗口内命中, 避免重复翻页/拉取。
_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, dict]] = {}


def clear_cache() -> None:
    """清空进程内缓存(测试 / 运维手动刷新用)。"""
    _cache.clear()


def _to_thsdk_symbol(code: str) -> str | None:
    """6位A股代码 -> thsdk 代码(USZA 深A / USHA 沪A / USTM 北交所)。

    与 src.web.api.auction._normalize_symbol 口径一致, 本地独立实现避免循环依赖。
    """
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("60", "68")):
        return f"USHA{code}"
    if code.startswith(("00", "30")):
        return f"USZA{code}"
    if code.startswith(("8", "4", "92")):
        return f"USTM{code}"
    return None


def _extract_tencent(dark: dict | None) -> dict | None:
    """从 compute_dark_flow 结果提取可比字段(单位为元)。数据不足返回 None。"""
    if not dark:
        return None
    data_status = dark.get("data_status")
    if data_status == "insufficient":
        return {
            "available": False,
            "data_status": data_status,
            "note": "腾讯逐笔数据不足(<30笔非竞价成交), 主力意图参考性低",
        }
    return {
        "available": True,
        "main_net": dark.get("main_net"),      # 元
        "big_net": dark.get("big_net"),        # 元(超大单 ≥100万)
        "mid_net": dark.get("mid_net"),        # 元(大单 20-100万)
        "retail_net": dark.get("small_net", dark.get("retail_net")),  # 元(散户)
        "signal": dark.get("signal"),
        "tick_count": dark.get("tick_count"),
        "data_status": data_status,
    }


def _extract_thsdk(flow: dict | None) -> dict | None:
    """从 compute_main_flow 结果提取可比字段(净额统一换算为元)。"""
    if not flow or flow.get("error") == "no_data" or flow.get("net_wan") is None:
        return None
    net_wan = flow.get("net_wan", 0.0) or 0.0
    big_net_wan = flow.get("big_net_wan", 0.0) or 0.0
    return {
        "available": True,
        "main_net": round(net_wan * 10000.0),                    # 元
        "big_net": round(big_net_wan * 10000.0),                 # 元(≥100万)
        "main_net_wan": round(net_wan, 2),                       # 万元(原口径)
        "big_net_wan": round(big_net_wan, 2),                    # 万元(原口径)
        "main_buy_wan": flow.get("main_buy_wan"),
        "main_sell_wan": flow.get("main_sell_wan"),
        "big_buy_wan": flow.get("big_buy_wan"),
        "big_sell_wan": flow.get("big_sell_wan"),
        "total_ticks": flow.get("total_ticks"),
        "valid_ticks": flow.get("valid_ticks"),
    }


def _consistency(tencent_main: float, thsdk_main: float) -> tuple[float, float]:
    """计算一致性(0-100)与发散幅度 delta_pct(%), 单位需一致(这里均为元)。

    两者都接近 0 时视为一致(均无主力动作), consistency=100, delta_pct=0。
    """
    denom = max(abs(tencent_main), abs(thsdk_main), 1.0)
    diff = abs(tencent_main - thsdk_main)
    delta_pct = diff / denom * 100.0
    consistency = max(0.0, min(100.0, 100.0 - delta_pct))
    return round(consistency, 1), round(delta_pct, 1)


def _tencent_symbol(code: str):
    """构建腾讯口径所需的 marketdata Symbol(懒加载, 避免导入环)。"""
    from marketdata import Symbol as MDSymbol

    return MDSymbol.parse(code, "CN")


def compare_main_flow(symbol: str) -> dict:
    """主力意图双源对比。symbol 为 6 位 A 股代码(如 002361)。

    返回:
      {
        "symbol": code,
        "tencent": dict|None,   # 腾讯逐笔口径(元)
        "thsdk":   dict|None,   # 同花顺 L2 口径(元), thsdk 失败时为 None
        "consistency": float|None,  # 0-100
        "delta_pct":   float|None,  # 发散幅度%
        "note": str,
      }
    """
    code = (symbol or "").strip()
    now = time.time()
    cached = _cache.get(code)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    tencent = None
    thsdk = None
    note = ""

    # 腾讯逐笔(暗盘主链路)
    try:
        from src.core.dark_flow import compute_dark_flow

        dark = compute_dark_flow(_tencent_symbol(code))
        tencent = _extract_tencent(dark)
    except Exception as e:  # noqa: BLE001 - 数据源异常统一降级, 不崩
        logger.warning("[main_flow] 腾讯逐笔对比失败 %s: %r", code, e)
        tencent = None
        note = f"tencent 数据暂不可用: {e!r}"

    # thsdk L2(同花顺口径)
    tsym = _to_thsdk_symbol(code)
    if tsym:
        try:
            from data_source.thsdk_l2 import compute_main_flow

            flow = compute_main_flow(tsym)
            thsdk = _extract_thsdk(flow)
        except Exception as e:  # noqa: BLE001
            logger.warning("[main_flow] thsdk 对比失败 %s(%s): %r", code, tsym, e)
            thsdk = None
    else:
        thsdk = None

    consistency = None
    delta_pct = None
    if tencent and tencent.get("available") and thsdk and thsdk.get("available"):
        consistency, delta_pct = _consistency(
            float(tencent["main_net"] or 0.0), float(thsdk["main_net"] or 0.0)
        )
        note = "双源一致性比对(腾讯逐笔 vs thsdk L2 主力净额)"
    elif not thsdk:
        note = ("thsdk 数据暂不可用" if not note else f"{note}; thsdk 数据暂不可用")
    elif not tencent or not tencent.get("available"):
        note = note or "tencent 数据暂不可用"

    result = {
        "symbol": code,
        "tencent": tencent,
        "thsdk": thsdk,
        "consistency": consistency,
        "delta_pct": delta_pct,
        "note": note,
    }
    _cache[code] = (time.time(), result)
    return result
