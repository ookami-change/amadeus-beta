"""OpenAI-compatible streaming chat for Ark (Doubao) and Bailian (Qwen)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from latency.adapters.types import AdapterError, LLMDelta, LLMItem, LLMUsage
from latency.env import Settings


class OpenAIChatLLM:
    """One streaming chat completion. The system prefix is sent unchanged so vendor caches can hit."""

    def __init__(self, settings: Settings, *, vendor: str) -> None:
        self._settings = settings
        if vendor not in {"ark", "qwen"}:
            raise ValueError(vendor)
        self.vendor = vendor

    def stream(
        self,
        *,
        system: str,
        history: list[dict[str, str]],
        user: str,
    ) -> AsyncIterator[LLMItem]:
        return self._stream(system=system, history=history, user=user)

    async def _stream(
        self,
        *,
        system: str,
        history: list[dict[str, str]],
        user: str,
    ) -> AsyncIterator[LLMItem]:
        if self.vendor == "ark":
            url = f"{self._settings.ark_base_url}/chat/completions"
            key = self._settings.ark_key
            model = self._settings.ark_model
        else:
            url = f"{self._settings.dashscope_base_url}/chat/completions"
            key = self._settings.dashscope_key
            model = self._settings.qwen_model
        messages: list[dict[str, str]] = [{"role": "system", "content": system}, *history]
        messages.append({"role": "user", "content": user})
        body: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": 80,
            "temperature": 0.7,
        }
        # Both fast tiers think by default. Thinking blows the latency budget.
        if self.vendor == "ark":
            body["thinking"] = {"type": "disabled"}
            body["prompt_cache_key"] = "amadeus-m0-persona-v1"
        else:
            body["enable_thinking"] = False
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(15.0, read=60.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", "replace")[:600]
                    raise AdapterError(self.vendor, f"HTTP {response.status_code} {detail}")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    parsed = _object(json.loads(data))
                    usage = parsed.get("usage")
                    if isinstance(usage, dict):
                        yielded = _usage(usage)
                        if yielded is not None:
                            yield yielded
                    choices = parsed.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    first = choices[0]
                    if not isinstance(first, dict):
                        continue
                    delta = first.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield LLMDelta(content)


def _object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _usage(usage: dict[str, object]) -> LLMUsage | None:
    prompt = usage.get("prompt_tokens")
    prompt_tokens = prompt if isinstance(prompt, int) else None
    cached: int | None = None
    details = usage.get("prompt_tokens_details")
    cached_value: object = details.get("cached_tokens") if isinstance(details, dict) else None
    if not isinstance(cached_value, int):
        cached_value = usage.get("cached_tokens")
    if isinstance(cached_value, int):
        cached = cached_value
    if prompt_tokens is None and cached is None:
        return None
    return LLMUsage(prompt_tokens=prompt_tokens, cached_tokens=cached)
