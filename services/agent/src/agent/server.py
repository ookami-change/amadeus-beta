"""WebSocket server: one ``Engine`` per connection.

    python -m agent.server --port 8765            # real vendors, keys from the environment / .env
    python -m agent.server --port 8765 --mock     # in-process vendors, energy VAD, costs nothing

Protocol: ``packages/protocol/spec.json``. Each session writes its turn reports
and errors as JSON lines under ``--log-dir`` (transcripts included, so the
directory stays on the server and out of git). Audio is never written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

import httpx
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from agent import protocol as p
from agent.engine import Engine, TurnConfig
from agent.ratelimit import RateGate
from agent.settings import AGENT_ROOT, Settings, load_dotenv
from agent.vad import SileroVAD, SpeechTracker
from agent.vendors.asr_volc import VolcASR
from agent.vendors.llm_qwen import QwenLLM, http_client
from agent.vendors.mock import MockASR, MockLLM, MockVoice
from agent.vendors.tts_minimax import MiniMaxVoice
from agent.vendors.types import ASR, LLM, Voice
from agent.voices import Voices

PERSONA = AGENT_ROOT / "src" / "agent" / "prompts" / "persona.md"
log = logging.getLogger("agent")


def energy_probability(hop: bytes) -> float:
    """A stand-in for Silero in ``--mock`` runs: loud is speech."""
    import numpy as np

    samples = np.frombuffer(hop, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
    return 1.0 if rms > 500.0 else 0.0


class Vendors:
    """Process-wide clients: one HTTP pool for the LLM, one MiniMax rate gate and socket pool."""

    def __init__(self, settings: Settings, *, mock: bool) -> None:
        self.mock = mock
        self.settings = settings
        # 1.5 s of slack on the window: at 10/min with no slack MiniMax still
        # refused 3 opens in the first M1-2 replay, so its clock and ours differ.
        self.gate = RateGate(settings.minimax_rpm if not mock else 600, window_s=61.5)
        self.http: httpx.AsyncClient | None = None
        self.llm: LLM
        self.asr: ASR
        if mock:
            self.asr = MockASR(lambda heard_ms: "店长，你来了。" if heard_ms > 300 else "")
            self.llm = MockLLM(first_token_s=0.3)
        else:
            self.http = http_client()
            self.asr = VolcASR(settings)
            self.llm = QwenLLM(settings, self.http)
        # One warm socket for the process, not one per session: a session that
        # ends would otherwise throw away a socket it paid quota for.
        self.voices = Voices(self.voice, self.gate)

    def voice(self) -> Voice:
        if self.mock:
            return MockVoice(first_audio_s=0.15)
        return MiniMaxVoice(self.settings)

    def probability(self) -> Callable[[bytes], float]:
        if self.mock:
            return energy_probability
        return SileroVAD(self.settings.vad_model).probability

    async def warm(self) -> None:
        if isinstance(self.llm, QwenLLM):
            await self.llm.warm()

    async def close(self) -> None:
        await self.voices.close()
        if self.http is not None:
            await self.http.aclose()


class WsSink:
    def __init__(self, ws: ServerConnection, log_file: TextIO | None) -> None:
        self.ws = ws
        self.log_file = log_file

    async def message(self, message: p.Message) -> None:
        if self.log_file is not None and isinstance(message, (p.TurnReport, p.Error)):
            row = {"type": message.TYPE, "at": time.time(), **asdict(message)}
            self.log_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.log_file.flush()
        try:
            await self.ws.send(message.to_json())
        except ConnectionClosed:
            pass

    async def audio(self, utterance: int, pcm: bytes) -> None:
        try:
            await self.ws.send(p.speech_frame(utterance, pcm))
        except ConnectionClosed:
            pass


async def session(
    ws: ServerConnection,
    vendors: Vendors,
    *,
    system: str,
    history: list[dict[str, str]],
    config: TurnConfig,
    log_dir: Path | None,
) -> None:
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=10)
        hello = p.parse(first)
    except (TimeoutError, p.ProtocolError, ConnectionClosed) as exc:
        await ws.close(1002, f"expected hello: {exc}"[:120])
        return
    if not isinstance(hello, p.Hello) or hello.sample_rate != p.MIC_SAMPLE_RATE:
        await ws.close(1002, "expected hello at 16 kHz")
        return
    session_id = uuid.uuid4().hex[:12]
    log_file = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{session_id}.jsonl").open(
            "a", encoding="utf-8"
        )
    sink = WsSink(ws, log_file)
    engine = Engine(
        asr=vendors.asr,
        llm=vendors.llm,
        voices=vendors.voices,
        tracker=SpeechTracker(vendors.probability()),
        sink=sink,
        system=system,
        history=history,
        config=config,
    )
    log.info("session %s from %s (%s)", session_id, ws.remote_address, hello.client)
    try:
        await ws.send(p.Welcome(p.PROTOCOL_VERSION, session_id, p.SPEECH_SAMPLE_RATE).to_json())
        await engine.start()
        async for frame in ws:
            if isinstance(frame, bytes):
                await engine.feed(p.mic_pcm(frame))
                continue
            message = p.parse(frame)
            if isinstance(message, p.Ping):
                await ws.send(
                    p.Pong(message.id, message.client_ms, round(engine.now_ms(), 1)).to_json()
                )
            elif isinstance(message, p.Bye):
                break
            # Playback reports feed history trimming in M1-3; they are accepted and ignored here.
    except p.ProtocolError as exc:
        await sink.message(p.Error("protocol", str(exc), False))
    except ConnectionClosed:
        pass
    finally:
        await engine.close()
        if log_file is not None:
            log_file.close()
        log.info("session %s closed", session_id)


async def main_async(args: argparse.Namespace) -> None:
    load_dotenv()
    settings = Settings.from_environ()
    if not args.mock:
        missing = settings.missing()
        if missing:
            raise SystemExit("missing " + ", ".join(missing))
        if not settings.vad_model.is_file():
            raise SystemExit(
                f"VAD model not found: {settings.vad_model} (run scripts/download_models.sh)"
            )
    vendors = Vendors(settings, mock=args.mock)
    await vendors.warm()
    system = PERSONA.read_text(encoding="utf-8")
    history: list[dict[str, str]] = []
    if args.history:
        history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    config = TurnConfig(launch_ms=None if args.serial else args.launch_ms)

    async def handler(ws: ServerConnection) -> None:
        await session(
            ws, vendors, system=system, history=history, config=config, log_dir=args.log_dir
        )

    async with serve(handler, args.host, args.port, max_size=1 << 20) as server:
        log.info("listening on %s:%s (%s)", args.host, args.port, "mock" if args.mock else "live")
        try:
            await server.serve_forever()
        finally:
            await vendors.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Amadeus conversation server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mock", action="store_true", help="in-process vendors; costs nothing")
    parser.add_argument("--history", help="JSON list of prior messages to start every session with")
    parser.add_argument("--launch-ms", type=float, default=400.0)
    parser.add_argument("--serial", action="store_true", help="no speculative launch (baseline)")
    parser.add_argument("--log-dir", type=Path, default=AGENT_ROOT / "logs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
