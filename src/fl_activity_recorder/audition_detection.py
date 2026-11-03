"""Derive conservative, independent playback intervals from recorded evidence."""

from __future__ import annotations

import math
from typing import Any

MIN_DURATION = 0.0
MAX_DURATION = 300.0
DEFAULT_BPM = 100.0


def detect_auditions(
    events: list[dict[str, Any]], diagnostics: list[dict[str, Any]], duration: float
) -> dict[str, Any]:
    """Pair explicit playback edges; snapshots establish state but never invent edges."""
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    opened: dict[str, Any] | None = None
    playing: bool | None = None
    uncertain = False
    for event in sorted(events, key=lambda e: (e.get("timeline_seconds") or 0, e["id"])):
        time = event.get("timeline_seconds")
        if time is None or not math.isfinite(time) or time < 0 or time > duration:
            continue
        payload = event["payload"]
        snapshot = event["action"] in {"snapshot", "initial_snapshot"}
        after = payload.get("playing") if snapshot else payload.get("after")
        if not snapshot and event["action"] != "playing_changed":
            continue
        if not isinstance(after, bool):
            continue
        if snapshot:
            if playing is not None and playing != after:
                uncertain = True
            if after and opened is None:
                opened = event
                uncertain = True
            playing = after
            continue
        if after:
            if opened is None:
                opened = event
                uncertain = playing is True or payload.get("before") is not False
            playing = True
            continue
        if opened is None:
            if playing is not False:
                warnings.append(f"Unpaired playback stop at {time:.3f}s")
            playing = False
            continue
        start = float(opened["timeline_seconds"])
        gap = any(
            d["code"] == "sequence_gap"
            and d["payload"].get("after", -1) < event["sequence"]
            and d["payload"].get("before", -1) > opened["sequence"]
            for d in diagnostics
        )
        reason = "Incomplete playback evidence" if uncertain or gap else None
        if payload.get("before") is not True:
            reason = "Conflicting playback state"
        length = time - start
        if length >= MIN_DURATION or reason:
            items.append({
                "id": f"audition_{opened['id']}_{event['id']}",
                "start_time": start, "end_time": time, "duration": length,
                "complete": reason is None,
                "eligible": reason is None and MIN_DURATION <= length <= MAX_DURATION,
                "reason": reason or ("Auditions longer than 5 minutes are unavailable"
                                     if length > MAX_DURATION else None),
            })
        opened = None
        uncertain = False
        playing = False
    if opened is not None:
        items.append({
            "id": f"audition_{opened['id']}_incomplete",
            "start_time": opened["timeline_seconds"], "end_time": duration,
            "duration": duration - opened["timeline_seconds"], "complete": False,
            "eligible": False, "reason": "Playback has no recorded stop",
        })
    return {"auditions": items, "diagnostics": warnings}


def resolve_audition_tempo(
    events: list[dict[str, Any]], start: float, end: float
) -> tuple[float, str]:
    """Resolve recorded tempo or label a temporary fallback when evidence is absent."""
    current: float | None = None
    selected: float | None = None
    for event in sorted(events, key=lambda e: (e.get("timeline_seconds") or 0, e["id"])):
        timestamp = event.get("timeline_seconds")
        if timestamp is None or not math.isfinite(timestamp) or timestamp < 0 or timestamp >= end:
            continue
        payload = event["payload"]
        action = event["action"]
        if action in {"snapshot", "initial_snapshot"} and "tempo" in payload:
            value = payload["tempo"]
        elif action == "tempo_changed":
            value = payload.get("after")
            before = payload.get("before")
            if (
                timestamp > start
                and current is not None
                and before is not None
                and before != current
            ):
                raise ValueError("Conflicting BPM evidence in this audition")
        else:
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 10 <= value <= 522
        ):
            current = None
            selected = None
            if timestamp >= start:
                raise ValueError("BPM is missing or invalid in this audition")
            continue
        value = float(value)
        if timestamp > start:
            if selected is None:
                selected = current
            if selected is None:
                raise ValueError("No reliable BPM at the start of this audition")
            if value != selected:
                raise ValueError("BPM changes during this audition")
        current = value
        if timestamp <= start:
            selected = value
    if selected is None:
        return DEFAULT_BPM, "default"
    return selected, "recorded"


def audition_tempo(events: list[dict[str, Any]], start: float, end: float) -> float:
    """Return constant recorded BPM, or 100 BPM for historical intervals without tempo."""
    return resolve_audition_tempo(events, start, end)[0]
