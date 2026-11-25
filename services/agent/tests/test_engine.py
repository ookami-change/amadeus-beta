"""The turn engine on scripted VAD and in-process vendors."""

from __future__ import annotations

import asyncio
import math
import struct
import unittest
from collections.abc import Callable

from agent import protocol as p
from agent.engine import FALLBACK_LINE, Engine, TurnConfig
from agent.ratelimit import RateGate
from agent.vad import HOP_MS, SpeechTracker
from agent.vendors.mock import MockASR, MockLLM, MockVoice
from agent.voices import Voices

FRAME_MS = 20.0
FRAME = b"\x00\x00" * 320


def scripted(speech: list[tuple[float, float]]) -> Callable[[bytes], float]:
    """A VAD that says 'speech' for hops whose start falls in one of the ranges."""
    hops = {"n": 0}

    def probability(hop: bytes) -> float:
        start = hops["n"] * HOP_MS
        hops["n"] += 1
        return 1.0 if any(a <= start < b for a, b in speech) else 0.0

    return probability


class Sink:
    def __init__(self) -> None:
        self.events: list[p.Message | tuple[str, int, int]] = []

    async def message(self, message: p.Message) -> None:
        self.events.append(message)

    async def audio(self, utterance: int, pcm: bytes) -> None:
        self.events.append(("audio", utterance, len(pcm)))

    def of(self, kind: type[p.Message]) -> list[p.Message]:
        return [e for e in self.events if isinstance(e, kind)]

    def index(self, pred: Callable[[object], bool]) -> int:
        return next(i for i, e in enumerate(self.events) if pred(e))


class Rig:
    def __init__(
        self,
        speech: list[tuple[float, float]],
        script: Callable[[float], str],
        *,
        llm: MockLLM | None = None,
        voice_delay_s: float = 0.0,
        config: TurnConfig | None = None,
    ) -> None:
        MockVoice.opened = 0
        MockVoice.failures = []
        self.sink = Sink()
        self.asr = MockASR(script)
        self.llm = llm or MockLLM(reply="嗯。店长，你来了。")
        self.made: list[MockVoice] = []

        def make() -> MockVoice:
            voice = MockVoice(first_audio_s=voice_delay_s)
            self.made.append(voice)
            return voice

        self.voices = Voices(make, RateGate(100), backoff_s=0.0)
        self.engine = Engine(
            asr=self.asr,
            llm=self.llm,
            voices=self.voices,
            tracker=SpeechTracker(scripted(speech)),
            sink=self.sink,
            system="你是克劳德。",
            config=config or TurnConfig(retry_wait_s=0.0),
            clock=lambda: 0.0,  # the session clock is the audio clock
        )

    async def run(self, total_ms: float, *, wait_reports: int = 1, settle_s: float = 3.0) -> None:
        await self.engine.start()
        t = 0.0
        while t < total_ms:
            await self.engine.feed(FRAME)
            t += FRAME_MS
            for _ in range(4):
                await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settle_s
        while len(self.sink.of(p.TurnReport)) < wait_reports and loop.time() < deadline:
            await asyncio.sleep(0.01)
        await self.engine.close()
        await self.voices.close()

    def report(self, index: int = 0) -> p.TurnReport:
        found = self.sink.of(p.TurnReport)[index]
        assert isinstance(found, p.TurnReport)
        return found


