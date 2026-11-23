"""R3c: replay combined turn-detection strategies on the human recordings.

The recordings themselves stay on the test machine. Everything here runs from
three files R3b committed:

* ``turns-human-annotations.json``: true speech end, VAD pauses (>= 300 ms) and
  short gaps (80-300 ms) for each of the 50 recordings.
* ``turns-human-volc.json``: Doubao streaming partials with the audio clock at
  which each one arrived, and the server ``definite`` clock for each
  ``end_window`` pass.
* ``turns-human.json``: R3b's own fixed-silence rows, used to check the replay.

The silence timeline is rebuilt from the VAD gaps, so a local endpointer here
sees the same silences R3b's did (``check_fixed`` compares the two). Strategies
that need a text judge read the partial the client holds at that frame, so ASR
lag is part of the result. Judge time is measured on this machine and added to
the endpoint.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from latency.human_labels import classify_human_endpoint
from latency.scoring import Pause, percentile

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FRAME_MS = 20.0
ARM_MS = 80.0
PAD_MS = 2500.0

# A partial ending on one of these is a turn that is still going.
CONNECTORS = (
    "然后",
    "就是",
    "所以",
    "但是",
    "可是",
    "不过",
    "而且",
    "因为",
    "如果",
    "要是",
    "的话",
    "还有",
    "或者",
    "比如",
    "那个",
    "这个",
    "那",
    "就",
    "和",
    "跟",
    "嗯",
    "呃",
    "额",
)
# Sentence-final particles and stops: the turn is most likely done.
FINALS = ("吗", "呢", "吧", "啦", "呀", "嘛", "了", "啊", "哦", "。", "？", "！", "?", "!")
STOPS = ("。", "？", "！", "?", "!")
STRIP = "，,、；;：: \t\n"

KINDS = {"t": "照着说", "f": "自由说", "s": "短句"}


@dataclass(frozen=True)
class Gap:
    start_ms: float
    end_ms: float


@dataclass(frozen=True)
class Partial:
    audio_ms: float
    text: str


@dataclass
class Item:
    id: str
    duration_ms: float
    speech_end_ms: float
    pauses: list[Pause]
    gaps: list[Gap]
    partials: list[Partial]
    server: dict[str, float | None] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return KINDS[self.id[0]]

    @property
    def total_ms(self) -> float:
        return self.duration_ms + PAD_MS


@dataclass(frozen=True)
class Outcome:
    endpoint_ms: float
    reason: str
    text: str
    judge_ms: tuple[float, ...] = ()


def tail(text: str) -> str:
    """The partial without trailing commas or spaces; stops are kept."""
    return text.rstrip(STRIP)


def ends_with_connector(text: str) -> bool:
    bare = tail(text)
    return bool(bare) and bare.endswith(CONNECTORS)


def ends_with_final(text: str) -> bool:
    bare = tail(text)
    return bool(bare) and bare.endswith(FINALS)


def latest(partials: Sequence[Partial], t_ms: float) -> str:
    """Text the client holds at audio time ``t_ms`` (same rule as R3b's ``_latest``)."""
    text = ""
    for heard in partials:
        if heard.audio_ms <= t_ms + 1.0:
            text = heard.text
        else:
            break
    return text


def load(annotations: Path, volc: Path) -> list[Item]:
    marks = json.loads(annotations.read_text(encoding="utf-8"))
    streams = json.loads(volc.read_text(encoding="utf-8"))["items"]
    items: list[Item] = []
    for raw in marks["items"]:
        item_id = str(raw["id"])
        gaps = [
            Gap(float(g["start_ms"]), float(g["end_ms"])) for g in raw.get("pauses", []) + raw.get("short_gaps", [])
        ]
        gaps.sort(key=lambda gap: gap.start_ms)
        duration = float(raw["duration_ms"])
        vad_end = raw.get("vad_end_ms")
        tail_start = float(vad_end) if isinstance(vad_end, (int, float)) else float(raw["speech_end_ms"])
        gaps.append(Gap(tail_start, duration + PAD_MS))
        stream = streams.get(item_id, {})
        partials = [
            Partial(float(p["audio_ms"]), str(p["text"]))
            for p in stream.get("partials", [])
            if isinstance(p.get("audio_ms"), (int, float))
        ]
        server: dict[str, float | None] = {}
        for key, one in stream.get("passes", {}).items():
            definite = one.get("definite") if isinstance(one, dict) else None
            clock = definite.get("client_audio_ms") if isinstance(definite, dict) else None
            # A definite on the last packet is the stream closing, not a server endpoint (R3b rule).
            if isinstance(clock, (int, float)) and float(clock) < duration + PAD_MS - 200.0:
                server[key] = float(clock)
            else:
                server[key] = None
        items.append(
            Item(
                id=item_id,
                duration_ms=duration,
                speech_end_ms=float(raw["speech_end_ms"]),
                pauses=[Pause(float(p["start_ms"]), float(p["end_ms"])) for p in raw.get("pauses", [])],
                gaps=gaps,
                partials=partials,
                server=server,
            )
        )
    return items


def _frame_start(t_ms: float) -> float:
    """Start of the first 20 ms frame whose centre falls in a gap starting at ``t_ms``.

    R3b maps each frame to the VAD hop under its centre, so silence starts at
    the first frame centred at or after the gap start.
    """
    index = math.ceil(t_ms / FRAME_MS - 0.5)
    return max(0, index) * FRAME_MS


Rule = Callable[[Item, float, float], tuple[bool, float]]
"""``rule(item, t_ms, silence_ms) -> (fire, judge_ms)`` evaluated once per silent frame."""


def walk(item: Item, rule: Rule) -> Outcome:
    """Run ``rule`` on every silent frame, gap by gap, until it fires."""
    spent: list[float] = []
    for gap in item.gaps:
        start = _frame_start(gap.start_ms)
        t = start + FRAME_MS
        while t <= min(gap.end_ms, item.total_ms) + 1e-6:
            fire, cost = rule(item, t, t - start)
            if cost > 0.0:
                spent.append(cost)
            if fire:
                return Outcome(t + (cost if cost > 0.0 else 0.0), "fire", latest(item.partials, t), tuple(spent))
            t += FRAME_MS
    return Outcome(item.total_ms, "audio-end", latest(item.partials, item.total_ms), tuple(spent))


def fixed(need_ms: float) -> Rule:
    def rule(item: Item, t_ms: float, silence_ms: float) -> tuple[bool, float]:
        del item, t_ms
        return silence_ms + 1e-6 >= need_ms, 0.0

    return rule


def adaptive(short_ms: float, long_ms: float) -> Rule:
    """S1: short silence unless the partial ends on a connector or filler."""

    def rule(item: Item, t_ms: float, silence_ms: float) -> tuple[bool, float]:
        text = latest(item.partials, t_ms)
        need = long_ms if ends_with_connector(text) else short_ms
        return silence_ms + 1e-6 >= need, 0.0

    return rule


class Judge:
    """A text completeness judge with a per-text cache, like ``Endpointer``."""

    name = "judge"

    def complete(self, text: str) -> tuple[bool, float]:
        raise NotImplementedError


class StopJudge(Judge):
    """Doubao's own punctuation: the partial already ends on 。？！."""

    name = "豆包标点"

    def complete(self, text: str) -> tuple[bool, float]:
        return tail(text).endswith(STOPS), 0.0


class FinalJudge(Judge):
    """Stop or sentence-final particle (吗呢吧了啊……)."""

    name = "句末语气词"

    def complete(self, text: str) -> tuple[bool, float]:
        return ends_with_final(text), 0.0


class ScoreJudge(Judge):
    """Wraps a model that returns a probability; scores are cached per text."""

    def __init__(self, name: str, score: Callable[[str], float], threshold: float) -> None:
        self.name = name
        self._score = score
        self.threshold = threshold
        self.cache: dict[str, tuple[float, float]] = {}

    def probability(self, text: str) -> tuple[float, float]:
        if text not in self.cache:
            started = time.perf_counter()
            value = self._score(text)
            self.cache[text] = (value, (time.perf_counter() - started) * 1000.0)
        return self.cache[text]

    def with_threshold(self, threshold: float) -> ScoreJudge:
        twin = ScoreJudge(self.name, self._score, threshold)
        twin.cache = self.cache
        return twin

    def complete(self, text: str) -> tuple[bool, float]:
        value, cost = self.probability(text)
        return value >= self.threshold, cost


def judged(consult_ms: float, cap_ms: float, judge: Judge, *, veto_connectors: bool = False) -> Rule:
    """S2 (and S4 with ``veto_connectors``): judge the text once silence reaches ``consult_ms``.

    A new partial is judged once; the endpoint is the frame plus the judge's
    time. With no yes, the endpoint is ``cap_ms`` of silence. S4 never asks the
    judge when the partial ends on a connector.
    """

    state: dict[str, Any] = {"text": None, "yes": False}

    def rule(item: Item, t_ms: float, silence_ms: float) -> tuple[bool, float]:
        if silence_ms + 1e-6 < consult_ms:
            state["text"] = None
            return False, 0.0
        if silence_ms + 1e-6 >= cap_ms:
            return True, 0.0
        text = latest(item.partials, t_ms)
        if veto_connectors and ends_with_connector(text):
            return False, 0.0
        if state["text"] == text:
            return bool(state["yes"]), 0.0
        yes, cost = judge.complete(text) if tail(text) else (False, 0.0)
        state["text"] = text
        state["yes"] = yes
        return yes, cost

    return rule


def server_or_local(window: str, need_ms: float, mode: str) -> Callable[[Item], Outcome]:
    """S3: Doubao's ``definite`` for one ``end_window`` and local fixed silence.

    ``first`` takes whichever comes first, ``last`` waits for both. A pass with
    no server endpoint falls back to the local one.
    """

    def run(item: Item) -> Outcome:
        local = walk(item, fixed(need_ms))
        server = item.server.get(window)
        if server is None:
            return Outcome(local.endpoint_ms, "local-only", local.text)
        pick = min(local.endpoint_ms, server) if mode == "first" else max(local.endpoint_ms, server)
        reason = "server" if pick == server else "local"
        return Outcome(pick, reason, latest(item.partials, pick))

    return run


def score(items: Sequence[Item], outcomes: Sequence[Outcome]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item, outcome in zip(items, outcomes, strict=True):
        barge, where = classify_human_endpoint(
            speech_end_ms=item.speech_end_ms, pauses=item.pauses, t_endpoint_ms=outcome.endpoint_ms
        )
        rows.append(
            {
                "id": item.id,
                "kind": item.kind,
                "endpoint_ms": round(outcome.endpoint_ms, 1),
                "lag_ms": round(outcome.endpoint_ms - item.speech_end_ms, 1),
                "false_barge_in": barge,
                "where": where,
                "reason": outcome.reason,
                "text": outcome.text,
                "judge_ms": [round(value, 2) for value in outcome.judge_ms],
            }
        )
    return {
        "all": _summary(rows),
        **{kind: _summary([r for r in rows if r["kind"] == kind]) for kind in KINDS.values()},
        "rows": rows,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    barge = [row for row in rows if row["false_barge_in"] is True]
    clean = [row for row in rows if row["false_barge_in"] is False]
    lags = [float(row["lag_ms"]) for row in clean]
    calls = [float(v) for row in rows for v in row["judge_ms"]]
    return {
        "n": len(rows),
        "false_barge_in": len(barge),
        "false_barge_in_rate": len(barge) / len(rows) if rows else None,
        "labeled_pause": sum(1 for row in barge if row["where"] == "labeled-pause"),
        "short_gap": sum(1 for row in barge if row["where"] == "short-gap"),
        "lag_ms": {"p50": percentile(lags, 50), "p95": percentile(lags, 95), "n": len(lags)},
        "judge_ms": {"p50": percentile(calls, 50), "p95": percentile(calls, 95), "n": len(calls)},
    }


def run_rule(items: Sequence[Item], make: Callable[[], Rule]) -> list[Outcome]:
    return [walk(item, make()) for item in items]


def check_fixed(items: Sequence[Item], r3b: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare the rebuilt fixed-silence curve with R3b's numbers for the same thresholds."""
    found: list[dict[str, Any]] = []
    curves = r3b["curves"]
    assert isinstance(curves, dict)
    for point in curves["固定静音"]:
        need = float(point["threshold_ms"])
        mine = score(items, run_rule(items, partial(fixed, need)))["all"]
        assert isinstance(mine, dict)
        found.append(
            {
                "threshold_ms": need,
                "r3b_false_barge_in": point["false_barge_in"],
                "replay_false_barge_in": mine["false_barge_in"],
                "r3b_lag_p50": point["lag_ms"]["p50"],
                "replay_lag_p50": mine["lag_ms"]["p50"],
                "r3b_lag_p95": point["lag_ms"]["p95"],
                "replay_lag_p95": mine["lag_ms"]["p95"],
            }
        )
    return found


def _point(label: str, params: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": label,
        "params": params,
        **{k: v for k, v in scored.items() if k != "rows"},
        "rows": scored["rows"],
    }


def best(points: Sequence[dict[str, Any]], limit: int) -> dict[str, Any] | None:
    """Lowest lag P50 among points with at most ``limit`` false barge-ins; then P95, then fewer barge-ins."""
    ok = [p for p in points if p["all"]["false_barge_in"] <= limit and p["all"]["lag_ms"]["p50"] is not None]
    if not ok:
        return None
    return min(ok, key=lambda p: (p["all"]["lag_ms"]["p50"], p["all"]["lag_ms"]["p95"], p["all"]["false_barge_in"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotations", type=Path, default=RESULTS / "turns-human-annotations.json")
    parser.add_argument("--volc", type=Path, default=RESULTS / "turns-human-volc.json")
    parser.add_argument("--r3b", type=Path, default=RESULTS / "turns-human.json")
    parser.add_argument("--out", type=Path, default=RESULTS / "turns-combo.json")
    parser.add_argument("--no-models", action="store_true", help="skip the LiveKit and punctuation judges")
    args = parser.parse_args()

    items = load(args.annotations, args.volc)
    r3b = json.loads(args.r3b.read_text(encoding="utf-8"))
    check = check_fixed(items, r3b)

    families: dict[str, list[dict[str, Any]]] = {}

    def add(family: str, label: str, params: dict[str, Any], outcomes: list[Outcome]) -> None:
        families.setdefault(family, []).append(_point(label, params, score(items, outcomes)))

    for need in (500, 800, 1000, 1100, 1200, 1300, 1400, 1500, 1800, 2000):
        add("基线：固定静音", f"固定 {need}", {"silence_ms": need}, run_rule(items, partial(fixed, float(need))))

    for short in (300, 400, 500, 600, 800, 1000, 1100, 1200, 1300, 1400, 1500):
        for long in (1000, 1200, 1500, 1800, 2000, 2500, 3000):
            if long <= short:
                continue
            add(
                "S1 自适应静音",
                f"短 {short} / 长 {long}",
                {"short_ms": short, "long_ms": long},
                run_rule(items, partial(adaptive, float(short), float(long))),
            )

    judges: list[Judge] = [StopJudge(), FinalJudge()]
    if not args.no_models:
        judges.extend(_model_judges())
    consults = (200, 300, 400, 500, 600, 800, 1000, 1200)
    caps = (1000, 1200, 1500, 2000, 2500)
    for judge in judges:
        for consult in consults:
            for cap in caps:
                params = {"judge": judge.name, "consult_ms": consult, "cap_ms": cap}
                if isinstance(judge, ScoreJudge):
                    params["threshold"] = judge.threshold
                add(
                    "S2 静音 + 文本判断",
                    f"{judge.name} {consult}/{cap}",
                    params,
                    run_rule(items, partial(judged, float(consult), float(cap), judge)),
                )
                add(
                    "S4 连接词否决 + 文本判断",
                    f"{judge.name} {consult}/{cap}",
                    params,
                    run_rule(items, partial(judged, float(consult), float(cap), judge, veto_connectors=True)),
                )

    for window in ("300", "400", "500", "800", "1000", "1200", "1600", "2000", "semantic"):
        for need in (500, 800, 1000, 1200, 1300, 1400, 1500, 2000):
            for mode in ("first", "last"):
                run = server_or_local(window, need, mode)
                add(
                    "S3 豆包断句 + 本地静音",
                    f"豆包 {window} {'先到' if mode == 'first' else '都到'} 本地 {need}",
                    {"end_window": window, "silence_ms": need, "mode": mode},
                    [run(item) for item in items],
                )

    picks: dict[str, dict[str, dict[str, Any] | None]] = {}
    for limit, name in ((5, "≤10%"), (2, "≤5%")):
        picks[name] = {family: best(points, limit) for family, points in families.items()}

    out = {
        "kind": "human-turn-combo",
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.processor() or platform.machine(),
        "frame_ms": FRAME_MS,
        "pad_ms": PAD_MS,
        "connectors": list(CONNECTORS),
        "finals": list(FINALS),
        "replay_check": check,
        "notes": ["每类方案只有权衡前沿上的点保留逐条录音的 rows，其余只留汇总。"],
        "families": {family: _trim(points) for family, points in families.items()},
        "picks": {
            name: {family: (None if pick is None else pick["label"]) for family, pick in by.items()}
            for name, by in picks.items()
        },
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    args.out.with_suffix(".md").write_text(markdown(out), encoding="utf-8")
    print(f"wrote {args.out} and {args.out.with_suffix('.md').name}")
    for row in check:
        print(row)
    for limit_name, by in picks.items():
        print(limit_name)
        for family, pick in by.items():
            if pick is None:
                print(f"  {family}: 无")
                continue
            a = pick["all"]
            lag = a["lag_ms"]
            print(
                f"  {family}: {pick['label']}  误抢话 {a['false_barge_in']}/50  滞后 P50 {lag['p50']} P95 {lag['p95']}"
            )


def _trim(points: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep per-recording rows only on frontier points, so the committed JSON stays small."""
    keep = {id(point) for point in frontier(points)}
    return [point if id(point) in keep else {k: v for k, v in point.items() if k != "rows"} for point in points]


def _ms(value: object) -> str:
    return "—" if not isinstance(value, (int, float)) else f"{value:.0f}ms"


def frontier(points: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Points no other point beats on both false barge-ins and lag P50."""
    ordered = sorted(
        (p for p in points if p["all"]["lag_ms"]["p50"] is not None),
        key=lambda p: (p["all"]["false_barge_in"], p["all"]["lag_ms"]["p50"]),
    )
    kept: list[dict[str, Any]] = []
    best = float("inf")
    for point in ordered:
        lag = float(point["all"]["lag_ms"]["p50"])
        if lag < best:
            kept.append(point)
            best = lag
    return kept


def markdown(out: dict[str, Any]) -> str:
    """Every family's frontier, with the three kinds of recording split out."""
    lines = [
        "# R3c 组合策略：每类方案的权衡前沿",
        "",
        f"生成时间 {out['generated_at']}，Python {out['python']}，{out['platform']}。",
        "",
        "只列「误抢话更少或者滞后更低」的点（前沿），全部点在 turns-combo.json。",
        "",
    ]
    families = out["families"]
    assert isinstance(families, dict)
    for family, points in families.items():
        lines += [
            f"## {family}",
            "",
            "| 方案 | 误抢话 | 滞后 P50 | P95 | 照着说 | 自由说 | 短句 | 判断器推理 P50 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for point in frontier(points):
            a = point["all"]
            assert isinstance(a, dict)
            cells = [
                str(point["label"]),
                f"{a['false_barge_in']}/{a['n']}",
                _ms(a["lag_ms"]["p50"]),
                _ms(a["lag_ms"]["p95"]),
            ]
            for kind in KINDS.values():
                k = point[kind]
                assert isinstance(k, dict)
                cells.append(f"{k['false_barge_in']}/{k['n']}")
            cells.append(_ms(a["judge_ms"]["p50"]))
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


def _model_judges() -> list[Judge]:
    judges: list[Judge] = []
    models = HERE / "models"
    livekit_dir = models / "livekit-turn"
    if (livekit_dir / "model_q8.onnx").is_file():
        from latency.turn_models import LiveKitTurn

        livekit = LiveKitTurn(livekit_dir)
        base = ScoreJudge("LiveKit", lambda text: float(livekit.probability(b"", text)), 0.0066)
        for threshold in (0.0066, 0.02, 0.05, 0.1, 0.2, 0.5):
            twin = base.with_threshold(threshold)
            twin.name = f"LiveKit≥{threshold:g}"
            judges.append(twin)
    punct = models / "punct" / "model.onnx"
    if punct.is_file():
        from latency.endpoint import PunctuationCompleteness

        model = PunctuationCompleteness(str(punct))
        judges.append(ScoreJudge("标点模型", lambda text: 1.0 if model.is_complete(text) else 0.0, 0.5))
    return judges


if __name__ == "__main__":
    main()
