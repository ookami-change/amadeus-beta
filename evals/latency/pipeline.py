"""One cascaded trial: pace audio, endpoint, then stream LLM clauses into TTS."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass

from latency.adapters.types import (
    ASRAdapter,
    ASRUpdate,
    AudioChunk,
    LLMAdapter,
    LLMDelta,
    LLMUsage,
    TTSAdapter,
    redact,
)
from latency.clauses import ClauseBuffer
from latency.corpus import Item
from latency.endpoint import CompletenessModel, Decision, Endpointer, EnergyGate
from latency.pcm import frames, read_wav, silence
from latency.scoring import classify_endpoint

FRAME_MS = 20.0
ASR_PACKET_MS = 200.0
ASR_WAIT_S = 3.0


@dataclass
class Trial:
    combo: str
    endpoint: str
    item_id: str
    category: str
    repeat: int
    ok: bool
    error: str | None
    partial_source: str
    t_speech_end: float
    t_endpoint: float | None
    t_asr_final: float | None
    t_llm_first_token: float | None
    t_llm_first_clause: float | None
    t_tts_first_audio: float | None
    early: bool
    false_barge_in: bool
    asr_used_partial: bool
    endpoint_reason: str
    transcript: str
    reply: str
    cached_tokens: int | None
    prompt_tokens: int | None
    mock: bool
    t_ready_ms: float | None = None
    t_tts_open_ms: float | None = None
    t_llm_start_ms: float | None = None
    schedule: str = "serial"

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def endpoint_trial(
    item: Item,
    *,
    mode: str,
    model: CompletenessModel | None,
    gate: EnergyGate | None = None,
) -> Trial:
    """Fast pass. Time is the audio timeline plus the model's own compute."""

    speech_gate = gate or EnergyGate()
    endpointer = Endpointer(mode, model)
    decision: Decision | None = None
    pcm = read_wav(item.audio)
    for frame in frames(pcm, FRAME_MS):
        decision = endpointer.push(
            frame.end_ms,
            speech_gate.speech(frame.pcm),
            item.oracle_text(frame.end_ms),
        )
        if decision is not None:
            break
    return _from_decision(item, mode, decision, partial_source="oracle", mock=False)


