"""Client-side request gate for vendor quotas.

MiniMax's synchronous T2A quota on this account is 10 RPM (console,
2026-11-25). R6b gated socket opens at 18 and still met 18 RPM errors. Every
synthesizer socket here goes through one process-wide gate.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


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

    def _delay(self) -> float:
        now = self._clock()
        while self._stamps and self._stamps[0] <= now - self.window_s:
            self._stamps.popleft()
        if len(self._stamps) < self.per_minute:
            return 0.0
        return self._stamps[0] + self.window_s - now + 0.01

    async def acquire(self) -> float:
        """Take one slot. Returns how long it waited, in seconds."""
        waited = 0.0
        async with self._lock:
            while (delay := self._delay()) > 0.0:
                await self._sleep(delay)
                waited += delay
            self._stamps.append(self._clock())
        self.waited_s += waited
        self.acquired += 1
        return waited
