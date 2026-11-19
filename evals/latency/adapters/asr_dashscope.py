"""Paraformer realtime ASR on Bailian's websocket."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from websockets.asyncio.client import connect

from latency.adapters.types import AdapterError, ASRUpdate
from latency.env import Settings


class DashScopeASR:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[ASRUpdate]:
        return self._transcribe(audio)

    async def _transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[ASRUpdate]:
        if not self._settings.dashscope_ws_url:
            raise AdapterError("dashscope-asr", "DASHSCOPE_WORKSPACE_ID is not set")
        task_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {self._settings.dashscope_key}"}
        start = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._settings.dashscope_asr_model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": 16000,
                    "punctuation_prediction_enabled": True,
                    "inverse_text_normalization_enabled": True,
                    # Vendor semantic punctuation waits for meaning. This benchmark
                    # measures its own endpointer, so ask Paraformer for fast VAD finals.
                    "semantic_punctuation_enabled": False,
                    "language_hints": ["zh"],
                },
                "input": {},
            },
        }
        finish = {
            "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {}},
        }
        queue: asyncio.Queue[ASRUpdate | BaseException | None] = asyncio.Queue()

        async def run() -> None:
            committed: list[str] = []
            started = asyncio.Event()
            try:
                async with connect(
                    self._settings.dashscope_ws_url,
                    additional_headers=headers,
                    max_size=8 * 1024 * 1024,
                    open_timeout=10,
                ) as ws:
                    await ws.send(json.dumps(start, ensure_ascii=False))

                    async def send_audio() -> None:
                        await started.wait()
                        async for chunk in audio:
                            if chunk:
                                await ws.send(chunk)
                        await ws.send(json.dumps(finish))

                    send_task = asyncio.create_task(send_audio())
                    try:
                        async for message in ws:
                            if not isinstance(message, str):
                                continue
                            event = _object(json.loads(message))
                            name = _event_name(event)
                            if name == "task-started":
                                started.set()
                                continue
                            if name == "task-failed":
                                raise AdapterError("dashscope-asr", _failure(event))
                            if name == "result-generated":
                                update = _hypothesis(event, committed)
                                if update is not None:
                                    await queue.put(update)
                            if name == "task-finished":
                                break
                    finally:
                        started.set()
                        await send_task
            except Exception as exc:
                await queue.put(exc if isinstance(exc, AdapterError) else AdapterError("dashscope-asr", str(exc)))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()


def _object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _event_name(event: dict[str, object]) -> str:
    header = event.get("header")
    if isinstance(header, dict):
        name = header.get("event")
        if isinstance(name, str):
            return name
    return ""


def _failure(event: dict[str, object]) -> str:
    header = event.get("header")
    if isinstance(header, dict):
        message = header.get("error_message")
        if isinstance(message, str) and message:
            return message[:600]
    return "task-failed"


def _hypothesis(event: dict[str, object], committed: list[str]) -> ASRUpdate | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    output = payload.get("output")
    if not isinstance(output, dict):
        return None
    sentence = output.get("sentence")
    if not isinstance(sentence, dict):
        return None
    if sentence.get("heartbeat") is True:
        return None
    text = sentence.get("text")
    if not isinstance(text, str):
        return None
    if sentence.get("sentence_end") is True:
        committed.append(text)
        return ASRUpdate(text="".join(committed), is_final=True)
    return ASRUpdate(text="".join(committed) + text, is_final=False)
