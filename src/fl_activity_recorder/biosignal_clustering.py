from __future__ import annotations

import math
from typing import Any

import numpy as np
from sklearn.cluster import KMeans  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from .live_signals import LiveSignalStore

# EDA and heart rate get both mean and spread; the rest contribute one summary
# statistic each so the feature vector stays compact and every entry maps to a
# single, nameable quantity in `FEATURE_DISPLAY` below.
PRIMARY_TAGS = ("EA", "HR")
SECONDARY_TAGS = ("SA", "SR", "SF", "T1", "TH")
MOTION_GROUPS = {
    "movement_energy": ("AX", "AY", "AZ"),
    "rotation_energy": ("GX", "GY", "GZ"),
    "magnetic_energy": ("MX", "MY", "MZ"),
}
MOTION_TAGS = {tag for group in MOTION_GROUPS.values() for tag in group}
ALL_SOURCE_TAGS = {*PRIMARY_TAGS, *SECONDARY_TAGS, *MOTION_TAGS}

# Discard samples outside physiologically plausible ranges before they can pull
# a window's average toward a sensor glitch.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "HR": (30.0, 220.0),
    "EA": (0.0, 100.0),
}

FEATURE_DISPLAY = {
    "EA_mean": "EDA",
    "EA_std": "EDA variability",
    "HR_mean": "HR",
    "HR_std": "HR variability",
    "SA_mean": "SCR amplitude",
    "SR_mean": "SCR rise time",
    "SF_mean": "SCR frequency",
    "T1_mean": "PPG temp",
    "TH_mean": "skin temp",
    "movement_energy": "movement",
    "rotation_energy": "rotation",
    "magnetic_energy": "field motion",
}
FEATURE_NAMES = [f"{tag}_mean" for tag in ("EA", "HR")] + [
    f"{tag}_std" for tag in ("EA", "HR")
] + [f"{tag}_mean" for tag in SECONDARY_TAGS] + list(MOTION_GROUPS)

# A window must have at least this share of its features present (not dropped
# by coverage or range checks) to be usable at all.
MIN_FEATURE_COVERAGE = 0.6
# A channel must cover at least this share of its own session-wide average
# sampling density within a window, or it is treated as missing for that window.
MIN_SAMPLE_COVERAGE_RATIO = 0.5
TARGET_WINDOW_COUNT = 28
WINDOW_SECONDS_MIN = 6.0
WINDOW_SECONDS_MAX = 30.0
OVERLAP_RATIO = 0.5

# Rough "how activated does this look" weighting used only to order clusters
# calm -> activated for display (color scale, headline word). Signs follow
# common arousal literature: faster/more variable HR, higher/more reactive
# EDA, and more movement all read as more activated.
AROUSAL_WEIGHTS: dict[str, float] = {
    "HR_mean": 1.0,
    "HR_std": 0.4,
    "EA_mean": 0.6,
    "EA_std": 0.4,
    "SA_mean": 0.4,
    "SF_mean": 0.4,
    "movement_energy": 0.6,
    "rotation_energy": 0.3,
    "magnetic_energy": 0.2,
}

EMPTY_RESULT: dict[str, Any] = {"window_seconds": 0.0, "windows": [], "clusters": []}


def compute_biosignal_clusters(
    signals: LiveSignalStore,
    session_id: str,
    duration_seconds: float,
    *,
    window_seconds: float | None = None,
    n_clusters: int = 3,
) -> dict[str, Any]:
    """Cluster overlapping timeline windows by physiological summary statistics.

    Mirrors the FlowNote pattern-suggestion pipeline: overlapping windows sized
    to the session, EDA/HR/SCR/temperature/motion features, windows with too
    little usable data dropped, KMeans over standardized features, and
    consecutive same-cluster windows merged into one region so the UI shows a
    handful of readable segments instead of a stripe of tiny ones.
    """
    if duration_seconds <= 0:
        return {**EMPTY_RESULT, "window_seconds": 0.0}

    resolved_window_seconds = window_seconds or _auto_window_seconds(duration_seconds)

    series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
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
        if tag in PLAUSIBLE_RANGES:
            low, high = PLAUSIBLE_RANGES[tag]
            keep = (values >= low) & (values <= high)
            times, values = times[keep], values[keep]
        if times.size:
            series[tag] = (times, values)

    if not series:
        return {**EMPTY_RESULT, "window_seconds": resolved_window_seconds}

    stride = resolved_window_seconds * (1 - OVERLAP_RATIO)
    window_starts = _window_starts(duration_seconds, resolved_window_seconds, stride)
    average_rates = {tag: len(times) / duration_seconds for tag, (times, _values) in series.items()}

    features = np.full((len(window_starts), len(FEATURE_NAMES)), np.nan)
    for row, start in enumerate(window_starts):
        end = min(duration_seconds, start + resolved_window_seconds)
        stats = _window_stats(series, average_rates, start, end)
        for column, name in enumerate(FEATURE_NAMES):
            if name in stats:
                features[row, column] = stats[name]

    coverage = np.mean(~np.isnan(features), axis=1)
    valid_indices = np.flatnonzero(coverage >= MIN_FEATURE_COVERAGE)
    if len(valid_indices) < 2:
        return {**EMPTY_RESULT, "window_seconds": resolved_window_seconds}

    valid_features = features[valid_indices]
    column_mean = np.nanmean(valid_features, axis=0)
    imputed = np.where(np.isnan(valid_features), column_mean, valid_features)

    resolved_clusters = max(1, min(n_clusters, len(valid_indices)))
    scaler = StandardScaler()
    scaled = scaler.fit_transform(imputed)
    model = KMeans(n_clusters=resolved_clusters, n_init=10, random_state=0).fit(scaled)
    centers = scaler.inverse_transform(model.cluster_centers_)

    # Cluster ids from KMeans are arbitrary; renumber calmest -> most activated
    # so id order (and therefore color and headline) means the same thing on
    # every session instead of depending on random initialization.
    order = sorted(
        range(resolved_clusters), key=lambda cid: _arousal_score(model.cluster_centers_[cid])
    )
    rank_of = {old_id: rank for rank, old_id in enumerate(order)}
    labels = np.array([rank_of[label] for label in model.labels_.tolist()])

    per_window = []
    for window_index, label in zip(valid_indices.tolist(), labels.tolist(), strict=True):
        start = window_starts[window_index]
        per_window.append(
            {
                "window_index": int(window_index),
                "start_seconds": float(start),
                "end_seconds": float(min(duration_seconds, start + resolved_window_seconds)),
                "cluster": int(label),
            }
        )
    windows = _merge_consecutive(per_window)
    clusters = [
        {
            "id": rank,
            "size": int(np.sum(labels == rank)),
            "centroid": dict(
                zip(FEATURE_NAMES, (float(v) for v in centers[old_id]), strict=True)
            ),
            "label": _describe_cluster(model.cluster_centers_[old_id], rank, resolved_clusters),
        }
        for rank, old_id in enumerate(order)
    ]
    return {"window_seconds": resolved_window_seconds, "windows": windows, "clusters": clusters}


