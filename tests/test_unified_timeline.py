from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from fl_activity_recorder.assets import CaptureManager
from fl_activity_recorder.database import Database
from fl_activity_recorder.live_signals import (
    EmotiBitLslReceiver,
    LiveSignalStore,
    SignalDescriptor,
    describe_stream,
)
from fl_activity_recorder.timebase import TimelineClock


class FakeLsl:
    """Provide a deterministic LSL clock and empty discovery result."""

    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def local_clock(self) -> float:
        """Return the configured clock value."""
        return self.value

    def resolve_streams(self, *, wait_time: float) -> list[Any]:
        """Return no automatically discovered streams."""
        del wait_time
        return []


class FakeXml:
    """Expose the tiny pylsl XML API used for channel metadata."""

    def __init__(self, channels: list[tuple[str, str]], index: int = 0) -> None:
        self.channels = channels
        self.index = index

    def child(self, name: str) -> FakeXml:
        """Return the first channel node."""
        del name
        return FakeXml(self.channels)

    def child_value(self, name: str) -> str:
        """Return the requested channel metadata value."""
        return self.channels[self.index][0 if name == "label" else 1]

    def next_sibling(self) -> FakeXml:
        """Return the next channel node."""
        return FakeXml(self.channels, self.index + 1)


class FakeInfo:
    """Describe a two-channel numeric EmotiBit stream."""

    def name(self) -> str:
        """Return the stream name."""
        return "EmotiBit-Bio"

    def source_id(self) -> str:
        """Return the stable source ID."""
        return "device-1"

    def channel_count(self) -> int:
        """Return the number of numeric channels."""
        return 2

    def nominal_srate(self) -> float:
        """Return the nominal stream rate."""
        return 15.0

    def channel_format(self) -> int:
        """Return pylsl's float32 channel-format constant."""
        return 1

    def desc(self) -> FakeXml:
        """Return channel labels and units."""
        return FakeXml([("EA", "microsiemens"), ("custom", "units")])


def create_session(database: Database) -> dict[str, object]:
    """Create an LSL-backed session beginning at second 1000."""
    session = database.create_session(
        project_title="Timeline",
        fl_version="26.2",
        api_version=45,
        recorder_version="test",
        timeline_zero_lsl=1000.0,
        timeline_zero_monotonic=500.0,
        clock_mode="lsl",
    )
    return database.get_session(str(session["id"]))


def descriptor(track_id: str = "EA") -> SignalDescriptor:
    """Return deterministic metadata for one test track."""
    return SignalDescriptor(
        track_id=track_id,
        type_tag=track_id,
        display_name="EDA",
        group_name="EDA",
        unit="microsiemens",
        nominal_rate=15.0,
        stream_name="EmotiBit-EA",
        source_id="device-1",
        channel_index=0,
        channel_format="1",
    )


def test_clock_uses_best_adjacent_lsl_mapping() -> None:
    fake = FakeLsl()
    clock = TimelineClock(fake)
    reading = clock.reading()
    assert clock.available is True
    assert reading.lsl_seconds == 1000.0
    assert clock.monotonic_to_lsl(reading.monotonic_seconds) == pytest.approx(1000.0, abs=0.01)


