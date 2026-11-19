"""Construct the adapter named by a benchmark combo."""

from __future__ import annotations

from latency.adapters.asr_dashscope import DashScopeASR
from latency.adapters.asr_volc import VolcASR
from latency.adapters.llm_openai import OpenAIChatLLM
from latency.adapters.mock import MockASR, MockLLM, MockTTS
from latency.adapters.tts_dashscope import DashScopeTTS
from latency.adapters.tts_minimax import MiniMaxTTS
from latency.adapters.tts_volc import VolcTTS
from latency.adapters.types import ASRAdapter, LLMAdapter, TTSAdapter
from latency.env import Settings

ASR_NAMES = ("volc", "dashscope")
LLM_NAMES = ("doubao", "qwen")
TTS_NAMES = ("volc", "minimax", "dashscope")


def build_asr(name: str, settings: Settings) -> ASRAdapter:
    if name == "volc":
        return VolcASR(settings)
    if name == "dashscope":
        return DashScopeASR(settings)
    if name == "mock":
        return MockASR()
    raise KeyError(name)


def build_llm(name: str, settings: Settings) -> LLMAdapter:
    if name == "doubao":
        return OpenAIChatLLM(settings, vendor="ark")
    if name == "qwen":
        return OpenAIChatLLM(settings, vendor="qwen")
    if name == "mock":
        return MockLLM()
    raise KeyError(name)


def build_tts(
    name: str,
    settings: Settings,
    *,
    emotion: str | None = None,
    speed: float = 1.0,
    volume: float = 1.0,
    model: str | None = None,
) -> TTSAdapter:
    if name == "volc":
        return VolcTTS(settings, emotion=emotion, speed=speed, volume=volume)
    if name == "minimax":
        return MiniMaxTTS(settings, emotion=emotion, speed=speed, volume=volume, model=model)
    if name == "dashscope":
        return DashScopeTTS(settings, emotion=emotion, speed=speed, volume=volume)
    if name == "mock":
        return MockTTS()
    raise KeyError(name)