def _arousal_score(scaled_center: np.ndarray) -> float:
    return float(
        sum(
            AROUSAL_WEIGHTS.get(name, 0.0) * scaled_center[index]
            for index, name in enumerate(FEATURE_NAMES)
        )
    )


def _auto_window_seconds(duration_seconds: float) -> float:
    """Pick a window width that yields roughly `TARGET_WINDOW_COUNT` windows."""
    raw = duration_seconds / TARGET_WINDOW_COUNT
    return min(WINDOW_SECONDS_MAX, max(WINDOW_SECONDS_MIN, raw))


def _window_starts(duration_seconds: float, window_seconds: float, stride: float) -> list[float]:
    if duration_seconds <= window_seconds:
        return [0.0]
    count = max(1, math.floor((duration_seconds - window_seconds) / stride) + 1)
    return [round(index * stride, 6) for index in range(count)]


def _window_stats(
    series: dict[str, tuple[np.ndarray, np.ndarray]],
    average_rates: dict[str, float],
    start: float,
    end: float,
) -> dict[str, float]:
    span = max(1e-6, end - start)
    means: dict[str, float] = {}
    stds: dict[str, float] = {}
    for tag, (times, values) in series.items():
        mask = (times >= start) & (times < end)
        count = int(np.count_nonzero(mask))
        expected = max(1.0, average_rates[tag] * span)
        if count == 0 or count / expected < MIN_SAMPLE_COVERAGE_RATIO:
            continue
        windowed = values[mask]
        means[tag] = float(np.mean(windowed))
        stds[tag] = float(np.std(windowed))

    stats: dict[str, float] = {}
    for tag in PRIMARY_TAGS:
        if tag in means:
            stats[f"{tag}_mean"] = means[tag]
            stats[f"{tag}_std"] = stds[tag]
    for tag in SECONDARY_TAGS:
        if tag in means:
            stats[f"{tag}_mean"] = means[tag]
    # Motion channels are summarized by within-window variability (how much the
    # axis moved), not its raw mean (which mostly reflects limb orientation).
    for feature_name, axes in MOTION_GROUPS.items():
        present = [stds[axis] for axis in axes if axis in stds]
        if len(present) == len(axes):
            stats[feature_name] = float(np.mean(present))
    return stats


def _merge_consecutive(per_window: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge runs of same-cluster, index-adjacent windows into one region."""
    merged: list[dict[str, Any]] = []
    for window in per_window:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous["cluster"] == window["cluster"]
            and window["window_index"] - previous["_last_index"] == 1
        ):
            previous["end_seconds"] = window["end_seconds"]
            previous["_last_index"] = window["window_index"]
        else:
            merged.append(
                {
                    "start_seconds": window["start_seconds"],
                    "end_seconds": window["end_seconds"],
                    "cluster": window["cluster"],
                    "_last_index": window["window_index"],
                }
            )
    for region in merged:
        del region["_last_index"]
    return merged


def _describe_cluster(
    scaled_center: np.ndarray,
    rank: int,
    total_clusters: int,
    *,
    top_k: int = 2,
    threshold: float = 0.25,
) -> str:
    """Turn a standardized cluster centroid into a short, plain-language label.

    Leads with a one-word energy reading (derived from the same calm ->
    activated ordering used for color) so the label is scannable before
    reading the supporting feature detail.
    """
    if total_clusters <= 1:
        headline = "Baseline"
    else:
        position = rank / (total_clusters - 1)
        headline = "Calmer" if position < 0.34 else "Activated" if position > 0.66 else "Neutral"

    order = np.argsort(-np.abs(scaled_center))
    parts: list[str] = []
    for index in order[:top_k]:
        magnitude = scaled_center[index]
        if abs(magnitude) < threshold:
            continue
        name = FEATURE_NAMES[index]
        display = FEATURE_DISPLAY.get(name, name)
        parts.append(f"{display} {'high' if magnitude > 0 else 'low'}")
    return f"{headline} · {', '.join(parts)}" if parts else headline
