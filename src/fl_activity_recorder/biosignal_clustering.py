from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .live_signals import LiveSignalStore

CARDIO_TAG = "HR"
EDA_TAG = "EA"
TEMPERATURE_TAGS = ("T1", "TH")
MOTION_TAGS = ("AX", "AY", "AZ", "GX", "GY", "GZ")
ALL_SOURCE_TAGS = {CARDIO_TAG, EDA_TAG, *TEMPERATURE_TAGS, *MOTION_TAGS}
FloatArray = NDArray[np.float64]

PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "HR": (30.0, 220.0),
    "EA": (0.0, 100.0),
}

MIN_SAMPLE_COVERAGE_RATIO = 0.5
MIN_WINDOW_SAMPLES = 2
SUGGESTION_LIMIT = 10
PARTITION_SECONDS = 300.0
FUSION_SECONDS = 1.0
PERCENTILE_THRESHOLD = 90.0
SCORE_THRESHOLD = 2.0
ALGORITHM_VERSION = "physiological-pattern-score-v3"

MODALITY_WEIGHTS = {"C": 0.30, "E": 0.30, "M": 0.25, "T": 0.15}
MODALITY_LABELS = {
    "C": "HR",
    "E": "EDA",
    "M": "Movement",
    "T": "Temperature",
}

EMPTY_RESULT: dict[str, Any] = {
    "window_seconds": FUSION_SECONDS,
    "windows": [],
    "clusters": [],
}


@dataclass(frozen=True, slots=True)
class ScoredWindow:
    """Store one fixed-width modality window and its robust anomaly score."""

    start: float
    end: float
    score: float


@dataclass(frozen=True, slots=True)
class CandidateSegment:
    """Store one non-overlapping constant-strength segment for a modality."""

    start: float
    end: float
    modality: str
    strength: float


@dataclass(frozen=True, slots=True)
class IntervalScore:
    """Store a fused candidate interval and the values exposed in its centroid."""

    start: float
    end: float
    score: float
    sample_count: int
    fused_peak: float
    modality_peaks: dict[str, float]


def compute_biosignal_clusters(
    signals: LiveSignalStore,
    session_id: str,
    duration_seconds: float,
    *,
    window_seconds: float | None = None,
    n_clusters: int = SUGGESTION_LIMIT,
) -> dict[str, Any]:
    """Return up to ten suggestions selected independently per five-minute partition.

    ``window_seconds`` remains accepted for wire compatibility, but the scoring
    pipeline uses modality-specific fixed windows. Each partition contributes
    at most one suggestion, and ``n_clusters`` controls the global result limit.
    """
    del window_seconds
    if duration_seconds <= 0:
        return {**EMPTY_RESULT, "algorithm_version": ALGORITHM_VERSION}

    series = _load_series(signals, session_id, duration_seconds)
    partition_winners: list[IntervalScore] = []
    partition_count = math.ceil(duration_seconds / PARTITION_SECONDS)
    for index in range(partition_count):
        partition_start = index * PARTITION_SECONDS
        partition_end = min(partition_start + PARTITION_SECONDS, duration_seconds)
        local_series = _slice_series(series, partition_start, partition_end)
        local_candidates = _score_series(local_series, partition_end - partition_start)
        local_winner = _select_non_overlapping(local_candidates, 1)
        if local_winner:
            partition_winners.append(_shift_interval(local_winner[0], partition_start))

    selected = _select_non_overlapping(
        partition_winners, min(SUGGESTION_LIMIT, max(0, n_clusters))
    )
    return _serialize_result(selected)