async def realtime_trial(
    item: Item,
    *,
    combo: str,
    mode: str,
    repeat: int,
    asr: ASRAdapter,
    llm: LLMAdapter,
    tts: TTSAdapter,
    system: str,
    history: list[dict[str, str]],
    model: CompletenessModel | None,
    oracle_partials: bool,
    mock: bool,
    instruction: str,
    defer_tts: bool = False,
    hold_asr: bool = False,
    fixed_silence_ms: float = 500.0,
    pad_ms: float = 0.0,
) -> Trial:
    """Real-time audio in, wall-clock stamps out. TTS is opened at the endpoint.

    ``defer_tts`` opens the synthesizer after the first LLM token, so the
    handshake is not inside the first-token wait. ``hold_asr`` leaves the
    recognizer socket up until that token arrives.
    """

    gate = EnergyGate()
    endpointer = Endpointer(mode, model, fixed_silence_ms=fixed_silence_ms)
    origin = time.perf_counter()

    def wall_ms() -> float:
        return (time.perf_counter() - origin) * 1000.0

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    latest_text = ""
    final_text = ""
    asr_final_ms: float | None = None
    asr_error: str | None = None

    async def audio_iter() -> AsyncIterator[bytes]:
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                break
            yield chunk

    async def consume_asr() -> None:
        nonlocal latest_text, final_text, asr_final_ms, asr_error
        try:
            async for update in asr.transcribe(audio_iter()):
                _apply_asr(update)
        except Exception as exc:
            asr_error = redact(str(exc))

    def _apply_asr(update: ASRUpdate) -> None:
        nonlocal latest_text, final_text, asr_final_ms
        if update.text:
            latest_text = update.text
        if update.is_final and update.text:
            final_text = update.text
            asr_final_ms = wall_ms()

    asr_task = asyncio.create_task(consume_asr())
    decision: Decision | None = None
    packet = bytearray()
    packet_ms = 0.0
    pcm = read_wav(item.audio) + (silence(pad_ms) if pad_ms else b"")
    try:
        for frame in frames(pcm, FRAME_MS):
            delay = frame.end_ms / 1000.0 - (time.perf_counter() - origin)
            if delay > 0:
                await asyncio.sleep(delay)
            packet.extend(frame.pcm)
            packet_ms += FRAME_MS
            if packet_ms >= ASR_PACKET_MS:
                await audio_queue.put(bytes(packet))
                packet.clear()
                packet_ms = 0.0
            partial = item.oracle_text(frame.end_ms) if oracle_partials else latest_text
            decision = endpointer.push(frame.end_ms, gate.speech(frame.pcm), partial)
            if decision is not None:
                break
        if packet:
            await audio_queue.put(bytes(packet))
        await audio_queue.put(None)
    except Exception as exc:
        await audio_queue.put(None)
        asr_task.cancel()
        return _failed(item, combo, mode, repeat, mock, redact(str(exc)), oracle_partials)

    deadline = time.perf_counter() + ASR_WAIT_S
    while time.perf_counter() < deadline:
        if asr_error or (final_text and final_text == latest_text):
            break
        if asr_task.done():
            break
        await asyncio.sleep(0.02)
    if not hold_asr:
        await _finish_asr(asr_task)

    ready_ms = wall_ms()

    # Decision.t_ms is the paced audio time plus model compute, which matches the
    # wall clock at the moment of the decision. Measuring it again after the ASR
    # wait would bill that wait to the endpointer.
    t_endpoint = decision.t_ms if decision is not None else wall_ms()

    early, barge = (False, False)
    if decision is not None:
        early, barge = classify_endpoint(
            speech_end_ms=item.speech_end_ms,
            pauses=list(item.pauses),
            t_endpoint_ms=t_endpoint,
        )
    transcript = latest_text or final_text
    used_partial = bool(transcript) and transcript != final_text
    if used_partial and asr_final_ms is None:
        asr_final_ms = t_endpoint
    if asr_error:
        return _failed(item, combo, mode, repeat, mock, asr_error, oracle_partials, decision, early, barge)
    if not transcript.strip():
        return _failed(
            item, combo, mode, repeat, mock, "ASR returned an empty transcript", oracle_partials, decision, early, barge
        )

    clause_queue: asyncio.Queue[str | None] = asyncio.Queue()
    first_token: float | None = None
    first_clause: float | None = None
    first_audio: float | None = None
    reply_parts: list[str] = []
    usage_prompt: int | None = None
    usage_cached: int | None = None
    llm_error: str | None = None
    tts_error: str | None = None
    opened_ms: float | None = None
    tts_task: asyncio.Task[None] | None = None

    async def clauses() -> AsyncIterator[str]:
        while True:
            piece = await clause_queue.get()
            if piece is None:
                break
            yield piece

    async def consume_tts() -> None:
        nonlocal first_audio, tts_error
        try:
            async for item_out in tts.speak(clauses()):
                if isinstance(item_out, AudioChunk) and item_out.pcm and first_audio is None:
                    first_audio = wall_ms()
        except Exception as exc:
            tts_error = redact(str(exc))

    async def ensure_tts() -> None:
        nonlocal opened_ms, tts_error, tts_task
        if tts_task is not None or tts_error is not None:
            return
        try:
            await tts.open(instruction=instruction)
        except Exception as exc:
            tts_error = redact(str(exc))
            return
        opened_ms = wall_ms()
        tts_task = asyncio.create_task(consume_tts())

    if not defer_tts:
        await ensure_tts()
        if tts_error is not None:
            if hold_asr:
                await _finish_asr(asr_task)
            return _failed(item, combo, mode, repeat, mock, tts_error, oracle_partials, decision, early, barge)

    buffer = ClauseBuffer()
    llm_start_ms = wall_ms()
    try:
        async for piece in llm.stream(system=system, history=history, user=transcript):
            if isinstance(piece, LLMDelta) and piece.text:
                if first_token is None:
                    first_token = wall_ms()
                    if hold_asr:
                        await _finish_asr(asr_task)
                    if defer_tts:
                        await ensure_tts()
                reply_parts.append(piece.text)
                for clause in buffer.push(piece.text):
                    if first_clause is None:
                        first_clause = wall_ms()
                    await clause_queue.put(clause)
            elif isinstance(piece, LLMUsage):
                usage_prompt = piece.prompt_tokens
                usage_cached = piece.cached_tokens
        tail = buffer.flush()
        if tail:
            if first_clause is None:
                first_clause = wall_ms()
            await clause_queue.put(tail)
    except Exception as exc:
        llm_error = redact(str(exc))
    finally:
        if hold_asr:
            await _finish_asr(asr_task)
        await clause_queue.put(None)

    if tts_task is not None:
        while first_audio is None and not tts_task.done():
            await asyncio.sleep(0.01)
        try:
            await asyncio.wait_for(tts_task, timeout=30.0)
        except TimeoutError:
            tts_task.cancel()
            tts_error = tts_error or "TTS produced no audio within 30s"
        try:
            await tts.close()
        except Exception as exc:
            tts_error = tts_error or redact(str(exc))

    error = llm_error or tts_error
    if first_audio is None and error is None:
        error = "TTS produced no audio"
    schedule = "defer-tts" if defer_tts else "hold-asr" if hold_asr else "serial"
    return Trial(
        combo=combo,
        endpoint=mode,
        item_id=item.item_id,
        category=item.category,
        repeat=repeat,
        ok=error is None and first_audio is not None,
        error=error,
        partial_source="oracle" if oracle_partials else "asr",
        t_speech_end=item.speech_end_ms,
        t_endpoint=t_endpoint,
        t_asr_final=asr_final_ms,
        t_llm_first_token=first_token,
        t_llm_first_clause=first_clause,
        t_tts_first_audio=first_audio,
        early=early,
        false_barge_in=barge,
        asr_used_partial=used_partial,
        endpoint_reason=decision.reason if decision else "none",
        transcript=transcript,
        reply="".join(reply_parts),
        cached_tokens=usage_cached,
        prompt_tokens=usage_prompt,
        mock=mock,
        t_ready_ms=ready_ms,
        t_tts_open_ms=opened_ms,
        t_llm_start_ms=llm_start_ms,
        schedule=schedule,
    )


