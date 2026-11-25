"""Settings from the environment. Secrets stay in the process environment, never in logs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(start: Path | None = None) -> Path | None:
    """Load the nearest ``.env`` without overriding variables already set.

    Values are not shell-quoted: ``MINIMAX_TTS_VOICE`` contains parentheses.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip():
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return candidate
    return None


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    volc_speech_key: str
    volc_asr_resource: str
    volc_asr_url: str
    dashscope_key: str
    dashscope_base_url: str
    qwen_model: str
    minimax_key: str
    minimax_base_url: str
    minimax_model: str
    minimax_voice: str
    minimax_rpm: int
    vad_model: Path

    @classmethod
    def from_environ(cls) -> Settings:
        return cls(
            volc_speech_key=_get("VOLC_SPEECH_API_KEY"),
            volc_asr_resource=_get("VOLC_ASR_RESOURCE", "volc.seedasr.sauc.duration"),
            volc_asr_url=_get(
                "VOLC_ASR_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
            ),
            dashscope_key=_get("DASHSCOPE_API_KEY"),
            dashscope_base_url=_get(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).rstrip("/"),
            qwen_model=_get("QWEN_MODEL", "qwen3.8-flash"),
            minimax_key=_get("MINIMAX_API_KEY"),
            minimax_base_url=_get("MINIMAX_BASE_URL", "https://api.minimaxi.com").rstrip("/"),
            minimax_model=_get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"),
            minimax_voice=_get("MINIMAX_TTS_VOICE", "Chinese (Mandarin)_IntellectualGirl"),
            # The account's synchronous T2A quota (console, 2026-11-25): 10 RPM.
            minimax_rpm=int(_get("MINIMAX_RPM", "10")),
            vad_model=Path(
                _get("AMADEUS_VAD_MODEL", str(AGENT_ROOT / "models" / "silero_vad.onnx"))
            ),
        )

    def missing(self) -> list[str]:
        out = []
        if not self.volc_speech_key:
            out.append("VOLC_SPEECH_API_KEY")
        if not self.dashscope_key:
            out.append("DASHSCOPE_API_KEY")
        if not self.minimax_key:
            out.append("MINIMAX_API_KEY")
        return out
