"""恒生接入主力意图三源对比测试(阶段2, v0.4.0)。

覆盖:
- HengshengClient: mock provider 调用 / 未配置无 mock 时 raise
- hengsheng_fund_flow: 原始行解析 / 非法代码 / 接口异常 / 30s 缓存
- main_flow_compare: 三源全可用一致性=min pairwise / 恒生失败降级双源
  / 三源全失败返空 / dde_ratio·rising_up_days 传递
- API 端点: 响应含 hengsheng 字段
"""
from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

# tests/fixtures 非包目录, 通过 sys.path shim 引入 mock 数据辅助模块
_FX = os.path.join(os.path.dirname(__file__), "fixtures")
if _FX not in sys.path:
    sys.path.insert(0, _FX)
import mock_hs as mhs  # noqa: E402
import mock_main_flow as mmf  # noqa: E402

from src.core import main_flow_compare  # noqa: E402
from src.core import hengsheng_client, hengsheng_fund_flow  # noqa: E402


def _raw_hs_rows() -> list[dict]:
    """AStockCashFlow 原始行(与 get_hs_fund_flow 解析口径一致)。"""
    rows = []
    for i in range(5):
        net = -23_600_000.0  # 恒定净额, 便于断言
        rows.append({
            "tradingday": f"202608{15 + i:02d}",
            "totalvalue": net,
            "largenetbuyvaluedde": net,
            "largenetbuyvaluedderatio": -7.8,
            "risingupdays": 10,
            "hugenetbuyvalue": round(net * 0.5),
            "largenetbuyvalue": round(net * 0.3),
            "mediumnetbuyvalue": round(net * 0.15),
            "smallnetbuyvalue": -round(net * 0.05),
            "changepct": -0.4,
            "closeprice": 12.38,
        })
    return rows


# ── HengshengClient ────────────────────────────────────────────────
def test_client_mock_provider_returns_data():
    provider = mhs.FakeMockProvider(_raw_hs_rows())
    client = hengsheng_client.HengshengClient(mock=provider)
    out = client.call_api("AStockCashFlow", params={"stockObject": ["002361.SZ"]})
    assert isinstance(out, list) and len(out) == 5
    assert out[-1]["totalvalue"] == pytest.approx(-23_600_000.0)
    assert provider.calls[0][0] == "AStockCashFlow"


def test_client_unconfigured_no_mock_raises():
    client = hengsheng_client.HengshengClient(base_url="", api_key="", mock=None)
    with pytest.raises(hengsheng_client.HengshengUnavailableError):
        client.call_api("AStockCashFlow", params={"stockObject": ["002361.SZ"]})


# ── hengsheng_fund_flow ────────────────────────────────────────────
@pytest.fixture
def _clean_hs_cache():
    hengsheng_fund_flow.clear_cache()
    yield
    hengsheng_fund_flow.clear_cache()


@pytest.fixture
def mock_hs_default_client(monkeypatch):
    """让 get_hs_fund_flow 使用可控的 FakeMockProvider client。"""
    provider = mhs.FakeMockProvider(_raw_hs_rows())
    client = hengsheng_client.HengshengClient(mock=provider)
    monkeypatch.setattr(hengsheng_fund_flow, "get_default_client",
                        lambda: client)
    return client, provider


def test_fund_flow_parses_days(_clean_hs_cache, mock_hs_default_client):
    client, provider = mock_hs_default_client
    r = hengsheng_fund_flow.get_hs_fund_flow("002361")
    assert r["available"] is True
    assert r["stockObject"] == "002361.SZ"
    assert len(r["days"]) == 5
    latest = r["days"][-1]
    assert latest["main_net"] == pytest.approx(-23_600_000.0)
    assert latest["big_net_dde"] == pytest.approx(-23_600_000.0)
    assert r["latest_dde_ratio"] == pytest.approx(-7.8)
    assert r["latest_rising_up_days"] == 10
    # 真实调用 AStockCashFlow + RealStockFundFlow 各一次
    api_ids = [c[0] for c in provider.calls]
    assert "AStockCashFlow" in api_ids


