"""Volcengine binary frames.

ASR and TTS share the 4-byte header and big-endian sizes, then diverge.
ASR follows the streaming-recognition layout (sequence flags, gzip payload).
TTS follows the v3 event layout (event number, then session id or connection id).

Integer fields are big-endian, as specified by the vendor docs.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass

PROTOCOL_V1 = 0x1
HEADER_WORDS = 0x1

MSG_FULL_CLIENT = 0x1
MSG_AUDIO_CLIENT = 0x2
MSG_FULL_SERVER = 0x9
MSG_AUDIO_SERVER = 0xB
MSG_ERROR = 0xF

FLAG_NO_SEQ = 0x0
FLAG_POS_SEQ = 0x1
FLAG_LAST = 0x2
FLAG_NEG_SEQ = 0x3
FLAG_EVENT = 0x4

SER_RAW = 0x0
SER_JSON = 0x1
COMP_NONE = 0x0
COMP_GZIP = 0x1

# Connection-level TTS events carry no session id.
_NO_SESSION = {1, 2, 50, 51, 52}
# These server events carry a connection id instead.
_HAS_CONNECT = {50, 51, 52}


@dataclass(frozen=True)
class VolcFrame:
    msg_type: int
    flags: int
    serialization: int
    compression: int
    sequence: int | None
    error_code: int | None
    event: int | None
    session_id: str | None
    connect_id: str | None
    payload: bytes

    def json_payload(self) -> dict[str, object]:
        if not self.payload:
            return {}
        parsed = json.loads(self.payload.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}


def _header(msg_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes(
        (
            (PROTOCOL_V1 << 4) | HEADER_WORDS,
            (msg_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        )
    )


def _sized(blob: bytes) -> bytes:
    return len(blob).to_bytes(4, "big") + blob


def build_asr_full_client(payload: dict[str, object]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw)
    return _header(MSG_FULL_CLIENT, FLAG_NO_SEQ, SER_JSON, COMP_GZIP) + _sized(compressed)


def build_asr_audio(pcm: bytes, *, sequence: int, last: bool) -> bytes:
    flags = FLAG_NEG_SEQ if last else FLAG_POS_SEQ
    seq = -abs(sequence) if last else sequence
    compressed = gzip.compress(pcm)
    return (
        _header(MSG_AUDIO_CLIENT, flags, SER_RAW, COMP_GZIP)
        + int(seq).to_bytes(4, "big", signed=True)
        + _sized(compressed)
    )


def build_tts_event(
    event: int,
    payload: dict[str, object] | None = None,
    *,
    session_id: str | None = None,
) -> bytes:
    body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    frame = bytearray(_header(MSG_FULL_CLIENT, FLAG_EVENT, SER_JSON, COMP_NONE))
    frame += int(event).to_bytes(4, "big", signed=True)
    if event not in _NO_SESSION:
        sid = (session_id or "").encode("utf-8")
        frame += _sized(sid)
    frame += _sized(body)
    return bytes(frame)


def parse_frame(data: bytes) -> VolcFrame:
    if len(data) < 4:
        raise ValueError(f"volc frame too short: {len(data)} bytes")
    header_bytes = (data[0] & 0x0F) * 4
    if header_bytes < 4 or header_bytes > len(data):
        raise ValueError(f"bad volc header size: {header_bytes}")
    msg_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = header_bytes

    sequence: int | None = None
    error_code: int | None = None
    event: int | None = None
    session_id: str | None = None
    connect_id: str | None = None

    if msg_type == MSG_ERROR:
        error_code, offset = _u32(data, offset)
    elif flags in (FLAG_POS_SEQ, FLAG_NEG_SEQ):
        sequence, offset = _i32(data, offset)

    if flags == FLAG_EVENT:
        event, offset = _i32(data, offset)
        if event not in _NO_SESSION:
            session_id, offset = _string(data, offset)
        if event in _HAS_CONNECT:
            connect_id, offset = _string(data, offset)

    payload, offset = _blob(data, offset)
    if compression == COMP_GZIP and payload:
        payload = gzip.decompress(payload)
    if serialization == SER_JSON and payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    return VolcFrame(
        msg_type=msg_type,
        flags=flags,
        serialization=serialization,
        compression=compression,
        sequence=sequence,
        error_code=error_code,
        event=event,
        session_id=session_id,
        connect_id=connect_id,
        payload=payload,
    )


def _need(data: bytes, offset: int, size: int) -> None:
    if offset + size > len(data):
        raise ValueError("volc frame truncated")


def _u32(data: bytes, offset: int) -> tuple[int, int]:
    _need(data, offset, 4)
    return int.from_bytes(data[offset : offset + 4], "big", signed=False), offset + 4


def _i32(data: bytes, offset: int) -> tuple[int, int]:
    _need(data, offset, 4)
    return int.from_bytes(data[offset : offset + 4], "big", signed=True), offset + 4


def _blob(data: bytes, offset: int) -> tuple[bytes, int]:
    size, offset = _u32(data, offset)
    _need(data, offset, size)
    return data[offset : offset + size], offset + size


def _string(data: bytes, offset: int) -> tuple[str, int]:
    blob, offset = _blob(data, offset)
    return blob.decode("utf-8"), offset
