"""Synthesize the 20-sentence turn-detection set. Not a character voice."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from latency.pcm import duration_ms, normalize_peak, silence, trim_silence, write_wav
from latency.turn_script import TURNS, validate

TAIL_MS = 1600.0
VOICE = "zh-CN-XiaoxiaoNeural"
HERE = Path(__file__).resolve().parent
OUT = HERE / "turns"


async def _speak(text: str, mp3_path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, VOICE).save(str(mp3_path))


def _to_pcm(mp3_path: Path) -> bytes:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(mp3_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    return completed.stdout


async def build(out_dir: Path) -> None:
    validate()
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        for line in TURNS:
            pieces: list[bytes] = []
            for index, text in enumerate(line.parts):
                mp3 = temp / f"{line.item_id}-{index}.mp3"
                await _speak(text, mp3)
                pcm = normalize_peak(trim_silence(_to_pcm(mp3)))
                if duration_ms(pcm) < 80:
                    raise RuntimeError(f"{line.item_id} part {index} is nearly empty after trimming")
                pieces.append(pcm)
            cursor = 0.0
            segments: list[dict[str, object]] = []
            chunks: list[bytes] = []
            for index, pcm in enumerate(pieces):
                start = cursor
                end = cursor + duration_ms(pcm)
                segments.append({"text": line.parts[index], "start_ms": round(start, 1), "end_ms": round(end, 1)})
                chunks.append(pcm)
                cursor = end
                if index == 0:
                    pause_end = cursor + line.pause_ms
                    chunks.append(silence(line.pause_ms))
                    cursor = pause_end
            speech_end = cursor
            chunks.append(silence(TAIL_MS))
            write_wav(out_dir / f"{line.item_id}.wav", b"".join(chunks))
            end_ms = segments[0]["end_ms"]
            if not isinstance(end_ms, float):
                raise RuntimeError(f"{line.item_id} segment end is missing")
            pause_start = end_ms
            items.append(
                {
                    "id": line.item_id,
                    "category": "pause",
                    "text": line.text,
                    "audio": f"{line.item_id}.wav",
                    "speech_end_ms": round(speech_end, 1),
                    "pause_ms": line.pause_ms,
                    "segments": segments,
                    "pauses": [{"start_ms": round(pause_start, 1), "end_ms": round(pause_start + line.pause_ms, 1)}],
                }
            )
    manifest = {
        "sample_rate": 16000,
        "channels": 1,
        "encoding": "pcm_s16le",
        "voice": VOICE,
        "engine": "edge-tts",
        "note": "带句中停顿的测量语料。不是角色声线。",
        "tail_silence_ms": TAIL_MS,
        "items": items,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(items)} turn utterances to {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize the turn-detection corpus")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    asyncio.run(build(args.out))


if __name__ == "__main__":
    main()