def test_capture_chunks_are_ordered_and_finalized(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    manager = CaptureManager(database, tmp_path / "sessions", TimelineClock(FakeLsl()))
    session_id = str(session["id"])
    manager.start(
        session_id,
        mime_type="video/webm",
        started_lsl=1000.25,
        metadata={"has_audio": False},
    )
    with pytest.raises(ValueError, match="Expected capture chunk 0"):
        manager.append(session_id, 1, b"late")
    manager.append(session_id, 0, b"header")
    manager.append(session_id, 1, b"body")
    asset = manager.complete(
        session_id,
        ended_lsl=1009.0,
        duration_seconds=8.75,
        metadata={"width": 1280, "height": 720},
    )
    assert manager.video_path(session_id).read_bytes() == b"headerbody"
    assert asset["status"] == "ready"


def test_live_chunks_are_incremental_and_query_across_boundaries(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    store = LiveSignalStore(database, tmp_path / "sessions", chunk_samples=2)
    session_id = str(session["id"])
    store.start_session(session_id, 1000.0)
    store.append(descriptor(), [999.9, 1000.0, 1000.5], [9.0, 0.1, 0.2])
    store.append(descriptor(), [1001.0, 1001.5], [0.3, 0.4])
    store.finish_session(1002.0, interrupted=False, degraded=False)
    result = store.query(session_id, "EA", 0.25, 1.25, 100)
    assert result["points"] == [[0.5, 0.2], [1.0, 0.3]]
    stream = database.list_signal_streams(session_id)[0]
    assert stream["sample_count"] == 4
    assert stream["metadata"]["source"] == "live_lsl"
    assert database.get_session(session_id)["sync_quality"] == "aligned_unverified"


def test_long_query_uses_chunk_minmax(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    store = LiveSignalStore(database, tmp_path / "sessions", chunk_samples=512)
    session_id = str(session["id"])
    store.start_session(session_id, 1000.0)
    for start in range(0, 14_400, 512):
        stop = min(14_400, start + 512)
        store.append(
            descriptor(),
            [1000.0 + float(index) for index in range(start, stop)],
            [float(index % 100) for index in range(start, stop)],
        )
    store.finish_session(15_400.0, interrupted=False, degraded=False)
    result = store.query(session_id, "EA", 0, 14_400, 10)
    assert result["mode"] == "minmax"
    assert len(result["points"]) <= 10


def test_reopen_marks_live_asset_interrupted_and_keeps_sealed_chunks(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    store = LiveSignalStore(database, tmp_path / "sessions", chunk_samples=2)
    session_id = str(session["id"])
    store.start_session(session_id, 1000.0)
    store.append(descriptor(), [1000.0, 1000.5], [0.1, 0.2])
    database.close()
    reopened = Database(tmp_path / "activity.db")
    assert reopened.get_session(session_id)["status"] == "interrupted"
    assert reopened.get_session(session_id)["sync_quality"] == "degraded"
    assert reopened.get_asset(session_id, "emotibit")["status"] == "interrupted"
    assert len(reopened.list_signal_chunks(session_id, "EA", 0, 1)) == 1


def test_multichannel_metadata_preserves_known_and_unknown_tracks() -> None:
    streams = describe_stream(FakeInfo())
    assert streams[0].track_id == "EA"
    assert streams[0].group_name == "EDA"
    assert streams[0].unit == "microsiemens"
    assert streams[1].track_id == "custom"
    assert streams[1].group_name == "Other"


def test_receiver_ignores_pre_session_samples_and_batches_source_timestamps(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    store = LiveSignalStore(database, tmp_path / "sessions", chunk_samples=100)
    published: list[dict[str, Any]] = []
    receiver = EmotiBitLslReceiver(store, published.append, lsl=FakeLsl())
    track = descriptor()
    receiver.accept_samples([track], [[1.0]], [999.5])
    receiver.begin_session(str(session["id"]), 1000.0)
    receiver.accept_samples([track], [[2.0], [3.0]], [1000.25, 1000.5])
    receiver._flush_live_batch()
    batch = next(item for item in published if item["type"] == "emotibit_samples")
    assert batch["tracks"][0]["points"] == [[0.25, 2.0], [0.5, 3.0]]
    receiver.finish_session(1001.0, interrupted=False)
    assert database.list_signal_streams(str(session["id"]))[0]["sample_count"] == 2


def test_receiver_marks_disconnect_and_recovery_without_stopping_session(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    store = LiveSignalStore(database, tmp_path / "sessions")
    receiver = EmotiBitLslReceiver(store, lambda _item: None, lsl=FakeLsl())
    track = descriptor()
    receiver.accept_samples([track], [[1.0]], [1000.0])
    receiver.begin_session(str(session["id"]), 1000.0)
    receiver._last_any_monotonic = time.monotonic() - 3
    receiver._last_by_track["EA"] = time.monotonic() - 3
    receiver._check_gaps(time.monotonic())
    receiver.accept_samples([track], [[2.0]], [1003.0])
    receiver._check_gaps(time.monotonic())
    receiver.finish_session(1004.0, interrupted=False)
    codes = [
        row["code"]
        for row in database._connection.execute("SELECT code FROM diagnostics ORDER BY id")
    ]
    assert codes[:2] == ["emotibit_lsl_disconnected", "emotibit_track_disconnected"]
    assert "emotibit_lsl_recovered" in codes
    assert database.get_session(str(session["id"]))["sync_quality"] == "degraded"


def test_delete_rejects_asset_path_escape(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    manager = CaptureManager(database, tmp_path / "sessions", TimelineClock(FakeLsl()))
    with pytest.raises((KeyError, ValueError)):
        manager.session_directory("../outside")
