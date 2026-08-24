"""竞价异动池 gap_pct 二次计算 + 脏数据过滤 + 缺失字段响应测试(2026-08-24)。

覆盖:
- _batch_prev_close: 一次 SQL IN 批查, 返回 {symbol: prev_close} 映射
- _compute_gap_pct: (价格/昨收 - 1)*100 计算 + 涨停试盘/跌停试盘脏数据过滤
- fetch_auction_anomaly 整合: gap_pct 由 klines mock 提供, withdraw_rate / volume_ratio
  固定 None(数据源不提供)
- API 响应(anomaly 接口)加 missing_fields + note
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

_FX = os.path.join(os.path.dirname(__file__), "fixtures")
if _FX not in sys.path:
    sys.path.insert(0, _FX)
import mock_main_flow as mmf  # noqa: E402

from src.core import auction_pool  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_pool_cache():
    auction_pool.clear_cache()
    yield
    auction_pool.clear_cache()


@pytest.fixture
def mock_thsdk_l2(monkeypatch):
    """注入 data_source.thsdk_l2 内存桩(get_auction_anomaly 返回 fake DF)。"""
    mod = mmf.fake_thsdk_l2_module()
    mod.get_auction_anomaly = MagicMock(return_value=mmf.fake_auction_df())
    monkeypatch.setitem(sys.modules, "data_source.thsdk_l2", mod)
    return mod


# ── _batch_prev_close: SQL IN 批查 ───────────────────────────────────────
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    """模拟 sqlalchemy 连接: 仅支持 execute(text).fetchall() 用于 klines 探测 + 批查。

    rows 为 [(symbol, close), ...] 元组列表, SQLAlchemy fetchall 返回的 Row 对象
    在迭代时按列解包, 等价于 tuple unpacking。
    """

    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt, params=None):  # noqa: ARG002
        sql = str(stmt).lower()
        # 探针(检测表是否存在)
        if "sqlite_master" in sql or "information_schema.tables" in sql:
            return _FakeResult([("klines",)])   # 假装表存在
        # 批查: SQLAlchemy bindparam(expanding=True) 把 IN :symbols 编译成
        # IN (__[postcompile_symbols]), 字面"in :symbols"不命中, 用"from klines"识别。
        if "from klines" in sql:
            return _FakeResult(self._rows)
        return _FakeResult([])


class _FakeCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *a):
        return False


class _FakeEng:
    def connect(self):
        return _FakeCtx(_FakeConn(self._rows))

    def __init__(self, rows):
        self._rows = rows


@pytest.fixture
def mock_klines_sqlite(monkeypatch):
    """注入 _batch_prev_close 用的 SQLite 引擎 mock(假装 klines 表存在 + 返回假数据)。"""
    rows = [
        ("002361", 8.05),    # 002361 昨收 8.05
        ("600000", 10.10),   # 600000 昨收 10.10
    ]
    from src.web import database as _db
    monkeypatch.setattr(_db, "engine", _FakeEng(rows))
    monkeypatch.setattr(_db, "IS_PG", False)
    return rows


def test_batch_prev_close_returns_map(mock_klines_sqlite):
    """一次 SQL IN 批查, 返回 {symbol: prev_close}。"""
    result = auction_pool._batch_prev_close(["002361", "600000"])
    assert result == {"002361": 8.05, "600000": 10.10}


def test_batch_prev_close_empty_input():
    """空 symbols -> 立即返回 {}, 不查 DB。"""
    assert auction_pool._batch_prev_close([]) == {}


def test_batch_prev_close_dedup(mock_klines_sqlite):
    """重复 symbol 去重。"""
    result = auction_pool._batch_prev_close(["002361", "002361", "600000"])
    assert result == {"002361": 8.05, "600000": 10.10}


def test_batch_prev_close_missing_symbol_omitted(mock_klines_sqlite):
    """查不到的 symbol 不在结果 dict 里(上层 gap_pct 保持 None)。"""
    result = auction_pool._batch_prev_close(["002361", "999999"])
    assert "002361" in result
    assert "999999" not in result


def test_batch_prev_close_table_missing(monkeypatch):
    """klines 表不存在 -> 返回 {} 不报错。"""
    class _NoTblConn:
        def execute(self, stmt, params=None):
            sql = str(stmt).lower()
            if "sqlite_master" in sql:
                return _FakeResult([])    # 表不存在
            return _FakeResult([])

    class _NoTblCtx:
        def __enter__(self):
            return _NoTblConn()

        def __exit__(self, *a):
            return False

    class _NoTblEng:
        def connect(self):
            return _NoTblCtx()

    from src.web import database as _db
    monkeypatch.setattr(_db, "engine", _NoTblEng())
    monkeypatch.setattr(_db, "IS_PG", False)
    assert auction_pool._batch_prev_close(["002361"]) == {}


def test_batch_prev_close_db_error(monkeypatch):
    """DB 抛异常 -> 返回 {} 不阻塞主流程。"""
    class _BoomEng:
        def connect(self):
            raise RuntimeError("DB 挂了")

    from src.web import database as _db
    monkeypatch.setattr(_db, "engine", _BoomEng())
    assert auction_pool._batch_prev_close(["002361"]) == {}


def test_batch_prev_close_skips_zero_or_none_close(monkeypatch):
    """prev_close <= 0 或 None -> 不进结果(上层 gap_pct 置 None)。"""
    rows = [
        ("002361", None),
        ("600000", 0.0),
        ("830001", -1.0),
    ]
    from src.web import database as _db
    monkeypatch.setattr(_db, "engine", _FakeEng(rows))
    monkeypatch.setattr(_db, "IS_PG", False)
    result = auction_pool._batch_prev_close(["002361", "600000", "830001"])
    assert result == {}


# ── _compute_gap_pct: 二次计算 + 脏数据过滤 ─────────────────────────────
def test_compute_gap_pct_happy_path(mock_klines_sqlite):
    """正常路径: (价格/昨收 - 1)*100 算 gap_pct。"""
    records = [
        {"symbol": "002361", "price_raw": 8.32, "anomaly_type": "高开"},
        {"symbol": "600000", "price_raw": 9.92, "anomaly_type": "低开"},
    ]
    auction_pool._compute_gap_pct(records)
    # 002361: 8.32 / 8.05 - 1 = 3.354...% → round(2)
    assert records[0]["gap_pct"] == pytest.approx(3.35, abs=0.01)
    # 600000: 9.92 / 10.10 - 1 = -1.782%
    assert records[1]["gap_pct"] == pytest.approx(-1.78, abs=0.01)


def test_compute_gap_pct_no_klines_data():
    """klines 缺失 -> gap_pct 保持 None(不抛异常)。"""
    records = [{"symbol": "002361", "price_raw": 8.32, "anomaly_type": "高开"}]
    auction_pool._compute_gap_pct(records)
    assert records[0]["gap_pct"] is None


def test_compute_gap_pct_no_price():
    """price_raw 缺失 -> gap_pct None。"""
    records = [{"symbol": "002361", "price_raw": None, "anomaly_type": "高开"}]
    auction_pool._compute_gap_pct(records)
    assert records[0]["gap_pct"] is None


def test_compute_gap_pct_zero_prev_close(monkeypatch):
    """prev_close <= 0 -> gap_pct None(避免除零)。"""
    monkeypatch.setattr(
        auction_pool, "_batch_prev_close", lambda _syms: {"002361": 0}
    )
    records = [{"symbol": "002361", "price_raw": 5.0, "anomaly_type": "高开"}]
    auction_pool._compute_gap_pct(records)
    assert records[0]["gap_pct"] is None


def test_compute_gap_pct_empty():
    """空列表立即返回, 不查 DB。"""
    auction_pool._compute_gap_pct([])  # 不应抛异常


def test_compute_gap_pct_dirty_limit_probe_high(monkeypatch):
    """脏数据过滤: 涨停试盘 + 价格<=1.01 + |gap|>30% -> gap_pct 置 None。

    用例构造: prev_close=0.5, price=1.01 -> gap=102%, 远超 30%, 视为脏。
    """
    monkeypatch.setattr(
        auction_pool, "_batch_prev_close", lambda _syms: {"830001": 0.5}
    )
    records = [
        {
            "symbol": "830001",
            "price_raw": 1.01,            # <= 1.01
            "anomaly_type": "涨停试盘",   # 含'涨停试盘'
            "gap_pct": 999,               # 占位旧值, 应被覆盖
        }
    ]
    auction_pool._compute_gap_pct(records)
    # gap = (1.01/0.5 - 1)*100 = 102% > 30%, 视为脏 -> None
    assert records[0]["gap_pct"] is None


def test_compute_gap_pct_dirty_limit_probe_low(monkeypatch):
    """脏数据过滤: 跌停试盘 + 价格<=1.01 + |gap|>30% -> None。"""
    monkeypatch.setattr(
        auction_pool, "_batch_prev_close", lambda _syms: {"830001": 2.0}
    )
    records = [
        {
            "symbol": "830001",
            "price_raw": 0.5,             # <= 1.01
            "anomaly_type": "跌停试盘",   # 含'跌停试盘'
        }
    ]
    auction_pool._compute_gap_pct(records)
    # gap = (0.5/2.0 - 1)*100 = -75% > 30%(绝对值), 视为脏
    assert records[0]["gap_pct"] is None


def test_compute_gap_pct_limit_probe_normal_kept(monkeypatch):
    """涨停试盘但价格 > 1.01 -> 不过滤, gap_pct 正常计算。

    价格 > 1.01 元 不在 dirty 过滤条件内, 即使 |gap| > 30% 也保留(可能是真实的
    大幅高开 + 涨停试盘)。
    """
    monkeypatch.setattr(
        auction_pool, "_batch_prev_close", lambda _syms: {"600123": 5.0}
    )
    records = [
        {
            "symbol": "600123",
            "price_raw": 11.0,            # > 1.01
            "anomaly_type": "涨停试盘",
        }
    ]
    auction_pool._compute_gap_pct(records)
    # gap = (11.0/5.0 - 1)*100 = 120%, > 30% 但 price > 1.01 不过滤
    assert records[0]["gap_pct"] == pytest.approx(120.0, abs=0.01)


def test_compute_gap_pct_non_limit_probe_high_kept(monkeypatch):
    """非涨停试盘/跌停试盘 + |gap| > 30% -> 保留(不过滤)。

    任务要求: 仅对 异动类型含'涨停试盘/跌停试盘' 且 价格<=1.01 的 record 应用脏数据过滤。
    其他场景不主动清洗。
    """
    monkeypatch.setattr(
        auction_pool, "_batch_prev_close", lambda _syms: {"600100": 5.0}
    )
    records = [
        {
            "symbol": "600100",
            "price_raw": 0.9,             # <= 1.01 但 anomaly_type 不是涨停/跌停试盘
            "anomaly_type": "高开",
        }
    ]
    auction_pool._compute_gap_pct(records)
    # gap = (0.9/5.0 - 1)*100 = -82%, |gap| > 30% 但 anomaly_type 不匹配 dirty 条件
    assert records[0]["gap_pct"] == pytest.approx(-82.0, abs=0.01)


# ── fetch_auction_anomaly 整合 ─────────────────────────────────────────
def test_fetch_auction_anomaly_with_klines(mock_thsdk_l2, mock_klines_sqlite):
    """完整链路: thsdk 拉 6 列 -> _compute_gap_pct 用 klines 二次计算 -> gap_pct 真实值。"""
    recs = auction_pool.fetch_auction_anomaly("CN")
    assert len(recs) == 2
    # 002361: 8.32/8.05 - 1 = 3.35% (近似)
    assert recs[0]["gap_pct"] == pytest.approx(3.35, abs=0.01)
    assert recs[0]["withdraw_rate"] is None
    assert recs[0]["volume_ratio"] is None
    # 600000: 9.92/10.10 - 1 = -1.78%
    assert recs[1]["gap_pct"] == pytest.approx(-1.78, abs=0.01)


def test_fetch_auction_anomaly_missing_fields_constant():
    """MISSING_FIELDS / MISSING_NOTE 暴露给 API 层。"""
    assert auction_pool.MISSING_FIELDS == ["withdraw_rate", "volume_ratio"]
    assert "撤单率" in auction_pool.MISSING_NOTE
    assert "量比" in auction_pool.MISSING_NOTE


# ── API 响应: missing_fields + note ──────────────────────────────────────
def test_api_anomaly_response_includes_missing_fields(mock_thsdk_l2, mock_klines_sqlite):
    """anomaly 接口响应带 missing_fields + note。

    直接调用 handler 函数(不走 TestClient), 避开 fastapi 鉴权中间件。
    handler 内部逻辑与 HTTP 响应一致, 覆盖 available/records/missing_fields/note。
    """
    from src.web.api.auction_pool import anomaly as _anomaly_handler

    body = _anomaly_handler(market="CN")
    assert body["available"] is True
    assert body["missing_fields"] == ["withdraw_rate", "volume_ratio"]
    assert "撤单率" in body["note"]
    assert "量比" in body["note"]
    # record 字段: gap_pct 二次计算 / withdraw_rate / volume_ratio 固定 None
    first = body["records"][0]
    assert first["withdraw_rate"] is None
    assert first["volume_ratio"] is None
    assert first["gap_pct"] == pytest.approx(3.35, abs=0.01)


def test_api_anomaly_unavailable_includes_missing_fields(monkeypatch):
    """数据源不可用 -> available=false, 响应仍带 missing_fields + note。"""
    from src.web.api.auction_pool import anomaly as _anomaly_handler

    def fake_fetch_empty(market="CN"):
        return []

    monkeypatch.setattr(
        "src.web.api.auction_pool.fetch_auction_anomaly", fake_fetch_empty
    )
    body = _anomaly_handler(market="CN")
    assert body["available"] is False
    assert body["count"] == 0
    assert body["missing_fields"] == ["withdraw_rate", "volume_ratio"]
    assert "撤单率" in body["note"]
    assert "数据源未接入" in body["note"] or "不可用" in body["note"]
