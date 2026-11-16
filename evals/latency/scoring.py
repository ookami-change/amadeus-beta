"""Turn an endpoint time into the false-barge-in flag the report uses."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Pause:
    start_ms: float
    end_ms: float


def classify_endpoint(
    *,
    speech_end_ms: float,
    pauses: list[Pause],
    t_endpoint_ms: float,
    tolerance_ms: float = 80.0,
) -> tuple[bool, bool]:
    """Return ``(early, false_barge_in)``.

    A false barge-in is an endpoint that lands in a labeled interior pause,
    before the utterance's real ending. The five long sentences carry those pauses.
    """

    early = t_endpoint_ms < speech_end_ms - tolerance_ms
    if not early or not pauses:
        return early, False
    for pause in pauses:
        if pause.start_ms - 40.0 <= t_endpoint_ms <= pause.end_ms + tolerance_ms:
            return True, True
    return True, False


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. ``pct`` is 50 or 95, not a fraction."""

    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]