async def _finish_asr(task: asyncio.Task[None]) -> None:
    if task.done():
        return
    try:
        await asyncio.wait_for(task, timeout=3.0)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


def _from_decision(
    item: Item,
    mode: str,
    decision: Decision | None,
    *,
    partial_source: str,
    mock: bool,
) -> Trial:
    early = False
    barge = False
    t_endpoint: float | None = None
    reason = "none"
    if decision is not None:
        t_endpoint = decision.t_ms
        reason = decision.reason
        early, barge = classify_endpoint(
            speech_end_ms=item.speech_end_ms,
            pauses=list(item.pauses),
            t_endpoint_ms=t_endpoint,
        )
    return Trial(
        combo="endpoint-only",
        endpoint=mode,
        item_id=item.item_id,
        category=item.category,
        repeat=1,
        ok=decision is not None,
        error=None if decision is not None else "endpointer never fired",
        partial_source=partial_source,
        t_speech_end=item.speech_end_ms,
        t_endpoint=t_endpoint,
        t_asr_final=None,
        t_llm_first_token=None,
        t_llm_first_clause=None,
        t_tts_first_audio=None,
        early=early,
        false_barge_in=barge,
        asr_used_partial=False,
        endpoint_reason=reason,
        transcript=decision.text if decision else "",
        reply="",
        cached_tokens=None,
        prompt_tokens=None,
        mock=mock,
    )


def _failed(
    item: Item,
    combo: str,
    mode: str,
    repeat: int,
    mock: bool,
    error: str,
    oracle_partials: bool,
    decision: Decision | None = None,
    early: bool = False,
    barge: bool = False,
) -> Trial:
    return Trial(
        combo=combo,
        endpoint=mode,
        item_id=item.item_id,
        category=item.category,
        repeat=repeat,
        ok=False,
        error=error,
        partial_source="oracle" if oracle_partials else "asr",
        t_speech_end=item.speech_end_ms,
        t_endpoint=decision.t_ms if decision else None,
        t_asr_final=None,
        t_llm_first_token=None,
        t_llm_first_clause=None,
        t_tts_first_audio=None,
        early=early,
        false_barge_in=barge,
        asr_used_partial=False,
        endpoint_reason=decision.reason if decision else "none",
        transcript="",
        reply="",
        cached_tokens=None,
        prompt_tokens=None,
        mock=mock,
    )
