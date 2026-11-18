"""One-command latency benchmark.

Endpoint-only mode needs no vendor keys. It feeds each recording's own text in
as a perfect streaming transcript, so the false-barge-in rate is the endpointer
itself, not an ASR. The full cascade needs keys and should be run on a machine
in mainland China; this script refuses to invent those numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from latency.adapters.factory import ASR_NAMES, LLM_NAMES, TTS_NAMES, build_asr, build_llm, build_tts
from latency.adapters.mock import MockASR
from latency.adapters.types import redact
from latency.corpus import Item, load_corpus
from latency.endpoint import CompletenessModel, PunctuationCompleteness
from latency.env import Settings, load_dotenv
from latency.pipeline import Trial, endpoint_trial, realtime_trial
from latency.stats import render_markdown, summarize

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "corpus" / "manifest.json"
PERSONA_PATH = HERE / "prompts" / "persona.md"
HISTORY_PATH = HERE / "prompts" / "history.json"
STABLE_INSTRUCTION = "年轻女声，安静，温和，吐字清楚，略带书卷气。情绪幅度小，像在小声交谈。"


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the cascaded speech pipeline")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--endpoint-only", action="store_true")
    parser.add_argument("--oracle-text", action="store_true")
    parser.add_argument("--endpoint", default="", help="fixed, semantic, or both (default depends on mode)")
    parser.add_argument("--combos", default="", help="Comma-separated asr:llm:tts. Default is the full 2x2x3 matrix.")
    parser.add_argument("--repeats", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mock", action="store_true", help="In-process adapters. Not a measurement.")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--region", default="", help="Where this machine is. Written into the summary, not looked up.")
    parser.add_argument("--gap", type=float, default=2.0, help="Seconds to wait between vendor trials.")
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=3,
        help="Stop a combo after this many failures in a row. Earlier trials are kept.",
    )
    parser.add_argument("--punct-model", type=Path, default=HERE / "models" / "punct" / "model.onnx")
    args = parser.parse_args()

    load_dotenv(Path.cwd())
    load_dotenv(HERE)
    settings = Settings.from_environ()
    if args.check_config:
        print(json.dumps(settings.presence(), ensure_ascii=False, indent=2))
        return

    items = load_corpus(args.manifest)
    if args.limit > 0:
        items = items[: args.limit]
    modes = _modes(args.endpoint, endpoint_only=args.endpoint_only, mock=args.mock)
    semantic_model = None
    if "semantic" in modes:
        if args.punct_model.is_file():
            semantic_model = PunctuationCompleteness(str(args.punct_model))
        else:
            print(
                "没有找到标点模型，语义断句这次不跑。"
                f" 期望路径：{args.punct_model}。下载：bash evals/latency/download_punct.sh",
                flush=True,
            )
            modes = [mode for mode in modes if mode != "semantic"]
            if not modes:
                raise SystemExit("没有可跑的断句方式")

    stopped: list[str] = []
    if args.endpoint_only:
        trials = [
            endpoint_trial(item, mode=mode, model=semantic_model if mode == "semantic" else None)
            for mode in modes
            for item in items
        ]
        kind = "endpoint-only"
    else:
        repeats = args.repeats or (1 if args.mock else 3)
        combos = _combos(args.combos, mock=args.mock)
        _require_keys(settings, combos, mock=args.mock)
        trials, stopped = asyncio.run(
            _realtime_matrix(
                items,
                combos=combos,
                modes=modes,
                repeats=repeats,
                settings=settings,
                semantic_model=semantic_model,
                oracle=args.oracle_text,
                mock=args.mock,
                gap=args.gap,
                max_consecutive_failures=args.max_consecutive_failures,
            )
        )
        kind = "mock" if args.mock else "cascade"

    summary = summarize(trials)
    payload = {
        "kind": kind,
        "generated_at": datetime.now(UTC).isoformat(),
        "region": args.region,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "partial_source": trials[0].partial_source if trials else "",
        "mock": args.mock,
        "gap_s": args.gap,
        "models": {
            "ark": settings.ark_model,
            "qwen": settings.qwen_model,
            "volc_asr_resource": settings.volc_asr_resource,
            "volc_tts_resource": settings.volc_tts_resource,
            "volc_voice": settings.volc_voice,
            "minimax": settings.minimax_model,
            "minimax_voice": settings.minimax_voice,
            "cosyvoice": settings.cosyvoice_model,
            "cosyvoice_voice": settings.cosyvoice_voice,
            "dashscope_asr": settings.dashscope_asr_model,
        },
        "stopped_combos": stopped,
        "notes": _notes(kind),
        "summary": summary,
        "trials": [trial.as_json() for trial in trials],
    }
    out = args.out or _default_out(kind)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    table = render_markdown(summary)
    out.with_suffix(".md").write_text(table, encoding="utf-8")
    print(table)
    print(f"wrote {out}")


def _modes(raw: str, *, endpoint_only: bool, mock: bool) -> list[str]:
    if raw.strip():
        modes = [piece.strip() for piece in raw.split(",") if piece.strip()]
    elif mock:
        modes = ["fixed"]
    elif endpoint_only:
        modes = ["fixed", "semantic"]
    else:
        modes = ["semantic"]
    for mode in modes:
        if mode not in {"fixed", "semantic"}:
            raise SystemExit(f"unknown endpoint mode: {mode}")
    return modes


def _combos(raw: str, *, mock: bool) -> list[tuple[str, str, str]]:
    if mock:
        return [("mock", "mock", "mock")]
    text = raw.strip()
    if not text:
        return [(asr, llm, tts) for asr in ASR_NAMES for llm in LLM_NAMES for tts in TTS_NAMES]
    combos: list[tuple[str, str, str]] = []
    for piece in text.split(","):
        parts = piece.strip().split(":")
        if len(parts) != 3:
            raise SystemExit(f"combo must be asr:llm:tts, got {piece}")
        combos.append((parts[0], parts[1], parts[2]))
    return combos


def _require_keys(settings: Settings, combos: list[tuple[str, str, str]], *, mock: bool) -> None:
    if mock:
        return
    missing: list[str] = []
    for asr, llm, tts in combos:
        for name in settings.missing_for(asr, llm, tts):
            if name not in missing:
                missing.append(name)
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(
            "缺少供应商密钥，拒绝用猜测填延迟表："
            f"{joined}。把它们写进仓库根目录的 .env 后再跑。"
            "端点对比不需要密钥：python -m latency.run --endpoint-only --oracle-text"
        )


async def _realtime_matrix(
    items: list[Item],
    *,
    combos: list[tuple[str, str, str]],
    modes: list[str],
    repeats: int,
    settings: Settings,
    semantic_model: CompletenessModel | None,
    oracle: bool,
    mock: bool,
    gap: float,
    max_consecutive_failures: int,
) -> tuple[list[Trial], list[str]]:
    system = PERSONA_PATH.read_text(encoding="utf-8").strip() + "\n"
    history = _history()
    trials: list[Trial] = []
    stopped: list[str] = []
    for asr_name, llm_name, tts_name in combos:
        combo_name = f"{asr_name}/{llm_name}/{tts_name}"
        consecutive = 0
        halt = False
        for mode in modes:
            if halt:
                break
            model = semantic_model if mode == "semantic" else None
            for repeat in range(1, repeats + 1):
                if halt:
                    break
                for item in items:
                    trial = await _one_trial(
                        item,
                        asr_name=asr_name,
                        llm_name=llm_name,
                        tts_name=tts_name,
                        mode=mode,
                        repeat=repeat,
                        settings=settings,
                        system=system,
                        history=history,
                        model=model,
                        oracle=oracle,
                        mock=mock,
                    )
                    trials.append(trial)
                    state = "ok" if trial.ok else trial.error
                    print(f"{trial.combo} {mode} #{repeat} {item.item_id} {state}", flush=True)
                    if trial.ok:
                        consecutive = 0
                    else:
                        consecutive += 1
                        if max_consecutive_failures > 0 and consecutive >= max_consecutive_failures:
                            halted = f"{combo_name} {mode}"
                            stopped.append(halted)
                            print(f"stop {halted} after {consecutive} consecutive failures", flush=True)
                            halt = True
                            break
                    if not mock and gap > 0:
                        await asyncio.sleep(gap)
    return trials, stopped


async def _one_trial(
    item: Item,
    *,
    asr_name: str,
    llm_name: str,
    tts_name: str,
    mode: str,
    repeat: int,
    settings: Settings,
    system: str,
    history: list[dict[str, str]],
    model: CompletenessModel | None,
    oracle: bool,
    mock: bool,
) -> Trial:
    trial: Trial | None = None
    for attempt in range(1, 4):
        asr = build_asr(asr_name, settings)
        if isinstance(asr, MockASR):
            asr.transcript = item.text
        llm = build_llm(llm_name, settings)
        tts = build_tts(tts_name, settings)
        try:
            trial = await realtime_trial(
                item,
                combo=f"{asr_name}/{llm_name}/{tts_name}",
                mode=mode,
                repeat=repeat,
                asr=asr,
                llm=llm,
                tts=tts,
                system=system,
                history=history,
                model=model,
                oracle_partials=oracle,
                mock=mock,
                instruction=STABLE_INSTRUCTION,
            )
        except Exception as exc:
            trial = _failed_trial(item, asr_name, llm_name, tts_name, mode, repeat, mock, oracle, redact(str(exc)))
        if trial.ok or not _retryable(trial.error) or attempt == 3:
            return trial
        await asyncio.sleep(8.0 * attempt)
    assert trial is not None
    return trial


def _retryable(error: str | None) -> bool:
    if not error:
        return False
    text = error.lower()
    return any(token in text for token in ("429", "limit_requests", "throttl", "empty transcript"))


def _failed_trial(
    item: Item,
    asr_name: str,
    llm_name: str,
    tts_name: str,
    mode: str,
    repeat: int,
    mock: bool,
    oracle: bool,
    error: str,
) -> Trial:
    return Trial(
        combo=f"{asr_name}/{llm_name}/{tts_name}",
        endpoint=mode,
        item_id=item.item_id,
        category=item.category,
        repeat=repeat,
        ok=False,
        error=error,
        partial_source="oracle" if oracle else "asr",
        t_speech_end=item.speech_end_ms,
        t_endpoint=None,
        t_asr_final=None,
        t_llm_first_token=None,
        t_llm_first_clause=None,
        t_tts_first_audio=None,
        early=False,
        false_barge_in=False,
        asr_used_partial=False,
        endpoint_reason="error",
        transcript="",
        reply="",
        cached_tokens=None,
        prompt_tokens=None,
        mock=mock,
    )


def _history() -> list[dict[str, str]]:
    raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("history.json must be a list")
    history: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get("role"), str) and isinstance(entry.get("content"), str):
            history.append({"role": entry["role"], "content": entry["content"]})
    if len(history) != 8:
        raise SystemExit(f"expected 4 rounds (8 messages), found {len(history)}")
    return history


def _default_out(kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return HERE / "results" / "runs" / f"{kind}-{stamp}.json"


def _notes(kind: str) -> list[str]:
    if kind == "endpoint-only":
        return [
            "端点时间 = 音频时间线上越过阈值的帧 + 完整度模型的计算时间。",
            "语义断句的文本是语料自身的逐段文本，相当于理想流式识别，不是某一家 ASR。",
            "固定阈值是连续静音 500ms。语义断句在静音 200ms 时询问模型，未完成则继续听，上限 1500ms。",
            "误抢话只统计 category=pause 的 5 条，并且端点落在标注的句中停顿里。",
            "干净样本排除了提前端点，避免把抢话造成的负延迟算进 P50。",
        ]
    if kind == "mock":
        return ["进程内假适配器，只验证流水线能写出时间戳。这些数字不是供应商延迟。"]
    return [
        "录音按实时速率送入。TTS 会话在断句时建立，与 LLM 首 token 重叠；文本仍等到第一个子句才发送。",
        "端到端 = t_tts_first_audio - t_speech_end。排名只用未抢话的成功样本。",
        "每个组合正式计时前，调用方应已确认测试机在中国大陆。本脚本不改写测得的数字。",
    ]


if __name__ == "__main__":
    main()
