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
                         lo: float = 5e4, hi: float = 100e4,
                         prev_close: float | None = None) -> dict:
    """拆单识别 v3: 识别主力伪装的中小单(含价格背景+套牢位置, 2026-08-11 二次修正)。

    修正史:
    v1 只按"同向+时间+金额" → 把散户割肉(跌中连续卖)误判为主力拆单卖
    v2 加价格方向(逆势=跌中买/涨中卖) → 仍误判: 涨中卖可能是散户解套盘!
       (用户洞察: 震荡后散户在上涨时卖套牢筹码, 神剑实测唯一涨中卖118万全是解套盘)
    v3 加"相对昨收位置": 
      - 涨中卖 + 价格<昨收(套牢区) = 散户解套(不是主力!)
      - 涨中卖 + 价格>昨收(获利区) = 疑似主力派发
      - 跌中买 + 价格<昨收 = 主力抄底吸筹(强信号)
      - 跌中买 + 价格>昨收 = 回落承接(中性)

    Returns: {buy_amt, sell_amt, net, contrarian_net, herd_buy, herd_sell, groups}
    """
    def _t2s(t: str) -> int:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)

    suspect_buy = suspect_sell = 0.0   # 疑似主力(逆势+位置确认)
    herd_buy = herd_sell = 0.0         # 散户(顺势/解套)
    groups = []
    seq = []
    for tk in ticks:
        if lo <= tk["amt"] <= hi:
            seq.append(tk)
        else:
            if len(seq) >= min_consec and all(x["d"] == seq[0]["d"] for x in seq):
                dur = _t2s(seq[-1]["t"]) - _t2s(seq[0]["t"])
                if dur <= window_sec:
                    g = _classify_split(seq, prev_close)
                    groups.append(g)
                    if g["contrarian"]:
                        if g["d"] == "B":
                            suspect_buy += g["amt"]
                        else:
                            suspect_sell += g["amt"]
                    else:
                        if g["d"] == "B":
                            herd_buy += g["amt"]
                        else:
                            herd_sell += g["amt"]
            seq = []
    # 尾组
    if len(seq) >= min_consec and all(x["d"] == seq[0]["d"] for x in seq):
        dur = _t2s(seq[-1]["t"]) - _t2s(seq[0]["t"])
        if dur <= window_sec:
            g = _classify_split(seq, prev_close)
            groups.append(g)
            if g["contrarian"]:
                if g["d"] == "B":
                    suspect_buy += g["amt"]
                else:
                    suspect_sell += g["amt"]
            else:
                if g["d"] == "B":
                    herd_buy += g["amt"]
                else:
                    herd_sell += g["amt"]

    groups.sort(key=lambda g: -g["amt"])
    return {
        "buy_amt": round(suspect_buy),
        "sell_amt": round(suspect_sell),
        "net": round(suspect_buy - suspect_sell),
        "herd_buy": round(herd_buy),
        "herd_sell": round(herd_sell),
        "groups": groups[:10],
    }


def _classify_split(seq: list[dict], prev_close: float | None) -> dict:
    """单组拆单分类, 返回组信息 + contrarian 标记。"""
    direction = seq[0]["d"]
    amt_sum = sum(x["amt"] for x in seq)
    p0, p1 = seq[0]["price"], seq[-1]["price"]
    price_dir = "up" if p1 > p0 else ("down" if p1 < p0 else "flat")
    # 相对昨收位置(套牢区 vs 获利区)
    below_prev = prev_close is not None and p0 < prev_close
    contrarian = False
    reason = ""
    if direction == "B" and price_dir == "down" and below_prev:
        contrarian = True      # 跌中买+套牢区 = 主力抄底吸筹(强信号)
        reason = "主力抄底"
    elif direction == "S" and price_dir == "up" and not below_prev:
        contrarian = True      # 涨中卖+获利区 = 主力派发
        reason = "主力派发"
    elif direction == "S" and price_dir == "up" and below_prev:
        contrarian = False     # 涨中卖+套牢区 = 散户解套盘(用户洞察!)
        reason = "散户解套"
    elif direction == "B" and price_dir == "down" and not below_prev:
        contrarian = False     # 跌中买+获利区 = 回落承接(中性)
        reason = "回落承接"
    else:
        reason = "横盘"

    return {
        "d": direction, "n": len(seq), "amt": round(amt_sum),
        "t0": seq[0]["t"], "t1": seq[-1]["t"],
        "p0": p0, "p1": p1,
        "contrarian": contrarian, "price_dir": price_dir, "reason": reason,
    }


