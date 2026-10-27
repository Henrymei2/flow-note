"""Exercise authentic overlay detection, storage, decoding, and task lifecycles."""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fl_activity_recorder.api import create_app
from fl_activity_recorder.audition_detection import detect_auditions
from fl_activity_recorder.config import Settings
from fl_activity_recorder.database import Database
from fl_activity_recorder.live_signals import LiveSignalStore, SignalDescriptor
from fl_activity_recorder.models import ActivityEvent
from fl_activity_recorder.overlay_generator import controls, generate_ambient
from fl_activity_recorder.overlays import OverlayManager, ffmpeg_executable, verify_audio


def edge(identifier: int, timestamp: float, playing: bool) -> dict[str, Any]:
    """Build one explicit playback transition with stable source identity."""
    return {
        "id": identifier,
        "sequence": identifier,
        "timeline_seconds": timestamp,
        "category": "transport",
        "action": "playing_changed",
        "payload": {"before": not playing, "after": playing},
    }


@pytest.mark.parametrize("length,eligible", [(5, None), (8, True), (300, True), (301, False)])
def test_detection_boundaries_and_order(length: float, eligible: bool | None) -> None:
    """Sort out-of-order evidence and enforce inclusive duration boundaries."""
    result = detect_auditions([edge(2, length, False), edge(1, 0, True)], [], length)
    assert len(result["auditions"]) == (0 if eligible is None else 1)
    if eligible is not None:
        assert result["auditions"][0]["eligible"] is eligible


def test_detection_duplicate_seek_gaps_and_incomplete() -> None:
    """Keep independent auditions and reject lost or truncated playback evidence."""
    events = [
        edge(1, 1, True),
        edge(2, 2, True),
        edge(4, 11, False),
        edge(5, 11.1, True),
        edge(6, 25, False),
        edge(7, 26, True),
    ]
    events.append({**edge(3, 5, True), "action": "seek"})
    result = detect_auditions(events, [], 40)["auditions"]
    assert [(a["start_time"], a["end_time"]) for a in result] == [(1, 11), (11.1, 25), (26, 40)]
    assert [a["eligible"] for a in result] == [True, True, False]
    gap = [{"code": "sequence_gap", "payload": {"after": 2, "before": 4}}]
    assert not detect_auditions(events, gap, 40)["auditions"][0]["eligible"]
    snapshot = {**edge(0, 0, True), "action": "initial_snapshot", "payload": {"playing": True}}
    assert not detect_auditions([snapshot, edge(1, 10, False)], [], 10)["auditions"][0]["eligible"]


class RawSignals:
    """Supply raw samples through the real storage iterator contract."""

    def __init__(self, series: dict[str, list[list[float]]]) -> None:
        """Save deterministic per-channel source data."""
        self.series = series

    def iter_raw(self, session_id: str, tag: str, start: float, end: float) -> Iterator[Any]:
        """Yield only original samples in the requested session window."""
        del session_id
        data = np.asarray(self.series.get(tag, []), dtype=float).reshape(-1, 2)
        yield data[(data[:, 0] >= start) & (data[:, 0] <= end)]


@pytest.mark.parametrize("tag,value", [("HR", 70), ("EA", 2)])
def test_single_channel_controls_and_long_gaps(tag: str, value: float) -> None:
    """Either valid modality works; missing controls stay neutral and gaps are not bridged."""
    store: Any = RawSignals({tag: [[i, value] for i in range(11)] + [[2, float("nan")]]})
    curves, valid = controls(store, "s", 0, 10, 10)
    assert valid[tag] and not valid["EA" if tag == "HR" else "HR"]
    assert np.isfinite(curves).all()
    assert np.allclose(curves, 0.5)
    store.series[tag] = [[0, value], [1, value], [10, value]]
    with pytest.raises(ValueError, match="No reliable"):
        controls(store, "s", 0, 10, 10)


