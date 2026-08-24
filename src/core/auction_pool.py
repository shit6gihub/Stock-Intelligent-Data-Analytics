"""竞价异动池 v0.3.1 (2026-08-24, 修复 gap_pct/缺失字段)。

- fetch_auction_anomaly(market): 拉同花顺集合竞价异动股(默认 CN→沪A), 转 list[dict]
- sync_auction_to_db(records)   : 把异动股写入 DB 表 auction_anomaly_records(供历史追踪)
- get_anomaly_history(symbol)   : 从 DB 查某只股票近 N 天竞价异动历史
- register_cron(scheduler)      : 把"工作日 09:25 竞价异动同步" job 注册到**现有** APScheduler
                                  实例(report_scheduler 的底层调度器), 不新开 scheduler

进程内 30s 缓存(与 main_flow_compare 同思路, 避免每轮监控重复拉取)。

⚠️ 字段口径(2026-08-24 修复):
- 实测 thsdk get_auction_anomaly 返回约 6 列: 时间/价格/总金额/代码/名称/异动类型1。
  - 价格 / 总金额 是占位脏数据(不可信,但仍可用于 (价格 / 昨收 - 1) 推导 gap_pct)。
  - 数据源**不提供** 撤单率 / 量比, 字段固定 None, API 响应 missing_fields 告知前端。
- gap_pct 在本模块内由 (价格 / 昨收 - 1)*100 二次计算, 昨收从 PG klines hypertable
  批量查(一次 SQL IN 批查 ~461 只股, 避免逐只查打爆库)。
  - 脏数据过滤: 异动类型含'涨停试盘/跌停试盘'且价格 <= 1.01 元时, 计算结果 |gap|>30%
    视为脏(价/昨收 失衡), gap_pct 置 None(记录仍保留,前端表格显 '—')。
  - 昨收缺失(库表无数据) -> gap_pct 保持 None。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

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

# 2026-08-24: 数据源不提供的字段,在 API 响应 missing_fields 显式告知前端。
MISSING_FIELDS: list[str] = ["withdraw_rate", "volume_ratio"]
MISSING_NOTE: str = "thsdk 竞价异动数据源不提供撤单率/量比,字段固定为空"

# 脏数据过滤阈值
_DIRTY_PRICE_THRESHOLD = 1.01  # 元
_DIRTY_GAP_THRESHOLD = 30.0    # %

_CACHE_TTL = 30.0
_cache: dict[str, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    """清空进程内缓存(测试 / 运维手动刷新用)。"""
    _cache.clear()


def _to_records(df) -> list[dict]:
    """DataFrame -> list[dict]。列名兼容映射 + 保留全量字段。

    2026-08-24 字段口径更新: 实测 thsdk get_auction_anomaly 只返回
    时间 / 价格 / 总金额 / 代码 / 名称 / 异动类型1 共 6 列。
    - 高开幅度 / 撤单率 / 量比 列已不存在 -> 直接置 None。
    - 价格 / 异动类型1 仍抓出, 供 fetch_auction_anomaly 二次推导 gap_pct / 过滤脏数据。
    """
    if df is None or len(df) == 0:
        return []

    cols = [str(c).strip() for c in df.columns]
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
    to_price = find("价格", "price", "price_now")
    to_anomaly = find("异动类型1", "异动类型", "anomaly_type", "anomaly")

    # 兼容旧的列(已被实测剔除,但仍保留抽取以防字段偶尔出现)
    to_gap = find("高开幅度", "涨跌幅", "涨幅", "涨跌", "gap", "openprice")
    to_withdraw = find("撤单率", "撤单", "withdraw")
    to_vol = find("量比", "volume_ratio", "volratio", "换手")

    records: list[dict] = []
    for _, row in df.iterrows():
        code = val(row, to_code) if to_code else None
        code = _normalize_symbol(str(code)) if code is not None else None

        name = val(row, to_name) if to_name else None
        price_raw = num(row, to_price) if to_price else None
        anomaly_type = str(val(row, to_anomaly)) if to_anomaly else None

        # 2026-08-24: withdraw_rate / volume_ratio 数据源不提供,固定 None。
        # 旧的 to_gap/to_withdraw/to_vol 仅作兜底:列还在时仍可读,但实测已无此列。
        gap_pct = num(row, to_gap) if to_gap else None
        withdraw_rate = num(row, to_withdraw) if to_withdraw else None
        volume_ratio = num(row, to_vol) if to_vol else None

        rec = {
            "code": code,
            "symbol": code,
            "name": str(name) if name is not None else "",
            # gap_pct 占位:本函数内仅做兼容抽取;真实口径在 fetch_auction_anomaly
            # 用 (价格/昨收 - 1)*100 二次计算并覆盖。
            "gap_pct": gap_pct,
            "withdraw_rate": withdraw_rate,
            "volume_ratio": volume_ratio,
            # 内部字段(供 fetch_auction_anomaly 二次计算用)
            "price_raw": price_raw,
            "anomaly_type": anomaly_type or "",
        }
        # 保留其余原始字段(去掉已提取的, 避免重复), 归一化列名以避免重复列冲突
        seen = set()
        skip_norm = {
            "代码", "名称", "高开幅度", "涨跌幅", "涨幅", "涨跌",
            "撤单率", "量比", "价格", "price", "异动类型1", "异动类型",
        }
        for c in df.columns:
            k = norm.get(c, c).replace("_", "")
            if k in skip_norm:
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


def _batch_prev_close(symbols: list[str]) -> dict[str, float]:
    """批量获取 symbols 的昨收价 (klines hypertable, ts < 今天, period='1d')。

    一次 SQL IN 批查, 避免 ~461 只股逐只查打爆 DB。
    - PG: 走 DISTINCT ON 取每个 symbol 最新一条 ts<today 的 close。
    - SQLite (测试/兜底): klines 表通常不存在, 直接返回 {}。
    - 库表缺 / 查询失败 / 昨收 <= 0 / symbol 无数据: 对应 symbol 不进结果, 上层
      gap_pct 保持 None。

    Returns:
        {symbol: prev_close} 仅包含查到的 symbol。查不到的 symbol 不在 dict 里。
    """
    if not symbols:
        return {}

    # 去重 + 过滤 6 位代码
    syms = sorted({str(s).strip() for s in symbols if s and str(s).strip()})
    if not syms:
        return {}

    try:
        from sqlalchemy import bindparam, text

        from src.web.database import IS_PG, engine

        today = datetime.now(timezone.utc).date()

        with engine.connect() as conn:
            # 探针: klines 表是否存在(SQLite 默认环境无该表)
            if IS_PG:
                tbl = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name='klines' LIMIT 1"
                    )
                ).first()
            else:
                tbl = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='klines' LIMIT 1"
                    )
                ).first()
            if not tbl:
                return {}

            # 一次 SQL IN 批查;按 symbol, ts DESC 取每 symbol 最新一条 ts<today 的 close。
            sql = text(
                "SELECT symbol, close FROM klines "
                "WHERE period = '1d' AND market = 'CN' "
                "  AND symbol IN :symbols "
                "  AND ts < :today "
                "ORDER BY symbol ASC, ts DESC"
            ).bindparams(bindparam("symbols", expanding=True))
            rows = conn.execute(
                sql, {"symbols": syms, "today": today}
            ).fetchall()

        result: dict[str, float] = {}
        for sym, close in rows:
            sym = str(sym)
            # ORDER BY symbol, ts DESC: 每 symbol 段内第一条就是 ts 最新(即昨收)。
            # 用 set 跳过同 symbol 的后续行。
            if sym in result:
                continue
            try:
                c = float(close) if close is not None else None
            except (TypeError, ValueError):
                c = None
            if c is not None and c > 0:
                result[sym] = c
        return result
    except Exception as e:  # noqa: BLE001 - DB 不可用统一降级,不阻塞主流程
        logger.warning("[auction_pool] 昨收批量查询失败 (n=%d): %r", len(syms), e)
        return {}


def _compute_gap_pct(records: list[dict]) -> None:
    """对 records 二次计算 gap_pct = (price_raw / prev_close - 1) * 100。

    - prev_close 缺失 / <=0 / price_raw 缺失 -> 该 record gap_pct 置 None(原值不保留)。
    - 脏数据过滤: 异动类型含'涨停试盘'或'跌停试盘' 且 价格 <= 1.01 元 且
      计算结果 |gap_pct| > 30% 时, gap_pct 置 None(记录保留,前端表格显 '—')。
    - 不属于以上情况的记录, 即使 gap 看似异常也保留(避免过度清洗)。

    就地修改 records; 返回 None。
    """
    if not records:
        return

    syms = [r.get("symbol") or r.get("code") for r in records]
    prev_closes = _batch_prev_close(syms)

    for rec in records:
        sym = rec.get("symbol") or rec.get("code")
        prev = prev_closes.get(str(sym)) if sym else None
        price = rec.get("price_raw")
        if (
            price is None
            or prev is None
            or prev <= 0
        ):
            rec["gap_pct"] = None
            continue
        gap = (float(price) / float(prev) - 1.0) * 100.0

        atype = str(rec.get("anomaly_type") or "")
        is_limit_probe = ("涨停试盘" in atype) or ("跌停试盘" in atype)
        if is_limit_probe and float(price) <= _DIRTY_PRICE_THRESHOLD and abs(gap) > _DIRTY_GAP_THRESHOLD:
            rec["gap_pct"] = None
            continue
        rec["gap_pct"] = round(gap, 2)


def fetch_auction_anomaly(market: str = "CN") -> list[dict]:
    """拉取竞价异动池(30s 缓存)。market: CN/SH/SZ/BJ/ALL 或 thsdk 代码。

    数据源不可用(thsdk 未安装 / 调用异常)时返回 [] 并记日志, 不抛异常(供 cron/API 降级)。

    2026-08-24 更新: 每条 record 的 gap_pct 二次计算 (价格/昨收 - 1)*100,
    withdraw_rate / volume_ratio 固定 None(数据源不提供)。
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

    # 二次计算 gap_pct (就地修改 all_records, 不增字段)
    _compute_gap_pct(all_records)

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
