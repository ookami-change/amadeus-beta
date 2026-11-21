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
from tts.lines import DOUBAO_TUNED, LINES, MINIMAX_SAFE, MINIMAX_TUNED, Line, MiniMaxTune

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
VENDORS = ("volc", "minimax", "dashscope")


async def _one(
    vendor: str,
    line: Line,
    settings: Settings,
    out_dir: Path,
    *,
    folder: str | None = None,
    instruction: str | None = None,
    model: str | None = None,
    voice: str | None = None,
    allow_instruction: bool | None = None,
    sample_rate: int = 22050,
    pitch: int = 0,
    text: str | None = None,
    stem: str | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    spoken = line.instruction if instruction is None else instruction
    said = line.text if text is None else text
    if model is None and vendor == "minimax":
        model = "speech-2.8-hd"
    tts = build_tts(
        vendor,
        settings,
        emotion=line.emotion,
        speed=line.speed,
        volume=line.volume,
        pitch=pitch,
        model=model,
        voice=voice,
        allow_instruction=allow_instruction,
        sample_rate=sample_rate,
    )
    marks: list[dict[str, object]] = []
    pcm = bytearray()
    sample_rate = 24000
    first_ms: float | None = None
    started = time.perf_counter()

    async def text_once() -> AsyncIterator[str]:
        yield said

    await tts.open(instruction=spoken)
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
    target = out_dir / (folder or vendor)
    target.mkdir(parents=True, exist_ok=True)
    name = stem or f"{line.number:02d}"
    wav_path = target / f"{name}.wav"
    _write_wav(wav_path, bytes(pcm), sample_rate)
    record: dict[str, object] = {
        "line": line.number,
        "text": said,
        "hint": line.hint,
        "instruction": spoken,
        "model": model,
        "voice": voice,
        "emotion": line.emotion,
        "speed": line.speed,
        "volume": line.volume,
        "pitch": pitch,
        "first_audio_ms": round(first_ms, 1),
        "sample_rate": sample_rate,
        "marks": marks,
    }
    if extra:
        record.update(extra)
    (target / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
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


def _line_for(number: int) -> Line:
    return next(line for line in LINES if line.number == number)


async def _minimax_set(
    settings: Settings,
    out_dir: Path,
    *,
    model: str,
    folder: str,
    takes: list[MiniMaxTune] | None,
) -> list[dict[str, object]]:
    missing = settings.missing_for("-", "-", "minimax")
    if missing:
        raise SystemExit("缺少语音合成密钥，没有写出试听音频：" + ", ".join(missing))
    results: list[dict[str, object]] = []
    plan: list[tuple[Line, MiniMaxTune | None]]
    if takes is None:
        plan = [(line, None) for line in LINES]
    else:
        plan = [(_line_for(take.number), take) for take in takes]
    for line, take in plan:
        emotion = line.emotion if take is None else take.emotion
        speed = line.speed if take is None else take.speed
        volume = line.volume if take is None else take.volume
        pitch = 0 if take is None else take.pitch
        text = None if take is None else take.text
        tuned_line = Line(line.number, line.text, line.hint, line.instruction, emotion, speed, volume)
        result = {"vendor": "minimax", "line": line.number, "error": "not started"}
        for attempt in range(1, 4):
            try:
                result = await _one(
                    "minimax",
                    tuned_line,
                    settings,
                    out_dir,
                    folder=folder,
                    model=model,
                    pitch=pitch,
                    text=text,
                )
                break
            except Exception as exc:
                message = redact(str(exc))
                result = {"vendor": "minimax", "line": line.number, "error": message}
                if attempt == 3 or "rate limit" not in message.lower():
                    break
                await asyncio.sleep(8.0 * attempt)
        result["model"] = model
        result["folder"] = folder
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        await asyncio.sleep(1.2)
    return results


async def _run(vendors: list[str], settings: Settings, out_dir: Path, *, tuned: bool) -> list[dict[str, object]]:
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
                spoken = DOUBAO_TUNED[line.number] if tuned else None
                result = await _one(vendor, line, settings, out_dir, instruction=spoken)
            except Exception as exc:
                result = {"vendor": vendor, "line": line.number, "error": redact(str(exc))}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return results


async def _minimax_safe(settings: Settings, out_dir: Path, *, model: str, folder: str) -> list[dict[str, object]]:
    missing = settings.missing_for("-", "-", "minimax")
    if missing:
        raise SystemExit("缺少语音合成密钥，没有写出试听音频：" + ", ".join(missing))
    results: list[dict[str, object]] = []
    for take in MINIMAX_SAFE:
        line = _line_for(take.number)
        tuned_line = Line(line.number, line.text, line.hint, line.instruction, take.emotion, take.speed, take.volume)
        stem = f"{take.number:02d}{take.variant}"
        result: dict[str, object] = {
            "vendor": "minimax",
            "line": take.number,
            "variant": take.variant,
            "error": "not started",
        }
        for attempt in range(1, 4):
            try:
                result = await _one(
                    "minimax",
                    tuned_line,
                    settings,
                    out_dir,
                    folder=folder,
                    model=model,
                    voice=settings.minimax_voice,
                    pitch=take.pitch,
                    text=take.text,
                    stem=stem,
                    extra={"variant": take.variant, "note": take.note},
                )
                break
            except Exception as exc:
                message = redact(str(exc))
                result = {
                    "vendor": "minimax",
                    "line": take.number,
                    "variant": take.variant,
                    "error": message,
                }
                if attempt == 3 or "rate limit" not in message.lower():
                    break
                await asyncio.sleep(8.0 * attempt)
        result["model"] = model
        result["folder"] = folder
        result["variant"] = take.variant
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        await asyncio.sleep(1.2)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize the D2 listening set")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--vendor", action="append", dest="vendors")
    parser.add_argument("--tuned", action="store_true", help="Use the per-line Doubao context_texts")
    parser.add_argument("--model", help="Override the vendor model. MiniMax uses this as speech-2.8-hd or turbo")
    parser.add_argument("--folder", help="Subfolder under --out. Defaults to the vendor name")
    parser.add_argument("--minimax-tuned", action="store_true", help="Write tuned takes to minimax-tuned")
    parser.add_argument("--minimax-safe", action="store_true", help="Write the four turbo safety takes")
    args = parser.parse_args()
    load_dotenv(Path.cwd())
    settings = Settings.from_environ()
    if args.minimax_safe:
        model = args.model or "speech-2.8-turbo"
        folder = args.folder or "minimax-safe"
        results = asyncio.run(_minimax_safe(settings, args.out, model=model, folder=folder))
        target = args.out / folder
        target.mkdir(parents=True, exist_ok=True)
        (target / "timings.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if args.minimax_tuned or (args.model and args.vendors == ["minimax"]):
        model = args.model or "speech-2.8-hd"
        if args.minimax_tuned:
            folder = args.folder or "minimax-tuned"
            takes: list[MiniMaxTune] | None = list(MINIMAX_TUNED)
        else:
            folder = args.folder or ("minimax-turbo" if "turbo" in model else "minimax-hd")
            takes = None
        results = asyncio.run(_minimax_set(settings, args.out, model=model, folder=folder, takes=takes))
    else:
        vendors = args.vendors or list(VENDORS)
        results = asyncio.run(_run(vendors, settings, args.out, tuned=args.tuned))
    (args.out / "timings.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
