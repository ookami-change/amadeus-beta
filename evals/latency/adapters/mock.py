"""In-process adapters for the harness self-check. Not a vendor measurement."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from latency.adapters.types import ASRUpdate, AudioChunk, LLMDelta, LLMItem, LLMUsage, StreamItem


class MockASR:
    def __init__(self, transcript: str = "", partial: str = "") -> None:
        self.transcript = transcript
        self.partial = partial

    def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[ASRUpdate]:
        return self._transcribe(audio)

    async def _transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[ASRUpdate]:
        yielded = False
        async for chunk in audio:
            if chunk and not yielded:
                yielded = True
                yield ASRUpdate(self.partial or self.transcript, False)
        await asyncio.sleep(0.01)
        yield ASRUpdate(self.transcript, True)


class MockLLM:
    def __init__(self, reply: str = "嗯。店长。") -> None:
        self.reply = reply

    def stream(
        self,
        *,
        system: str,
        history: list[dict[str, str]],
        user: str,
    ) -> AsyncIterator[LLMItem]:
        del system, history, user
        return self._stream()

    async def _stream(self) -> AsyncIterator[LLMItem]:
        await asyncio.sleep(0.01)
        yield LLMDelta(self.reply)
        yield LLMUsage(prompt_tokens=100, cached_tokens=0)


class MockTTS:
    def __init__(self, sample_rate: int = 24000) -> None:
        self._sample_rate = sample_rate
        self.opened = False

    async def open(self, *, instruction: str | None = None) -> None:
        del instruction
        await asyncio.sleep(0.005)
        self.opened = True

    def speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        return self._speak(text)

    async def _speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        async for clause in text:
            if clause.strip():
                await asyncio.sleep(0.005)
                yield AudioChunk(pcm=b"\x00\x01" * 48, sample_rate=self._sample_rate)

    async def close(self) -> None:
        self.opened = False
