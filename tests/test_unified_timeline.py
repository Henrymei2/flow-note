from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fl_activity_recorder.assets import CaptureManager
from fl_activity_recorder.database import Database
from fl_activity_recorder.emotibit import EmotiBitImporter
from fl_activity_recorder.timebase import TimelineClock


class FakeLsl:
    """Provide a deterministic local clock for timeline tests."""

    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def local_clock(self) -> float:
        """Return the configured clock value."""
        return self.value


def create_session(database: Database, *, completed: bool = True) -> dict[str, object]:
    """Create a completed session covering LSL seconds 1000 through 1010."""
    session = database.create_session(
        project_title="Timeline",
        fl_version="26.2",
        api_version=45,
        recorder_version="test",
        timeline_zero_lsl=1000.0,
        timeline_zero_monotonic=500.0,
        clock_mode="lsl",
    )
    database.add_sync_marker(
        str(session["id"]),
        {"kind": "start", "sequence": 0, "lsl_seconds": 1000.0},
    )
    database.add_sync_marker(
        str(session["id"]),
        {"kind": "stop", "sequence": 1, "lsl_seconds": 1010.0},
    )
    if completed:
        database.finish_session(str(session["id"]), timeline_end_lsl=1010.0)
    return database.get_session(str(session["id"]))


def test_clock_uses_best_adjacent_lsl_mapping() -> None:
    fake = FakeLsl()
    clock = TimelineClock(fake)
    reading = clock.reading()
    assert clock.available is True
    assert clock.mode == "lsl"
    assert reading.lsl_seconds == 1000.0
    assert clock.monotonic_to_lsl(reading.monotonic_seconds) == pytest.approx(1000.0, abs=0.01)


def test_capture_chunks_are_ordered_and_finalized(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database, completed=False)
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
    assert asset["start_offset_seconds"] == pytest.approx(0.25)


def write_emotibit_bundle(root: Path, *, lsl_header: bool = True) -> list[Path]:
    """Write a minimal official-style DataParser bundle."""
    info = root / "recording_info.json"
    info.write_text(
        json.dumps(
            [
                {
                    "info": {
                        "name": "EDA",
                        "typeTags": ["EA"],
                        "nominal_srate": 15,
                        "units": "microsiemens",
                    }
                }
            ]
        ),
        encoding="utf-8",
    )
    csv_path = root / "recording_EA.csv"
    timestamp = "LslMarkerSourceTimestamp" if lsl_header else "WrongTimestamp"
    csv_path.write_text(
        f"LocalTimestamp,EmotiBitTimestamp,PacketNumber,DataLength,TypeTag,ProtocolVersion,DataReliability,{timestamp},EA\n"
        "1700000000,5000,1,1,EA,1,100,999.5,0.1\n"
        "1700000001,6000,2,1,EA,1,100,1005.0,0.2\n"
        "1700000002,7000,3,1,EA,1,100,1010.5,0.3\n",
        encoding="utf-8",
    )
    return [info, csv_path]


def test_imports_parser_output_and_queries_relative_signal(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    manager = CaptureManager(database, tmp_path / "sessions", TimelineClock(FakeLsl()))
    importer = EmotiBitImporter(database, manager)
    result = importer.import_files(str(session["id"]), write_emotibit_bundle(tmp_path))
    assert result["status"] == "ready"
    streams = database.list_signal_streams(str(session["id"]))
    assert streams[0]["unit"] == "microsiemens"
    assert streams[0]["nominal_rate"] == 15
    query = importer.query(str(session["id"]), "EA", 0, 10, 100)
    assert query["points"] == [[5.0, 0.2]]
    stored = manager.root / streams[0]["relative_path"] / "packet.npy"
    assert np.load(stored).tolist() == [1, 2, 3]
    assert database.get_session(str(session["id"]))["sync_quality"] == "aligned_unverified"


def test_import_rejects_missing_lsl_timestamp_without_mutation(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    manager = CaptureManager(database, tmp_path / "sessions", TimelineClock(FakeLsl()))
    importer = EmotiBitImporter(database, manager)
    with pytest.raises(ValueError, match="LslMarkerSourceTimestamp"):
        importer.import_files(str(session["id"]), write_emotibit_bundle(tmp_path, lsl_header=False))
    assert database.list_signal_streams(str(session["id"])) == []


def test_replace_rolls_back_files_when_database_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    manager = CaptureManager(database, tmp_path / "sessions", TimelineClock(FakeLsl()))
    importer = EmotiBitImporter(database, manager)
    source_files = write_emotibit_bundle(tmp_path)
    importer.import_files(str(session["id"]), source_files)
    original_hash = database.get_asset(str(session["id"]), "emotibit")["sha256"]
    source_files[1].write_text(
        source_files[1].read_text(encoding="utf-8").replace("0.2", "9.2"),
        encoding="utf-8",
    )

    def fail_commit(*args: object, **kwargs: object) -> dict[str, object]:
        """Simulate a SQLite failure after the staged files are installed."""
        del args, kwargs
        raise RuntimeError("database failed")

    monkeypatch.setattr(database, "commit_emotibit_import", fail_commit)
    with pytest.raises(RuntimeError, match="database failed"):
        importer.import_files(str(session["id"]), source_files, replace=True)
    stored = manager.root / str(
        database.list_signal_streams(str(session["id"]))[0]["relative_path"]
    )
    assert np.load(stored / "value.npy").tolist() == [0.1, 0.2, 0.3]
    assert database.get_asset(str(session["id"]), "emotibit")["sha256"] == original_hash


def test_delete_rejects_asset_path_escape(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    manager = CaptureManager(database, tmp_path / "sessions", TimelineClock(FakeLsl()))
    with pytest.raises((KeyError, ValueError)):
        manager.session_directory("../outside")


def test_import_job_status_is_persisted(tmp_path: Path) -> None:
    database = Database(tmp_path / "activity.db")
    session = create_session(database)
    database.create_import_job("job-1", str(session["id"]), replace_existing=False)
    database.finish_import_job("job-1", "completed", result={"streams": 1})
    database.close()
    reopened = Database(tmp_path / "activity.db")
    job = reopened.get_import_job("job-1")
    assert job["status"] == "completed"
    assert job["result"] == {"streams": 1}