def test_generator_pcm_duration_seed_and_cancellation(tmp_path: Path) -> None:
    """Check actual WAV samples, deterministic seeds, fades, and cooperative cancellation."""
    curves = np.full((81, 2), 0.5)
    paths = [tmp_path / f"{seed}-{index}.wav" for index, seed in enumerate((42, 42, 43))]
    for path, seed in zip(paths, (42, 42, 43), strict=True):
        generate_ambient(path, 8.031, curves, seed, threading.Event())
    assert paths[0].read_bytes() == paths[1].read_bytes()
    assert paths[0].read_bytes() != paths[2].read_bytes()
    with wave.open(str(paths[0])) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (2, 2, 48000)
        assert audio.getnframes() == round(8.031 * 48000)
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").reshape(-1, 2)
    assert np.max(np.abs(samples)) <= 0.16 * 32767
    assert np.max(np.abs(samples[0])) == 0
    assert np.max(np.abs(np.diff(samples.astype(float), axis=0))) < 3000
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        generate_ambient(tmp_path / "cancel.wav", 8, curves, 42, cancelled)


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Create real tiny WebM fixtures using the shipped FFmpeg decoder/encoder."""
    directory = tmp_path_factory.mktemp("overlay-media")
    executable = ffmpeg_executable()
    paths = {name: directory / f"{name}.webm" for name in ("sound", "silent", "broken")}
    for name in ("sound", "silent"):
        command = [
            executable,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:r=2:d=14",
        ]
        if name == "sound":
            command += ["-f", "lavfi", "-i", "sine=frequency=220:duration=14", "-c:a", "libopus"]
        subprocess.run(
            [*command, "-c:v", "libvpx", "-t", "14", str(paths[name])],
            check=True,
            capture_output=True,
            timeout=30,
        )
    paths["broken"].write_bytes(b"not a webm")
    return paths


def test_actual_audio_decode(media: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Require a decodable full interval rather than a claimed audio metadata flag."""
    verify_audio(media["sound"], 1, 10, threading.Event())
    for name in ("silent", "broken"):
        with pytest.raises(ValueError, match="no recorded audio"):
            verify_audio(media[name], 1, 10, threading.Event())
    with pytest.raises(ValueError, match="incomplete audio"):
        verify_audio(media["sound"], 10, 10, threading.Event())
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        verify_audio(media["sound"], 0, 10, cancelled)
    original = time.monotonic
    calls = iter([0.0, 100.0])
    monkeypatch.setattr("fl_activity_recorder.overlays.time.monotonic", lambda: next(calls, 100))
    with pytest.raises(ValueError, match="timed out"):
        verify_audio(media["sound"], 0, 10, threading.Event())
    monkeypatch.setattr("fl_activity_recorder.overlays.time.monotonic", original)


def populate(database: Database, signals: LiveSignalStore, video: Path, root: Path) -> str:
    """Persist a finished session through authentic event and signal storage interfaces."""
    session = database.create_session(
        project_title="Overlay",
        fl_version="26",
        api_version=42,
        recorder_version="test",
        timeline_zero_lsl=1000,
    )
    session_id = str(session["id"])
    for item in (edge(1, 1, True), edge(2, 11, False)):
        database.add_event(
            session_id,
            item["sequence"],
            "2026-01-01",
            0,
            ActivityEvent(
                category="transport",
                action="playing_changed",
                summary="Playback",
                confidence="state_diff",
                payload=item["payload"],
            ),
            timeline_seconds=item["timeline_seconds"],
        )
    database.add_event(
        session_id,
        0,
        "2026-01-01",
        0,
        ActivityEvent(
            category="transport",
            action="initial_snapshot",
            summary="Initial",
            confidence="state_diff",
            payload={"playing": False, "tempo": 120.0},
        ),
        timeline_seconds=0,
    )
    signals.start_session(session_id, 1000)
    descriptor = SignalDescriptor(
        "HR", "HR", "Heart rate", "Heart", "bpm", 1, "EmotiBit-HR", "device", 0, "1"
    )
    signals.append(descriptor, list(range(1000, 1015)), [70] * 15)
    signals.finish_session(1014, interrupted=False, degraded=False)
    path = root / session_id / "video" / "capture.webm"
    path.parent.mkdir(parents=True)
    path.write_bytes(video.read_bytes())
    database.upsert_asset(
        session_id,
        "video",
        "ready",
        relative_path=str(path.relative_to(root)),
        duration_seconds=14,
        start_offset_seconds=0,
        metadata_json={"has_audio": False},
    )
    database.finish_session(session_id, "completed", timeline_end_lsl=1014)
    return session_id


