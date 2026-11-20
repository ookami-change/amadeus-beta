"""Stream the human recordings through Doubao ASR and keep the clocks.

One pass uses ``end_window_size`` 5000 so a normal pause does not split the
utterance. That pass keeps the partial hypotheses and the word times. The
other passes stop at the first definite utterance and only keep that clock.
Packets are 200 ms, sent at the audio rate, which is what the vendor asks for.
Each file is padded with 2500 ms of silence so a long threshold can still fire.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from latency.adapters.types import AdapterError
from latency.adapters.volc_frame import MSG_ERROR, build_asr_audio, build_asr_full_client, parse_frame
from latency.env import Settings, load_dotenv
from latency.pcm import read_wav, silence

HERE = Path(__file__).resolve().parent
PAD_MS = 2500
CHUNK_MS = 200
WINDOWS = (5000, 300, 400, 500, 800, 1000, 1200, 1600, 2000, 3000)


def _payload(*, end_window_size: int | None) -> dict[str, object]:
    request: dict[str, object] = {
        "model_name": "bigmodel",
        "enable_itn": True,
        "enable_punc": True,
        "enable_ddc": False,
        "show_utterances": True,
        "result_type": "full",
        "enable_nonstream": False,
    }
    if end_window_size is not None:
        request["end_window_size"] = end_window_size
    return {
        "user": {"uid": "amadeus-m0"},
        "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1},
        "request": request,
    }


def _parse_result(body: dict[str, object]) -> tuple[str, bool, float | None, list[dict[str, float | str]]]:
    result = body.get("result")
    if not isinstance(result, dict):
        return "", False, None, []
    text = result.get("text")
    heard = text if isinstance(text, str) else ""
    definite = False
    definite_end: float | None = None
    words: list[dict[str, float | str]] = []
    utterances = result.get("utterances")
    if isinstance(utterances, list):
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            if utterance.get("definite") is True:
                definite = True
                end = utterance.get("end_time")
                if isinstance(end, (int, float)):
                    definite_end = float(end)
            raw_words = utterance.get("words")
            if not isinstance(raw_words, list):
                continue
            for word in raw_words:
                if not isinstance(word, dict):
                    continue
                token = word.get("text")
                start = word.get("start_time")
                end = word.get("end_time")
                if isinstance(token, str) and isinstance(start, (int, float)) and isinstance(end, (int, float)):
                    words.append({"text": token, "start_ms": float(start), "end_ms": float(end)})
    return heard, definite, definite_end, words


async def _one(
    settings: Settings,
    pcm: bytes,
    *,
    end_window_size: int | None,
    keep_words: bool,
) -> dict[str, object]:
    request_id = str(uuid.uuid4())
    headers = settings.volc_headers(resource=settings.volc_asr_resource, request_id=request_id)
    sent = {"ms": 0.0}
    stop = asyncio.Event()
    partials: list[dict[str, object]] = []
    words: list[dict[str, float | str]] = []
    first: dict[str, object] | None = None
    last_text = ""

    async with connect(
        settings.volc_asr_url,
        additional_headers=headers,
        max_size=8 * 1024 * 1024,
        open_timeout=10,
    ) as ws:
        await ws.send(build_asr_full_client(_payload(end_window_size=end_window_size)))
        ack = await asyncio.wait_for(ws.recv(), timeout=8)
        if isinstance(ack, bytes):
            frame = parse_frame(ack)
            if frame.msg_type == MSG_ERROR:
                detail = frame.payload.decode("utf-8", "replace")[:300]
                raise AdapterError("volc-asr", detail or f"error {frame.error_code}")

        async def send() -> None:
            chunk_bytes = CHUNK_MS * 16 * 2
            nxt = time.monotonic()
            offset = 0
            while offset < len(pcm) and not stop.is_set():
                piece = pcm[offset : offset + chunk_bytes]
                offset += len(piece)
                await ws.send(build_asr_audio(piece, sequence=1, last=False))
                sent["ms"] = offset / 2 / 16.0
                nxt += CHUNK_MS / 1000.0
                delay = nxt - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            try:
                await ws.send(build_asr_audio(b"", sequence=1, last=True))
            except Exception:
                if first is None:
                    raise

        send_task = asyncio.create_task(send())
        try:
            async for message in ws:
                if not isinstance(message, bytes):
                    continue
                frame = parse_frame(message)
                if frame.msg_type == MSG_ERROR:
                    detail = frame.payload.decode("utf-8", "replace")[:300]
                    if first is not None:
                        break
                    raise AdapterError("volc-asr", detail or f"error {frame.error_code}")
                if not frame.payload:
                    if frame.sequence is not None and frame.sequence < 0:
                        break
                    continue
                try:
                    body = frame.json_payload()
                except json.JSONDecodeError:
                    continue
                heard, definite, definite_end, seen_words = _parse_result(body)
                if keep_words and seen_words:
                    words = seen_words
                if heard != last_text:
                    last_text = heard
                    partials.append({"audio_ms": round(sent["ms"], 1), "text": heard, "definite": definite})
                if definite and first is None:
                    first = {
                        "client_audio_ms": round(sent["ms"], 1),
                        "server_end_ms": definite_end,
                    }
                    if not keep_words:
                        stop.set()
                if frame.sequence is not None and frame.sequence < 0:
                    break
        finally:
            stop.set()
            try:
                await send_task
            except Exception:
                if first is None:
                    raise
    return {"partials": partials, "words": words, "definite": first}


async def _retry(
    settings: Settings,
    pcm: bytes,
    *,
    end_window_size: int | None,
    keep_words: bool,
) -> dict[str, object]:
    last = "unknown"
    for attempt in range(3):
        try:
            return await _one(settings, pcm, end_window_size=end_window_size, keep_words=keep_words)
        except (AdapterError, OSError, TimeoutError) as exc:
            last = str(exc)[:240]
            await asyncio.sleep(1.5 * (attempt + 1))
    return {"partials": [], "words": [], "definite": None, "error": last}


async def _run(audio_dir: Path, out: Path, concurrency: int) -> None:
    load_dotenv()
    settings = Settings.from_environ()
    if not settings.volc_speech_key and not (settings.volc_app_id and settings.volc_access_token):
        raise SystemExit("missing VOLC_SPEECH_API_KEY")
    files = sorted(audio_dir.glob("*.wav"))
    if len(files) < 50:
        raise SystemExit(f"expected 50 wavs in {audio_dir}, found {len(files)}")
    padded = {path.stem: read_wav(path) + silence(PAD_MS) for path in files}
    limit = asyncio.Semaphore(concurrency)
    items: dict[str, dict[str, Any]] = {stem: {"passes": {}} for stem in padded}

    async def bound(stem: str, window: int | None) -> None:
        async with limit:
            keep = window == WINDOWS[0]
            result = await _retry(settings, padded[stem], end_window_size=window, keep_words=keep)
            key = "semantic" if window is None else str(window)
            items[stem]["passes"][key] = {
                "definite": result.get("definite"),
                "error": result.get("error"),
            }
            if keep:
                items[stem]["words"] = result.get("words", [])
                items[stem]["partials"] = result.get("partials", [])
            definite = result.get("definite")
            mark = definite.get("client_audio_ms") if isinstance(definite, dict) else result.get("error", "none")
            print(f"{stem} window={key} definite={mark}", flush=True)

    tasks = []
    for stem in padded:
        for window in WINDOWS:
            tasks.append(bound(stem, window))
        tasks.append(bound(stem, None))
    await asyncio.gather(*tasks)
    payload = {
        "kind": "human-turn-volc",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": settings.volc_asr_url,
        "resource": settings.volc_asr_resource,
        "pad_ms": PAD_MS,
        "chunk_ms": CHUNK_MS,
        "windows_ms": list(WINDOWS),
        "semantic": "不带 end_window_size，由服务端自己判停。vad_segment_duration 不移动 definite，所以没有扫它。",
        "clock": (
            "client_audio_ms 是收到第一条 definite 时已送出的音频时长。"
            "200ms 一包，按实时速率送。词时间来自 5000ms 那遍。"
        ),
        "items": items,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Doubao partials and server endpoints")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "results" / "turns-human-volc.json")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(_run(args.audio, args.out, args.concurrency))


if __name__ == "__main__":
    main()
