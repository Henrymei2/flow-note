from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from fl_activity_recorder.api import create_app
from fl_activity_recorder.config import Settings
from fl_activity_recorder.live_signals import SignalDescriptor
from fl_activity_recorder.observer_contract import OBSERVER_BUILD, OBSERVER_SCHEMA, OBSERVER_VERSION
from fl_activity_recorder.protocol import DecodedMessage, MessageKind


class EmptyLsl:
    """Keep API tests independent from machine-level LSL streams."""

    def resolve_streams(self, *, wait_time: float) -> list[object]:
        """Return no discovered streams."""
        del wait_time
        return []

    def local_clock(self) -> float:
        """Use the process monotonic clock as a deterministic same-host LSL clock."""
        return time.monotonic()


class FakeR2Client:
    """Capture uploads without making API tests depend on Cloudflare."""

    def __init__(self) -> None:
        self.upload: dict[str, object] | None = None
        self.objects: dict[str, bytes] = {}

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, object],
        Config: object,
    ) -> None:
        del Config
        with open(filename, "rb") as handle:
            content = handle.read()
        self.upload = {
            "bucket": bucket,
            "key": key,
            "extra_args": ExtraArgs,
            "content": content,
        }
        self.objects[key] = content

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
        CacheControl: str,
    ) -> None:
        del Bucket, ContentType, CacheControl
        self.objects[Key] = Body

    def list_objects_v2(self, **arguments: object) -> dict[str, object]:
        prefix = str(arguments.get("Prefix") or "")
        return {
            "Contents": [{"Key": key} for key in self.objects if key.startswith(prefix)],
            "IsTruncated": False,
        }

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        del Bucket
        return {"Body": io.BytesIO(self.objects[Key])}

    def generate_presigned_url(
        self, operation: str, *, Params: dict[str, str], ExpiresIn: int
    ) -> str:
        assert operation == "get_object"
        return f"https://download.test/{Params['Key']}?expires={ExpiresIn}"


def make_emotibit_ready(app: object) -> None:
    """Inject one recent numeric EmotiBit sample for the Start preflight."""
    receiver = app.state.emotibit  # type: ignore[attr-defined]
    fake_lsl = EmptyLsl()
    receiver.lsl = fake_lsl
    app.state.recorder.clock._lsl = fake_lsl  # type: ignore[attr-defined]
    track = SignalDescriptor(
        track_id="EA",
        type_tag="EA",
        display_name="EDA",
        group_name="EDA",
        unit="microsiemens",
        nominal_rate=15.0,
        stream_name="EmotiBit-EA",
        source_id="test",
        channel_index=0,
        channel_format="1",
    )
    receiver.accept_samples([track], [[0.1]], [app.state.recorder.clock.now()])  # type: ignore[attr-defined]


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
        make_emotibit_ready(app)
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
        assert response.json()["detail"]["code"] == "emotibit_lsl_unavailable"


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
        make_emotibit_ready(app)
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


