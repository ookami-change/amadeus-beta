"""The WebSocket server end to end, on mock vendors and the energy VAD."""

from __future__ import annotations

import asyncio
import json
import math
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from agent import protocol as p
from agent.engine import TurnConfig
from agent.server import Vendors, session
from agent.settings import Settings


def tone(ms: float, amplitude: int = 3000) -> bytes:
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(i * 0.2))) for i in range(int(ms * 16))
    )


class ServerTests(unittest.TestCase):
    def test_one_turn_over_a_socket(self) -> None:
        async def go(log_dir: Path) -> tuple[list[p.Message], int]:
            vendors = Vendors(Settings.from_environ(), mock=True)

            async def handler(ws: ServerConnection) -> None:
                await session(
                    ws,
                    vendors,
                    system="你是克劳德。",
                    history=[],
                    config=TurnConfig(),
                    log_dir=log_dir,
                )

            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(f"ws://127.0.0.1:{port}") as ws:
                    await ws.send(p.Hello(p.PROTOCOL_VERSION, "test", 16000).to_json())
                    welcome = p.parse(await ws.recv())
                    assert isinstance(welcome, p.Welcome)
                    audio = tone(800) + b"\x00\x00" * 16 * 2500
                    for offset in range(0, len(audio), 640):
                        await ws.send(bytes([p.MIC_TAG]) + audio[offset : offset + 640])
                        await asyncio.sleep(
                            0.002
                        )  # a real client streams; a burst starves the ASR reader
                    await ws.send(p.Ping(1, 0.0).to_json())
                    seen: list[p.Message] = []
                    speech = 0
                    while not any(isinstance(m, p.TurnReport) for m in seen):
                        frame = await asyncio.wait_for(ws.recv(), timeout=5)
                        if isinstance(frame, bytes):
                            assert frame[0] == p.SPEECH_TAG
                            speech += len(frame) - 5
                        else:
                            seen.append(p.parse(frame))
                    await ws.send(p.Bye().to_json())
            return seen, speech

        with TemporaryDirectory() as tmp:
            seen, speech = asyncio.run(go(Path(tmp)))
            logs = list(Path(tmp).glob("*.jsonl"))
            self.assertEqual(len(logs), 1)
            rows = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
        kinds = [m.TYPE for m in seen]
        self.assertIn("pong", kinds)
        self.assertLess(kinds.index("prepare"), kinds.index("speak_start"))
        report = next(m for m in seen if isinstance(m, p.TurnReport))
        self.assertIsNone(report.error)
        self.assertEqual(report.commit_text, "店长，你来了。")
        self.assertGreater(speech, 0)
        self.assertEqual([r["type"] for r in rows], ["turn_report"])


if __name__ == "__main__":
    unittest.main()
