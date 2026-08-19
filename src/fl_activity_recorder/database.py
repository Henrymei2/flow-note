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
                    recorder_version TEXT NOT NULL
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
                    payload_json TEXT NOT NULL
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
                """
            )
            connection.execute(
                "UPDATE sessions SET status='interrupted', ended_at=? WHERE status='recording'",
                (utc_now(),),
            )

    def create_session(
        self,
        *,
        project_title: str | None,
        fl_version: str | None,
        api_version: int | None,
        recorder_version: str,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        started_at = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO sessions
                   (id, started_at, status, project_title, fl_version,
                    api_version, recorder_version)
                   VALUES (?, ?, 'recording', ?, ?, ?, ?)""",
                (
                    session_id,
                    started_at,
                    project_title,
                    fl_version,
                    api_version,
                    recorder_version,
                ),
            )
        return self.get_session(session_id)

    def finish_session(self, session_id: str, status: str = "completed") -> dict[str, Any] | None:
        if status not in {"completed", "interrupted"}:
            raise ValueError("invalid terminal session status")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET status=?, ended_at=? WHERE id=? AND status='recording'",
                (status, utc_now(), session_id),
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
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO events
                   (session_id, sequence, occurred_at, monotonic_seconds, category, action,
                    entity_type, entity_key, summary, confidence, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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


def _decode_event_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result