def test_complete_research_archive_round_trip(tmp_path: object) -> None:
    settings = Settings(
        database_path=tmp_path / "test.db",  # type: ignore[operator]
        data_directory=tmp_path / "sessions",  # type: ignore[operator]
        enable_midi=False,
        r2_account_id="0123456789abcdef0123456789abcdef",
        r2_bucket_name="research-sessions",
        r2_access_key_id="test-access",
        r2_secret_access_key="test-secret",
        r2_endpoint=("https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"),
    )
    app = create_app(settings)
    fake_r2 = FakeR2Client()
    app.state.r2._client = fake_r2
    with TestClient(app) as client:
        recorder = app.state.recorder
        recorder.ingest(
            DecodedMessage(
                MessageKind.HELLO,
                1,
                {
                    "api_version": 45,
                    "project_title": "Share Test",
                    "observer_version": OBSERVER_VERSION,
                    "observer_schema": OBSERVER_SCHEMA,
                    "observer_build": OBSERVER_BUILD,
                },
            )
        )
        make_emotibit_ready(app)
        session = client.post("/api/sessions").json()
        zero = float(session["timeline_zero_lsl"])
        descriptor = SignalDescriptor(
            track_id="EA",
            type_tag="EA",
            display_name="EDA",
            group_name="EDA",
            unit="microsiemens",
            nominal_rate=15.0,
            stream_name="EmotiBit-EA",
            source_id="test",
            channel_index=0,
            channel_format="1",
        )
        app.state.signals.append(descriptor, [zero + 0.25, zero + 0.5], [1.25, 1.5])
        capture = client.post(
            f"/api/sessions/{session['id']}/capture/start",
            json={
                "started_lsl": app.state.recorder.clock.now(),
                "mime_type": "video/webm",
                "metadata": {"has_audio": True},
            },
        )
        assert capture.status_code == 200
        assert (
            client.post(
                f"/api/sessions/{session['id']}/capture/chunks/0", content=b"video-data"
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/sessions/{session['id']}/capture/complete",
                json={"ended_lsl": zero + 1, "duration_seconds": 1},
            ).status_code
            == 200
        )
        recorder.ingest(
            DecodedMessage(
                MessageKind.EVENT,
                2,
                {
                    "category": "transport",
                    "action": "play",
                    "summary": "Playback started",
                    "confidence": "exact",
                },
            )
        )
        assert client.post(f"/api/sessions/{session['id']}/stop").status_code == 200

        shared = client.get(f"/api/sessions/{session['id']}/share")
        assert shared.status_code == 200
        assert shared.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(shared.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format_version"] == 1
            assert manifest["video"]["file"] == "media/video.webm"
            assert len(manifest["physiology"]) == 1
            assert "Playback started" in archive.read("events/events.jsonl").decode()
            for name, expected in manifest["checksums"].items():
                data = archive.read(name)
                assert len(data) == expected["bytes"]
                assert hashlib.sha256(data).hexdigest() == expected["sha256"]

        cloud_shared = client.post(f"/api/sessions/{session['id']}/cloud-share")
        assert cloud_shared.status_code == 201
        assert cloud_shared.json()["download_url"].startswith("https://download.test/")
        assert fake_r2.upload is not None
        assert fake_r2.upload["bucket"] == "research-sessions"
        assert bytes(fake_r2.upload["content"]).startswith(b"PK")  # type: ignore[arg-type]
        cloud_sessions = client.get("/api/cloud-sessions")
        assert cloud_sessions.status_code == 200
        assert cloud_sessions.json()[0]["project_title"] == "Share Test"
        assert cloud_sessions.json()[0]["event_count"] == 2
        assert cloud_sessions.json()[0]["archive_key"] == cloud_shared.json()["object_key"]
        download = client.get(
            "/api/cloud-sessions/download",
            params={"key": cloud_shared.json()["object_key"]},
            follow_redirects=False,
        )
        assert download.status_code == 307
        assert download.headers["location"].startswith("https://download.test/")

        imported = client.post(
            "/api/research-archives",
            content=shared.content,
            headers={"Content-Type": "application/zip"},
        )
        assert imported.status_code == 201
        imported_session = imported.json()
        assert imported_session["id"] != session["id"]
        assert imported_session["source_session_id"] == session["id"]
        detail = client.get(f"/api/sessions/{imported_session['id']}").json()
        assert detail["video_status"] == "ready"
        assert detail["emotibit_status"] == "ready"
        assert detail["event_count"] == 2
        timeline = client.get(
            f"/api/sessions/{imported_session['id']}/timeline",
            params={"start": 0, "end": 1, "tracks": "EA"},
        ).json()
        assert timeline["signals"][0]["points"] == [[0.25, 1.25], [0.5, 1.5]]
        assert client.get(f"/api/sessions/{imported_session['id']}/video").content == b"video-data"

        session_count = len(client.get("/api/sessions").json())
        invalid = client.post(
            "/api/research-archives",
            content=b"not a zip",
            headers={"Content-Type": "application/zip"},
        )
        assert invalid.status_code == 422
        assert len(client.get("/api/sessions").json()) == session_count


def test_interrupted_session_without_video_or_signals_can_be_shared(tmp_path: object) -> None:
    """Preserve and upload usable event data when optional recording sources are absent."""
    settings = Settings(
        database_path=tmp_path / "test.db",  # type: ignore[operator]
        data_directory=tmp_path / "sessions",  # type: ignore[operator]
        enable_midi=False,
        r2_account_id="0123456789abcdef0123456789abcdef",
        r2_bucket_name="research-sessions",
        r2_access_key_id="test-access",
        r2_secret_access_key="test-secret",
        r2_endpoint=("https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"),
    )
    app = create_app(settings)
    fake_r2 = FakeR2Client()
    app.state.r2._client = fake_r2
    session = app.state.database.create_session(
        project_title="Interrupted Session",
        fl_version="26.1.2",
        api_version=43,
        recorder_version="test",
    )
    session_id = str(session["id"])
    app.state.database.finish_session(session_id, "interrupted")

    with TestClient(app) as client:
        shared = client.get(f"/api/sessions/{session_id}/share")
        assert shared.status_code == 200
        with zipfile.ZipFile(io.BytesIO(shared.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["session"]["status"] == "interrupted"
            assert manifest["video"] is None
            assert manifest["physiology"] == []

        cloud_shared = client.post(f"/api/sessions/{session_id}/cloud-share")
        assert cloud_shared.status_code == 201
        assert fake_r2.upload is not None

        imported = client.post(
            "/api/research-archives",
            content=shared.content,
            headers={"Content-Type": "application/zip"},
        )
        assert imported.status_code == 201
        detail = client.get(f"/api/sessions/{imported.json()['id']}").json()
        assert detail["status"] == "interrupted"
        assert detail["video_status"] == "missing"
        assert detail["emotibit_status"] == "missing"


def test_biosignal_clusters_group_distinct_windows(tmp_path: object) -> None:
    """Windows with visibly different EDA/heart-rate levels land in different clusters."""
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
        make_emotibit_ready(app)
        session = client.post("/api/sessions").json()
        zero = float(session["timeline_zero_lsl"])

        duration = 300.0
        phase_boundary = 150.0
        sample_period = 2.0
        offsets = [t * sample_period + 0.5 for t in range(int(duration / sample_period))]
        constant_tags = {"EA": (0.1, 0.9), "HR": (60.0, 100.0), "SA": (1.0, 5.0), "SR": (1.0, 5.0),
                          "SF": (1.0, 5.0), "T1": (1.0, 5.0), "TH": (1.0, 5.0)}
        motion_tags = ["AX", "AY", "AZ", "GX", "GY", "GZ", "MX", "MY", "MZ"]

        for index, (tag, (low, high)) in enumerate(constant_tags.items()):
            descriptor = SignalDescriptor(
                track_id=tag, type_tag=tag, display_name=tag, group_name="test",
                unit=None, nominal_rate=1 / sample_period, stream_name=f"EmotiBit-{tag}",
                source_id="test", channel_index=index, channel_format="1",
            )
            values = [low if t < phase_boundary else high for t in offsets]
            app.state.signals.append(descriptor, [zero + t for t in offsets], values)

        # Motion channels differ in within-window *variability*, not level: a near-zero
        # amplitude square wave before the boundary ("still"), a large one after ("moving").
        for index, tag in enumerate(motion_tags):
            descriptor = SignalDescriptor(
                track_id=tag, type_tag=tag, display_name=tag, group_name="test",
                unit=None, nominal_rate=1 / sample_period, stream_name=f"EmotiBit-{tag}",
                source_id="test", channel_index=index, channel_format="1",
            )
            values = [
                (0.02 if t < phase_boundary else 3.0) * (1 if i % 2 == 0 else -1)
                for i, t in enumerate(offsets)
            ]
            app.state.signals.append(descriptor, [zero + t for t in offsets], values)

        recorder.stop_session(session["id"], timeline_end_lsl=zero + duration)

        response = client.get(f"/api/sessions/{session['id']}/biosignal-clusters")
        assert response.status_code == 200
        body = response.json()
        # Auto-sizing targets ~28 windows over the session, clamped to [6, 30]s.
        assert body["window_seconds"] == duration / 28

        regions = body["windows"]
        assert regions == sorted(regions, key=lambda w: w["start_seconds"])
        # Consecutive same-cluster windows are merged, so the raw windows collapse
        # into a handful of regions rather than being returned one by one.
        assert 2 <= len(regions) < 15
        assert regions[0]["cluster"] != regions[-1]["cluster"]
        assert regions[0]["start_seconds"] == 0.0
        assert regions[-1]["end_seconds"] == pytest.approx(duration)

        assert body["clusters"]
        for cluster in body["clusters"]:
            assert isinstance(cluster["label"], str) and cluster["label"]
            assert set(cluster["centroid"]) == {
                "EA_mean", "EA_std", "HR_mean", "HR_std", "SA_mean", "SR_mean",
                "SF_mean", "T1_mean", "TH_mean", "movement_energy", "rotation_energy",
                "magnetic_energy",
            }

        assert client.get("/api/sessions/does-not-exist/biosignal-clusters").status_code == 404
