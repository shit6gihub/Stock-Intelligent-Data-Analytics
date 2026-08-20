"""恒生接入测试 mock 数据与桩(阶段2, v0.4.0)。

伪造 get_hs_fund_flow 的返回(get_hs_fund_flow-shaped)与恒生 client mock
provider(AStockCashFlow 原始行), 供 test_main_flow_hengsheng 精确断言。

三源样例(神剑股份 002361, 20260819 mock):
- tencent main_net: -1,683,200,000 -> 需做成 -1683.2 万? 见样例用元。
- 本模块提供可控的 net / dde_ratio / rising_up_days, 测试自己选值。
"""
from __future__ import annotations


def hs_day(
    date: str = "20260819",
    main_net: float = -23_600_000.0,
    big_net_dde: float = -23_600_000.0,
    dde_ratio: float = -7.8,
    rising_up_days: int = 10,
    super_large_net: float = -11_800_000.0,
    large_net: float = -7_080_000.0,
    medium_net: float = -3_540_000.0,
    small_net: float = 1_180_000.0,
    change_pct: float = -0.4,
    close: float = 12.38,
) -> dict:
    """单日恒生资金流 dict(get_hs_fund_flow.days 元素)。"""
    return {
        "date": date,
        "main_net": main_net,
        "big_net_dde": big_net_dde,
        "big_net_dde_ratio": dde_ratio,
        "rising_up_days": rising_up_days,
        "super_large_net": super_large_net,
        "large_net": large_net,
        "medium_net": medium_net,
        "small_net": small_net,
        "change_pct": change_pct,
        "close": close,
    }


def make_hs_fund_flow(
    symbol: str = "002361",
    net: float = -23_600_000.0,
    dde_ratio: float = -7.8,
    rising_up_days: int = 10,
    days: int = 10,
    date: str = "20260819",
) -> dict:
    """构造 get_hs_fund_flow 形状的可用结果(最近日 main_net=net)。"""
    rows = []
    start_day = 10  # 20260810..20260819 共 10 天(不再依赖传入 date 的具体值)
    for i in range(days):
        rows.append(hs_day(
            date=f"202608{start_day + i:02d}", main_net=net, big_net_dde=net,
            dde_ratio=dde_ratio, rising_up_days=rising_up_days,
            super_large_net=round(net * 0.5), large_net=round(net * 0.3),
            medium_net=round(net * 0.15), small_net=-round(net * 0.05),
        ))
    rows.sort(key=lambda x: x["date"])
    return {
        "available": True,
        "stockObject": f"{symbol}.SZ",
        "days": rows,
        "latest_dde_ratio": dde_ratio,
        "latest_rising_up_days": rising_up_days,
        "source": "hengsheng",
        "note": None,
    }


class FakeMockProvider:
    """恒生 client 的 mock provider(带 .call 方法), 直接喂原始行。"""

    def __init__(self, data):
        self.data = data
        self.calls: list[tuple] = []

    def call(self, api_id, params, batch=None):
        self.calls.append((api_id, params, batch))
        return self.data
