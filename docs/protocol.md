# SysEx protocol v1

Messages are UTF-8 JSON, Base64URL encoded without padding, fragmented into SysEx frames, and protected by CRC32. Every byte between `F0` and `F7` is 7-bit safe.

## Frame

| Field | Bytes | Description |
| --- | ---: | --- |
| Start | 1 | `F0` |
| Manufacturer | 1 | `7D`, MIDI educational/private-use ID |
| Version | 1 | `01` |
| Kind | 1 | Hello=1, heartbeat=2, event=3, snapshot=4, diagnostic=5 |
| Sequence | 5 | Big-endian base-128 integer |
| Chunk index | 2 | Zero-based base-128 integer |
| Chunk count | 2 | Base-128 integer |
| CRC32 | 5 | Base-128 CRC of the unencoded JSON bytes |
| Payload | variable | Base64URL fragment |
| End | 1 | `F7` |

The companion reassembles chunks by kind, sequence, count, and CRC. Conflicting duplicates, malformed JSON, CRC failures, and unsupported versions are rejected. A sequence gap is stored as a diagnostic. Duplicate or out-of-order complete messages are ignored.

The observer emits a heartbeat once per second. Five seconds without a heartbeat marks an active recording as interrupted.

`HELLO` includes the FL/API versions plus `observer_version`, `observer_schema`,
`observer_build`, and the supported capability names. Protocol framing remains version 1;
observer schema 3 describes the unified-timeline event catalog, trigger provenance, and an
`observed_monotonic_seconds` timestamp captured when the source callback first detects an event. The
companion refuses to start a session unless the observer schema and build match.
