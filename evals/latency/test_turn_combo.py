"""Unit tests for the R3c strategy replay (``latency.turn_combo``)."""

from __future__ import annotations

import unittest
from typing import Any

from latency import turn_combo as tc
from latency.scoring import Pause


def _item(gaps: list[tuple[float, float]], partials: list[tuple[float, str]], speech_end: float) -> tc.Item:
    duration = speech_end + 100.0
    return tc.Item(
        id="f99",
        duration_ms=duration,
        speech_end_ms=speech_end,
        pauses=[Pause(start, end) for start, end in gaps if end - start >= 300.0],
        gaps=[tc.Gap(start, end) for start, end in gaps] + [tc.Gap(speech_end, duration + tc.PAD_MS)],
        partials=[tc.Partial(t, text) for t, text in partials],
    )


class TextRuleTests(unittest.TestCase):
    def test_connector_and_final(self) -> None:
        self.assertTrue(tc.ends_with_connector("我把杯子洗了，然后"))
        self.assertTrue(tc.ends_with_connector("就是， "))
        self.assertFalse(tc.ends_with_connector("我把杯子洗了。"))
        self.assertFalse(tc.ends_with_connector(""))
        self.assertTrue(tc.ends_with_final("你明天不是要面试吗"))
        self.assertTrue(tc.ends_with_final("好了。"))
        self.assertFalse(tc.ends_with_final("我今天"))

    def test_latest_uses_arrival_clock(self) -> None:
        partials = [tc.Partial(1000.0, "今天"), tc.Partial(1600.0, "今天早上")]
        self.assertEqual(tc.latest(partials, 999.0), "今天")  # R3b allows 1 ms of slack
        self.assertEqual(tc.latest(partials, 1598.0), "今天")
        self.assertEqual(tc.latest(partials, 1600.0), "今天早上")
        self.assertEqual(tc.latest(partials, 10.0), "")

    def test_frame_start_matches_centre_rule(self) -> None:
        self.assertEqual(tc._frame_start(3488.0), 3480.0)  # centre 3490 is past the gap start
        self.assertEqual(tc._frame_start(3500.0), 3500.0)
        self.assertEqual(tc._frame_start(3511.0), 3520.0)


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        # A 700 ms pause after "然后", then the real end at 4000 ms.
        self.item = _item(
            gaps=[(2000.0, 2700.0)],
            partials=[(1800.0, "我先把杯子擦了，然后"), (3800.0, "我先把杯子擦了，然后放好。")],
            speech_end=4000.0,
        )

    def _score(self, outcome: tc.Outcome) -> dict[str, object]:
        row = tc.score([self.item], [outcome])["rows"][0]
        assert isinstance(row, dict)
        return row

    def test_fixed_fires_in_pause(self) -> None:
        row = self._score(tc.walk(self.item, tc.fixed(500.0)))
        self.assertEqual(row["endpoint_ms"], 2500.0)
        self.assertTrue(row["false_barge_in"])
        self.assertEqual(row["where"], "labeled-pause")

    def test_adaptive_waits_after_connector(self) -> None:
        row = self._score(tc.walk(self.item, tc.adaptive(500.0, 1000.0)))
        self.assertFalse(row["false_barge_in"])
        self.assertEqual(row["endpoint_ms"], 4500.0)  # short threshold at the real end
        self.assertEqual(row["lag_ms"], 500.0)

    def test_judge_fires_on_stop_and_adds_cost(self) -> None:
        class Slow(tc.Judge):
            name = "slow"

            def complete(self, text: str) -> tuple[bool, float]:
                return text.endswith("。"), 12.5

        outcome = tc.walk(self.item, tc.judged(300.0, 1500.0, Slow()))
        row = self._score(outcome)
        self.assertFalse(row["false_barge_in"])  # "然后" is not a stop, so the pause is kept
        self.assertEqual(row["endpoint_ms"], 4300.0 + 12.5)
        self.assertEqual(len(outcome.judge_ms), 2)  # one call per new text

    def test_veto_skips_judge_on_connector(self) -> None:
        class Yes(tc.Judge):
            name = "yes"

            def complete(self, text: str) -> tuple[bool, float]:
                return True, 0.0

        self.assertTrue(self._score(tc.walk(self.item, tc.judged(300.0, 1500.0, Yes())))["false_barge_in"])
        vetoed = self._score(tc.walk(self.item, tc.judged(300.0, 1500.0, Yes(), veto_connectors=True)))
        self.assertFalse(vetoed["false_barge_in"])

    def test_cap_fires_without_yes(self) -> None:
        class No(tc.Judge):
            name = "no"

            def complete(self, text: str) -> tuple[bool, float]:
                return False, 0.0

        row = self._score(tc.walk(self.item, tc.judged(300.0, 600.0, No())))
        self.assertEqual(row["endpoint_ms"], 2600.0)
        self.assertTrue(row["false_barge_in"])

    def test_server_or_local(self) -> None:
        self.item.server = {"800": 4900.0, "300": None}
        first = tc.server_or_local("800", 1500.0, "first")(self.item)
        last = tc.server_or_local("800", 500.0, "last")(self.item)
        missing = tc.server_or_local("300", 1500.0, "first")(self.item)
        self.assertEqual(first.endpoint_ms, 4900.0)
        self.assertEqual(first.reason, "server")
        self.assertEqual(last.endpoint_ms, 4900.0)
        self.assertEqual(missing.reason, "local-only")
        self.assertEqual(missing.endpoint_ms, 5500.0)

    def test_best_respects_limit(self) -> None:
        points: list[dict[str, Any]] = [
            {"label": "a", "all": {"false_barge_in": 6, "lag_ms": {"p50": 100.0, "p95": 200.0}}},
            {"label": "b", "all": {"false_barge_in": 5, "lag_ms": {"p50": 900.0, "p95": 950.0}}},
            {"label": "c", "all": {"false_barge_in": 2, "lag_ms": {"p50": 900.0, "p95": 920.0}}},
        ]
        pick = tc.best(points, 5)
        assert pick is not None
        self.assertEqual(pick["label"], "c")
        self.assertIsNone(tc.best(points[:1], 5))
        tie: list[dict[str, Any]] = [
            {"label": "x", "all": {"false_barge_in": 5, "lag_ms": {"p50": 900.0, "p95": 920.0}}},
            {"label": "y", "all": {"false_barge_in": 4, "lag_ms": {"p50": 900.0, "p95": 920.0}}},
        ]
        pick = tc.best(tie, 5)
        assert pick is not None
        self.assertEqual(pick["label"], "y")


if __name__ == "__main__":
    unittest.main()
