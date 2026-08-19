from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .database import Database
from .protocol import MessageDecoder, ProtocolError
from .recorder import Recorder


class MidiReceiver:
    def __init__(
        self,
        recorder: Recorder,
        database: Database,
        port_name: str,
        *,
        retry_seconds: float = 2.0,
        mido_module: Any | None = None,
    ) -> None:
        self.recorder = recorder
        self.database = database
        self.port_name = port_name
        self.retry_seconds = retry_seconds
        self._mido = mido_module
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._decoder = MessageDecoder()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="midi-receiver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        mido = self._mido
        if mido is None:
            import mido as imported_mido  # type: ignore[import-untyped]

            mido = imported_mido
        while not self._stop.is_set():
            try:
                available = list(mido.get_input_names())
                selected = _find_port(available, self.port_name)
                if selected is None:
                    self._wait()
                    continue
                with mido.open_input(selected, callback=self._callback()) as midi_input:
                    while not self._stop.wait(0.5):
                        self.recorder.check_heartbeat()
                    midi_input.reset()
            except Exception as exc:  # hardware/backends fail outside our control
                self.database.add_diagnostic(
                    session_id=self.recorder.active_session_id,
                    level="warning",
                    code="midi_receiver_error",
                    message=str(exc),
                )
                self._wait()

    def _callback(self) -> Callable[[Any], None]:
        def receive(message: Any) -> None:
            if getattr(message, "type", None) != "sysex":
                return
            frame = bytes([0xF0, *message.data, 0xF7])
            try:
                decoded = self._decoder.feed(frame)
                if decoded is not None:
                    self.recorder.ingest(decoded)
            except ProtocolError as exc:
                self.database.add_diagnostic(
                    session_id=self.recorder.active_session_id,
                    level="warning",
                    code="malformed_frame",
                    message=str(exc),
                )

        return receive

    def _wait(self) -> None:
        self._stop.wait(self.retry_seconds)


def _find_port(available: list[str], expected: str) -> str | None:
    expected_lower = expected.casefold()
    for port in available:
        if port.casefold() == expected_lower:
            return port
    for port in available:
        if expected_lower in port.casefold():
            return port
    return None
