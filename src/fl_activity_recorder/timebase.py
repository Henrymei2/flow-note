from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, cast


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


def _load_pylsl() -> LslApi | None:
    """Load pylsl when its Python module and native library are usable."""
    try:
        import pylsl  # type: ignore[import-untyped]

        return cast(LslApi, pylsl)
    except (ImportError, OSError):
        return None
