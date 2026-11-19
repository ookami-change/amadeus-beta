"""CosyVoice streaming TTS. Text is sent with continue-task as clauses arrive."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from websockets.asyncio.client import ClientConnection, connect

from latency.adapters.types import AdapterError, AudioChunk, StreamItem, TTSMark
from latency.env import Settings


class DashScopeTTS:
    def __init__(
        self,
        settings: Settings,
        *,
        emotion: str | None = None,
        speed: float = 1.0,
        volume: float = 1.0,
        model: str | None = None,
        voice: str | None = None,
        allow_instruction: bool | None = None,
        sample_rate: int = 22050,
    ) -> None:
        self._settings = settings
        self._speed = speed
        self._volume = volume
        self._model = model or settings.cosyvoice_model
        self._voice = voice or settings.cosyvoice_voice
        self._allow_instruction = settings.cosyvoice_instruction if allow_instruction is None else allow_instruction
        self._sample_rate = sample_rate
        # CosyVoice's emotion control is the instruction string, not this field.
        # 龙安温 does not accept instructions unless the operator opts in.
        # A v3.5 designed voice does, so the caller can set allow_instruction.
        self._emotion = emotion
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[bytes | str | BaseException | None] = asyncio.Queue()
        self._task_id = ""

    async def open(self, *, instruction: str | None = None) -> None:
        if not self._settings.dashscope_ws_url:
            raise AdapterError("dashscope-tts", "DASHSCOPE_WORKSPACE_ID is not set")
        self._task_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {self._settings.dashscope_key}"}
        parameters: dict[str, object] = {
            "text_type": "PlainText",
            "voice": self._voice,
            "format": "pcm",
            "sample_rate": self._sample_rate,
            "word_timestamp_enabled": True,
            "rate": self._speed,
            "pitch": 1.0,
            "volume": int(round(max(0.0, min(1.0, self._volume)) * 100)),
        }
        if instruction and self._allow_instruction:
            parameters["instruction"] = instruction
        start = {
            "header": {"action": "run-task", "task_id": self._task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self._model,
                "parameters": parameters,
                "input": {},
            },
        }
        self._ws = await connect(
            self._settings.dashscope_ws_url,
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
            open_timeout=10,
        )
        self._reader = asyncio.create_task(self._read())
        await self._ws.send(json.dumps(start, ensure_ascii=False))
        await self._expect_started()

    def speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        return self._speak(text)

    async def _speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        ws = self._ws
        if ws is None:
            raise AdapterError("dashscope-tts", "open() was not called")

        async def send() -> None:
            async for clause in text:
                piece = clause.strip()
                if not piece:
                    continue
                message = {
                    "header": {
                        "action": "continue-task",
                        "task_id": self._task_id,
                        "streaming": "duplex",
                    },
                    "payload": {"input": {"text": piece}},
                }
                await ws.send(json.dumps(message, ensure_ascii=False))
            finish = {
                "header": {"action": "finish-task", "task_id": self._task_id, "streaming": "duplex"},
                "payload": {"input": {}},
            }
            await ws.send(json.dumps(finish))

        send_task = asyncio.create_task(send())
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, bytes):
                    if item:
                        yield AudioChunk(pcm=item, sample_rate=self._sample_rate)
                    continue
                event = _object(json.loads(item))
                name = _event_name(event)
                if name == "task-failed":
                    raise AdapterError("dashscope-tts", _failure(event))
                if name == "result-generated":
                    yield TTSMark(kind="result-generated", payload=event)
                if name == "task-finished":
                    break
        finally:
            await send_task

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            await ws.close()
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None

    async def _read(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for message in ws:
                await self._queue.put(message if isinstance(message, (bytes, str)) else None)
        except Exception as exc:
            await self._queue.put(AdapterError("dashscope-tts", str(exc)))
        finally:
            await self._queue.put(None)

    async def _expect_started(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                raise AdapterError("dashscope-tts", "socket closed before task-started")
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, bytes):
                continue
            event = _object(json.loads(item))
            name = _event_name(event)
            if name == "task-started":
                return
            if name == "task-failed":
                raise AdapterError("dashscope-tts", _failure(event))


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
