"""Events and interfaces shared by the vendor clients."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol

from agent.streams import Channel


@dataclass(frozen=True)
class ASRUpdate:
    """A partial or final transcript. ``text`` is the whole hypothesis, not a diff."""

    text: str
    is_final: bool


@dataclass(frozen=True)
class AudioChunk:
    """PCM s16le mono at ``sample_rate``."""

    pcm: bytes
    sample_rate: int


@dataclass(frozen=True)
class WordMark:
    """One word of synthesizer subtitles, relative to the start of the task's audio."""

    start_ms: float
    end_ms: float
    text: str


TTSEvent = AudioChunk | WordMark


class ASRStream(Protocol):
    """One recognition session. Push audio, read ``updates`` until it closes."""

    updates: Channel[ASRUpdate]

    async def send(self, pcm: bytes) -> None: ...

    async def finish(self) -> None:
        """No more audio; the final transcript follows on ``updates``."""
        ...

    async def close(self) -> None: ...


class ASR(Protocol):
    async def start(self) -> ASRStream: ...


class LLM(Protocol):
    def stream(
        self, *, system: str, history: list[dict[str, str]], user: str
    ) -> AsyncGenerator[str, None]:
        """Text deltas. Consume it with ``contextlib.aclosing`` in one task."""
        ...


class Voice(Protocol):
    """One synthesizer task on one socket: open, say clauses, finish, read ``events``."""

    events: Channel[TTSEvent]

    async def open(self) -> None: ...

    async def say(self, text: str) -> None: ...

    async def finish(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class VendorError(Exception):
    vendor: str
    message: str

    def __str__(self) -> str:
        return f"{self.vendor}: {self.message}"


SECRET_NAMES = ("VOLC_SPEECH_API_KEY", "VOLC_ACCESS_TOKEN", "DASHSCOPE_API_KEY", "MINIMAX_API_KEY")


def redact(text: str) -> str:
    """Strip credential values if a vendor echoes them back in an error."""
    cleaned = text
    for name in SECRET_NAMES:
        value = os.environ.get(name, "")
        if len(value) >= 6:
            cleaned = cleaned.replace(value, "***")
    return cleaned


def retryable(error: str | None) -> bool:
    """True when another attempt may succeed: quota, throttle, a dropped socket or a timeout."""
    if not error:
        return False
    text = error.lower()
    tokens = ("429", "limit_requests", "throttl", "rate limit", "closed", "timed out", "timeout")
    return any(token in text for token in tokens)
