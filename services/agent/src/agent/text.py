"""Text rules used by the turn engine, from R3c (connectors) and R6 (same-turn check, clauses)."""

from __future__ import annotations

import difflib
import re

# A partial ending on one of these is a turn that is still going (R3c S1).
CONNECTORS = (
    "然后", "就是", "所以", "但是", "可是", "不过", "而且", "因为", "如果", "要是", "的话",
    "还有", "或者", "比如", "那个", "这个", "那", "就", "和", "跟", "嗯", "呃", "额",
)  # fmt: skip
STRIP = "，,、；;：: \t\n"
_NOT_CONTENT = re.compile(r"[\s\W_]+", re.UNICODE)


def ends_with_connector(text: str) -> bool:
    bare = text.rstrip(STRIP)
    return bool(bare) and bare.endswith(CONNECTORS)


def content(text: str) -> str:
    return _NOT_CONTENT.sub("", text)


def texts_continue(started: str, now: str) -> bool:
    """True when ``now`` is the same turn the launched text ``started`` (R6: ratio >= 0.85)."""
    left, right = content(started), content(now)
    if left == right:
        return True
    if not left or not right:
        return False
    return difflib.SequenceMatcher(a=left, b=right).ratio() >= 0.85


_HARD = "。！？!?"
_SOFT = "，、；：…"


class ClauseBuffer:
    """Hard punctuation always cuts. A comma cuts once the clause is long enough to say."""

    def __init__(self, soft_min_chars: int = 6) -> None:
        self._soft_min = soft_min_chars
        self._buf = ""

    def push(self, token: str) -> list[str]:
        self._buf += token
        out: list[str] = []
        while (cut := self._cut_at()) is not None:
            clause, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if clause:
                out.append(clause)
        return out

    def flush(self) -> str | None:
        clause, self._buf = self._buf.strip(), ""
        return clause or None

    def _cut_at(self) -> int | None:
        for index, char in enumerate(self._buf):
            if char in _HARD and self._buf[: index + 1].strip():
                return index + 1
            if char in _SOFT and len(self._buf[: index + 1].strip()) >= self._soft_min:
                return index + 1
        return None
