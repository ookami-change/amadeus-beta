"""Score turn detectors on the human recordings.

Fixed silence, Smart Turn, and LiveKit are replayed from one pass over each
file. Doubao's server endpoint comes from the clocks in the volc collect.
Text models see the Doubao partials from the 5000 ms window pass, not the
script. The synthetic-corpus numbers are left where they were.
"""

from __future__ import annotations

import argparse
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from latency.endpoint import Endpointer
from latency.human_labels import Consult, classify_human_endpoint, replay_semantic
from latency.pcm import frames, read_wav, silence
from latency.scoring import Pause, percentile
from latency.vad import HOP_MS, SileroVAD

HERE = Path(__file__).resolve().parent
PAD_MS = 2500.0
CONSULT_MS = 200.0
CAP_MS = 1200.0
RECORD_CAP_MS = 2200.0
FRAME_MS = 20.0
FIXED_SWEEP = (200, 300, 400, 500, 600, 800, 1000, 1200, 1500, 2000)
SMART_SWEEP = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
LIVEKIT_SWEEP = (0.001, 0.003, 0.0066, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5)


class _Mask:
    def __init__(self, voiced: list[bool]) -> None:
        self._voiced = voiced
        self._index = 0

    def speech(self, pcm: bytes) -> bool:
        del pcm
        value = self._voiced[self._index] if self._index < len(self._voiced) else False
        self._index += 1
        return value


class _Record:
    def __init__(self, smart: object, livekit: object) -> None:
        self.smart = smart
        self.livekit = livekit
        self.pcm = b""
        self.silence_ms = 0.0
        self.audio_ms = 0.0
        self.events: list[Consult] = []

    def note_silence(self, silence_ms: float, end_ms: float) -> None:
        self.silence_ms = silence_ms
        self.audio_ms = end_ms

    def is_complete(self, text: str) -> bool:
        import time

        started = time.perf_counter()
        smart_p = float(self.smart.probability(self.pcm, text))  # type: ignore[attr-defined]
        smart_ms = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        livekit_p = float(self.livekit.probability(self.pcm, text))  # type: ignore[attr-defined]
        livekit_ms = (time.perf_counter() - started) * 1000.0
        self.events.append(Consult(self.audio_ms, self.silence_ms, smart_p, smart_ms, livekit_p, livekit_ms))
        return False


def _latest(partials: list[dict[str, object]], end_ms: float) -> str:
    text = ""
    for partial in partials:
        audio = partial.get("audio_ms")
        heard = partial.get("text")
        if isinstance(audio, (int, float)) and isinstance(heard, str) and float(audio) <= end_ms + 1.0:
            text = heard
        elif isinstance(audio, (int, float)) and float(audio) > end_ms + 1.0:
            break
    return text


def _voiced(probabilities: list[float], total_frames: int, original_frames: int) -> list[bool]:
    voiced: list[bool] = []
    for index in range(original_frames):
        if not probabilities:
            voiced.append(False)
            continue
        center = (index + 0.5) * FRAME_MS
        slot = min(len(probabilities) - 1, max(0, int(center / HOP_MS)))
        voiced.append(probabilities[slot] >= 0.5)
    voiced.extend([False] * max(0, total_frames - len(voiced)))
    return voiced[:total_frames]


def _fixed(pcm: bytes, voiced: list[bool], partials: list[dict[str, object]], need_ms: float) -> tuple[float, str, str]:
    gate = _Mask(voiced)
    endpointer = Endpointer("fixed", fixed_silence_ms=need_ms)
    heard = ""
    last = 0.0
    for frame in frames(pcm, FRAME_MS):
        heard = _latest(partials, frame.end_ms)
        last = frame.end_ms
        decision = endpointer.push(frame.end_ms, gate.speech(frame.pcm), heard)
        if decision is not None:
            return decision.t_ms, decision.reason, decision.text
    return last, "audio-end", heard


def _record(
    pcm: bytes,
    voiced: list[bool],
    partials: list[dict[str, object]],
    smart: object,
    livekit: object,
) -> list[Consult]:
    gate = _Mask(voiced)
    model = _Record(smart, livekit)
    endpointer = Endpointer("semantic", model, consult_ms=CONSULT_MS, cap_ms=RECORD_CAP_MS)
    for frame in frames(pcm, FRAME_MS):
        model.pcm = pcm[: int(frame.end_ms / 1000.0 * 16000) * 2]
        heard = _latest(partials, frame.end_ms)
        if endpointer.push(frame.end_ms, gate.speech(frame.pcm), heard) is not None:
            break
    return model.events


def _pauses(raw: object) -> list[Pause]:
    if not isinstance(raw, list):
        return []
    found: list[Pause] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = item.get("start_ms")
        end = item.get("end_ms")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            found.append(Pause(float(start), float(end)))
    return found


