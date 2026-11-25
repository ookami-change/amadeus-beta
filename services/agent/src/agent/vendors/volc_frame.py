"""Volcengine streaming-recognition binary frames (the ASR half of the eval's ``volc_frame``).

4-byte header, big-endian sizes, gzip payloads.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass

MSG_FULL_CLIENT = 0x1
MSG_AUDIO_CLIENT = 0x2
MSG_ERROR = 0xF
FLAG_NO_SEQ = 0x0
FLAG_POS_SEQ = 0x1
FLAG_LAST = 0x2
FLAG_NEG_SEQ = 0x3
SER_RAW = 0x0
SER_JSON = 0x1
COMP_GZIP = 0x1


@dataclass(frozen=True)
class Frame:
    msg_type: int
    flags: int
    sequence: int | None
    error_code: int | None
    payload: bytes


def _header(msg_type: int, flags: int, serialization: int) -> bytes:
    return bytes((0x11, (msg_type << 4) | flags, (serialization << 4) | COMP_GZIP, 0x00))


def _sized(blob: bytes) -> bytes:
    return len(blob).to_bytes(4, "big") + blob


def full_client(payload: dict[str, object]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _header(MSG_FULL_CLIENT, FLAG_NO_SEQ, SER_JSON) + _sized(gzip.compress(raw))


def audio(pcm: bytes, *, last: bool) -> bytes:
    # No sequence field: the server resets the socket if one is sent. Last packet is 0b0010.
    flags = FLAG_LAST if last else FLAG_NO_SEQ
    return _header(MSG_AUDIO_CLIENT, flags, SER_RAW) + _sized(gzip.compress(pcm))


def parse(data: bytes) -> Frame:
    if len(data) < 4:
        raise ValueError(f"volc frame too short: {len(data)} bytes")
    offset = (data[0] & 0x0F) * 4
    if offset < 4 or offset > len(data):
        raise ValueError("bad volc header size")
    msg_type, flags, compression = data[1] >> 4, data[1] & 0x0F, data[2] & 0x0F
    sequence: int | None = None
    error_code: int | None = None

    def take(size: int) -> bytes:
        nonlocal offset
        if offset + size > len(data):
            raise ValueError("volc frame truncated")
        chunk = data[offset : offset + size]
        offset += size
        return chunk

    if msg_type == MSG_ERROR:
        error_code = int.from_bytes(take(4), "big")
    elif flags in (FLAG_POS_SEQ, FLAG_NEG_SEQ):
        sequence = int.from_bytes(take(4), "big", signed=True)
    payload = take(int.from_bytes(take(4), "big"))
    if compression == COMP_GZIP and payload:
        payload = gzip.decompress(payload)
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    return Frame(msg_type, flags, sequence, error_code, payload)


def hypothesis(frame: Frame) -> tuple[str, bool] | None:
    """``(text, definite)`` from a result frame, or ``None``."""
    if not frame.payload:
        return None
    try:
        body = json.loads(frame.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return None
    text = result.get("text")
    text = text if isinstance(text, str) else ""
    utterances = result.get("utterances")
    definite = isinstance(utterances, list) and any(
        isinstance(one, dict) and one.get("definite") is True for one in utterances
    )
    if not text and not definite:
        return None
    return text, definite
