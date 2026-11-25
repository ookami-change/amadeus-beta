"""Qwen through Bailian's OpenAI-compatible streaming chat, on one shared HTTP client.

R6b found every call opening a fresh ``httpx.AsyncClient``; the client here is
created once per process so TLS and TCP are reused across turns.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import httpx

from agent.settings import Settings
from agent.vendors.types import VendorError, redact

MAX_TOKENS = 80


def http_client() -> httpx.AsyncClient:
    limits = httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120.0)
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=60.0), limits=limits)


class QwenLLM:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    async def warm(self) -> None:
        """Open the connection pool before the first turn; any HTTP answer will do."""
        try:
            await self._client.get(
                f"{self._settings.dashscope_base_url}/models", headers=self._headers()
            )
        except httpx.HTTPError:
            pass

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.dashscope_key}",
            "Content-Type": "application/json",
        }

    async def stream(
        self, *, system: str, history: list[dict[str, str]], user: str
    ) -> AsyncGenerator[str, None]:
        messages = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": user},
        ]
        body: dict[str, object] = {
            "model": self._settings.qwen_model,
            "messages": messages,
            "stream": True,
            "max_tokens": MAX_TOKENS,
            "temperature": 0.7,
            # The fast tier thinks by default, which blows the latency budget.
            "enable_thinking": False,
        }
        url = f"{self._settings.dashscope_base_url}/chat/completions"
        try:
            async with self._client.stream(
                "POST", url, headers=self._headers(), json=body
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", "replace")[:600]
                    raise VendorError("qwen", redact(f"HTTP {response.status_code} {detail}"))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    text = _delta(json.loads(data))
                    if text:
                        yield text
        except httpx.HTTPError as exc:
            raise VendorError("qwen", redact(str(exc)) or type(exc).__name__) from exc


def _delta(parsed: object) -> str:
    if not isinstance(parsed, dict):
        return ""
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) else ""
