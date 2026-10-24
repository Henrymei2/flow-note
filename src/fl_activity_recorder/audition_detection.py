"""Derive conservative, independent playback intervals from recorded evidence."""

from __future__ import annotations

import math
from typing import Any

MIN_DURATION = 8.0
MAX_DURATION = 300.0


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
