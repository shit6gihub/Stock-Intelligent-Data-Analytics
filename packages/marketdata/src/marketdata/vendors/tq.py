"""通达信TQ行情 vendor(本机网关 http://127.0.0.1:5100, JSON-RPC)。

链路: PanWatch(容器, host网络可达宿主127.0.0.1:5100) → frps(云7100/5100)
      → 小主机frpc → 通达信客户端自带TQ HTTP服务(127.0.0.1:17709)。

实测延迟(上海生产机): 快照/扩展指标 ~27-30ms, K线(10只×250日) ~48ms,
并发10路单次中位67ms — 全部远优于腾讯/东财 HTTP 爬源。
仅 CN 市场可用; 客户端未开时接口连接失败 → Engine 自动降级下一源。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from marketdata.symbol import Market, Symbol
from marketdata.types import Bar, Quote
from marketdata.vendors.base import KlineVendor, QuoteVendor

logger = logging.getLogger(__name__)

_TQ_URL = "http://172.18.0.1:5100/"  # 容器内宿主网关(panwatch-net); 宿主本机为 127.0.0.1:5100
_TIMEOUT_S = 4.0  # 正常 <100ms; 隧道断开时快速失败交给降级链


def _rpc(method: str, params: dict, timeout: float = _TIMEOUT_S):
    """发 JSON-RPC; 返回 result.Value 或抛异常(Engine 捕获后转下一源)。"""
    body = json.dumps({"id": 1, "method": method, "params": params}, ensure_ascii=False).encode("utf-8")
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(_TQ_URL, content=body, headers={"Content-Type": "application/json; charset=utf-8"})
        resp.raise_for_status()
        data = json.loads(resp.content.decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"TQ rpc error: {data['error']}")
    result = data.get("result") or {}
    # 快照类直接平铺在 result 里(ErrorId 字段共存); 列表/K线在 result.Value
    err = str(result.get("ErrorId", "0"))
    if err not in ("0", "") and "Value" in result or (err not in ("0", "") and "Value" not in result):
        raise RuntimeError(f"TQ {method} ErrorId={err}: {result.get('Error', '')}")
    return result.get("Value", result)


def _to_float(v) -> float | None:
    try:
        f = float(str(v).strip())
        return f
    except Exception:  # noqa: BLE001
        return None


def to_tq_code(sym: Symbol) -> str | None:
    """CN 代码 → TQ 格式(600519.SH / 000001.SZ / 430047.BJ); 非 CN 返回 None。"""
    code = sym.code.strip()
    if sym.market != Market.CN or len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("6", "9", "5")):
        return f"{code}.SH"
    if code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    return f"{code}.SZ"


class TqQuoteVendor(QuoteVendor):
    name = "tq"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        out: list[Quote] = []
        for sym in symbols:
            tqc = to_tq_code(sym)
            if not tqc:
                continue
            v = _rpc("get_market_snapshot", {"stock_code": tqc})
            if not isinstance(v, dict) or not v:
                continue
            now = _to_float(v.get("Now"))
            if now is None:
                continue
            last_close = _to_float(v.get("LastClose")) or 0.0
            change_amount = round(now - last_close, 4) if last_close else None
            change_pct = round((now - last_close) / last_close * 100, 4) if last_close else None
            buys = [_to_float(x) for x in (v.get("Buyp") or [])]
            sellv_total = sum((_to_float(x) or 0.0) for x in (v.get("Sellv") or []))
            buyv_total = sum((_to_float(x) or 0.0) for x in (v.get("Buyv") or []))
            inside = _to_float(v.get("Inside"))
            outside = _to_float(v.get("Outside"))
            out.append(
                Quote(
                    symbol=sym.code,
                    market="CN",
                    name="",  # TQ快照不带名称, 上层已有名称映射; 不猜名
                    current_price=now,
                    prev_close=last_close or None,
                    open_price=_to_float(v.get("Open")),
                    high_price=_to_float(v.get("Max")),
                    low_price=_to_float(v.get("Min")),
                    change_amount=change_amount,
                    change_pct=change_pct,
                    volume=_to_float(v.get("Volume")),
                    turnover=_to_float(v.get("Amount")),
                    volume_inner=(inside if inside is not None else None),
                    volume_outer=(outside if outside is not None else None),
                    quote_time=datetime.now(ZoneInfo("Asia/Shanghai")),
                )
            )
            del buys, sellv_total, buyv_total  # 五档暂不入库, 留待盘口面板专用通道
        return out


class TqKlineVendor(KlineVendor):
    name = "tq"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        if not symbols:
            return []
        sym = symbols[0]
        tqc = to_tq_code(sym)
        if not tqc:
            return []
        try:
            days = int(config.get("days") or 120)
        except Exception:  # noqa: BLE001
            days = 120
        days = min(max(days, 1), 800)
        # TQ 冷缓存只返回最新1根 → 先刷新K线缓存(实测刷新后 count 生效)
        try:
            _rpc("refresh_kline", {"stock_list": [tqc], "period": "1d"}, timeout=_TIMEOUT_S)
        except Exception:  # noqa: BLE001  刷新失败不阻塞, 直接尝试取数
            pass
        v = _rpc(
            "get_market_data",
            {
                "stock_list": [tqc],
                "period": "1d",
                "count": days,
                "dividend_type": "front",
            },
            timeout=max(_TIMEOUT_S, 15.0),
        )
        rows = (v or {}).get(tqc) if isinstance(v, dict) else None
        if not rows:
            return []
        dates = rows.get("Date") or []
        opens = rows.get("Open") or []
        closes = rows.get("Close") or []
        highs = rows.get("High") or []
        lows = rows.get("Low") or []
        volumes = rows.get("Volume") or []
        out: list[Bar] = []
        for i, d in enumerate(dates):
            try:
                out.append(
                    Bar(
                        date=str(d),
                        open=float(opens[i]),
                        close=float(closes[i]),
                        high=float(highs[i]),
                        low=float(lows[i]),
                        volume=float(volumes[i]) if i < len(volumes) else 0.0,
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        return out
