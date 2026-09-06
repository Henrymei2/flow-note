from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast


class LslApi(Protocol):
    """Describe the subset of pylsl used by the application."""

    def local_clock(self) -> float: ...


@dataclass(frozen=True, slots=True)
class ClockReading:
    """Represent adjacent readings from the Python and LSL clocks."""

    monotonic_seconds: float
    lsl_seconds: float
    utc_seconds: float


class TimelineClock:
    """Bridge Python's process clock to the LSL steady-clock domain."""

    def __init__(self, lsl_api: LslApi | None = None) -> None:
        self._lsl = lsl_api if lsl_api is not None else _load_pylsl()
        self._offset = self._measure_offset()

    @property
    def available(self) -> bool:
        """Return whether a real pylsl clock is available."""
        return self._lsl is not None

    @property
    def mode(self) -> str:
        """Return the active clock mode."""
        return "lsl" if self.available else "monotonic_fallback"

    def reading(self) -> ClockReading:
        """Capture a clock reading suitable for browser synchronization."""
        before = time.monotonic()
        lsl = self.now()
        after = time.monotonic()
        return ClockReading((before + after) / 2, lsl, time.time())

    def now(self) -> float:
        """Return the current time in the timeline clock domain."""
        if self._lsl is not None:
            return float(self._lsl.local_clock())
        return time.monotonic()

    def monotonic_to_lsl(self, value: float) -> float:
        """Map a Python monotonic timestamp into the timeline domain."""
        return value + self._offset

    def _measure_offset(self) -> float:
        """Select the lowest-latency adjacent monotonic-to-LSL offset sample."""
        if self._lsl is None:
            return 0.0
        samples: list[tuple[float, float]] = []
        for _ in range(9):
            before = time.monotonic()
            lsl = float(self._lsl.local_clock())
            after = time.monotonic()
            samples.append((after - before, lsl - ((before + after) / 2)))
        return min(samples, key=lambda item: item[0])[1]


class MarkerOutlet:
    """Publish periodic session markers for EmotiBit Oscilloscope."""

    def __init__(
        self,
        clock: TimelineClock,
        interval_seconds: float = 5.0,
        on_marker: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.clock = clock
        self.interval_seconds = interval_seconds
        self._outlet: Any | None = None
        self._session_id: str | None = None
        self._sequence = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_marker = on_marker
        if clock.available:
            try:
                from pylsl import StreamInfo, StreamOutlet  # type: ignore[import-untyped]

                info = StreamInfo("DataSyncMarker", "Markers", 1, 0, "string", "12345")
                self._outlet = StreamOutlet(info)
            except (ImportError, OSError, RuntimeError):
                self._outlet = None

    @property
    def available(self) -> bool:
        """Return whether marker publishing is operational."""
        return self._outlet is not None

    def start(self, session_id: str) -> list[dict[str, Any]]:
        """Begin publishing markers for one session."""
        self.stop()
        self._session_id = session_id
        self._sequence = 0
        self._stop.clear()
        first = self._push("start")
        self._thread = threading.Thread(target=self._run, name="lsl-markers", daemon=True)
        self._thread.start()
        return [first]

    def stop(self) -> list[dict[str, Any]]:
        """Stop publishing and return the final marker, if any."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._session_id is None:
            return []
        final = self._push("stop")
        self._session_id = None
        return [final]

    def _run(self) -> None:
        """Publish periodic sync markers until the active session stops."""
        while not self._stop.wait(self.interval_seconds):
            self._push("sync")

    def _push(self, kind: str) -> dict[str, Any]:
        """Publish and persist one timestamped marker payload."""
        timestamp = self.clock.now()
        marker = {
            "kind": kind,
            "session_id": self._session_id,
            "sequence": self._sequence,
            "lsl_seconds": timestamp,
        }
        self._sequence += 1
        if self._outlet is not None:
            self._outlet.push_sample([json.dumps(marker, separators=(",", ":"))], timestamp)
        if self._on_marker is not None and self._session_id is not None:
            self._on_marker(marker)
        return marker


def _load_pylsl() -> LslApi | None:
    """Load pylsl when its Python module and native library are usable."""
    try:
        import pylsl

        return cast(LslApi, pylsl)
    except (ImportError, OSError):
        return None
