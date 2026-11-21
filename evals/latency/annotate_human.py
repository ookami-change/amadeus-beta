"""Label the human turn recordings.

Speech regions come from Silero VAD. Word times come from Doubao streaming
ASR (``show_utterances``). Scripted lines are checked against the list text;
free lines have no script, so the ASR transcript is the text being aligned.
Pauses of at least 300 ms between voiced regions are the labeled interior
pauses. The audio stays on the server and is not written here.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from latency.human_labels import (
    PAUSE_MIN_MS,
    TimedChar,
    char_match,
    choose_speech_end,
    connector_before_pause,
    content,
    explode_words,
    interior_gaps,
    pause_aligned,
)
from latency.pcm import read_wav
from latency.scoring import Pause
from latency.vad import SileroVAD, speech_segments

HERE = Path(__file__).resolve().parent
LIST = HERE / "turns" / "record" / "list.json"

# Written after listening to the clocks, not after changing them to fit the script.
SPOT = {
    "t01": (
        "长停顿 3488–3808ms 在「然后」和「饼」之间，词边界对齐。"
        "清单希望停在「然后」后面，实际「然后」在 2752ms，中间还有一段有声。停顿不按稿子挪。"
    ),
    "t05": "丢掉落在文件时长之后的「七」「八」「糟」。剩下的说完时刻用 VAD 6912ms，和「乱」的词尾一致。",
    "t10": "丢掉落在文件时长之后的「写」。说完时刻用 VAD 6688ms，「这么」的词尾是 6162ms，差 526ms，保留较晚的 VAD。",
    "f01": "四处至少 300ms 的停顿都落在词与词之间。",
    "f05": "词尾 2172ms，VAD 2624ms，后面是长静音。保留较晚的 VAD。",
    "s01": "没有句中长停顿，VAD 和词尾相差不到 250ms。",
    "s02": "没有句中长停顿。VAD 比词尾晚 266ms，当作收尾余音，不改。",
    "s07": "没有句中长停顿，VAD 和词尾相差不到 250ms。",
}


def _expected_connector(note: str) -> str | None:
    if "「然后」" in note:
        return "然后"
    if "「就是」" in note:
        return "就是"
    return None


def _transcript(chars: list[TimedChar]) -> str:
    return "".join(char.text for char in chars)


def _within_file(words: list[dict[str, object]], duration_ms: float) -> list[dict[str, object]]:
    """Drop words whose start falls in the silence appended for the ASR pass."""

    kept: list[dict[str, object]] = []
    for word in words:
        start = word.get("start_ms")
        if isinstance(start, (int, float)) and float(start) >= duration_ms:
            continue
        kept.append(word)
    return kept


def annotate_item(
    *,
    item_id: str,
    kind: str,
    note: str,
    reference: str,
    pcm: bytes,
    probabilities: list[float],
    words: list[dict[str, object]],
) -> dict[str, object]:
    duration_ms = len(pcm) / 2 / 16.0
    words = _within_file(words, duration_ms)
    segments = speech_segments(probabilities)
    chars = explode_words(words)
    vad_end = segments[-1][1] if segments else None
    word_end = chars[-1].end_ms if chars else None
    speech_end, source = choose_speech_end(vad_end, word_end)
    gaps = interior_gaps(segments)
    pauses: list[dict[str, object]] = []
    shorts: list[dict[str, object]] = []
    for start, end in gaps:
        aligned = pause_aligned(chars, start, end) if chars else False
        row: dict[str, object] = {"start_ms": round(start, 1), "end_ms": round(end, 1), "aligned": aligned}
        if end - start >= PAUSE_MIN_MS:
            pauses.append(row)
        else:
            shorts.append(row)
    transcript = _transcript(chars)
    reference_match = char_match(reference, transcript) if reference else None
    connector = _expected_connector(note)
    pause_list: list[Pause] = []
    for pause in pauses:
        start_ms = pause["start_ms"]
        end_ms = pause["end_ms"]
        if isinstance(start_ms, float) and isinstance(end_ms, float):
            pause_list.append(Pause(start_ms, end_ms))
    connector_ok: bool | None = None
    if connector is not None and chars:
        connector_ok = connector_before_pause(chars, pause_list, connector)
    review = source not in {"agree", "vad-only"} or (reference_match is not None and reference_match < 0.85)
    if connector_ok is False:
        review = True
    if kind == "short" and pauses:
        review = True
    return {
        "id": item_id,
        "kind": kind,
        "duration_ms": round(duration_ms, 1),
        "speech_end_ms": round(speech_end, 1),
        "speech_end_source": source,
        "vad_end_ms": None if vad_end is None else round(vad_end, 1),
        "word_end_ms": None if word_end is None else round(word_end, 1),
        "reference": reference,
        "transcript": transcript,
        "char_match": None if reference_match is None else round(reference_match, 3),
        "expected_connector": connector,
        "connector_ok": connector_ok,
        "pauses": pauses,
        "short_gaps": shorts,
        "review": review,
        "words": words,
    }


def _load_words(path: Path) -> dict[str, list[dict[str, object]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, dict):
        raise SystemExit(f"{path} has no items object")
    found: dict[str, list[dict[str, object]]] = {}
    for key, value in items.items():
        if not isinstance(value, dict):
            continue
        words = value.get("words")
        if isinstance(words, list):
            found[str(key)] = [word for word in words if isinstance(word, dict)]
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Label human turn recordings")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--words", type=Path, required=True, help="volc_collect JSON that includes word times")
    parser.add_argument("--list", type=Path, default=LIST)
    parser.add_argument("--vad", type=Path, default=HERE / "models" / "silero_vad.onnx")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "turns-human-annotations.json")
    args = parser.parse_args()
    listing = json.loads(args.list.read_text(encoding="utf-8"))
    if not isinstance(listing, list):
        raise SystemExit("recording list must be an array")
    words_by_id = _load_words(args.words)
    vad = SileroVAD(args.vad)
    items: list[dict[str, object]] = []
    for raw in listing:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw["id"])
        audio = args.audio / f"{item_id}.wav"
        pcm = read_wav(audio)
        probabilities = vad.probabilities(pcm)
        row = annotate_item(
            item_id=item_id,
            kind=str(raw.get("kind", "")),
            note=str(raw.get("note", "")),
            reference=str(raw.get("text", "")),
            pcm=pcm,
            probabilities=probabilities,
            words=words_by_id.get(item_id, []),
        )
        items.append(row)
        flag = "review" if row["review"] else "ok"
        pauses_field = row["pauses"]
        pause_count = len(pauses_field) if isinstance(pauses_field, list) else 0
        print(
            f"{item_id} {flag} end={row['speech_end_ms']} pauses={pause_count} "
            f"src={row['speech_end_source']} match={row['char_match']} connector={row['connector_ok']}",
            flush=True,
        )
    payload = {
        "kind": "human-turn-annotations",
        "generated_at": datetime.now(UTC).isoformat(),
        "audio_dir": str(args.audio),
        "vad": "silero-vad v5.1.2 onnx, threshold 0.5, hop 32 ms, min speech 100 ms, merge silence under 80 ms",
        "alignment": (
            "豆包流式识别 bigmodel_async 的 show_utterances 词时间。"
            "照着说的句子用清单文本做字符对齐检查；自由说没有稿，对齐的是识别文本本身。"
        ),
        "pause_min_ms": PAUSE_MIN_MS,
        "notes": [
            "句中停顿是两段语音之间、时长至少 300ms 的静音。80ms 到 300ms 的间隙记在 short_gaps，不算标注停顿。",
            "真实说完时刻在 VAD 末尾和最后一个词的末尾相差不超过 250ms 时取较晚的那个。",
            "review 为 true 的条目要抽查：两端时钟差太多、稿子对不上、连接词不在停顿前，或短句里出现了长停顿。",
            "词时间来自补了 2500ms 尾部静音的识别。起点不早于原文件时长的词是这段静音上的幻觉，标注时丢掉。",
            (
                "照着说的句子里，清单要求停在「然后」或「就是」后面。"
                "词时间显示这个词经常贴在停顿结束、话重新开始的地方，所以 connector_ok 为 false。"
                "停顿区间按 VAD 留，不按稿子改。"
            ),
        ],
        "spot_check": SPOT,
        "items": items,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reviews = [row["id"] for row in items if row["review"] is True]
    empty = [row["id"] for row in items if not content(str(row["transcript"]))]
    print(f"wrote {args.out} items={len(items)} review={reviews} empty={empty}")


if __name__ == "__main__":
    main()
