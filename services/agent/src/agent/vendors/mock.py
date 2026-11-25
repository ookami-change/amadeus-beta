"""In-process vendors for tests and ``--mock`` runs. Free, and never touch the network."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable

from agent.streams import Channel
from agent.vendors.types import ASRUpdate, AudioChunk, TTSEvent, VendorError, WordMark

BYTES_PER_MS = 32  # 16 kHz s16le


class MockASR:
    """``script(audio_ms) -> text``: the hypothesis after that much audio. Final on ``finish``."""

    def __init__(self, script: Callable[[float], str], *, lag_ms: float = 0.0) -> None:
        self.script = script
        self.lag_ms = lag_ms
        self.started = 0

    async def start(self) -> MockASRStream:
        self.started += 1
        return MockASRStream(self.script, self.lag_ms)


class MockASRStream:
    def __init__(self, script: Callable[[float], str], lag_ms: float) -> None:
        self._script = script
        self._lag_s = lag_ms / 1000.0
        self._heard_ms = 0.0
        self._last = ""
        self.updates: Channel[ASRUpdate] = Channel()

    async def send(self, pcm: bytes) -> None:
        self._heard_ms += len(pcm) / BYTES_PER_MS
        text = self._script(self._heard_ms)
        if text and text != self._last:
            self._last = text
            self._later(ASRUpdate(text, False))

    async def finish(self) -> None:
        text = self._script(self._heard_ms)
        self._later(ASRUpdate(text, True), close=True)

    def _later(self, update: ASRUpdate, close: bool = False) -> None:
        def deliver() -> None:
            self.updates.put(update)
            if close:
                self.updates.close()

        asyncio.get_running_loop().call_later(self._lag_s, deliver)

    async def close(self) -> None:
        self.updates.close()


class MockLLM:
    def __init__(
        self,
        reply: str = "嗯。店长，你来了。",
        *,
        first_token_s: float = 0.0,
        fail: str | None = None,
    ):
        self.reply = reply
        self.first_token_s = first_token_s
        self.fail = fail
        self.calls: list[str] = []

    async def stream(
        self, *, system: str, history: list[dict[str, str]], user: str
    ) -> AsyncGenerator[str, None]:
        self.calls.append(user)
        await asyncio.sleep(self.first_token_s)
        if self.fail:
            raise VendorError("mock-llm", self.fail)
        for char in self.reply:
            yield char


class MockVoice:
    """Each clause comes back as 100 ms of audio per character, ``first_audio_s`` after the last."""

    opened = 0
    failures: list[str] = []

    def __init__(self, *, first_audio_s: float = 0.0) -> None:
        self.first_audio_s = first_audio_s
        self.events: Channel[TTSEvent] = Channel()
        self.said: list[str] = []
        self._pending: list[asyncio.Task[None]] = []
        self._at_ms = 0.0
        self.closed = False

    async def open(self) -> None:
        if MockVoice.failures:
            raise VendorError("mock-tts", MockVoice.failures.pop(0))
        MockVoice.opened += 1

    async def say(self, text: str) -> None:
        self.said.append(text)
        start = self._at_ms
        self._at_ms += 100.0 * len(text)

        before = self._pending[-1] if self._pending else None

        async def emit() -> None:
            if before is not None:
                await before
            await asyncio.sleep(self.first_audio_s)
            self.events.put(WordMark(start, self._at_ms, text))
            self.events.put(AudioChunk(b"\x00\x00" * 32 * 100 * len(text), 32000))

        self._pending.append(asyncio.create_task(emit()))

    async def finish(self) -> None:
        pending = list(self._pending)

        async def done() -> None:
            await asyncio.gather(*pending)
            self.events.close()

        self._pending.append(asyncio.create_task(done()))

    async def close(self) -> None:
        self.closed = True
        for task in self._pending:
            task.cancel()
        self.events.close()