def _score_series(
    series: dict[str, tuple[FloatArray, FloatArray]], duration_seconds: float
) -> list[IntervalScore]:
    """Score every candidate within one partition-local signal collection."""
    modality_windows: dict[str, list[ScoredWindow]] = {}

    cardio = series.get(CARDIO_TAG)
    if cardio is not None:
        features = _extract_features(
            cardio, duration_seconds, 10.0, 5.0, ("median", "slope", "delta")
        )
        modality_windows["C"] = _score_feature_windows(
            features, {"median": 0.5, "slope": 0.3, "delta": 0.2}
        )

    eda = series.get(EDA_TAG)
    if eda is not None:
        features = _extract_features(
            eda, duration_seconds, 60.0, 15.0, ("median", "slope", "variance")
        )
        modality_windows["E"] = _score_feature_windows(
            features, {"median": 0.4, "slope": 0.2, "variance": 0.4}
        )

    temperature_channels = []
    for tag in TEMPERATURE_TAGS:
        channel = series.get(tag)
        if channel is None:
            continue
        features = _extract_features(
            channel, duration_seconds, 120.0, 30.0, ("slope", "delta", "median")
        )
        temperature_channels.append(
            _score_feature_windows(features, {"slope": 0.5, "delta": 0.3, "median": 0.2})
        )
    if temperature_channels:
        modality_windows["T"] = _average_window_scores(temperature_channels)

    motion_energy = _motion_energy_series(series)
    if motion_energy is not None:
        features = _extract_features(
            motion_energy, duration_seconds, 5.0, 2.0, ("median", "delta")
        )
        modality_windows["M"] = _score_feature_windows(
            features, {"median": 0.5, "delta": 0.5}
        )

    segments = [
        segment
        for modality, windows in modality_windows.items()
        for segment in _candidate_segments(modality, windows)
    ]
    if not segments:
        return []

    grid_times, envelopes, fused = _fuse_segments(segments, duration_seconds)
    return _score_intervals(segments, grid_times, envelopes, fused)


def _load_series(
    signals: LiveSignalStore, session_id: str, duration_seconds: float
) -> dict[str, tuple[FloatArray, FloatArray]]:
    """Load and range-filter every source channel used by the scorer."""
    series: dict[str, tuple[FloatArray, FloatArray]] = {}
    for tag in ALL_SOURCE_TAGS:
        try:
            result = signals.query(session_id, tag, 0.0, duration_seconds, pixels=1_000_000)
        except KeyError:
            continue
        points = result["points"]
        if not points:
            continue
        array = np.asarray(points, dtype="f8")
        times, values = array[:, 0], array[:, 1]
        finite = np.isfinite(times) & np.isfinite(values)
        times, values = times[finite], values[finite]
        if tag in PLAUSIBLE_RANGES:
            low, high = PLAUSIBLE_RANGES[tag]
            plausible = (values >= low) & (values <= high)
            times, values = times[plausible], values[plausible]
        if times.size:
            order = np.argsort(times, kind="stable")
            series[tag] = (times[order], values[order])
    return series


def _slice_series(
    series: dict[str, tuple[FloatArray, FloatArray]], start: float, end: float
) -> dict[str, tuple[FloatArray, FloatArray]]:
    """Return one half-open partition with timestamps relative to its start."""
    sliced: dict[str, tuple[FloatArray, FloatArray]] = {}
    for tag, (times, values) in series.items():
        mask = (times >= start) & (times < end)
        if np.any(mask):
            sliced[tag] = (times[mask] - start, values[mask])
    return sliced


def _shift_interval(interval: IntervalScore, offset: float) -> IntervalScore:
    """Translate a partition-local interval back onto the session timeline."""
    return IntervalScore(
        start=interval.start + offset,
        end=interval.end + offset,
        score=interval.score,
        sample_count=interval.sample_count,
        fused_peak=interval.fused_peak,
        modality_peaks=interval.modality_peaks,
    )


def _window_starts(duration_seconds: float, length: float, step: float) -> list[float]:
    """Return starts for complete fixed-width half-open windows."""
    if duration_seconds < length:
        return []
    count = math.floor((duration_seconds - length) / step + 1e-9) + 1
    return [round(index * step, 6) for index in range(count)]


def _extract_features(
    series: tuple[FloatArray, FloatArray],
    duration_seconds: float,
    length: float,
    step: float,
    feature_names: tuple[str, ...],
) -> list[dict[str, float]]:
    """Extract requested statistics from sufficiently covered windows."""
    times, values = series
    average_rate = len(times) / max(duration_seconds, 1e-9)
    previous_median: float | None = None
    windows: list[dict[str, float]] = []
    for start in _window_starts(duration_seconds, length, step):
        end = start + length
        mask = (times >= start) & (times < end)
        count = int(np.count_nonzero(mask))
        expected = max(float(MIN_WINDOW_SAMPLES), average_rate * length)
        if count < MIN_WINDOW_SAMPLES or count / expected < MIN_SAMPLE_COVERAGE_RATIO:
            previous_median = None
            continue
        window_times = times[mask]
        window_values = values[mask]
        median = float(np.median(window_values))
        record = {"start": start, "end": end}
        if "median" in feature_names:
            record["median"] = median
        if "slope" in feature_names:
            record["slope"] = _linear_slope(window_times, window_values)
        if "variance" in feature_names:
            record["variance"] = float(np.var(window_values))
        if "delta" in feature_names:
            record["delta"] = 0.0 if previous_median is None else median - previous_median
        previous_median = median
        windows.append(record)
    return windows


