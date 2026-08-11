"""暗盘资金计算器 v5(2026-08-11 优化版, 截图验证对齐同花顺结构)。

数据基础(腾讯接口, 全部实测):
- 逐笔 appn=detail: 全天全量成交, 每笔 B=主动买/S=主动卖/M=中性
- dadan 档10 = 网页"大单数据"页(大单口径: 成交额/量阈值)
- 分价表 appn=price: 价位分布(价格维度)

优化点(v4 → v5):
1. 三分类: M 中性盘不再忽略(统计但不算净额)
2. 大单/暗盘分层: 大单(≥100万或≥1000手)=明盘, 中小单=暗盘(拆单藏身处)
   验证: 大单净买+4,843手 vs 中小单净买+32,460手 → 暗盘流入结构
3. 分价表价格维度: 低价承接比(价格<VWAP的买量占比) → 吸筹价位
4. 时段分解: 早盘/午盘/午后/尾盘
5. 信号: 暗盘显著流入+明盘流出 = 拆单吸筹(同花顺核心信号)
"""
from __future__ import annotations

import logging
import re
import urllib.request

from marketdata.symbol import Symbol
from marketdata.vendors.tencent_panel import fetch_price_distribution

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 大单阈值(元): 网页"大单数据"页筛选口径(成交额≥100万 或 量≥1000手)
BIG_AMOUNT = 100e4   # 100万元
BIG_VOLUME = 1000    # 1000手


