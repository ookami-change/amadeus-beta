"""Load the fixed persona used by the latency benchmark."""

from __future__ import annotations

from pathlib import Path

PERSONA_PATH = Path(__file__).resolve().parent / "persona.md"


def load_persona() -> str:
    return PERSONA_PATH.read_text(encoding="utf-8").strip() + "\n"
