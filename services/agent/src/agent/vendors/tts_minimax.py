"""MiniMax streaming TTS (domestic websocket), one task per socket.

``open()`` connects and sends ``task_start``; every clause is a
``task_continue``; ``finish()`` sends ``task_finish``. A reader task turns the
socket into ``events``, which closes on ``task_finished`` or with the error.
"""

from __future__ import annotations

import asyncio
import json

from websockets.asyncio.client import ClientConnection, connect

from agent.settings import Settings
from agent.streams import Channel
from agent.vendors.types import AudioChunk, TTSEvent, VendorError, WordMark, redact

SAMPLE_RATE = 32000


class MiniMaxVoice:
    def __init__(
        self,
        settings: Settings,
        *,
        emotion: str | None = None,
        speed: float = 1.0,
        volume: float = 1.0,
    ):
        self._settings = settings
        self._emotion = emotion
        self._speed = speed
        self._volume = volume
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._control: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.events: Channel[TTSEvent] = Channel()

    async def open(self) -> None:
        url = _ws_url(self._settings.minimax_base_url)
        headers = {"Authorization": f"Bearer {self._settings.minimax_key}"}
        try:
            self._ws = await connect(
                url, additional_headers=headers, max_size=8 * 1024 * 1024, open_timeout=10
            )
        except Exception as exc:
            raise VendorError("minimax-tts", redact(str(exc)) or type(exc).__name__) from exc
        self._reader = asyncio.create_task(self._read())
        hello = await self._next_control()
        if hello.get("event") != "connected_success":
            raise VendorError("minimax-tts", f"unexpected handshake: {hello.get('event')}")
        voice: dict[str, object] = {
            "voice_id": self._settings.minimax_voice,
            "speed": self._speed,
            "vol": self._volume,
            "pitch": 0,  # R8c: pitch stays 0
        }
        if self._emotion:
            voice["emotion"] = self._emotion
        start = {
            "event": "task_start",
            "model": self._settings.minimax_model,
            "voice_setting": voice,
            "audio_setting": {
                "sample_rate": SAMPLE_RATE,
                "bitrate": 128000,
                "format": "pcm",
                "channel": 1,
            },
            "subtitle_enable": True,
            "subtitle_type": "word",
            "language_boost": "Chinese",
        }
        await self._send(start)
        started = await self._next_control()
        if started.get("event") != "task_started":
            raise VendorError("minimax-tts", _error_text(started) or "task_start was rejected")

    async def say(self, text: str) -> None:
        if text.strip():
            await self._send({"event": "task_continue", "text": text.strip()})

    async def finish(self) -> None:
        await self._send({"event": "task_finish"})

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        reader, self._reader = self._reader, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if reader is not None and not reader.done():
            reader.cancel()
            try:
                await reader
            except BaseException:
                pass
        self.events.close()

    async def _send(self, event: dict[str, object]) -> None:
        if self._ws is None:
            raise VendorError("minimax-tts", "socket is not open")
        try:
            await self._ws.send(json.dumps(event, ensure_ascii=False))
        except Exception as exc:
            raise VendorError("minimax-tts", redact(str(exc)) or "socket closed") from exc

    async def _next_control(self) -> dict[str, object]:
        reader = self._reader
        getter = asyncio.create_task(self._control.get())
        assert reader is not None
        done, _ = await asyncio.wait(
            {getter, reader}, timeout=10, return_when=asyncio.FIRST_COMPLETED
        )
        if getter in done:
            return getter.result()
        getter.cancel()
        raise VendorError(
            "minimax-tts", "socket closed during handshake" if done else "handshake timed out"
        )

    async def _read(self) -> None:
        ws = self._ws
        assert ws is not None
        started = False
        error: BaseException | None = None
        try:
            async for message in ws:
                if not isinstance(message, str):
                    continue
                event = json.loads(message)
                if not isinstance(event, dict):
                    continue
                name = event.get("event")
                if name in {"task_failed", "error"} or _status(event) not in (None, 0):
                    error = VendorError("minimax-tts", _error_text(event))
                    return
                if not started:
                    self._control.put_nowait(event)
                    started = name == "task_started"
                    continue
                data = event.get("data")
                if isinstance(data, dict):
                    for mark in _marks(data.get("subtitle")):
                        self.events.put(mark)
                    audio_hex = data.get("audio")
                    if isinstance(audio_hex, str) and audio_hex:
                        self.events.put(
                            AudioChunk(pcm=bytes.fromhex(audio_hex), sample_rate=SAMPLE_RATE)
                        )
                if name == "task_finished":
                    return
            error = VendorError("minimax-tts", "socket closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = VendorError("minimax-tts", redact(str(exc)) or type(exc).__name__)
        finally:
            self.events.close(error)


def _status(event: dict[str, object]) -> int | None:
    base = event.get("base_resp")
    code = base.get("status_code") if isinstance(base, dict) else None
    return code if isinstance(code, int) else None


def _marks(subtitle: object) -> list[WordMark]:
    """Word timings. The vendor docs we have do not name the fields; both spellings are tried."""
    if not isinstance(subtitle, dict):
        return []
    words = subtitle.get("timestamped_words") or subtitle.get("words")
    out: list[WordMark] = []
    if isinstance(words, list):
        for word in words:
            if not isinstance(word, dict):
                continue
            text = word.get("word", word.get("text"))
            begin = word.get("time_begin", word.get("start"))
            end = word.get("time_end", word.get("end"))
            if (
                isinstance(text, str)
                and isinstance(begin, (int, float))
                and isinstance(end, (int, float))
            ):
                out.append(WordMark(float(begin), float(end), text))
    return out


def _ws_url(base: str) -> str:
    root = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return root.rstrip("/") + "/ws/v1/t2a_v2"


def _error_text(event: dict[str, object]) -> str:
    base = event.get("base_resp")
    if isinstance(base, dict):
        message = base.get("status_msg")
        if isinstance(message, str) and message:
            return redact(message[:600])
    message = event.get("message")
    if isinstance(message, str):
        return redact(message[:600])
    return str(event.get("event") or "minimax error")
