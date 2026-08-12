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
        """真实数据: 002361 应返回完整结构(盘中/盘后均可)。"""
        r = compute_dark_flow(Symbol.parse("002361", "CN"))
        assert r is not None
        assert "dark_net" in r and "signal" in r
        assert "big_net" in r and "small_net" in r
        assert "segments" in r and "strong_buy_zones" in r
        # 2026-08-12: 弹性断言 —— 盘中刚开盘可能只有几十笔, 盘后才是全天量
        assert r["tick_count"] > 0

    def test_tick_full_coverage(self):
        """逐笔应覆盖交易时段(盘后含尾盘, 盘中至少非空)。"""
        code = _tencent_code(Symbol.parse("002361", "CN"))
        assert code is not None
        ticks = _fetch_all_ticks(code)
        assert len(ticks) > 0
        # 盘后(15:30+)才要求全天覆盖; 盘中只要求有数据
        import datetime
        now = datetime.datetime.now().strftime("%H:%M")
        if now >= "15:30" or now < "09:25":
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
        assert "吸筹" in _judge_signal(8000e4, 8000e4, 3000e4, 5000e4, -5000e4,
                                       {"tail": 2000e4, "morning": 0, "mid": 0, "afternoon": 0}, 0.4, [{"price": 11.0}], [])

    def test_outflow(self):
        assert "流出" in _judge_signal(-9000e4, -9000e4, -5000e4, -4000e4, 5000e4,
                                       {"tail": -1000e4, "morning": 0, "mid": 0, "afternoon": 0}, 0.3, [], [])
        # 净流出但参与度高 = 洗盘吸筹
        assert "吸筹" in _judge_signal(-9000e4, -9000e4, -5000e4, -4000e4, 5000e4,
                                       {"tail": -1000e4, "morning": 0, "mid": 0, "afternoon": 0}, 0.3, [], [],
                                       0, 0, 40, 50)

    def test_watch(self):
        assert "平衡" in _judge_signal(100e4, 100e4, 50e4, 50e4, 0,
                                       {"tail": 0, "morning": 0, "mid": 0, "afternoon": 0}, 0.3, [], [])
