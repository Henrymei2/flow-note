from __future__ import annotations

from fastapi.testclient import TestClient

from fl_activity_recorder.api import create_app
from fl_activity_recorder.config import Settings
from fl_activity_recorder.observer_contract import OBSERVER_BUILD, OBSERVER_SCHEMA, OBSERVER_VERSION
from fl_activity_recorder.protocol import DecodedMessage, MessageKind


def test_session_api_and_dashboard(tmp_path: object) -> None:
    settings = Settings(
        database_path=tmp_path / "test.db",  # type: ignore[operator]
        enable_midi=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        recorder = app.state.recorder
        recorder.ingest(
            DecodedMessage(
                MessageKind.HELLO,
                1,
                {
                    "api_version": 42,
                    "fl_version": "26.1.3",
                    "project_title": "API Test",
                    "observer_version": OBSERVER_VERSION,
                    "observer_schema": OBSERVER_SCHEMA,
                    "observer_build": OBSERVER_BUILD,
                },
            )
        )
        recorder.ingest(DecodedMessage(MessageKind.HEARTBEAT, 2, {}))
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json()["connected"] is True
        start = client.post("/api/sessions")
        assert start.status_code == 201
        session = start.json()
        recorder.ingest(
            DecodedMessage(
                MessageKind.EVENT,
                3,
                {
                    "category": "mixer",
                    "action": "track_changed",
                    "summary": "Mixer track 1 changed",
                    "confidence": "state_diff",
                    "entity_type": "mixer_track",
                    "entity_key": "1",
                    "payload": {"volume": 0.75},
                },
            )
        )
        filtered = client.get(
            f"/api/sessions/{session['id']}/events",
            params={"category": "mixer", "search": "track 1"},
        )
        assert filtered.status_code == 200
        assert len(filtered.json()) == 1
        assert client.get(f"/api/sessions/{session['id']}/categories").json() == [
            "mixer",
            "project",
        ]
        export = client.get(f"/api/sessions/{session['id']}/export")
        assert export.status_code == 200
        assert "Mixer track 1 changed" in export.text
        assert OBSERVER_BUILD in export.text
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 409
        assert client.post(f"/api/sessions/{session['id']}/stop").status_code == 200
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 204


def test_disconnected_start_is_rejected(tmp_path: object) -> None:
    app = create_app(Settings(database_path=tmp_path / "test.db", enable_midi=False))  # type: ignore[operator]
    with TestClient(app) as client:
        response = client.post("/api/sessions")
        assert response.status_code == 409


def test_clock_capture_timeline_and_range_video_api(tmp_path: object) -> None:
    settings = Settings(
        database_path=tmp_path / "test.db",  # type: ignore[operator]
        data_directory=tmp_path / "sessions",  # type: ignore[operator]
        enable_midi=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        recorder = app.state.recorder
        recorder.ingest(
            DecodedMessage(
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
        clock = client.get("/api/clock").json()
        assert clock["mode"] in {"lsl", "monotonic_fallback"}
        session = client.post("/api/sessions").json()
        start = client.post(
            f"/api/sessions/{session['id']}/capture/start",
            json={
                "started_lsl": clock["lsl_seconds"],
                "mime_type": "video/webm",
                "clock_sync": {"rtt_ms": 1.0},
                "metadata": {"width": 1280, "height": 720, "has_audio": False},
            },
        )
        assert start.status_code == 200
        assert (
            client.post(
                f"/api/sessions/{session['id']}/capture/chunks/1", content=b"bad"
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/sessions/{session['id']}/capture/chunks/0", content=b"webm-data"
            ).status_code
            == 200
        )
        completed = client.post(
            f"/api/sessions/{session['id']}/capture/complete",
            json={
                "ended_lsl": clock["lsl_seconds"] + 1,
                "duration_seconds": 1,
            },
        )
        assert completed.json()["status"] == "ready"
        assert client.post(f"/api/sessions/{session['id']}/stop").status_code == 200
        timeline = client.get(
            f"/api/sessions/{session['id']}/timeline", params={"start": 0, "end": 2}
        )
        assert timeline.status_code == 200
        assert timeline.json()["events"][0]["timeline_seconds"] == 0
        video = client.get(f"/api/sessions/{session['id']}/video", headers={"Range": "bytes=0-3"})
        assert video.status_code == 206
        assert video.content == b"webm"
