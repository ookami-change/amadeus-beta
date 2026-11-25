"""Conversation protocol, Python binding of ``packages/protocol/spec.json``.

The spec is the definition. ``tests/test_protocol.py`` checks that every class
here has exactly the fields and types the spec lists, and that the shared
``fixtures.json`` round-trips.
"""

from __future__ import annotations

import json
import struct
import types
import typing
from dataclasses import asdict, dataclass, fields
from typing import Any, ClassVar, Literal, Union

PROTOCOL_VERSION = "0.1.0"
MIC_TAG = 1
SPEECH_TAG = 2
MIC_SAMPLE_RATE = 16000
SPEECH_SAMPLE_RATE = 32000

DirectorState = Literal["idle", "listening", "thinking", "speaking", "interrupted"]


@dataclass(frozen=True)
class LipsyncMark:
    start_ms: float
    end_ms: float
    text: str


@dataclass(frozen=True)
class Message:
    TYPE: ClassVar[str] = ""
    FROM: ClassVar[str] = ""

    def to_json(self) -> str:
        return json.dumps(
            {"type": self.TYPE, **asdict(self)}, ensure_ascii=False, separators=(",", ":")
        )


@dataclass(frozen=True)
class Hello(Message):
    TYPE: ClassVar[str] = "hello"
    FROM: ClassVar[str] = "client"
    protocol: str
    client: str
    sample_rate: int


@dataclass(frozen=True)
class Ping(Message):
    TYPE: ClassVar[str] = "ping"
    FROM: ClassVar[str] = "client"
    id: int
    client_ms: float


@dataclass(frozen=True)
class Playback(Message):
    TYPE: ClassVar[str] = "playback"
    FROM: ClassVar[str] = "client"
    utterance: int
    event: Literal["started", "stopped", "finished"]
    played_ms: float
    client_ms: float


@dataclass(frozen=True)
class Bye(Message):
    TYPE: ClassVar[str] = "bye"
    FROM: ClassVar[str] = "client"


@dataclass(frozen=True)
class Welcome(Message):
    TYPE: ClassVar[str] = "welcome"
    FROM: ClassVar[str] = "server"
    protocol: str
    session: str
    speech_rate: int


@dataclass(frozen=True)
class Pong(Message):
    TYPE: ClassVar[str] = "pong"
    FROM: ClassVar[str] = "server"
    id: int
    client_ms: float
    server_ms: float


@dataclass(frozen=True)
class Transcript(Message):
    TYPE: ClassVar[str] = "transcript"
    FROM: ClassVar[str] = "server"
    turn: int
    text: str
    final: bool


@dataclass(frozen=True)
class State(Message):
    TYPE: ClassVar[str] = "state"
    FROM: ClassVar[str] = "server"
    state: DirectorState
    turn: int | None


@dataclass(frozen=True)
class Prepare(Message):
    TYPE: ClassVar[str] = "prepare"
    FROM: ClassVar[str] = "server"
    turn: int


@dataclass(frozen=True)
class SpeakStart(Message):
    TYPE: ClassVar[str] = "speak_start"
    FROM: ClassVar[str] = "server"
    utterance: int
    turn: int


@dataclass(frozen=True)
class SpeakText(Message):
    TYPE: ClassVar[str] = "speak_text"
    FROM: ClassVar[str] = "server"
    utterance: int
    text: str


@dataclass(frozen=True)
class Lipsync(Message):
    TYPE: ClassVar[str] = "lipsync"
    FROM: ClassVar[str] = "server"
    utterance: int
    marks: list[LipsyncMark]


@dataclass(frozen=True)
class SpeakEnd(Message):
    TYPE: ClassVar[str] = "speak_end"
    FROM: ClassVar[str] = "server"
    utterance: int
    reason: Literal["done", "interrupted", "error"]


@dataclass(frozen=True)
class StopAudio(Message):
    TYPE: ClassVar[str] = "stop_audio"
    FROM: ClassVar[str] = "server"
    utterance: int


@dataclass(frozen=True)
class TurnReport(Message):
    TYPE: ClassVar[str] = "turn_report"
    FROM: ClassVar[str] = "server"
    turn: int
    onset_ms: float
    commit_ms: float | None
    commit_text: str
    final_text: str
    launches: int
    cancelled: int
    restarts: int
    launch_ms: float | None
    first_token_ms: float | None
    ready_ms: float | None
    speak_ms: float | None
    error: str | None