def test_fund_flow_invalid_symbol(_clean_hs_cache):
    r = hengsheng_fund_flow.get_hs_fund_flow("abc123")
    assert r["available"] is False
    assert r["note"]


def test_fund_flow_client_error(_clean_hs_cache, monkeypatch):
    def boom(api_id, params, batch=None):
        raise RuntimeError("connect refused")

    client = hengsheng_client.HengshengClient(mock=mhs.FakeMockProvider(boom))
    monkeypatch.setattr(hengsheng_fund_flow, "get_default_client", lambda: client)
    r = hengsheng_fund_flow.get_hs_fund_flow("002361")
    assert r["available"] is False
    assert "恒生" in (r["note"] or "")


def test_fund_flow_cache_hit(_clean_hs_cache, mock_hs_default_client):
    client, provider = mock_hs_default_client
    hengsheng_fund_flow.get_hs_fund_flow("002361")
    _clean_hs_cache  # 保持 TTL 内
    hengsheng_fund_flow.get_hs_fund_flow("002361")
    n_cf = sum(1 for c in provider.calls if c[0] == "AStockCashFlow")
    assert n_cf == 1  # 缓存命中, 不重复调 AStockCashFlow


def test_extract_hengsheng_none_when_unavailable():
    assert main_flow_compare._extract_hengsheng({"available": False, "days": []}) is None
    assert main_flow_compare._extract_hengsheng(None) is None


# ── main_flow_compare 三源 ─────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean_compare_cache():
    main_flow_compare.clear_cache()
    yield
    main_flow_compare.clear_cache()


@pytest.fixture
def mock_thsdk(monkeypatch):
    mod = mmf.fake_thsdk_l2_module()
    mod.compute_main_flow = MagicMock(return_value=dict(mmf.THSDK_OPPOSITE))
    monkeypatch.setitem(sys.modules, "data_source.thsdk_l2", mod)
    return mod


@pytest.fixture
def mock_tencent(monkeypatch):
    m = MagicMock(return_value=dict(mmf.TENCENT_OK))  # +500 万 元
    monkeypatch.setattr("src.core.dark_flow.compute_dark_flow", m)
    return m


