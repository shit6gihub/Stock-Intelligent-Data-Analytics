"""行情实时推送(2026-08-12): 自选股行情 WebSocket 推送, 免手动刷新/轮询。

实现: 后台线程每 5s 批量拉取(腾讯批量接口)自选股行情 → 广播给所有 WebSocket 订阅者。
前端 WebSocket 连 /api/quotes/ws, 收到 JSON 后更新持仓/自选列表现价。

⚠️ 2026-08-12 踩坑记录: 曾用 HTTP SSE(StreamingResponse + while True 生成器),
   在 uvicorn 0.52 + FastAPI 0.141 下无限循环生成器挂起(连接无首字节, 有限循环正常)。
   原因疑似 Starlette 流式响应与永续 await 的交互 bug。改用 WebSocket 绕开, 稳定。
"""
import asyncio
import json
import logging
import threading
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# 订阅者集合: {id: asyncio.Queue}(同一 event loop 内使用)
_subscribers: dict[int, asyncio.Queue] = {}
_subscribers_lock = threading.Lock()
_next_id = 0

# 聚合器状态
_agg_running = False
_agg_interval_s = 5.0
_last_snapshot: dict = {}


def subscribe() -> tuple[int, asyncio.Queue]:
    """注册订阅者, 返回 (id, queue)。必须在 event loop 线程内调用。"""
    global _next_id
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    with _subscribers_lock:
        _next_id += 1
        sid = _next_id
        _subscribers[sid] = q
    _ensure_aggregator()
    # 新订阅者立即收到当前快照
    if _last_snapshot:
        try:
            q.put_nowait({"type": "snapshot", "data": _last_snapshot})
        except Exception:
            pass
    return sid, q


def unsubscribe(sid: int):
    with _subscribers_lock:
        _subscribers.pop(sid, None)


def _broadcast(payload: dict):
    """广播给所有订阅者(跨线程: 聚合器线程 → 订阅者队列)。"""
    with _subscribers_lock:
        subs = list(_subscribers.items())
    for sid, q in subs:
        try:
            if q.full():
                try:
                    q.get_nowait()
                except Exception:
                    pass
            q.put_nowait(payload)  # asyncio.Queue put_nowait 底层原子, 跨线程可用
        except Exception:
            pass


def _ensure_aggregator():
    """确保聚合器线程已启动(幂等)。"""
    global _agg_running
    if _agg_running:
        return
    _agg_running = True
    t = threading.Thread(target=_aggregator_loop, name="quote-stream-agg", daemon=True)
    t.start()


def _aggregator_loop():
    """聚合器主循环: 每 5s 拉一次自选股行情, 广播。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        try:
            symbols = _collect_watchlist_symbols()
            if symbols:
                data = loop.run_until_complete(_fetch_batch_quotes(symbols))
                if data:
                    _last_snapshot.update(data)
                    _broadcast({"type": "quotes", "data": data, "ts": time.time()})
        except Exception as e:
            logger.warning(f"行情聚合器异常: {e}")
        time.sleep(_agg_interval_s)


def _collect_watchlist_symbols() -> dict[str, list[str]]:
    """从 DB 收集启用账户持仓(按市场分组)。"""
    try:
        from src.web.database import SessionLocal
        from src.web.models import Position, Stock, Account

        db = SessionLocal()
        try:
            pos_symbols = (
                db.query(Stock.symbol, Stock.market)
                .join(Position, Position.stock_id == Stock.id)
                .join(Account, Account.id == Position.account_id)
                .filter(Account.enabled == True)  # noqa: E712
                .all()
            )
            groups: dict[str, list[str]] = defaultdict(list)
            for sym, mkt in pos_symbols:
                groups.setdefault(mkt or "CN", []).append(sym)
            return dict(groups)
        finally:
            db.close()
    except Exception:
        return {}


async def _fetch_batch_quotes(groups: dict[str, list[str]]) -> dict:
    """按市场分组批量拉行情, 返回 {symbol: {price, change_pct, prev_close, name}}。"""
    from src.core.marketdata_client import md_quote_rows

    out: dict = {}
    for market, symbols in groups.items():
        try:
            rows = await asyncio.to_thread(md_quote_rows, symbols, market)
            for item in rows:
                sym = item.get("symbol")
                if not sym:
                    continue
                out[sym] = {
                    "price": item.get("current_price"),
                    "change_pct": item.get("change_pct"),
                    "prev_close": item.get("prev_close"),
                    "name": item.get("name"),
                }
        except Exception as e:
            logger.debug(f"批量行情 {market} 失败: {e}")
    return out


async def websocket_quote_handler(websocket):
    """WebSocket 端点实现: 连接后持续推送行情帧。

    2026-08-12 auth: 路由级 HTTPBearer 对 WS 握手 500, 这里从 query 取 token 自校验。
    前端连 ws://host/api/quotes/ws?token=<jwt>。
    """
    try:
        token = websocket.query_params.get("token", "")
        from src.web.api.auth import decode_token
        if not token or not decode_token(token):
            await websocket.close(code=4401, reason="unauthorized")
            return
    except Exception:
        try:
            await websocket.close(code=4401, reason="unauthorized")
        except Exception:
            pass
        return

    await websocket.accept()
    sid, q = subscribe()
    try:
        while True:
            payload = await q.get()
            await websocket.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass
    finally:
        unsubscribe(sid)


# 2026-08-12: 模块 import 即启动聚合器(不等第一个订阅者), 保证快照始终有数据
_ensure_aggregator()
