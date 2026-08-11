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
                price = float(parts[2])
                direction = parts[6]
                t = parts[1]
            except (ValueError, IndexError):
                continue
            if amt > 0:
                ticks.append({"d": direction, "amt": amt, "vol": vol, "price": price, "t": t})
    return ticks


def _detect_split_orders(ticks: list[dict], window_sec: int = 90, min_consec: int = 3,
                         lo: float = 5e4, hi: float = 100e4) -> dict:
    """拆单识别 v2: 识别主力伪装的中小单(含价格背景, 2026-08-11 修正)。

    之前只按"同向+时间+金额"识别 → 把散户割肉(跌中连续卖)误判为主力拆单卖。
    修正: 加价格背景分类(神剑实测: 跌中卖1443万全是散户割肉, 跌中买0万):
      - 逆势吸筹(拆单买): 价格下跌中连续主动买入 = 主力偷偷吸筹
      - 逆势派发(拆单卖): 价格上涨中连续主动卖出 = 主力偷偷出货
      - 顺势割肉(散户): 价格下跌中连续卖出 = 散户恐慌
      - 顺势追涨(散户): 价格上涨中连续买入 = 散户追高
    只有"逆势"两类算疑似主力, "顺势"两类是散户行为。

    Returns: {buy_amt, sell_amt, net, contrarian_net, groups}
    """
    def _t2s(t: str) -> int:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)

    # 价格背景: 组内价格方向(涨/跌/平)
    suspect_buy = suspect_sell = 0.0   # 逆势(疑似主力)
    herd_buy = herd_sell = 0.0         # 顺势(散户)
    groups = []
    seq = []
    for tk in ticks:
        if lo <= tk["amt"] <= hi:
            seq.append(tk)
        else:
            if len(seq) >= min_consec and all(x["d"] == seq[0]["d"] for x in seq):
                dur = _t2s(seq[-1]["t"]) - _t2s(seq[0]["t"])
                if dur <= window_sec:
                    direction = seq[0]["d"]
                    amt_sum = sum(x["amt"] for x in seq)
                    p0, p1 = seq[0]["price"], seq[-1]["price"]
                    price_dir = "up" if p1 > p0 else ("down" if p1 < p0 else "flat")
                    # 逆势 = 买在跌中 / 卖在涨中
                    contrarian = (direction == "B" and price_dir == "down") or \
                                 (direction == "S" and price_dir == "up")
                    groups.append({
                        "d": direction, "n": len(seq), "amt": round(amt_sum),
                        "t0": seq[0]["t"], "t1": seq[-1]["t"],
                        "p0": p0, "p1": p1,
                        "contrarian": contrarian,  # 疑似主力
                        "price_dir": price_dir,
                    })
                    if contrarian:
                        if direction == "B":
                            suspect_buy += amt_sum
                        else:
                            suspect_sell += amt_sum
                    else:
                        if direction == "B":
                            herd_buy += amt_sum
                        else:
                            herd_sell += amt_sum
            seq = []
    # 尾组
    if len(seq) >= min_consec and all(x["d"] == seq[0]["d"] for x in seq):
        dur = _t2s(seq[-1]["t"]) - _t2s(seq[0]["t"])
        if dur <= window_sec:
            direction = seq[0]["d"]
            amt_sum = sum(x["amt"] for x in seq)
            p0, p1 = seq[0]["price"], seq[-1]["price"]
            price_dir = "up" if p1 > p0 else ("down" if p1 < p0 else "flat")
            contrarian = (direction == "B" and price_dir == "down") or \
                         (direction == "S" and price_dir == "up")
            groups.append({
                "d": direction, "n": len(seq), "amt": round(amt_sum),
                "t0": seq[0]["t"], "t1": seq[-1]["t"],
                "p0": p0, "p1": p1,
                "contrarian": contrarian, "price_dir": price_dir,
            })
            if contrarian:
                if direction == "B":
                    suspect_buy += amt_sum
                else:
                    suspect_sell += amt_sum
            else:
                if direction == "B":
                    herd_buy += amt_sum
                else:
                    herd_sell += amt_sum

    groups.sort(key=lambda g: -g["amt"])
    return {
        "buy_amt": round(suspect_buy),       # 逆势拆单买(主力吸筹)
        "sell_amt": round(suspect_sell),     # 逆势拆单卖(主力派发)
        "net": round(suspect_buy - suspect_sell),
        "herd_buy": round(herd_buy),         # 顺势买(散户追涨)
        "herd_sell": round(herd_sell),       # 顺势卖(散户割肉)
        "groups": groups[:10],
    }


