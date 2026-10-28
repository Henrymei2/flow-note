"""Bounded-memory physiological controls and reproducible audio stems."""

from __future__ import annotations

import math
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.signal import butter, lfilter, sosfilt  # type: ignore[import-untyped]

from .audition_detection import DEFAULT_BPM
from .live_signals import LiveSignalStore

SAMPLE_RATE = 48000
GENERATOR_VERSION = "physiology-v4"
CONFIG: dict[str, Any] = {
    "sample_rate": SAMPLE_RATE,
    "control_hz": 10,
    "peak": 0.16,
    "fade_seconds": 0.5,
    "reverb_seconds": 2.4,
    "max_wet": 0.6,
    "ambience": "temperature-surf-v2",
    "rhythm": "expressive-eighths-v2",
    "default_bpm": DEFAULT_BPM,
    "normalization": "audition-median-p10-p90",
    "variation_thresholds": {"HR": 2.0, "EA": 0.02, "T1": 0.03, "TH": 0.03},
    "smoothing_seconds": {"HR": 0.6, "EA": 0.8, "T1": 1.5, "TH": 1.5},
    "control_smoothing_seconds": 0.15,
    "control_amplitude": 0.45,
    "eda_trend_amplitude": 0.08,
    "density_thresholds": [0.35, 0.55, 0.75],
    "density_hysteresis": 0.03,
    "timbre_steps": 32,
    "drum_velocity": [0.35, 0.65],
    "kick_decay_seconds": [0.24, 0.16],
    "tap_decay_seconds": [0.12, 0.07],
    "shaker_cutoff_hz": [2200, 4500],
    "surf_depth": [0.08, 0.30],
    "surf_frequency_hz": [0.06, 0.06],
    "surf_width": [0.20, 0.45],
}
FloatArray = NDArray[np.float64]
LIMITS = {"HR": (30, 220), "EA": (0, 100), "T1": (10, 50), "TH": (10, 50)}


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


