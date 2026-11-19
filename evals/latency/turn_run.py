"""Compare turn detectors on 20 sentences with a real streaming ASR hypothesis.

The text models see Paraformer partials, not the recording's own transcript.
Silence of 200 ms asks the model. If it says the turn is unfinished, listening
continues until 1200 ms of silence.
"""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from latency.endpoint import CompletenessModel, Endpointer, EnergyGate
from latency.pcm import frames, read_wav
from latency.scoring import Pause, classify_endpoint, percentile

HERE = Path(__file__).resolve().parent
TURNS = HERE / "turns"
PARAFORMER = HERE / "models" / "paraformer"
FRAME_MS = 20.0
CONSULT_MS = 200.0
CAP_MS = 1200.0


@dataclass
class Partial:
    end_ms: float
    text: str


def _recognizer() -> object:
    import sherpa_onnx

    encoder = _prefer(PARAFORMER, "encoder*.onnx")
    decoder = _prefer(PARAFORMER, "decoder*.onnx")
    tokens = next(PARAFORMER.rglob("tokens.txt"))
    return sherpa_onnx.OnlineRecognizer.from_paraformer(
        tokens=str(tokens),
        encoder=str(encoder),
        decoder=str(decoder),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        enable_endpoint_detection=False,
        provider="cpu",
    )


def _partials(pcm: bytes, recognizer: object) -> list[Partial]:
    stream = recognizer.create_stream()  # type: ignore[attr-defined]
    found: list[Partial] = []
    chunk = 16000 // 5  # 200 ms
    samples = memoryview(pcm)
    # int16 mono
    step = chunk * 2
    cursor = 0
    while cursor < len(samples):
        piece = bytes(samples[cursor : cursor + step])
        cursor += len(piece)
        audio = _float_audio(piece)
        stream.accept_waveform(16000, audio)
        while recognizer.is_ready(stream):  # type: ignore[attr-defined]
            recognizer.decode_stream(stream)  # type: ignore[attr-defined]
        text = _result_text(recognizer.get_result(stream))  # type: ignore[attr-defined]
        found.append(Partial(cursor / 2 / 16000 * 1000.0, text))
    stream.input_finished()
    while recognizer.is_ready(stream):  # type: ignore[attr-defined]
        recognizer.decode_stream(stream)  # type: ignore[attr-defined]
    found.append(Partial(len(pcm) / 2 / 16000 * 1000.0, _result_text(recognizer.get_result(stream))))  # type: ignore[attr-defined]
    return found


def _prefer(root: Path, pattern: str) -> Path:
    matches = sorted(root.rglob(pattern), key=lambda path: (0 if "int8" in path.name else 1, path.name))
    if not matches:
        raise SystemExit(f"missing {pattern} under {root}. Run evals/latency/download_turns.sh")
    return matches[0]


def _float_audio(pcm: bytes) -> list[float]:
    import array

    values = array.array("h")
    values.frombytes(pcm)
    return [sample / 32768.0 for sample in values]


def _result_text(result: object) -> str:
    text = getattr(result, "text", result)
    return text if isinstance(text, str) else str(text)


def _latest(partials: list[Partial], end_ms: float) -> str:
    text = ""
    for partial in partials:
        if partial.end_ms <= end_ms + 1.0:
            text = partial.text
        else:
            break
    return text


class _Ask:
    def __init__(self, model: object, pcm: bytes) -> None:
        self.model = model
        self.pcm = pcm
        self.calls_ms: list[float] = []

    def is_complete(self, text: str) -> bool:
        import time

        started = time.perf_counter()
        done = self.model.complete(self.pcm, text)  # type: ignore[attr-defined]
        self.calls_ms.append((time.perf_counter() - started) * 1000.0)
        return bool(done)


