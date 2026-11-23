"""Client-side request gate and retry backoff for vendor quotas.

MiniMax's domestic docs give T2A v2 a per-account RPM (10 free, 20 paid). R6
ran into it four times. Every synthesizer socket goes through ``RateGate``;
``retry`` backs off on a quota error instead of failing the sample.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

from latency.adapters.types import redact, retryable_error


class RateGate:
    """At most ``per_minute`` acquisitions in any ``window_s`` window."""

    def __init__(
        self,
        per_minute: int,
        *,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be at least 1")
        self.per_minute = per_minute
        self.window_s = window_s
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self.waited_s = 0.0
        self.acquired = 0

    def _prune(self, now: float) -> None:
        while self._stamps and self._stamps[0] <= now - self.window_s:
            self._stamps.popleft()

    def free(self) -> int:
        self._prune(self._clock())
        return self.per_minute - len(self._stamps)

    def _delay(self, need: int) -> float:
        """Seconds until ``need`` slots are free."""
        now = self._clock()
        self._prune(now)
        over = len(self._stamps) + need - self.per_minute
        if over <= 0:
            return 0.0
        return self._stamps[over - 1] + self.window_s - now + 0.01

    async def acquire(self) -> float:
        """Take one slot. Returns how long it waited, in seconds."""
        waited = 0.0
        async with self._lock:
            while (delay := self._delay(1)) > 0.0:
                await self._sleep(delay)
                waited += delay
            self._stamps.append(self._clock())
        self.waited_s += waited
        self.acquired += 1
        return waited

    async def reserve(self, need: int) -> float:
        """Wait, without taking anything, until ``need`` slots are free."""
        need = min(need, self.per_minute)
        waited = 0.0
        while (delay := self._delay(need)) > 0.0:
            await self._sleep(delay)
            waited += delay
        return waited


async def retry[T](
    action: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_s: float = 2.0,
    retryable: Callable[[str | None], bool] = retryable_error,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[int, str], None] | None = None,
) -> T:
    """Run ``action``; on a retryable error wait ``base_s * 2**n`` and try again."""
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except Exception as exc:
            error = redact(str(exc))
            if attempt == attempts or not retryable(error):
                raise
            if on_retry is not None:
                on_retry(attempt, error)
            await sleep(base_s * 2 ** (attempt - 1))
    raise AssertionError("unreachable")
