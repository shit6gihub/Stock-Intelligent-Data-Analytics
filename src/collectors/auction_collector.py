"""集合竞价采集器(2026-08-11): 悟道优先 + 腾讯批量行情降级。

背景: 悟道 MCP 竞价工具字段独家(consistency/bidStrength/弱转强), 但 9:15-10:30 限流,
恰好撞上竞价复盘(9:26)与盘中助手高频提问窗口。此采集器提供统一入口:
- 主源: 悟道竞价工具(独家字段, 限流窗口外)
- 降级: 腾讯批量行情算"竞价高开榜/竞价最强"(限流窗口内兜底, 不依赖悟道)
- 30s 缓存: 竞价数据生成后短时间内变化小, 避免助手多次提问重复请求

竞价时段判定: 9:15-9:35 之间竞价数据最有意义; 其余时段直接回退腾讯(降级)或提示。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# 腾讯批量拉取的候选池(昨日强势/权重, 降级时从这些票里算竞价高开榜)
AUCTION_FALLBACK_POOL = [
    "600519", "000001", "600036", "601318", "300750", "002594", "000333", "600900",
    "601899", "600030", "000858", "002415", "300059", "601668", "600276", "000725",
    "002475", "300308", "600111", "601012", "603986", "002371", "300124", "688981",
]

_cache: dict[str, tuple[float, str]] = {}


def _cache_get(key: str) -> str | None:
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < 30.0:
        return hit[1]
    return None


def _cache_set(key: str, value: str) -> None:
    _cache[key] = (time.time(), value)


def _is_auction_window() -> bool:
    """9:15-9:35 竞价窗口(竞价数据最有意义的时段)。"""
    now = datetime.now()
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= minutes <= 9 * 60 + 35


def _wudao_available() -> bool:
    """悟道 9:15-10:30 限流, 该窗口内直接跳过悟道(不再白白超时)。"""
    now = datetime.now()
    minutes = now.hour * 60 + now.minute
    return not (9 * 60 + 15 <= minutes <= 10 * 60 + 30)


def fetch_auction_overview(limit: int = 15) -> str:
    """竞价全景: 悟道 opening_snapshot 优先, 降级腾讯高开榜。"""
    cache_key = f"overview:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # 主源: 悟道(限流窗口外)
    if _wudao_available():
        try:
            from src.collectors.wudao_mcp_client import WudaoMCPClient

            cli = WudaoMCPClient()
            cli._initialize()
            res = cli.auction_opening_snapshot(limit=limit)
            text = res.get("text") if isinstance(res, dict) else ""
            if text:
                out = f"【竞价全景】\n{text}"
                _cache_set(cache_key, out)
                return out
            data = res.get("data") or {}
            summary = data.get("summary") or {}
            if summary:
                lines = ["【竞价全景】"]
                for k, v in summary.items():
                    lines.append(f"- {k}: {v}")
                for bucket in ("limitUpOpen", "limitDownOpen", "topLimitBuyAmount",
                               "topBidAmount", "prevBrokenFeedback", "prevLimitUpFeedback"):
                    rows = data.get(bucket) or []
                    if rows:
                        names = [str(r.get("name") or r.get("stockName") or r.get("code") or "")
                                 for r in rows[:5] if isinstance(r, dict)]
                        if names:
                            lines.append(f"  [{bucket}] " + ", ".join(names))
                out = "\n".join(lines)
                _cache_set(cache_key, out)
                return out
        except Exception as e:
            logger.debug(f"悟道竞价全景失败, 降级腾讯: {e}")

    # 降级: 腾讯批量高开榜
    return _fetch_tencent_gainer_board(limit, title="竞价高开榜(腾讯降级)")


def fetch_auction_strongest(limit: int = 10) -> str:
    """竞价最强个股: 悟道 bidStrength 优先, 降级腾讯涨幅榜。"""
    cache_key = f"strongest:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    if _wudao_available():
        try:
            from src.collectors.wudao_mcp_client import WudaoMCPClient

            cli = WudaoMCPClient()
            cli._initialize()
            res = cli.auction_market_scan(sort_by="bidStrength", limit=limit)
            text = res.get("text") if isinstance(res, dict) else ""
            if text:
                out = f"【竞价最强个股】\n{text}"
                _cache_set(cache_key, out)
                return out
            rows = res.get("rows") or res.get("data") or []
            if rows:
                lines = ["【竞价最强个股】"]
                for r in rows[:limit]:
                    if isinstance(r, dict):
                        name = r.get("name") or r.get("stockName") or r.get("code") or ""
                        strength = r.get("bidStrength") or r.get("bidAmountPercentile") or ""
                        amt = r.get("bidAmount") or r.get("limitBuyAmount") or ""
                        line = f"- {name}"
                        if strength:
                            line += f" 强度:{strength}"
                        if amt:
                            line += f" 金额:{amt}"
                        lines.append(line)
                out = "\n".join(lines)
                _cache_set(cache_key, out)
                return out
        except Exception as e:
            logger.debug(f"悟道竞价最强失败, 降级腾讯: {e}")

    return _fetch_tencent_gainer_board(limit, title="竞价最强(腾讯降级)")


def _fetch_tencent_gainer_board(limit: int, title: str) -> str:
    """腾讯批量行情 → 按涨幅排序的高开榜(竞价降级源)。"""
    try:
        from marketdata.vendors.tencent import TencentQuoteVendor
        from marketdata import Symbol

        vendor = TencentQuoteVendor()
        syms = [Symbol.parse(c, "CN") for c in AUCTION_FALLBACK_POOL]
        quotes = vendor.fetch(syms, {})
        rows = []
        for q in quotes:
            d = q.__dict__
            price = d.get("current_price")
            pct = d.get("change_pct")
            if not price or pct is None:
                continue
            rows.append((q.symbol, d.get("name", ""), price, pct))
        rows.sort(key=lambda r: -(r[3] or 0))
        if not rows:
            return "暂无竞价数据(数据源未就绪)"
        lines = [f"【{title}】(候选池{len(rows)}只, 按涨幅排序)"]
        for sym, name, price, pct in rows[:limit]:
            flag = " 🔴" if pct >= 9.5 else (" 🟠" if pct >= 5 else "")
            lines.append(f"- {sym} {name}: {price:.2f} ({pct:+.2f}%){flag}")
        out = "\n".join(lines)
        _cache_set(f"tencent:{limit}", out)
        return out
    except Exception as e:
        logger.warning(f"腾讯竞价降级失败: {e}")
        return f"竞价数据获取失败: {e}"


def fetch_auction_theme(limit: int = 10) -> str:
    """竞价主线题材(悟道独家, 无降级——题材信号没有免费等价源)。"""
    cache_key = f"theme:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    if not _wudao_available():
        return "竞价主线题材暂不可用(悟道限流窗口 9:15-10:30), 10:30 后重试"
    try:
        from src.collectors.wudao_mcp_client import WudaoMCPClient

        cli = WudaoMCPClient()
        cli._initialize()
        res = cli.auction_theme_strength(limit=limit, theme_source="concept")
        text = res.get("text") if isinstance(res, dict) else ""
        if text:
            out = f"【竞价主线题材】\n{text}"
            _cache_set(cache_key, out)
            return out
        groups = res.get("data") or res.get("signalGroup") or []
        if groups:
            lines = ["【竞价主线题材】"]
            for g in groups[:limit]:
                if isinstance(g, dict):
                    name = g.get("theme") or g.get("name") or ""
                    desc = g.get("description") or g.get("signal") or ""
                    lines.append(f"- {name}: {desc}")
            out = "\n".join(lines)
            _cache_set(cache_key, out)
            return out
        return "暂无竞价题材数据"
    except Exception as e:
        logger.debug(f"悟道竞价题材失败: {e}")
        return f"竞价题材获取失败: {e}"


def fetch_auction_weak_to_strong(limit: int = 15) -> str:
    """竞价弱转强(悟道独家, 无降级)。"""
    cache_key = f"wts:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    if not _wudao_available():
        return "竞价弱转强暂不可用(悟道限流窗口 9:15-10:30), 10:30 后重试"
    try:
        from src.collectors.wudao_mcp_client import WudaoMCPClient

        cli = WudaoMCPClient()
        cli._initialize()
        res = cli.auction_weak_to_strong(limit=limit)
        text = res.get("text") if isinstance(res, dict) else ""
        if text:
            out = f"【竞价弱转强】\n{text}"
            _cache_set(cache_key, out)
            return out
        rows = res.get("rows") or res.get("data") or []
        if rows:
            lines = ["【竞价弱转强】"]
            for r in rows[:limit]:
                if isinstance(r, dict):
                    name = r.get("name") or r.get("stockName") or r.get("code") or ""
                    score = r.get("wtsScore") or ""
                    origin = r.get("origin") or ""
                    lines.append(f"- {name} wtsScore:{score} 来源:{origin}")
            out = "\n".join(lines)
            _cache_set(cache_key, out)
            return out
        return "暂无弱转强数据"
    except Exception as e:
        logger.debug(f"悟道弱转强失败: {e}")
        return f"弱转强获取失败: {e}"


def fetch_auction_raw() -> dict:
    """结构化竞价数据(agent 用): 悟道原始 dict 优先, 限流窗口内快速失败不白等。

    返回: {opening_snapshot, theme_strength, market_scan, weak_to_strong,
           limitup_feedback, limited(bool), error}
    限流窗口(9:15-10:30)内不调用悟道(避免 25s 超时), 全部置空并标 limited=True;
    窗口外悟道失败也标 limited=False + error(由调用方降级展示)。
    """
    out: dict = {
        "opening_snapshot": {},
        "theme_strength": {},
        "market_scan": {},
        "weak_to_strong": {},
        "limitup_feedback": {},
        "limited": False,
        "error": "",
    }
    if not _wudao_available():
        out["limited"] = True
        out["error"] = "悟道限流窗口(9:15-10:30), 竞价数据降级"
        return out
    try:
        from src.collectors.wudao_mcp_client import WudaoMCPClient

        client = WudaoMCPClient()
        client._initialize()
        out["opening_snapshot"] = client.auction_opening_snapshot(limit=30) or {}
        out["theme_strength"] = client.auction_theme_strength(limit=10, theme_source="concept") or {}
        out["market_scan"] = client.auction_market_scan(sort_by="bidStrength", limit=10) or {}
        out["weak_to_strong"] = client.auction_weak_to_strong(limit=15) or {}
        out["limitup_feedback"] = client.auction_limitup_feedback(focus="all", group_by="streak") or {}
    except Exception as e:
        out["error"] = str(e)
    return out


def fetch_auction_risk(limit: int = 10) -> str:
    """竞价被核风险(悟道独家, 无降级)。"""
    cache_key = f"risk:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    if not _wudao_available():
        return "竞价被核风险暂不可用(悟道限流窗口 9:15-10:30), 10:30 后重试"
    try:
        from src.collectors.wudao_mcp_client import WudaoMCPClient

        cli = WudaoMCPClient()
        cli._initialize()
        res = cli.auction_limitup_feedback(focus="risk", group_by="streak")
        text = res.get("text") if isinstance(res, dict) else ""
        if text:
            out = f"【竞价被核风险】\n{text}"
            _cache_set(cache_key, out)
            return out
        summary = (res.get("data") or {}).get("summary") or res.get("summary") or {}
        lines = ["【竞价被核风险】"]
        if summary:
            break_rate = summary.get("breakRate") or summary.get("break_rate") or ""
            lines.append(f"- 炸板率:{break_rate}")
        rows = (res.get("data") or {}).get("risk") or res.get("risk") or []
        for r in rows[:limit]:
            if isinstance(r, dict):
                name = r.get("name") or r.get("stockName") or r.get("code") or ""
                lines.append(f"- {name}")
        out = "\n".join(lines)
        _cache_set(cache_key, out)
        return out
    except Exception as e:
        logger.debug(f"悟道被核风险失败: {e}")
        return f"被核风险获取失败: {e}"
