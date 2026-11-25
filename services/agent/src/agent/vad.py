"""Streaming voice activity: Silero VAD v5 plus the segment rules the M0 timeline used.

R3b/R3c/R6b took their silences from Silero run over each whole file
(threshold 0.5, voiced runs >= 100 ms, gaps < 80 ms merged). ``SpeechTracker``
applies the same rules causally, so the live engine sees the same silences,
plus the ~100 ms it takes to confirm that speech has resumed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

HOP_SAMPLES = 512
HOP_BYTES = HOP_SAMPLES * 2
HOP_MS = HOP_SAMPLES / 16.0  # 32 ms
CONTEXT_SAMPLES = 64
THRESHOLD = 0.5
MIN_SPEECH_MS = 100.0
MERGE_GAP_MS = 80.0


class SileroVAD:
    """One stream's model state. ``probability`` takes exactly one 512-sample hop."""

    def __init__(self, model_path: Path) -> None:
        import numpy as np
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._np = np
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._state: Any = np.zeros((2, 1, 128), dtype=np.float32)
        self._context: Any = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._rate: Any = np.array(16000, dtype=np.int64)

    def probability(self, hop: bytes) -> float:
        np = self._np
        chunk = (np.frombuffer(hop, dtype=np.int16).astype(np.float32) / 32768.0).reshape(1, -1)
        feed = np.concatenate([self._context, chunk], axis=1)
        raw, self._state = self._session.run(
            None, {"input": feed, "state": self._state, "sr": self._rate}
        )
        self._context = feed[:, -CONTEXT_SAMPLES:]
        return float(np.asarray(raw).reshape(-1)[0])


class SpeechTracker:
    """Turns per-hop probabilities into confirmed speech and the silence since it ended.

    A voiced run counts once it reaches ``MIN_SPEECH_MS`` (4 hops). Shorter runs
    are ignored, as the offline segmenter dropped them. Silence is measured
    from the end of the last confirmed voiced hop.
    """

    def __init__(self, probability: Callable[[bytes], float]) -> None:
        self._probability = probability
        self._pending = bytearray()
        self.audio_ms = 0.0
        self._run_start: float | None = None
        self.speaking = False
        self.speech_start_ms: float | None = None
        """Start of the current confirmed voiced stretch."""
        self.speech_end_ms: float | None = None
        """End of the last confirmed voiced hop; ``None`` before any speech."""

    def feed(self, pcm: bytes) -> list[str]:
        """Advance the clock. Returns events: ``onset`` (speech confirmed) and ``offset``."""
        events: list[str] = []
        self._pending.extend(pcm)
        while len(self._pending) >= HOP_BYTES:
            hop = bytes(self._pending[:HOP_BYTES])
            del self._pending[:HOP_BYTES]
            start = self.audio_ms
            self.audio_ms += HOP_MS
            voiced = self._probability(hop) >= THRESHOLD
            if voiced:
                if self._run_start is None:
                    self._run_start = start
                if self.speaking:
                    self.speech_end_ms = self.audio_ms
                elif (
                    self.speech_end_ms is not None
                    and start - self.speech_end_ms < MERGE_GAP_MS - 1e-6
                ):
                    # A gap this short was merged offline: the same stretch goes on.
                    self.speaking = True
                    self.speech_end_ms = self.audio_ms
                elif self.audio_ms - self._run_start >= MIN_SPEECH_MS - 1e-6:
                    self.speaking = True
                    self.speech_start_ms = self._run_start
                    self.speech_end_ms = self.audio_ms
                    events.append("onset")
            else:
                self._run_start = None
                if self.speaking:
                    self.speaking = False
                    events.append("offset")
        return events

    def silence_ms(self, now_ms: float | None = None) -> float | None:
        """Silence since the last confirmed speech, or ``None`` while speaking or before any."""
        if self.speaking or self.speech_end_ms is None:
            return None
        return (self.audio_ms if now_ms is None else now_ms) - self.speech_end_ms
