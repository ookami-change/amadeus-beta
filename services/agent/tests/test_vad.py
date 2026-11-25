"""Streaming Silero on synthetic audio. Skipped without the model."""

from __future__ import annotations

import math
import struct
import unittest

from agent.settings import Settings
from agent.vad import SileroVAD, SpeechTracker

MODEL = Settings.from_environ().vad_model


def voiced(ms: float) -> bytes:
    """A vowel-like buzz: 140 Hz with harmonics, loud enough for Silero."""
    out = []
    for i in range(int(ms * 16)):
        t = i / 16000.0
        x = sum(math.sin(2 * math.pi * 140 * k * t) / k for k in range(1, 12))
        out.append(struct.pack("<h", int(6000 * x * (0.6 + 0.4 * math.sin(2 * math.pi * 4 * t)))))
    return b"".join(out)


@unittest.skipUnless(MODEL.is_file(), "run scripts/download_models.sh")
class SileroTests(unittest.TestCase):
    def test_streaming_tracker_runs_on_the_model(self) -> None:
        tracker = SpeechTracker(SileroVAD(MODEL).probability)
        audio = b"\x00\x00" * 16 * 500 + voiced(1200) + b"\x00\x00" * 16 * 1500
        probabilities = []
        for offset in range(0, len(audio), 640):
            tracker.feed(audio[offset : offset + 640])
            probabilities.append(tracker.speaking)
        # Whatever Silero makes of a synthetic buzz, the tracker must end in silence with a clock.
        self.assertAlmostEqual(tracker.audio_ms, len(audio) / 32, delta=32)
        self.assertFalse(tracker.speaking)


if __name__ == "__main__":
    unittest.main()
