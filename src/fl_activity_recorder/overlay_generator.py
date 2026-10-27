"""Bounded-memory physiological controls and reproducible audio stems."""

from __future__ import annotations

import math
import threading
import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.signal import butter, lfilter, sosfilt  # type: ignore[import-untyped]

from .audition_detection import DEFAULT_BPM
from .live_signals import LiveSignalStore

SAMPLE_RATE = 48000
GENERATOR_VERSION = "physiology-v3"
CONFIG = {
    "sample_rate": SAMPLE_RATE,
    "control_hz": 10,
    "peak": 0.16,
    "fade_seconds": 0.5,
    "reverb_seconds": 2.4,
    "max_wet": 0.6,
    "ambience": "distant-surf-v1",
    "rhythm": "soft-eighths-v1",
    "default_bpm": DEFAULT_BPM,
}
FloatArray = NDArray[np.float64]
Baseline = dict[str, tuple[float, float]]
LIMITS = {"HR": (30, 220), "EA": (0, 100), "T1": (10, 50), "TH": (10, 50)}
FLOORS = {"HR": 5.0, "EA": 0.05, "T1": 0.1, "TH": 0.1}


def _series(
    signals: LiveSignalStore, session_id: str, tag: str, start: float, end: float
) -> FloatArray:
    """Load valid raw samples and collapse duplicate timestamps deterministically."""
    chunks = list(signals.iter_raw(session_id, tag, start, end))
    if not chunks:
        return np.empty((0, 2))
    data = np.concatenate(chunks)
    low, high = LIMITS[tag]
    data = data[np.isfinite(data).all(axis=1) & (data[:, 1] >= low) & (data[:, 1] <= high)]
    if not len(data):
        return data
    data = data[np.argsort(data[:, 0], kind="stable")]
    return data[np.concatenate(([True], np.diff(data[:, 0]) > 0))]


def _robust(values: FloatArray, floor: float) -> tuple[float, float]:
    """Return a stable median and MAD scale, including constant recordings."""
    median = float(np.median(values))
    return median, max(floor, float(np.median(np.abs(values - median))) * 1.4826)


def session_baselines(signals: LiveSignalStore, session_id: str, end: float) -> Baseline:
    """Reduce immutable raw chunks to one median per second for each channel."""
    result: Baseline = {}
    for tag, (low, high) in LIMITS.items():
        medians: list[float] = []
        current_second: int | None = None
        pending: list[FloatArray] = []
        # Only one second of raw values is retained across immutable chunk boundaries.
        for chunk in signals.iter_raw(session_id, tag, 0, end):
            chunk = chunk[
                np.isfinite(chunk).all(axis=1) & (chunk[:, 1] >= low) & (chunk[:, 1] <= high)
            ]
            if not len(chunk):
                continue
            chunk = chunk[np.argsort(chunk[:, 0], kind="stable")]
            bins = chunk[:, 0].astype(np.int64)
            seconds, boundaries = np.unique(bins, return_index=True)
            groups = np.split(chunk[:, 1], boundaries[1:])
            for second, values in zip(seconds, groups, strict=True):
                if current_second is not None and second != current_second:
                    medians.append(float(np.median(np.concatenate(pending))))
                    pending = []
                current_second = int(second)
                pending.append(values)
        if pending:
            medians.append(float(np.median(np.concatenate(pending))))
        if medians:
            result[tag] = _robust(np.asarray(medians), FLOORS[tag])
    return result


def controls(
    signals: LiveSignalStore,
    session_id: str,
    start: float,
    end: float,
    session_end: float,
    baselines: Baseline | None = None,
) -> tuple[FloatArray, dict[str, bool]]:
    """Build 10 Hz HR/EDA/temperature controls without bridging long gaps."""
    grid = np.linspace(start, end, math.ceil((end - start) * 10) + 1)
    result = np.full((len(grid), 3), 0.5)
    valid: dict[str, bool] = {}
    baselines = (
        session_baselines(signals, session_id, session_end) if baselines is None else baselines
    )
    for tag in LIMITS:
        data = _series(signals, session_id, tag, start, end)
        gaps = np.diff(data[:, 0])
        coverage = float(np.sum(gaps[gaps <= 5]))
        valid[tag] = len(data) >= 3 and coverage >= (end - start) * 0.5
        if not valid[tag] or (tag == "TH" and valid.get("T1")):
            continue
        column = {"HR": 0, "EA": 1, "T1": 2, "TH": 2}[tag]
        center, scale = baselines.get(tag, _robust(data[:, 1], FLOORS[tag]))
        values = np.interp(grid, data[:, 0], data[:, 1])
        # Each grid point only needs its enclosing pair, rather than a full-grid scan per sample.
        index = np.clip(np.searchsorted(data[:, 0], grid, side="right") - 1, 0, len(data) - 2)
        supported = (
            (grid >= data[index, 0])
            & (grid <= data[index + 1, 0])
            & (data[index + 1, 0] - data[index, 0] <= 5)
        )
        normalized = 0.5 + 0.2 * np.tanh((values - center) / (2 * scale))
        if tag == "EA":
            normalized += 0.1 * np.tanh(np.gradient(values, grid) / scale)
        target = np.where(supported, normalized, 0.5)
        result[:, column], _ = lfilter([0.05], [1, -0.95], target, zi=[0.475])
    if not any(valid.values()):
        raise ValueError("No reliable HR, EDA or temperature data in this audition")
    return result, valid


