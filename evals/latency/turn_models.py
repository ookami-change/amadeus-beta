"""CPU turn detectors used by the R3 comparison.

Smart Turn reads the waveform. LiveKit's multilingual detector reads the ASR
hypothesis. Both are asked only after the local energy gate has seen silence.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"
SAMPLE_RATE = 16000
SMART_SECONDS = 8


def _pcm_to_float(pcm: bytes) -> Any:
    import numpy as np

    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return np.zeros(1, dtype=np.float32)
    return audio


class SmartTurn:
    """Pipecat Smart Turn v3.2, int8 ONNX, threshold 0.5 as in their inference.py."""

    name = "smart-turn-v3.2"

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort
        from transformers import WhisperFeatureExtractor

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 2
        self._session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        # The constructor is untyped when transformers is installed, and Any when it is not.
        factory: Any = WhisperFeatureExtractor
        self._features = factory(chunk_length=SMART_SECONDS)

    def complete(self, pcm: bytes, text: str) -> bool:
        import numpy as np

        del text
        audio = _pcm_to_float(pcm)
        target = SMART_SECONDS * SAMPLE_RATE
        if audio.size > target:
            audio = audio[-target:]
        elif audio.size < target:
            padded = np.zeros(target, dtype=np.float32)
            padded[-audio.size :] = audio
            audio = padded
        extracted = self._features(
            audio,
            sampling_rate=SAMPLE_RATE,
            return_tensors="np",
            padding="max_length",
            max_length=target,
            truncation=True,
            do_normalize=True,
        )
        features = np.expand_dims(extracted.input_features.squeeze(0).astype(np.float32), axis=0)
        raw = self._session.run(None, {"input_features": features})[0]
        probability = float(np.asarray(raw).reshape(-1)[0])
        return probability > 0.5


def _normalize_livekit(text: str) -> str:
    """Match LiveKit's multilingual runner: NFKC, lower, drop punctuation except ' and -."""

    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text.lower())
    kept = "".join(
        char for char in normalized if not (unicodedata.category(char).startswith("P") and char not in ["'", "-"])
    )
    return re.sub(r"\s+", " ", kept).strip()


class LiveKitTurn:
    """livekit/turn-detector multilingual ONNX, revision v0.4.1-intl.

    A turn is complete when the end-of-utterance probability is at least the
    Chinese threshold published in languages.json.
    """

    name = "livekit-turn-detector-v0.4.1-intl"

    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 2
        self._session = ort.InferenceSession(
            str(model_dir / "model_q8.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(model_dir), truncation_side="left")
        languages = json.loads((model_dir / "languages.json").read_text(encoding="utf-8"))
        chinese = languages.get("zh")
        if not isinstance(chinese, dict) or not isinstance(chinese.get("threshold"), (int, float)):
            raise RuntimeError("languages.json has no numeric zh threshold")
        self.threshold = float(chinese["threshold"])

    def complete(self, pcm: bytes, text: str) -> bool:
        del pcm
        content = _normalize_livekit(text)
        if not content:
            return False
        rendered = self._tokenizer.apply_chat_template(
            [{"role": "user", "content": content}],
            add_generation_prompt=False,
            add_special_tokens=False,
            tokenize=False,
        )
        if not isinstance(rendered, str):
            raise RuntimeError("LiveKit chat template did not return text")
        cut = rendered.rfind("<|im_end|>")
        prompt = rendered[:cut] if cut >= 0 else rendered
        encoded = self._tokenizer(
            prompt, add_special_tokens=False, return_tensors="np", max_length=128, truncation=True
        )
        token_ids = encoded["input_ids"].astype("int64")
        probability = float(self._session.run(None, {"input_ids": token_ids})[0].flatten()[-1])
        return probability >= self.threshold