class EngineTests(unittest.TestCase):
    def test_hold_until_commit_and_reuse_the_warm_socket(self) -> None:
        rig = Rig([(0, 1000)], lambda ms: "店长，你来了。" if ms > 300 else "")
        asyncio.run(rig.run(3200))
        report = rig.report()
        self.assertIsNone(report.error)
        self.assertEqual((report.launches, report.cancelled, report.restarts), (1, 0, 0))
        assert (
            report.commit_ms is not None
            and report.launch_ms is not None
            and report.ready_ms is not None
        )
        assert report.speak_ms is not None
        # Speech ends at 1024 ms (hop grid); launch at +400 ms, commit at +1400 ms.
        self.assertAlmostEqual(report.launch_ms, 1024 + 400, delta=FRAME_MS)
        self.assertAlmostEqual(report.commit_ms, 1024 + 1400, delta=FRAME_MS)
        self.assertLess(report.ready_ms, report.commit_ms)  # prepared early...
        self.assertGreaterEqual(report.speak_ms, report.commit_ms)  # ...but held until the commit
        first_audio = rig.sink.index(lambda e: isinstance(e, tuple))
        prepare = rig.sink.index(lambda e: isinstance(e, p.Prepare))
        self.assertLess(prepare, first_audio)
        self.assertEqual(MockVoice.opened, 2)  # the warm one spoke; the next spare was prefetched
        self.assertEqual(rig.llm.calls, ["店长，你来了。"])
        texts = [e.text for e in rig.sink.of(p.SpeakText) if isinstance(e, p.SpeakText)]
        self.assertEqual(texts, ["嗯。", "店长，你来了。"])
        self.assertEqual(
            rig.engine.history[-1], {"role": "assistant", "content": "嗯。店长，你来了。"}
        )

    def test_speech_resuming_cancels_the_speculation(self) -> None:
        rig = Rig(
            [(0, 800), (1500, 2100)],
            lambda ms: "我想想，" if ms < 1600 else "我想想，明天再说。",
        )
        asyncio.run(rig.run(4500))
        report = rig.report()
        self.assertIsNone(report.error)
        self.assertEqual((report.launches, report.cancelled, report.restarts), (2, 1, 0))
        self.assertEqual(rig.llm.calls, ["我想想，", "我想想，明天再说。"])
        fates = [r for r in rig.sink.of(p.SpeakStart)]
        self.assertEqual(len(fates), 1)  # the cancelled reply was never played
        # It was cancelled at 1500 ms, before the synthesis point (commit - 400 ms),
        # so it never took the socket: the warm one spoke the kept reply.
        self.assertEqual(len([v for v in rig.made if v.said]), 1)
        self.assertEqual(MockVoice.opened, 2)
        self.assertEqual(report.sockets, 1)  # the spare opened after the reply

    def test_speculation_waits_for_the_synthesis_point(self) -> None:
        rig = Rig([(0, 1000)], lambda ms: "店长，你来了。" if ms > 300 else "")
        asyncio.run(rig.run(3200))
        report = rig.report()
        assert report.ready_ms is not None and report.commit_ms is not None
        # Thought at +400 ms, but synthesized only from +1000 ms (1400 - 400).
        self.assertGreaterEqual(report.ready_ms, 1024 + 1000 - FRAME_MS)
        self.assertLess(report.ready_ms, report.commit_ms)
        self.assertIsNotNone(report.voice_wait_ms)

    def test_partial_that_lagged_restarts_at_commit(self) -> None:
        rig = Rig([(0, 600)], lambda ms: "好" if ms < 1300 else "好的。")
        asyncio.run(rig.run(2600))
        report = rig.report()
        self.assertEqual((report.launches, report.restarts), (2, 1))
        self.assertEqual(rig.llm.calls, ["好", "好的。"])
        self.assertEqual(report.commit_text, "好的。")

    def test_connector_waits_for_the_long_silence(self) -> None:
        rig = Rig([(0, 600)], lambda ms: "然后" if ms > 200 else "")
        asyncio.run(rig.run(3000))
        report = rig.report()
        assert report.commit_ms is not None
        self.assertAlmostEqual(report.commit_ms, 608 + 1800, delta=FRAME_MS)

    def test_quota_error_on_open_is_retried(self) -> None:
        rig = Rig([(0, 600)], lambda ms: "你好。")
        MockVoice.failures = ["minimax-tts: rate limit exceeded(RPM)"]
        asyncio.run(rig.run(2600))
        self.assertIsNone(rig.report().error)
        self.assertEqual(len(rig.voices.retries), 1)

    def test_llm_failure_says_the_fallback_line(self) -> None:
        rig = Rig([(0, 600)], lambda ms: "你好。", llm=MockLLM(fail="HTTP 400 bad request"))
        asyncio.run(rig.run(2600))
        self.assertIsNone(rig.report().error)
        errors = rig.sink.of(p.Error)
        self.assertTrue(errors)
        texts = [e.text for e in rig.sink.of(p.SpeakText) if isinstance(e, p.SpeakText)]
        self.assertEqual("".join(texts), FALLBACK_LINE)

    def test_talking_over_her_stops_the_audio_and_starts_a_turn(self) -> None:
        rig = Rig(
            [(0, 600), (2200, 2800)],
            lambda ms: "你好。",
            llm=MockLLM(reply="嗯。" * 3),
            voice_delay_s=0.3,
            config=TurnConfig(launch_ms=None, retry_wait_s=0.0),
        )

        async def go() -> None:
            await rig.engine.start()
            t = 0.0
            while t < 5000:
                await rig.engine.feed(FRAME)
                t += FRAME_MS
                if 2100 <= t < 2120:
                    await asyncio.sleep(0.4)  # let the first reply start playing
                for _ in range(4):
                    await asyncio.sleep(0)
            await asyncio.sleep(1.0)
            await rig.engine.close()
            await rig.voices.close()

        asyncio.run(go())
        reports = rig.sink.of(p.TurnReport)
        self.assertGreaterEqual(len(reports), 2)
        first = reports[0]
        assert isinstance(first, p.TurnReport)
        self.assertEqual(first.error, "interrupted")
        self.assertTrue(rig.sink.of(p.StopAudio))
        ends = [e.reason for e in rig.sink.of(p.SpeakEnd) if isinstance(e, p.SpeakEnd)]
        self.assertIn("interrupted", ends)