def _walk(
    pcm: bytes,
    partials: list[Partial],
    *,
    mode: str,
    model: CompletenessModel | None,
) -> tuple[float, str, str, list[float]]:
    gate = EnergyGate()
    endpointer = Endpointer(mode, model, consult_ms=CONSULT_MS, cap_ms=CAP_MS)
    ask = model if isinstance(model, _Ask) else None
    hypothesis = ""
    for frame in frames(pcm, FRAME_MS):
        hypothesis = _latest(partials, frame.end_ms)
        if ask is not None:
            ask.pcm = pcm[: int(frame.end_ms / 1000.0 * 16000) * 2]
        decision = endpointer.push(frame.end_ms, gate.speech(frame.pcm), hypothesis)
        if decision is not None:
            calls = ask.calls_ms if ask is not None else []
            return decision.t_ms, decision.reason, decision.text, calls
    return frames(pcm, FRAME_MS)[-1].end_ms, "audio-end", hypothesis, []


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    early = [row for row in rows if row["early"] is True]
    barge = [row for row in rows if row["false_barge_in"] is True]
    clean = [row for row in rows if row["early"] is False]
    lags: list[float] = []
    for row in clean:
        lag = row["lag_ms"]
        if isinstance(lag, (int, float)):
            lags.append(float(lag))
    calls: list[float] = []
    for row in rows:
        raw = row["infer_ms"]
        if not isinstance(raw, list):
            continue
        for value in raw:
            if isinstance(value, (int, float)):
                calls.append(float(value))
    return {
        "n": len(rows),
        "early": len(early),
        "early_rate": len(early) / len(rows) if rows else None,
        "false_barge_in": len(barge),
        "false_barge_in_rate": len(barge) / len(rows) if rows else None,
        "lag_ms": {"p50": percentile(lags, 50), "p95": percentile(lags, 95), "n": len(lags)},
        "infer_ms": {"p50": percentile(calls, 50), "p95": percentile(calls, 95), "n": len(calls)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the R3 turn-detection comparison")
    parser.add_argument("--manifest", type=Path, default=TURNS / "manifest.json")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "turns.json")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items = manifest["items"]
    if not isinstance(items, list) or len(items) < 20:
        raise SystemExit("turn manifest needs 20 items")

    from latency.turn_models import LiveKitTurn, SmartTurn

    smart = SmartTurn(HERE / "models" / "smart-turn-v3.2-cpu.onnx")
    livekit = LiveKitTurn(HERE / "models" / "livekit-turn")
    schemes: list[tuple[str, str, object | None]] = [
        ("fixed-500", "fixed", None),
        ("smart-turn+200/1200", "semantic", smart),
        ("livekit-text+200/1200", "semantic", livekit),
    ]
    prepared: list[tuple[dict[str, object], bytes, list[Partial]]] = []
    recognizer = _recognizer()
    for item in items:
        if not isinstance(item, dict):
            continue
        audio = args.manifest.parent / str(item["audio"])
        pcm = read_wav(audio)
        prepared.append((item, pcm, _partials(pcm, recognizer)))

    grouped: dict[str, list[dict[str, object]]] = {}
    for name, mode, model in schemes:
        rows: list[dict[str, object]] = []
        for item, pcm, partials in prepared:
            ask = _Ask(model, pcm) if model is not None else None
            endpoint_ms, reason, heard, infer = _walk(pcm, partials, mode=mode, model=ask)
            pauses = item["pauses"]
            if not isinstance(pauses, list):
                raise RuntimeError(f"{item['id']} has no pause list")
            pause_list: list[Pause] = []
            for pause in pauses:
                if not isinstance(pause, dict):
                    continue
                start = pause["start_ms"]
                end = pause["end_ms"]
                if isinstance(start, (int, float)) and isinstance(end, (int, float)):
                    pause_list.append(Pause(float(start), float(end)))
            raw_end = item["speech_end_ms"]
            if not isinstance(raw_end, (int, float)):
                raise RuntimeError(f"{item['id']} has no speech end")
            speech_end = float(raw_end)
            early, barge = classify_endpoint(speech_end_ms=speech_end, pauses=pause_list, t_endpoint_ms=endpoint_ms)
            rows.append(
                {
                    "id": item["id"],
                    "endpoint_ms": round(endpoint_ms, 1),
                    "lag_ms": round(endpoint_ms - speech_end, 1),
                    "reason": reason,
                    "early": early,
                    "false_barge_in": barge,
                    "hypothesis": heard,
                    "infer_ms": [round(value, 2) for value in infer],
                }
            )
            print(f"{name} {item['id']} {'barge' if barge else 'held'} {reason}", flush=True)
        grouped[name] = rows

    payload = {
        "kind": "turn-detection",
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "asr": "sherpa-onnx streaming paraformer",
        "consult_ms": CONSULT_MS,
        "cap_ms": CAP_MS,
        "livekit_threshold": livekit.threshold,
        "notes": [
            "文本模型的输入是流式 Paraformer 的当前整句假设，不是语料原文。",
            "静音满 200ms 问一次模型；模型判没说完就继续听，静音满 1200ms 强制结束。",
            "端点滞后只统计没有提前截断的样本。推理耗时是每次调用模型的 CPU 时间。",
            "TEN Turn Detection 只发布了四份 safetensors，合计 15231271864 字节，没有 onnx 或 gguf。"
            "当时这台机器可用内存约 4.5 GiB，没有加载它，所以没有它的数字。",
        ],
        "summary": {name: _summary(rows) for name, rows in grouped.items()},
        "trials": grouped,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
