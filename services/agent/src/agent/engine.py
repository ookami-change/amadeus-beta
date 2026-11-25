"""The turn engine: one per conversation, independent of the transport.

The rules are the ones M0 measured (``evals/latency/REPORT.md``, R3c and R6b):

* **Commit** (end of the user's turn): adaptive silence. 1800 ms when the
  partial ends on a connector or filler, otherwise 1400 ms.
* **Speculative launch**: at 400 ms of silence the fast brain starts on the
  partial held at that moment. Speech resuming cancels it.
* **Hold**: a reply may be prepared early but is never played before the commit
  point. R6 played as soon as audio existed and cut in on 21/50 human
  recordings.
* **Late synthesis**: a speculation only thinks until the silence is within
  ``synth_lead_ms`` of the commit point; then it takes the synthesizer socket.
  A socket that has been sent text cannot be reused, and MiniMax allows 10 a
  minute on this account. The first M1-2 replay synthesized every speculation
  and queued for sockets for up to 13 s.
* **Two requests a reply**: MiniMax counts each ``task_continue`` against the
  same quota (the second replay met RPM refusals mid-synthesis at 5 opens a
  minute). The first send waits for ``first_send_chars``; the rest of the reply
  goes in one send when the LLM is done. Every send passes the rate gate.
* **Same-turn check**: at the commit point, and again on the final transcript
  while nothing has been played, an attempt whose text is not the same turn
  (R6's ``texts_continue``) is dropped and restarted.

Everything is timed on the session audio clock: milliseconds of mic audio
received, extended by wall time since the last frame. ``turn_report`` carries
those times so a replay can line them up with its annotations.

Interruptions are basic here (stop and listen). Backchannels, yielding after a
false commit and trimming history to what was heard are M1-3.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Protocol

from agent import protocol as p
from agent.streams import Channel
from agent.text import ClauseBuffer, ends_with_connector, texts_continue
from agent.vad import SpeechTracker
from agent.vendors.types import (
    ASR,
    LLM,
    ASRStream,
    AudioChunk,
    TTSEvent,
    WordMark,
    redact,
    retryable,
)
from agent.voices import Voices

BYTES_PER_MS = 32  # 16 kHz s16le
FALLBACK_LINE = "嗯？刚才没听清，你再说一遍？"


@dataclass(frozen=True)
class TurnConfig:
    short_ms: float = 1400.0
    long_ms: float = 1800.0
    launch_ms: float | None = 400.0
    """``None`` launches only at the commit point (the serial cascade)."""
    preroll_ms: float = 300.0
    """Audio before the confirmed onset that still goes to the recogniser."""
    retries: int = 2
    retry_wait_s: float = 1.0
    synth_lead_ms: float = 400.0
    """Start synthesizing this long before the commit point (MiniMax first audio: P95 ~360 ms)."""
    first_send_chars: int = 8
    """The first synthesis request carries at least this much text (「嗯。」 alone waits)."""


class Sink(Protocol):
    async def message(self, message: p.Message) -> None: ...

    async def audio(self, utterance: int, pcm: bytes) -> None: ...


@dataclass
class Attempt:
    text: str
    reason: str
    """speculate, commit, final, retry or fallback."""
    launch_ms: float
    fixed: str | None = None
    """Say this instead of asking the LLM (the fallback line)."""
    fate: str = "kept"
    released: bool = False
    utterance: int | None = None
    first_token_ms: float | None = None
    ready_ms: float | None = None
    speak_ms: float | None = None
    spoke: bool = False
    voice_wait_ms: float | None = None
    error: str | None = None
    clauses: list[str] = field(default_factory=list)
    held: list[AudioChunk | WordMark | str] = field(default_factory=list)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    may_speak: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    """Set near the commit point or on release; until then the attempt only thinks."""


@dataclass
class Turn:
    id: int
    onset_ms: float
    partial: str = ""
    final: str | None = None
    committed: bool = False
    commit_ms: float | None = None
    commit_text: str = ""
    asr: ASRStream | None = None
    asr_task: asyncio.Task[None] | None = None
    asr_error: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    active: Attempt | None = None
    speculated: bool = False
    retries: int = 0
    ended: bool = False
    sockets_before: int = 0


class Engine:
    def __init__(
        self,
        *,
        asr: ASR,
        llm: LLM,
        voices: Voices,
        tracker: SpeechTracker,
        sink: Sink,
        system: str,
        history: list[dict[str, str]] | None = None,
        config: TurnConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.asr = asr
        self.llm = llm
        self.voices = voices
        self.tracker = tracker
        self.sink = sink
        self.system = system
        self.history: list[dict[str, str]] = list(history or [])
        self.config = config or TurnConfig()
        self._clock = clock
        self._audio_ms = 0.0
        self._last_frame = clock()
        self._recent = bytearray()
        self._turns = 0
        self._utterances = 0
        self.turn: Turn | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.reports: list[p.TurnReport] = []

    # ---- clock and lifecycle ----

    def now_ms(self) -> float:
        """Session audio clock, extended by wall time since the last mic frame."""
        return self._audio_ms + (self._clock() - self._last_frame) * 1000.0

    async def start(self) -> None:
        self.voices.prefetch()
        await self.sink.message(p.State("idle", None))

    async def close(self) -> None:
        turn, self.turn = self.turn, None
        if turn is not None:
            await self._abandon(turn, "closed")
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        # The voice pool belongs to whoever made it (process-wide in the server).

    def _spawn(self, coro: object) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ---- mic audio ----

    async def feed(self, pcm: bytes) -> None:
        self._audio_ms += len(pcm) / BYTES_PER_MS
        self._last_frame = self._clock()
        keep = int(self.config.preroll_ms * BYTES_PER_MS) + 256 * BYTES_PER_MS
        self._recent.extend(pcm)
        if len(self._recent) > keep:
            del self._recent[: len(self._recent) - keep]
        events = self.tracker.feed(pcm)
        turn = self.turn
        if turn is not None and not turn.committed and turn.asr is not None:
            await turn.asr.send(pcm)
        if "onset" in events:
            await self._onset()
        turn = self.turn
        if turn is not None and not turn.committed:
            await self._listen(turn)

    async def _onset(self) -> None:
        turn = self.turn
        if turn is not None and not turn.committed:
            # The speaker went on: a speculative reply is no longer the turn.
            turn.speculated = False
            if turn.active is not None:
                await self._retire(turn, turn.active, "cancelled")
            return
        if turn is not None:
            await self._interrupt(turn)
        await self._begin_turn()

    async def _begin_turn(self) -> None:
        self._turns += 1
        start = (
            self.tracker.speech_start_ms
            if self.tracker.speech_start_ms is not None
            else self.now_ms()
        )
        turn = Turn(id=self._turns, onset_ms=start, sockets_before=self.voices.opened)
        self.turn = turn
        await self.voices.refresh()
        await self.sink.message(p.State("listening", turn.id))
        try:
            turn.asr = await self.asr.start()
        except Exception as exc:
            turn.asr_error = redact(str(exc))
            return
        # Everything since a little before the onset: the confirmed onset lags the first voiced hop.
        back_ms = self._audio_ms - start + self.config.preroll_ms
        await turn.asr.send(bytes(self._recent[-int(back_ms * BYTES_PER_MS) // 2 * 2 :]))
        turn.asr_task = self._spawn(self._read_asr(turn, turn.asr))

    async def _read_asr(self, turn: Turn, stream: ASRStream) -> None:
        try:
            async for update in stream.updates:
                if self.turn is not turn:
                    return
                if update.text:
                    turn.partial = update.text
                if update.is_final:
                    turn.final = update.text
                await self.sink.message(p.Transcript(turn.id, update.text, update.is_final))
        except Exception as exc:
            turn.asr_error = redact(str(exc))
        if self.turn is turn and turn.committed:
            await self._settle_final(turn)

    async def _listen(self, turn: Turn) -> None:
        silence = self.tracker.silence_ms(self.now_ms())
        if silence is None:
            return
        text = turn.partial
        need = self.config.long_ms if ends_with_connector(text) else self.config.short_ms
        if silence + 1e-6 >= need:
            await self._commit(turn, text)
            return
        launch = self.config.launch_ms
        if launch is not None and turn.active is None and not turn.speculated and text.strip():
            if silence + 1e-6 >= launch:
                turn.speculated = True
                self._launch(turn, text, "speculate")
        active = turn.active
        if active is not None and silence + 1e-6 >= need - self.config.synth_lead_ms:
            active.may_speak.set()

    async def _commit(self, turn: Turn, text: str) -> None:
        turn.committed = True
        turn.commit_ms = self.now_ms()
        turn.commit_text = text
        await self.sink.message(p.Prepare(turn.id))
        await self.sink.message(p.State("thinking", turn.id))
        if turn.active is not None and not texts_continue(turn.active.text, text):
            await self._retire(turn, turn.active, "restart-commit")
        if turn.active is None and text.strip():
            self._launch(turn, text, "commit")
        if turn.asr is not None:
            await turn.asr.finish()
        att = turn.active
        if att is not None:
            await self._release(att)
            if att.done.is_set():
                # It finished while held; nothing else will close the turn.
                await self._attempt_over(turn, att)
        if turn.asr is None and not turn.ended:
            await self._settle_final(turn)

    async def _settle_final(self, turn: Turn) -> None:
        """The recogniser has closed: check the final transcript against what is being said."""
        final = (turn.final if turn.final is not None else turn.partial).strip()
        active = turn.active
        if (
            active is not None
            and final
            and active.utterance is None
            and not texts_continue(active.text, final)
        ):
            await self._retire(turn, active, "restart-final")
            active = None
        if active is None and turn.active is None:
            if final:
                await self._release(self._launch(turn, final, "final"))
            else:
                await self._finish_turn(turn, turn.asr_error or "nothing heard")

    # ---- attempts ----

    def _launch(self, turn: Turn, text: str, reason: str, fixed: str | None = None) -> Attempt:
        att = Attempt(text=text, reason=reason, launch_ms=self.now_ms(), fixed=fixed)
        turn.attempts.append(att)
        turn.active = att
        att.task = self._spawn(self._drive(turn, att))
        return att

    async def _drive(self, turn: Turn, att: Attempt) -> None:
        voice = None
        reader: asyncio.Task[None] | None = None
        try:
            buffer = ClauseBuffer()
            pending: list[str] = []
            sends = 0

            async def send() -> None:
                nonlocal sends
                if voice is None or not pending:
                    return
                text = "".join(pending)
                pending.clear()
                asked = self.now_ms()
                await self.voices.request()
                if sends == 0:
                    att.voice_wait_ms = (att.voice_wait_ms or 0.0) + self.now_ms() - asked
                sends += 1
                await voice.say(text)

            async def say(clause: str) -> None:
                nonlocal voice, reader
                if voice is None:
                    await att.may_speak.wait()
                    asked = self.now_ms()
                    voice, _how = await self.voices.take()
                    att.voice_wait_ms = self.now_ms() - asked
                    reader = asyncio.create_task(self._read_voice(att, voice.events))
                att.spoke = True
                att.clauses.append(clause)
                await self._emit(att, clause)
                pending.append(clause)
                if sends == 0 and len("".join(pending)) >= self.config.first_send_chars:
                    await send()

            if att.fixed is not None:
                att.first_token_ms = self.now_ms()
                await say(att.fixed)
            else:
                stream = self.llm.stream(system=self.system, history=self.history, user=att.text)
                async with aclosing(stream) as tokens:
                    async for token in tokens:
                        if att.first_token_ms is None:
                            att.first_token_ms = self.now_ms()
                        for clause in buffer.push(token):
                            await say(clause)
                tail = buffer.flush()
                if tail:
                    await say(tail)
            await send()
            if voice is not None and reader is not None:
                await voice.finish()
                await asyncio.wait_for(asyncio.shield(reader), timeout=30.0)
                reader.result()
            elif att.error is None:
                att.error = "the reply was empty"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            att.error = redact(str(exc)) or type(exc).__name__
        finally:
            if reader is not None and not reader.done():
                reader.cancel()
            if voice is not None:
                await self.voices.give_back(voice, spoke=att.spoke)
            att.done.set()
        if turn.active is att:
            await self._attempt_over(turn, att)

    async def _read_voice(self, att: Attempt, events: Channel[TTSEvent]) -> None:
        async for event in events:
            if isinstance(event, AudioChunk) and att.ready_ms is None:
                att.ready_ms = self.now_ms()
            await self._emit(att, event)

    async def _emit(self, att: Attempt, item: AudioChunk | WordMark | str) -> None:
        """Send now if the attempt has been released, otherwise hold it until the commit point."""
        if not att.released:
            att.held.append(item)
            return
        if isinstance(item, str):
            if att.utterance is not None:
                await self.sink.message(p.SpeakText(att.utterance, item))
            else:
                att.held.append(item)
            return
        if isinstance(item, WordMark):
            if att.utterance is not None:
                mark = p.LipsyncMark(item.start_ms, item.end_ms, item.text)
                await self.sink.message(p.Lipsync(att.utterance, [mark]))
            else:
                att.held.append(item)
            return
        if att.utterance is None:
            await self._start_utterance(att)
        assert att.utterance is not None
        await self.sink.audio(att.utterance, item.pcm)

    async def _start_utterance(self, att: Attempt) -> None:
        self._utterances += 1
        att.utterance = self._utterances
        att.speak_ms = self.now_ms()
        turn = self.turn
        turn_id = turn.id if turn is not None else 0
        await self.sink.message(p.SpeakStart(att.utterance, turn_id))
        await self.sink.message(p.State("speaking", turn_id))
        held = [one for one in att.held if not isinstance(one, AudioChunk)]
        att.held = [one for one in att.held if isinstance(one, AudioChunk)]
        for one in held:
            await self._emit(att, one)

    async def _release(self, att: Attempt) -> None:
        att.released = True
        att.may_speak.set()
        held, att.held = att.held, []
        audio = [one for one in held if isinstance(one, AudioChunk)]
        rest = [one for one in held if not isinstance(one, AudioChunk)]
        if audio:
            await self._start_utterance(att)
        for one in rest + audio:
            await self._emit(att, one)

    async def _retire(self, turn: Turn, att: Attempt, fate: str) -> None:
        att.fate = fate
        if turn.active is att:
            turn.active = None
        task = att.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        if att.utterance is not None:
            await self.sink.message(p.StopAudio(att.utterance))
            await self.sink.message(p.SpeakEnd(att.utterance, "interrupted"))

    async def _attempt_over(self, turn: Turn, att: Attempt) -> None:
        if not turn.committed:
            if att.error is not None:
                turn.active = None  # a failed speculation; the commit will launch again
            return
        if att.error is None:
            if att.utterance is not None:
                await self.sink.message(p.SpeakEnd(att.utterance, "done"))
            reader = turn.asr_task
            if reader is not None and reader is not asyncio.current_task() and not reader.done():
                # Audio is already out; let the final transcript reach history and the report.
                await asyncio.wait({reader}, timeout=1.0)
            user = turn.final or turn.commit_text or att.text
            self.history += [
                {"role": "user", "content": user},
                {"role": "assistant", "content": "".join(att.clauses)},
            ]
            await self._finish_turn(turn, None)
            return
        if att.utterance is not None:
            await self.sink.message(p.StopAudio(att.utterance))
            await self.sink.message(p.SpeakEnd(att.utterance, "error"))
        att.fate = "failed"
        turn.active = None
        if att.reason == "fallback":
            await self.sink.message(p.Error("tts", att.error, retryable(att.error)))
            await self._finish_turn(turn, att.error)
        elif retryable(att.error) and turn.retries < self.config.retries:
            turn.retries += 1
            await asyncio.sleep(self.config.retry_wait_s * turn.retries)
            if self.turn is turn and turn.active is None:
                await self._release(self._launch(turn, att.text, "retry"))
        else:
            await self.sink.message(p.Error("reply", att.error, retryable(att.error)))
            await self._release(self._launch(turn, att.text, "fallback", fixed=FALLBACK_LINE))

    # ---- turn end ----

    async def _interrupt(self, turn: Turn) -> None:
        """Basic barge-in: stop whatever is being said and listen. M1-3 refines this."""
        if turn.active is not None:
            playing = turn.active.utterance is not None
            await self._retire(turn, turn.active, "interrupted")
            if playing:
                await self.sink.message(p.State("interrupted", turn.id))
        await self._finish_turn(turn, "interrupted")

    async def _abandon(self, turn: Turn, reason: str) -> None:
        if turn.active is not None:
            await self._retire(turn, turn.active, reason)
        await self._finish_turn(turn, reason)

    async def _finish_turn(self, turn: Turn, error: str | None) -> None:
        if turn.ended:
            return
        turn.ended = True
        if turn.asr is not None:
            stream, turn.asr = turn.asr, None
            await stream.close()
        task = turn.asr_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        kept = next((a for a in reversed(turn.attempts) if a.fate == "kept"), None)
        report = p.TurnReport(
            turn=turn.id,
            onset_ms=round(turn.onset_ms, 1),
            commit_ms=None if turn.commit_ms is None else round(turn.commit_ms, 1),
            commit_text=turn.commit_text,
            final_text=turn.final or "",
            launches=len(turn.attempts),
            cancelled=sum(1 for a in turn.attempts if a.fate == "cancelled"),
            restarts=sum(1 for a in turn.attempts if a.fate.startswith("restart")),
            launch_ms=_round(kept.launch_ms if kept else None),
            first_token_ms=_round(kept.first_token_ms if kept else None),
            ready_ms=_round(kept.ready_ms if kept else None),
            speak_ms=_round(kept.speak_ms if kept else None),
            sockets=self.voices.opened - turn.sockets_before,
            voice_wait_ms=_round(kept.voice_wait_ms if kept else None),
            error=error,
        )
        self.reports.append(report)
        await self.sink.message(report)
        if self.turn is turn:
            self.turn = None
            await self.sink.message(p.State("idle", None))


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