def _linear_slope(times: FloatArray, values: FloatArray) -> float:
    """Calculate a least-squares slope against the actual sample timestamps."""
    centered_times = times - float(np.mean(times))
    denominator = float(np.dot(centered_times, centered_times))
    if denominator <= 0:
        return 0.0
    centered_values = values - float(np.mean(values))
    return float(np.dot(centered_times, centered_values) / denominator)


def _robust_z(values: FloatArray) -> FloatArray:
    """Return median absolute deviation z-scores with finite zero fallback."""
    if values.size == 0:
        return np.asarray([], dtype="f8")
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if not math.isfinite(mad) or mad <= 0:
        return np.zeros(values.shape, dtype="f8")
    result = 0.675 * (values - median) / mad
    return np.where(np.isfinite(result), result, 0.0)


def _score_feature_windows(
    windows: list[dict[str, float]], weights: dict[str, float]
) -> list[ScoredWindow]:
    """Combine absolute robust feature z-scores into modality window scores."""
    if not windows:
        return []
    standardized = {
        name: _robust_z(np.asarray([window[name] for window in windows], dtype="f8"))
        for name in weights
    }
    return [
        ScoredWindow(
            start=window["start"],
            end=window["end"],
            score=float(
                sum(weight * abs(standardized[name][index]) for name, weight in weights.items())
            ),
        )
        for index, window in enumerate(windows)
    ]


def _average_window_scores(channels: list[list[ScoredWindow]]) -> list[ScoredWindow]:
    """Average available temperature-channel scores for matching windows."""
    grouped: dict[tuple[float, float], list[float]] = {}
    for channel in channels:
        for window in channel:
            grouped.setdefault((window.start, window.end), []).append(window.score)
    return [
        ScoredWindow(start=start, end=end, score=float(np.mean(scores)))
        for (start, end), scores in sorted(grouped.items())
    ]


def _motion_energy_series(
    series: dict[str, tuple[FloatArray, FloatArray]],
) -> tuple[FloatArray, FloatArray] | None:
    """Align six robust-standardized motion axes and return their RMS energy."""
    if any(tag not in series for tag in MOTION_TAGS):
        return None
    common_times = series[MOTION_TAGS[0]][0]
    for tag in MOTION_TAGS[1:]:
        common_times = np.intersect1d(common_times, series[tag][0], assume_unique=False)
    if common_times.size < MIN_WINDOW_SAMPLES:
        return None
    axes = []
    for tag in MOTION_TAGS:
        times, values = series[tag]
        indices = np.searchsorted(times, common_times)
        axes.append(_robust_z(values)[indices])
    matrix = np.vstack(axes)
    energy = np.sqrt(np.mean(np.square(matrix), axis=0))
    return common_times, energy


def _candidate_segments(modality: str, windows: list[ScoredWindow]) -> list[CandidateSegment]:
    """Convert qualifying overlapping windows into a maximum-strength envelope."""
    if not windows:
        return []
    scores = np.asarray([window.score for window in windows], dtype="f8")
    threshold = float(np.percentile(scores, PERCENTILE_THRESHOLD))
    ordered = np.sort(scores)
    qualifying: list[tuple[ScoredWindow, float]] = []
    for window in windows:
        if window.score < threshold or window.score <= SCORE_THRESHOLD:
            continue
        rank = 100.0 * float(np.searchsorted(ordered, window.score, side="right")) / len(ordered)
        strength = float(np.clip((rank - PERCENTILE_THRESHOLD) / 10.0, 0.0, 1.0))
        if strength > 0:
            qualifying.append((window, strength))
    if not qualifying:
        return []

    boundaries = sorted({point for window, _ in qualifying for point in (window.start, window.end)})
    segments: list[CandidateSegment] = []
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        midpoint = (start + end) / 2.0
        strength = max(
            (
                candidate_strength
                for window, candidate_strength in qualifying
                if window.start <= midpoint < window.end
            ),
            default=0.0,
        )
        if strength <= 0:
            continue
        previous = segments[-1] if segments else None
        if (
            previous is not None
            and math.isclose(previous.end, start)
            and math.isclose(previous.strength, strength)
        ):
            segments[-1] = CandidateSegment(previous.start, end, modality, strength)
        else:
            segments.append(CandidateSegment(start, end, modality, strength))
    return segments


