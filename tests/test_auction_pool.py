"""竞价异动池测试(阶段1.2, v0.3.0)。

覆盖:
- fetch_auction_anomaly: 市场映射 CN->USHA / SZ->USZA, DataFrame 转 dict, 代码归一化
- 30s 进程内缓存命中 / 过期
- 数据源不可用(未安装/抛异常) -> [] 容错
- sync_auction_to_db / get_anomaly_history: 用独立内存 SQLite 引擎验证 DB 读写
- register_cron: 复用现有调度器(不新开), job 成功注册 / 传入 None 不崩
"""
from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock, Mock

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


def test_fetch_cn_maps_to_usha(mock_thsdk_l2):
    """默认 market=CN -> 映射 USHA, 且 DataFrame 正确转 list[dict]。"""
    recs = auction_pool.fetch_auction_anomaly("CN")
    mock_thsdk_l2.get_auction_anomaly.assert_called_once_with("USHA")
    assert len(recs) == 2
    first = recs[0]
    assert first["symbol"] == "002361"
    assert first["code"] == "002361"
    assert first["name"] == "神剑股份"
    assert first["gap_pct"] == pytest.approx(3.38)
    assert first["withdraw_rate"] == pytest.approx(0.243)
    assert first["volume_ratio"] == pytest.approx(2.5)


def test_fetch_sz_maps_to_usza(mock_thsdk_l2):
    """market=SZ -> USZA。"""
    auction_pool.fetch_auction_anomaly("SZ")
    mock_thsdk_l2.get_auction_anomaly.assert_called_once_with("USZA")


def test_fetch_symbol_normalize(mock_thsdk_l2):
    """thsdk 前缀(USZA002361)与交易所后缀(002361.SZ)都归一化到 6 位代码。"""
    import pandas as pd

    df = pd.DataFrame(
        [
            {"代码": "USZA000001", "名称": "平安银行", "高开幅度": 1.0},
            {"代码": "002361.SZ", "名称": "神剑", "高开幅度": 2.0},
        ]
    )
    mock_thsdk_l2.get_auction_anomaly.return_value = df
    recs = auction_pool.fetch_auction_anomaly("CN")
    symbols = {r["symbol"] for r in recs}
    assert symbols == {"000001", "002361"}


def test_fetch_cache_hit(mock_thsdk_l2):
    """30s 内二次调用命中缓存, get_auction_anomaly 只调一次。"""
    auction_pool.fetch_auction_anomaly("CN")
    auction_pool.fetch_auction_anomaly("CN")
    assert mock_thsdk_l2.get_auction_anomaly.call_count == 1


def test_fetch_cache_expired(mock_thsdk_l2, monkeypatch):
    """超过 30s TTL 后重新拉取。"""
    calls = {"n": 0}

    def fake_time():
        calls["n"] += 1
        if calls["n"] == 3:   # 第2次请求的 now 越过 30s
            return 131.0
        return 100.0 + calls["n"] / 100.0

    monkeypatch.setattr(auction_pool.time, "time", fake_time)
    auction_pool.fetch_auction_anomaly("CN")
    auction_pool.fetch_auction_anomaly("CN")
    assert mock_thsdk_l2.get_auction_anomaly.call_count == 2


def test_fetch_thsdk_unavailable_fallback(mock_thsdk_l2):
    """数据源抛异常 -> 返回 [], 不崩。"""
    mock_thsdk_l2.get_auction_anomaly.side_effect = Exception("thsdk 连接失败")
    assert auction_pool.fetch_auction_anomaly("CN") == []


def test_fetch_thsdk_module_missing(monkeypatch):
    """thsdk 模块未安装(ImportError) -> 返回 [] 容错。"""
    if "data_source.thsdk_l2" in sys.modules:
        monkeypatch.delitem(sys.modules, "data_source.thsdk_l2")
    assert auction_pool.fetch_auction_anomaly("CN") == []


# ── DB 读写: 用独立内存 SQLite 引擎, 避免污染真实 data/panwatch.db ──────────
@pytest.fixture
def in_mem_db(monkeypatch):
    """注入内存 SQLite 引擎 + 建 auction_anomaly_records 表, 全程隔离真实 DB。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.web import models
    from src.web import database as _db

    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    monkeypatch.setattr(_db, "engine", engine)
    monkeypatch.setattr(_db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(_db, "IS_PG", False)
    # sync 里走 acquire_write -> 其上引用 _db.IS_PG(False 则取 sqlite 信号量, OK)
    return models


def test_sync_and_history_then_clear(in_mem_db):
    """同步落库 + 查询历史, 均走内存库。"""
    recs = [
        {"symbol": "002361", "name": "神剑股份", "gap_pct": 3.38,
         "withdraw_rate": 0.243, "volume_ratio": 2.5},
        {"symbol": "600000", "name": "浦发银行", "gap_pct": -1.2,
         "withdraw_rate": 0.1, "volume_ratio": 0.8},
    ]
    n = auction_pool.sync_auction_to_db(recs)
    assert n == 2

    hist = auction_pool.get_anomaly_history("002361", days=5)
    assert len(hist) == 1
    assert hist[0]["symbol"] == "002361"
    assert hist[0]["gap_pct"] == pytest.approx(3.38)

    # 其他代码查不到
    assert auction_pool.get_anomaly_history("999999", days=5) == []


def test_sync_empty_returns_zero(in_mem_db):
    """空 records 不写库, 返回 0。"""
    assert auction_pool.sync_auction_to_db([]) == 0


# ── register_cron: 复用现有 APScheduler, 不新开 ────────────────────────────
def test_register_cron_reuses_scheduler():
    """把 job 加到传入的现有调度器上, 且 id 唯一、触发时刻正确。"""
    sched = Mock()
    assert auction_pool.register_cron(sched) is True
    sched.add_job.assert_called_once()
    args = sched.add_job.call_args[0]
    kw = sched.add_job.call_args.kwargs if hasattr(sched.add_job.call_args, "kwargs") else sched.add_job.call_args[1]
    assert args[1] == "cron"          # trigger 以位置参数传入
    assert kw["day_of_week"] == "mon-fri"
    assert kw["hour"] == 9
    assert kw["minute"] == 25
    assert kw["id"] == "auction_anomaly_daily_sync"
    assert kw.get("replace_existing") is True


def test_register_cron_none_safe():
    """传入 None / 无 add_job 对象 -> 返回 False, 不崩。"""
    assert auction_pool.register_cron(None) is False
    assert auction_pool.register_cron(object()) is False
