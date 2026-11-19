"""Split a token stream into clauses that can be spoken before the reply finishes."""

from __future__ import annotations

_HARD = "。！？!?"
_SOFT = "，、；：…"


class ClauseBuffer:
    """Hard punctuation always cuts. A comma cuts once the clause is long enough to say."""

    def __init__(self, soft_min_chars: int = 6) -> None:
        self._soft_min = soft_min_chars
        self._buf = ""

    def push(self, token: str) -> list[str]:
        self._buf += token
        clauses: list[str] = []
        while True:
            cut = self._cut_at()
            if cut is None:
                break
            clause = self._buf[:cut].strip()
            self._buf = self._buf[cut:]
            if clause:
                clauses.append(clause)
        return clauses

    def flush(self) -> str | None:
        clause = self._buf.strip()
        self._buf = ""
        return clause or None

    def _cut_at(self) -> int | None:
        for index, char in enumerate(self._buf):
            if char in _HARD and self._buf[: index + 1].strip():
                return index + 1
            if char in _SOFT and len(self._buf[: index + 1].strip()) >= self._soft_min:
                return index + 1
        return None
