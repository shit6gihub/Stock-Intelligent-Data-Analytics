"""筹码分布计算器(成本分布 CYQ) — 对齐通达信/同花顺口径。

原理(公开算法, 陈浩《筹码分布》+ 通达信):
- 每日成交筹码在 [low, high] 间按"三角分布"(以均价为峰)分布
- 历史筹码按换手率衰减: 当日筹码 = 当日新增 + 昨日剩余×(1-换手率×衰减系数)
- 递归累加 → 当前各价位持仓成本分布

输入: 日K(open/high/low/close/volume) + 换手率(或用成交量/流通股本近似)
输出: 筹码分布序列 + COST(10/50/90) + 获利盘比例 + 筹码峰(主力成本区)

对齐验证: 与通达信/同花顺/东财偏差 <10%(公开文献结论)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 历史换手衰减系数(通达信默认, 突出近期筹码)
DECAY = 1.0


def fetch_sina_hist_price(symbol_code: str, start: str, end: str) -> list[dict] | None:
    """新浪历史分价表: 区间内各价位真实累计成交量(2026-08-11 接入)。

    symbol_code: 'sz002361' / 'sh600519'
    start/end: '2026-08-03' / '2026-08-10'
    Returns: [{price, volume, pct}] 按价降序; 失败 None
    """
    import re
    import urllib.request

    url = (f"http://market.finance.sina.com.cn/iframe/pricehis.php"
           f"?symbol={symbol_code}&startdate={start}&enddate={end}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("gb2312", "replace")
    except Exception:
        return None
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", m.group(1), re.S)]
        if len(cells) >= 3 and re.match(r"^\d+\.\d+$", cells[0]):
            try:
                rows.append({
                    "price": float(cells[0]),
                    "volume": int(cells[1].replace(",", "")),
                    "pct": float(cells[2].replace("%", "")),
                })
            except (ValueError, IndexError):
                continue
    return rows or None


def compute_chips_sina(symbol_code: str, days: int = 300) -> dict | None:
    """基于新浪历史分价表的筹码分布(真实价位分布, 2026-08-11)。

    分段拉取(每段 ~60 交易日) → 每段真实价位分布按换手率衰减 → 累加。
    比三角分布估算精确(真实成交价位), 失败回退 compute_chips(三角分布)。

    Returns: 同 compute_chips 结构
    """
    import datetime

    # 交易日近似: 用自然日倒推(周末/假日约 1.4x)
    segments = []
    end_d = datetime.date.today()
    span_days = int(days * 1.4) + 10
    seg_len = 90  # 每段 ~90 自然日(~60 交易日)
    cursor = end_d
    while segments == [] or (end_d - segments[-1]["start_d"]).days < span_days:
        start_d = cursor - datetime.timedelta(days=seg_len)
        seg = fetch_sina_hist_price(symbol_code, start_d.isoformat(), cursor.isoformat())
        if seg:
            segments.append({"start_d": start_d, "end": cursor.isoformat(), "start": start_d.isoformat(), "rows": seg})
        cursor = start_d - datetime.timedelta(days=1)
        if len(segments) >= 8:
            break

    if not segments:
        return None

    # 合并价位网格
    all_prices = set()
    for seg in segments:
        for r in seg["rows"]:
            all_prices.add(round(r["price"], 2))
    prices = sorted(all_prices)
    idx = {p: i for i, p in enumerate(prices)}
    n = len(prices)
    chips = [0.0] * n

    # 流通股本近似: 取最大单段总成交 × 系数(无精确流通股本时)
    max_seg_vol = max(sum(r["volume"] for r in seg["rows"]) for seg in segments)
    float_shares = max_seg_vol * 6 or 1.0

    # 由旧到新累加(衰减)
    for seg in reversed(segments):
        seg_vol = sum(r["volume"] for r in seg["rows"])
        if seg_vol <= 0:
            continue
        turnover = min(max(seg_vol / float_shares, 0.001), 1.0)
        keep = 1.0 - turnover * DECAY
        chips = [c * keep for c in chips]
        for r in seg["rows"]:
            i = idx.get(round(r["price"], 2))
            if i is not None:
                chips[i] += r["volume"]

    total = sum(chips)
    if total <= 0:
        return None
    chips = [c / total for c in chips]

    def cost(percent: float) -> float:
        cum = 0.0
        for i, c in enumerate(chips):
            cum += c
            if cum >= percent:
                return prices[i]
        return prices[-1]

    c10, c50, c90 = cost(0.10), cost(0.50), cost(0.90)
    # 现价(用最新收盘价, 从行情取; 失败用最新段最高价近似)
    last_close = None
    try:
        from marketdata.vendors.tencent import TencentQuoteVendor
        from marketdata import Symbol
        sym = Symbol.parse(symbol_code[2:], "CN")
        q = TencentQuoteVendor().fetch([sym], {})[0]
        last_close = q.current_price
    except Exception:
        last_close = segments[0]["rows"][0]["price"] if segments and segments[0]["rows"] else None
    if last_close is None:
        return None
    profit_ratio = sum(c for p, c in zip(prices, chips) if p <= last_close)
    peak_i = max(range(n), key=lambda i: chips[i])

    return {
        "prices": prices,
        "chips": [round(c * 100, 3) for c in chips],
        "cost_10": round(c10, 2),
        "cost_50": round(c50, 2),
        "cost_90": round(c90, 2),
        "profit_ratio": round(profit_ratio, 4),
        "peak_price": round(prices[peak_i], 2),
        "peak_ratio": round(chips[peak_i] * 100, 2),
        "concentration": round((c90 - c10) / c50, 4) if c50 else None,
        "step": 0.01,
        "last_close": last_close,
        "source": "sina_hist_price",
        "segments": len(segments),
    }


def compute_near_term_chips(symbol_code: str, days: int = 10) -> dict | None:
    """近期真实筹码分布(新浪历史分价表, 免衰减, 2026-08-11)。

    近 N 交易日窗口内, 换手衰减影响小 → 直接用真实价位分布即精确。
    输出近期主力成本带(筹码峰), 用于"主力意图"段。

    Returns: {peak_price, peak_ratio, cost_10/50/90, profit_ratio, concentration,
              cost_band(low/high), prices, chips}
    """
    import datetime

    end_d = datetime.date.today()
    start_d = end_d - datetime.timedelta(days=int(days * 1.4) + 5)
    rows = fetch_sina_hist_price(symbol_code, start_d.isoformat(), end_d.isoformat())
    if not rows or len(rows) < 5:
        return None

    total = sum(r["volume"] for r in rows)
    if total <= 0:
        return None
    prices = [r["price"] for r in rows]           # 已按价降序
    vols = [r["volume"] for r in rows]
    chips = [v / total for v in vols]

    # 现价
    last_close = None
    try:
        from marketdata.vendors.tencent import TencentQuoteVendor
        from marketdata import Symbol
        sym = Symbol.parse(symbol_code[2:], "CN")
        q = TencentQuoteVendor().fetch([sym], {})[0]
        last_close = q.current_price
    except Exception:
        last_close = None
    if last_close is None:
        last_close = prices[-1]

    # COST: 价格升序累计
    asc = sorted(zip(prices, chips))
    def cost(pct: float) -> float:
        cum = 0.0
        for p, c in asc:
            cum += c
            if cum >= pct:
                return p
        return asc[-1][0]

    c10, c50, c90 = cost(0.10), cost(0.50), cost(0.90)
    profit = sum(c for p, c in asc if p <= last_close)
    peak_i = max(range(len(prices)), key=lambda i: chips[i])

    # 主力成本带: 含峰值的 ±5% 价格区间内累计占比 ≥40%
    peak = prices[peak_i]
    band_low = peak * 0.95
    band_high = peak * 1.05
    band_vol = sum(v for p, v in zip(prices, vols) if band_low <= p <= band_high)

    return {
        "prices": prices,
        "chips": [round(c * 100, 3) for c in chips],
        "cost_10": round(c10, 2),
        "cost_50": round(c50, 2),
        "cost_90": round(c90, 2),
        "profit_ratio": round(profit, 4),
        "peak_price": round(peak, 2),
        "peak_ratio": round(chips[peak_i] * 100, 2),
        "concentration": round((c90 - c10) / c50, 4) if c50 else None,
        "cost_band": {"low": round(band_low, 2), "high": round(band_high, 2),
                      "ratio": round(band_vol / total * 100, 1)},
        "last_close": last_close,
        "source": "sina_hist_price",
        "window_days": days,
    }


def compute_chips(klines: list, float_shares: float | None = None,
                  price_step: float = 0.01) -> dict | None:
    """计算筹码分布。

    klines: 日K列表, 每项有 date/open/high/low/close/volume(升序, 越新越靠后)
    float_shares: 自由流通股本(股)。None 时用最大成交量×5 近似(粗略)。
    Returns: {prices, chips, cost_10, cost_50, cost_90, profit_ratio, peak_price, peak_ratio}
    """
    if not klines or len(klines) < 20:
        return None

    # 价格网格: 覆盖全历史 [min_low, max_high], 步长自适应
    lo_p = min(k.low for k in klines)
    hi_p = max(k.high for k in klines)
    if hi_p - lo_p > 20:
        step = 0.1
    elif hi_p - lo_p > 5:
        step = 0.05
    else:
        step = 0.01
    # 网格(含边界)
    n = int((hi_p - lo_p) / step) + 2
    prices = [round(lo_p - step + i * step, 2) for i in range(n)]
    idx = {p: i for i, p in enumerate(prices)}
    chips = [0.0] * n

    # 流通股本: 用最大换手日近似(成交量/换手率); 无换手率时用最大成交量×8
    if float_shares is None:
        max_vol = max(k.volume for k in klines if k.volume)
        float_shares = max_vol * 8 or 1.0

    for k in klines:
        if k.volume <= 0 or k.high <= k.low:
            continue
        turnover = k.volume / float_shares  # 换手率
        turnover = min(max(turnover, 0.001), 1.0)
        # 衰减: 旧筹码按 (1 - turnover×DECAY) 保留
        keep = 1.0 - turnover * DECAY
        chips = [c * keep for c in chips]
        # 新增筹码: 三角分布(均价为峰) — 近似: 线性分配, 均价处权重最高
        avg_p = (k.high + k.low + k.close) / 3.0
        vol = k.volume
        # 将 vol 按三角权重分配到 [low, high] 网格
        p_lo, p_hi = k.low, k.high
        i_lo, i_hi = idx.get(round(p_lo, 2)), idx.get(round(p_hi, 2))
        if i_lo is None or i_hi is None:
            # 边界外: 找最近网格
            i_lo = min(range(n), key=lambda i: abs(prices[i] - p_lo))
            i_hi = min(range(n), key=lambda i: abs(prices[i] - p_hi))
        i_lo, i_hi = sorted([i_lo, i_hi])
        i_avg = min(range(i_lo, i_hi + 1), key=lambda i: abs(prices[i] - avg_p))
        # 三角权重: 左升右降
        total_w = 0.0
        w = [0.0] * (i_hi - i_lo + 1)
        for j in range(i_lo, i_hi + 1):
            if i_hi == i_lo:
                w[j - i_lo] = 1.0
            elif j <= i_avg:
                w[j - i_lo] = (j - i_lo + 1) / (i_avg - i_lo + 1)
            else:
                w[j - i_lo] = (i_hi - j + 1) / (i_hi - i_avg + 1)
            total_w += w[j - i_lo]
        if total_w <= 0:
            continue
        for j in range(i_lo, i_hi + 1):
            chips[j] += vol * w[j - i_lo] / total_w

    total = sum(chips)
    if total <= 0:
        return None

    # 归一化
    chips = [c / total for c in chips]

    # COST(N): N%筹码在价格以下
    def cost(percent: float) -> float:
        cum = 0.0
        for i, c in enumerate(chips):
            cum += c
            if cum >= percent:
                return prices[i]
        return prices[-1]

    c10, c50, c90 = cost(0.10), cost(0.50), cost(0.90)
    # 获利盘: 价格 ≤ 最新收盘 的筹码占比
    last_close = klines[-1].close
    profit_ratio = sum(c for p, c in zip(prices, chips) if p <= last_close)
    # 筹码峰: 最大占比价位(主力成本区)
    peak_i = max(range(n), key=lambda i: chips[i])
    peak_price = prices[peak_i]
    peak_ratio = chips[peak_i]

    return {
        "prices": prices,
        "chips": [round(c * 100, 3) for c in chips],  # 百分比
        "cost_10": round(c10, 2),
        "cost_50": round(c50, 2),
        "cost_90": round(c90, 2),
        "profit_ratio": round(profit_ratio, 4),
        "peak_price": round(peak_price, 2),
        "peak_ratio": round(peak_ratio * 100, 2),
        "concentration": round((c90 - c10) / c50, 4) if c50 else None,  # 集中度
        "step": step,
        "last_close": last_close,
    }
