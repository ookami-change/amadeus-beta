"""The Python binding against packages/protocol/spec.json and fixtures.json."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent import protocol as p

ROOT = Path(__file__).resolve().parents[3] / "packages" / "protocol"
SPEC = json.loads((ROOT / "spec.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))


class SpecTests(unittest.TestCase):
    def test_version_and_rates(self) -> None:
        self.assertEqual(p.PROTOCOL_VERSION, SPEC["version"])
        self.assertEqual(p.MIC_TAG, SPEC["binary"]["mic"]["tag"])
        self.assertEqual(p.SPEECH_TAG, SPEC["binary"]["speech"]["tag"])
        self.assertEqual(p.MIC_SAMPLE_RATE, SPEC["binary"]["mic"]["sample_rate"])
        self.assertEqual(p.SPEECH_SAMPLE_RATE, SPEC["binary"]["speech"]["sample_rate"])

    def test_every_message_matches_the_spec(self) -> None:
        self.assertEqual(sorted(p.MESSAGES), sorted(SPEC["messages"]))
        for name, cls in p.MESSAGES.items():
            with self.subTest(name):
                self.assertEqual(cls.FROM, SPEC["messages"][name]["from"])
                self.assertEqual(p.field_kinds(cls), SPEC["messages"][name]["fields"])
        self.assertEqual(p.field_kinds(p.LipsyncMark), SPEC["structs"]["lipsync_mark"])

    def test_fixtures_round_trip(self) -> None:
        for raw in FIXTURES:
            with self.subTest(raw["type"]):
                message = p.parse(json.dumps(raw, ensure_ascii=False))
                self.assertEqual(json.loads(message.to_json()), raw)

    def test_bad_messages(self) -> None:
        for text in (
            '{"type":"nope"}',
            '{"type":"ping","id":1.5,"client_ms":0}',
            '{"type":"ping","id":true,"client_ms":0}',
            '{"type":"state","state":"asleep","turn":null}',
            '{"type":"transcript","turn":1,"text":"x"}',
            '{"type":"lipsync","utterance":1,"marks":[{"start_ms":0}]}',
            "[1]",
            "{",
        ):
            with self.subTest(text), self.assertRaises(p.ProtocolError):
                p.parse(text)

    def test_binary(self) -> None:
        self.assertEqual(p.mic_pcm(bytes([1, 1, 0])), b"\x01\x00")
        with self.assertRaises(p.ProtocolError):
            p.mic_pcm(b"\x02\x00")
        self.assertEqual(p.speech_frame(7, b"\x01\x00"), bytes([2, 7, 0, 0, 0, 1, 0]))


if __name__ == "__main__":
    unittest.main()
