"""盘口强度指标测试(2026-08-11): 腾讯实时数据四维度组合。"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent  # /tmp/PanWatch
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/marketdata/src"))

import pytest
from marketdata import Symbol
from src.core.dark_flow import compute_board_strength, _composite_signal


class TestBoardStrength:
    def test_real_compute(self):
        """真实数据: 002361 应返回完整四维度。"""
        r = compute_board_strength(Symbol.parse("002361", "CN"))
        assert r is not None
        dims = r["dimensions"]
        assert "active_net" in dims      # ① 主动买卖
        assert "big_order_net" in dims   # ② 大单主动
        assert "trend" in dims           # ③ 分时趋势
        assert "low_price" in dims       # ④ 低价承接
        assert r["signal"]

    def test_outer_inner_consistent(self):
        """外盘+内盘应≈总成交量。"""
        from marketdata.vendors.tencent import TencentQuoteVendor
        q = TencentQuoteVendor().fetch([Symbol.parse("002361", "CN")], {})[0]
        outer = q.volume_outer or 0
        inner = q.volume_inner or 0
        total = q.volume or 0
        if total > 0:
            assert abs(outer + inner - total) / total < 0.05

    def test_trend_has_tail(self):
        """分时趋势应含尾盘特征。"""
        from src.core.dark_flow import _fetch_trend_segments, _tencent_code
        code = _tencent_code(Symbol.parse("002361", "CN"))
        assert code is not None
        seg = _fetch_trend_segments(code)
        assert seg is not None
        assert "tail_delta" in seg
        assert "direction" in seg


class TestCompositeSignal:
    def test_strong_buy(self):
        dims = {
            "active_net": {"direction": "主动买优"},
            "big_order_net": {"direction": "大单买优"},
            "trend": {"direction": "尾盘流入"},
            "low_price": {"low_ratio": 0.45},
        }
        assert "吸筹" in _composite_signal(dims)

    def test_strong_sell(self):
        dims = {
            "active_net": {"direction": "主动卖优"},
            "big_order_net": {"direction": "大单卖优"},
            "trend": {"direction": "尾盘流出"},
            "low_price": {"low_ratio": 0.2},
        }
        assert "出货" in _composite_signal(dims)

    def test_conflict(self):
        dims = {
            "active_net": {"direction": "主动买优"},
            "big_order_net": {"direction": "大单买优"},
            "trend": {"direction": "尾盘流出"},
            "low_price": {"low_ratio": 0.3},
        }
        assert "分歧" in _composite_signal(dims)
