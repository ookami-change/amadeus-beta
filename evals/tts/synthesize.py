"""Synthesize the 14 lines with each TTS candidate and record time-to-first-audio.

Without API keys this command exits before writing any audio. It does not
substitute another engine and call it one of the candidates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from latency.adapters.factory import build_tts
from latency.adapters.types import AudioChunk, TTSMark, redact
from latency.env import Settings, load_dotenv
from tts.lines import LINES, Line

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
VENDORS = ("volc", "minimax", "dashscope")


async def _one(vendor: str, line: Line, settings: Settings, out_dir: Path) -> dict[str, object]:
    model = "speech-2.8-hd" if vendor == "minimax" else None
    tts = build_tts(
        vendor,
        settings,
        emotion=line.emotion,
        speed=line.speed,
        volume=line.volume,
        model=model,
    )
    marks: list[dict[str, object]] = []
    pcm = bytearray()
    sample_rate = 24000
    first_ms: float | None = None
    started = time.perf_counter()

    async def text_once() -> AsyncIterator[str]:
        yield line.text

    await tts.open(instruction=line.instruction)
    try:
        async for item in tts.speak(text_once()):
            if isinstance(item, AudioChunk) and item.pcm:
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000.0
                sample_rate = item.sample_rate
                pcm.extend(item.pcm)
            elif isinstance(item, TTSMark):
                marks.append({"kind": item.kind, "payload": item.payload})
    finally:
        await tts.close()
    if first_ms is None or not pcm:
        raise RuntimeError(f"{vendor} line {line.number} produced no audio")
    folder = out_dir / vendor
    folder.mkdir(parents=True, exist_ok=True)
    wav_path = folder / f"{line.number:02d}.wav"
    _write_wav(wav_path, bytes(pcm), sample_rate)
    (folder / f"{line.number:02d}.json").write_text(
        json.dumps(
            {
                "line": line.number,
                "text": line.text,
                "hint": line.hint,
                "instruction": line.instruction,
                "emotion": line.emotion,
                "speed": line.speed,
                "volume": line.volume,
                "first_audio_ms": round(first_ms, 1),
                "sample_rate": sample_rate,
                "marks": marks,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "vendor": vendor,
        "line": line.number,
        "first_audio_ms": round(first_ms, 1),
        "mark_count": len(marks),
        "bytes": len(pcm),
    }


def _write_wav(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)


async def _run(vendors: list[str], settings: Settings, out_dir: Path) -> list[dict[str, object]]:
    missing: list[str] = []
    for vendor in vendors:
        for name in settings.missing_for("-", "-", vendor):
            if name not in missing:
                missing.append(name)
    if missing:
        raise SystemExit(
            "缺少语音合成密钥，没有写出试听音频：" + ", ".join(missing) + "。补上 .env 后再跑 python -m tts.synthesize"
        )
    results: list[dict[str, object]] = []
    for vendor in vendors:
        for line in LINES:
            try:
                result = await _one(vendor, line, settings, out_dir)
            except Exception as exc:
                result = {"vendor": vendor, "line": line.number, "error": redact(str(exc))}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize the D2 listening set")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--vendor", action="append", dest="vendors")
    args = parser.parse_args()
    load_dotenv(Path.cwd())
    settings = Settings.from_environ()
    vendors = args.vendors or list(VENDORS)
    results = asyncio.run(_run(vendors, settings, args.out))
    (args.out / "timings.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
