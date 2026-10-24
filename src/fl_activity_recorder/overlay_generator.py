"""Bounded-memory physiological controls and reproducible ambient synthesis."""

from __future__ import annotations

import math
import threading
import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .live_signals import LiveSignalStore

SAMPLE_RATE = 48000
GENERATOR_VERSION = "ambient-v1"
CONFIG = {"sample_rate": SAMPLE_RATE, "control_hz": 10, "peak": 0.16, "fade_seconds": 0.5}
FloatArray = NDArray[np.float64]


def _series(
    signals: LiveSignalStore, session_id: str, tag: str, start: float, end: float
) -> FloatArray:
    """Load valid raw samples and collapse duplicate timestamps deterministically."""
    chunks = list(signals.iter_raw(session_id, tag, start, end))
    if not chunks:
        return np.empty((0, 2))
    data = np.concatenate(chunks)
    low, high = (30, 220) if tag == "HR" else (0, 100)
    data = data[np.isfinite(data).all(axis=1) & (data[:, 1] >= low) & (data[:, 1] <= high)]
    if not len(data):
        return data
    data = data[np.argsort(data[:, 0], kind="stable")]
    return data[np.concatenate(([True], np.diff(data[:, 0]) > 0))]


def _robust(values: FloatArray, floor: float) -> tuple[float, float]:
    """Return a stable median and MAD scale, including constant recordings."""
    median = float(np.median(values))
    return median, max(floor, float(np.median(np.abs(values - median))) * 1.4826)


def controls(
    signals: LiveSignalStore, session_id: str, start: float, end: float, session_end: float
) -> tuple[FloatArray, dict[str, bool]]:
    """Build smoothed 10 Hz controls without interpolating across long gaps."""
    grid = np.linspace(start, end, math.ceil((end - start) * 10) + 1)
    result = np.full((len(grid), 2), 0.5)
    valid = {}
    for column, tag in enumerate(("HR", "EA")):
        data = _series(signals, session_id, tag, start, end)
        coverage = float(np.sum(np.where(np.diff(data[:, 0]) <= 5,
                                         np.diff(data[:, 0]), 0))) if len(data) else 0
        valid[tag] = len(data) >= 3 and coverage >= (end - start) * 0.5
        if not valid[tag]:
            continue
        if tag == "HR":
            # One-second medians bound baseline memory independently of raw LSL rate.
            baseline: list[float] = []
            current_second: int | None = None
            pending: list[float] = []
            for chunk in signals.iter_raw(session_id, tag, 0, session_end):
                for timestamp, value in chunk:
                    if not math.isfinite(value) or not 30 <= value <= 220:
                        continue
                    second = int(timestamp)
                    if current_second is not None and second != current_second:
                        baseline.append(float(np.median(pending)))
                        pending = []
                    current_second = second
                    pending.append(float(value))
            if pending:
                baseline.append(float(np.median(pending)))
            center, scale = _robust(np.asarray(baseline or data[:, 1].tolist()), 5)
        else:
            center, scale = _robust(data[:, 1], 0.05)
        values = np.interp(grid, data[:, 0], data[:, 1])
        supported = np.zeros(len(grid), dtype=bool)
        for left, right in zip(data[:-1, 0], data[1:, 0], strict=True):
            if right - left <= 5:
                supported |= (grid >= left) & (grid <= right)
        normalized = 0.5 + 0.2 * np.tanh((values - center) / (2 * scale))
        if tag == "EA":
            normalized += 0.1 * np.tanh(np.gradient(values, grid) / scale)
        target = np.where(supported, normalized, 0.5)
        for index in range(1, len(grid)):
            result[index, column] = result[index - 1, column] + 0.05 * (
                target[index] - result[index - 1, column]
            )
    if not any(valid.values()):
        raise ValueError("No reliable HR or EDA data in this audition")
    return result, valid


def generate_ambient(
    path: Path, duration: float, curves: FloatArray, seed: int, cancelled: threading.Event
) -> None:
    """Stream deterministic filtered noise into a peak-bounded stereo PCM WAV."""
    frames = round(duration * SAMPLE_RATE)
    rng = np.random.default_rng(seed)
    memory = np.zeros((2, 2))
    control_times = np.linspace(0, duration, len(curves))
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, SAMPLE_RATE, frames, "NONE", "not compressed"))
        for first in range(0, frames, 4096):
            if cancelled.is_set():
                raise RuntimeError("Overlay generation cancelled")
            count = min(4096, frames - first)
            times = (np.arange(count) + first) / SAMPLE_RATE
            hr = np.interp(times, control_times, curves[:, 0])
            eda = np.interp(times, control_times, curves[:, 1])
            noise = rng.uniform(-1, 1, (count, 2))
            # Two continuous one-pole filters form a gentle, non-tonal noise band.
            for index in range(count):
                alpha = 0.015 + 0.055 * hr[index]
                memory[0] += alpha * (noise[index] - memory[0])
                memory[1] += 0.002 * (memory[0] - memory[1])
                noise[index] = memory[0] - memory[1]
            motion = 0.75 + 0.25 * np.sin(times * (0.3 + eda * 0.5))
            fade = np.minimum(1, np.minimum(times, duration - times) / 0.5)
            audio = np.tanh(noise * 2) * (0.10 + hr[:, None] * 0.06)
            audio *= (motion * np.maximum(0, fade))[:, None]
            output.writeframesraw((audio * 32767).astype("<i2").tobytes())
