"""竞价异动池 v0.3.0(thsdk L2 竞价异动能力落地)。

- fetch_auction_anomaly(market): 拉同花顺集合竞价异动股(默认 CN→沪A), 转 list[dict]
- sync_auction_to_db(records)   : 把异动股写入 DB 表 auction_anomaly_records(供历史追踪)
- get_anomaly_history(symbol)   : 从 DB 查某只股票近 N 天竞价异动历史
- register_cron(scheduler)      : 把"工作日 09:25 竞价异动同步" job 注册到**现有** APScheduler
                                  实例(report_scheduler 的底层调度器), 不新开 scheduler

进程内 30s 缓存(与 main_flow_compare 同思路, 避免每轮监控重复拉取)。

⚠️ 列名兼容: 同花顺 get_auction_anomaly 返回约 1000 行 DataFrame, 列名为中文/英文混合,
  通过关键词模糊匹配提取 code/name/gap_pct/withdraw_rate/volume_ratio, 未命中的原始列
  也一并保留(record 携带全量字段, 便于前端/下游按需取用)。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# market -> thsdk 市场代码
_MARKET_MAP = {
    "": "USHA",
    "CN": "USHA",     # 默认: 沪 A(任务口径)
    "SH": "USHA",
    "USHA": "USHA",
    "SZ": "USZA",
    "USZA": "USZA",
    "BJ": "USTM",
    "USTM": "USTM",
    # 全市场(沪深合并拉取去重)
    "ALL": None,
}

_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    """清空进程内缓存(测试 / 运维手动刷新用)。"""
    _cache.clear()


def _to_records(df) -> list[dict]:
    """DataFrame -> list[dict]。列名兼容映射 + 保留全量字段。"""
    if df is None or len(df) == 0:
        return []

    cols = [str(c).strip() for c in df.columns]
    all_cols_set = set(cols)
    # 保留原始列名 -> 规范化后列名(去空白, 小写), 便于统一取值
    norm = {c: c.strip().lower() for c in df.columns}

    def find(*keys: str) -> str | None:
        """按规范化关键词找列(返回原始列名), 找不到返回 None。"""
        for kk in keys:
            for c in df.columns:
                if norm[c] == kk or kk in norm[c]:
                    return c
        return None

    def val(row, col):
        try:
            v = row[col]
            return v.item() if hasattr(v, "item") else v
        except Exception:
            return None

    def num(row, col):
        v = val(row, col)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    to_code = find("代码", "代码代码", "股票代码", "证券代码", "code")
    to_name = find("名称", "股票名称", "证券名称", "name")
    to_gap = find("高开幅度", "涨跌幅", "涨幅", "涨跌", "gap", "openprice")
    to_withdraw = find("撤单率", "撤单", "withdraw")
    to_vol = find("量比", "volume_ratio", "volratio", "换手")

    records: list[dict] = []
    for _, row in df.iterrows():
        code = val(row, to_code) if to_code else None
        code = _normalize_symbol(str(code)) if code is not None else None

        name = val(row, to_name) if to_name else None
        gap_pct = num(row, to_gap) if to_gap else None
        withdraw_rate = num(row, to_withdraw) if to_withdraw else None
        volume_ratio = num(row, to_vol) if to_vol else None

        rec = {
            "code": code,
            "symbol": code,
            "name": str(name) if name is not None else "",
            "gap_pct": gap_pct,
            "withdraw_rate": withdraw_rate,
            "volume_ratio": volume_ratio,
        }
        # 保留其余原始字段(去掉已提取的, 避免重复), 归一化列名以避免重复列冲突
        seen = set()
        for c in df.columns:
            k = norm.get(c, c).replace("_", "")
            if k in ("代码", "名称", "高开幅度", "涨跌幅", "涨幅", "涨跌", "撤单率", "量比"):
                continue
            if k in seen:
                k = f"{k}_{len(seen)}"
            seen.add(k)
            rec[k] = val(row, c)
        if code is not None:
            records.append(rec)
    return records


def _normalize_symbol(raw: str) -> str:
    """把竞价异动返回的代码归一化为 6 位 A 股代码(去除交易所后缀 / thsdk 前缀)。

    例: "USZA002361" / "002361.SZ" / "002361" / "sh600000" -> 6 位数字代码。
    归一化失败(无法识别)则原样返回(由上层容错)。
    """
    s = (raw or "").strip().upper()
    # thsdk 前缀: USZA/USHA/USTM
    for prefix in ("USZA", "USHA", "USTM"):
        if s.startswith(prefix):
            tail = s[len(prefix):]
            if tail.isdigit() and len(tail) == 6:
                return tail
            break
    # 交易所后缀: 002361.SZ / 600000.SH / 830001.BJ
    if "." in s:
        base = s.split(".")[0]
        if base.isdigit() and len(base) == 6:
            return base
    # 纯 6 位数字
    if s.isdigit() and len(s) == 6:
        return s
    # tencent 前缀: sh600000 / sz002361
    if len(s) == 8 and s[:2] in ("SH", "SZ") and s[2:].isdigit():
        return s[2:]
    return raw


def fetch_auction_anomaly(market: str = "CN") -> list[dict]:
    """拉取竞价异动池(30s 缓存)。market: CN/SH/SZ/BJ/ALL 或 thsdk 代码。

    数据源不可用(thsdk 未安装 / 调用异常)时返回 [] 并记日志, 不抛异常(供 cron/API 降级)。
    """
    norm_market = (market or "CN").strip().upper()
    thsdk_market = _MARKET_MAP.get(norm_market, "USHA")

    cache_key = f"{norm_market}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        markets = [thsdk_market]
        if thsdk_market is None:  # ALL
            markets = ["USHA", "USZA"]

        from data_source.thsdk_l2 import get_auction_anomaly

        all_records: list[dict] = []
        seen_codes: set[str] = set()
        for m in markets:
            df = get_auction_anomaly(m)
            for rec in _to_records(df):
                sym = rec.get("symbol")
                if sym and sym in seen_codes:
                    continue
                if sym:
                    seen_codes.add(sym)
                all_records.append(rec)
    except Exception as e:  # noqa: BLE001 - 数据源不可用统一降级
        logger.warning("[auction_pool] 竞价异动拉取失败 market=%r: %r", market, e)
        all_records = []

    _cache[cache_key] = (time.time(), all_records)
    return all_records


def sync_auction_to_db(records: list) -> int:
    """把异动股写入 DB 表 auction_anomaly_records。返回入库条数(0 表示无数据/失败)。"""
    if not records:
        return 0
    try:
        from src.web.database import SessionLocal, acquire_write
        from src.web.models import AuctionAnomalyRecord

        lock = acquire_write()
        try:
            db = SessionLocal()
            try:
                for r in records:
                    db.add(
                        AuctionAnomalyRecord(
                            symbol=str(r.get("symbol") or r.get("code") or "")[:16],
                            name=str(r.get("name") or "")[:64],
                            gap_pct=r.get("gap_pct"),
                            withdraw_rate=r.get("withdraw_rate"),
                            volume_ratio=r.get("volume_ratio"),
                            note=str(r.get("note") or "")[:255],
                        )
                    )
                db.commit()
                return len(records)
            finally:
                db.close()
        finally:
            lock.release()
    except Exception as e:  # noqa: BLE001 - DB 写入失败不崩
        logger.error("[auction_pool] 竞价异动入库失败: %r", e)
        return 0


def get_anomaly_history(symbol: str, days: int = 5) -> list[dict]:
    """从 DB 查询某只股票近 N 天竞价异动历史(按时间倒序, 最多 200 条)。"""
    from datetime import datetime, timedelta

    from src.web.database import SessionLocal
    from src.web.models import AuctionAnomalyRecord

    sym = (symbol or "").strip()
    if not sym:
        return []
    days = max(1, min(int(days or 5), 90))
    since = datetime.now() - timedelta(days=days)

    db = SessionLocal()
    try:
        rows = (
            db.query(AuctionAnomalyRecord)
            .filter(
                AuctionAnomalyRecord.symbol == sym,
                AuctionAnomalyRecord.created_at >= since,
            )
            .order_by(AuctionAnomalyRecord.created_at.desc())
            .limit(200)
            .all()
        )
        return [
            {
                "symbol": r.symbol,
                "name": r.name,
                "gap_pct": r.gap_pct,
                "withdraw_rate": r.withdraw_rate,
                "volume_ratio": r.volume_ratio,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def register_cron(scheduler) -> bool:
    """把竞价异动同步 job 注册到**传入的现有** APScheduler 实例(禁止新开 scheduler)。

    在 server.py 的 lifespan 里, report_scheduler.start() 之后调用本函数, 把
    工作日 09:25:00 的竞价异动入库 worker 挂到该调度器的底层 APScheduler 上。
    调度器尚未 start / 传入 None -> 返回 False, 不崩。
    """
    if scheduler is None or not hasattr(scheduler, "add_job"):
        return False

    def _auction_sync_once():
        from src.core.auction_pool import fetch_auction_anomaly, sync_auction_to_db

        try:
            recs = fetch_auction_anomaly("CN")
            n = sync_auction_to_db(recs)
            logger.info("[auction] 竞价异动同步完成: %d 条入库", n)
        except Exception as e:  # noqa: BLE001
            logger.error("[auction] 竞价异动同步异常: %r", e)

    try:
        scheduler.add_job(
            _auction_sync_once,
            "cron",
            day_of_week="mon-fri",
            hour=9,
            minute=25,
            id="auction_anomaly_daily_sync",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("[auction] 竞价异动 job 已注册: 工作日 09:25 (%s)", scheduler.timezone)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("[auction] 竞价异动 job 注册失败: %r", e)
        return False
