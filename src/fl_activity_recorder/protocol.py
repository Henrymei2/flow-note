from __future__ import annotations

import base64
import binascii
import json
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

SYSEX_START = 0xF0
SYSEX_END = 0xF7
PRIVATE_MANUFACTURER_ID = 0x7D
PROTOCOL_VERSION = 1
MAX_FRAME_SIZE = 1024
HEADER_SIZE = 18
FRAME_OVERHEAD = HEADER_SIZE + 1


class ProtocolError(ValueError):
    """Raised when a SysEx frame cannot be decoded safely."""


class MessageKind(IntEnum):
    HELLO = 1
    HEARTBEAT = 2
    EVENT = 3
    SNAPSHOT = 4
    DIAGNOSTIC = 5


@dataclass(frozen=True, slots=True)
class DecodedMessage:
    kind: MessageKind
    sequence: int
    payload: dict[str, Any]


def _pack7(value: int, width: int) -> bytes:
    if value < 0 or value >= 128**width:
        raise ProtocolError(f"value {value} does not fit in {width} 7-bit bytes")
    return bytes((value >> (7 * offset)) & 0x7F for offset in reversed(range(width)))


def _unpack7(data: bytes) -> int:
    value = 0
    for byte in data:
        if byte > 0x7F:
            raise ProtocolError("non-7-bit value in SysEx header")
        value = (value << 7) | byte
    return value


def encode_message(
    kind: MessageKind,
    sequence: int,
    payload: dict[str, Any],
    *,
    max_frame_size: int = MAX_FRAME_SIZE,
) -> list[bytes]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    crc = zlib.crc32(raw) & 0xFFFFFFFF
    capacity = max_frame_size - FRAME_OVERHEAD
    if capacity < 32:
        raise ProtocolError("frame size is too small")
    chunks = [encoded[index : index + capacity] for index in range(0, len(encoded), capacity)]
    if not chunks:
        chunks = [b""]
    if len(chunks) >= 128**2:
        raise ProtocolError("message needs too many chunks")
    frames: list[bytes] = []
    for index, chunk in enumerate(chunks):
        header = bytes([SYSEX_START, PRIVATE_MANUFACTURER_ID, PROTOCOL_VERSION, int(kind)])
        header += _pack7(sequence, 5)
        header += _pack7(index, 2)
        header += _pack7(len(chunks), 2)
        header += _pack7(crc, 5)
        frames.append(header + chunk + bytes([SYSEX_END]))
    return frames


class MessageDecoder:
    def __init__(self) -> None:
        self._pending: dict[tuple[int, int, int, int], dict[int, bytes]] = {}

    def feed(self, frame: bytes | bytearray | tuple[int, ...] | list[int]) -> DecodedMessage | None:
        data = bytes(frame)
        if len(data) < HEADER_SIZE:
            raise ProtocolError("frame is truncated")
        if data[0] != SYSEX_START or data[-1] != SYSEX_END:
            raise ProtocolError("invalid SysEx boundaries")
        if data[1] != PRIVATE_MANUFACTURER_ID:
            raise ProtocolError("unexpected manufacturer ID")
        if data[2] != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version {data[2]}")
        try:
            kind = MessageKind(data[3])
        except ValueError as exc:
            raise ProtocolError(f"unknown message kind {data[3]}") from exc
        sequence = _unpack7(data[4:9])
        chunk_index = _unpack7(data[9:11])
        chunk_count = _unpack7(data[11:13])
        crc = _unpack7(data[13:18])
        if chunk_count == 0 or chunk_index >= chunk_count:
            raise ProtocolError("invalid chunk metadata")
        chunk = data[18:-1]
        if any(byte > 0x7F for byte in chunk):
            raise ProtocolError("payload is not 7-bit safe")
        key = (int(kind), sequence, chunk_count, crc)
        fragments = self._pending.setdefault(key, {})
        previous = fragments.get(chunk_index)
        if previous is not None and previous != chunk:
            self._pending.pop(key, None)
            raise ProtocolError("conflicting duplicate chunk")
        fragments[chunk_index] = chunk
        if len(fragments) != chunk_count:
            return None
        self._pending.pop(key, None)
        encoded = b"".join(fragments[index] for index in range(chunk_count))
        encoded += b"=" * (-len(encoded) % 4)
        try:
            raw = base64.urlsafe_b64decode(encoded)
            if zlib.crc32(raw) & 0xFFFFFFFF != crc:
                raise ProtocolError("CRC mismatch")
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            if isinstance(exc, ProtocolError):
                raise
            raise ProtocolError("invalid encoded payload") from exc
        if not isinstance(payload, dict):
            raise ProtocolError("payload must be a JSON object")
        return DecodedMessage(kind=kind, sequence=sequence, payload=payload)
