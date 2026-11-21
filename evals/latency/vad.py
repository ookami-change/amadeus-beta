"""Silero VAD v5.1.2 on 16 kHz mono PCM.

The ONNX file is downloaded by ``download_turns.sh`` and stays out of git.
Windows are 512 samples (32 ms). The official wrapper prepends 64 samples of
context, so the tensor the session sees is 576 samples wide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HOP_SAMPLES = 512
CONTEXT_SAMPLES = 64
HOP_MS = HOP_SAMPLES / 16.0


class SileroVAD:
    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def probabilities(self, pcm: bytes) -> list[float]:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        rate: Any = np.array(16000, dtype=np.int64)
        found: list[float] = []
        for offset in range(0, audio.size - HOP_SAMPLES + 1, HOP_SAMPLES):
            chunk = audio[offset : offset + HOP_SAMPLES].reshape(1, -1)
            feed = np.concatenate([context, chunk], axis=1)
            raw, state = self._session.run(None, {"input": feed, "state": state, "sr": rate})
            context = feed[:, -CONTEXT_SAMPLES:]
            found.append(float(np.asarray(raw).reshape(-1)[0]))
        return found


def speech_segments(
    probabilities: list[float],
    *,
    hop_ms: float = HOP_MS,
    threshold: float = 0.5,
    min_speech_ms: float = 100.0,
    merge_silence_ms: float = 80.0,
) -> list[tuple[float, float]]:
    """Return ``(start_ms, end_ms)`` of voiced regions.

    Gaps shorter than ``merge_silence_ms`` are swallowed, so a 300 ms pause
    stays visible and a gap inside one syllable does not.
    """

    voiced = [value >= threshold for value in probabilities]
    raw: list[tuple[float, float]] = []
    start: float | None = None
    for index, is_speech in enumerate(voiced):
        if is_speech and start is None:
            start = index * hop_ms
        if not is_speech and start is not None:
            raw.append((start, index * hop_ms))
            start = None
    if start is not None:
        raw.append((start, len(voiced) * hop_ms))
    kept = [(begin, end) for begin, end in raw if end - begin >= min_speech_ms]
    if not kept:
        return []
    merged: list[tuple[float, float]] = [kept[0]]
    for begin, end in kept[1:]:
        previous_start, previous_end = merged[-1]
        if begin - previous_end < merge_silence_ms:
            merged[-1] = (previous_start, end)
        else:
            merged.append((begin, end))
    return merged