def compute_dark_flow(symbol: Symbol) -> dict | None:
    """计算暗盘资金 v5: 三分类 + 大单/暗盘分层 + 价格维度 + 时段。"""
    code = _tencent_code(symbol)
    if not code:
        return None
    ticks = _fetch_all_ticks(code)
    if not ticks:
        return None

    # 昨收(用于套牢区判断: 涨中卖+低于昨收=散户解套, 非主力派发)
    prev_close = None
    try:
        from marketdata.vendors.tencent import TencentQuoteVendor
        q = TencentQuoteVendor().fetch([symbol], {})[0]
        prev_close = q.prev_close if q.prev_close else None
    except Exception:
        pass

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
    big_net = big_buy_amt - big_sell_amt   # 超大单(≥100万)净额
    small_net = small_buy_amt - small_sell_amt  # 中小单(<100万)净额

    # 当日主力意图(2026-08-11 修正, 腾讯官方口径):
    # 主力 = 成交金额≥20万 或 股数≥6万股(600手); 超大单≥100万; 大单=主力-超大单
    # ⚠️ 必须剔除竞价单(9:25-9:30 撮合非主动买卖), 否则主力净额被竞价B污染
    non_auction = [t for t in ticks if t["t"] >= "09:30"]
    main_buy_amt = sum(t["amt"] for t in non_auction if (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "B")
    main_sell_amt = sum(t["amt"] for t in non_auction if (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "S")
    main_net = main_buy_amt - main_sell_amt           # 主力净额(≥20万, 剔除竞价)
    big_net = big_buy_amt - big_sell_amt               # 超大单净额(≥100万, 已剔除竞价)
    mid_net = main_net - big_net                        # 大单净额(20万-100万)
    retail_buy_amt = sum(t["amt"] for t in non_auction if not (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "B")
    retail_sell_amt = sum(t["amt"] for t in non_auction if not (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "S")
    retail_net = retail_buy_amt - retail_sell_amt      # 散户净额(<20万, 剔除竞价)
    main_intensity = (main_buy_amt + main_sell_amt) / (buy_amt + sell_amt) * 100 if (buy_amt + sell_amt) else None  # 主力参与度%
    main_buy_ratio = main_buy_amt / (main_buy_amt + main_sell_amt) * 100 if (main_buy_amt + main_sell_amt) else None  # 主力买占主力成交%

    result = {
        "dark_net": round(dark_net),           # 全量主动净额
        "main_net": round(main_net),           # 主力净额(≥20万, 腾讯官方口径)
        "big_net": round(big_net),             # 超大单净额(≥100万)
        "mid_net": round(mid_net),             # 大单净额(20-100万)
        "small_net": round(retail_net),        # 散户净额(<20万)
        "buy_amt": round(buy_amt), "sell_amt": round(sell_amt),
        "m_amt": round(m_amt),
        "buy_vol": round(buy_vol), "sell_vol": round(sell_vol), "m_vol": round(m_vol),
        "buy_pct": round(buy_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
        "sell_pct": round(sell_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
        "auction_amt": round(auction_amt),   # 竞价撮合金额(元)
        "auction_vol": round(auction_vol),   # 竞价撮合手数
        "main_intensity": round(main_intensity, 1) if main_intensity is not None else None,  # 主力参与度%
        "main_buy_ratio": round(main_buy_ratio, 1) if main_buy_ratio is not None else None,  # 主力买占比%
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
        dark_net, main_net, big_net, mid_net, retail_net, seg,
        result.get("low_price_ratio"),
        result.get("strong_buy_zones", []),
        result.get("strong_sell_zones", []),
        auction_amt, auction_vol,
        result.get("main_intensity"), result.get("main_buy_ratio"),
    )

    # ---- 拆单识别(主力伪装的中小单) ----
    try:
        split = _detect_split_orders(ticks, prev_close=prev_close)
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

    # ---- 5日主力阶段(2026-08-11 用户洞察: 主力不可能一直买/散户不能一直卖) ----
    try:
        from marketdata.vendors.tencent_fundflow import TencentFundflowVendor
        cf = TencentFundflowVendor().fetch([symbol], {})[0]
        today_main = cf.main_net_inflow        # 今日主力净(腾讯口径, 元)
        main_5d = cf.main_net_5d               # 近5日主力净累计(元)
        if main_5d is not None:
            today_main = today_main or 0.0
            result["today_main_5d_net"] = round(today_main)
            result["main_5d_net"] = round(main_5d)
            if main_5d > 0 and today_main < 0:
                result["phase"] = "吸筹后转派发(5日净流入但今日流出, 主力开始获利了结)"
            elif main_5d > 0 and today_main > 0:
                result["phase"] = "持续吸筹(5日+今日均净流入)"
            elif main_5d < 0 and today_main > 0:
                result["phase"] = "派发后反弹(5日净流出但今日流入, 观察是否止跌)"
            elif main_5d < 0 and today_main < 0:
                result["phase"] = "持续派发(5日+今日均净流出)"
            else:
                result["phase"] = "阶段不明(数据不足)"
    except Exception as e:
        logger.debug(f"5日阶段判断失败: {e}")

    result["note"] = "v11: 拆单+套牢位+5日主力阶段(双向)"
    return result


def _judge_signal(dark_net: float, main_net: float, big_net: float, mid_net: float,
                  retail_net: float, seg: dict, low_ratio: float | None,
                  strong_buy: list | None = None, strong_sell: list | None = None,
                  auction_amt: float = 0.0, auction_vol: float = 0.0,
                  main_intensity: float | None = None, main_buy_ratio: float | None = None) -> str:
    """信号判定 v14: 主力买入强度(吸筹力度) + 净额方向。

    2026-08-11 二次修正(用户洞察: 同花顺暗盘流入多 = 主力在吸筹):
    - 同花顺"暗盘" ≈ 主力主动买入强度(占成交额 40-80%), 不是净额
    - 神剑: 主力买8.6亿(占40.6%)净额仅-2.9% → 判吸筹(同花顺一致), 不再判"托盘出货"
    - 主力买入强度 = 主力参与度%(占全市场成交) + 主力买占比%(买占主力成交)
    """
    threshold = 500e4  # 500万
    tail = seg.get("tail", 0)
    low_boost = "低位承接" if (low_ratio or 0) > 0.4 else ""
    auction_note = f"竞价{auction_amt/1e4:.0f}万" if auction_amt > 0 else ""
    # 吸筹力度: 主力参与度>35% 且 主力买占比>48% = 强吸筹
    strong_absorb = (main_intensity or 0) >= 35 and (main_buy_ratio or 0) >= 48
    intensity_note = f"主力买占比{main_buy_ratio:.0f}%" if main_buy_ratio else ""

    # 主力净方向(≥20万)
    if main_net > threshold:
        if tail > 0:
            return f"主力净流入+尾盘加仓(吸筹){low_boost}|{auction_note}|{intensity_note}"
        return f"主力净流入(主动买占优){low_boost}|{auction_note}|{intensity_note}"
    if main_net < -threshold:
        # 净流出但主力参与度高(买入强度大) = 对倒换手/洗盘吸筹
        if strong_absorb:
            return f"主力净流出但参与度高({main_buy_ratio:.0f}%买占)疑洗盘吸筹|{auction_note}"
        if tail < 0:
            return f"主力净流出+尾盘抛压(出货)|{auction_note}"
        return f"主力净流出(主动卖占优)|{auction_note}"
    # 平衡: 看买入强度定吸筹/派发
    if strong_absorb:
        return f"主力平衡但参与度高({main_buy_ratio:.0f}%买占)疑吸筹|{auction_note}"
    return f"主力平衡(买卖接近)|{auction_note}"
