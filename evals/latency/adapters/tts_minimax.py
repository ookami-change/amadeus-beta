"""MiniMax streaming TTS on the domestic websocket.

Clauses are ``task_continue`` messages on one session opened in ``open()``.
Word timestamps, when the model returns them, arrive on ``task_continued``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from websockets.asyncio.client import ClientConnection, connect

from latency.adapters.types import AdapterError, AudioChunk, StreamItem, TTSMark
from latency.env import Settings


class MiniMaxTTS:
    def __init__(
        self,
        settings: Settings,
        *,
        emotion: str | None = None,
        speed: float = 1.0,
        volume: float = 1.0,
        pitch: int = 0,
        model: str | None = None,
    ) -> None:
        if not isinstance(pitch, int) or not -12 <= pitch <= 12:
            raise ValueError("minimax pitch must be an integer from -12 to 12")
        self._settings = settings
        self._emotion = emotion
        self._speed = speed
        self._volume = volume
        self._pitch = pitch
        self._model = model or settings.minimax_model
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[dict[str, object] | BaseException | None] = asyncio.Queue()

    async def open(self, *, instruction: str | None = None) -> None:
        del instruction  # MiniMax expresses style through emotion, speed, and volume.
        url = _ws_url(self._settings.minimax_base_url)
        headers = {"Authorization": f"Bearer {self._settings.minimax_key}"}
        self._ws = await connect(
            url,
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
            open_timeout=10,
        )
        self._reader = asyncio.create_task(self._read())
        hello = await self._next()
        if hello.get("event") != "connected_success":
            raise AdapterError("minimax-tts", f"unexpected handshake: {hello.get('event')}")
        voice: dict[str, object] = {
            "voice_id": self._settings.minimax_voice,
            "speed": self._speed,
            "vol": self._volume,
            "pitch": self._pitch,
        }
        if self._emotion:
            voice["emotion"] = self._emotion
        start = {
            "event": "task_start",
            "model": self._model,
            "voice_setting": voice,
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "pcm",
                "channel": 1,
            },
            "subtitle_enable": True,
            "subtitle_type": "word",
            "language_boost": "Chinese",
        }
        await self._ws.send(json.dumps(start, ensure_ascii=False))
        started = await self._next()
        if started.get("event") != "task_started":
            raise AdapterError("minimax-tts", _error_text(started) or "task_start was rejected")

    def speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        return self._speak(text)

    async def _speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        ws = self._ws
        if ws is None:
            raise AdapterError("minimax-tts", "open() was not called")

        async def send() -> None:
            async for clause in text:
                piece = clause.strip()
                if not piece:
                    continue
                await ws.send(json.dumps({"event": "task_continue", "text": piece}, ensure_ascii=False))
            await ws.send(json.dumps({"event": "task_finish"}))

        send_task = asyncio.create_task(send())
        try:
            while True:
                event = await self._queue.get()
                if event is None:
                    break
                if isinstance(event, BaseException):
                    raise event
                name = event.get("event")
                if name in {"task_failed", "error"}:
                    raise AdapterError("minimax-tts", _error_text(event))
                data = event.get("data")
                if isinstance(data, dict):
                    subtitle = data.get("subtitle")
                    if isinstance(subtitle, dict):
                        yield TTSMark(kind="subtitle", payload=subtitle)
                    audio_hex = data.get("audio")
                    if isinstance(audio_hex, str) and audio_hex:
                        try:
                            pcm = bytes.fromhex(audio_hex)
                        except ValueError as exc:
                            raise AdapterError("minimax-tts", "audio chunk was not hex") from exc
                        if pcm:
                            yield AudioChunk(pcm=pcm, sample_rate=32000)
                if name == "task_finished":
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
                if isinstance(message, str):
                    parsed = json.loads(message)
                    if isinstance(parsed, dict):
                        await self._queue.put({str(key): value for key, value in parsed.items()})
        except Exception as exc:
            await self._queue.put(AdapterError("minimax-tts", str(exc)))
        finally:
            await self._queue.put(None)

    async def _next(self) -> dict[str, object]:
        item = await self._queue.get()
        if item is None:
            raise AdapterError("minimax-tts", "socket closed")
        if isinstance(item, BaseException):
            raise item
        return item


def _ws_url(base: str) -> str:
    if base.startswith("https://"):
        root = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        root = "ws://" + base[len("http://") :]
    else:
        root = base
    return root.rstrip("/") + "/ws/v1/t2a_v2"


def _error_text(event: dict[str, object]) -> str:
    base = event.get("base_resp")
    if isinstance(base, dict):
        message = base.get("status_msg")
        if isinstance(message, str) and message:
            return message[:600]
    message = event.get("message")
    if isinstance(message, str):
        return message[:600]
    return str(event.get("event") or "minimax error")
