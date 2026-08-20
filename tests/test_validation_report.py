from __future__ import annotations

import json

from fl_activity_recorder.observer_contract import OBSERVER_BUILD
from fl_activity_recorder.validation_report import EXPECTED_EVENT_CLASSES, build_report


def test_validation_report_lists_observed_confidence_and_trigger(tmp_path: object) -> None:
    export = tmp_path / "session.jsonl"  # type: ignore[operator]
    lines = [
        json.dumps(
            {
                "type": "session",
                "data": {"id": "session-1", "observer_build": OBSERVER_BUILD},
            }
        )
    ]
    for category, action in EXPECTED_EVENT_CLASSES:
        lines.append(
            json.dumps(
                {
                    "type": "event",
                    "data": {
                        "category": category,
                        "action": action,
                        "confidence": "state_diff",
                        "payload": {
                            "trigger": {
                                "callback": "OnRefresh",
                                "refresh_flags": 1,
                                "fallback_poll": False,
                            }
                        },
                    },
                }
            )
        )
    export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = build_report(export)
    assert report["observer_build"] == OBSERVER_BUILD
    assert report["observed"] == len(EXPECTED_EVENT_CLASSES)
    assert report["missing"] == 0
    assert report["checks"][0]["triggers"][0]["callback"] == "OnRefresh"


def test_validation_report_marks_old_export_classes_missing(tmp_path: object) -> None:
    export = tmp_path / "old.jsonl"  # type: ignore[operator]
    export.write_text(
        json.dumps(
            {
                "type": "event",
                "data": {
                    "category": "undo",
                    "action": "history_changed",
                    "confidence": "signal_only",
                    "payload": {},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = build_report(export)
    assert report["observed"] == 0
    assert report["missing"] == len(EXPECTED_EVENT_CLASSES)