class TrackerTests(unittest.TestCase):
    def feed(
        self, speech: list[tuple[float, float]], total_ms: float
    ) -> tuple[SpeechTracker, list[str]]:
        tracker = SpeechTracker(scripted(speech))
        events: list[str] = []
        for _ in range(int(total_ms / FRAME_MS)):
            events += tracker.feed(FRAME)
        return tracker, events

    def test_blips_shorter_than_100_ms_are_ignored(self) -> None:
        tracker, events = self.feed([(0, 500), (1000, 1064)], 1500)
        self.assertEqual(events, ["onset", "offset"])
        self.assertAlmostEqual(tracker.silence_ms() or 0, 1500 - 512, delta=HOP_MS)

    def test_gaps_under_80_ms_do_not_split_speech(self) -> None:
        tracker, events = self.feed([(0, 300), (340, 600)], 1000)
        self.assertEqual(events.count("onset"), 1)
        self.assertAlmostEqual(tracker.speech_end_ms or 0, 608, delta=HOP_MS)


class GateTests(unittest.TestCase):
    def test_window(self) -> None:
        now = {"t": 0.0}
        slept: list[float] = []

        async def sleep(s: float) -> None:
            slept.append(s)
            now["t"] += s

        gate = RateGate(2, clock=lambda: now["t"], sleep=sleep)

        async def go() -> list[float]:
            waits = [await gate.acquire(), await gate.acquire()]
            now["t"] = 10.0
            waits.append(await gate.acquire())
            return waits

        waits = asyncio.run(go())
        self.assertEqual(waits[:2], [0.0, 0.0])
        self.assertTrue(math.isclose(waits[2], 50.01, abs_tol=1e-3))


class VoicesTests(unittest.TestCase):
    def test_unspoken_socket_is_reused_and_stale_spare_replaced(self) -> None:
        now = {"t": 0.0}

        async def go() -> tuple[str, str, str, int]:
            MockVoice.opened = 0
            MockVoice.failures = []
            voices = Voices(MockVoice, RateGate(100), clock=lambda: now["t"])
            first, how1 = await voices.take()
            await voices.give_back(first, spoke=False)
            again, how2 = await voices.take()
            assert again is first
            await voices.give_back(again, spoke=False)
            now["t"] = 120.0
            await voices.refresh()
            fresh, how3 = await voices.take()
            assert fresh is not first
            await voices.close()
            return how1, how2, how3, MockVoice.opened

        how1, how2, how3, opened = asyncio.run(go())
        self.assertEqual((how1, how2, how3), ("new", "warm", "prefetched"))
        self.assertEqual(opened, 2)


def tone(ms: float, amplitude: int = 3000) -> bytes:
    n = int(ms * 16)
    return b"".join(struct.pack("<h", int(amplitude * math.sin(i * 0.2))) for i in range(n))


if __name__ == "__main__":
    unittest.main()
