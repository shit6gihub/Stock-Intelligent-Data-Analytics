"""L2 逐笔数据源(预留, 2026-08-11)。

未来接入付费 L2(腾讯L2/新浪L2/iTick)时实现 fetch_l2_ticks,
返回与腾讯逐笔同构的 [{d, amt, vol, price, t}] 列表即可无缝替换 dark_flow 主数据源。

候选源(已调研):
  - 腾讯L2(需付费账号)
  - 新浪L2(接口已下线, 需APP逆向)
  - iTick 99 USDT/月(十档+逐笔+委托队列+WebSocket)
  - 掘金量化L2(需券商资金门槛)
"""


def fetch_l2_ticks(code: str, source: str) -> list[dict]:
    """从 L2 源拉取全天逐笔(预留接口)。

    Args:
        code: 股票代码(如 sz002361)
        source: 数据源标识(l2_tencent / l2_sina / l2_itick)

    Returns:
        [{d: B/S/M, amt: 金额, vol: 手数, price: 价格, t: HH:MM:SS}]
    """
    raise NotImplementedError(
        f"L2 数据源 {source} 未接入。当前默认免费源 tencent_ticks 运行正常, "
        "付费 L2 接入需先购买相应数据服务(见 dark_flow.DARK_SOURCE 注释)。"
    )