@dataclass(frozen=True)
class Error(Message):
    TYPE: ClassVar[str] = "error"
    FROM: ClassVar[str] = "server"
    code: str
    message: str
    retryable: bool


MESSAGES: dict[str, type[Message]] = {
    cls.TYPE: cls
    for cls in (
        Hello,
        Ping,
        Playback,
        Bye,
        Welcome,
        Pong,
        Transcript,
        State,
        Prepare,
        SpeakStart,
        SpeakText,
        Lipsync,
        SpeakEnd,
        StopAudio,
        TurnReport,
        Error,
    )
}


class ProtocolError(ValueError):
    pass


def kind_of(hint: Any) -> str:
    """The spec.json spelling of a field type.

    For example ``int``, ``number?``, ``enum:a|b`` or ``lipsync_mark[]``.
    """
    origin = typing.get_origin(hint)
    if origin in (Union, types.UnionType):
        args = [arg for arg in typing.get_args(hint) if arg is not type(None)]
        if len(args) != 1:
            raise TypeError(f"unsupported union {hint}")
        return kind_of(args[0]) + "?"
    if origin is Literal:
        return "enum:" + "|".join(str(arg) for arg in typing.get_args(hint))
    if origin is list:
        return kind_of(typing.get_args(hint)[0]) + "[]"
    if hint is LipsyncMark:
        return "lipsync_mark"
    simple = {str: "string", int: "int", float: "number", bool: "bool"}
    if hint in simple:
        return simple[hint]
    raise TypeError(f"unsupported field type {hint}")


def field_kinds(cls: type[Any]) -> dict[str, str]:
    hints = typing.get_type_hints(cls)
    return {one.name: kind_of(hints[one.name]) for one in fields(cls)}


def _value(hint: Any, value: object, path: str) -> object:
    kind = kind_of(hint)
    origin = typing.get_origin(hint)
    if kind.endswith("?"):
        if value is None:
            return None
        inner = next(arg for arg in typing.get_args(hint) if arg is not type(None))
        return _value(inner, value, path)
    if origin is list:
        if not isinstance(value, list):
            raise ProtocolError(f"{path}: expected an array")
        inner = typing.get_args(hint)[0]
        return [_value(inner, one, f"{path}[{index}]") for index, one in enumerate(value)]
    if origin is Literal:
        if value not in typing.get_args(hint):
            raise ProtocolError(f"{path}: expected {kind}")
        return value
    if hint is LipsyncMark:
        return LipsyncMark(**_fields(LipsyncMark, value, path))  # type: ignore[arg-type]
    if hint is bool:
        if not isinstance(value, bool):
            raise ProtocolError(f"{path}: expected bool")
        return value
    if hint is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{path}: expected int")
        return value
    if hint is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolError(f"{path}: expected number")
        return float(value)
    if hint is str:
        if not isinstance(value, str):
            raise ProtocolError(f"{path}: expected string")
        return value
    raise ProtocolError(f"{path}: unsupported type")


def _fields(cls: type[Any], raw: object, path: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ProtocolError(f"{path}: expected an object")
    hints = typing.get_type_hints(cls)
    out: dict[str, object] = {}
    for one in fields(cls):
        if one.name not in raw:
            raise ProtocolError(f"{path}.{one.name}: missing")
        out[one.name] = _value(hints[one.name], raw[one.name], f"{path}.{one.name}")
    return out


def parse(text: str | bytes) -> Message:
    """Parse one text frame. Raises ``ProtocolError`` on anything the spec does not allow."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProtocolError("message: expected an object")
    cls = MESSAGES.get(str(raw.get("type")))
    if cls is None:
        raise ProtocolError(f"message: unknown type {raw.get('type')!r}")
    return cls(**_fields(cls, raw, cls.TYPE))


def mic_pcm(frame: bytes) -> bytes:
    """PCM out of a client mic frame."""
    if not frame or frame[0] != MIC_TAG:
        raise ProtocolError("not a mic frame")
    return frame[1:]


def speech_frame(utterance: int, pcm: bytes) -> bytes:
    return struct.pack("<BI", SPEECH_TAG, utterance) + pcm
