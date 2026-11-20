"""Compare the hiss under a voice, not the digital silence between words.

CosyVoice inserts exact zeros in the gaps, so a quiet-frame floor reads as
silence even when the owner hears noise on the voiced part. This measures the
median high-frequency residual of frames louder than -35 dBFS.
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

SPEECH_DB = -35.0


def speech_hiss(path: Path) -> float | None:
    """Median of highpass-residual / RMS on voiced frames. Higher means more hiss."""

    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        pcm = handle.readframes(handle.getnframes())
    samples = array.array("h")
    samples.frombytes(pcm)
    window = max(1, int(rate * 0.02))
    threshold = 32768.0 * (10 ** (SPEECH_DB / 20.0))
    ratios: list[float] = []
    for start in range(0, len(samples) - window, window):
        chunk = samples[start : start + window]
        energy = sum(sample * sample for sample in chunk) / len(chunk)
        rms = math.sqrt(energy)
        if rms <= threshold:
            continue
        previous = chunk[0]
        high = 0.0
        for sample in chunk:
            delta = sample - previous
            high += delta * delta
            previous = sample
        ratios.append(math.sqrt(high / len(chunk)) / rms)
    if not ratios:
        return None
    ratios.sort()
    return ratios[len(ratios) // 2]


def median_hiss(directory: Path) -> float | None:
    values = []
    for number in range(1, 15):
        path = directory / f"{number:02d}.wav"
        if not path.is_file():
            continue
        value = speech_hiss(path)
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    return values[len(values) // 2]