def _segments(supported: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """Locate contiguous supported grid runs without per-sample Python work."""
    edges = np.flatnonzero(np.diff(np.r_[False, supported, False]))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def controls(
    signals: LiveSignalStore,
    session_id: str,
    start: float,
    end: float,
    session_end: float | None = None,
    *,
    summary: dict[str, Any] | None = None,
) -> tuple[FloatArray, dict[str, bool]]:
    """Build time-weighted local controls; session_end is retained for older callers only."""
    del session_end
    grid = np.linspace(start, end, math.ceil((end - start) * 10) + 1)
    dt = float(grid[1] - grid[0])
    result = np.full((len(grid), 3), 0.5)
    valid: dict[str, bool] = {}
    statistics: dict[str, Any] = {}
    for tag in LIMITS:
        data = _series(signals, session_id, tag, start, end)
        gaps = np.diff(data[:, 0])
        coverage = float(np.sum(gaps[gaps <= 5]))
        valid[tag] = len(data) >= 3 and coverage >= (end - start) * 0.5
        selected = valid[tag] and not (tag == "TH" and valid.get("T1"))
        threshold = float(CONFIG["variation_thresholds"][tag])
        info: dict[str, Any] = {
            "valid": valid[tag], "selected": selected, "baseline": None,
            "p10": None, "p90": None, "flat": None, "threshold": threshold,
        }
        statistics[tag] = info
        if not selected:
            continue
        # Reduce and interpolate each raw run independently: even the first half-second
        # after a gap must never interpolate toward a representative on its other side.
        values = np.full(len(grid), np.nan)
        for raw in np.split(data, np.flatnonzero(gaps > 5) + 1):
            if len(raw) < 2:
                continue
            bins = np.floor(raw[:, 0] - start).astype(np.int64)
            _, boundaries = np.unique(bins, return_index=True)
            reduced = np.asarray([
                np.median(group, axis=0) for group in np.split(raw, boundaries[1:])
            ])
            left = int(np.searchsorted(grid, raw[0, 0], side="left"))
            right = int(np.searchsorted(grid, raw[-1, 0], side="right"))
            values[left:right] = np.interp(grid[left:right], reduced[:, 0], reduced[:, 1])
        supported = np.isfinite(values)
        runs = _segments(supported)
        alpha = 1 - math.exp(-dt / CONFIG["smoothing_seconds"][tag])
        smoothed = values.copy()
        for left, right in runs:
            smoothed[left:right], _ = lfilter(
                [alpha], [1, alpha - 1], values[left:right],
                zi=[values[left] * (1 - alpha)],
            )
        if not np.any(supported):
            valid[tag] = False
            info.update(valid=False, selected=False)
            continue
        center = float(np.median(smoothed[supported]))
        p10, p90 = np.percentile(smoothed[supported], [10, 90]).tolist()
        flat = p90 - p10 < threshold
        info.update(baseline=center, p10=p10, p90=p90, flat=flat)
        if flat:
            continue
        scale = (p90 - p10) / 2
        target = np.full(len(grid), 0.5)
        for left, right in runs:
            local = smoothed[left:right]
            mapped = 0.5 + CONFIG["control_amplitude"] * np.tanh((local - center) / scale)
            if tag == "EA" and right - left > 1:
                mapped += CONFIG["eda_trend_amplitude"] * np.tanh(
                    np.gradient(local, dt) / scale
                )
            target[left:right] = np.clip(mapped, 0.05, 0.95)
        alpha = 1 - math.exp(-dt / CONFIG["control_smoothing_seconds"])
        column = {"HR": 0, "EA": 1, "T1": 2, "TH": 2}[tag]
        result[:, column], _ = lfilter(
            [alpha], [1, alpha - 1], target, zi=[target[0] * (1 - alpha)]
        )
    if not any(valid.values()):
        raise ValueError("No reliable HR, EDA or temperature data in this audition")
    if summary is not None:
        summary.update(scope="audition", channels=statistics)
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
    memory = [np.zeros((len(sos), 2, 3)) for sos in filters]
    phase = float(rng.uniform(0, 2 * np.pi))
    secondary_phase = float(rng.uniform(0, 2 * np.pi))
    drift_phase = float(rng.uniform(0, 2 * np.pi))
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
            noise = rng.uniform(-1, 1, (count, 3))
            bands = []
            for index, sos in enumerate(filters):
                filtered, memory[index] = sosfilt(sos, noise, axis=0, zi=memory[index])
                # Match broadband levels before temperature blends the fixed bands.
                bands.append(filtered * (7.0 if index == 0 else 4.2))
            blend = temperature[:, None]
            texture = bands[0] * (1 - blend * 0.65) + bands[1] * blend * 0.65
            width = CONFIG["surf_width"][0] + CONFIG["surf_width"][1] * blend
            # Equal-power common/independent mixing: widening never cancels the mono centre.
            audio = np.sqrt(1 - width) * texture[:, :1] + np.sqrt(width) * texture[:, 1:]
            frequency = (
                CONFIG["surf_frequency_hz"][0] + CONFIG["surf_frequency_hz"][1] * temperature
            )
            increments = 2 * np.pi * frequency / SAMPLE_RATE
            angles = phase + np.r_[0.0, np.cumsum(increments[:-1])]
            phase = float((phase + np.sum(increments)) % (2 * np.pi))
            secondary = secondary_phase + np.r_[0.0, np.cumsum(increments[:-1] * 0.61)]
            secondary_phase = float((secondary_phase + np.sum(increments) * 0.61) % (2 * np.pi))
            depth = CONFIG["surf_depth"][0] + CONFIG["surf_depth"][1] * temperature
            motion = 0.78 + depth * (0.75 * np.sin(angles) + 0.25 * np.sin(secondary))
            motion += 0.025 * np.sin(2 * np.pi * 0.037 * times + drift_phase)
            audio *= (0.075 + blend * 0.045) * motion[:, None]
            audio = 0.16 * np.tanh(audio / 0.16)
            _write(output, audio, times, duration)


def drum_events(duration: float, curves: FloatArray, bpm: float) -> list[tuple[float, int, float]]:
    """Place grid-aligned drums with four density levels and bar-rate hysteresis."""
    events = []
    times = np.linspace(0, duration, len(curves))
    thresholds = CONFIG["density_thresholds"]
    hysteresis = CONFIG["density_hysteresis"]
    level = int(np.searchsorted(thresholds, curves[0, 0], side="right"))
    for eighth in range(math.ceil(duration * bpm / 30)):
        at = eighth * 30 / bpm
        if at >= duration:
            break
        hr = float(np.clip(np.interp(at, times, curves[:, 0]), 0, 1))
        if eighth % 8 == 0:
            while level < 3 and hr >= thresholds[level] + hysteresis:
                level += 1
            while level > 0 and hr < thresholds[level - 1] - hysteresis:
                level -= 1
        if eighth % 2 and not (
            level == 3 or (level == 2 and eighth % 4 == 3) or (level == 1 and eighth % 8 == 7)
        ):
            continue
        instrument = (eighth // 2) % 2 if eighth % 2 == 0 else 2
        velocity = CONFIG["drum_velocity"][0] + CONFIG["drum_velocity"][1] * hr
        events.append((at, instrument, velocity))
    return events


def _drum_sample(instrument: int, step: int, seed: int) -> NDArray[np.float32]:
    """Synthesize one of 32 articulation levels, keeping the kick's fundamental fixed."""
    u = step / (CONFIG["timbre_steps"] - 1)
    t = np.arange(round(0.25 * SAMPLE_RATE)) / SAMPLE_RATE
    rng = np.random.default_rng((seed ^ 0xD2A5 ^ (instrument * 0x12345)) & 0xFFFFFFFF)
    if instrument == 0:
        decay = CONFIG["kick_decay_seconds"][0] * (1 - u) + CONFIG["kick_decay_seconds"][1] * u
        sample = np.sin(2 * np.pi * (52 * t + 2.8 * (1 - np.exp(-25 * t))))
    else:
        cutoff = CONFIG["shaker_cutoff_hz"][0] * (1 - u) + CONFIG["shaker_cutoff_hz"][1] * u
        band = [500 if instrument == 1 else 1300, cutoff]
        sos = butter(2, band, btype="bandpass", fs=SAMPLE_RATE, output="sos")
        sample = sosfilt(sos, rng.uniform(-1, 1, len(t)))
        decay = CONFIG["tap_decay_seconds"][0] * (1 - u) + CONFIG["tap_decay_seconds"][1] * u
        if instrument == 2:
            decay *= 0.7
    sample *= np.exp(-6 * t / decay) * (1 - np.exp(-t * 1200))
    sample *= np.clip((0.25 - t) / 0.003, 0, 1)
    sample *= (0.10 if instrument == 2 else 0.13) / max(float(np.max(np.abs(sample))), 1e-9)
    return np.asarray(sample, dtype=np.float32)


def generate_drums(
    path: Path,
    duration: float,
    curves: FloatArray,
    bpm: float,
    seed: int,
    cancelled: threading.Event,
) -> None:
    """Stream drums using a bounded per-job articulation cache and deterministic events."""
    events = []
    for at, instrument, velocity in drum_events(duration, curves, bpm):
        u = (velocity - CONFIG["drum_velocity"][0]) / CONFIG["drum_velocity"][1]
        step = round(np.clip(u, 0, 1) * (CONFIG["timbre_steps"] - 1))
        events.append((round(at * SAMPLE_RATE), instrument, velocity, step))
    samples: dict[tuple[int, int], NDArray[np.float32]] = {}
    frames = round(duration * SAMPLE_RATE)
    cursor = 0
    sample_frames = round(0.25 * SAMPLE_RATE)
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, SAMPLE_RATE, frames, "NONE", "not compressed"))
        for first in range(0, frames, 4096):
            if cancelled.is_set():
                raise RuntimeError("Overlay generation cancelled")
            count = min(4096, frames - first)
            mono = np.zeros(count)
            while cursor < len(events) and events[cursor][0] + sample_frames <= first:
                cursor += 1
            for position, instrument, velocity, step in events[cursor:]:
                if position >= first + count:
                    break
                key = (instrument, step)
                if key not in samples:
                    samples[key] = _drum_sample(instrument, step, seed)
                left, right = max(first, position), min(first + count, position + sample_frames)
                mono[left - first : right - first] += (
                    samples[key][left - position : right - position] * velocity
                )
            audio = np.column_stack((mono, mono))
            _write(output, audio, (np.arange(count) + first) / SAMPLE_RATE, duration)