def compute_dark_flow(symbol: Symbol) -> dict | None:
    """计算暗盘资金 v5: 三分类 + 大单/暗盘分层 + 价格维度 + 时段。"""
    code = _tencent_code(symbol)
    if not code:
        return None
    ticks = _fetch_all_ticks(code)
    if not ticks:
        return None

    # ---- 基础统计(三分类, 竞价单单独处理) ----
    # 关键(2026-08-11 三表破解): 9:25-9:30 集合竞价撮合不是"主动买入",
    # 腾讯网页大单把它算中性盘。方向标记 B 在竞价时段不可信。
    buy_amt = sell_amt = m_amt = 0.0
    buy_vol = sell_vol = m_vol = 0.0
    auction_amt = auction_vol = 0.0   # 竞价单(集合竞价撮合)
    # 大单分层
    big_buy_amt = big_sell_amt = 0.0
    small_buy_amt = small_sell_amt = 0.0
    # 时段
    seg = {"morning": 0.0, "mid": 0.0, "afternoon": 0.0, "tail": 0.0}

    for tk in ticks:
        d, amt, vol, t = tk["d"], tk["amt"], tk["vol"], tk["t"]
        is_big = amt >= BIG_AMOUNT or vol >= BIG_VOLUME
        hm = t[:5]
        # 竞价时段(9:25-9:30): 集合竞价撮合, 方向不可信, 单独统计
        if t < "09:30":
            auction_amt += amt
            auction_vol += vol
            continue
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
    dark_net = buy_amt - sell_amt          # 全量主动净额(剔除竞价)
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
        "auction_amt": round(auction_amt),   # 竞价撮合金额(元)
        "auction_vol": round(auction_vol),   # 竞价撮合手数
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
        auction_amt, auction_vol,
    )

    # ---- 拆单识别(主力伪装的中小单) ----
    try:
        split = _detect_split_orders(ticks)
        result["split_order"] = split
    except Exception as e:
        logger.debug(f"拆单识别失败: {e}")

    # ---- 价位级承接分析(2026-08-11 用户洞察) ----
    # 找"大单卖+中小买"(主力砸散户接) 或 "大单买+中小卖"(主力吸筹) 的价位
    try:
        from collections import defaultdict
        by_price = defaultdict(lambda: {"big_buy": 0.0, "big_sell": 0.0, "small_buy": 0.0, "small_sell": 0.0})
        for tk in ticks:
            p = round(tk["price"], 2)
            is_big = tk["amt"] >= BIG_AMOUNT
            if is_big:
                if tk["d"] == "B": by_price[p]["big_buy"] += tk["amt"]
                elif tk["d"] == "S": by_price[p]["big_sell"] += tk["amt"]
            else:
                if tk["d"] == "B": by_price[p]["small_buy"] += tk["amt"]
                elif tk["d"] == "S": by_price[p]["small_sell"] += tk["amt"]
        # 主力吸筹位: 大单净买>800万 且 中小单净卖(散户割)
        absorb_zones, distribute_zones = [], []
        for p, d in by_price.items():
            total = d["big_buy"] + d["big_sell"] + d["small_buy"] + d["small_sell"]
            if total < 1000e4:  # 只留 1000万以上成交的价位
                continue
            big_net = d["big_buy"] - d["big_sell"]
            small_net = d["small_buy"] - d["small_sell"]
            if big_net > 800e4 and small_net < -300e4:
                absorb_zones.append({"price": p, "big_net": round(big_net), "small_net": round(small_net)})
            elif big_net < -800e4 and small_net > 300e4:
                distribute_zones.append({"price": p, "big_net": round(big_net), "small_net": round(small_net)})
        result["absorb_zones"] = sorted(absorb_zones, key=lambda x: -x["big_net"])[:6]
        result["distribute_zones"] = sorted(distribute_zones, key=lambda x: x["big_net"])[:6]
    except Exception as e:
        logger.debug(f"承接价位分析失败: {e}")

    result["note"] = "v8: 主力信号=大单净方向(同花顺暗盘口径), 承接价位分解"
    return result


def _judge_signal(dark_net: float, big_net: float, small_net: float,
                  seg: dict, low_ratio: float | None,
                  strong_buy: list | None = None, strong_sell: list | None = None,
                  auction_amt: float = 0.0, auction_vol: float = 0.0) -> str:
    """信号判定 v8: 主信号=大单净方向(主力), 中小单作对手盘佐证。

    2026-08-11 修正(价位级分解实证): 神剑 11.84 大单净买+4486万/中小单净卖-744万,
    同花顺判"暗盘流入4.02亿"= 大单吸筹。所以主力信号看大单(≥100万)净额,
    中小单=散户行为(对手盘)。
    """
    threshold = 500e4  # 500万
    tail = seg.get("tail", 0)
    strong_buy = strong_buy or []
    strong_sell = strong_sell or []
    n_buy_zone = len(strong_buy)
    n_sell_zone = len(strong_sell)
    low_boost = "低位承接" if (low_ratio or 0) > 0.4 else ""
    auction_note = f"竞价{auction_amt/1e4:.0f}万" if auction_amt > 0 else ""

    # 主力信号 = 大单净方向(同花顺"暗盘"≈大单主动净额)
    if big_net > threshold:
        if small_net < -threshold:
            # 大单吸筹 + 中小单割肉 = 典型吸筹(同花顺暗盘流入场景)
            return f"主力吸筹(大单+{big_net/1e4:.0f}万, 散户-{abs(small_net)/1e4:.0f}万){low_boost}|{auction_note}"
        if tail > 0:
            return f"主力流入+尾盘加仓(吸筹){low_boost}|{auction_note}"
        return f"主力流入(大单主动买){low_boost}|{auction_note}"
    if big_net < -threshold:
        if small_net > threshold:
            # 大单出货 + 中小单接盘 = 派发
            return f"主力派发(大单-{abs(big_net)/1e4:.0f}万, 散户+{small_net/1e4:.0f}万)|{auction_note}"
        if tail < 0:
            return f"主力流出+尾盘抛压(出货)|{auction_note}"
        return f"主力流出(大单主动卖)|{auction_note}"
    return f"观望(大单买卖接近, 吸筹区{n_buy_zone}/抛压区{n_sell_zone})|{auction_note}"
