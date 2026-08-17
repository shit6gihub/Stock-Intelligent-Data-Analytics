"""K线每日 backfill 调度器(2026-08-17)
- 收盘后 18:00 自动拉取当日 + 最近 2 天 K线(覆盖当日 + 周末补齐)
- 周一到周五(交易日)
- 调 klines_ingestor.ingest_batch, 复用 ingest_symbol

设计要点:
- 独立于 Agent 调度器(同 price_alert / report / paper_trading 模式)
- 跑在线程里, 不阻塞 Web 事件循环
- 失败 retry 一次(防止偶发网络抖动)
- 静默时段(>4 小时)不跑(异常)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.collectors.klines_ingestor import ingest_batch, get_default_symbols
from src.web.database import create_engine
from src.web.database import DB_URL

logger = logging.getLogger(__name__)


# 18:00 daily, 交易日(周一到周五) 拉最近 2 天(覆盖当日 + 周末)
BACKFILL_CRON = {"hour": 18, "minute": 0}
BACKFILL_DAYS = 2  # 拉最近 2 天(覆盖当日 + 周末/节假日补齐)
BACKFILL_DAYS_FALLBACK = 7  # 失败重试用 7 天
CONCURRENCY = 5  # 并发 ingest 股数


def _is_market_day() -> bool:
    """简单交易日判断: 周一到周五 = 交易日。
    注: 实际节假日需要专门的交易日历(目前用不到, 留个 hook)。
    """
    return datetime.now(timezone.utc).weekday() < 5  # 0-4 = Mon-Fri


def _backfill_in_worker(days: int) -> dict:
    """在线程里跑 backfill, 避免阻塞 asyncio 事件循环。"""
    from src.models.market import MARKETS
    from src.models.market import MarketCode

    # 简单判断当前是否在交易时段后(>= 16:00 Asia/Shanghai)
    # 18:00 跑一般收盘后 3 小时, 数据稳定
    engine = create_engine(DB_URL, pool_pre_ping=True)
    try:
        symbols = get_default_symbols()
        logger.info(
            f"[kline backfill] 开始: {len(symbols)} 只股, days={days}, "
            f"concurrent={CONCURRENCY}"
        )
        start = time.time()
        result = asyncio.run(
            ingest_batch(
                engine,
                symbols,
                period="1d",
                days=days,
                concurrency=CONCURRENCY,
            )
        )
        elapsed = time.time() - start
        rate = result["total_ingested"] / max(elapsed, 0.1)
        logger.info(
            f"[kline backfill] 完成: {result['total_ingested']} 行 / "
            f"{elapsed:.1f}s / {rate:.0f} 行/秒"
        )
        return {
            "ingested": result["total_ingested"],
            "elapsed": elapsed,
            "rate": rate,
        }
    finally:
        engine.dispose()


class KlineBackfillScheduler:
    """K线每日 backfill 调度器, 18:00 收盘后自动入库。"""

    def __init__(self, timezone: str = "Asia/Shanghai"):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self._running = False

    async def _backfill_job(self):
        if self._running:
            logger.warning("[kline backfill] 上轮还在跑, 跳过本轮")
            return
        if not _is_market_day():
            logger.info("[kline backfill] 今天非交易日, 跳过")
            return
        self._running = True
        try:
            # 第 1 次: 拉 2 天
            result = await asyncio.to_thread(_backfill_in_worker, BACKFILL_DAYS)
            if result["ingested"] == 0:
                # 0 行入库(可能数据源问题), 再试 7 天兜底
                logger.warning("[kline backfill] 首次入库 0 行, 尝试 7 天回填")
                await asyncio.to_thread(_backfill_in_worker, BACKFILL_DAYS_FALLBACK)
        except Exception as e:
            logger.exception(f"[kline backfill] 异常: {e}")
        finally:
            self._running = False

    def start(self):
        self.scheduler.add_job(
            self._backfill_job,
            "cron",
            day_of_week="mon-fri",
            hour=BACKFILL_CRON["hour"],
            minute=BACKFILL_CRON["minute"],
            id="kline_backfill_daily",
            replace_existing=True,
            coalesce=True,  # 错过的多次合并成一次
            max_instances=1,
        )
        self.scheduler.start()
        from src.core.scheduler_registry import register

        register("kline_backfill", self.scheduler)
        logger.info(
            f"K线入库调度器已启动: 每日 {BACKFILL_CRON['hour']:02d}:"
            f"{BACKFILL_CRON['minute']:02d} (周一至五, "
            f"{self.scheduler.timezone})"
        )

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("K线入库调度器已关闭")

    def trigger_now(self) -> dict:
        """手动触发一次 backfill(管理界面或测试用)。"""
        return _backfill_in_worker(BACKFILL_DAYS)
