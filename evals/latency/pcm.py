"""PCM s16le helpers. The corpus and the benchmark stay on 16 kHz mono."""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

RATE = 16000
SAMPLE_WIDTH = 2


@dataclass(frozen=True)
class Frame:
    end_ms: float
    pcm: bytes


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError(f"{path} must be mono 16-bit PCM")
        if handle.getframerate() != RATE:
            raise ValueError(f"{path} must be {RATE} Hz, got {handle.getframerate()}")
        return handle.readframes(handle.getnframes())


def write_wav(path: Path, pcm: bytes, *, rate: int = RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(rate)
        handle.writeframes(pcm)


def rms(pcm: bytes) -> float:
    count = len(pcm) // SAMPLE_WIDTH
    if count == 0:
        return 0.0
    total = 0.0
    for (sample,) in struct.iter_unpack("<h", pcm[: count * SAMPLE_WIDTH]):
        total += float(sample * sample)
    return math.sqrt(total / count) / 32768.0


def silence(ms: float, *, rate: int = RATE) -> bytes:
    count = int(rate * ms / 1000.0)
    return b"\x00\x00" * count


def frames(pcm: bytes, frame_ms: float = 20.0, *, rate: int = RATE) -> list[Frame]:
    samples = int(rate * frame_ms / 1000.0)
    size = samples * SAMPLE_WIDTH
    output: list[Frame] = []
    end = 0.0
    for offset in range(0, len(pcm), size):
        chunk = pcm[offset : offset + size]
        if len(chunk) < size:
            chunk = chunk + (b"\x00\x00" * ((size - len(chunk)) // SAMPLE_WIDTH))
        end += frame_ms
        output.append(Frame(end_ms=end, pcm=chunk))
    return output


def normalize_peak(pcm: bytes, peak: float = 0.7) -> bytes:
    count = len(pcm) // SAMPLE_WIDTH
    if count == 0:
        return pcm
    loudest = 1
    for (sample,) in struct.iter_unpack("<h", pcm[: count * SAMPLE_WIDTH]):
        loudest = max(loudest, abs(sample))
    scale = (peak * 32767.0) / loudest
    out = bytearray()
    for (sample,) in struct.iter_unpack("<h", pcm[: count * SAMPLE_WIDTH]):
        scaled = max(-32768, min(32767, int(sample * scale)))
        out += struct.pack("<h", scaled)
    return bytes(out)


def trim_silence(pcm: bytes, *, threshold: float | None = None, frame_ms: float = 10.0) -> bytes:
    pieces = frames(pcm, frame_ms)
    if not pieces:
        return pcm
    levels = [rms(piece.pcm) for piece in pieces]
    limit = threshold if threshold is not None else max(0.008, max(levels) * 0.08)
    first = 0
    last = len(pieces) - 1
    while first <= last and levels[first] < limit:
        first += 1
    while last >= first and levels[last] < limit:
        last -= 1
    if first > last:
        return b""
    return b"".join(piece.pcm for piece in pieces[first : last + 1])


def duration_ms(pcm: bytes, *, rate: int = RATE) -> float:
    return (len(pcm) // SAMPLE_WIDTH) / rate * 1000.0
