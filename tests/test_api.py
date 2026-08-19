from __future__ import annotations

from fastapi.testclient import TestClient

from fl_activity_recorder.api import create_app
from fl_activity_recorder.config import Settings
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
                {"api_version": 42, "fl_version": "26.1.3", "project_title": "API Test"},
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
        export = client.get(f"/api/sessions/{session['id']}/export")
        assert export.status_code == 200
        assert "Mixer track 1 changed" in export.text
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 409
        assert client.post(f"/api/sessions/{session['id']}/stop").status_code == 200
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 204


def test_disconnected_start_is_rejected(tmp_path: object) -> None:
    app = create_app(Settings(database_path=tmp_path / "test.db", enable_midi=False))  # type: ignore[operator]
    with TestClient(app) as client:
        response = client.post("/api/sessions")
        assert response.status_code == 409
