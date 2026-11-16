"""Synthesize the 20-utterance corpus. Audio is a measurement fixture, not a character voice."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from latency.corpus_script import SCRIPT, counts
from latency.pcm import duration_ms, normalize_peak, silence, trim_silence, write_wav

PAUSE_MS = 750.0
TAIL_MS = 1200.0
VOICE = "zh-CN-XiaoxiaoNeural"
HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"


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
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        temp = Path(tmp)
        for line in SCRIPT:
            pieces: list[bytes] = []
            for index, text in enumerate(line.parts):
                mp3 = temp / f"{line.item_id}-{index}.mp3"
                await _speak(text, mp3)
                pcm = trim_silence(_to_pcm(mp3))
                pcm = normalize_peak(pcm)
                if duration_ms(pcm) < 80:
                    raise RuntimeError(f"{line.item_id} part {index} is nearly empty after trimming")
                pieces.append(pcm)
            cursor = 0.0
            segments: list[dict[str, object]] = []
            pauses: list[dict[str, float]] = []
            chunks: list[bytes] = []
            for index, pcm in enumerate(pieces):
                start = cursor
                end = cursor + duration_ms(pcm)
                segments.append({"text": line.parts[index], "start_ms": round(start, 1), "end_ms": round(end, 1)})
                chunks.append(pcm)
                cursor = end
                if index < len(pieces) - 1:
                    pauses.append({"start_ms": round(cursor, 1), "end_ms": round(cursor + PAUSE_MS, 1)})
                    chunks.append(silence(PAUSE_MS))
                    cursor += PAUSE_MS
            speech_end = cursor
            chunks.append(silence(TAIL_MS))
            audio_name = f"{line.item_id}.wav"
            write_wav(out_dir / audio_name, b"".join(chunks))
            items.append(
                {
                    "id": line.item_id,
                    "category": line.category,
                    "text": line.text,
                    "audio": audio_name,
                    "speech_end_ms": round(speech_end, 1),
                    "segments": segments,
                    "pauses": pauses,
                }
            )
    manifest = {
        "sample_rate": 16000,
        "channels": 1,
        "encoding": "pcm_s16le",
        "voice": VOICE,
        "engine": "edge-tts",
        "note": "合成语料只用于测量。它不是角色声线候选，也不是 D2 的供应商对比。",
        "pause_ms": PAUSE_MS,
        "tail_silence_ms": TAIL_MS,
        "counts": counts(),
        "items": items,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    sample = out_dir / "01.wav"
    web_audio = HERE.parents[1] / "apps" / "web" / "public" / "audio" / "sample.wav"
    if sample.is_file() and web_audio.parent.is_dir():
        web_audio.write_bytes(sample.read_bytes())
        print(f"copied a lip-sync sample to {web_audio}")
    print(f"wrote {len(items)} utterances to {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize the M0 latency corpus")
    parser.add_argument("--out", type=Path, default=CORPUS)
    args = parser.parse_args()
    asyncio.run(build(args.out))


if __name__ == "__main__":
    main()
