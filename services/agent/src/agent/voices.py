"""Synthesizer sockets (R6b's warm socket, made long-lived; the server shares one pool).

One socket is kept open and handed to whichever attempt reaches its first
clause. An attempt cancelled before it spoke gives the socket back; one that
spoke closes it, and the next one is opened in the background. A spare that
has idled too long is replaced, since the vendor may have dropped it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from agent.ratelimit import RateGate
from agent.vendors.types import Voice, redact, retryable

MAX_IDLE_S = 50.0


class Voices:
    def __init__(
        self,
        make: Callable[[], Voice],
        gate: RateGate,
        *,
        clock: Callable[[], float] = time.monotonic,
        attempts: int = 3,
        backoff_s: float = 1.0,
    ) -> None:
        self._make = make
        self._gate = gate
        self._clock = clock
        self._attempts = attempts
        self._backoff_s = backoff_s
        self._spare: tuple[Voice, float] | None = None
        self._pending: asyncio.Task[Voice] | None = None
        self.opened = 0
        self.retries: list[str] = []

    async def _open(self) -> Voice:
        for attempt in range(1, self._attempts + 1):
            await self._gate.acquire()
            voice = self._make()
            try:
                await voice.open()
            except Exception as exc:
                await _quiet_close(voice)
                error = redact(str(exc))
                if attempt == self._attempts or not retryable(error):
                    raise
                self.retries.append(error)
                await asyncio.sleep(self._backoff_s * 2 ** (attempt - 1))
                continue
            self.opened += 1
            return voice
        raise AssertionError("unreachable")

    async def request(self) -> float:
        """One synthesis request (``task_continue``) against the same quota as an open."""
        return await self._gate.acquire()

    def prefetch(self) -> None:
        """Open a spare in the background if there is none."""
        if self._spare is None and self._pending is None:
            self._pending = asyncio.create_task(self._open())

    async def refresh(self) -> None:
        """Replace a spare that has idled too long. Called when the user starts talking."""
        if self._spare is not None and self._clock() - self._spare[1] > MAX_IDLE_S:
            voice, _ = self._spare
            self._spare = None
            await _quiet_close(voice)
        self.prefetch()

    async def take(self) -> tuple[Voice, str]:
        """A ready socket and how it was obtained: ``warm``, ``prefetched`` or ``new``."""
        if self._spare is not None:
            voice, since = self._spare
            self._spare = None
            if self._clock() - since <= MAX_IDLE_S:
                return voice, "warm"
            await _quiet_close(voice)
        if self._pending is not None:
            pending, self._pending = self._pending, None
            try:
                return await pending, "prefetched"
            except Exception:
                pass  # fall through to a fresh open, which retries
        return await self._open(), "new"

    async def give_back(self, voice: Voice | None, *, spoke: bool) -> None:
        if voice is None:
            return
        if not spoke and self._spare is None:
            self._spare = (voice, self._clock())
            return
        await _quiet_close(voice)
        self.prefetch()

    async def close(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.cancel()
            try:
                await _quiet_close(await pending)
            except BaseException:
                pass
        if self._spare is not None:
            await _quiet_close(self._spare[0])
            self._spare = None


async def _quiet_close(voice: Voice) -> None:
    try:
        await voice.close()
    except Exception:
        pass
