"""Serial versus overlapped cascade.

The commit point is the R3b choice: 2000 ms of silence. The overlapped arm
starts the fast brain on the latest ASR partial once 150 ms of silence has
passed and Smart Turn says the turn is probably finished. The same text keeps
the reply; a large mismatch cancels it and starts again. Continuing speech
before the commit point cancels a speculative call.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import platform
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from latency.adapters.factory import build_asr, build_llm, build_tts
from latency.adapters.types import ASRUpdate, AudioChunk, LLMDelta, LLMUsage, redact, retryable_error
from latency.clauses import ClauseBuffer
from latency.corpus import Item, load_corpus
from latency.endpoint import Endpointer, EnergyGate
from latency.env import Settings, load_dotenv
from latency.pcm import frames, read_wav, silence
from latency.pipeline import Trial, realtime_trial
from latency.scoring import percentile
from latency.stats import summarize

HERE = Path(__file__).resolve().parent
PERSONA_PATH = HERE / "prompts" / "persona.md"
HISTORY_PATH = HERE / "prompts" / "history.json"
FRAME_MS = 20.0
ASR_PACKET_MS = 200.0
COMMIT_MS = 2000.0
SPECULATE_MS = 150.0
PAD_MS = 2500.0
PUNCTUATION = set("，。！？、；：…,.!?;: \n\t“”\"'（）()《》【】")


def content(text: str) -> str:
    return "".join(char for char in text if char not in PUNCTUATION and not char.isspace())


def texts_continue(partial: str, final: str) -> bool:
    """True when the final transcript is the same turn the partial already started."""

    left = content(partial)
    right = content(final)
    if left == right:
        return True
    if not left or not right:
        return False
    return difflib.SequenceMatcher(a=left, b=right).ratio() >= 0.85


@dataclass
class OverlapResult:
    item_id: str
    ok: bool
    error: str | None
    t_speech_end: float
    t_endpoint: float | None
    t_first_audio: float | None
    llm_calls: int
    cancelled: int
    transcript: str
    reply: str

    def as_json(self) -> dict[str, object]:
        return asdict(self)


class _Attempt:
    def __init__(self, text: str) -> None:
        self.text = text
        self.discarded = False
        self.task: asyncio.Task[None] | None = None
        self.first_token: float | None = None
        self.first_audio: float | None = None
        self.reply: list[str] = []
        self.error: str | None = None
        self.tts: object | None = None


async def overlap_trial(
    item: Item,
    *,
    asr: object,
    llm: object,
    make_tts: Callable[[], object],
    system: str,
    history: list[dict[str, str]],
    probable: Callable[[bytes, str], bool],
    commit_ms: float = COMMIT_MS,
    speculate_ms: float = SPECULATE_MS,
    pad_ms: float = PAD_MS,
) -> OverlapResult:
    gate = EnergyGate()
    endpointer = Endpointer("fixed", fixed_silence_ms=commit_ms)
    origin = time.perf_counter()

    def wall_ms() -> float:
        return (time.perf_counter() - origin) * 1000.0

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    latest = ""
    final = ""
    asr_error: str | None = None

    async def audio_iter() -> AsyncIterator[bytes]:
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                break
            yield chunk

    async def consume_asr() -> None:
        nonlocal latest, final, asr_error
        try:
            async for update in asr.transcribe(audio_iter()):  # type: ignore[attr-defined]
                if isinstance(update, ASRUpdate) and update.text:
                    latest = update.text
                    if update.is_final:
                        final = update.text
        except Exception as exc:
            asr_error = redact(str(exc))

    asr_task = asyncio.create_task(consume_asr())
    attempt: _Attempt | None = None
    calls = 0
    cancelled = 0
    committed = False
    speculated = False
    decision_ms: float | None = None

    async def retire(current: _Attempt) -> None:
        current.discarded = True
        task = current.task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        voice = getattr(current, "tts", None)
        if voice is not None:
            try:
                await voice.close()
            except Exception:
                pass

    async def drive(current: _Attempt) -> None:
        tts = current.tts
        if tts is None:
            current.error = "no synthesizer"
            return
        clause_queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def clauses() -> AsyncIterator[str]:
            while True:
                piece = await clause_queue.get()
                if piece is None:
                    break
                yield piece

        async def consume_tts() -> None:
            try:
                async for item_out in tts.speak(clauses()):  # type: ignore[attr-defined]
                    if isinstance(item_out, AudioChunk) and item_out.pcm and current.first_audio is None:
                        if not current.discarded:
                            current.first_audio = wall_ms()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current.error = redact(str(exc))

        tts_task: asyncio.Task[None] | None = None
        try:
            await tts.open(instruction="年轻女声，安静，温和，吐字清楚，略带书卷气。情绪幅度小，像在小声交谈。")  # type: ignore[attr-defined]
            tts_task = asyncio.create_task(consume_tts())
            buffer = ClauseBuffer()
            async for piece in llm.stream(system=system, history=history, user=current.text):  # type: ignore[attr-defined]
                if current.discarded:
                    break
                if isinstance(piece, LLMDelta) and piece.text:
                    if current.first_token is None:
                        current.first_token = wall_ms()
                    current.reply.append(piece.text)
                    for clause in buffer.push(piece.text):
                        await clause_queue.put(clause)
                elif isinstance(piece, LLMUsage):
                    continue
            tail = buffer.flush()
            if tail and not current.discarded:
                await clause_queue.put(tail)
        except asyncio.CancelledError:
            current.discarded = True
            if tts_task is not None:
                tts_task.cancel()
            raise
        except Exception as exc:
            current.error = redact(str(exc))
        finally:
            await clause_queue.put(None)
            if tts_task is not None:
                try:
                    await asyncio.wait_for(tts_task, timeout=30.0)
                except TimeoutError:
                    tts_task.cancel()
                except asyncio.CancelledError:
                    pass

    async def launch(text: str, *, replacing: bool) -> None:
        nonlocal attempt, calls, cancelled
        if attempt is not None and texts_continue(attempt.text, text) and not attempt.discarded:
            return
        if attempt is not None:
            await retire(attempt)
            if replacing:
                cancelled += 1
            # The synthesizer needs the previous socket to finish closing.
            await asyncio.sleep(0.6)
        calls += 1
        current = _Attempt(text)
        current.tts = make_tts()
        attempt = current
        current.task = asyncio.create_task(drive(current))

    async def launch_cancel() -> None:
        nonlocal attempt, cancelled, speculated
        if attempt is None:
            return
        await retire(attempt)
        cancelled += 1
        attempt = None
        speculated = False

    pcm = read_wav(item.audio) + silence(pad_ms)
    packet = bytearray()
    packet_ms = 0.0
    heard_pcm = bytearray()
    silence_ms = 0.0
    try:
        for frame in frames(pcm, FRAME_MS):
            delay = frame.end_ms / 1000.0 - (time.perf_counter() - origin)
            if delay > 0:
                await asyncio.sleep(delay)
            heard_pcm.extend(frame.pcm)
            packet.extend(frame.pcm)
            packet_ms += FRAME_MS
            if packet_ms >= ASR_PACKET_MS:
                await audio_queue.put(bytes(packet))
                packet.clear()
                packet_ms = 0.0
            speech = gate.speech(frame.pcm)
            decision = endpointer.push(frame.end_ms, speech, latest)
            if speech:
                silence_ms = 0.0
                if attempt is not None and not committed:
                    await launch_cancel()
                continue
            silence_ms += FRAME_MS
            if decision is not None and not committed:
                committed = True
                decision_ms = decision.t_ms
                if latest.strip():
                    await launch(latest, replacing=attempt is not None)
            elif (
                not speculated
                and not committed
                and latest.strip()
                and silence_ms >= speculate_ms
                and probable(bytes(heard_pcm), latest)
            ):
                speculated = True
                await launch(latest, replacing=False)
        if packet:
            await audio_queue.put(bytes(packet))
        await audio_queue.put(None)
    except Exception as exc:
        await audio_queue.put(None)
        asr_task.cancel()
        return _bad(item, redact(str(exc)), calls, cancelled)

    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline and not final and asr_error is None and not asr_task.done():
        await asyncio.sleep(0.02)
    try:
        await asyncio.wait_for(asr_task, timeout=3.0)
    except TimeoutError:
        asr_task.cancel()
    transcript = final or latest
    if asr_error:
        return _bad(item, asr_error, calls, cancelled)
    if attempt is not None and transcript and not texts_continue(attempt.text, transcript):
        await launch(transcript, replacing=True)
    elif attempt is None and transcript.strip():
        await launch(transcript, replacing=False)
    if attempt is None or attempt.task is None:
        return _bad(item, "overlap never started", calls, cancelled)
    try:
        await attempt.task
    except asyncio.CancelledError:
        pass
    kept = attempt
    error = kept.error
    if kept.first_audio is None and error is None:
        error = "TTS produced no audio"
    return OverlapResult(
        item_id=item.item_id,
        ok=error is None and kept.first_audio is not None and not kept.discarded,
        error=error,
        t_speech_end=item.speech_end_ms,
        t_endpoint=decision_ms,
        t_first_audio=None if kept.discarded else kept.first_audio,
        llm_calls=calls,
        cancelled=cancelled,
        transcript=transcript,
        reply="" if kept.discarded else "".join(kept.reply),
    )


def _again(error: str | None) -> bool:
    if retryable_error(error):
        return True
    if not error:
        return False
    text = error.lower()
    return "closed" in text or "1000" in text or "socket" in text


def _bad(item: Item, error: str, calls: int, cancelled: int) -> OverlapResult:
    return OverlapResult(item.item_id, False, error, item.speech_end_ms, None, None, calls, cancelled, "", "")


def _history() -> list[dict[str, str]]:
    raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history: list[dict[str, str]] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("role"), str) and isinstance(entry.get("content"), str):
                history.append({"role": entry["role"], "content": entry["content"]})
    return history


def _summary(results: list[OverlapResult]) -> dict[str, object]:
    clean = [row for row in results if row.ok and row.t_first_audio is not None]
    e2e = [row.t_first_audio - row.t_speech_end for row in clean if row.t_first_audio is not None]
    calls = sum(row.llm_calls for row in results)
    cancelled = sum(row.cancelled for row in results)
    return {
        "n": len(results),
        "ok": sum(1 for row in results if row.ok),
        "e2e_ms": {"p50": percentile(e2e, 50), "p95": percentile(e2e, 95), "n": len(e2e)},
        "llm_calls": calls,
        "cancelled": cancelled,
        "cancel_rate": cancelled / calls if calls else None,
        "extra_calls": calls - len(clean),
    }


async def _both(items: list[Item], settings: Settings, smart_path: Path) -> tuple[list[Trial], list[OverlapResult]]:
    from latency.turn_models import SmartTurn

    system = PERSONA_PATH.read_text(encoding="utf-8").strip() + "\n"
    history = _history()
    smart = SmartTurn(smart_path)
    serial: list[Trial] = []
    overlapped: list[OverlapResult] = []
    for item in items:
        serial_trial = await _serial(item, settings, system, history)
        serial.append(serial_trial)
        print(f"serial {item.item_id} {'ok' if serial_trial.ok else serial_trial.error}", flush=True)
        await asyncio.sleep(2.0)
        over = await _overlap_once(item, settings, system, history, smart.complete)
        overlapped.append(over)
        mark = "ok" if over.ok else over.error
        print(f"overlap {item.item_id} {mark} calls={over.llm_calls} cancelled={over.cancelled}", flush=True)
        await asyncio.sleep(2.0)
    return serial, overlapped


async def _serial(item: Item, settings: Settings, system: str, history: list[dict[str, str]]) -> Trial:
    last: Trial | None = None
    for attempt in range(1, 4):
        asr = build_asr("volc", settings)
        llm = build_llm("qwen", settings)
        tts = build_tts("minimax", settings)
        try:
            last = await realtime_trial(
                item,
                combo="volc/qwen/minimax",
                mode="fixed",
                repeat=1,
                asr=asr,
                llm=llm,
                tts=tts,
                system=system,
                history=history,
                model=None,
                oracle_partials=False,
                mock=False,
                instruction="年轻女声，安静，温和，吐字清楚，略带书卷气。情绪幅度小，像在小声交谈。",
                fixed_silence_ms=COMMIT_MS,
                pad_ms=PAD_MS,
            )
        except Exception as exc:
            last = None
            error = redact(str(exc))
            print(f"serial {item.item_id} attempt {attempt} {error}", flush=True)
            if attempt == 3 or not retryable_error(error):
                return _bad_trial(item, error)
        if last is not None and (last.ok or not retryable_error(last.error) or attempt == 3):
            return last
        await asyncio.sleep(4.0)
    assert last is not None
    return last


async def _overlap_once(
    item: Item,
    settings: Settings,
    system: str,
    history: list[dict[str, str]],
    probable: Callable[[bytes, str], bool],
) -> OverlapResult:
    last = _bad(item, "not run", 0, 0)
    for attempt in range(1, 4):
        asr = build_asr("volc", settings)
        llm = build_llm("qwen", settings)
        last = await overlap_trial(
            item,
            asr=asr,
            llm=llm,
            make_tts=lambda: build_tts("minimax", settings),
            system=system,
            history=history,
            probable=probable,
        )
        if last.ok or not _again(last.error) or attempt == 3:
            return last
        await asyncio.sleep(10.0)
    return last


def _bad_trial(item: Item, error: str) -> Trial:
    return Trial(
        combo="volc/qwen/minimax",
        endpoint="fixed",
        item_id=item.item_id,
        category=item.category,
        repeat=1,
        ok=False,
        error=error,
        partial_source="asr",
        t_speech_end=item.speech_end_ms,
        t_endpoint=None,
        t_asr_final=None,
        t_llm_first_token=None,
        t_llm_first_clause=None,
        t_tts_first_audio=None,
        early=False,
        false_barge_in=False,
        asr_used_partial=False,
        endpoint_reason="error",
        transcript="",
        reply="",
        cached_tokens=None,
        prompt_tokens=None,
        mock=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare serial and overlapped cascades")
    parser.add_argument("--manifest", type=Path, default=HERE / "corpus" / "manifest.json")
    parser.add_argument("--smart", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    load_dotenv()
    settings = Settings.from_environ()
    missing = settings.missing_for("volc", "qwen", "minimax")
    if missing:
        raise SystemExit("missing " + ", ".join(missing))
    items = load_corpus(args.manifest)
    if args.limit:
        items = items[: args.limit]
    serial, overlapped = asyncio.run(_both(items, settings, args.smart))
    serial_summary = summarize(serial)
    payload = {
        "kind": "early-speech",
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commit_ms": COMMIT_MS,
        "speculate_ms": SPECULATE_MS,
        "pad_ms": PAD_MS,
        "continue_rule": "标点去掉之后完全相同，或者字符重合率不低于 0.85，就沿用这次生成。否则作废重来。",
        "serial": serial_summary,
        "overlap": _summary(overlapped),
        "serial_trials": [trial.as_json() for trial in serial],
        "overlap_trials": [trial.as_json() for trial in overlapped],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"serial": serial_summary, "overlap": payload["overlap"]}, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