def server_clock(client_audio_ms: float, duration_ms: float, pad_ms: float) -> str:
    """A definite on the last packet is the stream closing, not a server endpoint.

    The collector appends ``pad_ms`` of silence. A window longer than that pad
    never fills on a clip whose own tail is short, and the socket then reports
    ``definite`` as the audio runs out.
    """

    if client_audio_ms >= duration_ms + pad_ms - 200.0:
        return "stream-end"
    return "server-definite"


def _row(
    item_id: str,
    speech_end: float,
    pauses: list[Pause],
    endpoint_ms: float,
    reason: str,
    heard: str,
    infer: list[float],
) -> dict[str, object]:
    if reason == "stream-end":
        return {
            "id": item_id,
            "endpoint_ms": round(endpoint_ms, 1),
            "lag_ms": None,
            "reason": reason,
            "false_barge_in": False,
            "where": "unresolved",
            "hypothesis": heard,
            "infer_ms": [],
        }
    barge, where = classify_human_endpoint(speech_end_ms=speech_end, pauses=pauses, t_endpoint_ms=endpoint_ms)
    return {
        "id": item_id,
        "endpoint_ms": round(endpoint_ms, 1),
        "lag_ms": round(endpoint_ms - speech_end, 1),
        "reason": reason,
        "false_barge_in": barge,
        "where": where,
        "hypothesis": heard,
        "infer_ms": [round(value, 2) for value in infer],
    }


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    barge = [row for row in rows if row["false_barge_in"] is True]
    labeled = [row for row in barge if row["where"] == "labeled-pause"]
    short = [row for row in barge if row["where"] == "short-gap"]
    unresolved = [row for row in rows if row["where"] == "unresolved"]
    clean = [row for row in rows if row["false_barge_in"] is False and row["where"] != "unresolved"]
    lags = [float(row["lag_ms"]) for row in clean if isinstance(row["lag_ms"], (int, float))]
    calls: list[float] = []
    for row in rows:
        raw = row["infer_ms"]
        if isinstance(raw, list):
            calls.extend(float(value) for value in raw if isinstance(value, (int, float)))
    count = len(rows)
    return {
        "n": count,
        "false_barge_in": len(barge),
        "false_barge_in_rate": len(barge) / count if count else None,
        "labeled_pause": len(labeled),
        "short_gap": len(short),
        "unresolved": len(unresolved),
        "lag_ms": {"p50": percentile(lags, 50), "p95": percentile(lags, 95), "n": len(lags)},
        "infer_ms": {"p50": percentile(calls, 50), "p95": percentile(calls, 95), "n": len(calls)},
    }


