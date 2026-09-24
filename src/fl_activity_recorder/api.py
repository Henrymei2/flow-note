from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import __version__
from .assets import CaptureManager
from .biosignal_clustering import compute_biosignal_clusters
from .config import Settings
from .database import Database
from .live_signals import EmotiBitLslReceiver, LiveSignalStore
from .midi_receiver import MidiReceiver
from .r2_storage import R2Storage, R2StorageError
from .recorder import Recorder
from .research_archive import ResearchArchiveError, ResearchArchiveManager

PACKAGE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_environment()
    database = Database(resolved.database_path)
    recorder = Recorder(database)
    data_directory = resolved.data_directory or resolved.database_path.parent / "sessions"
    captures = CaptureManager(database, data_directory, recorder.clock)
    signals = LiveSignalStore(database, data_directory)
    research_archives = ResearchArchiveManager(database, data_directory)
    r2 = R2Storage(resolved)
    emotibit = EmotiBitLslReceiver(
        signals,
        recorder.publish,
        stream_prefix=resolved.emotibit_lsl_prefix,
    )
    recorder.set_session_stop_hook(
        lambda _session_id, ended_lsl, interrupted: emotibit.finish_session(
            ended_lsl, interrupted=interrupted
        )
    )
    receiver = (
        MidiReceiver(recorder, database, resolved.midi_event_port) if resolved.enable_midi else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        recorder.bind_loop(asyncio.get_running_loop())
        emotibit.start()
        if receiver is not None:
            receiver.start()
        monitor = asyncio.create_task(_heartbeat_monitor(recorder))
        try:
            yield
        finally:
            monitor.cancel()
            if receiver is not None:
                receiver.stop()
            if recorder.active_session_id is not None:
                recorder.stop_session(recorder.active_session_id, interrupted=True)
            emotibit.stop()
            database.close()

    app = FastAPI(title="FL Studio Activity Recorder", version=__version__, lifespan=lifespan)
    app.state.database = database
    app.state.recorder = recorder
    app.state.settings = resolved
    app.state.captures = captures
    app.state.signals = signals
    app.state.emotibit = emotibit
    app.state.research_archives = research_archives
    app.state.r2 = r2
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"sessions": database.list_sessions()},
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return _combined_health(recorder, emotibit)

    @app.get("/api/clock")
    async def clock() -> dict[str, Any]:
        """Return adjacent server clocks for browser RTT calibration."""
        reading = recorder.clock.reading()
        return {
            "lsl_seconds": reading.lsl_seconds,
            "monotonic_seconds": reading.monotonic_seconds,
            "utc_seconds": reading.utc_seconds,
            "mode": recorder.clock.mode,
        }

    @app.get("/api/sharing")
    async def sharing_status() -> dict[str, Any]:
        """Return non-secret Cloudflare R2 configuration state."""
        return r2.public_status()

    @app.get("/api/cloud-sessions")
    async def list_cloud_sessions() -> list[dict[str, Any]]:
        """List downloadable research sessions published in R2."""
        try:
            return await asyncio.to_thread(r2.list_sessions)
        except R2StorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/cloud-sessions/download")
    async def download_cloud_session(key: str = Query(min_length=1)) -> RedirectResponse:
        """Redirect to a short-lived signed URL for one cataloged R2 archive."""
        try:
            url = r2.create_download_url(key)
        except R2StorageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RedirectResponse(url=url, status_code=307)

    @app.post("/api/bookmarks", status_code=201)
    async def create_bookmark(request: Request) -> dict[str, Any]:
        payload = await request.json()
        session_id = str(payload.get("session_id") or "")
        try:
            session = database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        if session["status"] == "recording":
            raise HTTPException(status_code=409, detail="Stop recording before adding a bookmark")
        try:
            values = _validated_bookmark_payload(payload, session)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return database.create_bookmark(session_id=session_id, **values)

    @app.get("/api/bookmarks")
    async def list_bookmarks(
        search: str | None = None,
        label: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if session_id:
            try:
                database.get_session(session_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Session not found") from exc
        return database.list_bookmarks(search=search, label=label, session_id=session_id)

    @app.get("/api/bookmark-labels")
    async def list_bookmark_labels() -> list[dict[str, Any]]:
        return database.list_bookmark_labels()

    @app.get("/api/bookmarks/{bookmark_id}")
    async def get_bookmark(bookmark_id: str) -> dict[str, Any]:
        try:
            return database.get_bookmark(bookmark_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Bookmark not found") from exc

    @app.patch("/api/bookmarks/{bookmark_id}")
    async def update_bookmark(bookmark_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            note = _required_note(payload.get("note"))
            labels = _normalized_labels(payload.get("labels"))
            return database.update_bookmark(bookmark_id, note=note, labels=labels)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Bookmark not found") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/bookmarks/{bookmark_id}", status_code=204)
    async def delete_bookmark(bookmark_id: str) -> Response:
        if not database.delete_bookmark(bookmark_id):
            raise HTTPException(status_code=404, detail="Bookmark not found")
        return Response(status_code=204)

    @app.post("/api/sessions", status_code=201)
    async def start_session() -> dict[str, Any]:
        if not recorder.clock.available or not emotibit.ready:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "emotibit_lsl_unavailable",
                    "message": "A recent numeric EmotiBit LSL sample is required",
                },
            )
        try:
            session = recorder.start_session()
            emotibit.begin_session(str(session["id"]), float(session["timeline_zero_lsl"]))
            return session
        except RuntimeError as exc:
            if recorder.active_session_id is not None:
                recorder.stop_session(recorder.active_session_id, interrupted=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict[str, Any]:
        try:
            return recorder.stop_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Active session not found") from exc

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return database.list_sessions()

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        title = str(payload.get("project_title") or "").strip() or None
        try:
            return database.rename_session(session_id, title)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        """Return one session with its assets and signal catalog."""
        try:
            session = database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        session["assets"] = database.list_assets(session_id)
        session["signals"] = database.list_signal_streams(session_id)
        return session

    @app.get("/api/sessions/{session_id}/events")
    async def list_events(
        session_id: str,
        category: str | None = None,
        confidence: str | None = None,
        search: str | None = None,
        limit: int = Query(default=500, ge=1, le=5000),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        try:
            database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return database.list_events(
            session_id,
            category=category,
            confidence=confidence,
            search=search,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/sessions/{session_id}/categories")
    async def list_event_categories(session_id: str) -> list[str]:
        try:
            database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return database.list_event_categories(session_id)

    @app.get("/api/sessions/{session_id}/timeline")
    async def timeline(
        session_id: str,
        start: float = 0,
        end: float = Query(default=600, gt=0),
        pixels: int = Query(default=1000, ge=50, le=5000),
        tracks: str = "",
    ) -> dict[str, Any]:
        """Return synchronized event and decimated signal windows."""
        try:
            session = database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        selected = [item for item in tracks.split(",") if item]
        signals = []
        for tag in selected:
            try:
                signals.append(app.state.signals.query(session_id, tag, start, end, pixels))
            except KeyError:
                continue
        return {
            "session_id": session_id,
            "start": start,
            "end": end,
            "events": database.list_timeline_events(session_id, start, end),
            "signals": signals,
            "duration_seconds": _session_duration(session),
        }

    @app.get("/api/sessions/{session_id}/biosignal-clusters")
    async def biosignal_clusters(
        session_id: str,
        window_seconds: float | None = Query(default=None, ge=5.0, le=120.0),
        clusters: int = Query(default=3, ge=2, le=6),
    ) -> dict[str, Any]:
        """Return ranked, non-overlapping physiological pattern suggestions.

        The scorer uses fixed per-modality windows. `window_seconds` remains a
        compatibility parameter, while `clusters` can reduce the default top
        three suggestions.
        """
        try:
            session = database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return await asyncio.to_thread(
            compute_biosignal_clusters,
            app.state.signals,
            session_id,
            _session_duration(session),
            window_seconds=window_seconds,
            n_clusters=clusters,
        )

    @app.post("/api/sessions/{session_id}/capture/start")
    async def start_capture(session_id: str, request: Request) -> dict[str, Any]:
        """Start an ordered browser capture upload."""
        try:
            session = database.get_session(session_id)
            if session["status"] != "recording":
                raise HTTPException(status_code=409, detail="Session is not recording")
            payload = await request.json()
            started_lsl = float(payload["started_lsl"])
            if abs(recorder.clock.now() - started_lsl) > 10:
                raise ValueError("Browser clock estimate is outside the allowed range")
            metadata = dict(payload.get("metadata") or {})
            sync = dict(payload.get("clock_sync") or {})
            database.add_clock_sample(
                session_id=session_id,
                source="browser_capture",
                lsl_seconds=started_lsl,
                rtt_ms=_optional_float(sync.get("rtt_ms")),
                metadata=sync,
            )
            if not metadata.get("has_audio", False):
                database.add_diagnostic(
                    session_id=session_id,
                    level="warning",
                    code="audio_unavailable",
                    message="Chrome did not provide an audio track; video is silent",
                )
            else:
                audio_settings = metadata.get("audio_settings")
                channel_count = (
                    audio_settings.get("channelCount")
                    if isinstance(audio_settings, dict)
                    else None
                )
                if channel_count != 2:
                    verified_count = (
                        channel_count if isinstance(channel_count, (int, float)) else None
                    )
                    database.add_diagnostic(
                        session_id=session_id,
                        level="warning",
                        code=(
                            "audio_not_stereo"
                            if verified_count is not None
                            else "audio_stereo_unverified"
                        ),
                        message=(
                            f"Chrome provided {verified_count:g}-channel audio; "
                            "stereo is recommended"
                            if verified_count is not None
                            else (
                                "Chrome did not report the audio channel count; "
                                "stereo is unverified"
                            )
                        ),
                        payload={"channel_count": verified_count},
                    )
            return captures.start(
                session_id,
                mime_type=str(payload.get("mime_type") or "video/webm"),
                started_lsl=started_lsl,
                metadata=metadata,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/capture/chunks/{sequence}")
    async def append_capture_chunk(
        session_id: str, sequence: int, request: Request
    ) -> dict[str, int]:
        """Append the next numbered browser media chunk."""
        try:
            data = await request.body()
            return {"bytes": captures.append(session_id, sequence, data)}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/capture/complete")
    async def complete_capture(session_id: str, request: Request) -> dict[str, Any]:
        """Finalize a complete browser video asset."""
        payload = await request.json()
        try:
            return captures.complete(
                session_id,
                ended_lsl=float(payload["ended_lsl"]),
                duration_seconds=float(payload.get("duration_seconds") or 0),
                metadata=dict(payload.get("metadata") or {}),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/capture/abort")
    async def abort_capture(session_id: str, request: Request) -> dict[str, Any]:
        """Retain and mark an interrupted browser video asset."""
        payload = await request.json()
        return captures.abort(session_id, str(payload.get("reason") or "browser capture aborted"))

    @app.get("/api/sessions/{session_id}/video")
    async def video(session_id: str) -> FileResponse:
        """Serve a completed video with framework-provided Range support."""
        try:
            path = captures.video_path(session_id)
            asset = database.get_asset(session_id, "video")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Video is not ready") from exc
        return FileResponse(path, media_type=asset["mime_type"] or "video/webm")

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str) -> StreamingResponse:
        try:
            session = database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

        def lines() -> Any:
            yield json.dumps({"type": "session", "data": session}, ensure_ascii=False) + "\n"
            yield (
                json.dumps(
                    {
                        "type": "assets",
                        "data": {
                            "assets": database.list_assets(session_id),
                            "signals": database.list_signal_streams(session_id),
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            offset = 0
            while True:
                events = database.list_events(session_id, limit=1000, offset=offset)
                if not events:
                    break
                for event in reversed(events):
                    yield json.dumps({"type": "event", "data": event}, ensure_ascii=False) + "\n"
                offset += len(events)

        headers = {"Content-Disposition": f'attachment; filename="session-{session_id}.jsonl"'}
        return StreamingResponse(lines(), media_type="application/x-ndjson", headers=headers)

    @app.get("/api/sessions/{session_id}/share")
    async def share_session(session_id: str) -> FileResponse:
        """Download the available data for one finished session as a portable archive."""
        try:
            path = await asyncio.to_thread(research_archives.export, session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except ResearchArchiveError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"fl-studio-research-session-{session_id}.zip",
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/sessions/{session_id}/cloud-share", status_code=201)
    async def cloud_share_session(session_id: str) -> dict[str, Any]:
        """Upload a finished session's available data and return a temporary private link."""
        path: Path | None = None
        try:
            session = database.get_session(session_id)
            path = await asyncio.to_thread(research_archives.export, session_id)
            return await asyncio.to_thread(r2.upload_archive, session_id, path, session)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except ResearchArchiveError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except R2StorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    @app.post("/api/research-archives", status_code=201)
    async def import_research_archive(request: Request) -> dict[str, Any]:
        """Verify and import a portable research archive uploaded as a ZIP body."""
        handle, temporary_name = tempfile.mkstemp(
            prefix="research-upload-", suffix=".zip", dir=data_directory
        )
        os.close(handle)
        temporary = Path(temporary_name)
        size = 0
        try:
            with temporary.open("wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 100 * 1024**3:
                        raise HTTPException(status_code=413, detail="Archive is too large")
                    output.write(chunk)
            if size == 0:
                raise HTTPException(status_code=422, detail="Choose a research archive ZIP")
            return await asyncio.to_thread(research_archives.import_archive, temporary)
        except ResearchArchiveError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        if recorder.active_session_id == session_id:
            raise HTTPException(status_code=409, detail="Stop the recording before deleting it")
        try:
            database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        captures.delete_session_files(session_id)
        if not database.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return Response(status_code=204)

    @app.get("/api/live")
    async def live(request: Request) -> StreamingResponse:
        queue = recorder.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                initial = {"type": "health", "health": _combined_health(recorder, emotibit)}
                yield f"data: {json.dumps(initial)}\n\n"
                while not await request.is_disconnected():
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                recorder.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def _session_duration(session: dict[str, Any]) -> float:
    zero = _optional_float(session.get("timeline_zero_lsl"))
    end = _optional_float(session.get("timeline_end_lsl"))
    return max(0.0, end - zero) if zero is not None and end is not None else 0.0


def _combined_health(recorder: Recorder, emotibit: EmotiBitLslReceiver) -> dict[str, Any]:
    """Combine FL, clock, and EmotiBit readiness in one dashboard health response."""
    return {**recorder.health(), "emotibit": emotibit.health()}


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _validated_bookmark_payload(
    payload: dict[str, Any], session: dict[str, Any]
) -> dict[str, Any]:
    try:
        start = float(payload["start_seconds"])
        end = float(payload["end_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Bookmark start and end times are required") from exc
    duration = _session_duration(session)
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("Bookmark times must be finite")
    if start < 0 or end <= start or end > duration + 1e-6:
        raise ValueError("Bookmark interval must be within the completed session")
    source_type = str(payload.get("source_type") or "manual")
    if source_type not in {"manual", "pattern"}:
        raise ValueError("Bookmark source must be manual or pattern")
    pattern_context = payload.get("pattern_context")
    if source_type == "pattern":
        if not isinstance(pattern_context, dict):
            raise ValueError("Pattern bookmarks require suggestion context")
        required = {"label", "cluster_id", "window_seconds", "algorithm_version"}
        if not required.issubset(pattern_context):
            raise ValueError("Pattern suggestion context is incomplete")
    elif pattern_context is not None:
        raise ValueError("Manual bookmarks cannot include pattern context")
    return {
        "start_seconds": start,
        "end_seconds": end,
        "note": _required_note(payload.get("note")),
        "source_type": source_type,
        "labels": _normalized_labels(payload.get("labels")),
        "pattern_context": pattern_context,
    }


def _required_note(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A written note is required")
    return value.strip()


def _normalized_labels(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError("Labels must be a list of text values")
    labels: list[str] = []
    seen: set[str] = set()
    for raw in value:
        label = " ".join(raw.split())
        normalized = label.casefold()
        if label and normalized not in seen:
            labels.append(label)
            seen.add(normalized)
    return labels


async def _heartbeat_monitor(recorder: Recorder) -> None:
    while True:
        await asyncio.sleep(1.0)
        recorder.check_heartbeat()
