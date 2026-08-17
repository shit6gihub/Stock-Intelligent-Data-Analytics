"""K线后台入库 worker (2026-08-17)
- 拉腾讯/东财/新浪 3 个数据源,写入 PG klines hypertable
- 盘后收盘作业跑一次(主),盘中盘中 5m 跑一次(可选)
- 入库用 ON CONFLICT DO NOTHING 幂等
- 主源 = tencent,备源 = eastmoney + sina

调用:
- 收盘后跑日K:     python -m src.collectors.klines_ingestor --period 1d --backfill 800
- 盘中跑5m K:       python -m src.collectors.klines_ingestor --period 5m --intraday
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from src.collectors.market_http import fetch_source
from src.collectors.kline_collector import KlineCollector, KlineData
from src.models.market import MarketCode, MARKETS
from src.web.database import DB_URL  # 复用应用 DB 连接

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# 数据源 → 友好名(写入 source 列)
SOURCE_TENCENT = "tencent"
SOURCE_EASTMONEY = "eastmoney"
SOURCE_SINA = "sina"


def _to_db_row(symbol: str, market: str, period: str, source: str, k: KlineData, ts) -> dict:
    """KlineData → klines 表字典。"""
    return {
        "ts": ts,
        "symbol": symbol,
        "market": market,
        "period": period,
        "source": source,
        "open": float(k.open),
        "high": float(k.high),
        "low": float(k.low),
        "close": float(k.close),
        "volume": int(k.volume or 0),
        "quality_flag": 1,
    }


def _fetch_from_source(source: str, symbol: str, market: MarketCode, days: int) -> list[KlineData]:
    """从指定 source 拉 K线。失败返回空 list。"""
    try:
        with fetch_source(source):
            kc = KlineCollector(market)
            klines = kc.get_klines(symbol, days=days)
            return klines
    except Exception as e:
        logger.warning(f"[{source}] {symbol}.{market.value} 拉取失败: {e}")
        return []


async def ingest_symbol(
    db_engine,
    symbol: str,
    market: MarketCode,
    period: str,
    days: int,
) -> dict:
    """拉 1 只股的 K线,3 源各一份入库。返回入库统计。"""
    rows_by_source: dict[str, list[dict]] = {s: [] for s in [SOURCE_TENCENT, SOURCE_EASTMONEY, SOURCE_SINA]}

    # 3 个源并发拉
    tasks = [
        asyncio.to_thread(_fetch_from_source, src, symbol, market, days)
        for src in rows_by_source.keys()
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for src, klines in zip(rows_by_source.keys(), results):
        if not isinstance(klines, list) or not klines:
            continue
        for k in klines:
            # KlineData.date 是 'YYYY-MM-DD', 转为 ts (带时区)
            try:
                ts = datetime.fromisoformat(str(k.date)).replace(tzinfo=timezone.utc)
            except Exception:
                ts = datetime.now(timezone.utc)
            rows_by_source[src].append(_to_db_row(symbol, market.value, period, src, k, ts))

    # 入库
    total = 0
    with db_engine.begin() as conn:
        for src, rows in rows_by_source.items():
            if not rows:
                continue
            # 用 raw SQL 配合 execute + ON CONFLICT DO NOTHING
            # 一次 1 行,确保 ON CONFLICT 走对路径
            for row in rows:
                result = conn.execute(
                    text(
                        "INSERT INTO klines (ts, symbol, market, period, source, "
                        "open, high, low, close, volume, quality_flag) "
                        "VALUES (:ts, :symbol, :market, :period, :source, "
                        ":open, :high, :low, :close, :volume, :quality_flag) "
                        "ON CONFLICT (symbol, market, period, ts, source) DO NOTHING"
                    ),
                    row,
                )
                total += result.rowcount
    return {"symbol": symbol, "ingested": total, "by_source": {s: len(r) for s, r in rows_by_source.items()}}


async def ingest_batch(
    db_engine,
    symbols: list[tuple[str, str]],
    period: str = "1d",
    days: int = 800,
    concurrency: int = 5,
) -> dict:
    """批量入库,限制并发避免打爆数据源。"""
    sem = asyncio.Semaphore(concurrency)

    async def one(sym: str, mkt_str: str):
        async with sem:
            try:
                mkt = MarketCode(mkt_str.upper())
            except ValueError:
                return None
            return await ingest_symbol(db_engine, sym, mkt, period, days)

    tasks = [one(s, m) for s, m in symbols]
    results = await asyncio.gather(*tasks)
    total_ingested = 0
    for r in results:
        if r:
            total_ingested += r["ingested"]
            logger.info(
                f"  {r['symbol']}.{period}: "
                f"ingested={r['ingested']}, "
                f"by_source={r['by_source']}"
            )
    return {"total_ingested": total_ingested}


def get_default_symbols() -> list[tuple[str, str]]:
    """从 users.stocks 表读所有用户的自选股,按 (symbol, market) 去重后返回。

    2026-08-17 修复: 多用户场景下(每用户各自加自选), 同一 (symbol, market) 会出现多行。
    K线是全局数据(主键不含 user_id), 入库会去重 — 但 get_default_symbols 拉取前
    应该先去重, 避免每天 18:00 cron 重复拉同一股 14 次(网络浪费)。
    """
    from src.web.database import SessionLocal
    from src.web.models import Stock

    with SessionLocal() as db:
        rows = db.query(Stock.symbol, Stock.market).all()
        # 用 (symbol, market) 集合去重 — 不同 user 各自加的同一股只算一次
        seen: set[tuple[str, str]] = set()
        result: list[tuple[str, str]] = []
        for r in rows:
            sym = r.symbol
            mkt = r.market.value if hasattr(r.market, "value") else str(r.market)
            key = (sym, mkt)
            if key in seen:
                continue
            seen.add(key)
            result.append(key)
        return result


async def main_async(args):
    db_engine = create_engine(DB_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
    symbols = get_default_symbols()
    logger.info(f"开始入库: {len(symbols)} 只股, period={args.period}, days={args.days}")

    start = time.time()
    result = await ingest_batch(db_engine, symbols, period=args.period, days=args.days)
    elapsed = time.time() - start

    logger.info(
        f"\n✅ 完成: {result['total_ingested']} 行入库 / {elapsed:.1f}s / "
        f"{result['total_ingested']/elapsed:.0f} 行/秒"
    )
    db_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="K线后台入库 worker")
    parser.add_argument("--period", default="1d", help="周期: 1d / 5m / 1m")
    parser.add_argument("--days", type=int, default=800, help="拉几天(默认 800 ≈ 2.2 年)")
    parser.add_argument("--intraday", action="store_true", help="盘中模式:5m K")
    args = parser.parse_args()

    if args.intraday:
        args.period = "5m"
        args.days = 2  # 盘中只拉最近 2 天(避免过大)

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()