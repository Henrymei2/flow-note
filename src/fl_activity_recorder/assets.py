from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from .database import Database
from .timebase import TimelineClock


class CaptureManager:
    """Own crash-tolerant, sequential browser recording uploads."""

    def __init__(self, database: Database, root: Path, clock: TimelineClock) -> None:
        self.database = database
        self.root = root.resolve()
        self.clock = clock
        self.root.mkdir(parents=True, exist_ok=True)
        self._next_chunks: dict[str, int] = {}
        self._locks: dict[str, threading.Lock] = {}

    def session_directory(self, session_id: str) -> Path:
        """Return a contained asset directory for a validated session id."""
        self.database.get_session(session_id)
        target = (self.root / session_id).resolve()
        if target.parent != self.root:
            raise ValueError("Invalid session asset path")
        return target

    def start(
        self,
        session_id: str,
        *,
        mime_type: str,
        started_lsl: float,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Initialize a new partial browser capture."""
        if self.database.get_session(session_id)["status"] != "recording":
            raise RuntimeError("Session is not recording")
        video = self.session_directory(session_id) / "video"
        video.mkdir(parents=True, exist_ok=True)
        partial = video / "capture.webm.part"
        if partial.exists():
            partial.unlink()
        partial.touch()
        self._next_chunks[session_id] = 0
        self._locks[session_id] = threading.Lock()
        session = self.database.get_session(session_id)
        zero = float(session["timeline_zero_lsl"])
        return self.database.upsert_asset(
            session_id,
            "video",
            "recording",
            relative_path=str(partial.relative_to(self.root)),
            mime_type=mime_type,
            started_lsl=started_lsl,
            start_offset_seconds=started_lsl - zero,
            metadata_json=metadata,
        )

    def append(self, session_id: str, sequence: int, data: bytes) -> int:
        """Append exactly the next browser capture chunk."""
        expected = self._next_chunks.get(session_id)
        if expected is None:
            raise RuntimeError("Capture is not active")
        if self.database.get_session(session_id)["status"] != "recording":
            raise RuntimeError("Session is no longer recording")
        if sequence != expected:
            raise ValueError(f"Expected capture chunk {expected}, received {sequence}")
        partial = self.session_directory(session_id) / "video" / "capture.webm.part"
        lock = self._locks[session_id]
        with lock, partial.open("ab") as handle:
            handle.write(data)
            handle.flush()
        self._next_chunks[session_id] = expected + 1
        return len(data)

    def complete(
        self,
        session_id: str,
        *,
        ended_lsl: float,
        duration_seconds: float,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Finalize a browser capture with an atomic rename."""
        if session_id not in self._next_chunks:
            raise RuntimeError("Capture is not active")
        existing = self.database.get_asset(session_id, "video")
        if ended_lsl < float(existing["started_lsl"]):
            raise ValueError("Capture end precedes its start")
        if duration_seconds < 0:
            raise ValueError("Capture duration cannot be negative")
        directory = self.session_directory(session_id) / "video"
        partial = directory / "capture.webm.part"
        final = directory / "capture.webm"
        os.replace(partial, final)
        digest = _sha256(final)
        self._next_chunks.pop(session_id, None)
        self._locks.pop(session_id, None)
        combined_metadata = {**existing["metadata"], **metadata}
        return self.database.upsert_asset(
            session_id,
            "video",
            "ready",
            relative_path=str(final.relative_to(self.root)),
            size_bytes=final.stat().st_size,
            sha256=digest,
            ended_lsl=ended_lsl,
            duration_seconds=duration_seconds,
            metadata_json=combined_metadata,
        )

    def abort(self, session_id: str, reason: str) -> dict[str, Any]:
        """Mark an interrupted capture while retaining its partial file."""
        self._next_chunks.pop(session_id, None)
        self._locks.pop(session_id, None)
        try:
            existing = self.database.get_asset(session_id, "video")
            metadata = {**existing["metadata"], "abort_reason": reason}
        except KeyError:
            metadata = {"abort_reason": reason}
        return self.database.upsert_asset(
            session_id, "video", "interrupted", metadata_json=metadata
        )

    def video_path(self, session_id: str) -> Path:
        """Return the finalized video path for a session."""
        asset = self.database.get_asset(session_id, "video")
        if asset["status"] != "ready" or not asset["relative_path"]:
            raise KeyError((session_id, "video"))
        path = (self.root / str(asset["relative_path"])).resolve()
        if self.root not in path.parents:
            raise ValueError("Stored video path escapes the asset root")
        return path

    def delete_session_files(self, session_id: str) -> None:
        """Delete only the contained directory owned by a session."""
        target = (self.root / session_id).resolve()
        if target.parent != self.root:
            raise ValueError("Invalid session asset path")
        if target.exists():
            shutil.rmtree(target)


def _sha256(path: Path) -> str:
    """Calculate a file SHA-256 without buffering it in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
