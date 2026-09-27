from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .live_signals import LiveSignalStore

CARDIO_TAG = "HR"
EDA_TAG = "EA"
TEMPERATURE_TAGS = ("T1", "TH")
ALL_SOURCE_TAGS = {CARDIO_TAG, EDA_TAG, *TEMPERATURE_TAGS}
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
ALGORITHM_VERSION = "physiological-pattern-score-v4"

MODALITY_WEIGHTS = {"C": 0.40, "E": 0.40, "T": 0.20}
# Plain-language naming for the one-line suggestion headline.
TAG_NAMES = {"HR": "Heart rate", "EA": "EDA", "TH": "Temperature", "T1": "Temperature"}
# A heart-rate change this large within one scoring window is almost always a sensor artefact.
HR_GLITCH_BPM = 30.0
TAG_UNITS = {"HR": ("bpm", 0), "EA": ("µS", 2), "TH": ("°C", 1), "T1": ("°C", 1)}

MODALITY_LABELS = {
    "C": "HR",
    "E": "EDA",
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
    # Source tag, raw feature values, block medians and z-scores behind the score.
    detail: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CandidateSegment:
    """Store one non-overlapping constant-strength segment for a modality."""

    start: float
    end: float
    modality: str
    strength: float
    detail: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class IntervalScore:
    """Store a fused candidate interval and the values exposed in its centroid."""

    start: float
    end: float
    score: float
    sample_count: int
    fused_peak: float
    modality_peaks: dict[str, float]
    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    headline: dict[str, Any] | None = None


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
            winner = _shift_interval(local_winner[0], partition_start)
            partition_winners.append(
                replace(winner, headline=_headline(winner, series, partition_start, partition_end))
            )

    selected = _select_non_overlapping(
        partition_winners, min(SUGGESTION_LIMIT, max(0, n_clusters))
    )
    return _serialize_result(selected)


def _headline(
    interval: IntervalScore,
    series: dict[str, tuple[FloatArray, FloatArray]],
    block_start: float,
    block_end: float,
) -> dict[str, Any] | None:
    """Describe the single largest contributor to a suggestion in real units.

    The driving modality is the one with the largest weighted envelope peak,
    and within it the feature with the largest weighted robust z-score. The
    comparison value is that feature's median across the same partition, so
    the sentence restates exactly what the scorer compared.
    """
    candidates = [
        (MODALITY_WEIGHTS[name] * interval.modality_peaks[name], name)
        for name in interval.details
        if interval.modality_peaks.get(name, 0) > 0
    ]
    if not candidates:
        return None
    modality = max(candidates)[1]
    detail = interval.details[modality]
    tag = detail["tag"]
    feature, info = max(
        detail["features"].items(),
        key=lambda item: detail["weights"][item[0]] * abs(item[1]["z"]),
    )
    name = TAG_NAMES[tag]
    unit, decimals = TAG_UNITS[tag]

    def number(value: float, signed: bool = False) -> str:
        return f"{value:{'+' if signed else ''}.{decimals}f}"

    above = info["z"] >= 0
    value, usual = info["value"], info["usual"]
    length = detail["window"][1] - detail["window"][0]
    if feature == "median":
        title = f"{name} {'higher' if above else 'lower'}"
        text = f"{number(value)} vs usual {number(usual)} {unit}"
    elif feature == "slope":
        title = f"{name} {'rising' if above else 'falling'} fast"
        text = f"{number(value * length, True)} {unit} in {length:g}s"
    elif feature == "delta":
        title = f"{name} {'jumped up' if above else 'dropped'}"
        text = f"{number(value, True)} {unit}"
    else:
        # Spread differences are tiny in µS, so compare standard deviations as a ratio.
        ratio = math.sqrt(max(value, 0.0) / usual) if usual > 0 else float("inf")
        title = f"{name} {'more jumpy' if above else 'steadier'}"
        text = f"{ratio:.1f}× usual movement" if math.isfinite(ratio) else "moving after a flat stretch"
    change = value * length if feature == "slope" else value if feature == "delta" else 0.0
    if tag == CARDIO_TAG and abs(change) > HR_GLITCH_BPM:
        text += " · possible sensor glitch"

    window = detail["window"]
    # Mark the signals whose scorer envelope actually contributed to this suggestion.
    flagged = {
        detail_item["tag"]
        for name, detail_item in interval.details.items()
        if interval.modality_peaks.get(name, 0) > 0
    }
    # Heart rate and EDA are always shown for context; temperature only when it
    # helped pick this stretch. Contributing signals come first.
    tags = [CARDIO_TAG, EDA_TAG, *(item for item in TEMPERATURE_TAGS if item in flagged)]
    signals = [
        summary
        for other in sorted(tags, key=lambda item: item not in flagged)
        if (summary := _signal_summary(series, other, window, block_start, block_end))
    ]
    for item in signals:
        item["flagged"] = item["tag"] in flagged
    band = next((item["band"] for item in signals if item["tag"] == tag), None)
    if band is None and (summary := _signal_summary(series, tag, window, block_start, block_end)):
        band = summary["band"]
    return {
        "tag": tag,
        "feature": feature,
        "title": title,
        "text": text,
        "window": window,
        "block": [block_start, block_end],
        "band": band,
        "signals": signals,
    }


# Skin-conductance responses are usually counted from about 0.05 µS upward.
SCR_MIN_MICROSIEMENS = 0.05
SCR_LARGE_MICROSIEMENS = 0.2
SCR_BASELINE_SECONDS = 30.0


def _sweat_response(
    times: FloatArray, values: FloatArray, inside_window: FloatArray, window: list[float]
) -> dict[str, Any] | None:
    """Describe EDA as a quick rise or fall against the 30 s just before.

    EDA drifts slowly over minutes, so a level comparison with the whole block
    hides the short sweat responses people can see on the curve.
    """
    before = values[(times >= window[0] - SCR_BASELINE_SECONDS) & (times < window[0])]
    if before.size < MIN_WINDOW_SAMPLES:
        return None
    low, usual, high = (float(v) for v in np.percentile(before, [25.0, 50.0, 75.0]))
    rise = float(np.max(inside_window)) - usual
    fall = float(np.min(inside_window)) - usual
    if rise >= SCR_MIN_MICROSIEMENS and rise >= -fall:
        value, trend = usual + rise, "up2" if rise >= SCR_LARGE_MICROSIEMENS else "up"
    elif fall <= -SCR_MIN_MICROSIEMENS:
        value, trend = usual + fall, "down2" if fall <= -SCR_LARGE_MICROSIEMENS else "down"
    else:
        value, trend = float(np.median(inside_window)), "steady"
    return {
        "tag": EDA_TAG,
        "value": value,
        "usual": usual,
        "band": [low, high],
        "trend": trend,
        "baseline": "before",
    }


def _signal_summary(
    series: dict[str, tuple[FloatArray, FloatArray]],
    tag: str,
    window: list[float],
    block_start: float,
    block_end: float,
) -> dict[str, Any] | None:
    """Compare one signal's level in the trigger window with its usual block level.

    The usual range is the block's interquartile range (the grey band in the
    UI); the arrow counts how far outside it the window's median sits, in
    robust spread units, so the arrow and the band always agree.
    """
    if tag not in series:
        return None
    times, values = series[tag]
    inside_block = values[(times >= block_start) & (times < block_end)]
    inside_window = values[(times >= window[0]) & (times < window[1])]
    if inside_block.size < MIN_WINDOW_SAMPLES or inside_window.size < MIN_WINDOW_SAMPLES:
        return None
    if tag == EDA_TAG:
        summary = _sweat_response(times, values, inside_window, window)
        if summary is not None:
            return summary
    low, usual, high = (float(v) for v in np.percentile(inside_block, [25.0, 50.0, 75.0]))
    value = float(np.median(inside_window))
    spread = max((high - low) / 1.349, 1e-9)
    level = (value - usual) / spread
    trend = "up2" if level >= 2 else "up" if level >= 1 else "down2" if level <= -2 else "down" if level <= -1 else "steady"
    return {
        "tag": tag,
        "value": value,
        "usual": usual,
        "band": [low, high],
        "trend": trend,
        "baseline": "block",
    }


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
            features, {"median": 0.5, "slope": 0.3, "delta": 0.2}, CARDIO_TAG
        )

    eda = series.get(EDA_TAG)
    if eda is not None:
        features = _extract_features(
            eda, duration_seconds, 60.0, 15.0, ("median", "slope", "variance")
        )
        modality_windows["E"] = _score_feature_windows(
            features, {"median": 0.4, "slope": 0.2, "variance": 0.4}, EDA_TAG
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
            _score_feature_windows(
                features, {"slope": 0.5, "delta": 0.3, "median": 0.2}, tag
            )
        )
    if temperature_channels:
        modality_windows["T"] = _average_window_scores(temperature_channels)

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
    return replace(
        interval,
        start=interval.start + offset,
        end=interval.end + offset,
        details={
            name: {**detail, "window": [detail["window"][0] + offset, detail["window"][1] + offset]}
            for name, detail in interval.details.items()
        },
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
    windows: list[dict[str, float]], weights: dict[str, float], tag: str | None = None
) -> list[ScoredWindow]:
    """Combine absolute robust feature z-scores into modality window scores."""
    if not windows:
        return []
    raw = {name: np.asarray([window[name] for window in windows], dtype="f8") for name in weights}
    standardized = {name: _robust_z(values) for name, values in raw.items()}
    usual = {name: float(np.median(values)) for name, values in raw.items()}
    return [
        ScoredWindow(
            start=window["start"],
            end=window["end"],
            score=float(
                sum(weight * abs(standardized[name][index]) for name, weight in weights.items())
            ),
            detail=None if tag is None else {
                "tag": tag,
                "window": [window["start"], window["end"]],
                "weights": weights,
                "features": {
                    name: {
                        "value": float(raw[name][index]),
                        "usual": usual[name],
                        "z": float(standardized[name][index]),
                    }
                    for name in weights
                },
            },
        )
        for index, window in enumerate(windows)
    ]


