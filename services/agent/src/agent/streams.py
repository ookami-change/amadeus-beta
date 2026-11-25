"""A queue-backed async iterator.

Vendor streams cross tasks: the socket reader fills one, the turn engine
drains it, and either side may be cancelled. Async generator functions do not
survive that (R6b logged ``aclose(): asynchronous generator is already
running`` when a cancelled consumer left a generator suspended in another
task). A ``Channel`` has no frame to close, so cancelling either end is safe.
"""

from __future__ import annotations

import asyncio


class Channel[T]:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[bool, T | BaseException | None]] = asyncio.Queue()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def put(self, item: T) -> None:
        if not self._closed:
            self._queue.put_nowait((False, item))

    def close(self, error: BaseException | None = None) -> None:
        """End the stream; a reader then gets ``error`` or ``StopAsyncIteration``."""
        if not self._closed:
            self._closed = True
            self._queue.put_nowait((True, error))

    def __aiter__(self) -> Channel[T]:
        return self

    async def __anext__(self) -> T:
        end, value = await self._queue.get()
        if end:
            self._queue.put_nowait((True, value))  # every later read ends too
            if isinstance(value, BaseException):
                raise value
            raise StopAsyncIteration
        return value  # type: ignore[return-value]
