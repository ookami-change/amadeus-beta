"""R6b: early start on the human recordings, live.

R6 measured early start on 20 synthetic sentences that never pause, so the
speaker never went on after it had launched. ``early_plan`` replays the human
recordings offline and shows that playing a speculative reply as soon as its
audio exists cuts in on a mid-sentence pause about as often as a 1-second
endpoint would. This runner checks that live and measures the fix:

* **Commit rule.** Silence timing comes from the annotated VAD gaps, the same
  timeline R3c replays, so live and offline differ only in vendor timing. The
  text the rule reads is the live Doubao partial.
* **Launch.** At ``launch_ms`` of silence the fast brain starts on the held
  partial. Speech resuming cancels it. At the commit point the attempt is kept
  if the commit text is the same turn (R6's ``texts_continue``), otherwise it
  restarts; the same check runs once more against the final transcript.
* **Playback.** ``early`` plays when audio exists (R6). ``hold`` keeps the
  prepared audio until the commit point.
* **Synthesizer sockets.** ``warm`` opens one MiniMax socket before the turn and
  hands it to whichever attempt speaks first; an attempt cancelled before its
  first clause gives the socket back unused. Every open goes through a
  client-side RPM gate with backoff.

Every attempt is logged with its launch clock, text, fate and timings, so the
per-sample breakdown in the report comes straight from the JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from latency import turn_combo as tc
from latency.adapters.types import (
    ASRAdapter,
    ASRUpdate,
    AudioChunk,
    LLMAdapter,
    LLMDelta,
    TTSAdapter,
    redact,
    retryable_error,
)
from latency.clauses import ClauseBuffer
from latency.human_labels import classify_human_endpoint
from latency.overlap import HISTORY_PATH, PERSONA_PATH, texts_continue
from latency.pcm import frames, read_wav, silence
from latency.ratelimit import RateGate, retry
from latency.scoring import percentile

HERE = Path(__file__).resolve().parent
FRAME_MS = tc.FRAME_MS
ASR_PACKET_MS = 200.0
INSTRUCTION = "年轻女声，安静，温和，吐字清楚，略带书卷气。情绪幅度小，像在小声交谈。"


@dataclass(frozen=True)
class Arm:
    name: str
    commit: str
    """``fixed2000``, ``s1`` (adaptive 1400/1800) or ``s4`` (LiveKit ≥0.1, 1000/2500, connector veto)."""
    launch_ms: float | None
    """Silence before a speculative launch; ``None`` launches only at the commit point."""
    play: str
    warm: bool
    smart: bool = False
    """R6: also ask Smart Turn (> 0.5) before launching."""
    replace_wait_s: float = 0.0
    """R6 waited 0.6 s for the old MiniMax socket to close before opening the next."""


ARMS: dict[str, Arm] = {
    "serial": Arm("serial", "s1", None, "hold", warm=True),
    "r6": Arm("r6", "fixed2000", 150.0, "early", warm=False, smart=True, replace_wait_s=0.6),
    "s1": Arm("s1", "s1", 400.0, "hold", warm=True),
    "s4": Arm("s4", "s4", 400.0, "hold", warm=True),
}


def commit_rule(key: str, livekit: tc.Judge | None) -> tc.Rule:
    if key == "fixed2000":
        return tc.fixed(2000.0)
    if key == "s1":
        return tc.adaptive(1400.0, 1800.0)
    if key == "s4":
        if livekit is None:
            raise ValueError("s4 needs the LiveKit model")
        return tc.judged(1000.0, 2500.0, livekit, veto_connectors=True)
    raise ValueError(key)


def silence_at(item: tc.Item, t_ms: float) -> float | None:
    """Silence so far at frame end ``t_ms``, on the same grid as ``turn_combo.walk``; ``None`` in speech."""
    for gap in item.gaps:
        start = tc._frame_start(gap.start_ms)
        if start + FRAME_MS - 1e-6 <= t_ms <= min(gap.end_ms, item.total_ms) + 1e-6:
            return t_ms - start
    return None


@dataclass
class Attempt:
    text: str
    reason: str
    """speculate, commit or final."""
    audio_ms: float
    launch_ms: float
    fate: str = "kept"
    retired_ms: float | None = None
    socket: str | None = None
    gate_wait_ms: float = 0.0
    first_token_ms: float | None = None
    first_clause_ms: float | None = None
    first_audio_ms: float | None = None
    spoke: bool = False
    error: str | None = None
    reply: list[str] = field(default_factory=list)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    tts: TTSAdapter | None = field(default=None, repr=False)

    def as_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "reason": self.reason,
            "audio_ms": self.audio_ms,
            "launch_ms": round(self.launch_ms, 1),
            "fate": self.fate,
            "retired_ms": None if self.retired_ms is None else round(self.retired_ms, 1),
            "socket": self.socket,
            "gate_wait_ms": round(self.gate_wait_ms, 1),
            "first_token_ms": _r(self.first_token_ms),
            "first_clause_ms": _r(self.first_clause_ms),
            "first_audio_ms": _r(self.first_audio_ms),
            "spoke": self.spoke,
            "error": self.error,
            "reply": "".join(self.reply),
        }


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


class Voices:
    """Synthesizer sockets for one turn, all opened through the shared gate."""

    def __init__(self, make_tts: Callable[[], TTSAdapter], gate: RateGate, *, warm: bool) -> None:
        self._make = make_tts
        self._gate = gate
        self.warm = warm
        self._spare: TTSAdapter | None = None
        self._pending: asyncio.Task[tuple[TTSAdapter, float]] | None = None
        self._live: list[TTSAdapter] = []
        self.opened = 0
        self.retries: list[str] = []

    async def _open(self) -> tuple[TTSAdapter, float]:
        waited = 0.0

        async def once() -> TTSAdapter:
            nonlocal waited
            waited += await self._gate.acquire()
            voice = self._make()
            try:
                await voice.open(instruction=INSTRUCTION)
            except BaseException:
                await _quiet_close(voice)
                raise
            return voice

        voice = await retry(once, on_retry=lambda n, error: self.retries.append(error))
        self.opened += 1
        self._live.append(voice)
        return voice, waited * 1000.0

    async def warm_up(self) -> None:
        if self.warm and self._spare is None:
            self._spare, _ = await self._open()

    async def take(self) -> tuple[TTSAdapter, str, float]:
        if self._spare is not None:
            voice, self._spare = self._spare, None
            return voice, "warm", 0.0
        if self._pending is not None:
            pending, self._pending = self._pending, None
            voice, waited = await pending
            return voice, "prefetched", waited
        voice, waited = await self._open()
        return voice, "new", waited

    async def give_back(self, voice: TTSAdapter | None, *, spoke: bool) -> None:
        if voice is None:
            return
        if self.warm and not spoke and self._spare is None:
            self._spare = voice
            return
        await _quiet_close(voice)
        if self.warm and self._spare is None and self._pending is None:
            self._pending = asyncio.create_task(self._open())

    async def close_all(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            try:
                voice, _ = await self._pending
                self._live.append(voice)
            except BaseException:
                pass
            self._pending = None
        for voice in self._live:
            await _quiet_close(voice)
        self._live.clear()
        self._spare = None


async def _quiet_close(voice: TTSAdapter) -> None:
    try:
        await voice.close()
    except Exception:
        pass


@dataclass
class Turn:
    """Everything one trial needs besides the vendors."""

    item: tc.Item
    pcm: bytes
    arm: Arm
    rule: tc.Rule
    system: str
    history: list[dict[str, str]]
    smart: Callable[[bytes, str], bool] | None = None


async def run_turn(
    turn: Turn,
    *,
    asr: ASRAdapter,
    llm: LLMAdapter,
    voices: Voices,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """One live trial. Times are milliseconds from the first audio frame."""
    item = replace(turn.item, partials=[])
    arm = turn.arm
    await voices.warm_up()
    origin = clock()

    def wall_ms() -> float:
        return (clock() - origin) * 1000.0

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    final = ""
    asr_error: str | None = None

    async def audio_iter() -> AsyncIterator[bytes]:
        while (chunk := await audio_queue.get()) is not None:
            yield chunk

    async def consume_asr() -> None:
        nonlocal final, asr_error
        try:
            async for update in asr.transcribe(audio_iter()):
                if isinstance(update, ASRUpdate) and update.text:
                    item.partials.append(tc.Partial(wall_ms(), update.text))
                    if update.is_final:
                        final = update.text
        except Exception as exc:
            asr_error = redact(str(exc))

    async def drive(att: Attempt, wait_s: float) -> None:
        if wait_s:
            await asyncio.sleep(wait_s)
        voice, how, waited = await voices.take()
        att.tts, att.socket, att.gate_wait_ms = voice, how, waited
        clause_queue: asyncio.Queue[str | None] = asyncio.Queue()
        tts_task: asyncio.Task[None] | None = None

        async def clauses() -> AsyncIterator[str]:
            while (piece := await clause_queue.get()) is not None:
                yield piece

        async def consume_tts() -> None:
            try:
                async for out in voice.speak(clauses()):
                    if isinstance(out, AudioChunk) and out.pcm and att.first_audio_ms is None:
                        att.first_audio_ms = wall_ms()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                att.error = redact(str(exc))

        async def say(clause: str) -> None:
            nonlocal tts_task
            if tts_task is None:
                att.spoke = True
                att.first_clause_ms = wall_ms()
                tts_task = asyncio.create_task(consume_tts())
            await clause_queue.put(clause)

        finished = False
        try:
            buffer = ClauseBuffer()
            async for piece in llm.stream(system=turn.system, history=turn.history, user=att.text):
                if isinstance(piece, LLMDelta) and piece.text:
                    if att.first_token_ms is None:
                        att.first_token_ms = wall_ms()
                    att.reply.append(piece.text)
                    for clause in buffer.push(piece.text):
                        await say(clause)
            tail = buffer.flush()
            if tail:
                await say(tail)
            finished = True
        except asyncio.CancelledError:
            if tts_task is not None:
                tts_task.cancel()
            raise
        except Exception as exc:
            att.error = redact(str(exc))
            finished = True
        finally:
            if finished and tts_task is not None:
                await clause_queue.put(None)
                try:
                    await asyncio.wait_for(tts_task, timeout=30.0)
                except TimeoutError:
                    tts_task.cancel()
                    att.error = att.error or "TTS timed out"

    attempts: list[Attempt] = []
    active: Attempt | None = None

    def launch(text: str, reason: str, audio_ms: float, *, replacing: bool = False) -> Attempt:
        """``replacing``: R6 waited before reopening MiniMax after a restart, not after a cancel."""
        att = Attempt(text=text, reason=reason, audio_ms=audio_ms, launch_ms=wall_ms())
        att.task = asyncio.create_task(drive(att, arm.replace_wait_s if replacing else 0.0))
        attempts.append(att)
        return att

    async def retire(att: Attempt, fate: str) -> None:
        att.fate = fate
        att.retired_ms = wall_ms()
        task = att.task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except BaseException:
                pass
        await voices.give_back(att.tts, spoke=att.spoke)
        att.tts = None

    asr_task = asyncio.create_task(consume_asr())
    commit_audio: float | None = None
    commit_wall: float | None = None
    commit_text = ""
    speculated = False
    smart_check: asyncio.Task[bool] | None = None
    heard = bytearray()
    packet = bytearray()
    packet_ms = 0.0
    try:
        for frame in frames(turn.pcm, FRAME_MS):
            delay = frame.end_ms / 1000.0 - (clock() - origin)
            if delay > 0:
                await asyncio.sleep(delay)
            heard.extend(frame.pcm)
            packet.extend(frame.pcm)
            packet_ms += FRAME_MS
            if packet_ms >= ASR_PACKET_MS:
                await audio_queue.put(bytes(packet))
                packet.clear()
                packet_ms = 0.0
            if commit_audio is not None:
                continue
            t = frame.end_ms
            quiet = silence_at(item, t)
            if quiet is None:
                speculated = False
                smart_check = None
                if active is not None:
                    await retire(active, "cancelled")
                    active = None
                continue
            fire, cost = turn.rule(item, t, quiet)
            text = tc.latest(item.partials, t)
            if fire:
                commit_audio, commit_wall, commit_text = t + cost, wall_ms(), text
                replacing = active is not None and not texts_continue(active.text, text)
                if active is not None and replacing:
                    await retire(active, "restart-commit")
                    active = None
                if active is None and text.strip():
                    active = launch(text, "commit", t, replacing=replacing)
                continue
            if (
                arm.launch_ms is not None
                and active is None
                and not speculated
                and text.strip()
                and quiet + 1e-6 >= arm.launch_ms
            ):
                if not arm.smart or turn.smart is None:
                    speculated = True
                    active = launch(text, "speculate", t)
                elif smart_check is None:
                    # Smart Turn takes ~100 ms of CPU; off the loop so audio keeps flowing.
                    smart_check = asyncio.create_task(asyncio.to_thread(turn.smart, bytes(heard), text))
                elif smart_check.done():
                    yes = not smart_check.cancelled() and smart_check.exception() is None and smart_check.result()
                    smart_check = None
                    if yes:
                        speculated = True
                        active = launch(text, "speculate", t)
        if packet:
            await audio_queue.put(bytes(packet))
        await audio_queue.put(None)
        try:
            await asyncio.wait_for(asr_task, timeout=5.0)
        except TimeoutError:
            asr_task.cancel()
        transcript = final or tc.latest(item.partials, float("inf"))
        if asr_error is None and transcript.strip():
            replacing = active is not None and not texts_continue(active.text, transcript)
            if active is not None and replacing:
                await retire(active, "restart-final")
                active = None
            if active is None:
                active = launch(transcript, "final", item.total_ms, replacing=replacing)
        if active is not None and active.task is not None:
            await active.task
    finally:
        await audio_queue.put(None)
        if not asr_task.done():
            asr_task.cancel()
        for att in attempts:
            if att.task is not None and not att.task.done():
                att.task.cancel()
        await voices.close_all()

    kept = active
    error = asr_error or (kept.error if kept is not None else "nothing launched")
    if kept is not None and kept.first_audio_ms is None and error is None:
        error = "TTS produced no audio"
    return _row(turn, attempts, kept, error, commit_audio, commit_wall, commit_text, final, voices)


def _row(
    turn: Turn,
    attempts: list[Attempt],
    kept: Attempt | None,
    error: str | None,
    commit_audio: float | None,
    commit_wall: float | None,
    commit_text: str,
    final: str,
    voices: Voices,
) -> dict[str, Any]:
    item, arm = turn.item, turn.arm
    end = item.speech_end_ms
    first_audio = kept.first_audio_ms if kept is not None else None
    heard_at: float | None = None
    if first_audio is not None:
        heard_at = first_audio if arm.play == "early" or commit_wall is None else max(first_audio, commit_wall)
    commit_barge = False
    if commit_audio is not None:
        commit_barge, _ = classify_human_endpoint(speech_end_ms=end, pauses=item.pauses, t_endpoint_ms=commit_audio)
    cut_in = [
        a
        for a in attempts
        if arm.play == "early"
        and a.fate != "kept"
        and a.first_audio_ms is not None
        and a.retired_ms is not None
        and a.first_audio_ms < a.retired_ms
    ]
    return {
        "id": item.id,
        "kind": item.kind,
        "arm": arm.name,
        "ok": error is None and heard_at is not None,
        "error": error,
        "speech_end_ms": end,
        "commit_audio_ms": _r(commit_audio),
        "commit_wall_ms": _r(commit_wall),
        "commit_text": commit_text,
        "final": final,
        "heard_ms": _r(heard_at),
        "e2e_ms": None if heard_at is None else round(heard_at - end, 1),
        "commit_barge": commit_barge,
        "cut_in": len(cut_in),
        "false_barge_in": commit_barge or bool(cut_in),
        "llm_calls": len(attempts),
        "cancelled": sum(1 for a in attempts if a.fate == "cancelled"),
        "restarts": sum(1 for a in attempts if a.fate.startswith("restart")),
        "sockets": voices.opened,
        "socket_retries": list(voices.retries),
        "gate_wait_ms": round(sum(a.gate_wait_ms for a in attempts), 1),
        "attempts": [a.as_json() for a in attempts],
    }


def breakdown(row: dict[str, Any]) -> dict[str, float | None]:
    """Where the kept attempt's time went, relative to the true speech end."""
    kept = next((a for a in row["attempts"] if a["fate"] == "kept"), None)
    end = float(row["speech_end_ms"])

    def rel(value: object) -> float | None:
        return None if not isinstance(value, (int, float)) else round(float(value) - end, 1)

    def diff(a: object, b: object) -> float | None:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return round(float(a) - float(b), 1)
        return None

    if kept is None:
        return {}
    return {
        "commit": rel(row["commit_wall_ms"]),
        "launch": rel(kept["launch_ms"]),
        "first_token_after_launch": diff(kept["first_token_ms"], kept["launch_ms"]),
        "first_audio_after_clause": diff(kept["first_audio_ms"], kept["first_clause_ms"]),
        "ready": rel(kept["first_audio_ms"]),
        "held": diff(row["heard_ms"], kept["first_audio_ms"]),
        "heard": rel(row["heard_ms"]),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [r for r in rows if r["ok"] and not r["false_barge_in"]]
    e2e = [float(r["e2e_ms"]) for r in clean]
    calls = sum(int(r["llm_calls"]) for r in rows)
    cancelled = sum(int(r["cancelled"]) for r in rows)
    restarts = sum(int(r["restarts"]) for r in rows)
    parts: dict[str, dict[str, float | None]] = {}
    for key in ("commit", "launch", "first_token_after_launch", "first_audio_after_clause", "ready", "held", "heard"):
        values = [v for r in clean if (v := breakdown(r).get(key)) is not None]
        parts[key] = {"p50": percentile(values, 50), "p95": percentile(values, 95)}
    return {
        "n": len(rows),
        "ok": sum(1 for r in rows if r["ok"]),
        "false_barge_in": sum(1 for r in rows if r["ok"] and r["false_barge_in"]),
        "cut_in": sum(int(r["cut_in"]) for r in rows),
        "e2e_ms": {"p50": percentile(e2e, 50), "p95": percentile(e2e, 95), "n": len(e2e)},
        "llm_calls": calls,
        "cancelled": cancelled,
        "restarts": restarts,
        "wasted_rate": (calls - len(rows)) / calls if calls else None,
        "sockets": sum(int(r["sockets"]) for r in rows),
        "socket_retries": sum(len(r["socket_retries"]) for r in rows),
        "trial_retries": sum(int(r.get("trial_retries", 0)) for r in rows),
        "gate_wait_ms": round(sum(float(r["gate_wait_ms"]) for r in rows), 1),
        "breakdown_ms": parts,
        "errors": sorted({str(r["error"]) for r in rows if r["error"]}),
    }


def _history() -> list[dict[str, str]]:
    raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    return [
        {"role": e["role"], "content": e["content"]}
        for e in raw
        if isinstance(e, dict) and isinstance(e.get("role"), str) and isinstance(e.get("content"), str)
    ]


async def _campaign(args: argparse.Namespace) -> dict[str, Any]:
    items = tc.load(args.annotations, args.volc)
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [item for item in items if item.id in wanted]
    if args.limit:
        items = items[: args.limit]
    arms = [ARMS[name] for name in args.arms.split(",")]
    system = PERSONA_PATH.read_text(encoding="utf-8").strip() + "\n"
    history = _history()
    gate = RateGate(args.rpm)

    livekit: tc.Judge | None = None
    smart: Callable[[bytes, str], bool] | None = None
    if not args.mock:
        from latency.turn_models import LiveKitTurn, SmartTurn

        if any(arm.commit == "s4" for arm in arms):
            model = LiveKitTurn(args.models / "livekit-turn")
            livekit = tc.ScoreJudge("LiveKit≥0.1", lambda text: float(model.probability(b"", text)), 0.1)
        if any(arm.smart for arm in arms):
            smart = SmartTurn(args.models / "smart-turn-v3.2-cpu.onnx").complete
    else:
        livekit = tc.FinalJudge()

    if args.mock:
        from latency.adapters.mock import MockASR, MockLLM, MockTTS

        def make_asr(item: tc.Item) -> ASRAdapter:
            return MockASR(transcript=tc.latest(item.partials, float("inf")), partial="店长")

        def make_llm() -> LLMAdapter:
            return MockLLM()

        def make_tts() -> TTSAdapter:
            return MockTTS()
    else:
        from latency.adapters.factory import build_asr, build_llm, build_tts
        from latency.env import Settings, load_dotenv

        load_dotenv()
        settings = Settings.from_environ()
        missing = settings.missing_for("volc", "qwen", "minimax")
        if missing:
            raise SystemExit("missing " + ", ".join(missing))

        def make_asr(item: tc.Item) -> ASRAdapter:
            del item
            return build_asr("volc", settings)

        def make_llm() -> LLMAdapter:
            return build_llm("qwen", settings)

        def make_tts() -> TTSAdapter:
            return build_tts("minimax", settings)

    rows: list[dict[str, Any]] = []
    started = datetime.now(UTC).isoformat()
    for item in items:
        pcm = (silence(item.duration_ms) if args.mock else read_wav(args.audio / f"{item.id}.wav")) + silence(tc.PAD_MS)
        for arm in arms:
            row: dict[str, Any] = {}
            for trial in range(1, 4):
                await gate.reserve(4)
                turn = Turn(item, pcm, arm, commit_rule(arm.commit, livekit), system, history, smart)
                voices = Voices(make_tts, gate, warm=arm.warm)
                row = await run_turn(turn, asr=make_asr(item), llm=make_llm(), voices=voices)
                row["trial_retries"] = trial - 1
                if row["ok"] or not _again(row["error"]) or trial == 3:
                    break
                print(f"{arm.name} {item.id} retry after {row['error']}", flush=True)
                await asyncio.sleep(20.0)
            rows.append(row)
            mark = "ok" if row["ok"] else row["error"]
            print(
                f"{arm.name:<6} {item.id} {mark} e2e={row['e2e_ms']} barge={row['false_barge_in']} "
                f"calls={row['llm_calls']} cancel={row['cancelled']} restart={row['restarts']} "
                f"sockets={row['sockets']}",
                flush=True,
            )
            await asyncio.sleep(args.gap_s)
    return {
        "kind": "early-live",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "mock": bool(args.mock),
        "rpm_gate": args.rpm,
        "arms": {arm.name: arm.__dict__ for arm in arms},
        "summary": {arm.name: summarize([r for r in rows if r["arm"] == arm.name]) for arm in arms},
        "rows": rows,
    }


def _again(error: str | None) -> bool:
    if retryable_error(error):
        return True
    text = (error or "").lower()
    return "closed" in text or "socket" in text or "timed out" in text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audio", type=Path, default=Path("/var/lib/amadeus/turns"))
    parser.add_argument("--annotations", type=Path, default=tc.RESULTS / "turns-human-annotations.json")
    parser.add_argument("--volc", type=Path, default=tc.RESULTS / "turns-human-volc.json")
    parser.add_argument("--models", type=Path, default=HERE / "models")
    parser.add_argument("--arms", default="serial,r6,s1,s4")
    parser.add_argument("--ids", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--rpm", type=int, default=18, help="client-side cap on MiniMax socket opens per minute")
    parser.add_argument("--gap-s", type=float, default=2.0)
    parser.add_argument("--mock", action="store_true", help="in-process adapters; checks the wiring, costs nothing")
    parser.add_argument("--out", type=Path, default=tc.RESULTS / "early-live.json")
    args = parser.parse_args()
    payload = asyncio.run(_campaign(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
