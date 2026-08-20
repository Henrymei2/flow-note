from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPECTED_EVENT_CLASSES = (
    ("transport", "playing_changed"),
    ("channel", "selection_changed"),
    ("channel", "renamed"),
    ("channel", "recolored"),
    ("pattern", "selection_changed"),
    ("pattern", "renamed"),
    ("pattern", "recolored"),
    ("pattern", "steps_changed"),
    ("mixer", "selection_changed"),
    ("mixer", "track_changed"),
    ("mixer", "renamed"),
    ("mixer", "recolored"),
    ("plugin", "parameters_changed"),
    ("plugin", "controls_changed"),
    ("project_edit", "edit"),
    ("project_edit", "undo"),
    ("project_edit", "redo"),
    ("navigation", "focus_changed"),
    ("playlist", "playlist_changed"),
    ("system", "host_refresh"),
)


def build_report(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    session: dict[str, Any] = {}
    observed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON on line {line_number}: {error.msg}") from error
            if item.get("type") == "session":
                session = dict(item.get("data") or {})
            elif item.get("type") == "event":
                event = dict(item.get("data") or {})
                key = (str(event.get("category", "")), str(event.get("action", "")))
                observed.setdefault(key, []).append(event)

    checks = []
    for category, action in EXPECTED_EVENT_CLASSES:
        matches = observed.get((category, action), [])
        provenances = [
            dict(event.get("payload") or {}).get("trigger")
            for event in matches
            if dict(event.get("payload") or {}).get("trigger")
        ]
        checks.append(
            {
                "category": category,
                "action": action,
                "status": "observed" if matches else "missing",
                "count": len(matches),
                "confidences": sorted({str(event.get("confidence")) for event in matches}),
                "triggers": provenances,
            }
        )
    return {
        "source": str(source),
        "session_id": session.get("id"),
        "observer_version": session.get("observer_version"),
        "observer_build": session.get("observer_build"),
        "checks": checks,
        "observed": sum(check["status"] == "observed" for check in checks),
        "missing": sum(check["status"] == "missing" for check in checks),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check an FL Activity Recorder JSONL export")
    parser.add_argument("export", type=Path)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()
    report = build_report(args.export)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "Observer {} · {} observed · {} missing".format(
                report.get("observer_build") or "unknown",
                report["observed"],
                report["missing"],
            )
        )
        for check in report["checks"]:
            marker = "PASS" if check["status"] == "observed" else "MISS"
            confidence = ",".join(check["confidences"]) or "-"
            print(f"{marker} {check['category']}/{check['action']} [{confidence}]")
    raise SystemExit(1 if report["missing"] else 0)


if __name__ == "__main__":
    main()
