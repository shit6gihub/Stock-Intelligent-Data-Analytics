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
