"""Doubao bidirectional TTS. The socket opens before any text arrives."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from websockets.asyncio.client import ClientConnection, connect

from latency.adapters.types import AdapterError, AudioChunk, StreamItem, TTSMark
from latency.adapters.volc_frame import (
    MSG_ERROR,
    VolcFrame,
    build_tts_event,
    parse_frame,
)
from latency.env import Settings


class VolcTTS:
    def __init__(
        self,
        settings: Settings,
        *,
        emotion: str | None = None,
        speed: float = 1.0,
        volume: float = 1.0,
    ) -> None:
        self._settings = settings
        self._emotion = emotion
        self._speed = speed
        self._volume = volume
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[VolcFrame | BaseException | None] = asyncio.Queue()
        self._session_id = ""

    async def open(self, *, instruction: str | None = None) -> None:
        request_id = str(uuid.uuid4())
        headers = self._settings.volc_headers(
            resource=self._settings.volc_tts_resource,
            request_id=request_id,
        )
        self._ws = await connect(
            "wss://openspeech.bytedance.com/api/v3/tts/bidirection",
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
            open_timeout=10,
        )
        self._reader = asyncio.create_task(self._read())
        await self._ws.send(build_tts_event(1, {}))
        await self._expect({50}, fail={51})
        self._session_id = uuid.uuid4().hex
        audio_params: dict[str, object] = {
            "format": "pcm",
            "sample_rate": 24000,
            "enable_subtitle": True,
            "speech_rate": _speech_rate(self._speed),
            "loudness_rate": _loudness(self._volume),
        }
        req_params: dict[str, object] = {
            "speaker": self._settings.volc_voice,
            "audio_params": audio_params,
            "additions": json.dumps({"disable_markdown_filter": True}, ensure_ascii=False),
        }
        if instruction:
            req_params["context_texts"] = [instruction]
        body: dict[str, object] = {
            "user": {"uid": "amadeus-m0"},
            "event": 100,
            "namespace": "BidirectionalTTS",
            "req_params": req_params,
        }
        await self._ws.send(build_tts_event(100, body, session_id=self._session_id))
        await self._expect({150}, fail={51, 153})

    def speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        return self._speak(text)

    async def _speak(self, text: AsyncIterator[str]) -> AsyncIterator[StreamItem]:
        ws = self._ws
        if ws is None:
            raise AdapterError("volc-tts", "open() was not called")

        async def send() -> None:
            async for clause in text:
                piece = clause.strip()
                if not piece:
                    continue
                body: dict[str, object] = {
                    "event": 200,
                    "namespace": "BidirectionalTTS",
                    "req_params": {"text": piece},
                }
                await ws.send(build_tts_event(200, body, session_id=self._session_id))
            await ws.send(build_tts_event(102, {}, session_id=self._session_id))

        send_task = asyncio.create_task(send())
        try:
            while True:
                frame = await self._queue.get()
                if frame is None:
                    break
                if isinstance(frame, BaseException):
                    raise frame
                if frame.msg_type == MSG_ERROR or frame.event in {51, 153}:
                    detail = frame.payload.decode("utf-8", "replace")[:600]
                    raise AdapterError("volc-tts", detail or f"error {frame.error_code}")
                if frame.event == 352 and frame.payload:
                    yield AudioChunk(pcm=frame.payload, sample_rate=24000)
                elif frame.payload and frame.event not in {152, 352}:
                    yield TTSMark(kind=str(frame.event), payload=_json_or_raw(frame.payload))
                if frame.event == 152:
                    break
        finally:
            await send_task

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.send(build_tts_event(2, {}))
            except Exception:
                pass
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
                if isinstance(message, bytes):
                    await self._queue.put(parse_frame(message))
        except Exception as exc:
            await self._queue.put(AdapterError("volc-tts", str(exc)))
        finally:
            await self._queue.put(None)

    async def _expect(self, ok: set[int], *, fail: set[int]) -> VolcFrame:
        while True:
            frame = await self._queue.get()
            if frame is None:
                raise AdapterError("volc-tts", "socket closed while opening the session")
            if isinstance(frame, BaseException):
                raise frame
            if frame.msg_type == MSG_ERROR or frame.event in fail:
                detail = frame.payload.decode("utf-8", "replace")[:600]
                raise AdapterError("volc-tts", detail or f"error {frame.error_code}")
            if frame.event in ok:
                return frame


def _speech_rate(speed: float) -> int:
    """Map a multiplier onto Doubao's speech_rate, where -50 is 0.5x and 100 is 2x."""

    rate = int(round((speed - 1.0) * 100))
    return max(-50, min(100, rate))


def _loudness(volume: float) -> int:
    rate = int(round((volume - 1.0) * 50))
    return max(-50, min(100, rate))


def _json_or_raw(payload: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"bytes": len(payload)}
    if isinstance(parsed, dict):
        return {str(key): value for key, value in parsed.items()}
    return {"value": parsed}