def _average_window_scores(channels: list[list[ScoredWindow]]) -> list[ScoredWindow]:
    """Average available temperature-channel scores for matching windows."""
    grouped: dict[tuple[float, float], list[ScoredWindow]] = {}
    for channel in channels:
        for window in channel:
            grouped.setdefault((window.start, window.end), []).append(window)
    # The averaged score is explained through its most deviant channel.
    return [
        ScoredWindow(
            start=start,
            end=end,
            score=float(np.mean([window.score for window in windows])),
            detail=max(windows, key=lambda window: window.score).detail,
        )
        for (start, end), windows in sorted(grouped.items())
    ]


def _candidate_segments(modality: str, windows: list[ScoredWindow]) -> list[CandidateSegment]:
    """Convert qualifying overlapping windows into a maximum-strength envelope."""
    if not windows:
        return []
    scores = np.asarray([window.score for window in windows], dtype="f8")
    threshold = float(np.percentile(scores, PERCENTILE_THRESHOLD))
    ordered = np.sort(scores)
    qualifying: list[tuple[ScoredWindow, float]] = []  # (window, strength)
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
        strength, source = max(
            (
                (candidate_strength, window)
                for window, candidate_strength in qualifying
                if window.start <= midpoint < window.end
            ),
            key=lambda item: item[0],
            default=(0.0, None),
        )
        if strength <= 0:
            continue
        previous = segments[-1] if segments else None
        if (
            previous is not None
            and math.isclose(previous.end, start)
            and math.isclose(previous.strength, strength)
        ):
            segments[-1] = replace(previous, end=end)
        else:
            segments.append(
                CandidateSegment(
                    start, end, modality, strength, source.detail if source else None
                )
            )
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
        # Remember the strongest overlapping window per modality to explain the score.
        strongest: dict[str, CandidateSegment] = {}
        for segment in segments:
            if segment.detail is None or segment.end <= start or segment.start >= end:
                continue
            current = strongest.get(segment.modality)
            if current is None or segment.strength > current.strength:
                strongest[segment.modality] = segment
        candidates.append(
            IntervalScore(
                start=start,
                end=end,
                score=score,
                sample_count=int(values.size),
                fused_peak=float(np.max(values)),
                modality_peaks=modality_peaks,
                details={name: segment.detail for name, segment in strongest.items()},
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
                    # Retain the legacy field for frontend compatibility. Movement is
                    # no longer loaded or scored by this algorithm version.
                    "movement_peak": 0.0,
                    "temperature_peak": item.modality_peaks["T"],
                },
                "label": (
                    f"Suggestion #{suggestion_rank[(item.start, item.end)]}"
                    f" · {' + '.join(modalities)}"
                ),
                "headline": item.headline,
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
