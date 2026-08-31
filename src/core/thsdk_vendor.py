# -*- coding: utf-8 -*-
"""thsdk/wencai_vendor.py (SIDA 2026-08-31 Hermes D4)
=====================================================
集中接入同花顺 thsdk 1.7.18 (云端版 L2 已开通), 镜像 tq.py 接口契约让上层
Engine 可无感切换 TDX ↔ thsdk ↔ wencai。

P0 范围 (今晚): 仅 WENCAI (板块/龙虎榜/涨停归因等 30+ 项 §11 实测命中)。
P1 范围 (明天盘中验证后): 大单/tick_super_level1/klines 等深度接口, 经 D1 两阶段门禁通过后启用。

凭据: THS_USERNAME/THS_PASSWORD 走 .env.sida.local (不入 git, 见 .gitignore)。
本文件绝不出现明文凭据。
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

# thsdk 仅在调用时延迟导入 (容器/镜像层不一定安装; P0 凭据未注入前调用即 fail)
try:
    from thsdk import THS  # type: ignore
except Exception:  # ModuleNotFoundError 等
    THS = None  # type: ignore

from src.core.utils.rate_limit import CircuitBreaker, RateLimiter, with_retry

logger = logging.getLogger(__name__)

# ----- 凭据 (.env 治理) -----
def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"{name} 未设置。请在 .env.sida.local 配置 (不入 git, 见 .gitignore); "
            f"参考 docs/thsdk_setup.md。"
        )
    return val


# ----- 默认限频 (Hermes D4 §一致性) -----
# thsdk_alert.RATE_LIMIT_SEC=0.05 (thsdk 高频); wencai_nlp 250ms (手册 §4.5)
THSDK_DEFAULT_INTERVAL_SEC = float(os.environ.get("THSDK_RATE_LIMIT_SEC", "0.05"))
WENCAI_DEFAULT_INTERVAL_SEC = float(os.environ.get("WENCAI_RATE_LIMIT_SEC", "0.5"))

# 默认熔断: 60s 窗口 10 失败 熔断 60s (对齐 thsdk_alert 现行)
DEFAULT_BREAKER = lambda: CircuitBreaker(window_sec=60.0, max_fail=10, cooldown_sec=60.0)


class ThsdkVendor:
    """thsdk 集中封装: 凭据从 env 读, 限频 + 熔断 + 重试统一, 镜像 tq.py 接口契约。

    用法:
        v = ThsdkVendor()
        df_or_list = v.wencai_query("今日主力资金净流入排名前20, 非ST")
        df_or_list = v.wencai_queries(["query1", "query2"])
        df = v.klines("USZA002361", interval="day", count=100)
    """

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None,
                 *, rate_limit_sec: Optional[float] = None,
                 breaker: Optional[CircuitBreaker] = None) -> None:
        self._username = username or _require_env("THS_USERNAME")
        self._password = password or _require_env("THS_PASSWORD")
        if THS is None:
            raise RuntimeError("thsdk 未安装。请在镜像/容器内 `pip install thsdk==1.7.18`。")
        # 限频: thsdk 默认 50ms; wencai 默认 500ms (手动指定可覆盖)
        self._rate_thsdk = RateLimiter(min_interval_sec=rate_limit_sec or THSDK_DEFAULT_INTERVAL_SEC)
        self._rate_wencai = RateLimiter(min_interval_sec=WENCAI_DEFAULT_INTERVAL_SEC)
        self._breaker = breaker if breaker is not None else DEFAULT_BREAKER()
        self._login_lock = threading.Lock()
        self._ths = None  # THS 客户端 (会话保持)

    @property
    def username(self) -> str:
        return self._username

    def _ensure_login(self) -> Any:
        """获取登录的 THS 句柄 (会话保持, 避免每次调用重登)。"""
        if self._ths is not None:
            return self._ths
        with self._login_lock:
            if self._ths is not None:
                return self._ths
            # THS 是 context manager; 此处手动 enter 以维持长连接
            self._ths = THS({"username": self._username,
                              "password": self._password, "mac": ""})
            self._ths.__enter__()
            return self._ths

    def _call(self, method_name: str, rate: RateLimiter, *args, **kwargs) -> Any:
        if not self._breaker.allow():
            raise RuntimeError(f"thsdk circuit breaker open ({method_name})")
        rate.wait()
        ths = self._ensure_login()
        method = getattr(ths, method_name)
        try:
            return method(*args, **kwargs)
        except Exception:
            self._breaker.fail()
            raise

    # ---------- wencai (P0: 本晚启用) ----------
    def wencai_query(self, query: str, **kwargs) -> Any:
        """wencai_nlp 单条查询, 带限频+熔断+重试。"""
        return with_retry(
            self._call, "wencai_nlp", self._rate_wencai, query, **kwargs,
            retries=3, backoff=[1.0, 2.0, 4.0], breaker=self._breaker,
        )

    def wencai_queries(self, queries: List[str]) -> List[Any]:
        """批量查询 (替代 thsdk_alert.WENCAI_QUERIES 直接调用)。"""
        out: List[Any] = []
        for q in queries:
            try:
                resp = self.wencai_query(q)
                out.append({"query": q, "ok": True, "resp": resp})
            except Exception as e:
                logger.warning("wencai query 失败: %s | %s", q, e)
                out.append({"query": q, "ok": False, "error": str(e)})
        return out

    # ---------- 深度接口 (P1: 明天盘中验证后启用) ----------
    def klines(self, code: str, **kwargs):
        return with_retry(self._call, "klines", self._rate_thsdk, code, **kwargs,
                          retries=3, backoff=[1.0, 2.0, 4.0], breaker=self._breaker)

    def market_data_cn(self, codes: List[str], **kwargs):
        return with_retry(self._call, "market_data_cn", self._rate_thsdk, codes, **kwargs,
                          retries=3, backoff=[1.0, 2.0, 4.0], breaker=self._breaker)

    def big_order_flow(self, code: str, **kwargs):  # P1
        return with_retry(self._call, "big_order_flow", self._rate_thsdk, code, **kwargs,
                          retries=3, backoff=[1.0, 2.0, 4.0], breaker=self._breaker)

    def tick_super_level1(self, code: str, **kwargs):  # P1
        return with_retry(self._call, "tick_super_level1", self._rate_thsdk, code, **kwargs,
                          retries=3, backoff=[1.0, 2.0, 4.0], breaker=self._breaker)

    def close(self) -> None:
        if self._ths is not None:
            try:
                self._ths.__exit__(None, None, None)
            except Exception:
                pass
            self._ths = None

    def __enter__(self) -> "ThsdkVendor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ----- 集中查询库 (替代 thsdk_alert.WENCAI_QUERIES 的内联常量) -----
# 完整 wencai 查询清单迁移到这里 (Hermes D4 §Q1: 与 thsdk_alert 现有
# WENCAI_QUERIES 合并为统一 wencai_queries 配置)
WENCAI_QUERIES_DEFAULT: List[str] = [
    # === 已有 (thsdk_alert 现有 2 条) ===
    "主力资金流入升, 股价反向上行, 非ST",
    "近5日主力净流入增加, 非ST",
    # === 手册 §11 补测命中可补齐决策先锋候选池 (Hermes D4 §Q1) ===
    "今日涨停个股及涨停原因",
    "今日连板个股, 显示连板数",
    "今日主力资金净流入排名前20的个股",
    "今日主力资金净流出排名前20的个股",
    "近5日主力资金净流入排名前20的个股, 非ST",
    "今日行业板块涨幅排名前20",
    "今日概念板块涨幅排名前20",
    "今日市场宽度: 上涨家数占比, 非ST",
    "近3日主力连续净流入的个股, 非ST",
]


def get_default_queries() -> List[str]:
    """供 thsdk_alert / board / future alert 模块统一读查询库。"""
    return list(WENCAI_QUERIES_DEFAULT)


def get_vendor() -> ThsdkVendor:
    """默认 vendor (凭据 .env, 限频/熔断默认)。"""
    return ThsdkVendor()
