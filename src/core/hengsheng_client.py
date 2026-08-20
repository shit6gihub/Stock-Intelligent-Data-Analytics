"""恒生数据库(聚源)调用客户端 v0.4.0。

封装恒生金融数据库 HTTP API(自带 Mock 回退):
- 配置来源: 环境变量 HENGSHENG_BASE_URL + HENGSHENG_API_KEY(Bearer Token)。
- 凭证缺失时自动进入 Mock 模式(开发/测试友好, 让整条链路先跑通)。
- lazy 连接: 首次 call_api 才建会话; 失败 raise 但不 panic(供 main_flow_compare 容错降级)。

用法:
    client = HengshengClient()          # 读 env; 无凭证→Mock 模式
    rows = client.call_api("AStockCashFlow", params={"stockObject": ["002361.SZ"], ...})
    client.close()

Mock 模式可注入自定义 mock 后端(mock=<object with call(api_id, params, batch)>),
测试用 tests/fixtures/mock_hs.py 提供的工厂构造确定性数据。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# API 名称 -> 路径(见"金融数据库接口全量文档" 2.6 / 3.1)。
_API_PATHS: dict[str, str] = {
    "AStockCashFlow": "/cloudtest/apigateway/v2/rag/quote/stockcapflow",
    "RealStockFundFlow": "/cloudtest/apigateway/v2/rag/quote/realStockFundFlow",
}

_HENGSHENG_TIMEOUT = (5.0, 30.0)  # (connect, read)


class HengshengUnavailableError(RuntimeError):
    """恒生凭证缺失 / 接口不可用。调用方应捕获并降级(返 None + note)。"""


class HengshengClient:
    """恒生数据库 HTTP 客户端(带 Mock 回退)。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        mock: object | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("HENGSHENG_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("HENGSHENG_API_KEY") or ""
        self._configured = bool(self.base_url and self.api_key)
        self._mock = mock
        self._session = None

    @property
    def available(self) -> bool:
        """已配置真实凭证(否则走 Mock)。"""
        return self._configured

    @property
    def mode(self) -> str:
        return "real" if self._configured else "mock"

    def connect(self) -> None:
        """lazy 建连(仅在真实模式且尚无会话时)。Mock 模式无需网络。"""
        if self._configured and self._session is None:
            try:
                import requests

                self._session = requests.Session()
            except ImportError as e:  # pragma: no cover - 环境缺 requests
                raise HengshengUnavailableError(f"requests 未安装: {e!r}") from e

    def close(self) -> None:
        """关闭会话。"""
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001 - 关闭资源失败不升级
                pass
            self._session = None

    def call_api(self, api_id: str, params: dict | None = None,
                 batch: str | None = None, fmt: str = "json") -> list | dict:
        """调用恒生接口。

        :param api_id: 接口名(如 AStockCashFlow / RealStockFundFlow)
        :param params: 接口参数(如 {"stockObject": ["002361.SZ"], ...})
        :param batch: 可选批处理参数(透传)
        :param fmt: 返回格式(默认 json)
        :return: 接口返回(尽力归一化为 list[dict] 或 dict)
        :raises HengshengUnavailableError: 未配置凭证且无 mock
        """
        params = params or {}
        if self._mock is not None:
            return self._mock.call(api_id, params, batch)
        if not self._configured:
            raise HengshengUnavailableError(
                "HENGSHENG_BASE_URL / HENGSHENG_API_KEY 未配置, 且未注入 mock"
            )
        self.connect()
        path = _API_PATHS.get(api_id)
        if not path:
            raise HengshengUnavailableError(f"未知恒生接口: {api_id!r}")
        url = f"{self.base_url}{path}"
        body: dict = {"apiId": api_id, "format": fmt, "params": params}
        if batch is not None:
            body["batch"] = batch
        try:
            resp = self._session.post(
                url, headers={"Authorization": f"Bearer {self.api_key}"},
                json=body, timeout=_HENGSHENG_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001 - 网络/HTTP 异常统一包装
            logger.warning("[hengsheng] %s 调用失败: %r", api_id, e)
            raise HengshengUnavailableError(f"{api_id} 调用失败: {e!r}") from e
        return resp.json()


def get_default_client() -> HengshengClient:
    """进程内单例(懒加载): 供 hengsheng_fund_flow 使用。

    凭证缺失时自动注入 _DefaultMockProvider, 让整条链路在无 key 环境下跑通。
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = HengshengClient(mock=_DefaultMockProvider())
    return _DEFAULT


def configure_client(base_url: str, api_key: str) -> None:
    """强制切换真实凭证(凭证到位后由 .env 或运维调用)。"""
    global _DEFAULT
    _DEFAULT = HengshengClient(base_url=base_url, api_key=api_key)
    logger.info("[hengsheng] 已切换到真实接口模式")


class _DefaultMockProvider:
    """内置确定性 Mock: 凭证缺失时返回合成的 AStockCashFlow 数据。

    只做占位(让三源链路跑通), 测试精确断言用 tests/fixtures/mock_hs.py 注入。
    """

    def call(self, api_id: str, params: dict, batch: str | None) -> list:
        objs = params.get("stockObject") or []
        code = (objs[0] if isinstance(objs, list) and objs else "002361.SZ") or "002361.SZ"
        seed = sum(ord(c) for c in code)
        days = 10
        rows: list[dict] = []
        for i in range(days):
            # 单调递减的主力口径(净流出), 让一致性样例呈现"三源偏空"。
            dde = -(2_300_000 + seed % 1_000_000 + i * 80_000)
            date = f"202608{19 - i:02d}"
            rows.append({
                "tradingday": date,
                "totalvalue": dde,
                "largenetbuyvaluedde": dde,
                "largenetbuyvaluedderatio": round(dde / 300_000_000 * 100, 2),
                "risingupdays": max(0, 10 - i),
                "hugenetbuyvalue": round(dde * 0.5),
                "largenetbuyvalue": round(dde * 0.3),
                "mediumnetbuyvalue": round(dde * 0.15),
                "smallnetbuyvalue": -round(dde * 0.05),
                "changepct": -0.4,
                "closeprice": 12.4 - i * 0.05,
            })
        return rows[::-1]


_DEFAULT: HengshengClient | None = None

