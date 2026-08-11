"""盘口强度指标 v3(2026-08-11): 腾讯实时数据的组合近似。

背景: 同花顺"暗盘资金"是 L2 委托级专有指标(识别主力拆单), 腾讯免费接口
(Level-1 成交统计)无数学等价公式。本模块用腾讯实时数据组合一个
"盘口强度"指标, 从多维度逼近主力意图:

维度:
① 主动买卖净额  : 行情外盘/内盘(全量主动买/卖) → 主动方向
② 大单主动净额  : 逐笔明细 B-S(大单口径) → 大单方向
③ 主力分时趋势  : hsfundtab todayFundTrend(分钟累计) → 时段特征(尾盘偷袭)
④ 低价承接     : 分价表价格<均价成交量占比 → 低位吸筹

信号综合(盘口强度):
  主动买优 + 大单买优 + 尾盘流入 + 低价承接高 → 强吸筹特征
  各维度可交叉验证, 避免单一维度误导。

注意: 这是"盘口强度", 不是同花顺"暗盘资金"。暗盘需要 L2 委托数据。
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime

from marketdata.symbol import Symbol
from marketdata.vendors.tencent import TencentQuoteVendor
from marketdata.vendors.tencent_panel import fetch_big_order_stats, fetch_price_distribution

logger = logging.getLogger(__name__)

_FUNDFLOW_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 大单阈值(元)
BIG_ORDER_THRESHOLD = 20e4


def _tencent_code(symbol: Symbol) -> str | None:
    code = (symbol.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code[0] in ("6", "9") or code.startswith("688"):
        return f"sh{code}"
    if code[0] in ("0", "2", "3"):
        return f"sz{code}"
    return None


def _get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        logger.warning(f"[盘口强度] {url[:60]} 失败: {e}")
        return None


def compute_board_strength(symbol: Symbol) -> dict | None:
    """计算盘口强度(四维度组合)。"""
    code = _tencent_code(symbol)
    if not code:
        return None

    result: dict = {"symbol": symbol.code, "dimensions": {}}

    # ---- ① 主动买卖净额(行情外盘/内盘) ----
    try:
        qs = TencentQuoteVendor().fetch([symbol], {})
        if qs:
            q = qs[0].__dict__
            outer = float(q.get("volume_outer") or 0)  # 外盘(主动买)
            inner = float(q.get("volume_inner") or 0)  # 内盘(主动卖)
            price = float(q.get("current_price") or 0)
            # 若 Quote 未解析外盘/内盘, 从原始行取
            if outer == 0 and inner == 0:
                raw = _fetch_raw_quote(code)
                if raw:
                    outer = raw.get("outer") or 0
                    inner = raw.get("inner") or 0
                    price = raw.get("price") or price
            net_amt = (outer - inner) * 100 * price
            result["dimensions"]["active_net"] = {
                "outer": outer, "inner": inner,
                "net_amount": round(net_amt),
                "direction": "主动买优" if net_amt > 0 else ("主动卖优" if net_amt < 0 else "均衡"),
            }
    except Exception as e:
        logger.debug(f"[盘口强度] 主动买卖失败: {e}")

    # ---- ② 大单主动净额(逐笔 B-S) ----
    try:
        buy_amt, sell_amt = _fetch_tick_bs(code)
        big_net = buy_amt - sell_amt
        result["dimensions"]["big_order_net"] = {
            "buy": round(buy_amt), "sell": round(sell_amt),
            "net_amount": round(big_net),
            "direction": "大单买优" if big_net > 0 else ("大单卖优" if big_net < 0 else "均衡"),
        }
    except Exception as e:
        logger.debug(f"[盘口强度] 大单主动失败: {e}")

    # ---- ③ 主力分时趋势(时段特征) ----
    try:
        seg = _fetch_trend_segments(code)
        if seg:
            result["dimensions"]["trend"] = seg
    except Exception as e:
        logger.debug(f"[盘口强度] 分时趋势失败: {e}")

    # ---- ④ 低价承接(分价表) ----
    try:
        prices = fetch_price_distribution(symbol, limit=70)
        if prices and len(prices) > 3:
            total_vol = sum(p["volume"] for p in prices)
            vwap = sum(p["price"] * p["volume"] for p in prices) / total_vol if total_vol else 0
            low_vol = sum(p["volume"] for p in prices if p["price"] < vwap)
            low_ratio = low_vol / total_vol if total_vol else 0
            result["dimensions"]["low_price"] = {
                "vwap": round(vwap, 2),
                "low_ratio": round(low_ratio, 3),
                "note": "价格<均价成交量占比",
            }
    except Exception as e:
        logger.debug(f"[盘口强度] 分价表失败: {e}")

    # ---- 综合信号 ----
    result["signal"] = _composite_signal(result["dimensions"])
    return result


def _fetch_raw_quote(code: str) -> dict | None:
    """从腾讯行情原始行取外盘/内盘(parts[7]/[8])。"""
    try:
        url = f"https://qt.gtimg.cn/q={code}"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("gbk", "replace")
        parts = body.split("~")
        if len(parts) > 9:
            return {
                "outer": float(parts[7]),
                "inner": float(parts[8]),
                "price": float(parts[3]),
            }
    except Exception:
        pass
    return None


def _fetch_tick_bs(code: str, max_pages: int = 70) -> tuple[float, float]:
    """逐笔大单主动买/卖金额合计(B-S)。"""
    buy = sell = 0.0
    for p in range(max_pages):
        url = f"https://stock.gtimg.cn/data/index.php?appn=detail&action=data&c={code}&p={p}"
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read().decode("gbk", "replace")
            m = re.search(r'\[(\d+),"(.*?)"\]', body)
            if not m:
                break
            for r in m.group(2).split("|"):
                parts = r.split("/")
                if len(parts) < 7:
                    continue
                try:
                    amt = float(parts[5])
                    direction = parts[6]
                except (ValueError, IndexError):
                    continue
                if amt < 5e4:  # 过滤碎单
                    continue
                if direction == "B":
                    buy += amt
                elif direction == "S":
                    sell += amt
        except Exception:
            break
    return buy, sell


def _fetch_trend_segments(code: str) -> dict | None:
    """分时趋势: 全时段 + 尾盘特征(差分分钟累计值)。"""
    j = _get_json(f"{_FUNDFLOW_URL}?code={code}&type=todayFundTrend&klineNeedDay=20")
    if not j or j.get("code") != 0:
        return None
    ml = (j.get("data") or {}).get("todayFundTrend", {}).get("minList", [])
    if not ml:
        return None

    def _net(m):
        return float(m.get("MainNetInflow") or 0)

    last = _net(ml[-1])
    # 尾盘特征: 最后30分钟增量(14:30-15:00)
    tail_start = None
    for m in ml:
        t = m.get("time", "")
        if t[8:12] >= "1430" and tail_start is None:
            tail_start = _net(m)
    tail_delta = last - tail_start if tail_start is not None else None

    # 早盘(9:30-10:30)增量
    morning_start = None
    for m in ml:
        t = m.get("time", "")
        if t[8:12] >= "1030" and morning_start is None:
            morning_start = _net(m)
    morning_delta = morning_start if morning_start is not None else None

    seg = {
        "main_net_total": round(last),
        "morning_net": round(morning_delta or 0),   # 早盘累计
        "tail_delta": round(tail_delta) if tail_delta is not None else None,  # 尾盘增量
        "points": len(ml),
        "direction": "尾盘流入" if (tail_delta or 0) > 0 else ("尾盘流出" if (tail_delta or 0) < 0 else "尾盘平稳"),
    }
    return seg


def _composite_signal(dims: dict) -> str:
    """综合信号: 多维度交叉。"""
    active = dims.get("active_net", {}).get("direction", "")
    big = dims.get("big_order_net", {}).get("direction", "")
    trend = dims.get("trend", {}).get("direction", "")
    low = dims.get("low_price", {}).get("low_ratio", 0)

    score = 0
    if "买" in active:
        score += 1
    elif "卖" in active:
        score -= 1
    if "买" in big:
        score += 1
    elif "卖" in big:
        score -= 1
    if "流入" in trend:
        score += 1
    elif "流出" in trend:
        score -= 1
    if low and low > 0.4:  # 低价承接>40% = 低位吸筹特征
        score += 1

    if score >= 3:
        return "强吸筹特征(主动买+大单买+尾盘流入+低价承接)"
    if score >= 2:
        return "偏多(主动买占优)"
    if score <= -3:
        return "强出货特征(主动卖+大单卖+尾盘流出)"
    if score <= -2:
        return "偏空(主动卖占优)"
    return "中性(信号分歧)"
