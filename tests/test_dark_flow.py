"""暗盘资金计算器测试 v5(2026-08-11): 三分类 + 大单/暗盘分层 + 分价表价位维度。"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent  # /tmp/PanWatch
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/marketdata/src"))

import pytest
from marketdata import Symbol
from src.core.dark_flow import compute_dark_flow, _judge_signal, _fetch_all_ticks, _tencent_code


class TestDarkFlowV5:
    def test_real_compute(self):
        """真实数据: 002361 应返回完整结构。"""
        r = compute_dark_flow(Symbol.parse("002361", "CN"))
        assert r is not None
        assert "dark_net" in r and "signal" in r
        assert "big_net" in r and "small_net" in r
        assert "segments" in r and "strong_buy_zones" in r
        assert r["tick_count"] > 100

    def test_tick_full_coverage(self):
        """逐笔应覆盖全天(尾盘段有数据)。"""
        code = _tencent_code(Symbol.parse("002361", "CN"))
        assert code is not None
        ticks = _fetch_all_ticks(code)
        assert len(ticks) > 1000
        assert any(tk["t"] >= "14:30" for tk in ticks)

    def test_amount_matches_daily(self):
        """逐笔总金额应≈全天成交额。"""
        from marketdata.vendors.tencent import TencentQuoteVendor
        code = _tencent_code(Symbol.parse("002361", "CN"))
        assert code is not None
        ticks = _fetch_all_ticks(code)
        total = sum(tk["amt"] for tk in ticks)
        q = TencentQuoteVendor().fetch([Symbol.parse("002361", "CN")], {})[0]
        turnover = q.turnover or 0
        if turnover > 0:
            assert abs(total - turnover) / turnover < 0.05

    def test_price_zones(self):
        """分价表吸筹/抛压区应存在(神剑: 低位强买区+开盘抛压)。"""
        r = compute_dark_flow(Symbol.parse("002361", "CN"))
        assert r is not None
        assert "strong_buy_zones" in r
        # 竞买率应合理(0-100)
        for z in r["strong_buy_zones"]:
            assert 0 <= z["ratio"] <= 100


class TestJudgeSignal:
    def test_inflow_tail(self):
        assert "吸筹" in _judge_signal(8000e4, 0, 0, {"tail": 2000e4, "morning": 0, "mid": 0, "afternoon": 0}, 0.4, [{"price": 11.0}], [])

    def test_outflow(self):
        assert "流出" in _judge_signal(-9000e4, 0, 0, {"tail": -1000e4, "morning": 0, "mid": 0, "afternoon": 0}, 0.3, [], [])

    def test_watch(self):
        assert "观望" in _judge_signal(100e4, 0, 0, {"tail": 0, "morning": 0, "mid": 0, "afternoon": 0}, 0.3, [], [])