def _write(output: wave.Wave_write, audio: FloatArray, times: FloatArray, duration: float) -> None:
    """Apply boundary fades and a strict per-stem peak ceiling."""
    fade = np.clip(np.minimum(times, duration - times) / 0.5, 0, 1)
    audio = np.clip(audio, -0.16, 0.16) * fade[:, None]
    output.writeframesraw((audio * 32767).astype("<i2").tobytes())


def generate_ambient(
    path: Path, duration: float, curves: FloatArray, seed: int, cancelled: threading.Event
) -> None:
    """Stream dark, slowly swelling wind/surf with temperature-controlled warmth."""
    frames = round(duration * SAMPLE_RATE)
    rng = np.random.default_rng(seed)
    filters = [
        butter(3, [100, cutoff], btype="bandpass", fs=SAMPLE_RATE, output="sos")
        for cutoff in (550, 1400)
    ]
    memory = [np.zeros((len(sos), 2, 2)) for sos in filters]
    # Independent seeded phases create a slow, non-beat-synchronous swell.
    phases = rng.uniform(0, 2 * np.pi, (3, 2))
    rates = rng.uniform([0.055, 0.095, 0.17], [0.085, 0.14, 0.22])
    control_times = np.linspace(0, duration, len(curves))
    column = 2 if curves.shape[1] >= 3 else 0
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, SAMPLE_RATE, frames, "NONE", "not compressed"))
        for first in range(0, frames, 4096):
            if cancelled.is_set():
                raise RuntimeError("Overlay generation cancelled")
            count = min(4096, frames - first)
            times = (np.arange(count) + first) / SAMPLE_RATE
            temperature = np.interp(times, control_times, curves[:, column])
            noise = rng.uniform(-1, 1, (count, 2))
            bands = []
            for index, sos in enumerate(filters):
                filtered, memory[index] = sosfilt(sos, noise, axis=0, zi=memory[index])
                # Match broadband levels before temperature blends the fixed bands.
                bands.append(filtered * (7.0 if index == 0 else 4.2))
            blend = temperature[:, None]
            texture = bands[0] * (1 - blend * 0.65) + bands[1] * blend * 0.65
            motion = 0.75 + sum(
                weight * np.sin(2 * np.pi * rate * times[:, None] + phase)
                for weight, rate, phase in zip((0.13, 0.08, 0.04), rates, phases, strict=True)
            )
            audio = texture * (0.075 + blend * 0.045) * motion
            audio = 0.16 * np.tanh(audio / 0.16)
            _write(output, audio, times, duration)


def drum_events(duration: float, curves: FloatArray, bpm: float) -> list[tuple[float, int, float]]:
    """Place a restrained kick/tap pattern and HR-dependent eighth-note fills."""
    events = []
    times = np.linspace(0, duration, len(curves))
    for eighth in range(math.ceil(duration * bpm / 30)):
        at = eighth * 30 / bpm
        hr = float(np.interp(at, times, curves[:, 0]))
        if eighth % 2 and hr <= 0.55:
            continue
        instrument = (eighth // 2) % 2 if eighth % 2 == 0 else 2
        events.append((at, instrument, 0.45 + 0.55 * hr))
    return events


def generate_drums(
    path: Path,
    duration: float,
    curves: FloatArray,
    bpm: float,
    seed: int,
    cancelled: threading.Event,
) -> None:
    """Mix small locally synthesized samples into a bounded-memory beat-grid stem."""
    rng = np.random.default_rng(seed ^ 0xD2A5)
    t = np.arange(round(0.25 * SAMPLE_RATE)) / SAMPLE_RATE
    kick = np.sin(2 * np.pi * (52 * t + 2.8 * (1 - np.exp(-25 * t)))) * np.exp(-24 * t)
    noise = rng.uniform(-1, 1, len(t))
    tap = noise * np.exp(-65 * t)
    shaker = np.concatenate(([0.0], np.diff(noise))) * np.exp(-95 * t) * 0.35
    samples = [sample * (1 - np.exp(-t * 1200)) * 0.13 for sample in (kick, tap, shaker)]
    events = [
        (round(at * SAMPLE_RATE), instrument, velocity)
        for at, instrument, velocity in drum_events(duration, curves, bpm)
    ]
    frames = round(duration * SAMPLE_RATE)
    cursor = 0
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, SAMPLE_RATE, frames, "NONE", "not compressed"))
        for first in range(0, frames, 4096):
            if cancelled.is_set():
                raise RuntimeError("Overlay generation cancelled")
            count = min(4096, frames - first)
            mono = np.zeros(count)
            while cursor < len(events) and events[cursor][0] + len(t) <= first:
                cursor += 1
            for position, instrument, velocity in events[cursor:]:
                if position >= first + count:
                    break
                left, right = max(first, position), min(first + count, position + len(t))
                mono[left - first : right - first] += (
                    samples[instrument][left - position : right - position] * velocity
                )
            audio = np.column_stack((mono, mono))
            _write(output, audio, (np.arange(count) + first) / SAMPLE_RATE, duration)