def wait_ready(manager: OverlayManager, session_id: str, overlay_id: str) -> dict[str, Any]:
    """Wait with a bounded deadline for a real background generation task."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        record = manager.get(session_id, overlay_id)
        if record["status"] in {"ready", "failed"}:
            return record
        time.sleep(0.02)
    pytest.fail("Overlay worker did not complete")


def test_api_cache_regeneration_and_ownership(
    tmp_path: Path,
    media: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate and range-serve real audio; preserve old results when replacement fails."""
    root = tmp_path / "sessions"
    app = create_app(
        Settings(database_path=tmp_path / "db.sqlite", data_directory=root, enable_midi=False)
    )
    db = app.state.database
    manager = app.state.overlays
    try:
        session_id = populate(db, app.state.signals, media["sound"], root)
        source = root / session_id / "video" / "capture.webm"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        client = TestClient(app)
        auditions = client.get(f"/api/sessions/{session_id}/auditions").json()["auditions"]
        assert auditions[0]["eligible"]  # Deliberately false has_audio does not suppress decoding.
        endpoint = f"/api/sessions/{session_id}/overlays"
        payload = {"audition_id": auditions[0]["id"]}
        response = client.post(endpoint, json=payload)
        assert response.status_code == 202
        record = wait_ready(manager, session_id, response.json()["overlay_id"])
        assert record["status"] == "ready", record
        assert client.post(endpoint, json=payload).json()["overlay_id"] == record["overlay_id"]
        audio_url = f"{endpoint}/{record['overlay_id']}/audio"
        assert client.get(audio_url, headers={"Range": "bytes=0-43"}).status_code == 206
        assert client.get(f"/api/sessions/wrong/overlays/{record['overlay_id']}").status_code == 404
        assert client.post(endpoint, json={**payload, "start_time": 0}).status_code == 422
        assert client.post(endpoint, json={**payload, "regenerate": "false"}).status_code == 422

        def fail(*args: Any) -> None:
            """Simulate a synthesis failure after input validation."""
            raise ValueError("synthetic generation failure")

        with monkeypatch.context() as scoped:
            scoped.setattr("fl_activity_recorder.overlays.generate_ambient", fail)
            replacement = client.post(endpoint, json={**payload, "regenerate": True}).json()
            assert wait_ready(manager, session_id, replacement["overlay_id"])["status"] == "failed"
            assert client.get(audio_url).status_code == 200
        retry = client.post(endpoint, json={**payload, "regenerate": True}).json()
        assert retry["seed"] != record["seed"]
        assert wait_ready(manager, session_id, retry["overlay_id"])["status"] == "ready"
        assert len(db.list_overlays(session_id)) == 1
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert client.delete(f"/api/sessions/{session_id}").status_code == 204
        assert not (root / session_id).exists()
        assert not db.list_overlays(session_id)
    finally:
        manager.close()
        db.close()


