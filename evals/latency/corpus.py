"""Load the generated corpus and reveal oracle partials along the timeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from latency.scoring import Pause


@dataclass(frozen=True)
class Segment:
    text: str
    start_ms: float
    end_ms: float


@dataclass(frozen=True)
class Item:
    item_id: str
    category: str
    text: str
    audio: Path
    speech_end_ms: float
    segments: tuple[Segment, ...]
    pauses: tuple[Pause, ...]

    def oracle_text(self, t_ms: float) -> str:
        """Words a perfect streaming ASR would already have at ``t_ms``."""

        spoken: list[str] = []
        for segment in self.segments:
            if t_ms >= segment.end_ms - 1.0:
                spoken.append(segment.text)
            elif t_ms >= segment.start_ms:
                span = max(1.0, segment.end_ms - segment.start_ms)
                fraction = min(1.0, (t_ms - segment.start_ms) / span)
                count = max(1, int(round(len(segment.text) * fraction)))
                spoken.append(segment.text[:count])
                break
            else:
                break
        return "".join(spoken)


def load_corpus(manifest: Path) -> list[Item]:
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("corpus manifest must be an object")
    items = raw.get("items")
    if not isinstance(items, list):
        raise ValueError("corpus manifest has no items")
    root = manifest.parent
    loaded: list[Item] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        loaded.append(_item(entry, root))
    return loaded


def _item(entry: dict[str, object], root: Path) -> Item:
    audio = entry.get("audio")
    if not isinstance(audio, str):
        raise ValueError("corpus item missing audio")
    segments = tuple(_segment(piece) for piece in _list(entry.get("segments")))
    pauses = tuple(_pause(piece) for piece in _list(entry.get("pauses")))
    speech_end = entry.get("speech_end_ms")
    return Item(
        item_id=str(entry.get("id")),
        category=str(entry.get("category")),
        text=str(entry.get("text")),
        audio=root / audio,
        speech_end_ms=float(speech_end) if isinstance(speech_end, (int, float)) else 0.0,
        segments=segments,
        pauses=pauses,
    )


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _segment(value: object) -> Segment:
    if not isinstance(value, dict):
        raise ValueError("bad segment")
    return Segment(
        text=str(value.get("text") or ""),
        start_ms=float(value.get("start_ms") or 0),
        end_ms=float(value.get("end_ms") or 0),
    )


def _pause(value: object) -> Pause:
    if not isinstance(value, dict):
        raise ValueError("bad pause")
    return Pause(start_ms=float(value.get("start_ms") or 0), end_ms=float(value.get("end_ms") or 0))
