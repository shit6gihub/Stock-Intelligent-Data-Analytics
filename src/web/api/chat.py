"""AI 对话 API 端点。"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.ai_client import AIClient
from src.models.market import MarketCode
from src.web.database import SessionLocal, get_db
from src.web.models import (
    AIModel,
    AIService,
    AnalysisHistory,
    ChatConversation,
    ChatMessage,
    PaperTradingPosition,
    Position,
    Stock,
    StockSuggestion,
)

logger = logging.getLogger(__name__)
router = APIRouter()

SYSTEM_PROMPT = """你是 PanWatch 的 AI 投资助手。

你可以使用工具获取用户的投资数据。当用户的问题涉及具体数据时，主动调用工具获取，不要让用户自己提供。

规则：
- 需要数据时主动调用工具，不要反问用户要数据
- 基于工具返回的实时数据回答，不编造价格等具体数据
- 给出明确的观点和理由
- 涉及买卖建议时说明风险
- 用中文回答
- 保持简洁，避免冗余
- 用户问「新闻 / 资讯 / 热点 / 今天有什么消息」类问题时，必须调用 get_market_news 工具获取实时资讯热榜与每日简报，再基于返回内容回答；严禁在不调用工具的情况下凭记忆编造新闻、题材或资金流向。若工具返回为空，如实说明「暂无实时资讯数据」并建议盘后重试。"""

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5

# ──────────────── Tool Definitions ────────────────

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "获取用户的实盘持仓和模拟盘持仓。用于回答持仓相关问题（持仓健康吗、该调仓吗、盈亏情况等）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "获取某只股票的实时行情（价格、涨跌幅、成交量等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600519"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical_analysis",
            "description": "获取股票的技术面分析（趋势、MACD、RSI、支撑位、压力位等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_suggestions",
            "description": "获取某只股票最近的 AI 建议和分析报告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_watchlist",
            "description": "获取用户的自选股（关注列表）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_capital_flow",
            "description": "获取某只 A 股的主力资金流向（主力净流入、超大单/大单/中单/小单净流入、5日主力净流入趋势）。用于回答资金面问题（主力在吸筹还是出货、资金流向如何等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 002361"},
                    "market": {"type": "string", "description": "市场代码：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tdx_wenda",
            "description": "通达信问小达：自然语言投研查询。用于回答市场级的选股/排行/资金流向问题，如“今日主力净流入前10的A股”“今日涨幅前10的概念板块”“近3日主力净流入前10的半导体”“今日涨停家数最多的概念板块”“今日龙虎榜机构净买入前10”。适合不指定个股、而是看板块/全市场维度的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "自然语言查询，如 “今日主力净流入前10的A股”"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_news",
            "description": "获取市场资讯与新闻：聚合全网/财经媒体热点话题(news_hotlist)与 AI 生成的每日市场简报(briefings, 早/午/收盘/晚盘)。用于回答「有什么新闻/资讯/热点」「今天消息面」「近期题材催化」等新闻资讯类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "资讯热榜返回条数，默认 15", "default": 15},
                    "briefing_type": {"type": "string", "description": "每日简报类型：morning(早盘)/noon(午盘)/close(收盘)/evening(晚间)，不填则返回全部", "default": ""},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kline_patterns",
            "description": "识别某只股票的K线组合形态（金针探底/双针探底/红三兵/涨停双响炮/揭竿而起/上升三法/小步上扬/放量突破/三只乌鸦/空方炮/黄昏之星等）。基于同花顺K线形态教学体系。用于回答「XX股票K线什么形态」「有没有金针探底/红三兵」「技术形态怎么样」等问题。返回形态名称+信号方向+特征描述。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 600519"},
                    "market": {"type": "string", "description": "市场：CN(默认)/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_auction_data",
            "description": "获取A股集合竞价数据（9:25后当日竞价已生成）：竞价全景（竞价涨停/跌停/涨停委买额/成交额/昨炸板反馈/昨涨停反馈）、竞价最强个股（按bidStrength/金额/涨幅）、竞价主线题材、弱转强/被核风险。用于回答「集合竞价怎么样」「今天竞价最强是谁」「竞价主线是什么」「竞价有超预期的吗」「昨高标被核了吗」「今天弱转强的是谁」等竞价相关问题。注意：9:25前当日竞价数据未生成，会明确提示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "string", "description": "竞价场景：overview(竞价全景，默认)/strongest(最强个股)/theme(主线题材)/weak_to_strong(弱转强)/risk(被核风险)/watchlist(盯盘名单)"},
                    "limit": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
                },
                "required": [],
            },
        },
    },
]


def _build_watchlist_context(db: Session) -> str:
    """构建用户自选股列表。"""
    stocks = db.query(Stock).order_by(Stock.sort_order.asc()).all()
    if not stocks:
        return "用户暂无自选股。"
    lines = [f"- {s.name}({s.market}:{s.symbol})" for s in stocks]
    return "自选股列表：\n" + "\n".join(lines)


async def _execute_tool(db: Session, name: str, args: dict) -> str:
    """执行工具调用，返回结果文本。"""
    try:
        if name == "get_portfolio":
            result = _build_portfolio_context(db)
            return result or "用户暂无持仓。"
        elif name == "get_stock_quote":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_realtime_context(symbol, market)
            return result or f"未能获取 {market}:{symbol} 的行情数据。"
        elif name == "get_technical_analysis":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_technical_context(symbol, market)
            return result or f"未能获取 {market}:{symbol} 的技术面数据。"
        elif name == "get_stock_suggestions":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = _build_stock_context(db, symbol, market)
            return result or f"暂无 {market}:{symbol} 的 AI 建议。"
        elif name == "get_watchlist":
            return _build_watchlist_context(db)
        elif name == "get_capital_flow":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            result = await _fetch_capital_flow_context(symbol, market)
            return result or f"未能获取 {market}:{symbol} 的资金流向数据。"
        elif name == "tdx_wenda":
            question = (args.get("question") or "").strip()
            if not question:
                return "请提供查询问题, 如 '今日主力净流入前10的A股'。"
            try:
                from marketdata.vendors.tdx import ask_wenda

                res = ask_wenda(question)
                if not res or not isinstance(res, dict):
                    return f"通达信问小达未返回数据: {question}"
                rows = res.get("data") or []
                if not rows:
                    return f"通达信问小达无结果: {question}"
                lines = [f"通达信问小达查询结果「{question}」(共{len(rows)}条):"]
                for r in rows[:15]:
                    if not isinstance(r, dict):
                        continue
                    name = r.get("sec_name") or r.get("name") or ""
                    code = r.get("sec_code") or r.get("code") or ""
                    chg = r.get("chg") or r.get("change_pct") or ""
                    main_net = next(
                        (v for k, v in r.items() if "主力净额" in k or "主力净" in k),
                        "",
                    )
                    line = f"- {code} {name}"
                    if chg:
                        line += f" 涨{chg}%"
                    if main_net:
                        line += f" 主力净额{main_net}"
                    lines.append(line)
                return "\n".join(lines)
            except Exception as e:
                logger.warning(f"TDX 问小达工具失败: {e}")
                return f"通达信问小达查询失败: {e}"
        elif name == "get_market_news":
            limit = int((args.get("limit") or 15))
            briefing_type = (args.get("briefing_type") or "").strip()
            try:
                from src.collectors.wudao_mcp_client import WudaoMCPClient
                cli = WudaoMCPClient()
                cli._initialize()
                parts = []
                try:
                    hot = cli.call_tool("news_hotlist", {"limit": limit})
                    # wudao news_hotlist 返回 {'text': '...'} 或 {'rows':[...]}
                    hot_text = hot.get("text") if isinstance(hot, dict) else ""
                    if hot_text:
                        parts.append("【资讯热榜】\n" + str(hot_text))
                    else:
                        hot_rows = hot.get("rows") or hot.get("data") or hot.get("items") or []
                        if isinstance(hot_rows, dict):
                            hot_rows = hot_rows.get("rows") or hot_rows.get("data") or []
                        if hot_rows:
                            lines = ["【资讯热榜】"]
                            for r in hot_rows[:limit]:
                                if isinstance(r, dict):
                                    title = r.get("title") or r.get("name") or r.get("keyword") or ""
                                    heat = r.get("heat") or r.get("hot") or r.get("count") or ""
                                    tag = r.get("tag") or r.get("source") or ""
                                    line = "- " + str(title)
                                    if tag:
                                        line += " #" + str(tag)
                                    if heat:
                                        line += " (热度" + str(heat) + ")"
                                    lines.append(line)
                            parts.append("\n".join(lines))
                except Exception as e:
                    logger.warning(f"news_hotlist 失败: {e}")
                try:
                    brief_args = {"detailLevel": "digest"}
                    if briefing_type:
                        brief_args["type"] = briefing_type
                    brief = cli.call_tool("briefings", brief_args)
                    brief_text = ""
                    if isinstance(brief, dict):
                        brief_text = brief.get("text") or brief.get("digest") or ""
                    if brief_text:
                        parts.append("【每日简报】\n" + str(brief_text))
                except Exception as e:
                    logger.warning(f"briefings 失败: {e}")
                if not parts:
                    return "暂无实时资讯数据（可能非交易时段或数据源未就绪），建议盘后重试。"
                return "\n\n".join(parts)
            except Exception as e:
                logger.error(f"get_market_news 工具失败: {e}")
                return f"资讯获取失败: {e}"
        elif name == "get_kline_patterns":
            symbol = args.get("symbol", "")
            market = args.get("market", "CN")
            return await _fetch_kline_pattern_context(symbol, market)
        elif name == "get_auction_data":
            scene = args.get("scene", "overview")
            limit = int(args.get("limit", 10) or 10)
            return await _fetch_auction_context(scene, limit)
        else:
            return f"未知工具: {name}"
    except Exception as e:
        logger.error(f"工具执行失败 {name}: {e}")
        return f"工具执行出错: {e}"


class CreateConversationBody(BaseModel):
    stock_symbol: str | None = None
    stock_market: str | None = None
    initial_context: str | None = None


class SendMessageBody(BaseModel):
    content: str


def _get_ai_client(db: Session, model_id: int | None = None) -> AIClient:
    """获取 AI 客户端实例。"""
    model = None
    service = None

    if model_id:
        model = db.query(AIModel).filter(AIModel.id == model_id).first()

    if not model:
        model = db.query(AIModel).filter(AIModel.is_default == True).first()  # noqa: E712

    if not model:
        model = db.query(AIModel).first()

    if model:
        service = db.query(AIService).filter(AIService.id == model.service_id).first()

    if model and service:
        return AIClient(
            base_url=service.base_url,
            api_key=service.api_key,
            model=model.model,
        )

    settings = Settings()
    return AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
    )


def _build_stock_context(db: Session, symbol: str, market: str) -> str:
    """为绑定股票构建上下文摘要。"""
    parts = []

    # 最近建议
    suggestions = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .limit(3)
        .all()
    )
    if suggestions:
        lines = []
        for s in suggestions:
            lines.append(f"- [{s.agent_label or s.agent_name}] {s.action_label}: {s.signal or s.reason or ''}")
        parts.append("最近 AI 建议：\n" + "\n".join(lines))

    # 最近分析报告
    histories = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.stock_symbol == symbol)
        .order_by(AnalysisHistory.created_at.desc())
        .limit(1)
        .all()
    )
    if histories:
        h = histories[0]
        content_preview = (h.content or "")[:500]
        parts.append(f"最近分析（{h.agent_name}, {h.analysis_date}）：\n{content_preview}")

    if not parts:
        return ""
    return "\n\n".join(parts)


def _build_portfolio_context(db: Session) -> str:
    """构建用户全部持仓摘要。"""
    lines: list[str] = []

    # 实盘持仓
    positions = db.query(Position).all()
    if positions:
        real_lines = []
        for p in positions:
            stock = db.query(Stock).filter(Stock.id == p.stock_id).first()
            if not stock:
                continue
            real_lines.append(
                f"- {stock.name}({stock.market}:{stock.symbol}) "
                f"{p.quantity}股 成本{p.cost_price} 风格{p.trading_style or '波段'}"
            )
        if real_lines:
            lines.append("实盘持仓：\n" + "\n".join(real_lines))

    # 模拟盘持仓
    paper_positions = (
        db.query(PaperTradingPosition)
        .filter(PaperTradingPosition.status == "open")
        .all()
    )
    if paper_positions:
        paper_lines = []
        for pp in paper_positions:
            pnl_str = f"浮盈{pp.unrealized_pnl:.1f}" if pp.unrealized_pnl else ""
            paper_lines.append(
                f"- {pp.stock_name or pp.stock_symbol}({pp.stock_market}:{pp.stock_symbol}) "
                f"{pp.quantity}股 入场价{pp.entry_price}"
                f"{f' 止损{pp.stop_loss}' if pp.stop_loss else ''}"
                f"{f' 目标{pp.target_price}' if pp.target_price else ''}"
                f"{f' {pnl_str}' if pnl_str else ''}"
            )
        if paper_lines:
            lines.append("模拟盘持仓：\n" + "\n".join(paper_lines))

    if not lines:
        return ""
    return "\n\n".join(lines)


async def _fetch_realtime_context(symbol: str, market: str) -> str:
    """异步获取实时行情和技术面。"""
    try:
        from src.core.marketdata_client import md_quote_rows
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        rows = await asyncio.to_thread(md_quote_rows, [symbol], mc.value)
        if not rows:
            return ""
        q = rows[0]
        price = q.get("current_price", "--")
        change = q.get("change_pct", "--")
        volume = q.get("volume", "--")
        name = q.get("name", symbol)
        return f"实时行情：{name}（{market}:{symbol}）价格 {price}，涨跌幅 {change}%，成交量 {volume}"
    except Exception as e:
        logger.debug(f"获取实时行情失败: {e}")
        return ""


async def _fetch_technical_context(symbol: str, market: str) -> str:
    """获取技术面摘要。"""
    try:
        from src.core.data_collector import DataCollector

        collector = DataCollector()
        summary = await asyncio.to_thread(
            collector.get_kline_summary, symbol, market
        )
        if not summary or summary.get("error"):
            return ""
        s = summary.get("summary", {})
        trend = s.get("trend", "--")
        macd = s.get("macd_status", "--")
        rsi = s.get("rsi_14", "--")
        support = s.get("support_level", "--")
        resistance = s.get("resistance_level", "--")
        return f"技术面：趋势 {trend}，MACD {macd}，RSI {rsi}，支撑位 {support}，压力位 {resistance}"
    except Exception as e:
        logger.debug(f"获取技术面失败: {e}")
        return ""


async def _fetch_capital_flow_context(symbol: str, market: str) -> str:
    """获取主力资金流向摘要（A股）。"""
    try:
        from src.collectors.capital_flow_collector import CapitalFlowCollector
        from src.models.market import MarketCode

        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        collector = CapitalFlowCollector(mc)
        summary = await asyncio.to_thread(
            collector.get_capital_flow_summary, symbol
        )
        if not summary or summary.get("error"):
            return ""
        # summary 结构: 主力净流入/占比/超大单/大单/中单/小单/5日趋势 等
        parts = []
        for k, v in summary.items():
            if k == "error":
                continue
            parts.append(f"{k}:{v}")
        return f"资金流向：{'，'.join(parts)}"
    except Exception as e:
        logger.debug(f"获取资金流失败: {e}")
        return ""


async def _fetch_kline_pattern_context(symbol: str, market: str) -> str:
    """识别 K 线组合形态(同花顺教学体系 + TA-Lib 标准形态)。"""
    try:
        from src.core.marketdata_client import get_market_data
        from src.core.kline_pattern import detect_patterns, format_patterns
        from src.collectors.kline_collector import _detect_talib_patterns

        md = get_market_data()
        bars = await asyncio.to_thread(md.klines, symbol, market="CN" if market == "CN" else market, days=60)
        if not bars:
            return f"未能获取 {market}:{symbol} 的K线数据。"
        hits = detect_patterns(bars)
        text = format_patterns(hits)
        # TA-Lib 标准形态
        talib_hits = _detect_talib_patterns(list(bars))
        if talib_hits:
            text += "\n\n【TA-Lib 标准形态】"
            for p in talib_hits[:8]:
                text += f"\n- {p['cn_name']}({p['name']}) {p['signal']} 强度{p['strength']}"
        # 附带最近价格信息
        last = bars[-1]
        head = f"{market}:{symbol} 最近K线({last.date}): 开{last.open} 高{last.high} 低{last.low} 收{last.close}\n"
        return head + text
    except Exception as e:
        logger.debug(f"获取K线形态失败: {e}")
        return f"K线形态识别失败: {e}"


async def _fetch_auction_context(scene: str, limit: int = 10) -> str:
    """集合竞价数据(悟道 MCP 竞价工具,9:25 后当日数据就绪)。"""
    try:
        from src.collectors.wudao_mcp_client import WudaoMCPClient

        cli = WudaoMCPClient()
        cli._initialize()
        lines: list[str] = []
        scene = (scene or "overview").strip() or "overview"

        if scene in ("strongest", "watchlist"):
            # 竞价最强个股(市值归一化 bidStrength)
            res = cli.call_tool("auction_market_scan", {"sortBy": "bidStrength", "limit": limit})
            text = res.get("text") if isinstance(res, dict) else ""
            if text:
                lines.append("【竞价最强个股】\n" + str(text))
            else:
                rows = res.get("rows") or res.get("data") or []
                if rows:
                    lines.append("【竞价最强个股】")
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
        elif scene == "theme":
            res = cli.call_tool("auction_theme_signal", {"limit": limit})
            text = res.get("text") if isinstance(res, dict) else ""
            if text:
                lines.append("【竞价主线题材】\n" + str(text))
            else:
                groups = (res.get("data") or res.get("signalGroup") or [])
                if groups:
                    lines.append("【竞价主线题材】")
                    for g in groups[:limit]:
                        if isinstance(g, dict):
                            name = g.get("theme") or g.get("name") or ""
                            desc = g.get("description") or g.get("signal") or ""
                            lines.append(f"- {name}: {desc}")
        elif scene == "weak_to_strong":
            res = cli.call_tool("auction_weak_to_strong", {"limit": limit})
            text = res.get("text") if isinstance(res, dict) else ""
            if text:
                lines.append("【竞价弱转强】\n" + str(text))
            else:
                rows = res.get("rows") or res.get("data") or []
                if rows:
                    lines.append("【竞价弱转强】")
                    for r in rows[:limit]:
                        if isinstance(r, dict):
                            name = r.get("name") or r.get("stockName") or r.get("code") or ""
                            score = r.get("wtsScore") or ""
                            origin = r.get("origin") or ""
                            lines.append(f"- {name} wtsScore:{score} 来源:{origin}")
        elif scene == "risk":
            # 昨高标被核风险
            res = cli.call_tool("auction_limitup_feedback", {"focus": "risk", "groupBy": "streak"})
            text = res.get("text") if isinstance(res, dict) else ""
            if text:
                lines.append("【竞价被核风险】\n" + str(text))
            else:
                summary = (res.get("data") or {}).get("summary") or res.get("summary") or {}
                if summary:
                    break_rate = summary.get("breakRate") or summary.get("break_rate") or ""
                    lines.append(f"【竞价被核风险】炸板率:{break_rate}")
                rows = (res.get("data") or {}).get("risk") or res.get("risk") or []
                if rows:
                    for r in rows[:limit]:
                        if isinstance(r, dict):
                            name = r.get("name") or r.get("stockName") or r.get("code") or ""
                            lines.append(f"- {name}")
        else:
            # overview: 竞价全景(六个桶)
            res = cli.call_tool("auction_opening_snapshot", {"limit": limit})
            text = res.get("text") if isinstance(res, dict) else ""
            if text:
                lines.append("【竞价全景】\n" + str(text))
            else:
                data = res.get("data") or {}
                summary = data.get("summary") or {}
                if summary:
                    lines.append("【竞价全景】")
                    for k, v in summary.items():
                        lines.append(f"- {k}: {v}")
                for bucket in ("limitUpOpen", "limitDownOpen", "topLimitBuyAmount", "topBidAmount", "prevBrokenFeedback", "prevLimitUpFeedback"):
                    rows = data.get(bucket) or []
                    if rows:
                        lines.append(f"  [{bucket}] " + ", ".join(
                            str(r.get("name") or r.get("stockName") or r.get("code") or "") for r in rows[:5] if isinstance(r, dict)
                        ))

        if not lines:
            return "暂无集合竞价数据(9:25 前当日竞价未生成,或数据源未就绪),建议 9:25 后重试。"
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"获取集合竞价失败: {e}")
        return f"集合竞价数据获取失败: {e}"


@router.get("/suggested-questions")
def suggested_questions(
    symbol: str = Query(..., description="股票代码"),
    market: str = Query("CN", description="市场"),
    db: Session = Depends(get_db),
):
    """根据股票当前状态生成推荐问题（纯模板，不调 AI）。"""
    questions: list[str] = []

    # 查最近建议
    latest_suggestion = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .first()
    )
    if latest_suggestion:
        action = (latest_suggestion.action or "").lower()
        label = latest_suggestion.action_label or latest_suggestion.action or ""
        if action in ("buy", "add"):
            questions.append(f"最新的「{label}」信号可靠吗？入场时机如何？")
        elif action in ("sell", "reduce"):
            questions.append(f"最新给出了「{label}」建议，现在该操作吗？")
        elif action == "alert":
            questions.append("最近的异动提醒是什么情况？需要关注吗？")

    # 查持仓（Position 通过 stock_id 关联 Stock 表）
    has_position = (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    ) is not None
    if has_position:
        questions.append("当前持仓该继续持有还是考虑减仓？")
    else:
        questions.append("现在适合建仓吗？")

    # 通用问题
    questions.append("分析近期走势和关键支撑压力位")
    questions.append("有什么值得关注的消息或事件？")

    return {"questions": questions[:5]}


@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody | None = None,
    db: Session = Depends(get_db),
):
    conv = ChatConversation(
        stock_symbol=body.stock_symbol if body else None,
        stock_market=body.stock_market if body else None,
        initial_context=body.initial_context if body else None,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title or "",
        "stock_symbol": conv.stock_symbol,
        "stock_market": conv.stock_market,
        "created_at": str(conv.created_at or ""),
    }


@router.get("/conversations")
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ChatConversation)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title or "",
            "stock_symbol": c.stock_symbol,
            "stock_market": c.stock_market,
            "created_at": str(c.created_at or ""),
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "对话不存在")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return {
        "conversation": {
            "id": conv.id,
            "title": conv.title or "",
            "stock_symbol": conv.stock_symbol,
            "stock_market": conv.stock_market,
            "created_at": str(conv.created_at or ""),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": str(m.created_at or ""),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "对话不存在")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"ok": True}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageBody,
):
    """发送消息并获取 AI 回复。"""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "对话不存在")

        # 保存用户消息
        user_msg = ChatMessage(
            conversation_id=conversation_id,
            role="user",
            content=body.content,
        )
        db.add(user_msg)

        # 更新对话标题（首条消息取前 20 字）
        if not conv.title:
            conv.title = body.content[:20]

        db.commit()
        db.refresh(user_msg)

        # 构建消息列表
        messages_for_ai: list[dict] = []

        # System prompt
        system_content = SYSTEM_PROMPT

        # 绑定股票提示
        if conv.stock_symbol and conv.stock_market:
            system_content += f"\n\n当前对话关联股票：{conv.stock_market}:{conv.stock_symbol}"

        # 前端页面快照（对话创建时传入）
        if conv.initial_context:
            system_content += "\n\n--- 用户页面快照（对话创建时） ---\n" + conv.initial_context

        messages_for_ai.append({"role": "system", "content": system_content})

        # 历史消息
        history = (
            db.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        recent = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
        for m in recent:
            if m.role in ("user", "assistant"):
                messages_for_ai.append({"role": m.role, "content": m.content})

        # 注入基础上下文（持仓 + 绑定股票的行情/建议）
        context_parts: list[str] = []

        # 用户持仓
        portfolio_ctx = _build_portfolio_context(db)
        if portfolio_ctx:
            context_parts.append(portfolio_ctx)

        # 绑定股票的实时数据
        if conv.stock_symbol and conv.stock_market:
            realtime = await _fetch_realtime_context(conv.stock_symbol, conv.stock_market)
            if realtime:
                context_parts.append(realtime)
            technical = await _fetch_technical_context(conv.stock_symbol, conv.stock_market)
            if technical:
                context_parts.append(technical)
            stock_ctx = _build_stock_context(db, conv.stock_symbol, conv.stock_market)
            if stock_ctx:
                context_parts.append(stock_ctx)

        if context_parts:
            # 把上下文追加到 system message
            messages_for_ai[0]["content"] += "\n\n--- 当前数据 ---\n" + "\n\n".join(context_parts)

        # 调用 AI（带 tool use，用于按需获取更多数据）
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                try:
                    response_msg = await ai_client.chat_with_tools(
                        messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                    )
                except Exception:
                    # 模型不支持 tool use → 直接用 chat_multi
                    logger.info("Tool use 不可用，使用普通对话")
                    ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                    break

                if not response_msg.tool_calls:
                    ai_response = response_msg.content or ""
                    break

                # 执行 tool calls
                messages_for_ai.append({
                    "role": "assistant",
                    "content": response_msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in response_msg.tool_calls
                    ],
                })

                for tc in response_msg.tool_calls:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    logger.info(f"Tool call: {tc.function.name}({tool_args})")
                    result = await _execute_tool(db, tc.function.name, tool_args)
                    messages_for_ai.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                ai_response = response_msg.content or "抱歉，处理轮次过多，请精简问题再试。"

        except Exception as e:
            logger.error(f"AI 对话失败: {e}")
            ai_response = f"抱歉，AI 服务暂时不可用：{e}"

        # 保存 AI 回复
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)

        # 更新对话时间
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        return {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "created_at": str(assistant_msg.created_at or ""),
        }
    finally:
        db.close()
