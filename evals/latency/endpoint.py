"""Fixed-silence and semantic endpointers.

Both see the same voiced/unvoiced frames. The fixed endpointer fires after
500 ms of silence. The semantic one consults a completeness model at 200 ms
and keeps listening through a pause when the words so far are not a finished turn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from latency.pcm import rms


class CompletenessModel(Protocol):
    def is_complete(self, text: str) -> bool:
        """True when ``text`` is a finished conversational turn."""


@dataclass(frozen=True)
class Decision:
    """``audio_ms`` is the frame time. ``compute_ms`` is model time on top of that."""

    audio_ms: float
    compute_ms: float
    text: str
    reason: str

    @property
    def t_ms(self) -> float:
        return self.audio_ms + self.compute_ms


class EnergyGate:
    """Speech gate for clean, peak-normalized recordings. Not a noise-robust VAD."""

    def __init__(self, threshold: float = 0.02) -> None:
        self.threshold = threshold

    def speech(self, pcm: bytes) -> bool:
        return rms(pcm) >= self.threshold


class Endpointer:
    def __init__(
        self,
        mode: str,
        model: CompletenessModel | None = None,
        *,
        fixed_silence_ms: float = 500.0,
        consult_ms: float = 200.0,
        cap_ms: float = 1500.0,
        arm_ms: float = 80.0,
    ) -> None:
        if mode not in {"fixed", "semantic"}:
            raise ValueError(mode)
        if mode == "semantic" and model is None:
            raise ValueError("semantic endpointing needs a completeness model")
        self.mode = mode
        self.model = model
        self.fixed_silence_ms = fixed_silence_ms
        self.consult_ms = consult_ms
        self.cap_ms = cap_ms
        self.arm_ms = arm_ms
        self._prev_end = 0.0
        self._speech_ms = 0.0
        self._armed = False
        self._silence_start: float | None = None
        self._checked_text: str | None = None
        self._complete = False
        self._compute_ms = 0.0
        self.fired = False

    def push(self, end_ms: float, speech: bool, text: str) -> Decision | None:
        if self.fired:
            return None
        frame_ms = max(0.0, end_ms - self._prev_end)
        self._prev_end = end_ms
        if speech:
            self._speech_ms += frame_ms
            self._silence_start = None
            self._checked_text = None
            if self._speech_ms >= self.arm_ms:
                self._armed = True
            return None
        if not self._armed:
            return None
        if self._silence_start is None:
            self._silence_start = end_ms - frame_ms
        silence_ms = end_ms - self._silence_start
        if self.mode == "fixed":
            if silence_ms >= self.fixed_silence_ms:
                self.fired = True
                return Decision(end_ms, 0.0, text, f"silence-{int(self.fixed_silence_ms)}")
            return None
        if silence_ms < self.consult_ms:
            return None
        if self._checked_text != text:
            assert self.model is not None
            note = getattr(self.model, "note_silence", None)
            if callable(note):
                note(silence_ms, end_ms)
            started = time.perf_counter()
            self._complete = self.model.is_complete(text)
            self._compute_ms = (time.perf_counter() - started) * 1000.0
            self._checked_text = text
        if self._complete or silence_ms >= self.cap_ms:
            self.fired = True
            reason = "complete" if self._complete else "silence-cap"
            return Decision(end_ms, self._compute_ms, text, reason)
        return None


class KeywordCompleteness:
    """Test double. A turn is finished when the text already ends on a stop."""

    def is_complete(self, text: str) -> bool:
        stripped = text.strip()
        return len(stripped) >= 2 and stripped.endswith(("。", "！", "？", "!", "?"))


class PunctuationCompleteness:
    """CT-Transformer punctuation via sherpa-onnx. The model file is downloaded, not vendored."""

    def __init__(self, model_path: str) -> None:
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError("Install the semantic group first: uv sync --directory evals --group semantic") from exc
        config = sherpa_onnx.OfflinePunctuationConfig()
        config.model.ct_transformer = model_path
        self._punct = sherpa_onnx.OfflinePunctuation(config)

    def is_complete(self, text: str) -> bool:
        bare = "".join(ch for ch in text if ch not in "，。！？、；：…,.!?;: \n\t")
        if len(bare) < 2:
            return False
        restored = str(self._punct.add_punctuation(bare)).rstrip()
        return restored.endswith(("。", "！", "？", "!", "?"))
