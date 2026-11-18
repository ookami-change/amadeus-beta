"""First-token bench for one chat model.

Each trial sends the same persona, the same four-round history, and one user
line. Thinking stays off, max_tokens is 80, and calls are serial. ``new`` opens
a client per trial. ``reuse`` warms one client up, then times fifty more.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from latency.adapters.types import redact
from latency.env import Settings, load_dotenv
from latency.prompts import load_persona
from latency.scoring import percentile

HERE = Path(__file__).resolve().parent
HISTORY_PATH = HERE / "prompts" / "history.json"
USER_LINE = "店长，今天的账还是差三块。你说我要不要现在就改？"


def _messages(system: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": USER_LINE}]


def _scrub(text: str) -> str:
    cleaned = redact(text)
    return re.sub(r"\d{6,}", "[id]", cleaned)


def _target(settings: Settings, vendor: str, model: str) -> tuple[str, str, dict[str, object]]:
    if vendor == "ark":
        url = f"{settings.ark_base_url}/chat/completions"
        key = settings.ark_key
        extra: dict[str, object] = {"thinking": {"type": "disabled"}, "prompt_cache_key": "amadeus-m0-persona-v1"}
    elif vendor == "qwen":
        url = f"{settings.dashscope_base_url}/chat/completions"
        key = settings.dashscope_key
        extra = {"enable_thinking": False}
    elif vendor == "minimax":
        url = f"{settings.minimax_base_url}/v1/chat/completions"
        key = settings.minimax_key
        # MiniMax-M3 answers directly when thinking is disabled. M3.1-Flash rejects this.
        extra = {"thinking": {"type": "disabled"}}
    else:
        raise SystemExit(f"unknown vendor {vendor}")
    if not key:
        raise SystemExit(f"{vendor} has no API key in the environment")
    return url, key, extra


async def _once(
    client: httpx.AsyncClient,
    *,
    url: str,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    extra: dict[str, object],
) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 80,
        "temperature": 0.7,
        **extra,
    }
    started = time.perf_counter()
    first_ms: float | None = None
    text: list[str] = []
    cached: int | None = None
    prompt_tokens: int | None = None
    async with client.stream(
        "POST",
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
    ) as response:
        if response.status_code >= 400:
            detail = (await response.aread()).decode("utf-8", "replace")[:400]
            raise RuntimeError(redact(f"HTTP {response.status_code} {detail}"))
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                break
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                continue
            usage = parsed.get("usage")
            if isinstance(usage, dict):
                prompt = usage.get("prompt_tokens")
                if isinstance(prompt, int):
                    prompt_tokens = prompt
                details = usage.get("prompt_tokens_details")
                cached_value = details.get("cached_tokens") if isinstance(details, dict) else usage.get("cached_tokens")
                if isinstance(cached_value, int):
                    cached = cached_value
            choices = parsed.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue
            delta = choices[0].get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000.0
                text.append(content)
    if first_ms is None:
        raise RuntimeError("no content token")
    return {
        "first_ms": round(first_ms, 1),
        "text": "".join(text),
        "cached_tokens": cached,
        "prompt_tokens": prompt_tokens,
    }


async def _series(
    *,
    url: str,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    extra: dict[str, object],
    repeats: int,
    gap: float,
    reuse: bool,
) -> tuple[list[dict[str, object]], list[str]]:
    timeout = httpx.Timeout(20.0, read=60.0)
    rows: list[dict[str, object]] = []
    replies: list[str] = []
    if reuse:
        client_cm = httpx.AsyncClient(timeout=timeout)
    else:
        client_cm = None
    client = await client_cm.__aenter__() if client_cm is not None else None
    try:
        if client is not None:
            await _once(client, url=url, key=key, model=model, messages=messages, extra=extra)
            await asyncio.sleep(gap)
        for index in range(1, repeats + 1):
            own = client if client is not None else httpx.AsyncClient(timeout=timeout)
            entered = client is None
            if entered:
                await own.__aenter__()
            try:
                last_error = ""
                for attempt in range(1, 4):
                    try:
                        row = await _once(own, url=url, key=key, model=model, messages=messages, extra=extra)
                        row["n"] = index
                        rows.append(row)
                        if len(replies) < 10:
                            replies.append(str(row["text"]))
                        print(f"{'reuse' if reuse else 'new'} #{index} {row['first_ms']}", flush=True)
                        last_error = ""
                        break
                    except Exception as exc:
                        last_error = _scrub(str(exc))
                        if attempt == 3 or not any(
                            token in last_error.lower() for token in ("429", "rate limit", "throttl")
                        ):
                            print(f"{'reuse' if reuse else 'new'} #{index} fail {last_error[:160]}", flush=True)
                            break
                        await asyncio.sleep(8.0 * attempt)
                if last_error:
                    rows.append({"n": index, "error": last_error})
            finally:
                if entered:
                    await own.__aexit__(None, None, None)
            if gap > 0 and index < repeats:
                await asyncio.sleep(gap)
    finally:
        if client_cm is not None:
            await client_cm.__aexit__(None, None, None)
    return rows, replies


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure one model's time to first token")
    parser.add_argument("--vendor", required=True, choices=("ark", "qwen", "minimax"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--gap", type=float, default=1.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    settings = Settings.from_environ()
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(history, list) or len(history) != 8:
        raise SystemExit("history.json must be four rounds")
    messages = _messages(load_persona(), [item for item in history if isinstance(item, dict)])
    url, key, extra = _target(settings, args.vendor, args.model)
    payload: dict[str, object] = {
        "kind": "first-token",
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "vendor": args.vendor,
        "model": args.model,
        "user": USER_LINE,
        "max_tokens": 80,
        "temperature": 0.7,
        "gap_s": args.gap,
        "modes": {},
    }
    modes = payload["modes"]
    assert isinstance(modes, dict)
    for reuse in (False, True):
        rows, replies = asyncio.run(
            _series(
                url=url,
                key=key,
                model=args.model,
                messages=messages,
                extra=extra,
                repeats=args.repeats,
                gap=args.gap,
                reuse=reuse,
            )
        )
        values: list[float] = []
        cached: list[float] = []
        for row in rows:
            first = row.get("first_ms")
            if isinstance(first, (int, float)):
                values.append(float(first))
            cached_value = row.get("cached_tokens")
            if isinstance(cached_value, (int, float)):
                cached.append(float(cached_value))
        name = "reuse" if reuse else "new"
        modes[name] = {
            "ok": len(values),
            "n": len(rows),
            "first_ms": {"p50": percentile(values, 50), "p95": percentile(values, 95), "n": len(values)},
            "cached_tokens": {"p50": percentile(cached, 50), "n": len(cached)},
            "replies": replies if reuse else [],
            "trials": rows,
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
