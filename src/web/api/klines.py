from fastapi import APIRouter, HTTPException
from datetime import datetime

from pydantic import BaseModel, Field

from src.collectors.kline_collector import KlineCollector
from src.models.market import MarketCode

router = APIRouter()


class KlineItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")
    days: int | None = Field(default=60, description="K线天数")
    interval: str | None = Field(default="1d", description="周期: 1d/1w/1m")


class KlineBatchRequest(BaseModel):
    items: list[KlineItem]


class KlineSummaryItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class KlineSummaryBatchRequest(BaseModel):
    items: list[KlineSummaryItem]


def _parse_market(market: str) -> MarketCode:
    # 兼容数据源返回的交易所代码(SH/SZ/BJ 均属 A股 CN 市场)
    if (market or "").upper() in ("SH", "SZ", "BJ"):
        market = "CN"
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支持的市场: {market}")


def _serialize_klines(klines) -> list[dict]:
    return [
        {
            "date": k.date,
            "open": k.open,
            "close": k.close,
            "high": k.high,
            "low": k.low,
            "volume": k.volume,
        }
        for k in klines
    ]


def _aggregate_klines(klines, interval: str) -> list:
    """Aggregate daily klines to week/month."""

    iv = (interval or "1d").lower()
    if iv in ("1d", "day", "d"):
        return klines
    if iv not in ("1w", "1m", "week", "month", "w", "m"):
        return klines

    parsed = []
    for k in klines or []:
        try:
            dt = datetime.strptime(k.date, "%Y-%m-%d")
        except Exception:
            continue
        parsed.append((dt, k))

    parsed.sort(key=lambda x: x[0])
    buckets: dict[str, list] = {}
    for dt, k in parsed:
        if iv in ("1w", "week", "w"):
            y, w, _ = dt.isocalendar()
            key = f"{y:04d}-W{w:02d}"
        else:
            key = f"{dt.year:04d}-{dt.month:02d}"
        buckets.setdefault(key, []).append((dt, k))

    out = []
    for _, items in buckets.items():
        items.sort(key=lambda x: x[0])
        first = items[0][1]
        last = items[-1][1]
        high = max(it[1].high for it in items)
        low = min(it[1].low for it in items)
        vol = sum(it[1].volume for it in items)
        out.append(
            type(first)(
                date=items[-1][0].strftime("%Y-%m-%d"),
                open=first.open,
                close=last.close,
                high=high,
                low=low,
                volume=vol,
            )
        )
    out.sort(key=lambda k: k.date)
    return out


@router.get("/{symbol}")
def get_klines(symbol: str, market: str = "CN", days: int = 60, interval: str = "1d"):
    """获取单只股票/指数K线数据(指数代码自动识别,走指数K线源)"""
    market_code = _parse_market(market)
    # 指数识别: 已知指数代码 → 走指数K线(支持大盘详情页复用 InteractiveKline)
    from src.web.api.market import MARKET_INDICES

    is_index = any(idx["symbol"] == symbol for idx in MARKET_INDICES)
    if is_index:
        # 云服务器东财必失败(502) → 直接腾讯K线,避免每次白等 10s 超时
        import requests as _req
        import logging
        _log_k = logging.getLogger(__name__)

        idx_conf = next((i for i in MARKET_INDICES if i["symbol"] == symbol), None)
        tencent_code = idx_conf.get("tencent_symbol", "") if idx_conf else ""
        try:
            # 2026-08-13 修复: 裸 requests.get(timeout=8) 同步阻塞 asyncio 事件循环,
            # 海外节点 web.ifzq.gtimg.cn 偶发连接挂起(43.154.254.x HK CDN) → 事件循环堵死 → 全站无响应。
            # 改走 market_http: 短超时(5s)+ 退避重试, 失败快速抛错(不长时间卡住)。
            from src.collectors.market_http import market_get
            raw_resp = market_get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                host_key="web.ifzq.gtimg.cn",
                params={"param": f"{tencent_code},day,,,{days},qfq"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
                retries=1,
                parse="json",
                symbol=symbol,
                log_label="腾讯指数K线",
            )
            if raw_resp is None:
                raise RuntimeError(f"腾讯指数K线请求失败({tencent_code})")
            d = raw_resp
            data = (d.get("data") or {}).get(tencent_code) or {}
            bars = data.get("day") or data.get("qfqday") or []
            if not bars:
                raise RuntimeError(f"腾讯指数K线返回空 bars({tencent_code},msg={d.get('msg')})")
            from src.collectors.kline_collector import KlineData

            raw = [
                KlineData(
                    date=b[0],
                    open=float(b[1]),
                    close=float(b[2]),
                    high=float(b[3]),
                    low=float(b[4]),
                    volume=float(b[5]) if len(b) > 5 else 0,
                )
                for b in bars
            ]
            klines = _aggregate_klines(raw, interval)
            return {
                "symbol": symbol,
                "market": market_code.value,
                "days": days,
                "interval": interval,
                "klines": _serialize_klines(klines),
                "is_index": True,
            }
        except Exception as e:
            # ⚠️ 指数K线失败必须显式 fail: 否则会回退到股票K线分支,
            # 导致"上证指数"页面显示平安银行数据(代码 000001 都是它)
            _log_k.error(f"指数K线获取失败({symbol}/{tencent_code}): {e}")
            raise HTTPException(503, f"指数K线不可用({symbol}): {e}")

    collector = KlineCollector(market_code)
    klines = collector.get_klines(symbol, days=days)
    klines = _aggregate_klines(klines, interval)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "days": days,
        "interval": interval,
        "klines": _serialize_klines(klines),
    }


@router.post("/batch")
def get_klines_batch(payload: KlineBatchRequest):
    """批量获取K线数据"""
    if not payload.items:
        return []

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        collector = KlineCollector(market_code)
        days = item.days or 60
        interval = item.interval or "1d"
        klines = collector.get_klines(item.symbol, days=days)
        klines = _aggregate_klines(klines, interval)
        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "days": days,
                "interval": interval,
                "klines": _serialize_klines(klines),
            }
        )

    return results


@router.get("/{symbol}/summary")
def get_kline_summary(symbol: str, market: str = "CN"):
    """获取单只股票K线摘要"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)
    summary = collector.get_kline_summary(symbol)
    # 主力意图+筹码(2026-08-11): A股附加, 供前端个股窗口独立展示
    main_intent = None
    main_intent_structured = None
    if market_code.value == "CN":
        try:
            # 2026-08-12 性能优化: 一次 compute_dark_flow 同时产出字符串+结构化,
            # 避免 summary+structured 各调一次(逐笔翻页/分价表各跑一遍)
            from src.agents.intraday_monitor import _main_intent_both
            main_intent, main_intent_structured = _main_intent_both(symbol)
        except Exception:
            try:
                from src.agents.intraday_monitor import _main_intent_summary
                main_intent = _main_intent_summary(symbol)
            except Exception:
                main_intent = None
            try:
                from src.agents.intraday_monitor import _main_intent_structured
                main_intent_structured = _main_intent_structured(symbol)
            except Exception:
                main_intent_structured = None
    return {
        "symbol": symbol,
        "market": market_code.value,
        "summary": summary,
        "main_intent": main_intent,
        "main_intent_structured": main_intent_structured,
    }


@router.post("/summary/batch")
def get_kline_summary_batch(payload: KlineSummaryBatchRequest):
    """批量获取K线摘要"""
    if not payload.items:
        return []

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        collector = KlineCollector(market_code)
        summary = collector.get_kline_summary(item.symbol)
        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "summary": summary,
            }
        )

    return results
