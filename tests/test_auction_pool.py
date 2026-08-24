"""竞价异动池测试(阶段1.2, v0.3.0; 2026-08-24 字段口径更新)。

覆盖:
- fetch_auction_anomaly: 市场映射 CN->USHA / SZ->USZA, DataFrame 转 dict, 代码归一化
- 30s 进程内缓存命中 / 过期
- 数据源不可用(未安装/抛异常) -> [] 容错
- sync_auction_to_db / get_anomaly_history: 用独立内存 SQLite 引擎验证 DB 读写
- register_cron: 复用现有调度器(不新开), job 成功注册 / 传入 None 不崩
- 字段口径(2026-08-24): 实测 thsdk 仅返回 6 列, withdraw_rate/volume_ratio 固定 None;
  gap_pct 在 klines mock 缺失环境下也为 None(测试文件 tests/test_auction_gap.py 覆盖
  klines mock 后 gap_pct 二次计算 + 脏数据过滤)
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
    """默认 market=CN -> 映射 USHA, 且 DataFrame 正确转 list[dict]。

    2026-08-24 口径: 实测 6 列 -> 撤单率/量比固定 None;
    gap_pct 在 klines 缺失环境下保持 None(由 test_auction_gap.py 覆盖有 klines 场景)。
    """
    recs = auction_pool.fetch_auction_anomaly("CN")
    mock_thsdk_l2.get_auction_anomaly.assert_called_once_with("USHA")
    assert len(recs) == 2
    first = recs[0]
    assert first["symbol"] == "002361"
    assert first["code"] == "002361"
    assert first["name"] == "神剑股份"
    # 字段口径更新(2026-08-24)
    assert first["gap_pct"] is None         # 无 klines mock, 无法二次计算
    assert first["withdraw_rate"] is None   # 数据源不提供
    assert first["volume_ratio"] is None    # 数据源不提供
    # 内部字段(供 _compute_gap_pct 用)
    assert first["price_raw"] == pytest.approx(8.32)
    assert first["anomaly_type"] == "高开"


def test_fetch_sz_maps_to_usza(mock_thsdk_l2):
    """market=SZ -> USZA。"""
    auction_pool.fetch_auction_anomaly("SZ")
    mock_thsdk_l2.get_auction_anomaly.assert_called_once_with("USZA")


def test_fetch_symbol_normalize(mock_thsdk_l2):
    """thsdk 前缀(USZA002361)与交易所后缀(002361.SZ)都归一化到 6 位代码。"""
    import pandas as pd

    df = pd.DataFrame(
        [
            {"时间": "09:25", "价格": 10.0, "总金额": 100.0,
             "代码": "USZA000001", "名称": "平安银行", "异动类型1": "高开"},
            {"时间": "09:25", "价格": 20.0, "总金额": 200.0,
             "代码": "002361.SZ", "名称": "神剑", "异动类型1": "高开"},
        ]
    )
    mock_thsdk_l2.get_auction_anomaly.return_value = df
    recs = auction_pool.fetch_auction_anomaly("CN")
    symbols = {r["symbol"] for r in recs}
    assert symbols == {"000001", "002361"}


def test_fetch_legacy_cols_backcompat(mock_thsdk_l2):
    """2026-08-24 兼容: 老格式(高开幅度/撤单率/量比 列)仍能解析抽出字段。

    旧 thsdk 版本可能返回这 3 列, _to_records 用关键词模糊匹配仍能抓出;
    但后续 _compute_gap_pct 会用 (价格/昨收 - 1)*100 覆盖 gap_pct(若 klines 缺失则 None)。
    """
    mock_thsdk_l2.get_auction_anomaly.return_value = mmf.fake_auction_df_with_legacy_cols()
    recs = auction_pool.fetch_auction_anomaly("CN")
    first = recs[0]
    # 旧列兼容: 抽出后被二次计算覆盖(无 klines mock -> None)
    assert first["gap_pct"] is None
    # 旧 withdraw_rate/volume_ratio 不再由新数据源提供,但若 DataFrame 中残留旧列,仍可抽出
    assert first["withdraw_rate"] == pytest.approx(0.243)
    assert first["volume_ratio"] == pytest.approx(2.5)


def test_fetch_cache_hit(mock_thsdk_l2):
    """30s 内二次调用命中缓存, get_auction_anomaly 只调一次。"""
    auction_pool.fetch_auction_anomaly("CN")
    auction_pool.fetch_auction_anomaly("CN")
    assert mock_thsdk_l2.get_auction_anomaly.call_count == 1


def test_fetch_cache_expired(mock_thsdk_l2, monkeypatch):
    """超过 30s TTL 后重新拉取。

    2026-08-24: fetch_auction_anomaly 增加了 _compute_gap_pct -> _batch_prev_close ->
    SQLAlchemy 内部多次调用 time.time()(连接池 starttime 计时)。fake_time 难以精准控制。
    改为直接预置 _cache(写入一个"很旧的时间戳"), 第 2 次 fetch 时 now - cached[0] 必然 > 30s。
    """
    # 预置一个 100s 前的缓存(模拟"已经过期 30s 缓存")
    auction_pool._cache["CN"] = (time.time() - 100.0, [{"symbol": "000001", "name": "stale"}])
    auction_pool.fetch_auction_anomaly("CN")
    # 缓存过期 -> 重新拉取, 应再调一次 get_auction_anomaly
    assert mock_thsdk_l2.get_auction_anomaly.call_count == 1


def test_fetch_thsdk_unavailable_fallback(mock_thsdk_l2):
    """数据源抛异常 -> 返回 [], 不崩。"""
    mock_thsdk_l2.get_auction_anomaly.side_effect = Exception("thsdk 连接失败")
    assert auction_pool.fetch_auction_anomaly("CN") == []


def _thsdk_actually_importable() -> bool:
    """2026-08-20 辅助: 检测 thsdk 在当前环境下能否真正 import。"""
    try:
        import data_source.thsdk_l2  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    _thsdk_actually_importable(),
    reason="thsdk 实际可 import (本环境已装 thsdk); 这个用例只验证 ImportError 路径",
)
def test_fetch_thsdk_module_missing(monkeypatch):
    """thsdk 模块未安装(ImportError) -> 返回 [] 容错。

    2026-08-20 修复: 同 test_main_flow_compare.test_thsdk_module_missing
    — thsdk 实际安装时 delitem 后仍可重导入, 触发不到 ImportError 分支。
    """
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
