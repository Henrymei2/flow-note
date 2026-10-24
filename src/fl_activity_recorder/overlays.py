"""Own local overlay tasks, source validation, cache publication, and cancellation."""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .assets import CaptureManager
from .audition_detection import detect_auditions
from .database import Database
from .live_signals import LiveSignalStore
from .overlay_generator import CONFIG, GENERATOR_VERSION, SAMPLE_RATE, controls, generate_ambient


def _digest(value: Any) -> str:
    """Hash canonical generation inputs without depending on Python hash randomization."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def ffmpeg_executable() -> str:
    """Resolve the FFmpeg binary supplied by the installed platform wheel."""
    import imageio_ffmpeg  # type: ignore[import-untyped]

    return str(imageio_ffmpeg.get_ffmpeg_exe())


def verify_audio(path: Path, start: float, duration: float, cancelled: threading.Event) -> None:
    """Decode the entire selected audio range with bounded memory and a hard timeout."""
    try:
        executable = ffmpeg_executable()
    except (ImportError, RuntimeError) as exc:
        raise ValueError("Overlay unavailable: the bundled audio decoder is missing") from exc
    with tempfile.TemporaryFile() as decoded, tempfile.TemporaryFile() as errors:
        command = [executable, "-v", "error", "-nostdin", "-xerror", "-i", str(path),
                   "-ss", str(start), "-t", str(duration), "-map", "0:a:0", "-vn",
                   "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"]
        with subprocess.Popen(command, stdout=decoded, stderr=errors,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            deadline = time.monotonic() + 60
            try:
                while process.poll() is None:
                    if cancelled.wait(0.05):
                        raise RuntimeError("Overlay generation cancelled")
                    if time.monotonic() > deadline:
                        raise ValueError("Overlay unavailable: audio decoding timed out")
                if process.returncode != 0 or decoded.tell() < duration * 8000 * 2 * 0.98:
                    raise ValueError("Overlay unavailable: no recorded audio or incomplete audio")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


class OverlayManager:
    """Serialize heavy work and keep generated resources within their owning session."""

    def __init__(
        self, database: Database, captures: CaptureManager, signals: LiveSignalStore
    ) -> None:
        """Initialize one application's worker and recover interrupted local tasks."""
        self.database = database
        self.captures = captures
        self.signals = signals
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="overlay")
        self._cancel: dict[str, threading.Event] = {}
        self._futures: dict[str, tuple[str, Future[None]]] = {}
        self._audio_cache: set[str] = set()
        self._closing = False
        self._readers: dict[str, int] = {}
        self._condition = threading.Condition(self._lock)
        for record in database.list_overlays():
            if record["status"] in {"pending", "generating"}:
                record.update(status="failed", error="Generation interrupted; please retry")
                database.save_overlay(record)
        # Only remove files inside the known overlay directory of existing sessions.
        for session in database.list_sessions():
            directory = self._directory(session["id"])
            if directory.exists():
                referenced = {str((captures.root / str(r["relative_path"])).resolve())
                              for r in database.list_overlays(session["id"])
                              if r["status"] == "ready" and r.get("relative_path")}
                for path in directory.glob("*.tmp"):
                    path.unlink(missing_ok=True)
                for path in directory.glob("*.wav"):
                    if str(path.resolve()) not in referenced:
                        path.unlink(missing_ok=True)

    def close(self) -> None:
        """Cancel active work and join the worker before the database is closed."""
        with self._lock:
            self._closing = True
            for event in self._cancel.values():
                event.set()
            self._condition.wait_for(lambda: not any(self._readers.values()))
        self._executor.shutdown(wait=True)

    def _event(self, session_id: str) -> threading.Event:
        """Return the cancellation token owned by one session for this application."""
        with self._lock:
            if self._closing:
                raise RuntimeError("Overlay service is closing")
            event = self._cancel.setdefault(session_id, threading.Event())
            if event.is_set():
                raise RuntimeError("Session is being deleted")
            return event

    def _directory(self, session_id: str) -> Path:
        """Reject overlay-directory symlinks that escape their owning session."""
        owner = self.captures.session_directory(session_id)
        directory = (owner / "overlays").resolve()
        if directory.parent != owner:
            raise ValueError("Overlay directory escapes its session")
        return directory

    def _sources(self, session_id: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """Read authoritative session data and fingerprint immutable source references."""
        session = self.database.get_session(session_id)
        events, diagnostics = self.database.audition_sources(session_id)
        duration = max(0, (session["timeline_end_lsl"] or session["timeline_zero_lsl"] or 0)
                       - (session["timeline_zero_lsl"] or 0))
        detected = detect_auditions(events, diagnostics, duration)
        video = next((a for a in self.database.list_assets(session_id) if a["kind"] == "video"), {})
        chunks = [self.database.list_signal_chunks(session_id, tag, 0, duration)
                  for tag in ("HR", "EA")]
        stat = None
        if video.get("status") == "ready":
            path = self.captures.video_path(session_id)
            if path.is_file():
                info = path.stat()
                stat = (info.st_size, info.st_mtime_ns)
        fingerprint = _digest([events, diagnostics, chunks, video, stat, duration])
        return {**detected, "duration": duration, "session": session}, fingerprint, video

    def auditions(self, session_id: str) -> dict[str, Any]:
        """Return detected intervals and verified generation availability, without synthesis."""
        with self._reading(session_id):
            return self._auditions(session_id)

    @contextmanager
    def _reading(self, session_id: str) -> Iterator[None]:
        """Keep source files alive until a cancellable availability read has finished."""
        with self._condition:
            self._event(session_id)
            self._readers[session_id] = self._readers.get(session_id, 0) + 1
        try:
            yield
        finally:
            with self._condition:
                self._readers[session_id] -= 1
                self._condition.notify_all()

    def _auditions(self, session_id: str) -> dict[str, Any]:
        """Inspect one session while its source files are protected from deletion."""
        detected, fingerprint, video = self._sources(session_id)
        session = detected["session"]
        event = self._event(session_id)
        with self._lock:
            records = self.database.list_overlays(session_id)
        for item in detected["auditions"]:
            item["overlay"] = next((self.public(r) for r in reversed(records)
                                    if r["audition_id"] == item["id"]
                                    and r["source_fingerprint"] == fingerprint
                                    and r["status"] == "ready"), None)
            if not item["eligible"]:
                continue
            try:
                if session["status"] == "recording":
                    raise ValueError("Stop recording before generating an overlay")
                if video.get("status") != "ready":
                    raise ValueError("Overlay unavailable: no recorded audio.")
                offset = float(video.get("start_offset_seconds") or 0)
                if (item["start_time"] < offset or item["end_time"] >
                        offset + float(video.get("duration_seconds") or 0) + 0.02):
                    raise ValueError("Audition is not fully covered by the recording")
                cache_key = _digest([fingerprint, item["id"]])
                with self._lock:
                    cached = cache_key in self._audio_cache
                if not cached:
                    verify_audio(self.captures.video_path(session_id),
                                 item["start_time"] - offset, item["duration"], event)
                    with self._lock:
                        self._audio_cache.add(cache_key)
                _, valid = controls(self.signals, session_id, item["start_time"],
                                    item["end_time"], detected["duration"])
                item["channels"] = valid
            except (ValueError, OSError, KeyError) as exc:
                item.update(eligible=False, reason=str(exc))
        return {"session_id": session_id, "auditions": detected["auditions"],
                "diagnostics": detected["diagnostics"]}

    @staticmethod
    def public(record: dict[str, Any]) -> dict[str, Any]:
        """Expose generation metadata without local filesystem references or cache internals."""
        return {key: value for key, value in record.items()
                if key not in {"relative_path", "cache_key", "source_fingerprint"}}

    def get(self, session_id: str, overlay_id: str) -> dict[str, Any]:
        """Resolve a record strictly within its owning session."""
        with self._lock:
            self.database.get_session(session_id)
            for record in self.database.list_overlays(session_id):
                if record["overlay_id"] == overlay_id:
                    return record
        raise KeyError(overlay_id)

    def audio_path(self, session_id: str, overlay_id: str) -> Path:
        """Return a contained ready WAV, rejecting stale or missing source assets."""
        record = self.get(session_id, overlay_id)
        _, fingerprint, _ = self._sources(session_id)
        if record["status"] != "ready" or record["source_fingerprint"] != fingerprint:
            raise ValueError("Overlay is not ready or its sources have changed")
        path: Path = (self.captures.root / str(record["relative_path"])).resolve()
        directory = self._directory(session_id)
        if path.parent != directory or not path.is_file():
            raise ValueError("Overlay audio is missing")
        return path

    def request(self, session_id: str, audition_id: str, regenerate: bool) -> dict[str, Any]:
        """Validate a server-owned interval and deduplicate or queue its generation."""
        available = self.auditions(session_id)
        item = next((a for a in available["auditions"] if a["id"] == audition_id), None)
        if not item or not item["eligible"]:
            raise ValueError(item["reason"] if item else "Unknown audition")
        _, fingerprint, _ = self._sources(session_id)
        with self._lock:
            self._event(session_id)
            self.database.get_session(session_id)
            matching = [r for r in self.database.list_overlays(session_id)
                        if r["audition_id"] == audition_id
                        and r["source_fingerprint"] == fingerprint
                        and r["config_hash"] == _digest(CONFIG)
                        and r["generator_version"] == GENERATOR_VERSION]
            for previous in reversed(matching):
                if previous["status"] in {"pending", "generating"}:
                    return self.public(previous)
                if previous["status"] == "ready" and not regenerate:
                    try:
                        self.audio_path(session_id, previous["overlay_id"])
                        return self.public(previous)
                    except ValueError:
                        previous.update(status="failed", error="Cached WAV is missing")
                        self.database.save_overlay(previous)
            if sum(not future.done() for _, future in self._futures.values()) >= 8:
                raise RuntimeError("Overlay queue is full; retry after a task finishes")
            seed = secrets.randbits(32) if regenerate else 0
            key = _digest([session_id, audition_id, fingerprint, GENERATOR_VERSION, CONFIG, seed])
            failed = next((r for r in matching if r["cache_key"] == key), None)
            record = {
                "overlay_id": failed["overlay_id"] if failed else str(uuid.uuid4()),
                "session_id": session_id, "audition_id": audition_id,
                "start_time": item["start_time"], "end_time": item["end_time"],
                "duration_seconds": item["duration"], "preset": "ambient",
                "generator_version": GENERATOR_VERSION, "config": CONFIG, "seed": seed,
                "config_hash": _digest(CONFIG), "sample_rate": SAMPLE_RATE,
                "status": "pending", "error": None, "cache_key": key,
                "source_fingerprint": fingerprint,
            }
            self.database.save_overlay(record)
            future = self._executor.submit(self._generate, dict(record))
            self._futures[record["overlay_id"]] = (session_id, future)
            return self.public(record)

    def _generate(self, record: dict[str, Any]) -> None:
        """Generate a temporary WAV and publish it only while its owner still exists."""
        temporary: Path | None = None
        try:
            session_id = record["session_id"]
            event = self._event(session_id)
            with self._lock:
                record["status"] = "generating"
                self.database.save_overlay(record)
                directory = self._directory(session_id)
                directory.mkdir(exist_ok=True)
            temporary = directory / f"{record['overlay_id']}.tmp"
            detected, fingerprint, _ = self._sources(session_id)
            if fingerprint != record["source_fingerprint"]:
                raise RuntimeError("Source data changed; select the audition again")
            curves, valid = controls(self.signals, session_id, record["start_time"],
                                     record["end_time"], detected["duration"])
            generate_ambient(temporary, record["duration_seconds"], curves, record["seed"], event)
            with self._lock:
                if event.is_set() or fingerprint != self._sources(session_id)[1]:
                    raise RuntimeError("Generation cancelled or source data changed")
                final = temporary.with_suffix(".wav")
                temporary.replace(final)
                record.update(status="ready", channels=valid,
                              relative_path=str(final.relative_to(self.captures.root)))
                self.database.save_overlay(record)
                for old in self.database.list_overlays(session_id):
                    if (old["audition_id"] == record["audition_id"]
                            and old["overlay_id"] != record["overlay_id"]):
                        if old.get("relative_path"):
                            old_path = (self.captures.root / old["relative_path"]).resolve()
                            if old_path.parent == directory:
                                old_path.unlink(missing_ok=True)
                        self.database.remove_overlay(old["overlay_id"])
        except Exception as exc:
            with self._lock:
                try:
                    self.database.get_session(record["session_id"])
                    record.update(status="failed", error=str(exc))
                    self.database.save_overlay(record)
                except KeyError:
                    pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            with self._lock:
                self._futures.pop(record["overlay_id"], None)

    def delete_session(self, session_id: str) -> None:
        """Cancel and join session work before deleting its owned files and database row."""
        with self._lock:
            self._cancel.setdefault(session_id, threading.Event()).set()
            self._condition.wait_for(lambda: not self._readers.get(session_id, 0))
            futures = [future for owner, future in self._futures.values() if owner == session_id]
        for future in futures:
            future.result()
        with self._lock:
            self.captures.delete_session_files(session_id)
            if not self.database.delete_session(session_id):
                raise KeyError(session_id)
            self._audio_cache.clear()
