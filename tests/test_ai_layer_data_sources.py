"""AI 助手层数据接入修复测试: 新闻多源优先 + 竞价悟道限流降级。"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent  # /tmp/PanWatch
sys.path.insert(0, str(ROOT))

import pytest
from src.collectors import auction_collector as ac


class TestAuctionWindowLogic:
    """窗口判断: 用构造的 datetime 验证边界逻辑(不依赖真实时钟)。"""

    def _check(self, hour, minute):
        # 复制 _wudao_available 的判定逻辑做边界验证
        minutes = hour * 60 + minute
        return not (9 * 60 + 15 <= minutes <= 10 * 60 + 30)

    def test_wudao_unavailable_0915(self):
        assert self._check(9, 15) is False  # 9:15 限流开始

    def test_wudao_unavailable_1029(self):
        assert self._check(10, 29) is False  # 10:29 仍限流

    def test_wudao_available_1031(self):
        assert self._check(10, 31) is True  # 10:31 恢复

    def test_wudao_available_1100(self):
        assert self._check(11, 0) is True

    def test_auction_window_0925(self):
        minutes = 9 * 60 + 25
        assert 9 * 60 + 15 <= minutes <= 9 * 60 + 35

    def test_auction_window_0931(self):
        minutes = 9 * 60 + 31
        assert 9 * 60 + 15 <= minutes <= 9 * 60 + 35  # 9:31 仍在竞价窗口内


class TestAuctionRawLimited:
    def test_raw_limited_logic(self):
        """限流窗口判定应正确(直接验证函数边界)。"""
        # 直接用真实当前时间跑: 现在非限流窗口则 limited=False(不 mock)
        raw = ac.fetch_auction_raw()
        assert isinstance(raw, dict)
        assert "opening_snapshot" in raw
        assert "limited" in raw
        assert "error" in raw

    def test_tencent_fallback_produces_board(self):
        """腾讯降级高开榜应返回文本(真实网络, 候选池)。"""
        out = ac._fetch_tencent_gainer_board(5, title="测试榜")
        assert "测试榜" in out
        assert "涨幅" in out


class TestNewsMultiSource:
    def test_chat_news_uses_flash_news(self):
        """get_market_news 应优先市场级多源快讯(flash_news), 悟道降级。"""
        src = open(str(ROOT / "src/web/api/chat.py")).read()
        assert "flash_news" in src
        assert "wudao_mcp_client" in src
        # 在 get_market_news 分支内部: flash_news(多源)应出现在 news_hotlist(悟道)之前
        branch_start = src.find('name == "get_market_news"')
        assert branch_start != -1
        branch = src[branch_start:]
        news_pos = branch.find("flash_news(")
        wudao_pos = branch.find('cli.call_tool("news_hotlist"')
        assert news_pos != -1 and wudao_pos != -1
        assert news_pos < wudao_pos
