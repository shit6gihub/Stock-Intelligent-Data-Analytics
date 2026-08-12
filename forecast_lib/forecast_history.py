# 历史预测存储(SQLite)
import json
import sqlite3 as _sqlite3

try:
    from .forecast_paths import FORECAST_DB_PATH
except ImportError:  # forecast_server.py 将 forecast_lib 直接加入 sys.path
    from forecast_paths import FORECAST_DB_PATH

_HISTORY_DB = FORECAST_DB_PATH



def _init_history_db():
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            stock_name TEXT DEFAULT '',
            last_close REAL,
            last_date TEXT,
            target_date TEXT DEFAULT '',
            pred_days INTEGER,
            direction TEXT,
            expected_pct REAL,
            prediction TEXT,
            action TEXT,
            tone TEXT,
            confidence TEXT,
            target_price REAL,
            stop_loss REAL,
            summary TEXT,
            sentiment_adj REAL,
            models TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    # 迁移: 旧表无 stock_name/target_date 列则补(ALTER TABLE ADD COLUMN)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(forecasts)").fetchall()]
        if "stock_name" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN stock_name TEXT DEFAULT ''")
        if "target_date" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN target_date TEXT DEFAULT ''")
        if "sentiment_notes" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN sentiment_notes TEXT DEFAULT ''")
        if "capital_flow" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN capital_flow TEXT DEFAULT ''")
        if "dragon_tiger" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN dragon_tiger TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    conn.close()



_init_history_db()



def get_stock_name(symbol: str) -> str:
    """查股票名称(baostock query_stock_basic,需带市场前缀,失败返回空)。"""
    try:
        import baostock as bs
        code = f"sh.{symbol}" if symbol.startswith(("6", "9")) else f"sz.{symbol}"
        lg = bs.login()
        if lg.error_code != "0":
            return ""
        rs = bs.query_stock_basic(code=code)
        name = ""
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            # query_stock_basic(code) 返回 [code, code_name, ipoDate, outDate, type, status]
            if len(row) >= 2:
                name = row[1]
        bs.logout()
        return name or ""
    except Exception:
        return ""



def save_forecast(rec: dict):
    """保存一次预测到历史库。"""
    try:
        conn = _sqlite3.connect(_HISTORY_DB)
        conn.execute(
            """INSERT INTO forecasts
               (symbol, stock_name, last_close, last_date, target_date, pred_days, direction, expected_pct,
                prediction, action, tone, confidence, target_price, stop_loss,
                summary, sentiment_adj, sentiment_notes, models, capital_flow, dragon_tiger)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.get("symbol", ""), rec.get("stock_name", ""),
                rec.get("last_close"), rec.get("last_date"),
                rec.get("target_date", ""),
                rec.get("pred_days"), rec.get("direction"), rec.get("expected_pct"),
                json.dumps(rec.get("prediction", []), ensure_ascii=False),
                rec.get("action", ""), rec.get("tone", ""), rec.get("confidence", ""),
                rec.get("target_price"), rec.get("stop_loss"),
                rec.get("summary", ""), rec.get("sentiment_adj"),
                rec.get("sentiment_notes", "[]"),
                json.dumps(rec.get("models", {}), ensure_ascii=False, default=str),
                json.dumps(rec.get("capital_flow", []), ensure_ascii=False),
                json.dumps(rec.get("dragon_tiger", []), ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"保存历史失败: {e}")



def list_forecasts(limit: int = 50, symbol: str = ""):
    """查询历史预测列表(含到期对照 outcome 字段, 2026-08-12 增加)。"""
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    q = "SELECT * FROM forecasts"
    params: list = []
    if symbol:
        q += " WHERE symbol = ?"
        params.append(symbol)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    for r in rows:
        try:
            r["prediction"] = json.loads(r["prediction"])
        except Exception:
            pass
        _attach_outcome(r)
    return rows


def _attach_outcome(r: dict) -> None:
    """给一条 forecast 记录附加到期对照: outcome_return_pct / outcome_status。

    status: hit(方向对) / miss(方向错) / pending(未到期) / no_data(取不到K线)。
    用 KlineCollector 拉实际行情, 按 target_date 收盘 vs last_close 判方向。
    失败/无数据 → no_data(不阻断列表)。
    """
    r.setdefault("outcome_return_pct", None)
    r.setdefault("outcome_status", "pending")
    try:
        from datetime import date, datetime
        target_date = str(r.get("target_date") or "")[:10]
        last_close = r.get("last_close")
        direction = r.get("direction")
        if not target_date or not last_close or not direction:
            r["outcome_status"] = "no_data"
            return
        if target_date > date.today().isoformat():
            r["outcome_status"] = "pending"
            return
        try:
            from src.collectors.kline_collector import KlineCollector
            from src.models.market import MarketCode
            from src.core.context_store import _to_market  # noqa: F401 (兼容)
        except Exception:
            from src.collectors.kline_collector import KlineCollector
            from src.models.market import MarketCode

        symbol = str(r.get("symbol") or "").split(".")[0]
        market = MarketCode.CN
        klines = KlineCollector(market).get_klines(symbol, days=60)
        td = datetime.strptime(target_date, "%Y-%m-%d").date()
        actual = None
        for k in klines or []:
            d = str(getattr(k, "date", ""))[:10]
            if d and d <= target_date:
                actual = float(getattr(k, "close", 0) or 0)
        if actual is None or actual <= 0:
            r["outcome_status"] = "no_data"
            return
        r["outcome_return_pct"] = round((actual / float(last_close) - 1) * 100, 2)
        actual_dir = "up" if actual > float(last_close) else "down" if actual < float(last_close) else "flat"
        if actual_dir == "flat":
            r["outcome_status"] = "pending"  # 平盘视为未定
            return
        r["outcome_status"] = "hit" if actual_dir == direction else "miss"
    except Exception:
        r["outcome_status"] = "no_data"
