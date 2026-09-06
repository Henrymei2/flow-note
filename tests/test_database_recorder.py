from __future__ import annotations

import sqlite3
import time

from fl_activity_recorder.database import Database
from fl_activity_recorder.observer_contract import OBSERVER_BUILD, OBSERVER_SCHEMA, OBSERVER_VERSION
from fl_activity_recorder.protocol import DecodedMessage, MessageKind
from fl_activity_recorder.recorder import Recorder


def message(kind: MessageKind, sequence: int, payload: dict[str, object]) -> DecodedMessage:
    return DecodedMessage(kind=kind, sequence=sequence, payload=payload)


def connect(recorder: Recorder) -> None:
    recorder.ingest(
        message(
            MessageKind.HELLO,
            1,
            {
                "api_version": 42,
                "fl_version": "26.1.3",
                "project_title": "Night Drive",
                "observer_version": OBSERVER_VERSION,
                "observer_schema": OBSERVER_SCHEMA,
                "observer_build": OBSERVER_BUILD,
            },
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
    assert session["timeline_zero_utc"] is not None
    assert session["monotonic_lsl_offset"] is not None
    assert session["clock_model_version"] == "lsl-local-clock-offset-v1"
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


def test_old_observer_cannot_start_and_health_explains_why(tmp_path: object) -> None:
    database = Database(tmp_path / "activity.db")  # type: ignore[operator]
    recorder = Recorder(database)
    recorder.ingest(
        message(
            MessageKind.HELLO,
            1,
            {"api_version": 42, "observer_schema": 1, "observer_build": "old"},
        )
    )
    assert recorder.health()["compatibility_reason"] == "observer_schema_mismatch"
    try:
        recorder.start_session()
    except RuntimeError as error:
        assert "out of date" in str(error)
    else:
        raise AssertionError("old observer unexpectedly started")
    database.close()


def test_existing_database_adds_observer_identity_columns(tmp_path: object) -> None:
    path = tmp_path / "legacy.db"  # type: ignore[operator]
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE sessions (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
            status TEXT NOT NULL, project_title TEXT, fl_version TEXT,
            api_version INTEGER, recorder_version TEXT NOT NULL
        )"""
    )
    connection.commit()
    connection.close()
    database = Database(path)
    columns = {
        row[1] for row in database._connection.execute("PRAGMA table_info(sessions)").fetchall()
    }
    assert {"observer_version", "observer_build"} <= columns
    database.close()


def test_reloaded_observer_starts_a_new_sequence_epoch(tmp_path: object) -> None:
    database = Database(tmp_path / "activity.db")  # type: ignore[operator]
    recorder = Recorder(database)
    recorder.ingest(
        message(
            MessageKind.HELLO,
            90,
            {"api_version": 42, "observer_schema": 1, "observer_build": "old"},
        )
    )
    recorder.ingest(message(MessageKind.HEARTBEAT, 91, {}))
    recorder.ingest(
        message(
            MessageKind.HELLO,
            1,
            {
                "api_version": 45,
                "observer_version": OBSERVER_VERSION,
                "observer_schema": OBSERVER_SCHEMA,
                "observer_build": OBSERVER_BUILD,
            },
        )
    )
    assert recorder.last_sequence == 1
    assert recorder.health()["compatible"] is True
    assert recorder.health()["observer"]["observer_schema"] == OBSERVER_SCHEMA
    database.close()


def test_reload_interrupts_active_session_instead_of_mixing_observer_epochs(
    tmp_path: object,
) -> None:
    database = Database(tmp_path / "activity.db")  # type: ignore[operator]
    recorder = Recorder(database)
    connect(recorder)
    session = recorder.start_session()
    recorder.ingest(
        message(
            MessageKind.HELLO,
            1,
            {
                "api_version": 45,
                "observer_version": OBSERVER_VERSION,
                "observer_schema": OBSERVER_SCHEMA,
                "observer_build": OBSERVER_BUILD,
            },
        )
    )
    assert database.get_session(str(session["id"]))["status"] == "interrupted"
    diagnostic = database._connection.execute(
        "SELECT code FROM diagnostics ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert diagnostic["code"] == "observer_restarted"
    database.close()


def test_source_observation_time_drives_relative_timeline(tmp_path: object) -> None:
    database = Database(tmp_path / "activity.db")  # type: ignore[operator]
    recorder = Recorder(database)
    connect(recorder)
    session = recorder.start_session()
    observed = time.monotonic() - 0.25
    recorder.ingest(
        message(
            MessageKind.EVENT,
            3,
            {
                "category": "mixer",
                "action": "track_changed",
                "summary": "Delayed stable read",
                "confidence": "state_diff",
                "observed_monotonic_seconds": observed,
            },
        )
    )
    event = database.list_events(str(session["id"]))[0]
    assert event["time_confidence"] == "source"
    assert event["transport_delay_ms"] >= 200
    assert event["observed_monotonic_seconds"] == observed
    recorder.stop_session(str(session["id"]))
    database.close()
