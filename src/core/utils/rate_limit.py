# -*- coding: utf-8 -*-
"""统一限流器 (SIDA 2026-08-31 Hermes D4)
==============================================
thsdk_alert 已有自写 RateLimiter (RATE_LIMIT_SEC=0.05) 与 CircuitBreaker (60s/10失败/熔断60s);
按 Hermes D4 要求, 抽到 utils/rate_limit.py 供 thsdk_vendor / wencai_vendor / wencai_alert
统一使用, 避免三路各写一份。
"""

from __future__ import annotations

import time
import threading
from collections import deque
from typing import Optional


class RateLimiter:
    """固定间隔限流器(令牌桶近似, 单调用线程安全)。"""

    def __init__(self, min_interval_sec: float = 0.05) -> None:
        if min_interval_sec <= 0:
            raise ValueError("min_interval_sec must be > 0")
        self._interval = float(min_interval_sec)
        self._lock = threading.Lock()
        self._last_call_ts: float = 0.0

    def wait(self) -> None:
        """阻塞直到距上次调用 ≥ min_interval_sec。"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_ts
            if self._last_call_ts > 0 and elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_call_ts = time.monotonic()


class CircuitBreaker:
    """滑动窗口熔断: window_sec 内失败 ≥ max_fail 即熔断 cooldown_sec。
    调用方: cb.allow() -> True/False, 失败时 cb.fail(), 成功时 cb.success()。
    """

    def __init__(self, window_sec: float = 60.0, max_fail: int = 10,
                 cooldown_sec: float = 60.0) -> None:
        self._window = float(window_sec)
        self._max_fail = int(max_fail)
        self._cooldown = float(cooldown_sec)
        self._lock = threading.Lock()
        self._fail_times: deque[float] = deque()
        self._open_until: float = 0.0

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if now < self._open_until:
                return False
            # 清理窗口外的失败时间戳
            cutoff = now - self._window
            while self._fail_times and self._fail_times[0] < cutoff:
                self._fail_times.popleft()
            return True

    def fail(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._fail_times.append(now)
            cutoff = now - self._window
            while self._fail_times and self._fail_times[0] < cutoff:
                self._fail_times.popleft()
            if len(self._fail_times) >= self._max_fail:
                self._open_until = now + self._cooldown

    def success(self) -> None:
        with self._lock:
            self._fail_times.clear()
            self._open_until = 0.0


def with_retry(fn, *args, retries: int = 3,
               backoff: Optional[list] = None, breaker: Optional[CircuitBreaker] = None,
               **kwargs):
    """统一重试+熔断包装。fn 抛异常则按 backoff 退避重试; 超出 retries 抛最后异常。

    breaker: 若提供, 失败计入熔断; 触发熔断后直接抛 RuntimeError 短路。
    backoff: 退避秒数列表, 默认 [1.0, 2.0, 4.0]。
    """
    backoff = backoff if backoff is not None else [1.0, 2.0, 4.0]
    last_err: Optional[BaseException] = None
    for i in range(retries + 1):
        if breaker is not None and not breaker.allow():
            raise RuntimeError("circuit breaker open")
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if breaker is not None:
                breaker.fail()
            if i >= retries:
                break
            time.sleep(backoff[min(i, len(backoff) - 1)])
    assert last_err is not None
    raise last_err
