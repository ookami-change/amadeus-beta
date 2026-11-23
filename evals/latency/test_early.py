"""Unit tests for R6b: the rate gate, the offline launch replay and the live runner on mocks."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator

from latency import early_live as el
from latency import early_plan as ep
from latency import turn_combo as tc
from latency.adapters.mock import MockASR, MockLLM, MockTTS
from latency.adapters.types import LLMItem
from latency.pcm import silence
from latency.ratelimit import RateGate, retry
from latency.scoring import Pause

TEXT = "店长，你来了。"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class RateGateTests(unittest.TestCase):
    def test_window_blocks_the_third_call(self) -> None:
        clock = FakeClock()
        gate = RateGate(2, clock=clock, sleep=clock.sleep)

        async def go() -> list[float]:
            waits = [await gate.acquire(), await gate.acquire()]
            clock.now = 10.0
            waits.append(await gate.acquire())
            return waits

        waits = asyncio.run(go())
        self.assertEqual(waits[:2], [0.0, 0.0])
        self.assertAlmostEqual(waits[2], 50.01, places=3)
        self.assertEqual(gate.acquired, 3)

    def test_reserve_does_not_take(self) -> None:
        clock = FakeClock()
        gate = RateGate(3, clock=clock, sleep=clock.sleep)

        async def go() -> float:
            await gate.acquire()
            await gate.acquire()
            return await gate.reserve(2)

        self.assertAlmostEqual(asyncio.run(go()), 60.01, places=3)
        self.assertEqual(gate.free(), 3)

    def test_retry_backs_off_on_quota_only(self) -> None:
        clock = FakeClock()
        calls = {"n": 0}

        async def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("minimax-tts: rate limit exceeded(RPM)")
            return "ok"

        self.assertEqual(asyncio.run(retry(flaky, sleep=clock.sleep)), "ok")
        self.assertEqual(clock.slept, [2.0, 4.0])

        async def broken() -> str:
            raise RuntimeError("bad voice id")

        with self.assertRaises(RuntimeError):
            asyncio.run(retry(broken, sleep=clock.sleep))


def _item(duration: float = 1000.0) -> tc.Item:
    """Speech 0-300, a 400 ms pause, speech 700-900, then silence."""
    return tc.Item(
        id="t99",
        duration_ms=duration,
        speech_end_ms=900.0,
        pauses=[Pause(300.0, 700.0)],
        gaps=[tc.Gap(300.0, 700.0), tc.Gap(900.0, duration + tc.PAD_MS)],
        partials=[tc.Partial(200.0, TEXT)],
    )


class SilenceGridTests(unittest.TestCase):
    def test_live_grid_matches_the_replay(self) -> None:
        item = _item()
        rule = tc.fixed(500.0)
        t = 0.0
        fired = None
        while fired is None and t < item.total_ms:
            t += tc.FRAME_MS
            quiet = el.silence_at(item, t)
            if quiet is not None and rule(item, t, quiet)[0]:
                fired = t
        self.assertEqual(fired, tc.walk(item, tc.fixed(500.0)).endpoint_ms)
        self.assertIsNone(el.silence_at(item, 760.0))


class PlanTests(unittest.TestCase):
    def test_cancel_then_keep_and_audible_cut(self) -> None:
        item = _item()
        commit = tc.walk(item, tc.fixed(500.0))
        plan = ep.plan(item, ep.LaunchRule(200.0), commit)
        self.assertEqual([one.fate for one in plan.launches], ["cancelled", "kept"])
        self.assertEqual(plan.sockets(warm=True), 1)  # cancelled 200 ms after launch, before its first clause
        # Launch at 1100 ms is ready at 1790 ms, after the 1400 ms commit, so hold waits for the audio.
        self.assertEqual(plan.outcome("hold"), (False, 1100.0 + ep.FIRST_AUDIO_MS - item.speech_end_ms))
        self.assertEqual(plan.outcome("early"), (False, 1100.0 + ep.FIRST_AUDIO_MS - item.speech_end_ms))
        early = ep.plan(item, ep.LaunchRule(20.0), commit)
        self.assertFalse(early.launches[0].heard())  # 360 ms is shorter than the first audio
        self.assertIsNone(ep.plan(item, None, commit).launches[0].retired_ms)


class SlowLLM(MockLLM):
    def __init__(self, delay_s: float) -> None:
        super().__init__(reply="嗯。店长。")
        self.delay_s = delay_s
        self.calls = 0

    def stream(self, *, system: str, history: list[dict[str, str]], user: str) -> AsyncIterator[LLMItem]:
        self.calls += 1
        return self._slow()

    async def _slow(self) -> AsyncIterator[LLMItem]:
        await asyncio.sleep(self.delay_s)
        async for piece in self._stream():
            yield piece


class CountingTTS(MockTTS):
    made = 0

    def __init__(self) -> None:
        super().__init__()
        CountingTTS.made += 1


class LiveRunnerTests(unittest.TestCase):
    def _run(self, arm: el.Arm, llm: SlowLLM) -> dict[str, object]:
        CountingTTS.made = 0
        turn = el.Turn(_item(), silence(1800.0), arm, tc.fixed(500.0), "你是克劳德。", [])
        voices = el.Voices(CountingTTS, RateGate(100), warm=arm.warm)
        return asyncio.run(el.run_turn(turn, asr=MockASR(transcript=TEXT, partial=TEXT), llm=llm, voices=voices))

    def test_hold_reuses_the_warm_socket_after_a_cancel(self) -> None:
        llm = SlowLLM(0.4)
        row = self._run(el.Arm("hold", "fixed500", 200.0, "hold", warm=True), llm)
        self.assertTrue(row["ok"], row["error"])
        self.assertEqual(row["cancelled"], 1)
        self.assertEqual(row["restarts"], 0)
        self.assertEqual(row["sockets"], 1)
        self.assertEqual(CountingTTS.made, 1)
        self.assertEqual(llm.calls, 2)
        self.assertFalse(row["false_barge_in"])
        heard, commit = row["heard_ms"], row["commit_wall_ms"]
        assert isinstance(heard, float) and isinstance(commit, float)
        self.assertGreaterEqual(heard, commit)
        attempts = row["attempts"]
        assert isinstance(attempts, list)
        self.assertEqual([a["fate"] for a in attempts], ["cancelled", "kept"])
        self.assertFalse(attempts[0]["spoke"])

    def test_early_play_counts_a_cut_in(self) -> None:
        row = self._run(el.Arm("early", "fixed500", 60.0, "early", warm=False), SlowLLM(0.0))
        self.assertTrue(row["ok"], row["error"])
        self.assertEqual(row["cut_in"], 1)
        self.assertTrue(row["false_barge_in"])
        self.assertFalse(row["commit_barge"])
        self.assertEqual(row["sockets"], 2)

    def test_breakdown_is_relative_to_speech_end(self) -> None:
        row = self._run(el.Arm("hold", "fixed500", 200.0, "hold", warm=True), SlowLLM(0.0))
        parts = el.breakdown(row)
        assert parts["heard"] is not None and parts["launch"] is not None
        self.assertAlmostEqual(parts["launch"], 200.0, delta=60.0)
        self.assertGreaterEqual(parts["heard"], 480.0)
        summary = el.summarize([row])
        self.assertEqual(summary["ok"], 1)


if __name__ == "__main__":
    unittest.main()
