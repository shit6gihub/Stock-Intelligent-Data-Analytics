"""暗盘 L2 数据源接入层。

2026-08-31: 接入通达信 .tck(超盘回放落盘, 36字节委托号级, 官方方向 2B/2S)。
由 dark_flow._fetch_all_ticks 在 PANWATCH_DARK_SOURCE != "tencent_ticks" 时分发调用,
本模块抛异常/返回空 → 上层自动回退腾讯逐笔(双数据源兜底)。

数据源(环境变量 PANWATCH_DARK_SOURCE):
  tencent_ticks(默认) = 腾讯逐笔(免费, 盘中实时, 方向自解析)
  tdx_tck             = 通达信 .tck 落盘(盘后精确, 官方方向; 找不到回退腾讯)
"""
import os
from pathlib import Path


def fetch_l2_ticks(code: str, source: str, **kw) -> list[dict]:
    """按 source 拉取 L2 逐笔, 返回 [{d, amt, vol, price, t}] 同构列表。

    d='B'买/'S'卖, amt=金额(元), vol=成交量(股), price=价格, t='HH:MM:SS'。
    抛异常 = 数据源不可用, 调用方(_fetch_all_ticks)捕获后回退腾讯逐笔。
    """
    if source == "tdx_tck":
        return _fetch_tdx_tck(code)
    raise ValueError(f"未知暗盘数据源: {source}")


def _fetch_tdx_tck(code: str) -> list[dict]:
    """读通达信 .tck 落盘文件 → 逐笔(官方方向)。

    找不到目录/文件或解析失败抛异常 → 回退腾讯。
    文件: {TDX_TCK_DIR}/{code}_YYYYMMDD.tck, 取最新一份(可能跨日)。
    """
    from src.core.tdx_tick_parser import parse_tck, ticks_from_tck

    tck_dir = os.environ.get("TDX_TCK_DIR", "/app/data/tdx_tck")
    base = Path(tck_dir)
    if not base.is_dir():
        raise FileNotFoundError(f"TDX_TCK_DIR 不存在: {tck_dir}")

    files = sorted(base.glob(f"{code}_*.tck"), reverse=True)
    if not files:
        raise FileNotFoundError(f"{tck_dir} 下无 {code}_*.tck")

    trades, _orders, _cancels = parse_tck(str(files[0]))
    ticks = ticks_from_tck(trades)
    if not ticks:
        raise ValueError(f"{files[0].name} 解析出 0 条有效逐笔(连续竞价)")
    return ticks
