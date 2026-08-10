"""K线组合形态识别(2026-08-10,基于同花顺教学文《K线经典形态》《K线形态大全》)。

输入: 日K序列(open/high/low/close/volume),输出识别到的形态列表。
仅做技术形态识别,不构成投资建议。

可量化识别形态(按同花顺原文特征):
- 金针探底: 极长下影线(下影>=实体2倍),且出现在近期低位
- 双针探底: 两根长下影线,低点接近,构成底部确认
- 红三兵: 三连阳,收盘价递增,实体稳步
- 涨停双响炮: 涨停(或大阳) → 1-3根小整理 → 再涨停(或大阳)
- 揭竿而起: 连续下跌后突然一根大阳线(涨幅>=5%)
- 上升三法: 大阳 → 3根小阴小阳(不破首阳低点) → 再大阳突破
- 小步上扬: 连续多根小阳线阶梯爬升(>=5根,每根涨幅<3%)
- 放量突破(均线多头·布林突破简化): 突破前N日高点+放量

启发式阈值基于同花顺原文描述,可调;识别结果供 AI 助手参考,需结合位置/量能/资金流综合判断。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# 涨停阈值(简化:主板 10% 附近视为涨停)
LIMIT_UP_PCT = 9.5
# 大阳线涨幅阈值
BIG_YANG_PCT = 5.0
# 长下影线: 下影线长度 >= 实体长度的倍数
SHADOW_RATIO = 2.0
# 实体最小绝对值(价格单位): 实体过小的十字星/横盘不算针
MIN_BODY = 0.5


def _is_long_lower_shadow(bar) -> bool:
    """长下影判定: 实体足够大 + 下影>=实体*SHADOW_RATIO(带浮点容差)。"""
    body = _body_len(bar)
    if body < MIN_BODY:
        return False
    return _shadow_bottom(bar) + 1e-6 >= body * SHADOW_RATIO


@dataclass
class PatternHit:
    """识别到的形态。"""

    name: str            # 形态名(与同花顺原文一致)
    signal: str          # 信号方向: 看涨 / 看跌 / 中性
    description: str     # 特征描述
    position: str = ""   # 出现位置(低位/高位/趋势中)
    bars: list[int] = field(default_factory=list)  # 涉及的K线索引(从尾部倒数)
    extra: dict = field(default_factory=dict)


def _is_yang(bar) -> bool:
    return bar.close >= bar.open


def _body_len(bar) -> float:
    return abs(bar.close - bar.open)


def _shadow_bottom(bar) -> float:
    """下影线长度。"""
    return min(bar.open, bar.close) - bar.low


def _shadow_top(bar) -> float:
    """上影线长度。"""
    return bar.high - max(bar.open, bar.close)


def _change_pct(bar) -> float:
    """涨跌幅(相对前收)。"""
    return (bar.close - bar.open) / bar.open * 100 if bar.open else 0.0


def detect_patterns(bars: list, lookback: int = 30) -> list[PatternHit]:
    """识别 K 线形态。bars: 日K序列(升序,需含 open/high/low/close/volume)。"""
    if len(bars) < 5:
        return []
    seq = bars[-lookback:]
    hits: list[PatternHit] = []

    # 近期高低位判断(用于金针/双针的"低位"条件)
    window_low = min(b.low for b in seq)
    window_high = max(b.high for b in seq)

    # ---- 1. 金针探底 ----
    last = bars[-1]
    if _is_long_lower_shadow(last):
        # 出现在近期低位(最低价在窗口下 60% 区间内,即盘中确实探到低位)
        if last.low <= window_low + (window_high - window_low) * 0.6:
            hits.append(PatternHit(
                name="金针探底", signal="看涨", position="低位",
                description="极长下影线(下影>=实体2倍),急跌后快速拉回,下方有大资金托底,可能是见底信号",
                bars=[-1],
            ))

    # ---- 2. 双针探底 ----
    if len(bars) >= 3:
        b1, b2 = bars[-2], bars[-1]
        if (_is_long_lower_shadow(b1) and
                _is_long_lower_shadow(b2) and
                abs(b1.low - b2.low) / max(b1.low, b2.low) < 0.03):
            hits.append(PatternHit(
                name="双针探底", signal="看涨", position="低位",
                description="两次急跌都被拉回,两根长下影线低点接近,底部支撑更坚固,容易迎来反弹",
                bars=[-2, -1],
            ))

    # ---- 3. 红三兵 ----
    if len(bars) >= 3:
        b1, b2, b3 = bars[-3], bars[-2], bars[-1]
        if (_is_yang(b1) and _is_yang(b2) and _is_yang(b3) and
                b1.close < b2.close < b3.close):
            hits.append(PatternHit(
                name="红三兵", signal="看涨", position="趋势中",
                description="三根连续阳线收盘价递增,买盘积聚多头增强,后续大概率继续走强",
                bars=[-3, -2, -1],
            ))

    # ---- 4. 涨停双响炮 ----
    if len(bars) >= 5:
        b1, b2, b3 = bars[-3], bars[-2], bars[-1]
        if (_change_pct(b1) >= LIMIT_UP_PCT and _change_pct(b3) >= LIMIT_UP_PCT and
                abs(_change_pct(b2)) < 5.0):
            hits.append(PatternHit(
                name="涨停双响炮", signal="看涨", position="趋势中",
                description="涨停→短暂休整(不深跌)→再涨停,多头攻势干脆利落,短期往往还有冲高空间",
                bars=[-3, -2, -1],
            ))

    # ---- 5. 揭竿而起 ----
    if len(bars) >= 4:
        prev = bars[-4:-1]
        last = bars[-1]
        if (_change_pct(last) >= BIG_YANG_PCT and
                all(not _is_yang(b) for b in prev) and
                last.close > max(b.close for b in prev)):
            hits.append(PatternHit(
                name="揭竿而起", signal="看涨", position="低位",
                description="连续下跌后突然拉出一根大阳线,空头砸盘失败,多头强势反攻,行情有望反转",
                bars=[-1],
            ))

    # ---- 6. 上升三法 ----
    if len(bars) >= 5:
        b1, mid, b5 = bars[-5], bars[-4:-1], bars[-1]
        if (_change_pct(b1) >= BIG_YANG_PCT and _change_pct(b5) >= BIG_YANG_PCT and
                all(b.close >= b1.low and b.close <= b1.close for b in mid) and
                b5.close > b1.close):
            hits.append(PatternHit(
                name="上升三法", signal="看涨", position="趋势中",
                description="大阳线拉起→中间几根小K线横盘(不破首阳低点)→再大阳突破,主力拉升→洗盘→再拉升,蓄势再涨",
                bars=[-5, -4, -3, -2, -1],
            ))

    # ---- 7. 小步上扬 ----
    if len(bars) >= 5:
        last5 = bars[-5:]
        if (all(_is_yang(b) for b in last5) and
                all(0 < _change_pct(b) < 3.0 for b in last5) and
                all(last5[i].close >= last5[i-1].close for i in range(1, 5))):
            hits.append(PatternHit(
                name="小步上扬", signal="看涨", position="趋势中",
                description="每天小阳线慢慢爬升,买盘温和持续,典型慢牛走势,趋势相对稳健",
                bars=[-5, -4, -3, -2, -1],
            ))

    # ---- 8. 放量突破(均线多头·布林突破简化) ----
    if len(bars) >= 20:
        prev20 = bars[-21:-1]
        last = bars[-1]
        prior_high = max(b.high for b in prev20)
        avg_vol = sum(b.volume for b in prev20) / 20 if prev20 else 0
        if (last.close > prior_high and
                last.volume > avg_vol * 1.5 and
                avg_vol > 0):
            hits.append(PatternHit(
                name="放量突破(均线多头·布林突破)", signal="看涨", position="趋势中",
                description=f"收盘价突破前20日高点({prior_high:.2f})且放量(量能>20日均量1.5倍),趋势向上打开空间",
                bars=[-1],
                extra={"break_high": prior_high, "vol_ratio": round(last.volume / avg_vol, 2) if avg_vol else 0},
            ))

    return hits


def format_patterns(hits: list[PatternHit]) -> str:
    """格式化识别结果(供 AI 助手 / API 使用)。"""
    if not hits:
        return "近期未识别到典型K线组合形态。"
    lines = [f"识别到 {len(hits)} 个K线形态:"]
    for h in hits:
        lines.append(f"- 【{h.name}】信号: {h.signal} 位置: {h.position or '未知'}")
        lines.append(f"  {h.description}")
    return "\n".join(lines)
