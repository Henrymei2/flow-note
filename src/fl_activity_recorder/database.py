from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ActivityEvent


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection:
            yield self._connection

    def _migrate(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('recording','completed','interrupted')),
                    project_title TEXT,
                    fl_version TEXT,
                    api_version INTEGER,
                    recorder_version TEXT NOT NULL,
                    observer_version TEXT,
                    observer_build TEXT,
                    timeline_zero_lsl REAL,
                    timeline_zero_monotonic REAL,
                    timeline_zero_utc REAL,
                    monotonic_lsl_offset REAL,
                    clock_model_version TEXT,
                    timeline_end_lsl REAL,
                    clock_mode TEXT,
                    video_status TEXT NOT NULL DEFAULT 'missing',
                    emotibit_status TEXT NOT NULL DEFAULT 'missing',
                    sync_quality TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    monotonic_seconds REAL NOT NULL,
                    category TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT,
                    entity_key TEXT,
                    summary TEXT NOT NULL,
                    confidence TEXT NOT NULL
                        CHECK(confidence IN ('exact','state_diff','signal_only')),
                    payload_json TEXT NOT NULL,
                    observed_monotonic_seconds REAL,
                    received_monotonic_seconds REAL,
                    timeline_seconds REAL,
                    transport_delay_ms REAL,
                    time_confidence TEXT NOT NULL DEFAULT 'received'
                );
                CREATE INDEX IF NOT EXISTS events_session_sequence
                    ON events(session_id, sequence, id);
                CREATE INDEX IF NOT EXISTS events_filter
                    ON events(session_id, category, confidence);
                CREATE TABLE IF NOT EXISTS diagnostics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    occurred_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recording_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    relative_path TEXT,
                    mime_type TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    started_lsl REAL,
                    ended_lsl REAL,
                    start_offset_seconds REAL,
                    duration_seconds REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(session_id, kind)
                );
                CREATE TABLE IF NOT EXISTS signal_streams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    type_tag TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    unit TEXT,
                    nominal_rate REAL,
                    sample_count INTEGER NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    relative_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(session_id, type_tag)
                );
                CREATE TABLE IF NOT EXISTS signal_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    type_tag TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    min_value REAL NOT NULL,
                    max_value REAL NOT NULL,
                    relative_path TEXT NOT NULL,
                    UNIQUE(session_id, type_tag, sequence)
                );
                CREATE INDEX IF NOT EXISTS signal_chunks_window
                    ON signal_chunks(session_id, type_tag, start_seconds, end_seconds);
                CREATE TABLE IF NOT EXISTS sync_markers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    lsl_seconds REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS clock_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    lsl_seconds REAL NOT NULL,
                    monotonic_seconds REAL,
                    utc_seconds REAL,
                    rtt_ms REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS emotibit_imports (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
                    replace_existing INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_json TEXT,
                    error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS emotibit_one_running_per_session
                    ON emotibit_imports(session_id) WHERE status='running';
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "observer_version" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN observer_version TEXT")
            if "observer_build" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN observer_build TEXT")
            session_additions = {
                "timeline_zero_lsl": "REAL",
                "timeline_zero_monotonic": "REAL",
                "timeline_zero_utc": "REAL",
                "monotonic_lsl_offset": "REAL",
                "clock_model_version": "TEXT",
                "timeline_end_lsl": "REAL",
                "clock_mode": "TEXT",
                "video_status": "TEXT NOT NULL DEFAULT 'missing'",
                "emotibit_status": "TEXT NOT NULL DEFAULT 'missing'",
                "sync_quality": "TEXT NOT NULL DEFAULT 'pending'",
            }
            for name, definition in session_additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
            event_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(events)").fetchall()
            }
            event_additions = {
                "observed_monotonic_seconds": "REAL",
                "received_monotonic_seconds": "REAL",
                "timeline_seconds": "REAL",
                "transport_delay_ms": "REAL",
                "time_confidence": "TEXT NOT NULL DEFAULT 'received'",
            }
            for name, definition in event_additions.items():
                if name not in event_columns:
                    connection.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")
            connection.execute(
                "UPDATE sessions SET status='interrupted', ended_at=? WHERE status='recording'",
                (utc_now(),),
            )
            connection.execute(
                """UPDATE recording_assets SET status='interrupted'
                   WHERE kind='emotibit' AND status='recording'"""
            )
            connection.execute(
                """UPDATE sessions SET emotibit_status='interrupted', sync_quality='degraded'
                   WHERE id IN (
                       SELECT session_id FROM recording_assets
                       WHERE kind='emotibit' AND status='interrupted'
                   ) AND status='interrupted'"""
            )
            connection.execute(
                """UPDATE emotibit_imports SET status='failed', completed_at=?,
                   error='Application stopped during import' WHERE status='running'""",
                (utc_now(),),
            )

    def create_session(
        self,
        *,
        project_title: str | None,
        fl_version: str | None,
        api_version: int | None,
        recorder_version: str,
        observer_version: str | None = None,
        observer_build: str | None = None,
        timeline_zero_lsl: float | None = None,
        timeline_zero_monotonic: float | None = None,
        timeline_zero_utc: float | None = None,
        monotonic_lsl_offset: float | None = None,
        clock_model_version: str | None = None,
        clock_mode: str | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        started_at = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO sessions
                   (id, started_at, status, project_title, fl_version,
                    api_version, recorder_version, observer_version, observer_build,
                    timeline_zero_lsl, timeline_zero_monotonic, timeline_zero_utc,
                    monotonic_lsl_offset, clock_model_version, clock_mode)
                   VALUES (?, ?, 'recording', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    started_at,
                    project_title,
                    fl_version,
                    api_version,
                    recorder_version,
                    observer_version,
                    observer_build,
                    timeline_zero_lsl,
                    timeline_zero_monotonic,
                    timeline_zero_utc,
                    monotonic_lsl_offset,
                    clock_model_version,
                    clock_mode,
                ),
            )
        return self.get_session(session_id)

    def finish_session(
        self,
        session_id: str,
        status: str = "completed",
        *,
        timeline_end_lsl: float | None = None,
    ) -> dict[str, Any] | None:
        """Finish an active session and record its final timeline clock value."""
        if status not in {"completed", "interrupted"}:
            raise ValueError("invalid terminal session status")
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE sessions SET status=?, ended_at=?, timeline_end_lsl=?
                   WHERE id=? AND status='recording'""",
                (status, utc_now(), timeline_end_lsl, session_id),
            )
        return self.get_session(session_id) if cursor.rowcount else None

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id) AS event_count
                   FROM sessions s WHERE s.id=?""",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return dict(row)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id) AS event_count
                   FROM sessions s ORDER BY started_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return bool(cursor.rowcount)

    def add_event(
        self,
        session_id: str,
        sequence: int,
        occurred_at: str,
        monotonic_seconds: float,
        event: ActivityEvent,
        *,
        observed_monotonic_seconds: float | None = None,
        received_monotonic_seconds: float | None = None,
        timeline_seconds: float | None = None,
        transport_delay_ms: float | None = None,
        time_confidence: str = "received",
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO events
                   (session_id, sequence, occurred_at, monotonic_seconds, category, action,
                    entity_type, entity_key, summary, confidence, payload_json,
                    observed_monotonic_seconds, received_monotonic_seconds,
                    timeline_seconds, transport_delay_ms, time_confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    sequence,
                    occurred_at,
                    monotonic_seconds,
                    event.category,
                    event.action,
                    event.entity_type,
                    event.entity_key,
                    event.summary,
                    event.confidence,
                    json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                    observed_monotonic_seconds,
                    received_monotonic_seconds,
                    timeline_seconds,
                    transport_delay_ms,
                    time_confidence,
                ),
            )
            event_id = int(cursor.lastrowid or 0)
        return self.get_event(event_id)

    def get_event(self, event_id: int) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM events WHERE id=?", (event_id,)
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return _decode_event_row(row)

    def list_events(
        self,
        session_id: str,
        *,
        category: str | None = None,
        confidence: str | None = None,
        search: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["session_id=?"]
        arguments: list[Any] = [session_id]
        if category:
            clauses.append("category=?")
            arguments.append(category)
        if confidence:
            clauses.append("confidence=?")
            arguments.append(confidence)
        if search:
            clauses.append("(summary LIKE ? OR payload_json LIKE ?)")
            pattern = f"%{search}%"
            arguments.extend([pattern, pattern])
        arguments.extend([max(1, min(limit, 5000)), max(0, offset)])
        query = (
            f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ? OFFSET ?"
        )
        with self._lock:
            rows = self._connection.execute(query, arguments).fetchall()
        return [_decode_event_row(row) for row in rows]

    def list_event_categories(self, session_id: str) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT category FROM events WHERE session_id=? ORDER BY category",
                (session_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def list_timeline_events(
        self, session_id: str, start: float, end: float, limit: int = 5000
    ) -> list[dict[str, Any]]:
        """Return events within one relative timeline window."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM events
                   WHERE session_id=? AND timeline_seconds BETWEEN ? AND ?
                   ORDER BY timeline_seconds, id LIMIT ?""",
                (session_id, start, end, max(1, min(limit, 10000))),
            ).fetchall()
        return [_decode_event_row(row) for row in rows]

    def add_diagnostic(
        self,
        *,
        session_id: str | None,
        level: str,
        code: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO diagnostics
                   (session_id, occurred_at, level, code, message, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    utc_now(),
                    level,
                    code,
                    message,
                    json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def add_sync_marker(self, session_id: str, marker: dict[str, Any]) -> None:
        """Persist a marker emitted for an active session."""
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO sync_markers
                   (session_id, kind, sequence, lsl_seconds) VALUES (?, ?, ?, ?)""",
                (
                    session_id,
                    str(marker["kind"]),
                    int(marker["sequence"]),
                    float(marker["lsl_seconds"]),
                ),
            )

    def count_sync_markers(self, session_id: str) -> int:
        """Return the number of LSL markers persisted for a session."""
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM sync_markers WHERE session_id=?", (session_id,)
            ).fetchone()
        return int(row[0])

    def add_clock_sample(
        self,
        *,
        session_id: str | None,
        source: str,
        lsl_seconds: float,
        monotonic_seconds: float | None = None,
        utc_seconds: float | None = None,
        rtt_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a clock calibration or browser synchronization sample."""
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO clock_samples
                   (session_id, source, lsl_seconds, monotonic_seconds, utc_seconds,
                    rtt_ms, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    source,
                    lsl_seconds,
                    monotonic_seconds,
                    utc_seconds,
                    rtt_ms,
                    json.dumps(metadata or {}, separators=(",", ":")),
                ),
            )

    def upsert_asset(
        self,
        session_id: str,
        kind: str,
        status: str,
        **values: Any,
    ) -> dict[str, Any]:
        """Create or update one session asset."""
        allowed = {
            "relative_path",
            "mime_type",
            "size_bytes",
            "sha256",
            "started_lsl",
            "ended_lsl",
            "start_offset_seconds",
            "duration_seconds",
            "metadata_json",
        }
        fields = {key: value for key, value in values.items() if key in allowed}
        if "metadata_json" in fields and not isinstance(fields["metadata_json"], str):
            fields["metadata_json"] = json.dumps(fields["metadata_json"], separators=(",", ":"))
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM recording_assets WHERE session_id=? AND kind=?",
                (session_id, kind),
            ).fetchone()
            if row is None:
                names = ["session_id", "kind", "status", *fields]
                placeholders = ",".join("?" for _ in names)
                connection.execute(
                    f"INSERT INTO recording_assets ({','.join(names)}) VALUES ({placeholders})",
                    [session_id, kind, status, *fields.values()],
                )
            else:
                assignments = ["status=?", *(f"{name}=?" for name in fields)]
                connection.execute(
                    f"UPDATE recording_assets SET {','.join(assignments)} WHERE id=?",
                    [status, *fields.values(), int(row["id"])],
                )
            session_field = "video_status" if kind == "video" else "emotibit_status"
            connection.execute(
                f"UPDATE sessions SET {session_field}=? WHERE id=?", (status, session_id)
            )
        return self.get_asset(session_id, kind)

    def get_asset(self, session_id: str, kind: str) -> dict[str, Any]:
        """Return one decoded session asset."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM recording_assets WHERE session_id=? AND kind=?",
                (session_id, kind),
            ).fetchone()
        if row is None:
            raise KeyError((session_id, kind))
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def list_assets(self, session_id: str) -> list[dict[str, Any]]:
        """Return all decoded assets for a session."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM recording_assets WHERE session_id=? ORDER BY kind", (session_id,)
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            results.append(item)
        return results

    def replace_signal_streams(self, session_id: str, streams: list[dict[str, Any]]) -> None:
        """Atomically replace signal metadata after a successful file import."""
        with self.transaction() as connection:
            connection.execute("DELETE FROM signal_streams WHERE session_id=?", (session_id,))
            for stream in streams:
                connection.execute(
                    """INSERT INTO signal_streams
                       (session_id, type_tag, display_name, group_name, unit, nominal_rate,
                        sample_count, start_seconds, end_seconds, relative_path, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        stream["type_tag"],
                        stream["display_name"],
                        stream["group_name"],
                        stream.get("unit"),
                        stream.get("nominal_rate"),
                        stream["sample_count"],
                        stream["start_seconds"],
                        stream["end_seconds"],
                        stream["relative_path"],
                        json.dumps(stream.get("metadata", {}), separators=(",", ":")),
                    ),
                )

    def commit_emotibit_import(
        self,
        session_id: str,
        streams: list[dict[str, Any]],
        *,
        relative_path: str,
        bundle_sha256: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically publish all EmotiBit stream and asset metadata."""
        metadata_json = json.dumps(metadata, separators=(",", ":"))
        with self.transaction() as connection:
            connection.execute("DELETE FROM signal_streams WHERE session_id=?", (session_id,))
            for stream in streams:
                connection.execute(
                    """INSERT INTO signal_streams
                       (session_id, type_tag, display_name, group_name, unit, nominal_rate,
                        sample_count, start_seconds, end_seconds, relative_path, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        stream["type_tag"],
                        stream["display_name"],
                        stream["group_name"],
                        stream.get("unit"),
                        stream.get("nominal_rate"),
                        stream["sample_count"],
                        stream["start_seconds"],
                        stream["end_seconds"],
                        stream["relative_path"],
                        json.dumps(stream.get("metadata", {}), separators=(",", ":")),
                    ),
                )
            connection.execute(
                """INSERT INTO recording_assets
                   (session_id, kind, status, relative_path, sha256, metadata_json)
                   VALUES (?, 'emotibit', 'ready', ?, ?, ?)
                   ON CONFLICT(session_id, kind) DO UPDATE SET
                       status='ready', relative_path=excluded.relative_path,
                       sha256=excluded.sha256, metadata_json=excluded.metadata_json""",
                (session_id, relative_path, bundle_sha256, metadata_json),
            )
            connection.execute(
                """UPDATE sessions SET emotibit_status='ready',
                   sync_quality='aligned_unverified' WHERE id=?""",
                (session_id,),
            )
        return self.get_asset(session_id, "emotibit")

    def list_signal_streams(self, session_id: str) -> list[dict[str, Any]]:
        """Return metadata for all physiological signal tracks."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM signal_streams WHERE session_id=? ORDER BY group_name, type_tag",
                (session_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            results.append(item)
        return results

    def ensure_live_signal_stream(self, session_id: str, stream: dict[str, Any]) -> None:
        """Create metadata for a live LSL track before its first chunk is committed."""
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO signal_streams
                   (session_id, type_tag, display_name, group_name, unit, nominal_rate,
                    sample_count, start_seconds, end_seconds, relative_path, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
                   ON CONFLICT(session_id, type_tag) DO NOTHING""",
                (
                    session_id,
                    stream["type_tag"],
                    stream["display_name"],
                    stream["group_name"],
                    stream.get("unit"),
                    stream.get("nominal_rate"),
                    stream["relative_path"],
                    json.dumps(stream.get("metadata", {}), separators=(",", ":")),
                ),
            )

    def add_signal_chunk(self, session_id: str, chunk: dict[str, Any]) -> None:
        """Publish one immutable signal chunk and update its track aggregate atomically."""
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO signal_chunks
                   (session_id, type_tag, sequence, start_seconds, end_seconds,
                    sample_count, min_value, max_value, relative_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    chunk["type_tag"],
                    chunk["sequence"],
                    chunk["start_seconds"],
                    chunk["end_seconds"],
                    chunk["sample_count"],
                    chunk["min_value"],
                    chunk["max_value"],
                    chunk["relative_path"],
                ),
            )
            connection.execute(
                """UPDATE signal_streams SET
                       sample_count=sample_count + ?,
                       start_seconds=CASE WHEN sample_count=0 THEN ?
                                          ELSE MIN(start_seconds, ?) END,
                       end_seconds=CASE WHEN sample_count=0 THEN ?
                                        ELSE MAX(end_seconds, ?) END
                   WHERE session_id=? AND type_tag=?""",
                (
                    chunk["sample_count"],
                    chunk["start_seconds"],
                    chunk["start_seconds"],
                    chunk["end_seconds"],
                    chunk["end_seconds"],
                    session_id,
                    chunk["type_tag"],
                ),
            )

    def list_signal_chunks(
        self, session_id: str, type_tag: str, start: float, end: float
    ) -> list[dict[str, Any]]:
        """Return immutable chunks intersecting a relative timeline window."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM signal_chunks
                   WHERE session_id=? AND type_tag=?
                     AND end_seconds>=? AND start_seconds<=?
                   ORDER BY sequence""",
                (session_id, type_tag, start, end),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_sync_quality(self, session_id: str, quality: str) -> None:
        """Set the current evidence-backed synchronization quality label."""
        with self.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET sync_quality=? WHERE id=?", (quality, session_id)
            )

    def create_import_job(
        self, job_id: str, session_id: str, *, replace_existing: bool
    ) -> dict[str, Any]:
        """Persist a new EmotiBit import task."""
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO emotibit_imports
                   (id, session_id, status, replace_existing, created_at)
                   VALUES (?, ?, 'running', ?, ?)""",
                (job_id, session_id, int(replace_existing), utc_now()),
            )
        return self.get_import_job(job_id)

    def finish_import_job(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Finish a persisted EmotiBit import task."""
        if status not in {"completed", "failed"}:
            raise ValueError("Invalid import job status")
        with self.transaction() as connection:
            connection.execute(
                """UPDATE emotibit_imports SET status=?, completed_at=?, result_json=?, error=?
                   WHERE id=?""",
                (
                    status,
                    utc_now(),
                    json.dumps(result, separators=(",", ":")) if result is not None else None,
                    error,
                    job_id,
                ),
            )
        return self.get_import_job(job_id)

    def get_import_job(self, job_id: str) -> dict[str, Any]:
        """Return a persisted EmotiBit import task."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM emotibit_imports WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["replace_existing"] = bool(result["replace_existing"])
        result["result"] = (
            json.loads(result.pop("result_json")) if result["result_json"] is not None else None
        )
        return result

    def has_running_import(self, session_id: str) -> bool:
        """Return whether a session currently owns an import task."""
        with self._lock:
            row = self._connection.execute(
                """SELECT 1 FROM emotibit_imports
                   WHERE session_id=? AND status='running' LIMIT 1""",
                (session_id,),
            ).fetchone()
        return row is not None


def _decode_event_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result
