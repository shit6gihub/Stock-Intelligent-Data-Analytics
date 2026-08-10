"""kline_pattern 模块测试(合成 K 线验证各形态识别)。"""

from __future__ import annotations

import pytest


class FakeBar:
    def __init__(self, o, h, l, c, v=1.0, date="2026-08-01"):
        self.open, self.high, self.low, self.close, self.volume, self.date = o, h, l, c, v, date


def make_bars(rows):
    """rows: [(o,h,l,c,v), ...] → [FakeBar...]"""
    return [FakeBar(*r, date=f"2026-08-{i+1:02d}") for i, r in enumerate(rows)]


def test_golden_needle():
    """金针探底: 低位极长下影线。"""
    from src.core.kline_pattern import detect_patterns
    # 前 5 根阴跌,最后一根长下影(下影=5, 实体=1)
    bars = make_bars([
        (10, 10.2, 9.8, 9.9, 1), (9.9, 10.0, 9.5, 9.6, 1), (9.6, 9.7, 9.2, 9.3, 1),
        (9.3, 9.4, 8.9, 9.0, 1), (9.0, 9.1, 8.6, 8.7, 1),
        (8.2, 9.0, 7.0, 8.8, 2),  # 实体=0.6 下影=1.2(2x) 收盘8.8 在窗口下半区
    ])
    hits = detect_patterns(bars)
    names = [h.name for h in hits]
    assert "金针探底" in names, f"应识别金针探底,实际 {names}"


def test_three_red_soldiers():
    """红三兵: 三连阳收盘递增。"""
    from src.core.kline_pattern import detect_patterns
    # 前 3 根横盘背景 + 3 根连阳
    bars = make_bars([
        (10, 10.1, 9.9, 10.0, 1), (10, 10.1, 9.9, 10.0, 1), (10, 10.1, 9.9, 10.0, 1),
        (10, 10.5, 9.8, 10.4, 1), (10.4, 10.9, 10.2, 10.8, 1.2), (10.8, 11.4, 10.7, 11.3, 1.5),
    ])
    hits = detect_patterns(bars)
    names = [h.name for h in hits]
    assert "红三兵" in names, f"应识别红三兵,实际 {names}"


def test_double_needle_bottom():
    """双针探底: 两根长下影低点接近。"""
    from src.core.kline_pattern import detect_patterns
    # 两根长下影: 实体0.6 下影1.3,低点 9.0/9.05 接近
    bars = make_bars([
        (10, 10.2, 9.9, 10.1, 1), (10.1, 10.3, 9.9, 10.2, 1), (10.2, 10.4, 9.9, 10.3, 1),
        (10.3, 11.0, 9.0, 10.9, 1.5),  # 实体0.6 下影1.3
        (10.9, 11.5, 9.05, 11.4, 1.5),  # 实体0.5 下影1.85
    ])
    hits = detect_patterns(bars)
    names = [h.name for h in hits]
    assert "双针探底" in names, f"应识别双针探底,实际 {names}"


def test_limit_up_double_cannon():
    """涨停双响炮: 涨停→整理→涨停。"""
    from src.core.kline_pattern import detect_patterns
    bars = make_bars([
        (10, 10.1, 9.9, 10.0, 1), (10, 10.1, 9.9, 10.0, 1), (10, 10.1, 9.9, 10.0, 1),
        (10, 11.2, 9.9, 11.2, 3),   # 涨停
        (11.2, 11.3, 11.0, 11.1, 1),  # 整理
        (11.1, 12.3, 11.0, 12.3, 3),  # 涨停
    ])
    hits = detect_patterns(bars)
    names = [h.name for h in hits]
    assert "涨停双响炮" in names, f"应识别涨停双响炮,实际 {names}"


def test_no_pattern_on_flat():
    """横盘无形态。"""
    from src.core.kline_pattern import detect_patterns
    bars = make_bars([(10, 10.1, 9.9, 10.0, 1)] * 10)
    hits = detect_patterns(bars)
    assert len(hits) == 0, f"横盘不应有形态,实际 {[h.name for h in hits]}"


def test_format_empty():
    from src.core.kline_pattern import format_patterns
    assert "未识别到" in format_patterns([])
