"""Create three CosyVoice v3.5-flash designed voices and synthesize the 14 lines.

Also asks Doubao's voice-design endpoint once. That product needs a speaker slot
bought in the console. A refusal is recorded; no voice is invented.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import uuid
from pathlib import Path

import httpx

from latency.adapters.types import redact
from latency.env import Settings, load_dotenv
from tts.lines import DOUBAO_TUNED, LINES
from tts.synthesize import _one
from tts.voices import DESIGNS, PREVIEW_TEXT, TARGET_MODEL, V2_CLEAN, Design, instruction_units

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "design"


def _scrub(text: str, workspace: str) -> str:
    cleaned = redact(text)
    if workspace:
        cleaned = cleaned.replace(workspace, "[workspace]")
    return re.sub(r"\d{6,}", "[id]", cleaned)[:500]


def _custom_url(settings: Settings) -> str:
    parts = settings.dashscope_ws_url.split("/")
    if len(parts) < 3 or not parts[2]:
        raise SystemExit("DASHSCOPE_WORKSPACE_ID is not set")
    return f"https://{parts[2]}/api/v1/services/audio/tts/customization"


async def _post(
    client: httpx.AsyncClient, url: str, key: str, body: dict[str, object], workspace: str
) -> dict[str, object]:
    response = await client.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
    )
    raw = response.text
    if response.status_code >= 400:
        raise RuntimeError(_scrub(f"HTTP {response.status_code} {raw}", workspace))
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("voice design returned a non-object")
    return {str(key): value for key, value in parsed.items()}


async def _create(client: httpx.AsyncClient, url: str, key: str, design: Design, workspace: str) -> dict[str, object]:
    created = await _post(
        client,
        url,
        key,
        {
            "model": "voice-enrollment",
            "input": {
                "action": "create_voice",
                "target_model": TARGET_MODEL,
                "voice_prompt": design.prompt,
                "preview_text": PREVIEW_TEXT,
                "prefix": design.prefix,
                "language_hints": ["zh"],
            },
            "parameters": {"sample_rate": 24000, "response_format": "wav"},
        },
        workspace,
    )
    output = created.get("output")
    if not isinstance(output, dict):
        raise RuntimeError(f"{design.key} returned no output")
    voice_id = output.get("voice_id")
    if not isinstance(voice_id, str) or not voice_id:
        raise RuntimeError(f"{design.key} returned no voice id")
    preview = output.get("preview_audio")
    audio = b""
    if isinstance(preview, dict) and isinstance(preview.get("data"), str):
        audio = base64.b64decode(preview["data"])
    status = "DEPLOYING"
    for _ in range(30):
        queried = await _post(
            client,
            url,
            key,
            {"model": "voice-enrollment", "input": {"action": "query_voice", "voice_id": voice_id}},
            workspace,
        )
        info = queried.get("output")
        if isinstance(info, dict) and isinstance(info.get("status"), str):
            status = info["status"]
        if status in {"OK", "UNDEPLOYED"}:
            break
        await asyncio.sleep(2)
    return {"key": design.key, "prefix": design.prefix, "voice_id": voice_id, "status": status, "preview": audio}


async def _doubao_probe(settings: Settings) -> dict[str, object]:
    """One call. The account may not have a speaker slot; that result is the finding."""

    if not settings.volc_speech_key:
        return {"called": False, "error": "no volc speech key"}
    url = "https://openspeech.bytedance.com/api/v3/tts/voice_design"
    headers = {
        "X-Api-Key": settings.volc_speech_key,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    body = {
        "text": PREVIEW_TEXT,
        "prompt": {"text_prompt": "年轻女性，二十岁上下，音色清澈柔和，音量偏小，语速中等偏慢"},
        "language": 0,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json=body)
    detail = _scrub(response.text, "")
    return {"called": True, "http": response.status_code, "detail": detail}


def _has_words(marks: list[dict[str, object]]) -> bool:
    for mark in marks:
        payload = mark.get("payload")
        if not isinstance(payload, dict):
            continue
        inner = payload.get("payload")
        output = inner.get("output") if isinstance(inner, dict) else None
        sentence = output.get("sentence") if isinstance(output, dict) else None
        words = sentence.get("words") if isinstance(sentence, dict) else None
        if isinstance(words, list) and words:
            return True
    return False


async def _synth(settings: Settings, made: list[dict[str, object]], workspace: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in made:
        if item.get("status") != "OK":
            rows.append({"key": item["key"], "error": f"status {item.get('status')}"})
            continue
        voice_id = str(item["voice_id"])
        key = str(item["key"])
        for line in LINES:
            instruction = DOUBAO_TUNED[line.number]
            try:
                result = await _one(
                    "dashscope",
                    line,
                    settings,
                    OUT,
                    folder=key,
                    instruction=instruction,
                    model=TARGET_MODEL,
                    voice=voice_id,
                    allow_instruction=True,
                    sample_rate=24000,
                )
                result["instruction"] = "on"
            except Exception as exc:
                result = {"vendor": "dashscope", "line": line.number, "instruction": "rejected"}
                result["instruction_error"] = _scrub(str(exc), workspace)
                try:
                    result = await _one(
                        "dashscope",
                        line,
                        settings,
                        OUT,
                        folder=key,
                        instruction="",
                        model=TARGET_MODEL,
                        voice=voice_id,
                        allow_instruction=False,
                        sample_rate=24000,
                    )
                    result["instruction"] = "rejected"
                    result["instruction_error"] = _scrub(str(exc), workspace)
                except Exception as second:
                    result["error"] = _scrub(str(second), workspace)
            saved = OUT / key / f"{line.number:02d}.json"
            words = False
            if saved.is_file():
                parsed = json.loads(saved.read_text(encoding="utf-8"))
                marks = parsed.get("marks")
                words = _has_words(marks) if isinstance(marks, list) else False
            result["key"] = key
            result["words"] = words
            result["instruction_units"] = instruction_units(instruction)
            rows.append(result)
            print(json.dumps({k: v for k, v in result.items() if k != "preview"}, ensure_ascii=False), flush=True)
    return rows


async def _run_designs(settings: Settings, designs: tuple[Design, ...], label: str) -> None:
    if not settings.dashscope_key or not settings.dashscope_ws_url:
        raise SystemExit("缺少百炼密钥或业务空间，没有创建设计音色")
    url = _custom_url(settings)
    workspace = url.split("/")[2].split(".")[0]
    OUT.mkdir(parents=True, exist_ok=True)
    made: list[dict[str, object]] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for design in designs:
            try:
                item = await _create(client, url, settings.dashscope_key, design, workspace)
            except Exception as exc:
                item = {"key": design.key, "status": "error", "error": _scrub(str(exc), workspace)}
            preview = item.pop("preview", b"")
            if isinstance(preview, bytes) and preview:
                (OUT / f"{design.key}-preview.wav").write_bytes(preview)
            made.append(item)
            print(design.key, item.get("status"), item.get("voice_id", ""), flush=True)
    public = []
    for design, item in zip(designs, made, strict=True):
        public.append(
            {
                "key": design.key,
                "prefix": design.prefix,
                "prompt": design.prompt,
                "preview_text": PREVIEW_TEXT,
                "target_model": TARGET_MODEL,
                "voice_id": item.get("voice_id"),
                "status": item.get("status"),
                "error": item.get("error"),
            }
        )
    if label == "v2-clean":
        payload: dict[str, object] = {"voices": public}
        (OUT / "v2-clean-voice.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    else:
        probe = await _doubao_probe(settings)
        payload = {"voices": public, "doubao_voice_design": probe}
        (OUT / "voices.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = await _synth(settings, made, workspace)
    name = "v2-clean-timings.json" if label == "v2-clean" else "timings.json"
    (OUT / name).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _run(settings: Settings) -> None:
    await _run_designs(settings, DESIGNS, "three")


def main() -> None:
    parser = argparse.ArgumentParser(description="Design three CosyVoice voices and synthesize the 14 lines")
    parser.add_argument("--clean-v2", action="store_true", help="Redesign only V2 with a no-noise sentence")
    args = parser.parse_args()
    load_dotenv(Path.cwd())
    settings = Settings.from_environ()
    if args.clean_v2:
        asyncio.run(_run_designs(settings, (V2_CLEAN,), "v2-clean"))
    else:
        asyncio.run(_run(settings))


if __name__ == "__main__":
    main()