def test_delete_cancels_deduplicated_generation(
    tmp_path: Path,
    media: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel an active worker before its session files are deleted or published."""
    app = create_app(
        Settings(database_path=tmp_path / "db", data_directory=tmp_path / "data", enable_midi=False)
    )
    manager = app.state.overlays
    entered = threading.Event()

    def blocked(
        path: Path, duration: float, curves: Any, seed: int, event: threading.Event
    ) -> None:
        """Wait for real manager cancellation instead of completing a generation."""
        entered.set()
        assert event.wait(10)
        raise RuntimeError("cancelled")

    try:
        session_id = populate(
            app.state.database, app.state.signals, media["sound"], manager.captures.root
        )
        audition_id = manager.auditions(session_id)["auditions"][0]["id"]
        monkeypatch.setattr("fl_activity_recorder.overlays.generate_ambient", blocked)
        first = manager.request(session_id, audition_id, False)
        assert entered.wait(5)
        assert manager.request(session_id, audition_id, True)["overlay_id"] == first["overlay_id"]
        manager.delete_session(session_id)
        assert not manager.database.list_overlays(session_id)
        assert not (manager.captures.root / session_id).exists()
    finally:
        manager.close()
        app.state.database.close()


def test_complete_event_read_and_restart_recovery(tmp_path: Path, media: dict[str, Path]) -> None:
    """Read beyond UI limits and recover interrupted tasks without losing ready audio."""
    app = create_app(
        Settings(database_path=tmp_path / "db", data_directory=tmp_path / "data", enable_midi=False)
    )
    manager = app.state.overlays
    db = app.state.database
    try:
        session_id = populate(db, app.state.signals, media["sound"], manager.captures.root)
        with db.transaction() as connection:
            connection.executemany(
                """INSERT INTO events (session_id, sequence, occurred_at, monotonic_seconds,
                   category, action, summary, confidence, payload_json, timeline_seconds)
                   VALUES (?, ?, 'test', 0, 'transport', 'tempo_changed', 'Tempo',
                   'state_diff', '{"before":120,"after":120}', 0.5)""",
                [(session_id, index) for index in range(3, 10010)],
            )
        events, _ = db.audition_sources(session_id)
        assert len(events) == 10010
        audition_id = manager.auditions(session_id)["auditions"][0]["id"]
        generated = manager.request(session_id, audition_id, False)
        record = wait_ready(manager, session_id, generated["overlay_id"])
        assert record["status"] == "ready"
        record.update(status="generating")
        db.save_overlay(record)
        directory = manager.captures.root / session_id / "overlays"
        temporary = directory / "interrupted.tmp"
        temporary.write_bytes(b"unfinished")
        manager.close()
        manager = OverlayManager(db, manager.captures, app.state.signals)
        assert manager.get(session_id, record["overlay_id"])["status"] == "failed"
        assert not temporary.exists()
        retry = manager.request(session_id, audition_id, False)
        assert wait_ready(manager, session_id, retry["overlay_id"])["status"] == "ready"
    finally:
        manager.close()
        db.close()


def test_audio_coverage_and_no_physiology(tmp_path: Path, media: dict[str, Path]) -> None:
    """Reject out-of-video intervals and missing physiology without trusting capture flags."""
    app = create_app(
        Settings(database_path=tmp_path / "db", data_directory=tmp_path / "data", enable_midi=False)
    )
    manager = app.state.overlays
    db = app.state.database
    try:
        session_id = populate(db, app.state.signals, media["sound"], manager.captures.root)
        db.upsert_asset(session_id, "video", "ready", start_offset_seconds=2)
        item = manager.auditions(session_id)["auditions"][0]
        assert not item["eligible"] and "not fully covered" in item["reason"]
        db.upsert_asset(session_id, "video", "ready", start_offset_seconds=0.5)
        assert manager.auditions(session_id)["auditions"][0]["eligible"]
        with db.transaction() as connection:
            connection.execute("DELETE FROM signal_chunks WHERE session_id=?", (session_id,))
        item = manager.auditions(session_id)["auditions"][0]
        assert not item["eligible"] and "No reliable" in item["reason"]
    finally:
        manager.close()
        db.close()


def tempo_event(identifier: int, timestamp: float, value: Any, before: Any = 120) -> dict[str, Any]:
    """Build explicit tempo evidence without a DAW connection."""
    return {
        "id": identifier,
        "timeline_seconds": timestamp,
        "action": "tempo_changed",
        "payload": {"before": before, "after": value},
    }


@pytest.mark.parametrize(
    "events,expected",
    [
        ([tempo_event(1, 0, 120)], 120),
        ([tempo_event(1, 0, 120), tempo_event(2, 5, 120)], 120),
        ([tempo_event(1, 0, 120), tempo_event(2, 1, 130)], 130),
        ([tempo_event(1, 0, 120), tempo_event(2, 11, 130)], 120),
        (
            [
                {
                    "id": 1,
                    "timeline_seconds": 0,
                    "action": "initial_snapshot",
                    "payload": {"tempo": 98},
                }
            ],
            98,
        ),
    ],
)
def test_constant_audition_tempo(events: list[dict[str, Any]], expected: float) -> None:
    """Recover start evidence, accepting repeated values and excluding the stop boundary."""
    from fl_activity_recorder.audition_detection import audition_tempo

    assert audition_tempo(events, 1, 11) == expected


@pytest.mark.parametrize(
    "events",
    [
        [tempo_event(1, 3, 120)],
        [tempo_event(1, 0, 120), tempo_event(2, 5, 130)],
        [tempo_event(1, 0, 120), tempo_event(2, 5, 130), tempo_event(3, 6, 120, 130)],
        [tempo_event(1, 0, 120), tempo_event(2, 5, 120, 90)],
        [
            tempo_event(1, 0, 120),
            {"id": 2, "timeline_seconds": 4, "action": "snapshot", "payload": {"tempo": 90}},
        ],
    ],
)
def test_unreliable_or_changing_tempo(events: list[dict[str, Any]]) -> None:
    """A temporary tempo change or contradictory snapshot disables the entire interval."""
    from fl_activity_recorder.audition_detection import audition_tempo

    with pytest.raises(ValueError, match="BPM"):
        audition_tempo(events, 1, 11)


def test_temperature_preference_and_mapping(tmp_path: Path) -> None:
    """Use one reliable temperature sensor, and fall back conservatively across gaps."""
    store: Any = RawSignals(
        {
            "T1": [[i, 30 + i * 0.05] for i in range(101)],
            "TH": [[i, 29 - i * 0.05] for i in range(101)],
        }
    )
    curves, valid = controls(store, "s", 80, 90, 100)
    assert valid["T1"] and valid["TH"] and not valid["HR"]
    assert curves[-1, 2] > 0.5
    store.series["T1"] = [[80, 32], [90, 33]]
    fallback, valid = controls(store, "s", 80, 90, 100)
    assert not valid["T1"] and valid["TH"]
    assert fallback[-1, 2] < 0.5
    path = tmp_path / "temperature.wav"
    generate_ambient(path, 10, curves, 0, threading.Event())
    first = path.read_bytes()
    curves[:, 2] = 0.2
    generate_ambient(path, 10, curves, 0, threading.Event())
    assert first != path.read_bytes()


def test_surf_spectrum_temperature_and_filter_continuity(tmp_path: Path) -> None:
    """Distant surf suppresses hiss while warmer controls add a little brightness and level."""
    measurements = []
    for temperature in (0.2, 0.8):
        path = tmp_path / f"surf-{temperature}.wav"
        curves = np.full((101, 3), 0.5)
        curves[:, 2] = temperature
        generate_ambient(path, 10, curves, 42, threading.Event())
        with wave.open(str(path)) as audio:
            stereo = np.frombuffer(audio.readframes(audio.getnframes()), "<i2").reshape(-1, 2)
        samples = stereo[48000:-48000, 0].astype(float) / 32768
        power = np.abs(np.fft.rfft(samples)) ** 2
        frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
        assert power[frequencies > 3000].sum() / power.sum() < 0.005
        assert power[(frequencies >= 100) & (frequencies <= 1400)].sum() / power.sum() > 0.9
        measurements.append(
            (np.sqrt(np.mean(samples**2)), np.sum(frequencies * power) / power.sum())
        )
        jumps = np.abs(np.diff(stereo.astype(float), axis=0))
        assert np.mean(jumps[4095::4096]) < 3 * np.mean(jumps)
    assert 1 < measurements[1][0] / measurements[0][0] < 1.4
    assert measurements[1][1] > measurements[0][1] * 1.3


def test_hr_drum_grid_and_stem_audio(tmp_path: Path) -> None:
    """HR affects density and velocity, preserving eighth-note grid and bounded PCM."""
    from fl_activity_recorder.overlay_generator import drum_events, generate_drums

    low, high = np.full((101, 3), 0.3), np.full((101, 3), 0.7)
    quiet, intense = drum_events(10, low, 123), drum_events(10, high, 123)
    assert len(intense) > len(quiet)
    assert intense[0][2] > quiet[0][2]
    assert all(abs(at * 123 / 30 - round(at * 123 / 30)) < 1e-8 for at, _, _ in intense)
    paths = [tmp_path / f"drums-{i}.wav" for i in range(3)]
    for path, seed in zip(paths, (42, 42, 43), strict=True):
        generate_drums(path, 10.031, high, 123, seed, threading.Event())
    assert paths[0].read_bytes() == paths[1].read_bytes()
    assert paths[0].read_bytes() != paths[2].read_bytes()
    with wave.open(str(paths[0])) as audio:
        assert audio.getnframes() == round(10.031 * 48000)
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        assert 0 < np.max(np.abs(samples)) <= 0.16 * 32767
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        generate_drums(tmp_path / "cancelled.wav", 10, high, 123, 0, cancelled)


def test_combined_stems_cache_restart_and_failure(
    tmp_path: Path,
    media: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish complete stem sets; reuse controls and preserve ready audio after failure."""
    app = create_app(
        Settings(database_path=tmp_path / "db", data_directory=tmp_path / "data", enable_midi=False)
    )
    manager, db = app.state.overlays, app.state.database
    try:
        session_id = populate(db, app.state.signals, media["sound"], manager.captures.root)
        item = manager.auditions(session_id)["auditions"][0]
        # The UI eligibility pass already computed the controls and checked audio.
        with monkeypatch.context() as scoped:

            def unexpected(*args: Any, **kwargs: Any) -> None:
                raise AssertionError("Unchanged inputs must reuse validation and feature caches")

            scoped.setattr("fl_activity_recorder.overlays.controls", unexpected)
            scoped.setattr("fl_activity_recorder.overlays.verify_audio", unexpected)
            response = manager.request(session_id, item["id"], False, "physiological")
            record = wait_ready(manager, session_id, response["overlay_id"])
            assert record["status"] == "ready", record
            assert record["stems"] == ["ambient", "drums"] and record["bpm"] == 120
            assert record["spatial"]["values"] == []  # HR-only input has no wet effect.
            assert (
                manager.request(session_id, item["id"], False, "physiological")["overlay_id"]
                == record["overlay_id"]
            )
        client = TestClient(app)
        url = f"/api/sessions/{session_id}/overlays/{record['overlay_id']}/audio"
        assert client.get(url + "?stem=drums", headers={"Range": "bytes=0-43"}).status_code == 206
        assert client.get(url + "?stem=unknown").status_code == 409
        assert "stem_paths" not in manager.public(record)
        assert (
            manager.auditions(session_id)["auditions"][0]["overlay"]["overlay_id"]
            == record["overlay_id"]
        )
        db.save_overlay({**record, "generator_version": "physiology-v2"})
        stale = manager.auditions(session_id)["auditions"][0]
        assert stale["overlay"] is None and stale["needs_regeneration"]
        assert len(db.list_overlays(session_id)) == 1  # Listing never regenerates sound.
        db.save_overlay(record)
        paths = [
            manager.audio_path(session_id, record["overlay_id"], stem) for stem in record["stems"]
        ]
        with monkeypatch.context() as scoped:

            def fail(*args: Any, **kwargs: Any) -> None:
                raise ValueError("drum render failed")

            scoped.setattr("fl_activity_recorder.overlays.generate_drums", fail)
            replacement = manager.request(session_id, item["id"], True, "physiological")
            assert wait_ready(manager, session_id, replacement["overlay_id"])["status"] == "failed"
            assert all(path.exists() for path in paths)
            assert not list(paths[0].parent.glob("*.tmp"))
        manager.close()
        manager = OverlayManager(db, manager.captures, app.state.signals)
        assert all(path.exists() for path in paths)
        manager.audio_path(session_id, record["overlay_id"], "drums").unlink()
        replacement = manager.request(session_id, item["id"], False, "physiological")
        assert replacement["status"] in {"pending", "generating"}
        assert wait_ready(manager, session_id, replacement["overlay_id"])["status"] == "ready"
        manager.delete_session(session_id)
        assert not paths[0].parent.exists()
    finally:
        manager.close()
        db.close()


def test_temperature_fingerprint_and_requested_interval_only(
    tmp_path: Path,
    media: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temperature edits invalidate caches; a POST validates only its target interval."""
    app = create_app(
        Settings(database_path=tmp_path / "db", data_directory=tmp_path / "data", enable_midi=False)
    )
    manager, db = app.state.overlays, app.state.database
    try:
        session_id = populate(db, app.state.signals, media["sound"], manager.captures.root)
        detected, fingerprint, _ = manager._sources(session_id)
        audition_id = detected["auditions"][0]["id"]
        with monkeypatch.context() as scoped:

            def unexpected(*args: Any) -> None:
                raise AssertionError("POST must not validate the entire audition list")

            scoped.setattr(manager, "auditions", unexpected)
            response = manager.request(session_id, audition_id, False, "physiological")
            first = wait_ready(manager, session_id, response["overlay_id"])
        assert first["status"] == "ready"
        # Add temperature without changing the playback interval or original audio.
        store = app.state.signals
        store.start_session(session_id, 1000)
        descriptor = SignalDescriptor(
            "T1", "T1", "Temperature", "Temperature", "°C", 1, "EmotiBit-T1", "device", 0, "1"
        )
        store.append(descriptor, list(range(1000, 1015)), [32 + i * 0.01 for i in range(15)])
        store.finish_session(1014, interrupted=False, degraded=False)
        assert manager._sources(session_id)[1] != fingerprint
        assert manager.auditions(session_id)["auditions"][0]["overlay"] is None
        response = manager.request(session_id, audition_id, False, "physiological")
        second = wait_ready(manager, session_id, response["overlay_id"])
        assert second["status"] == "ready"
        assert second["temperature_source"] == "T1"
        assert second["overlay_id"] != first["overlay_id"]
        assert not list(manager._directory(session_id).glob("*.tmp"))
        # Legacy requests still work if historical recordings lack BPM evidence.
        with db.transaction() as connection:
            connection.execute(
                "DELETE FROM events WHERE session_id=? AND action='initial_snapshot'", (session_id,)
            )
        historical = manager.auditions(session_id)["auditions"][0]
        assert historical["eligible"] and historical["bpm"] == 100
        assert historical["bpm_source"] == "default"
        fallback = manager.request(session_id, audition_id, False, "physiological")
        fallback = wait_ready(manager, session_id, fallback["overlay_id"])
        assert fallback["status"] == "ready"
        assert fallback["bpm"] == 100 and fallback["bpm_source"] == "default"
        legacy = manager.request(session_id, audition_id, False, "ambient")
        assert wait_ready(manager, session_id, legacy["overlay_id"])["status"] == "ready"
    finally:
        manager.close()
        db.close()


def test_eda_trend_and_long_gap_controls() -> None:
    """EDA trend affects wet controls; unsupported gaps return gently to neutral."""
    store: Any = RawSignals({"EA": [[i, 1 + i * 0.02] for i in range(101)]})
    curves, valid = controls(store, "s", 70, 90, 100)
    assert valid["EA"] and curves[-1, 1] > 0.5
    store.series["EA"] = [[i, 1 + i * 0.02] for i in range(101) if not 78 <= i <= 84]
    gaps, valid = controls(store, "s", 70, 90, 100)
    assert valid["EA"]  # Sufficient coverage remains on both sides of the gap.
    assert abs(gaps[140, 1] - 0.5) < abs(gaps[70, 1] - 0.5)


def test_invalid_preset_shape_returns_422(tmp_path: Path) -> None:
    """Malformed JSON preset values must never trigger a server type error."""
    app = create_app(Settings(database_path=tmp_path / "db", enable_midi=False))
    try:
        client = TestClient(app)
        for value in ({}, [], 12, None):
            assert (
                client.post(
                    "/api/sessions/missing/overlays", json={"audition_id": "a", "preset": value}
                ).status_code
                == 422
            )
    finally:
        app.state.overlays.close()
        app.state.database.close()


@pytest.mark.parametrize("events", [
    [],
    [tempo_event(1, 0, True)],
    [tempo_event(1, 0, 0)],
    [tempo_event(1, 0, 120), tempo_event(2, 0.5, None)],
    [{"id": 1, "timeline_seconds": 0, "action": "initial_snapshot", "payload": {}}],
])
def test_historical_tempo_defaults_are_labelled(events: list[dict[str, Any]]) -> None:
    """Missing historical tempo uses 100, without claiming it was recorded."""
    from fl_activity_recorder.audition_detection import audition_tempo, resolve_audition_tempo

    assert audition_tempo(events, 1, 11) == 100
    assert resolve_audition_tempo(events, 1, 11) == (100, "default")
    assert resolve_audition_tempo([tempo_event(1, 0, 100)], 1, 11) == (100, "recorded")
