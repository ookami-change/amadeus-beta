"""Harness tests. They do not call vendors and do not need the generated corpus."""

from __future__ import annotations

import asyncio
import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from latency.adapters.mock import MockASR, MockLLM, MockTTS
from latency.adapters.types import retryable_error
from latency.adapters.volc_frame import (
    build_asr_audio,
    build_asr_full_client,
    build_tts_event,
    parse_frame,
)
from latency.clauses import ClauseBuffer
from latency.corpus import Item, Segment
from latency.corpus_script import SCRIPT, counts
from latency.endpoint import Endpointer, EnergyGate, KeywordCompleteness
from latency.pcm import frames
from latency.pipeline import endpoint_trial, realtime_trial
from latency.prompts import load_persona
from latency.scoring import Pause, classify_endpoint, percentile
from latency.turn_models import _normalize_livekit
from latency.turn_script import TURNS, validate
from tts.lines import DOUBAO_TUNED, LINES, MINIMAX_TUNED
from tts.voices import DESIGNS, PREVIEW_TEXT, V2_CLEAN, instruction_units


def _tone(ms: int, amplitude: int = 9000) -> bytes:
    count = 16000 * ms // 1000
    out = bytearray()
    for index in range(count):
        sample = int(amplitude * __import__("math").sin(2 * 3.14159265 * 220 * index / 16000))
        out += struct.pack("<h", sample)
    return bytes(out)


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * (16000 * ms // 1000)


def _write(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm)


class ProtocolTests(unittest.TestCase):
    def test_tts_start_connection_matches_the_documented_layout(self) -> None:
        frame = build_tts_event(1, {})
        self.assertEqual(frame[:8].hex(), "1114100000000001")
        parsed = parse_frame(frame)
        self.assertEqual(parsed.event, 1)
        self.assertIsNone(parsed.session_id)
        self.assertEqual(parsed.json_payload(), {})

    def test_tts_session_roundtrip(self) -> None:
        frame = build_tts_event(100, {"req_params": {"text": "店长"}}, session_id="abc")
        parsed = parse_frame(frame)
        self.assertEqual(parsed.event, 100)
        self.assertEqual(parsed.session_id, "abc")
        body = parsed.json_payload()
        req = body["req_params"]
        self.assertIsInstance(req, dict)
        assert isinstance(req, dict)
        self.assertEqual(req["text"], "店长")

    def test_asr_request_roundtrip(self) -> None:
        frame = build_asr_full_client({"request": {"model_name": "bigmodel"}})
        parsed = parse_frame(frame)
        self.assertEqual(parsed.json_payload()["request"], {"model_name": "bigmodel"})
        audio = build_asr_audio(b"\x01\x02", sequence=3, last=True)
        parsed_audio = parse_frame(audio)
        self.assertIsNone(parsed_audio.sequence)
        self.assertEqual(parsed_audio.flags, 0x2)
        self.assertEqual(parsed_audio.payload, b"\x01\x02")


class SplitterTests(unittest.TestCase):
    def test_hard_and_soft_cuts(self) -> None:
        buffer = ClauseBuffer()
        self.assertEqual(buffer.push("嗯。下一句"), ["嗯。"])
        self.assertEqual(buffer.push("还没说完，"), ["下一句还没说完，"])
        self.assertEqual(buffer.flush(), None)

    def test_short_comma_does_not_cut(self) -> None:
        buffer = ClauseBuffer()
        self.assertEqual(buffer.push("嗯，"), [])
        self.assertEqual(buffer.flush(), "嗯，")


class EndpointTests(unittest.TestCase):
    def test_fixed_threshold_false_barges_on_a_long_pause(self) -> None:
        pcm = _tone(400) + _silence(760) + _tone(400) + _silence(1200)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pause.wav"
            _write(path, pcm)
            item = _pause_item(path)
            trial = endpoint_trial(item, mode="fixed", model=None)
        self.assertTrue(trial.ok)
        self.assertTrue(trial.false_barge_in)
        assert trial.t_endpoint is not None
        self.assertAlmostEqual(trial.t_endpoint, 900.0, delta=20.0)

    def test_semantic_model_holds_through_the_pause(self) -> None:
        pcm = _tone(400) + _silence(760) + _tone(400) + _silence(1200)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pause.wav"
            _write(path, pcm)
            item = _pause_item(path)
            trial = endpoint_trial(item, mode="semantic", model=KeywordCompleteness())
        self.assertTrue(trial.ok)
        self.assertFalse(trial.false_barge_in)
        self.assertFalse(trial.early)
        assert trial.t_endpoint is not None
        self.assertGreater(trial.t_endpoint, item.speech_end_ms)

    def test_energy_gate_ignores_exact_silence(self) -> None:
        gate = EnergyGate()
        self.assertFalse(gate.speech(_silence(20)))
        self.assertTrue(gate.speech(_tone(20)))


class TurnScriptTests(unittest.TestCase):
    def test_twenty_pauses_sit_on_a_connector(self) -> None:
        validate()
        self.assertEqual(len(TURNS), 20)
        self.assertTrue(all(400 <= line.pause_ms <= 1000 for line in TURNS))

    def test_livekit_normalization_drops_chinese_punctuation(self) -> None:
        self.assertEqual(_normalize_livekit("然后，就是。"), "然后就是")


class VoiceDesignTests(unittest.TestCase):
    def test_prompts_fit_the_documented_limits(self) -> None:
        self.assertEqual(len(DESIGNS), 3)
        self.assertTrue(15 <= len(PREVIEW_TEXT) <= 200)
        for design in DESIGNS:
            self.assertLessEqual(len(design.prompt), 500)
            self.assertLessEqual(len(design.prefix), 10)
            self.assertTrue(design.prefix.isalnum())
        self.assertEqual(set(DOUBAO_TUNED), {line.number for line in LINES})
        self.assertTrue(all(instruction_units(text) <= 100 for text in DOUBAO_TUNED.values()))
        self.assertLessEqual(len(V2_CLEAN.prompt), 500)
        self.assertLessEqual(len(V2_CLEAN.prefix), 10)
        self.assertNotIn(V2_CLEAN, DESIGNS)

    def test_minimax_tunes_keep_the_words(self) -> None:
        import re

        from latency.adapters.tts_minimax import MiniMaxTTS

        self.assertEqual([take.number for take in MINIMAX_TUNED], list(range(1, 15)))
        allowed = {"happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm", "fluent"}
        by_number = {line.number: line for line in LINES}
        focus = {1, 7, 10, 11, 14}
        for take in MINIMAX_TUNED:
            line = by_number[take.number]
            self.assertIn(take.emotion, allowed)
            self.assertTrue(-12 <= take.pitch <= 12)
            self.assertTrue(0.5 <= take.speed <= 2)
            self.assertTrue(0 < take.volume <= 10)
            bare = re.sub(r"[^\w]", "", take.text, flags=re.UNICODE)
            self.assertEqual(bare, re.sub(r"[^\w]", "", line.text, flags=re.UNICODE))
            if take.number in focus:
                changed = (
                    take.emotion != line.emotion
                    or take.speed != line.speed
                    or take.volume != line.volume
                    or take.pitch != 0
                    or take.text != line.text
                )
                self.assertTrue(changed)
        with self.assertRaises(ValueError):
            MiniMaxTTS(object(), pitch=13)  # type: ignore[arg-type]


class RetryTests(unittest.TestCase):
    def test_minimax_rpm_text_is_retried(self) -> None:
        self.assertTrue(retryable_error("minimax-tts: rate limit exceeded(RPM)"))
        self.assertFalse(retryable_error("volc-asr: no close frame received or sent"))


class ScoreTests(unittest.TestCase):
    def test_percentile_nearest_rank(self) -> None:
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(percentile([1, 2, 3, 4], 95), 4)
        self.assertIsNone(percentile([], 50))

    def test_false_barge_requires_the_labeled_pause(self) -> None:
        pauses = [Pause(400, 1160)]
        early, barge = classify_endpoint(speech_end_ms=1560, pauses=pauses, t_endpoint_ms=900)
        self.assertTrue(early and barge)
        early, barge = classify_endpoint(speech_end_ms=1560, pauses=pauses, t_endpoint_ms=1760)
        self.assertFalse(early or barge)


class PersonaTests(unittest.TestCase):
    def test_prompt_is_about_fifteen_hundred_characters(self) -> None:
        text = load_persona()
        length = len("".join(text.split()))
        self.assertGreaterEqual(length, 1400)
        self.assertLessEqual(length, 1700)

    def test_script_covers_the_required_mix(self) -> None:
        self.assertEqual(counts(), {"greeting": 3, "statement": 8, "pause": 5, "question": 4})
        self.assertEqual(len(SCRIPT), 20)


class RealtimeTests(unittest.TestCase):
    def test_mock_cascade_records_ordered_timestamps(self) -> None:
        pcm = _tone(200) + _silence(700)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hi.wav"
            _write(path, pcm)
            item = Item(
                item_id="01",
                category="greeting",
                text="店长，你来了。",
                audio=path,
                speech_end_ms=200,
                segments=(Segment("店长，你来了。", 0, 200),),
                pauses=(),
            )
            asr = MockASR(transcript=item.text)
            trial = asyncio.run(
                realtime_trial(
                    item,
                    combo="mock/mock/mock",
                    mode="fixed",
                    repeat=1,
                    asr=asr,
                    llm=MockLLM(),
                    tts=MockTTS(),
                    system="你是克劳德。",
                    history=[],
                    model=None,
                    oracle_partials=False,
                    mock=True,
                    instruction="",
                )
            )
        self.assertTrue(trial.ok, trial.error)
        self.assertFalse(trial.false_barge_in)
        assert trial.t_endpoint is not None
        assert trial.t_llm_first_token is not None
        assert trial.t_llm_first_clause is not None
        assert trial.t_tts_first_audio is not None
        self.assertLess(trial.t_speech_end, trial.t_endpoint)
        self.assertLess(trial.t_endpoint, trial.t_tts_first_audio)
        self.assertLessEqual(trial.t_llm_first_token, trial.t_llm_first_clause)
        self.assertIn("店长", trial.transcript)
        # The fixed endpointer must not look ahead of the frame grid.
        self.assertEqual(len(frames(pcm, 20)), (200 + 700) // 20)


class EndpointerUnitTests(unittest.TestCase):
    def test_semantic_does_not_fire_on_an_empty_partial(self) -> None:
        endpointer = Endpointer("semantic", KeywordCompleteness(), arm_ms=20)
        decision = None
        for end in range(20, 420, 20):
            decision = endpointer.push(float(end), end <= 200, "")
        self.assertIsNone(decision)


class RecordingListTests(unittest.TestCase):
    def test_list_covers_the_script_and_the_two_extra_sets(self) -> None:
        path = Path(__file__).resolve().parent / "turns" / "record" / "list.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        scripted = [row for row in rows if row["kind"] == "scripted"]
        self.assertEqual([row["id"] for row in scripted], [line.item_id for line in TURNS])
        for row, line in zip(scripted, TURNS, strict=True):
            self.assertEqual(row["text"], line.text)
            self.assertTrue("然后" in row["note"] or "就是" in row["note"])
        self.assertEqual(sum(row["kind"] == "free" for row in rows), 20)
        self.assertEqual(sum(row["kind"] == "short" for row in rows), 10)
        self.assertTrue(all(row["text"] == "" for row in rows if row["kind"] == "free"))
        self.assertTrue(all(row["text"] and "不要停" in row["note"] for row in rows if row["kind"] == "short"))
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        page = (path.parent / "index.html").read_text(encoding="utf-8")
        self.assertIn("16000", page)
        self.assertIn("list.json", page)


def _pause_item(path: Path) -> Item:
    first = "理论上这样写是对的，只是"
    second = "没有人会真的这么做。"
    return Item(
        item_id="12",
        category="pause",
        text=first + second,
        audio=path,
        speech_end_ms=1560,
        segments=(Segment(first, 0, 400), Segment(second, 1160, 1560)),
        pauses=(Pause(400, 1160),),
    )


if __name__ == "__main__":
    unittest.main()
