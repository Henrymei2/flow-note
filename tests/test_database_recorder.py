from __future__ import annotations

import time

from fl_activity_recorder.database import Database
from fl_activity_recorder.protocol import DecodedMessage, MessageKind
from fl_activity_recorder.recorder import Recorder


def message(kind: MessageKind, sequence: int, payload: dict[str, object]) -> DecodedMessage:
    return DecodedMessage(kind=kind, sequence=sequence, payload=payload)


def connect(recorder: Recorder) -> None:
    recorder.ingest(
        message(
            MessageKind.HELLO,
            1,
            {"api_version": 42, "fl_version": "26.1.3", "project_title": "Night Drive"},
        )
    )
    recorder.ingest(message(MessageKind.HEARTBEAT, 2, {}))


def test_persists_only_during_active_session(tmp_path: object) -> None:
    database = Database(tmp_path / "activity.db")  # type: ignore[operator]
    recorder = Recorder(database)
    connect(recorder)
    recorder.ingest(
        message(
            MessageKind.EVENT,
            3,
            {
                "category": "transport",
                "action": "play",
                "summary": "Ignored",
                "confidence": "exact",
            },
        )
    )
    session = recorder.start_session()
    recorder.ingest(
        message(
            MessageKind.EVENT,
            4,
            {
                "category": "transport",
                "action": "play",
                "summary": "Playback started",
                "confidence": "exact",
            },
        )
    )
    recorder.stop_session(str(session["id"]))
    recorder.ingest(
        message(
            MessageKind.EVENT,
            5,
            {
                "category": "transport",
                "action": "stop",
                "summary": "Ignored",
                "confidence": "exact",
            },
        )
    )
    events = database.list_events(str(session["id"]))
    assert [event["summary"] for event in events] == [
        "Playback started",
        "Recording started with the current FL Studio state",
    ]
    database.close()


def test_sequence_gap_and_heartbeat_interruption(tmp_path: object) -> None:
    database = Database(tmp_path / "activity.db")  # type: ignore[operator]
    recorder = Recorder(database, heartbeat_timeout=5.0)
    connect(recorder)
    session = recorder.start_session()
    recorder.ingest(message(MessageKind.HEARTBEAT, 5, {}))
    recorder.last_heartbeat = time.monotonic() - 6
    recorder.check_heartbeat()
    assert database.get_session(str(session["id"]))["status"] == "interrupted"
    rows = database._connection.execute("SELECT code FROM diagnostics ORDER BY id").fetchall()
    assert [row["code"] for row in rows] == ["sequence_gap", "heartbeat_timeout"]
    database.close()


def test_old_api_cannot_start(tmp_path: object) -> None:
    database = Database(tmp_path / "activity.db")  # type: ignore[operator]
    recorder = Recorder(database)
    recorder.ingest(message(MessageKind.HELLO, 1, {"api_version": 41}))
    try:
        recorder.start_session()
    except RuntimeError as error:
        assert "API 42" in str(error)
    else:
        raise AssertionError("old API unexpectedly started")
    database.close()
