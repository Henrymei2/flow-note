from __future__ import annotations

import pytest

from fl_activity_recorder.protocol import (
    MessageDecoder,
    MessageKind,
    ProtocolError,
    encode_message,
)


def test_unicode_fragmented_round_trip() -> None:
    payload = {"summary": "合成器を調整 🎛️", "values": list(range(80))}
    frames = encode_message(MessageKind.EVENT, 1234, payload, max_frame_size=80)
    assert len(frames) > 1
    assert all(len(frame) <= 80 for frame in frames)
    decoder = MessageDecoder()
    decoded = None
    for frame in reversed(frames):
        decoded = decoder.feed(frame) or decoded
    assert decoded is not None
    assert decoded.kind is MessageKind.EVENT
    assert decoded.sequence == 1234
    assert decoded.payload == payload


def test_duplicate_identical_chunk_is_safe() -> None:
    frames = encode_message(MessageKind.SNAPSHOT, 7, {"text": "x" * 300}, max_frame_size=70)
    decoder = MessageDecoder()
    assert decoder.feed(frames[0]) is None
    assert decoder.feed(frames[0]) is None
    decoded = None
    for frame in frames[1:]:
        decoded = decoder.feed(frame) or decoded
    assert decoded is not None


def test_crc_failure_is_rejected() -> None:
    frame = bytearray(encode_message(MessageKind.EVENT, 9, {"ok": True})[0])
    frame[-2] = ord("A") if frame[-2] != ord("A") else ord("B")
    with pytest.raises(ProtocolError, match="CRC|encoded"):
        MessageDecoder().feed(frame)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda frame: bytes([0, *frame[1:]]),
        lambda frame: bytes([*frame[:-1], 0]),
        lambda frame: bytes([frame[0], 0x01, *frame[2:]]),
        lambda frame: bytes([frame[0], frame[1], 0x7F, *frame[3:]]),
    ],
)
def test_invalid_headers_are_rejected(mutation: object) -> None:
    frame = encode_message(MessageKind.HELLO, 1, {})[0]
    with pytest.raises(ProtocolError):
        MessageDecoder().feed(mutation(frame))  # type: ignore[operator]
