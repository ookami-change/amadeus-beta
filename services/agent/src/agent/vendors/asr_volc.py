"""Doubao streaming ASR (bigmodel_async), one socket per user turn."""

from __future__ import annotations

import asyncio
import uuid

from websockets.asyncio.client import ClientConnection, connect

from agent.settings import Settings
from agent.streams import Channel
from agent.vendors import volc_frame as vf
from agent.vendors.types import ASRUpdate, VendorError, redact

PACKET_MS = 200
"""R3b and R6b sent 200 ms packets; partial timing in the reports assumes it."""
PACKET_BYTES = PACKET_MS * 32


class VolcASR:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def start(self) -> VolcStream:
        stream = VolcStream(self._settings)
        stream.begin()
        return stream


class VolcStream:
    """Audio may be sent before the socket is up; it is buffered and flushed after the ack."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.updates: Channel[ASRUpdate] = Channel()
        self._outbox: asyncio.Queue[tuple[bytes, bool] | None] = asyncio.Queue()
        self._packet = bytearray()
        self._finished = False
        self._task: asyncio.Task[None] | None = None

    def begin(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def send(self, pcm: bytes) -> None:
        if self._finished:
            return
        self._packet.extend(pcm)
        while len(self._packet) >= PACKET_BYTES:
            self._outbox.put_nowait((bytes(self._packet[:PACKET_BYTES]), False))
            del self._packet[:PACKET_BYTES]

    async def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._outbox.put_nowait((bytes(self._packet), True))
        self._packet.clear()

    async def close(self) -> None:
        self._finished = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        self.updates.close()

    async def _run(self) -> None:
        settings = self._settings
        headers = {
            "X-Api-Resource-Id": settings.volc_asr_resource,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Key": settings.volc_speech_key,
        }
        payload: dict[str, object] = {
            "user": {"uid": "amadeus"},
            "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "show_utterances": True,
                "result_type": "full",
                # The agent decides the endpoint itself; no second pass.
                "enable_nonstream": False,
                "end_window_size": 800,
            },
        }
        error: BaseException | None = None
        try:
            async with connect(
                settings.volc_asr_url,
                additional_headers=headers,
                max_size=8 * 1024 * 1024,
                open_timeout=10,
            ) as ws:
                await ws.send(vf.full_client(payload))
                # The server drops the socket if audio arrives before this ack is read.
                self._handle(vf.parse(await asyncio.wait_for(_recv_bytes(ws), timeout=8)))
                sender = asyncio.create_task(self._send_all(ws))
                try:
                    async for message in ws:
                        if not isinstance(message, bytes):
                            continue
                        frame = vf.parse(message)
                        self._handle(frame)
                        if frame.sequence is not None and frame.sequence < 0:
                            break
                finally:
                    if not sender.done():
                        sender.cancel()
                    try:
                        await sender
                    except BaseException:
                        pass
        except asyncio.CancelledError:
            raise
        except VendorError as exc:
            error = exc
        except Exception as exc:
            error = VendorError("volc-asr", redact(str(exc)) or type(exc).__name__)
        finally:
            self.updates.close(error)

    async def _send_all(self, ws: ClientConnection) -> None:
        while (item := await self._outbox.get()) is not None:
            pcm, last = item
            await ws.send(vf.audio(pcm, last=last))
            if last:
                return

    def _handle(self, frame: vf.Frame) -> None:
        if frame.msg_type == vf.MSG_ERROR:
            detail = redact(frame.payload.decode("utf-8", "replace")[:600])
            raise VendorError("volc-asr", detail or f"error {frame.error_code}")
        found = vf.hypothesis(frame)
        if found is not None:
            self.updates.put(ASRUpdate(text=found[0], is_final=found[1]))


async def _recv_bytes(ws: ClientConnection) -> bytes:
    while True:
        message = await ws.recv()
        if isinstance(message, bytes):
            return message
