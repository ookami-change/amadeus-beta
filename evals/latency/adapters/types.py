"""Streaming events shared by every ASR, LLM, and TTS adapter.

Adapters yield these as soon as the vendor produces them. The pipeline stamps
the clock. Nothing here waits for an upstream utterance to finish.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ASRUpdate:
    """A partial or final transcript. ``text`` is the current hypothesis, not a diff."""

    text: str
    is_final: bool


@dataclass(frozen=True)
class LLMDelta:
    text: str


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int | None
    cached_tokens: int | None


@dataclass(frozen=True)
class AudioChunk:
    """PCM s16le mono. ``sample_rate`` is the vendor's rate."""

    pcm: bytes
    sample_rate: int


@dataclass(frozen=True)
class TTSMark:
    """Non-audio sideband such as a word timestamp or a sentence event."""

    kind: str
    payload: dict[str, object]


StreamItem = AudioChunk | TTSMark
LLMItem = LLMDelta | LLMUsage


class ASRAdapter(Protocol):
    def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[ASRUpdate]: ...


class LLMAdapter(Protocol):
    def stream(
        self,
        *,
        system: str,
        history: list[dict[str, str]],
        user: str,
    ) -> AsyncIterator[LLMItem]: ...


class TTSAdapter(Protocol):
    async def open(self, *, instruction: str | None = None) -> None: ...

    def speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]: ...

    async def close(self) -> None: ...


@dataclass
class AdapterError(Exception):
    vendor: str
    message: str

    def __str__(self) -> str:
        return f"{self.vendor}: {self.message}"


def redact(text: str) -> str:
    """Strip credential values if a vendor echoes them back in an error."""

    cleaned = text
    for name in (
        "VOLC_SPEECH_API_KEY",
        "VOLC_ACCESS_TOKEN",
        "ARK_API_KEY",
        "DASHSCOPE_API_KEY",
        "MINIMAX_API_KEY",
    ):
        value = os.environ.get(name, "")
        if len(value) >= 6:
            cleaned = cleaned.replace(value, "***")
    return cleaned


def retryable_error(error: str | None) -> bool:
    """True when another attempt may succeed: quota, throttle, or a blank transcript."""

    if not error:
        return False
    text = error.lower()
    return any(token in text for token in ("429", "limit_requests", "throttl", "rate limit", "empty transcript"))
