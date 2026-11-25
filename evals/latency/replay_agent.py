"""M1-2 acceptance: replay the human recordings through the agent server over WebSocket.

Start the server first (``services/agent``, ``python -m agent.server``), then:

    python -m latency.replay_agent --url ws://127.0.0.1:8765 --out latency/results/m1-replay.json

Each recording gets its own session. The client streams the WAV plus 2.5 s of
silence in real time as mic frames, keeps streaming silence until the server is
idle again, and stamps everything it receives on the audio clock (ms of audio
sent). The server's ``turn_report`` carries its own timings on the same clock.

* **False barge-in**: the first commit lands before the annotated speech end
  (``classify_human_endpoint``, the R3b/R3c/R6b rule).
* **End to end**: annotated speech end to the first speech frame the client
  receives after that commit. Unlike R6b this includes the socket hop.

``--wav`` replays one file with a given speech end, for smoke tests against
``--mock`` servers where the recordings are not available.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from latency import turn_combo as tc
from latency.human_labels import classify_human_endpoint
from latency.pcm import read_wav, silence
from latency.scoring import Pause, percentile

FRAME_MS = 20.0
FRAME_BYTES = 640
PAD_MS = 2500.0
SETTLE_TIMEOUT_S = 25.0
MIC_TAG = 1
SPEECH_TAG = 2


async def replay(url: str, item_id: str, pcm: bytes) -> dict[str, Any]:
    """One session. Returns everything received, stamped on the audio clock."""
    received: list[dict[str, Any]] = []
    first_speech: dict[int, float] = {}
    state: dict[str, Any] = {"value": "", "reports": 0}
    async with connect(url, max_size=1 << 22) as ws:
        await ws.send(json.dumps({"type": "hello", "protocol": "0.1.0", "client": "replay", "sample_rate": 16000}))
        welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if welcome.get("type") != "welcome":
            raise RuntimeError(f"expected welcome, got {welcome.get('type')}")
        origin = time.perf_counter()

        def clock() -> float:
            return (time.perf_counter() - origin) * 1000.0

        async def read() -> None:
            async for frame in ws:
                at = clock()
                if isinstance(frame, bytes):
                    if frame and frame[0] == SPEECH_TAG:
                        utterance = int.from_bytes(frame[1:5], "little")
                        first_speech.setdefault(utterance, at)
                    continue
                message = json.loads(frame)
                message["at_ms"] = round(at, 1)
                received.append(message)
                if message.get("type") == "state":
                    state["value"] = str(message.get("state"))
                if message.get("type") == "turn_report":
                    state["reports"] += 1

        reader = asyncio.create_task(read())
        sent_ms = 0.0
        audio = pcm + silence(PAD_MS)
        offset = 0
        deadline: float | None = None
        try:
            while True:
                chunk = audio[offset : offset + FRAME_BYTES] if offset < len(audio) else silence(FRAME_MS)
                offset += FRAME_BYTES
                if len(chunk) < FRAME_BYTES:
                    chunk += silence(FRAME_MS)[: FRAME_BYTES - len(chunk)]
                await ws.send(bytes([MIC_TAG]) + chunk)
                sent_ms += FRAME_MS
                delay = sent_ms - clock()
                if delay > 0:
                    await asyncio.sleep(delay / 1000.0)
                if offset >= len(audio):
                    if deadline is None:
                        deadline = time.perf_counter() + SETTLE_TIMEOUT_S
                    if state["reports"] and state["value"] == "idle":
                        break
                    if time.perf_counter() > deadline:
                        break
            await ws.send(json.dumps({"type": "bye"}))
        finally:
            reader.cancel()
            try:
                await reader
            except BaseException:
                pass
    return {"id": item_id, "received": received, "first_speech": first_speech}


def score(item_id: str, speech_end_ms: float, pauses: list[Pause], run: dict[str, Any]) -> dict[str, Any]:
    reports = [m for m in run["received"] if m.get("type") == "turn_report"]
    starts = {m["utterance"]: m["turn"] for m in run["received"] if m.get("type") == "speak_start"}
    errors = [m for m in run["received"] if m.get("type") == "error"]
    committed = [r for r in reports if r.get("commit_ms") is not None]
    first_commit = committed[0]["commit_ms"] if committed else None
    barge = False
    if first_commit is not None:
        barge, _ = classify_human_endpoint(
            speech_end_ms=speech_end_ms, pauses=pauses, t_endpoint_ms=float(first_commit)
        )
    # The server never plays before a commit, so the first speech frame is the answer.
    # (The client clock trails the server's by up to one frame; do not compare them.)
    heard = None
    answered = None
    for utterance, at in sorted(run["first_speech"].items(), key=lambda kv: kv[1]):
        heard = at
        answered = next((r for r in reports if r["turn"] == starts.get(utterance)), None)
        break
    answered = answered or (committed[0] if committed else None)
    error = None
    if heard is None:
        error = next((r.get("error") for r in reports if r.get("error")), None) or "nothing heard"

    def rel(value: object) -> float | None:
        return None if not isinstance(value, (int, float)) else round(float(value) - speech_end_ms, 1)

    return {
        "id": item_id,
        "ok": heard is not None,
        "error": error,
        "speech_end_ms": speech_end_ms,
        "false_barge_in": barge,
        "e2e_ms": rel(heard),
        "turns": len(reports),
        "launches": sum(int(r["launches"]) for r in reports),
        "cancelled": sum(int(r["cancelled"]) for r in reports),
        "restarts": sum(int(r["restarts"]) for r in reports),
        "vendor_errors": [e.get("message") for e in errors],
        "breakdown": {
            "commit": rel(answered.get("commit_ms")) if answered else None,
            "launch": rel(answered.get("launch_ms")) if answered else None,
            "first_token": rel(answered.get("first_token_ms")) if answered else None,
            "ready": rel(answered.get("ready_ms")) if answered else None,
            "speak": rel(answered.get("speak_ms")) if answered else None,
            "heard": rel(heard),
        },
        "commit_text": answered.get("commit_text") if answered else None,
        "final_text": answered.get("final_text") if answered else None,
        "reports": reports,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [r for r in rows if r["ok"] and not r["false_barge_in"]]
    e2e = [float(r["e2e_ms"]) for r in clean]
    parts: dict[str, Any] = {}
    for key in ("commit", "launch", "first_token", "ready", "speak", "heard"):
        values = [float(v) for r in clean if (v := r["breakdown"][key]) is not None]
        parts[key] = {"p50": percentile(values, 50), "p95": percentile(values, 95)}
    tokens = [
        float(r["breakdown"]["first_token"]) - float(r["breakdown"]["launch"])
        for r in clean
        if r["breakdown"]["first_token"] is not None and r["breakdown"]["launch"] is not None
    ]
    return {
        "n": len(rows),
        "ok": sum(1 for r in rows if r["ok"]),
        "failed": [r["id"] for r in rows if not r["ok"]],
        "false_barge_in": sum(1 for r in rows if r["ok"] and r["false_barge_in"]),
        "barge_ids": [r["id"] for r in rows if r["ok"] and r["false_barge_in"]],
        "e2e_ms": {
            "p50": percentile(e2e, 50),
            "p95": percentile(e2e, 95),
            "mean": round(statistics.mean(e2e), 1) if e2e else None,
            "n": len(e2e),
        },
        "first_token_after_launch_ms": {"p50": percentile(tokens, 50), "p95": percentile(tokens, 95)},
        "breakdown_ms": parts,
        "launches": sum(r["launches"] for r in rows),
        "cancelled": sum(r["cancelled"] for r in rows),
        "restarts": sum(r["restarts"] for r in rows),
        "trial_retries": sum(r.get("trial_retries", 0) for r in rows),
        "vendor_errors": sum(len(r["vendor_errors"]) for r in rows),
    }


def _again(row: dict[str, Any]) -> bool:
    text = " ".join([str(row.get("error") or "")] + [str(e) for e in row["vendor_errors"]]).lower()
    return not row["ok"] and any(t in text for t in ("rate limit", "429", "closed", "timed out", "timeout"))


async def campaign(args: argparse.Namespace) -> dict[str, Any]:
    jobs: list[tuple[str, bytes, float, list[Pause]]] = []
    if args.wav:
        jobs.append((args.wav.stem, read_wav(args.wav), float(args.speech_end_ms), []))
    else:
        items = tc.load(args.annotations, args.volc)
        wanted = [one for one in args.ids.split(",") if one]
        items = [i for i in items if not wanted or i.id in wanted]
        if args.limit:
            items = items[: args.limit]
        jobs = [(i.id, read_wav(args.audio / f"{i.id}.wav"), i.speech_end_ms, i.pauses) for i in items]
    rows: list[dict[str, Any]] = []
    started = datetime.now(UTC).isoformat()
    for item_id, pcm, speech_end, pauses in jobs:
        row: dict[str, Any] = {}
        for trial in range(1, 4):
            try:
                run = await replay(args.url, item_id, pcm)
                row = score(item_id, speech_end, pauses, run)
            except Exception as exc:
                row = {"id": item_id, "ok": False, "error": f"replay: {exc}", "vendor_errors": []}
            row["trial_retries"] = trial - 1
            if row.get("ok") or not _again(row) or trial == 3:
                break
            print(f"{item_id} retry after {row.get('error')} {row['vendor_errors']}", flush=True)
            await asyncio.sleep(20.0)
        rows.append(row)
        print(
            f"{item_id} {'ok' if row.get('ok') else row.get('error')} e2e={row.get('e2e_ms')} "
            f"barge={row.get('false_barge_in')} launches={row.get('launches')} "
            f"cancel={row.get('cancelled')} restart={row.get('restarts')}",
            flush=True,
        )
        await asyncio.sleep(args.gap_s)
    good = [r for r in rows if "breakdown" in r]
    return {
        "kind": "m1-replay",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "summary": summarize(good) if good else {},
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="ws://127.0.0.1:8765")
    parser.add_argument("--audio", type=Path, default=Path("/var/lib/amadeus/turns"))
    parser.add_argument("--annotations", type=Path, default=tc.RESULTS / "turns-human-annotations.json")
    parser.add_argument("--volc", type=Path, default=tc.RESULTS / "turns-human-volc.json")
    parser.add_argument("--ids", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--gap-s", type=float, default=3.0)
    parser.add_argument("--wav", type=Path, help="replay one file instead of the annotated set")
    parser.add_argument("--speech-end-ms", type=float, default=0.0)
    parser.add_argument("--out", type=Path, default=tc.RESULTS / "m1-replay.json")
    args = parser.parse_args()
    payload = asyncio.run(campaign(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
