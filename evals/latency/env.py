"""Environment loading. Secrets stay in the process environment, never in logs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(start: Path | None = None) -> Path | None:
    """Load the nearest ``.env`` without overriding variables already set."""

    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            _apply_env_file(candidate)
            return candidate
    return None


def _apply_env_file(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    volc_speech_key: str
    volc_app_id: str
    volc_access_token: str
    ark_key: str
    ark_base_url: str
    ark_model: str
    dashscope_key: str
    dashscope_workspace: str
    dashscope_base_url: str
    dashscope_ws_url: str
    qwen_model: str
    minimax_key: str
    minimax_base_url: str
    minimax_model: str
    volc_voice: str
    volc_tts_resource: str
    minimax_voice: str
    cosyvoice_model: str
    cosyvoice_voice: str
    cosyvoice_instruction: bool
    volc_asr_resource: str
    volc_asr_url: str
    dashscope_asr_model: str

    @classmethod
    def from_environ(cls) -> Settings:
        workspace = _get("DASHSCOPE_WORKSPACE_ID")
        explicit_ws = _get("DASHSCOPE_WS_URL")
        if explicit_ws:
            ws_url = explicit_ws
        elif workspace:
            ws_url = f"wss://{workspace}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
        else:
            ws_url = ""
        return cls(
            volc_speech_key=_get("VOLC_SPEECH_API_KEY"),
            volc_app_id=_get("VOLC_APP_ID"),
            volc_access_token=_get("VOLC_ACCESS_TOKEN"),
            ark_key=_get("ARK_API_KEY"),
            ark_base_url=_get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"),
            ark_model=_get("ARK_MODEL", "deepseek-v4-1-flash-261017"),
            dashscope_key=_get("DASHSCOPE_API_KEY"),
            dashscope_workspace=workspace,
            dashscope_base_url=_get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip(
                "/"
            ),
            dashscope_ws_url=ws_url,
            qwen_model=_get("QWEN_MODEL", "qwen3.8-flash"),
            minimax_key=_get("MINIMAX_API_KEY"),
            minimax_base_url=_get("MINIMAX_BASE_URL", "https://api.minimaxi.com").rstrip("/"),
            minimax_model=_get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"),
            volc_voice=_get("VOLC_TTS_VOICE", "zh_female_zhixingnv_uranus_bigtts"),
            volc_tts_resource=_get("VOLC_TTS_RESOURCE", "seed-tts-2.0"),
            minimax_voice=_get("MINIMAX_TTS_VOICE", "Chinese (Mandarin)_IntellectualGirl"),
            cosyvoice_model=_get("COSYVOICE_MODEL", "cosyvoice-v3-flash"),
            cosyvoice_voice=_get("COSYVOICE_VOICE", "longanwen_v3"),
            cosyvoice_instruction=_get("COSYVOICE_INSTRUCTION", "0") in {"1", "true", "yes"},
            volc_asr_resource=_get("VOLC_ASR_RESOURCE", "volc.seedasr.sauc.duration"),
            volc_asr_url=_get("VOLC_ASR_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"),
            dashscope_asr_model=_get("DASHSCOPE_ASR_MODEL", "paraformer-realtime-v2"),
        )

    def volc_headers(self, *, resource: str, request_id: str) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-Api-Resource-Id": resource,
            "X-Api-Request-Id": request_id,
        }
        if self.volc_speech_key:
            headers["X-Api-Key"] = self.volc_speech_key
        elif self.volc_app_id and self.volc_access_token:
            headers["X-Api-App-Key"] = self.volc_app_id
            headers["X-Api-Access-Key"] = self.volc_access_token
        return headers

    def missing_for(self, asr: str, llm: str, tts: str) -> list[str]:
        missing: list[str] = []
        if asr == "volc" or tts == "volc":
            if not self.volc_speech_key and not (self.volc_app_id and self.volc_access_token):
                missing.append("VOLC_SPEECH_API_KEY")
        if llm == "doubao" and not self.ark_key:
            missing.append("ARK_API_KEY")
        if asr == "dashscope" or llm == "qwen" or tts == "dashscope":
            if not self.dashscope_key:
                missing.append("DASHSCOPE_API_KEY")
        if (asr == "dashscope" or tts == "dashscope") and not self.dashscope_ws_url:
            missing.append("DASHSCOPE_WORKSPACE_ID")
        if tts == "minimax" and not self.minimax_key:
            missing.append("MINIMAX_API_KEY")
        seen: set[str] = set()
        unique: list[str] = []
        for name in missing:
            if name not in seen:
                seen.add(name)
                unique.append(name)
        return unique

    def presence(self) -> dict[str, bool]:
        """Booleans only. Safe to print."""

        return {
            "VOLC_SPEECH_API_KEY": bool(self.volc_speech_key),
            "VOLC_APP_ID": bool(self.volc_app_id),
            "VOLC_ACCESS_TOKEN": bool(self.volc_access_token),
            "ARK_API_KEY": bool(self.ark_key),
            "DASHSCOPE_API_KEY": bool(self.dashscope_key),
            "DASHSCOPE_WORKSPACE_ID": bool(self.dashscope_workspace),
            "MINIMAX_API_KEY": bool(self.minimax_key),
        }
