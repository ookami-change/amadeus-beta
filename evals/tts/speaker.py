"""Speaker similarity between the turbo safety takes and the original turbo lines.

The embedding model is CAM++ from 3D-Speaker, packed for sherpa-onnx. It is
Apache-2.0. The model file is downloaded on first use and is not committed.
Scores are cosine similarity. They are an aid for the listening test. They do
not by themselves decide which take is still the same person.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
)
MODEL_NAME = "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
# sha256 of the sherpa-onnx release asset named above.
MODEL_SHA256 = "e6af4d4c444f3dab01d991bb9b7d5af98eb979e329f6c8880a19d5553561ed25"


def _read_pcm(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"{path} is not mono 16-bit pcm")
        pcm = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
    samples = [sample / 32768.0 for sample in memoryview(pcm).cast("h")]
    return samples, rate


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embeddings differ in length")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("embedding norm is zero")
    return dot / (left_norm * right_norm)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MODEL_NAME
    if MODEL_SHA256 and path.is_file() and _sha256(path) == MODEL_SHA256:
        return path
    temporary = path.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, temporary)
    digest = _sha256(temporary)
    if MODEL_SHA256 and digest != MODEL_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"speaker model sha256 is {digest}, expected {MODEL_SHA256}")
    temporary.replace(path)
    return path


def _extractor(model: Path) -> object:
    import sherpa_onnx

    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(model),
        num_threads=1,
        debug=False,
        provider="cpu",
    )
    if not config.validate():
        raise RuntimeError("sherpa-onnx rejected the speaker model")
    return sherpa_onnx.SpeakerEmbeddingExtractor(config)


def _embed(extractor: object, path: Path) -> list[float]:
    samples, rate = _read_pcm(path)
    stream = extractor.create_stream()  # type: ignore[attr-defined]
    stream.accept_waveform(sample_rate=rate, waveform=samples)
    stream.input_finished()
    if not extractor.is_ready(stream):  # type: ignore[attr-defined]
        raise RuntimeError(f"{path} produced no embedding")
    embedding = extractor.compute(stream)  # type: ignore[attr-defined]
    return [float(value) for value in embedding]


def compare(safe_dir: Path, reference_dir: Path, model: Path, anchor: int = 2) -> dict[str, object]:
    extractor = _extractor(model)
    cache: dict[Path, list[float]] = {}

    def embed(path: Path) -> list[float]:
        cached = cache.get(path)
        if cached is None:
            cached = _embed(extractor, path)
            cache[path] = cached
        return cached

    anchor_path = reference_dir / f"{anchor:02d}.wav"
    anchor_embed = embed(anchor_path)
    rows: list[dict[str, object]] = []
    originals: list[dict[str, object]] = []
    for number in (10, 11, 14):
        reference = reference_dir / f"{number:02d}.wav"
        reference_embed = embed(reference)
        originals.append(
            {
                "line": number,
                "same_line": 1.0,
                "versus_anchor": round(_cosine(reference_embed, anchor_embed), 4),
                "anchor": anchor,
            }
        )
        for variant in ("a", "b", "c", "d"):
            path = safe_dir / f"{number:02d}{variant}.wav"
            sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            taken = embed(path)
            rows.append(
                {
                    "line": number,
                    "variant": variant,
                    "emotion": sidecar.get("emotion"),
                    "speed": sidecar.get("speed"),
                    "volume": sidecar.get("volume"),
                    "pitch": sidecar.get("pitch"),
                    "text": sidecar.get("text"),
                    "same_line": round(_cosine(taken, reference_embed), 4),
                    "versus_anchor": round(_cosine(taken, anchor_embed), 4),
                    "anchor": anchor,
                }
            )
    return {
        "model": MODEL_NAME,
        "model_url": MODEL_URL,
        "license": "Apache-2.0",
        "source": "3D-Speaker CAM++, packed by sherpa-onnx",
        "metric": "cosine similarity of speaker embeddings",
        "anchor_line": anchor,
        "originals_versus_anchor": originals,
        "takes": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the turbo safety takes against the original turbo lines")
    parser.add_argument("--safe", type=Path, default=HERE / "out" / "minimax-safe")
    parser.add_argument("--reference", type=Path, default=HERE / "out" / "minimax-turbo")
    parser.add_argument("--model-dir", type=Path, default=HERE / "models")
    parser.add_argument("--out", type=Path, default=HERE / "out" / "minimax-safe" / "similarity.json")
    args = parser.parse_args()
    model = ensure_model(args.model_dir)
    report = compare(args.safe, args.reference, model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
