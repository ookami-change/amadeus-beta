"""Labels and scores for the human turn-detection retest.

A false barge-in is any endpoint before the true end of speech. Landing inside
a labeled interior pause and landing in a shorter gap are counted separately.
Endpoint lag is only computed for utterances that were not cut off early.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from latency.scoring import Pause

PUNCTUATION = set("，。！？、；：…,.!?;: \n\t“”\"'（）()《》【】")
PAUSE_MIN_MS = 300.0
SHORT_GAP_MIN_MS = 80.0
TOLERANCE_MS = 80.0


def content(text: str) -> str:
    return "".join(char for char in text if char not in PUNCTUATION and not char.isspace())


@dataclass(frozen=True)
class TimedChar:
    text: str
    start_ms: float
    end_ms: float


def explode_words(words: list[dict[str, object]]) -> list[TimedChar]:
    """Spread each ASR word's time evenly across its content characters."""

    chars: list[TimedChar] = []
    for word in words:
        raw = word.get("text")
        start = word.get("start_ms")
        end = word.get("end_ms")
        if not isinstance(raw, str) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        pieces = content(raw)
        if not pieces:
            continue
        span = max(0.0, float(end) - float(start))
        step = span / len(pieces)
        for index, char in enumerate(pieces):
            chars.append(TimedChar(char, float(start) + index * step, float(start) + (index + 1) * step))
    return chars


def char_match(reference: str, transcript: str) -> float:
    left = content(reference)
    right = content(transcript)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(a=left, b=right).ratio()


def interior_gaps(segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    for (_, end), (nxt, _) in zip(segments, segments[1:], strict=False):
        if nxt - end >= SHORT_GAP_MIN_MS:
            gaps.append((end, nxt))
    return gaps


def choose_speech_end(vad_end_ms: float | None, word_end_ms: float | None) -> tuple[float, str]:
    """Pick the true end, and say why the two clocks did or did not agree."""

    if vad_end_ms is None and word_end_ms is None:
        raise ValueError("no speech end")
    if vad_end_ms is None and word_end_ms is not None:
        return word_end_ms, "words-only"
    if word_end_ms is None and vad_end_ms is not None:
        return vad_end_ms, "vad-only"
    assert vad_end_ms is not None and word_end_ms is not None
    delta = vad_end_ms - word_end_ms
    if abs(delta) <= 250.0:
        return max(vad_end_ms, word_end_ms), "agree"
    if delta > 250.0:
        return vad_end_ms, "vad-after-words"
    return word_end_ms, "words-after-vad"


def pause_aligned(chars: list[TimedChar], start_ms: float, end_ms: float) -> bool:
    for left, right in zip(chars, chars[1:], strict=False):
        overlap = min(right.start_ms, end_ms) - max(left.end_ms, start_ms)
        if overlap >= 100.0:
            return True
    return False


def connector_before_pause(chars: list[TimedChar], pauses: list[Pause], token: str) -> bool:
    """True when ``token`` ends within 120 ms of a labeled pause opening."""

    if len(token) != 2:
        raise ValueError(token)
    for left, right in zip(chars, chars[1:], strict=False):
        if left.text + right.text != token:
            continue
        for pause in pauses:
            if pause.start_ms - 80.0 <= right.end_ms <= pause.start_ms + 120.0:
                return True
    return False


def classify_human_endpoint(
    *,
    speech_end_ms: float,
    pauses: list[Pause],
    t_endpoint_ms: float,
    tolerance_ms: float = TOLERANCE_MS,
) -> tuple[bool, str]:
    """Return ``(false_barge_in, where)``.

    ``where`` is ``labeled-pause``, ``short-gap``, or ``after``. Anything before
    the true end counts, including an endpoint that missed the labeled pauses
    and cut a shorter gap or the middle of a word.
    """

    if t_endpoint_ms >= speech_end_ms - tolerance_ms:
        return False, "after"
    for pause in pauses:
        if pause.start_ms - 40.0 <= t_endpoint_ms <= pause.end_ms + tolerance_ms:
            return True, "labeled-pause"
    return True, "short-gap"


@dataclass(frozen=True)
class Consult:
    audio_ms: float
    silence_ms: float
    smart_p: float
    smart_ms: float
    livekit_p: float
    livekit_ms: float


def silence_reached(
    voiced: list[bool],
    need_ms: float,
    *,
    frame_ms: float = 20.0,
    arm_ms: float = 80.0,
) -> float | None:
    """Frame end when silence first lasts ``need_ms``, using the endpointer's clock."""

    speech_ms = 0.0
    armed = False
    silence_start: float | None = None
    previous = 0.0
    for index, is_speech in enumerate(voiced):
        end_ms = (index + 1) * frame_ms
        frame = max(0.0, end_ms - previous)
        previous = end_ms
        if is_speech:
            speech_ms += frame
            silence_start = None
            if speech_ms >= arm_ms:
                armed = True
            continue
        if not armed:
            continue
        if silence_start is None:
            silence_start = end_ms - frame
        if end_ms - silence_start >= need_ms:
            return end_ms
    return None


def replay_semantic(
    consults: list[Consult],
    voiced: list[bool],
    *,
    kind: str,
    smart_threshold: float,
    livekit_threshold: float,
    consult_ms: float,
    cap_ms: float,
) -> tuple[float, str, list[float]]:
    """Replay one threshold on consults recorded while the model kept saying no.

    The firing rule matches ``Endpointer``: a new hypothesis is scored once,
    and a later cap frame still adds the last scoring time.
    """

    if kind not in {"smart", "livekit", "both"}:
        raise ValueError(kind)
    last_compute = 0.0
    spent: list[float] = []
    for item in consults:
        if item.silence_ms + 1e-9 < consult_ms:
            continue
        if kind == "smart":
            passed = item.smart_p > smart_threshold
            compute = item.smart_ms
        elif kind == "livekit":
            passed = item.livekit_p >= livekit_threshold
            compute = item.livekit_ms
        else:
            passed = item.smart_p > smart_threshold and item.livekit_p >= livekit_threshold
            compute = item.smart_ms + item.livekit_ms
        last_compute = compute
        spent.append(compute)
        if passed or item.silence_ms >= cap_ms:
            reason = "complete" if passed else "silence-cap"
            return item.audio_ms + compute, reason, spent
    cap_at = silence_reached(voiced, cap_ms)
    if cap_at is None:
        cap_at = len(voiced) * 20.0
    return cap_at + last_compute, "silence-cap", spent