def _markdown(summary: dict[str, dict[str, object]]) -> str:
    lines = [
        (
            "| 方案 | n | 误抢话 | 其中句中停顿 | 其中更短间隙 | 音频结束仍未断句 "
            "| 未抢话的端点滞后 P50 | P95 | 滞后样本 | 单次推理 P50 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in summary.items():
        lag = row["lag_ms"]
        infer = row["infer_ms"]
        if not isinstance(lag, dict) or not isinstance(infer, dict):
            continue
        rate = row["false_barge_in_rate"]
        rate_text = "—" if not isinstance(rate, float) else f"{rate * 100:.1f}%"
        lines.append(
            (
                "| {name} | {n} | {barge}/{n}（{rate}） | {labeled} | {short} "
                "| {unresolved} | {p50} | {p95} | {ln} | {ip50} |"
            ).format(
                name=name,
                n=row["n"],
                barge=row["false_barge_in"],
                rate=rate_text,
                labeled=row["labeled_pause"],
                short=row["short_gap"],
                unresolved=row["unresolved"],
                p50=_ms(lag.get("p50")),
                p95=_ms(lag.get("p95")),
                ln=lag.get("n"),
                ip50=_ms(infer.get("p50")),
            )
        )
    return "\n".join(lines) + "\n"


def _ms(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}ms"
    return "—"


def _pick(summary: dict[str, dict[str, object]]) -> dict[str, object] | None:
    best: dict[str, object] | None = None
    best_lag = float("inf")
    for name, row in summary.items():
        rate = row.get("false_barge_in_rate")
        lag = row.get("lag_ms")
        if row.get("unresolved"):
            continue
        if not isinstance(rate, float) or rate > 0.05 or not isinstance(lag, dict):
            continue
        p50 = lag.get("p50")
        if not isinstance(p50, (int, float)):
            continue
        if float(p50) < best_lag:
            best_lag = float(p50)
            best = {"name": name, "false_barge_in_rate": rate, "lag_p50": p50, "lag_p95": lag.get("p95")}
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="Score human turn-detection schemes")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--volc", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "results" / "turns-human.json")
    args = parser.parse_args()
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    volc = json.loads(args.volc.read_text(encoding="utf-8"))
    items = annotations.get("items")
    volc_items = volc.get("items")
    if not isinstance(items, list) or not isinstance(volc_items, dict):
        raise SystemExit("annotations or volc file is the wrong shape")

    from latency.turn_models import LiveKitTurn, SmartTurn

    smart = SmartTurn(HERE / "models" / "smart-turn-v3.2-cpu.onnx")
    livekit = LiveKitTurn(HERE / "models" / "livekit-turn")
    vad = SileroVAD(HERE / "models" / "silero_vad.onnx")
    prepared: list[dict[str, object]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw["id"])
        pcm = read_wav(args.audio / f"{item_id}.wav") + silence(PAD_MS)
        original = read_wav(args.audio / f"{item_id}.wav")
        probabilities = vad.probabilities(original)
        voiced = _voiced(probabilities, len(frames(pcm, FRAME_MS)), len(frames(original, FRAME_MS)))
        volc_row = volc_items.get(item_id)
        partials: list[dict[str, object]] = []
        if isinstance(volc_row, dict):
            raw_partials = volc_row.get("partials")
            if isinstance(raw_partials, list):
                partials = [item for item in raw_partials if isinstance(item, dict)]
        speech_end = raw.get("speech_end_ms")
        if not isinstance(speech_end, (int, float)):
            raise SystemExit(f"{item_id} has no speech end")
        consults = _record(pcm, voiced, partials, smart, livekit)
        prepared.append(
            {
                "id": item_id,
                "pcm": pcm,
                "voiced": voiced,
                "partials": partials,
                "duration_ms": float(raw["duration_ms"]) if isinstance(raw.get("duration_ms"), (int, float)) else 0.0,
                "speech_end": float(speech_end),
                "pauses": _pauses(raw.get("pauses")),
                "consults": consults,
                "volc": volc_row if isinstance(volc_row, dict) else {},
            }
        )
        print(f"recorded {item_id} consults={len(consults)}", flush=True)

    grouped: dict[str, list[dict[str, object]]] = {}

    def add_fixed(name: str, need: float) -> None:
        rows: list[dict[str, object]] = []
        for item in prepared:
            pcm = item["pcm"]
            voiced = item["voiced"]
            partials = item["partials"]
            assert isinstance(pcm, bytes) and isinstance(voiced, list) and isinstance(partials, list)
            endpoint, reason, heard = _fixed(pcm, voiced, partials, need)
            rows.append(_row(str(item["id"]), float(item["speech_end"]), item["pauses"], endpoint, reason, heard, []))  # type: ignore[arg-type]
        grouped[name] = rows

    def add_semantic(name: str, kind: str, smart_threshold: float, livekit_threshold: float, cap_ms: float) -> None:
        rows = []
        for item in prepared:
            consults = item["consults"]
            voiced = item["voiced"]
            assert isinstance(consults, list) and isinstance(voiced, list)
            endpoint, reason, infer = replay_semantic(
                consults,
                voiced,
                kind=kind,
                smart_threshold=smart_threshold,
                livekit_threshold=livekit_threshold,
                consult_ms=CONSULT_MS,
                cap_ms=cap_ms,
            )
            heard = ""
            rows.append(
                _row(str(item["id"]), float(item["speech_end"]), item["pauses"], endpoint, reason, heard, infer)  # type: ignore[arg-type]
            )
        grouped[name] = rows

    def add_doubao(name: str, key: str) -> None:
        rows = []
        for item in prepared:
            volc_row = item["volc"]
            assert isinstance(volc_row, dict)
            passes = volc_row.get("passes")
            endpoint: float | None = None
            reason = "missing"
            if isinstance(passes, dict):
                one = passes.get(key)
                if isinstance(one, dict):
                    definite = one.get("definite")
                    error = one.get("error")
                    if isinstance(definite, dict) and isinstance(definite.get("client_audio_ms"), (int, float)):
                        endpoint = float(definite["client_audio_ms"])
                        duration = item["duration_ms"]
                        assert isinstance(duration, float)
                        reason = server_clock(endpoint, duration, PAD_MS)
                    elif isinstance(error, str):
                        reason = "error"
            if endpoint is None:
                voiced = item["voiced"]
                assert isinstance(voiced, list)
                endpoint = len(voiced) * FRAME_MS
                if reason == "missing":
                    reason = "no-definite"
            rows.append(
                _row(str(item["id"]), float(item["speech_end"]), item["pauses"], endpoint, reason, "", [])  # type: ignore[arg-type]
            )
        grouped[name] = rows

    for need in (500, 800):
        add_fixed(f"固定静音 {need}ms", float(need))
    add_semantic("Smart Turn 0.5，200/1200", "smart", 0.5, livekit.threshold, CAP_MS)
    add_semantic(f"LiveKit ≥{livekit.threshold:.4f}，200/1200", "livekit", 0.5, livekit.threshold, CAP_MS)
    add_semantic("Smart Turn 且 LiveKit，200/1200", "both", 0.5, livekit.threshold, CAP_MS)
    add_doubao("豆包 end_window 800ms", "800")
    add_doubao("豆包不设 end_window", "semantic")

    curves: dict[str, list[dict[str, object]]] = {
        "固定静音": [],
        "Smart Turn": [],
        "LiveKit": [],
        "两者都判完": [],
        "豆包 end_window": [],
    }
    for need in FIXED_SWEEP:
        add_fixed(f"curve-fixed-{need}", float(need))
        curves["固定静音"].append({"threshold_ms": need, **_summary(grouped.pop(f"curve-fixed-{need}"))})
    for threshold in SMART_SWEEP:
        name = f"curve-smart-{threshold}"
        add_semantic(name, "smart", threshold, livekit.threshold, CAP_MS)
        curves["Smart Turn"].append({"threshold": threshold, **_summary(grouped.pop(name))})
    for threshold in LIVEKIT_SWEEP:
        name = f"curve-livekit-{threshold}"
        add_semantic(name, "livekit", 0.5, threshold, CAP_MS)
        curves["LiveKit"].append({"threshold": threshold, **_summary(grouped.pop(name))})
    for threshold in SMART_SWEEP:
        name = f"curve-both-{threshold}"
        add_semantic(name, "both", threshold, livekit.threshold, CAP_MS)
        curves["两者都判完"].append(
            {
                "smart_threshold": threshold,
                "livekit_threshold": livekit.threshold,
                **_summary(grouped.pop(name)),
            }
        )
    windows = volc.get("windows_ms")
    if isinstance(windows, list):
        for window in windows:
            if not isinstance(window, int):
                continue
            name = f"curve-doubao-{window}"
            add_doubao(name, str(window))
            curves["豆包 end_window"].append({"end_window_ms": window, **_summary(grouped.pop(name))})

    summary = {name: _summary(rows) for name, rows in grouped.items()}
    # The curves were popped out of grouped. Put the eligible curve points into
    # the picker as well, under stable names, without duplicating the trials.
    picker = dict(summary)
    for family, points in curves.items():
        for point in points:
            label = _curve_label(family, point)
            skip = {"threshold", "threshold_ms", "smart_threshold", "livekit_threshold", "end_window_ms"}
            picker[label] = {key: value for key, value in point.items() if key not in skip}
    chosen = _pick(picker)
    payload = {
        "kind": "human-turn-detection",
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pad_ms": PAD_MS,
        "consult_ms": CONSULT_MS,
        "cap_ms": CAP_MS,
        "livekit_threshold": livekit.threshold,
        "smart_threshold": 0.5,
        "tolerance_ms": 80,
        "notes": [
            "误抢话：端点早于真实说完 80ms 以上就算。句中停顿是标注里至少 300ms 的间隙，其余提前判定算更短间隙。",
            "端点滞后只统计没有误抢话的样本，包含这次模型调用的 CPU 时间。",
            "检测器看到的音频在文件末尾补了 2500ms 静音，这样长阈值也能触发。真实说完时刻不含这段补静音。",
            "Smart Turn 和 LiveKit 用豆包 end_window 5000 那一遍的全量中间结果，按送出的音频时钟对齐，不含网络往返。",
            "豆包服务端断句的端点是收到第一条 definite 时已经送出的音频时长。发包 200ms，按实时速率送。",
            "definite 落在发送结束前 200ms 以内的，记为音频发完仍未断句，不进滞后，也不参加 ≤5% 的挑选。",
            "合成语料的旧表还在报告前一节，那组数字对看语调的模型有偏，不作为这里的结论。",
        ],
        "summary": summary,
        "curves": curves,
        "chosen": chosen,
        "trials": grouped,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = args.out.with_suffix(".md")
    curve_rows = {name: row for name, row in picker.items() if name not in summary}
    md.write_text(_markdown(summary) + "\n" + _markdown(curve_rows) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"chosen {chosen}")


def _curve_label(family: str, point: dict[str, object]) -> str:
    if "threshold_ms" in point:
        return f"{family} {point['threshold_ms']}ms"
    if "end_window_ms" in point:
        return f"{family} {point['end_window_ms']}ms"
    if "smart_threshold" in point and family == "两者都判完":
        return f"{family} smart>{point['smart_threshold']}"
    if "threshold" in point:
        return f"{family} {point['threshold']}"
    return family


if __name__ == "__main__":
    main()