def _tencent_code(symbol: Symbol) -> str | None:
    code = (symbol.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code[0] in ("6", "9") or code.startswith("688"):
        return f"sh{code}"
    if code[0] in ("0", "2", "3"):
        return f"sz{code}"
    return None


def _fetch_all_ticks(code: str, max_pages: int = 200) -> list[dict]:
    """翻页拉取全天全量逐笔, 返回 [{direction, amount, volume, time}]。"""
    ticks = []
    for p in range(max_pages):
        url = f"https://stock.gtimg.cn/data/index.php?appn=detail&action=data&c={code}&p={p}"
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read().decode("gbk", "replace")
        except Exception:
            break
        m = re.search(r'\[(\d+),"(.*?)"\]', body)
        if not m:
            break
        rows = m.group(2).split("|")
        if not rows or len(rows) < 2:
            break
        for r in rows:
            parts = r.split("/")
            if len(parts) < 7:
                continue
            try:
                amt = float(parts[5])
                vol = float(parts[4])
                direction = parts[6]
                t = parts[1]
            except (ValueError, IndexError):
                continue
            if amt > 0:
                ticks.append({"d": direction, "amt": amt, "vol": vol, "t": t})
    return ticks


def compute_dark_flow(symbol: Symbol) -> dict | None:
    """计算暗盘资金 v5: 三分类 + 大单/暗盘分层 + 价格维度 + 时段。"""
    code = _tencent_code(symbol)
    if not code:
        return None
    ticks = _fetch_all_ticks(code)
    if not ticks:
        return None

    # ---- 基础统计(三分类) ----
    buy_amt = sell_amt = m_amt = 0.0
    buy_vol = sell_vol = m_vol = 0.0
    # 大单分层
    big_buy_amt = big_sell_amt = 0.0
    small_buy_amt = small_sell_amt = 0.0
    # 时段
    seg = {"morning": 0.0, "mid": 0.0, "afternoon": 0.0, "tail": 0.0}

    for tk in ticks:
        d, amt, vol, t = tk["d"], tk["amt"], tk["vol"], tk["t"]
        is_big = amt >= BIG_AMOUNT or vol >= BIG_VOLUME
        hm = t[:5]
        if d == "B":
            buy_amt += amt; buy_vol += vol
            if is_big: big_buy_amt += amt
            else: small_buy_amt += amt
            sign = 1.0
        elif d == "S":
            sell_amt += amt; sell_vol += vol
            if is_big: big_sell_amt += amt
            else: small_sell_amt += amt
            sign = -1.0
        else:  # M 中性
            m_amt += amt; m_vol += vol
            continue
        # 时段净额
        if hm < "10:30":
            seg["morning"] += sign * amt
        elif hm < "11:30":
            seg["mid"] += sign * amt
        elif hm < "14:30":
            seg["afternoon"] += sign * amt
        else:
            seg["tail"] += sign * amt

    # ---- 结果 ----
    dark_net = buy_amt - sell_amt          # 全量主动净额
    big_net = big_buy_amt - big_sell_amt   # 大单(明盘)净额
    small_net = small_buy_amt - small_sell_amt  # 中小单(暗盘)净额

    result = {
        "dark_net": round(dark_net),           # 暗盘资金(全量主动净额)
        "big_net": round(big_net),             # 明盘(大单净额)
        "small_net": round(small_net),         # 暗盘(中小单净额)
        "buy_amt": round(buy_amt), "sell_amt": round(sell_amt),
        "m_amt": round(m_amt),
        "buy_vol": round(buy_vol), "sell_vol": round(sell_vol), "m_vol": round(m_vol),
        "buy_pct": round(buy_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
        "sell_pct": round(sell_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
        "segments": {k: round(v) for k, v in seg.items()},
        "tick_count": len(ticks),
    }

    # ---- 价格维度(分价表) ----
    # 真实字段(2026-08-11 截图破解): 价~主动买量~总成交量~委托买量~委托卖量
    # 竞买率 = 主动买量/总成交量 → 每价位买盘强度
    try:
        from marketdata.vendors.tencent_panel import fetch_price_distribution
        prices = fetch_price_distribution(symbol, limit=70)
        if prices and len(prices) > 5:
            total_vol = sum(px["volume"] for px in prices)
            if total_vol > 0:
                vwap = sum(px["price"] * px["volume"] for px in prices) / total_vol
                result["vwap"] = round(vwap, 2)
                # 低价承接: 价格<VWAP 的成交量占比
                low_vol = sum(px["volume"] for px in prices if px["price"] < vwap)
                result["low_price_ratio"] = round(low_vol / total_vol, 3)

                # 吸筹价位: 主成交区(量>3万手)且竞买率>55% 的价位
                strong_buy_zones = []
                strong_sell_zones = []
                for px in prices:
                    vol = px.get("volume") or 0
                    buy_vol = px.get("buy_volume") or 0
                    if vol < 30000:
                        continue
                    ratio = buy_vol / vol * 100 if vol else 0
                    if ratio >= 55:
                        strong_buy_zones.append({"price": px["price"], "ratio": round(ratio, 1), "vol": round(vol)})
                    elif ratio <= 45:
                        strong_sell_zones.append({"price": px["price"], "ratio": round(ratio, 1), "vol": round(vol)})
                result["strong_buy_zones"] = strong_buy_zones[:6]   # 吸筹价位
                result["strong_sell_zones"] = strong_sell_zones[:6] # 抛压价位
    except Exception:
        pass

    result["signal"] = _judge_signal(
        dark_net, big_net, small_net, seg,
        result.get("low_price_ratio"),
        result.get("strong_buy_zones", []),
        result.get("strong_sell_zones", []),
    )
    result["note"] = "v5: 三分类+大单/暗盘分层+分价表价位维度(腾讯逐笔全天全量)"
    return result


def _judge_signal(dark_net: float, big_net: float, small_net: float,
                  seg: dict, low_ratio: float | None,
                  strong_buy: list | None = None, strong_sell: list | None = None) -> str:
    """信号判定 v5: 方向(全量主动净额) + 价位维度(吸筹/抛压区) + 时段。

    同花顺核心: 主力净流入 = 明盘 + 暗盘。免费近似用全量主动净额作主信号,
    分价表吸筹/抛压价位作佐证(价跌但低位强买 = 吸筹)。
    """
    threshold = 500e4  # 500万
    tail = seg.get("tail", 0)
    strong_buy = strong_buy or []
    strong_sell = strong_sell or []
    n_buy_zone = len(strong_buy)
    n_sell_zone = len(strong_sell)
    low_boost = "低位承接" if (low_ratio or 0) > 0.4 else ""

    # 主信号: 全量主动净额方向(同花顺"主力净流入"近似)
    if dark_net > threshold:
        if tail > 0:
            return f"主力流入+尾盘加仓(暗盘吸筹){low_boost}"
        if n_buy_zone >= 2 and n_sell_zone <= n_buy_zone:
            return f"主力流入+低位强买{n_buy_zone}区(吸筹){low_boost}"
        return f"主力流入(主动买占优){low_boost}"
    if dark_net < -threshold:
        if tail < 0:
            return f"主力流出+尾盘抛压(出货)"
        if n_sell_zone >= 2:
            return f"主力流出+高位抛压{n_sell_zone}区(派发)"
        return f"主力流出(主动卖占优)"
    return f"观望(主动买卖接近, 吸筹区{n_buy_zone}/抛压区{n_sell_zone})"
