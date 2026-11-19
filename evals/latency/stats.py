"""Aggregate trials into P50 / P95 tables."""

from __future__ import annotations

from latency.pipeline import Trial
from latency.scoring import percentile


def summarize(trials: list[Trial]) -> dict[str, object]:
    groups: dict[tuple[str, str], list[Trial]] = {}
    for trial in trials:
        groups.setdefault((trial.combo, trial.endpoint), []).append(trial)
    rows: list[dict[str, object]] = []
    for combo, endpoint in sorted(groups):
        group = groups[(combo, endpoint)]
        ok = [trial for trial in group if trial.ok]
        clean = [trial for trial in ok if not trial.early]
        pause = [trial for trial in ok if trial.category == "pause"]
        false_hits = sum(1 for trial in pause if trial.false_barge_in)
        rows.append(
            {
                "combo": combo,
                "endpoint": endpoint,
                "n": len(group),
                "ok": len(ok),
                "clean": len(clean),
                "pause_n": len(pause),
                "false_barge_in": false_hits,
                "false_barge_in_rate": (false_hits / len(pause)) if pause else None,
                "e2e_ms": _pair(clean, _e2e),
                "endpoint_ms": _pair(clean, _endpoint_lag),
                "asr_ms": _pair(clean, _asr_lag),
                "llm_first_token_ms": _pair(clean, _llm_wait),
                "llm_first_clause_ms": _pair(clean, _clause_wait),
                "tts_first_audio_ms": _pair(clean, _tts_wait),
                "cached_tokens": _pair(ok, _cached),
            }
        )
    return {"rows": rows}


def render_markdown(summary: dict[str, object]) -> str:
    rows = summary.get("rows")
    if not isinstance(rows, list):
        return ""
    header = (
        "| 组合 | 断句 | n | 成功 | 干净样本 | 误抢话 | "
        "端到端 P50 | 端到端 P95 | 断句 P50 | ASR P50 | LLM 首 token P50 | 子句 P50 | TTS 首包 P50 |"
    )
    lines = [header, "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        if not isinstance(row, dict):
            continue
        rate = row.get("false_barge_in_rate")
        rate_text = (
            "—" if not isinstance(rate, float) else f"{rate * 100:.0f}% ({row['false_barge_in']}/{row['pause_n']})"
        )
        lines.append(
            "| {combo} | {endpoint} | {n} | {ok} | {clean} | {rate} | {e2e50} | {e2e95} | {ep} | {asr} | {llm} | {clause} | {tts} |".format(
                combo=row.get("combo"),
                endpoint=row.get("endpoint"),
                n=row.get("n"),
                ok=row.get("ok"),
                clean=row.get("clean"),
                rate=rate_text,
                e2e50=_cell(row.get("e2e_ms"), 0),
                e2e95=_cell(row.get("e2e_ms"), 1),
                ep=_cell(row.get("endpoint_ms"), 0),
                asr=_cell(row.get("asr_ms"), 0),
                llm=_cell(row.get("llm_first_token_ms"), 0),
                clause=_cell(row.get("llm_first_clause_ms"), 0),
                tts=_cell(row.get("tts_first_audio_ms"), 0),
            )
        )
    return "\n".join(lines) + "\n"


def _pair(trials: list[Trial], pick: object) -> dict[str, float | int | None]:
    if not callable(pick):
        return {"p50": None, "p95": None, "n": 0}
    values = [value for value in (pick(trial) for trial in trials) if isinstance(value, (int, float))]
    return {
        "p50": percentile([float(value) for value in values], 50),
        "p95": percentile([float(value) for value in values], 95),
        "n": len(values),
    }


def _cell(pair: object, index: int) -> str:
    if not isinstance(pair, dict):
        return "—"
    key = "p50" if index == 0 else "p95"
    value = pair.get(key)
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.0f}"


def _e2e(trial: Trial) -> float | None:
    if trial.t_tts_first_audio is None:
        return None
    return trial.t_tts_first_audio - trial.t_speech_end


def _endpoint_lag(trial: Trial) -> float | None:
    if trial.t_endpoint is None:
        return None
    return trial.t_endpoint - trial.t_speech_end


def _asr_lag(trial: Trial) -> float | None:
    if trial.t_asr_final is None:
        return None
    return trial.t_asr_final - trial.t_speech_end


def _llm_wait(trial: Trial) -> float | None:
    if trial.t_llm_first_token is None or trial.t_endpoint is None or trial.t_asr_final is None:
        return None
    return trial.t_llm_first_token - max(trial.t_endpoint, trial.t_asr_final)


def _clause_wait(trial: Trial) -> float | None:
    if trial.t_llm_first_clause is None or trial.t_llm_first_token is None:
        return None
    return trial.t_llm_first_clause - trial.t_llm_first_token


def _tts_wait(trial: Trial) -> float | None:
    if trial.t_tts_first_audio is None or trial.t_llm_first_clause is None:
        return None
    return trial.t_tts_first_audio - trial.t_llm_first_clause


def _cached(trial: Trial) -> float | None:
    if trial.cached_tokens is None:
        return None
    return float(trial.cached_tokens)
