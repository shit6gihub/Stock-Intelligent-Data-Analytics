"""暗盘资金计算器测试 v4(2026-08-11): 腾讯逐笔全天全量 + 时段分解。"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent  # /tmp/PanWatch
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/marketdata/src"))

import pytest
from marketdata import Symbol
from src.core.dark_flow import compute_dark_flow, _judge_signal, _fetch_all_ticks, _tencent_code


class TestDarkFlowV4:
    def test_real_compute(self):
        """真实数据: 002361 应返回完整结构(暗盘/时段/信号)。"""
        r = compute_dark_flow(Symbol.parse("002361", "CN"))
        assert r is not None
        assert "dark_net" in r and "segments" in r and "signal" in r
        assert r["tick_count"] > 100
        # 时段四段齐全
        assert set(r["segments"].keys()) == {"morning", "mid", "afternoon", "tail"}

    def test_tick_full_coverage(self):
        """逐笔应覆盖全天(尾盘段有数据)。"""
        code = _tencent_code(Symbol.parse("002361", "CN"))
        assert code is not None
        ticks = _fetch_all_ticks(code)
        assert len(ticks) > 1000
        # 最后一条应在 14:30 之后(覆盖尾盘)
        assert any(t >= "14:30" for _, _, t in ticks)

    def test_amount_matches_daily(self):
        """逐笔总金额应≈全天成交额(21.32亿 vs 行情21.31亿)。"""
        from marketdata.vendors.tencent import TencentQuoteVendor
        code = _tencent_code(Symbol.parse("002361", "CN"))
        assert code is not None
        ticks = _fetch_all_ticks(code)
        total = sum(amt for _, amt, _ in ticks)
        q = TencentQuoteVendor().fetch([Symbol.parse("002361", "CN")], {})[0]
        turnover = q.turnover or 0
        if turnover > 0:
            assert abs(total - turnover) / turnover < 0.05


class TestJudgeSignal:
    def test_dark_inflow_tail(self):
        assert "吸筹" in _judge_signal(8000e4, {"tail": 2000e4, "morning": 0, "mid": 0, "afternoon": 0})

    def test_dark_outflow(self):
        assert "流出" in _judge_signal(-9000e4, {"tail": -1000e4, "morning": 0, "mid": 0, "afternoon": 0})

    def test_watch(self):
        assert "观望" in _judge_signal(100e4, {"tail": 0, "morning": 0, "mid": 0, "afternoon": 0})
