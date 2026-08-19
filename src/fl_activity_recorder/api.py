from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .database import Database
from .midi_receiver import MidiReceiver
from .recorder import Recorder

PACKAGE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_environment()
    database = Database(resolved.database_path)
    recorder = Recorder(database)
    receiver = (
        MidiReceiver(recorder, database, resolved.midi_event_port) if resolved.enable_midi else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        recorder.bind_loop(asyncio.get_running_loop())
        if receiver is not None:
            receiver.start()
        monitor = asyncio.create_task(_heartbeat_monitor(recorder))
        try:
            yield
        finally:
            monitor.cancel()
            if receiver is not None:
                receiver.stop()
            database.close()

    app = FastAPI(title="FL Studio Activity Recorder", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.recorder = recorder
    app.state.settings = resolved
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
        return recorder.health()

    @app.post("/api/sessions", status_code=201)
    async def start_session() -> dict[str, Any]:
        try:
            return recorder.start_session()
        except RuntimeError as exc:
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

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str) -> StreamingResponse:
        try:
            session = database.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

        def lines() -> Any:
            yield json.dumps({"type": "session", "data": session}, ensure_ascii=False) + "\n"
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

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        if recorder.active_session_id == session_id:
            raise HTTPException(status_code=409, detail="Stop the recording before deleting it")
        if not database.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return Response(status_code=204)

    @app.get("/api/live")
    async def live(request: Request) -> StreamingResponse:
        queue = recorder.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                yield f"data: {json.dumps({'type': 'health', 'health': recorder.health()})}\n\n"
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


async def _heartbeat_monitor(recorder: Recorder) -> None:
    while True:
        await asyncio.sleep(1.0)
        recorder.check_heartbeat()
