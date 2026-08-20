from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from . import __version__
from .database import Database
from .models import ActivityEvent
from .observer_contract import OBSERVER_BUILD, OBSERVER_SCHEMA, OBSERVER_VERSION
from .protocol import DecodedMessage, MessageKind

MINIMUM_API_VERSION = 42


class Recorder:
    def __init__(self, database: Database, *, heartbeat_timeout: float = 5.0) -> None:
        self.database = database
        self.heartbeat_timeout = heartbeat_timeout
        self.active_session_id: str | None = None
        self.last_heartbeat: float | None = None
        self.last_sequence: int | None = None
        self.hello: dict[str, Any] = {}
        self.latest_snapshot: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def connected(self) -> bool:
        return self.last_heartbeat is not None and (
            time.monotonic() - self.last_heartbeat <= self.heartbeat_timeout
        )

    @property
    def compatible(self) -> bool:
        return self.compatibility_reason is None

    @property
    def compatibility_reason(self) -> str | None:
        api_version = self.hello.get("api_version")
        if not isinstance(api_version, int) or api_version < MINIMUM_API_VERSION:
            return "api_unsupported"
        if self.hello.get("observer_schema") != OBSERVER_SCHEMA:
            return "observer_schema_mismatch"
        if self.hello.get("observer_build") != OBSERVER_BUILD:
            return "observer_outdated"
        return None

    def health(self) -> dict[str, Any]:
        age = None
        if self.last_heartbeat is not None:
            age = max(0.0, time.monotonic() - self.last_heartbeat)
        return {
            "connected": self.connected,
            "compatible": self.compatible,
            "compatibility_reason": self.compatibility_reason,
            "minimum_api_version": MINIMUM_API_VERSION,
            "required_observer": {
                "version": OBSERVER_VERSION,
                "schema": OBSERVER_SCHEMA,
                "build": OBSERVER_BUILD,
            },
            "heartbeat_age_seconds": age,
            "active_session_id": self.active_session_id,
            "observer": dict(self.hello),
        }

    def start_session(self) -> dict[str, Any]:
        with self._lock:
            if self.active_session_id is not None:
                return self.database.get_session(self.active_session_id)
            if not self.connected:
                raise RuntimeError("FL Studio observer is not connected")
            if not self.compatible:
                reason = self.compatibility_reason
                if reason == "api_unsupported":
                    raise RuntimeError(
                        f"FL Studio MIDI scripting API {MINIMUM_API_VERSION}+ is required"
                    )
                raise RuntimeError(
                    "The FL Studio observer is out of date. Reinstall it and reload the "
                    f"script (required build: {OBSERVER_BUILD})."
                )
            session = self.database.create_session(
                project_title=_string_or_none(
                    self.latest_snapshot.get("project_title") or self.hello.get("project_title")
                ),
                fl_version=_string_or_none(self.hello.get("fl_version")),
                api_version=_int_or_none(self.hello.get("api_version")),
                recorder_version=__version__,
                observer_version=_string_or_none(self.hello.get("observer_version")),
                observer_build=_string_or_none(self.hello.get("observer_build")),
            )
            self.active_session_id = str(session["id"])
            initial = ActivityEvent(
                category="project",
                action="initial_snapshot",
                summary="Recording started with the current FL Studio state",
                confidence="state_diff",
                entity_type="project",
                payload=dict(self.latest_snapshot),
            )
            stored = self.database.add_event(
                self.active_session_id,
                self.last_sequence or 0,
                _utc_now(),
                time.monotonic(),
                initial,
            )
            self._publish({"type": "session_started", "session": session, "event": stored})
            return session

    def stop_session(self, session_id: str, *, interrupted: bool = False) -> dict[str, Any]:
        with self._lock:
            if self.active_session_id != session_id:
                raise KeyError(session_id)
            result = self.database.finish_session(
                session_id, "interrupted" if interrupted else "completed"
            )
            self.active_session_id = None
        if result is None:
            raise KeyError(session_id)
        self._publish({"type": "session_stopped", "session": result})
        return result

    def ingest(self, message: DecodedMessage) -> None:
        now = time.monotonic()
        with self._lock:
            if message.kind is MessageKind.HELLO:
                restarted = self.last_sequence is not None
                if restarted and self.active_session_id is not None:
                    restarted_session_id = self.active_session_id
                    self.database.add_diagnostic(
                        session_id=restarted_session_id,
                        level="warning",
                        code="observer_restarted",
                        message="The FL Studio observer restarted during recording",
                        payload={
                            "previous_sequence": self.last_sequence,
                            "new_sequence": message.sequence,
                            "previous_observer": dict(self.hello),
                            "new_observer": dict(message.payload),
                        },
                    )
                    self.stop_session(restarted_session_id, interrupted=True)
                self.last_sequence = message.sequence
                self.hello = dict(message.payload)
                self.last_heartbeat = now
                self.latest_snapshot = {}
                self._publish({"type": "health", "health": self.health()})
                return
            if self.last_sequence is not None:
                if message.sequence > self.last_sequence + 1:
                    missing = message.sequence - self.last_sequence - 1
                    self.database.add_diagnostic(
                        session_id=self.active_session_id,
                        level="warning",
                        code="sequence_gap",
                        message=f"{missing} observer message(s) were not received",
                        payload={"after": self.last_sequence, "before": message.sequence},
                    )
                if message.sequence <= self.last_sequence:
                    self.database.add_diagnostic(
                        session_id=self.active_session_id,
                        level="info",
                        code="duplicate_or_out_of_order",
                        message="An old observer message was ignored",
                        payload={"sequence": message.sequence, "last": self.last_sequence},
                    )
                    return
            self.last_sequence = message.sequence
            if message.kind is MessageKind.HEARTBEAT:
                self.last_heartbeat = now
                for key in ("project_title", "fl_version", "api_version"):
                    if key in message.payload:
                        self.hello[key] = message.payload[key]
                self._publish({"type": "health", "health": self.health()})
                return
            if message.kind is MessageKind.SNAPSHOT:
                self.latest_snapshot = dict(message.payload)
            if message.kind is MessageKind.DIAGNOSTIC:
                self.database.add_diagnostic(
                    session_id=self.active_session_id,
                    level=str(message.payload.get("level", "warning")),
                    code=str(message.payload.get("code", "observer")),
                    message=str(message.payload.get("message", "Observer diagnostic")),
                    payload=dict(message.payload),
                )
            session_id = self.active_session_id
            if session_id is None or message.kind not in {MessageKind.EVENT, MessageKind.SNAPSHOT}:
                return
            payload = dict(message.payload)
            if message.kind is MessageKind.SNAPSHOT:
                payload = {
                    "category": "project",
                    "action": "snapshot",
                    "summary": "FL Studio state snapshot",
                    "confidence": "state_diff",
                    "entity_type": "project",
                    "payload": payload,
                }
            event = ActivityEvent.from_payload(payload)
            stored = self.database.add_event(session_id, message.sequence, _utc_now(), now, event)
            self._publish({"type": "event", "event": stored})

    def check_heartbeat(self) -> None:
        if self.active_session_id is not None and not self.connected:
            session_id = self.active_session_id
            self.database.add_diagnostic(
                session_id=session_id,
                level="warning",
                code="heartbeat_timeout",
                message="FL Studio observer connection was lost",
            )
            self.stop_session(session_id, interrupted=True)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _publish(self, item: dict[str, Any]) -> None:
        if self._loop is None:
            return

        def deliver() -> None:
            for queue in tuple(self._subscribers):
                if queue.full():
                    with suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                queue.put_nowait(item)

        self._loop.call_soon_threadsafe(deliver)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None
