"""Doubao streaming ASR (bigmodel_async) over the v3 binary websocket."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from websockets.asyncio.client import connect

from latency.adapters.types import AdapterError, ASRUpdate
from latency.adapters.volc_frame import (
    MSG_ERROR,
    build_asr_audio,
    build_asr_full_client,
    parse_frame,
)
from latency.env import Settings


class VolcASR:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[ASRUpdate]:
        return self._transcribe(audio)

    async def _transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[ASRUpdate]:
        request_id = str(uuid.uuid4())
        headers = self._settings.volc_headers(
            resource=self._settings.volc_asr_resource,
            request_id=request_id,
        )
        payload: dict[str, object] = {
            "user": {"uid": "amadeus-m0"},
            "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "show_utterances": True,
                "result_type": "full",
                # Second-pass recognition adds a wait after the vendor's own endpoint.
                # The benchmark applies its own endpoint, so leave this off.
                "enable_nonstream": False,
                "end_window_size": 800,
            },
        }
        queue: asyncio.Queue[ASRUpdate | BaseException | None] = asyncio.Queue()

        async def run() -> None:
            try:
                async with connect(
                    self._settings.volc_asr_url,
                    additional_headers=headers,
                    max_size=8 * 1024 * 1024,
                    open_timeout=10,
                ) as ws:
                    await ws.send(build_asr_full_client(payload))

                    async def send() -> None:
                        sequence = 1
                        async for chunk in audio:
                            if not chunk:
                                continue
                            await ws.send(build_asr_audio(chunk, sequence=sequence, last=False))
                            sequence += 1
                        await ws.send(build_asr_audio(b"", sequence=sequence, last=True))

                    send_task = asyncio.create_task(send())
                    try:
                        async for message in ws:
                            if not isinstance(message, bytes):
                                continue
                            frame = parse_frame(message)
                            if frame.msg_type == MSG_ERROR:
                                detail = frame.payload.decode("utf-8", "replace")[:600]
                                raise AdapterError("volc-asr", detail or f"error {frame.error_code}")
                            update = _hypothesis(frame)
                            if update is not None:
                                await queue.put(update)
                            if frame.sequence is not None and frame.sequence < 0:
                                break
                    finally:
                        await send_task
            except Exception as exc:
                await queue.put(exc if isinstance(exc, AdapterError) else AdapterError("volc-asr", str(exc)))
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


def _hypothesis(frame: object) -> ASRUpdate | None:
    from latency.adapters.volc_frame import VolcFrame

    if not isinstance(frame, VolcFrame) or not frame.payload:
        return None
    try:
        body = json.loads(frame.payload.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    text = result.get("text")
    if not isinstance(text, str):
        text = ""
    definite = False
    utterances = result.get("utterances")
    if isinstance(utterances, list):
        for utterance in utterances:
            if isinstance(utterance, dict) and utterance.get("definite") is True:
                definite = True
    if not text and not definite:
        return None
    return ASRUpdate(text=text, is_final=definite)