@pytest.fixture
def mock_hengsheng(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr("src.core.hengsheng_fund_flow.get_hs_fund_flow", m)
    return m


def test_three_source_all_available(mock_tencent, mock_thsdk, mock_hengsheng):
    # 腾讯 +500万, thsdk -500万, 恒生 -2360万
    mock_hengsheng.return_value = mhs.make_hs_fund_flow("002361", net=-23_600_000,
                                                        dde_ratio=-7.8, rising_up_days=10)
    r = main_flow_compare.compare_main_flow("002361")
    assert r["hengsheng"]["available"] is True
    assert r["hengsheng"]["latest_date"]
    assert r["hengsheng"]["main_net"] == pytest.approx(-23_600_000.0)
    assert r["dde_ratio"] == pytest.approx(-7.8)
    assert r["rising_up_days"] == 10
    # 三源一致性 = min pairwise
    pairs = [
        main_flow_compare._pairwise_consistency(5e6, -5e6),
        main_flow_compare._pairwise_consistency(5e6, -23.6e6),
        main_flow_compare._pairwise_consistency(-5e6, -23.6e6),
    ]
    assert r["consistency"] == pytest.approx(min(pairs), abs=0.05)
    assert r["delta_pct"] == pytest.approx(100.0 - r["consistency"], abs=0.05)
    assert len(r["notes"]) == 3
    assert all("可用" in n for n in r["notes"])


def test_three_source_dde_fields_passthrough(mock_tencent, mock_thsdk, mock_hengsheng):
    mock_hengsheng.return_value = mhs.make_hs_fund_flow(
        "002361", net=-5_000_000, dde_ratio=-3.2, rising_up_days=6)
    r = main_flow_compare.compare_main_flow("002361")
    h = r["hengsheng"]
    assert h["big_net_dde"] == pytest.approx(-5_000_000.0)
    assert h["dde_ratio"] == pytest.approx(-3.2)
    assert h["rising_up_days"] == 6
    assert h["super_large_net"] == pytest.approx(-2_500_000.0)
    assert h["large_net"] == pytest.approx(-1_500_000.0)
    assert h["medium_net"] == pytest.approx(-750_000.0)
    assert h["small_net"] == pytest.approx(250_000.0)


def test_hengsheng_fail_degrade_dual(mock_tencent, mock_thsdk, mock_hengsheng):
    # 恒生抛异常 -> 降级双源(腾讯 vs thsdk)
    mock_hengsheng.side_effect = Exception("hengsheng 连接失败")
    r = main_flow_compare.compare_main_flow("002361")
    assert r["hengsheng"] is None
    assert r["tencent"]["available"] is True
    assert r["thsdk"]["available"] is True
    assert r["consistency"] is not None  # 双源仍可比
    assert any("hengsheng" in n and "数据暂不可用" in n for n in r["notes"])
    assert "hengsheng" in r["note"]


def test_all_three_fail_returns_empty(mock_thsdk, mock_hengsheng, monkeypatch):
    monkeypatch.setattr("src.core.dark_flow.compute_dark_flow",
                        MagicMock(return_value=dict(mmf.TENCENT_INSUFFICIENT)))
    mock_thsdk.compute_main_flow.side_effect = Exception("thsdk 连接失败")
    mock_hengsheng.side_effect = Exception("hengsheng 连接失败")
    r = main_flow_compare.compare_main_flow("002361")
    assert r["tencent"]["available"] is False
    assert r["thsdk"] is None
    assert r["hengsheng"] is None
    assert r["consistency"] is None
    assert r["delta_pct"] is None


def test_two_source_when_hengsheng_missing_data(mock_tencent, mock_thsdk, mock_hengsheng):
    # 恒生接口通但无数据(available False) -> 双源一致性
    mock_hengsheng.return_value = {"available": False, "days": [], "note": "无数据"}
    r = main_flow_compare.compare_main_flow("002361")
    assert r["hengsheng"] is None
    assert r["consistency"] is not None


def test_consistency_three_math():
    c, d = main_flow_compare._consistency_three([5e6, 5e6, 5e6])
    assert c == 100.0 and d == 0.0
    c2, d2 = main_flow_compare._consistency_three([5e6, -5e6, _min := -23.6e6])
    pairs = [main_flow_compare._pairwise_consistency(5e6, -5e6),
             main_flow_compare._pairwise_consistency(5e6, -23.6e6),
             main_flow_compare._pairwise_consistency(-5e6, -23.6e6)]
    assert c2 == pytest.approx(min(pairs), abs=0.05)
    assert d2 == pytest.approx(100.0 - c2, abs=0.05)


# ── API 端点 ──────────────────────────────────────────────────────
def test_api_endpoint_contains_hengsheng(mock_tencent, mock_thsdk, mock_hengsheng):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.api.main_flow import router

    mock_hengsheng.return_value = mhs.make_hs_fund_flow("002361", net=-23_600_000)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.get("/compare/002361")
    assert resp.status_code == 200
    data = resp.json()
    assert "hengsheng" in data
    assert data["hengsheng"]["available"] is True
    assert data["hengsheng"]["main_net"] == pytest.approx(-23_600_000.0)
    assert "notes" in data
    assert "consistency" in data and "delta_pct" in data
    assert data["dde_ratio"] is not None
    assert data["rising_up_days"] is not None


def test_api_validate_symbol():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.api.main_flow import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.get("/compare/notacode")
    assert resp.status_code == 400
