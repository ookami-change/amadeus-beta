"""R6b: replay early-start launch rules on the human recordings, offline.

R6 launched the fast brain at 150 ms of silence on whatever partial the
client held. Most of its restarts came from that partial missing the last
words. This replays launch rules on the same timeline R3c uses (VAD gaps plus
the Doubao partials with their arrival clocks) and counts, per rule:

* launches, and cancels because the speaker went on talking;
* restarts at the commit point, when the launched text is not the same turn as
  the text held at commit (R6's ``texts_continue``);
* restarts after the stream closes, against the final transcript;
* the launch lag of the attempt that is finally kept, relative to the true
  speech end.

The commit point is R3c's adaptive 1400/1800. Nothing here calls a vendor.
The live run (``latency.early_live``) checks the rules that win.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from latency import turn_combo as tc
from latency.human_labels import classify_human_endpoint
from latency.overlap import texts_continue
from latency.scoring import percentile

FIRST_CLAUSE_MS = 560.0
"""R6: first token about 500 ms plus the first clause about 60 ms after launch."""
FIRST_AUDIO_MS = 690.0
"""R6 fast pile: LLM first token about 500 ms plus MiniMax first audio about 190 ms."""
RESTART_GAP_MS = 600.0
"""R6 waited this long before reopening MiniMax after a cancel."""


@dataclass(frozen=True)
class LaunchRule:
    """Launch once ``silence_ms`` of silence has passed and the partial is fresh enough.

    ``fresh_ms`` is how far past the start of the silence the held partial must
    have arrived; ``None`` accepts any non-empty partial (R6). ``quiet_ms``
    additionally asks that the partial has not changed for that long.
    """

    silence_ms: float
    fresh_ms: float | None = None
    quiet_ms: float = 0.0

    @property
    def label(self) -> str:
        parts = [f"静音 {int(self.silence_ms)}"]
        if self.fresh_ms is not None:
            parts.append(f"新于静音起点 {int(self.fresh_ms)}")
        if self.quiet_ms:
            parts.append(f"文字稳定 {int(self.quiet_ms)}")
        return "，".join(parts)


@dataclass(frozen=True)
class Launch:
    audio_ms: float
    text: str
    fate: str
    """kept, cancelled (speaker went on), restart-commit or restart-final."""
    retired_ms: float | None = None
    """When it was dropped; for a cancel, the frame the speaker resumed."""

    def consumed_socket(self) -> bool:
        """A dropped attempt only spent a synthesizer socket if its first clause went out."""
        return self.retired_ms is not None and self.retired_ms - self.audio_ms >= FIRST_CLAUSE_MS

    def heard(self) -> bool:
        """With play-when-ready, a cancelled attempt was audible if its audio beat the speaker back."""
        return (
            self.fate == "cancelled"
            and self.retired_ms is not None
            and self.retired_ms - self.audio_ms > FIRST_AUDIO_MS
        )


@dataclass
class Plan:
    item_id: str
    kind: str
    speech_end_ms: float
    commit_ms: float
    false_barge_in: bool
    launches: list[Launch]
    final_text: str

    @property
    def kept(self) -> Launch:
        return self.launches[-1]

    @property
    def cancelled(self) -> int:
        return sum(1 for one in self.launches if one.fate == "cancelled")

    @property
    def restarts(self) -> int:
        return sum(1 for one in self.launches if one.fate.startswith("restart"))

    def sockets(self, warm: bool) -> int:
        """Synthesizer sockets opened: one per attempt (R6), or one warm socket reused until it speaks."""
        if not warm:
            return len(self.launches)
        return 1 + sum(1 for one in self.launches[:-1] if one.consumed_socket())

    def outcome(self, play: str) -> tuple[bool, float]:
        """``(false_barge_in, first audio heard - speech end)`` under a playback policy.

        ``early`` plays as soon as audio exists (R6). ``hold`` prepares the
        reply but starts playback no earlier than the commit point.
        """
        ready = self.kept.audio_ms + FIRST_AUDIO_MS
        if play == "early":
            barge = self.false_barge_in or any(one.heard() for one in self.launches)
            return barge, ready - self.speech_end_ms
        return self.false_barge_in, max(ready, self.commit_ms) - self.speech_end_ms


def _held(item: tc.Item, t_ms: float) -> tc.Partial | None:
    held: tc.Partial | None = None
    for heard in item.partials:
        if heard.audio_ms <= t_ms + 1.0:
            held = heard
        else:
            break
    return held


def _since_change(item: tc.Item, t_ms: float) -> float:
    """How long the held text has stayed the same, by arrival clock."""
    held = _held(item, t_ms)
    if held is None:
        return 0.0
    changed = held.audio_ms
    for heard in item.partials:
        if heard.audio_ms > t_ms + 1.0:
            break
        if heard.text != held.text:
            changed = heard.audio_ms
    later = [p.audio_ms for p in item.partials if p.audio_ms <= t_ms + 1.0 and p.text == held.text]
    first_same = min((t for t in later if t >= changed), default=held.audio_ms)
    return t_ms - first_same


def ready(item: tc.Item, rule: LaunchRule, t_ms: float, silence_start: float, silence_ms: float) -> bool:
    if silence_ms + 1e-6 < rule.silence_ms:
        return False
    held = _held(item, t_ms)
    if held is None or not held.text.strip():
        return False
    if rule.fresh_ms is not None and held.audio_ms < silence_start + rule.fresh_ms:
        return False
    return not (rule.quiet_ms and _since_change(item, t_ms) + 1e-6 < rule.quiet_ms)


def plan(item: tc.Item, rule: LaunchRule | None, commit: tc.Outcome) -> Plan:
    """Walk the silences up to the commit point, then settle against commit and final text.

    ``rule=None`` is the serial cascade: one launch at the commit point.
    """
    launches: list[Launch] = []
    active: tuple[float, str] | None = None
    for gap in item.gaps if rule is not None else ():
        start = tc._frame_start(gap.start_ms)
        if start >= commit.endpoint_ms:
            break
        t = start + tc.FRAME_MS
        end = min(gap.end_ms, item.total_ms, commit.endpoint_ms)
        while t <= end + 1e-6:
            if active is None and rule is not None and ready(item, rule, t, start, t - start):
                active = (t, tc.latest(item.partials, t))
            t += tc.FRAME_MS
        if active is not None and gap.end_ms < commit.endpoint_ms - 1e-6:
            launches.append(Launch(active[0], active[1], "cancelled", gap.end_ms))
            active = None
    final_text = tc.latest(item.partials, item.total_ms)
    if active is None or not texts_continue(active[1], commit.text):
        if active is not None:
            launches.append(Launch(active[0], active[1], "restart-commit", commit.endpoint_ms))
        active = (commit.endpoint_ms + (RESTART_GAP_MS if launches else 0.0), commit.text)
    if final_text and not texts_continue(active[1], final_text):
        launches.append(Launch(active[0], active[1], "restart-final", item.total_ms))
        active = (item.total_ms + RESTART_GAP_MS, final_text)
    launches.append(Launch(active[0], active[1], "kept"))
    barge, _ = classify_human_endpoint(
        speech_end_ms=item.speech_end_ms, pauses=item.pauses, t_endpoint_ms=commit.endpoint_ms
    )
    return Plan(
        item_id=item.id,
        kind=item.kind,
        speech_end_ms=item.speech_end_ms,
        commit_ms=commit.endpoint_ms,
        false_barge_in=barge,
        launches=launches,
        final_text=final_text,
    )


def summarize(plans: Sequence[Plan], play: str) -> dict[str, Any]:
    outcomes = [p.outcome(play) for p in plans]
    e2e = [lag for barge, lag in outcomes if not barge]
    calls = sum(len(p.launches) for p in plans)
    return {
        "n": len(plans),
        "false_barge_in": sum(1 for barge, _ in outcomes if barge),
        "e2e_ms": {"p50": percentile(e2e, 50), "p95": percentile(e2e, 95), "n": len(e2e)},
        "llm_calls": calls,
        "cancelled": sum(p.cancelled for p in plans),
        "restarts": sum(p.restarts for p in plans),
        "sockets_per_attempt": sum(p.sockets(warm=False) for p in plans),
        "sockets_warm": sum(p.sockets(warm=True) for p in plans),
    }


def launch_rules() -> list[LaunchRule | None]:
    out: list[LaunchRule | None] = [None]
    out += [LaunchRule(ms) for ms in (150, 200, 300, 400, 500, 600, 800)]
    out += [LaunchRule(ms, fresh_ms=0.0) for ms in (150, 300)]
    out += [LaunchRule(ms, quiet_ms=quiet) for ms in (150, 300) for quiet in (200.0, 400.0)]
    return out


def commit_rules(with_models: bool) -> list[tuple[str, tc.Rule]]:
    out: list[tuple[str, tc.Rule]] = [
        ("固定 2000（R6）", tc.fixed(2000.0)),
        ("S1 短 1400 / 长 1800", tc.adaptive(1400.0, 1800.0)),
        ("S1 短 1200 / 长 1500", tc.adaptive(1200.0, 1500.0)),
    ]
    if with_models:
        for judge in tc._model_judges():
            if judge.name == "LiveKit≥0.1":
                for consult, cap in ((1000.0, 2500.0), (1200.0, 2500.0)):
                    label = f"S4 LiveKit≥0.1 {int(consult)}/{int(cap)}"
                    out.append((label, tc.judged(consult, cap, judge, veto_connectors=True)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotations", type=Path, default=tc.RESULTS / "turns-human-annotations.json")
    parser.add_argument("--volc", type=Path, default=tc.RESULTS / "turns-human-volc.json")
    parser.add_argument("--out", type=Path, default=tc.RESULTS / "early-plan.json")
    parser.add_argument("--no-models", action="store_true", help="skip the LiveKit commit rules")
    args = parser.parse_args()
    items = tc.load(args.annotations, args.volc)
    rows: list[dict[str, Any]] = []
    for commit_label, commit_rule in commit_rules(not args.no_models):
        commits = {item.id: tc.walk(item, commit_rule) for item in items}
        for rule in launch_rules():
            plans = [plan(item, rule, commits[item.id]) for item in items]
            label = rule.label if rule is not None else "串行（断句后才起跑）"
            for play in ("early", "hold") if rule is not None else ("hold",):
                rows.append(
                    {
                        "commit": commit_label,
                        "launch": label,
                        "play": play,
                        "summary": summarize(plans, play),
                        "items": [
                            {
                                "id": p.item_id,
                                "commit_ms": p.commit_ms,
                                "outcome": p.outcome(play),
                                "launches": [
                                    {"audio_ms": one.audio_ms, "text": one.text, "fate": one.fate} for one in p.launches
                                ],
                            }
                            for p in plans
                        ],
                    }
                )
    payload = {
        "kind": "early-plan",
        "generated_at": datetime.now(UTC).isoformat(),
        "first_clause_ms": FIRST_CLAUSE_MS,
        "first_audio_ms": FIRST_AUDIO_MS,
        "restart_gap_ms": RESTART_GAP_MS,
        "serial_extra_ms": "串行行的端到端只含断句和首音频之间的模型时间，不含等识别定稿",
        "rows": rows,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    for row in rows:
        s = row["summary"]
        print(
            f"{row['commit']:<22} {row['launch']:<16} {row['play']:<5} "
            f"barge {s['false_barge_in']:>2}  e2e {s['e2e_ms']['p50']:>5.0f}/{s['e2e_ms']['p95']:>5.0f}  "
            f"calls {s['llm_calls']:>3} cancel {s['cancelled']:>2} restart {s['restarts']:>2}  "
            f"sockets {s['sockets_per_attempt']:>3}->{s['sockets_warm']:>3}"
        )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
