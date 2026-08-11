"""盘口强度/暗盘资金计算器 v4(2026-08-11 修正)。

重大修正: 腾讯逐笔接口 appn=detail&action=data 返回**全天全量成交明细**
(非大单!):
- 翻页 p=0..N 直到空页, 每页70条, 序号连续无跳变
- 9:25:00 竞价 → 15:15:30 收盘, 含尾盘集合竞价
- 总金额 = 全天成交额(实测 21.32亿 = 21.31亿 ✓)
- 每笔含方向: B=主动买(吃卖单) S=主动卖(砸买单) M=中性/集合竞价

基于此, "暗盘资金"可免费近似:
  暗盘资金 ≈ Σ(主动买金额) − Σ(主动卖金额)  (同花顺口径的免费近似)
  方向可信(神剑 +4311万, 同花顺 +4016万, 差7%), 量级与L2有差(同花顺4.02亿是L2委托级)
"""
from __future__ import annotations

import logging
import re
import urllib.request

from marketdata.symbol import Symbol

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 主动买卖净额最小金额过滤(元): 过滤集合竞价/零碎单噪音
MIN_AMOUNT = 1e4  # 1万元


def _tencent_code(symbol: Symbol) -> str | None:
    code = (symbol.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code[0] in ("6", "9") or code.startswith("688"):
        return f"sh{code}"
    if code[0] in ("0", "2", "3"):
        return f"sz{code}"
    return None


def _fetch_all_ticks(code: str, max_pages: int = 200) -> list[tuple[str, float, str]]:
    """翻页拉取全天全量逐笔, 返回 [(方向, 金额, 时间)]。到空页停止。"""
    ticks: list[tuple[str, float, str]] = []
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
                direction = parts[6]
                t = parts[1]
            except (ValueError, IndexError):
                continue
            if amt >= MIN_AMOUNT:
                ticks.append((direction, amt, t))
    return ticks


def compute_dark_flow(symbol: Symbol) -> dict | None:
    """计算暗盘资金(主动买卖净额) + 时段分解。"""
    code = _tencent_code(symbol)
    if not code:
        return None
    ticks = _fetch_all_ticks(code)
    if not ticks:
        return None

    buy = sell = m_amt = 0.0
    n_buy = n_sell = n_m = 0
    # 时段分解: 早盘(9:25-10:30) 午盘(10:30-11:30) 午后(13:00-14:30) 尾盘(14:30-15:15)
    seg = {"morning": 0.0, "mid": 0.0, "afternoon": 0.0, "tail": 0.0}
    for direction, amt, t in ticks:
        hm = t[:5]
        if direction == "B":
            buy += amt; n_buy += 1
            sign = 1.0
        elif direction == "S":
            sell += amt; n_sell += 1
            sign = -1.0
        else:
            m_amt += amt; n_m += 1
            continue
        if hm < "10:30":
            seg["morning"] += sign * amt
        elif hm < "11:30":
            seg["mid"] += sign * amt
        elif hm < "14:30":
            seg["afternoon"] += sign * amt
        else:
            seg["tail"] += sign * amt

    dark_net = buy - sell
    dark_net_with_m = buy + m_amt - sell
    signal = _judge_signal(dark_net, seg)

    return {
        "dark_net": round(dark_net),          # 暗盘资金(B-S)
        "dark_net_with_m": round(dark_net_with_m),  # 含中性单
        "buy_total": round(buy), "sell_total": round(sell),
        "m_total": round(m_amt),
        "tick_count": len(ticks),
        "segments": {k: round(v) for k, v in seg.items()},
        "signal": signal,
        "note": "腾讯逐笔全天全量(B/S方向), 同花顺口径免费近似",
    }


def _judge_signal(dark_net: float, seg: dict) -> str:
    """信号判定: 暗盘净额方向 + 尾盘特征。"""
    threshold = 500e4  # 500万
    tail = seg.get("tail", 0)
    if dark_net > threshold and tail > 0:
        return "暗盘流入+尾盘加仓(吸筹)"
    if dark_net > threshold:
        return "暗盘流入(主动买占优)"
    if dark_net < -threshold and tail < 0:
        return "暗盘流出+尾盘抛压(出货)"
    if dark_net < -threshold:
        return "暗盘流出(主动卖占优)"
    return "观望(主动买卖接近)"