def _fuse_segments(
    segments: list[CandidateSegment], duration_seconds: float
) -> tuple[FloatArray, dict[str, FloatArray], FloatArray]:
    """Project modality envelopes onto one-second bins and calculate F(t)."""
    grid_times: FloatArray = np.arange(
        0.0, duration_seconds, FUSION_SECONDS, dtype=np.float64
    )
    envelopes: dict[str, FloatArray] = {
        name: np.zeros(grid_times.shape, dtype=np.float64) for name in MODALITY_WEIGHTS
    }
    for segment in segments:
        mask = (grid_times >= segment.start) & (grid_times < segment.end)
        envelopes[segment.modality][mask] = np.maximum(
            envelopes[segment.modality][mask], segment.strength
        )
    active_count = np.zeros(grid_times.shape, dtype="i4")
    weighted: FloatArray = np.zeros(grid_times.shape, dtype=np.float64)
    for name, weight in MODALITY_WEIGHTS.items():
        active_count += (envelopes[name] > 0).astype("i4")
        weighted += weight * envelopes[name]
    fused: FloatArray = (0.8 + 0.2 * active_count) * weighted
    return grid_times, envelopes, fused


def _score_intervals(
    segments: list[CandidateSegment],
    grid_times: FloatArray,
    envelopes: dict[str, FloatArray],
    fused: FloatArray,
) -> list[IntervalScore]:
    """Score every distinct post-dedup candidate interval from the fused signal."""
    candidates: list[IntervalScore] = []
    for start, end in sorted({(segment.start, segment.end) for segment in segments}):
        mask = (grid_times >= start) & (grid_times < end)
        values = fused[mask]
        if values.size == 0:
            continue
        top_count = max(1, math.ceil(values.size * 0.25))
        top_values = np.sort(values)[-top_count:]
        score = 0.5 * float(np.max(values)) + 0.30 * float(np.mean(top_values))
        modality_peaks = {
            modality: float(np.max(modality_values[mask]))
            for modality, modality_values in envelopes.items()
        }
        candidates.append(
            IntervalScore(
                start=start,
                end=end,
                score=score,
                sample_count=int(values.size),
                fused_peak=float(np.max(values)),
                modality_peaks=modality_peaks,
            )
        )
    return candidates


def _select_non_overlapping(
    candidates: list[IntervalScore], limit: int
) -> list[IntervalScore]:
    """Greedily select the highest-scoring intervals without positive overlap."""
    if limit <= 0:
        return []
    selected: list[IntervalScore] = []
    ordered = sorted(candidates, key=lambda item: (-item.score, item.start, item.end - item.start))
    for candidate in ordered:
        if all(
            candidate.end <= existing.start or candidate.start >= existing.end
            for existing in selected
        ):
            selected.append(candidate)
            if len(selected) >= limit:
                break
    return selected


def _serialize_result(selected: list[IntervalScore]) -> dict[str, Any]:
    """Adapt selected intervals to the response consumed by the existing UI."""
    score_order = sorted(selected, key=lambda item: (item.score, -item.start))
    cluster_id = {(item.start, item.end): index for index, item in enumerate(score_order)}
    descending = sorted(selected, key=lambda item: (-item.score, item.start, item.end - item.start))
    suggestion_rank = {(item.start, item.end): index + 1 for index, item in enumerate(descending)}

    clusters = []
    for item in score_order:
        modalities = [
            MODALITY_LABELS[name]
            for name in MODALITY_WEIGHTS
            if item.modality_peaks[name] > 0
        ]
        clusters.append(
            {
                "id": cluster_id[(item.start, item.end)],
                "size": item.sample_count,
                "centroid": {
                    "score": item.score,
                    "f_peak": item.fused_peak,
                    "cardio_peak": item.modality_peaks["C"],
                    "eda_peak": item.modality_peaks["E"],
                    "movement_peak": item.modality_peaks["M"],
                    "temperature_peak": item.modality_peaks["T"],
                },
                "label": (
                    f"Suggestion #{suggestion_rank[(item.start, item.end)]}"
                    f" · {' + '.join(modalities)}"
                ),
            }
        )
    windows = [
        {
            "start_seconds": item.start,
            "end_seconds": item.end,
            "cluster": cluster_id[(item.start, item.end)],
        }
        for item in sorted(selected, key=lambda item: item.start)
    ]
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "window_seconds": FUSION_SECONDS,
        "windows": windows,
        "clusters": clusters,
    }
