from __future__ import annotations

import numpy as np
import pytest

from fl_activity_recorder.biosignal_clustering import (
    CandidateSegment,
    IntervalScore,
    ScoredWindow,
    _candidate_segments,
    _extract_features,
    _fuse_segments,
    _motion_energy_series,
    _robust_z,
    _score_intervals,
    _score_series,
    _select_non_overlapping,
    _serialize_result,
    _slice_series,
    _window_starts,
    compute_biosignal_clusters,
)


class _FakeSignalStore:
    """Provide deterministic in-memory points through the LiveSignalStore query shape."""

    def __init__(self, points: dict[str, list[list[float]]]) -> None:
        """Store points by EmotiBit type tag."""
        self.points = points

    def query(
        self, session_id: str, type_tag: str, start: float, end: float, pixels: int
    ) -> dict[str, object]:
        """Return points for a known tag and raise for unavailable channels."""
        del session_id, start, end, pixels
        if type_tag not in self.points:
            raise KeyError(type_tag)
        return {"points": self.points[type_tag]}


def _partitioned_heart_rate_points(duration_seconds: int) -> list[list[float]]:
    """Build repeatable heart-rate anomalies within every five-minute partition."""
    points = []
    peaks = ((30.0, 42.0, 35.0), (140.0, 152.0, 50.0), (250.0, 262.0, 65.0))
    for index in range(duration_seconds):
        timestamp = index + 0.5
        local_time = timestamp % 300.0
        value = 70.0 + 0.15 * (index % 7)
        for start, end, increase in peaks:
            if start <= local_time < end:
                value += increase
        points.append([timestamp, value])
    return points


def test_complete_windows_and_real_time_features() -> None:
    """Feature extraction uses complete windows and actual timestamps."""
    times = np.arange(0.5, 20.0, 1.0)
    values = 2.0 * times + 1.0

    assert _window_starts(19.9, 10.0, 5.0) == [0.0, 5.0]
    features = _extract_features(
        (times, values), 20.0, 10.0, 10.0, ("median", "slope", "delta")
    )

    assert len(features) == 2
    assert features[0]["slope"] == pytest.approx(2.0)
    assert features[0]["delta"] == 0.0
    assert features[1]["delta"] == pytest.approx(20.0)


def test_missing_window_resets_delta_and_zero_mad_is_safe() -> None:
    """A coverage gap breaks delta continuity and zero MAD produces finite zeros."""
    times = np.concatenate((np.arange(0.5, 10.0), np.arange(20.5, 30.0)))
    values = np.concatenate((np.arange(10.0), np.arange(10.0) + 100.0))

    features = _extract_features(
        (times, values), 30.0, 10.0, 10.0, ("median", "delta")
    )

    assert [feature["start"] for feature in features] == [0.0, 20.0]
    assert [feature["delta"] for feature in features] == [0.0, 0.0]
    assert np.array_equal(_robust_z(np.asarray([4.0, 4.0, 4.0])), np.zeros(3))


def test_motion_energy_requires_and_combines_six_axes() -> None:
    """Motion energy is the RMS of aligned robust-standardized axes."""
    times = np.arange(6.0)
    values = np.asarray([-2.0, -1.0, 0.0, 1.0, 2.0, 8.0])
    series = {
        tag: (times, values * (index + 1))
        for index, tag in enumerate(("AX", "AY", "AZ", "GX", "GY", "GZ"))
    }

    result = _motion_energy_series(series)

    assert result is not None
    result_times, energy = result
    assert np.array_equal(result_times, times)
    assert np.allclose(energy, np.abs(_robust_z(values)))
    del series["GZ"]
    assert _motion_energy_series(series) is None


def test_partition_slicing_is_half_open_and_uses_local_time() -> None:
    """A boundary sample belongs only to the following partition at local time zero."""
    series = {
        "HR": (
            np.asarray([299.5, 300.0, 300.5]),
            np.asarray([70.0, 80.0, 90.0]),
        )
    }

    first = _slice_series(series, 0.0, 300.0)
    second = _slice_series(series, 300.0, 600.0)

    assert first["HR"][0].tolist() == [299.5]
    assert first["HR"][1].tolist() == [70.0]
    assert second["HR"][0].tolist() == [0.0, 0.5]
    assert second["HR"][1].tolist() == [80.0, 90.0]


def test_percentile_strength_and_overlap_envelope() -> None:
    """The upper decile maps linearly and overlapping windows keep maximum strength."""
    windows = [ScoredWindow(100.0 + index, 101.0 + index, float(index)) for index in range(9)]
    windows.extend((ScoredWindow(10.0, 20.0, 9.0), ScoredWindow(15.0, 25.0, 10.0)))

    segments = _candidate_segments("C", windows)

    assert [(item.start, item.end) for item in segments] == [(10.0, 15.0), (15.0, 25.0)]
    assert segments[0].strength == pytest.approx(1.0 / 11.0)
    assert segments[1].strength == 1.0


def test_fusion_interval_score_and_greedy_selection() -> None:
    """Fusion, top-quartile scoring, and non-overlap selection follow the design."""
    segments = [
        CandidateSegment(0.0, 4.0, "C", 1.0),
        CandidateSegment(2.0, 6.0, "E", 1.0),
        CandidateSegment(6.0, 8.0, "M", 1.0),
    ]
    grid, envelopes, fused = _fuse_segments(segments, 8.0)

    assert fused.tolist() == pytest.approx([0.3, 0.3, 0.72, 0.72, 0.3, 0.3, 0.25, 0.25])
    candidates = _score_intervals(segments, grid, envelopes, fused)
    assert candidates[0].score == pytest.approx(0.576)

    selected = _select_non_overlapping(candidates, 3)
    assert [(item.start, item.end) for item in selected] == [(0.0, 4.0), (6.0, 8.0)]


def test_serialization_keeps_frontend_contract_and_warmest_high_score() -> None:
    """The existing response shape ranks the highest score at the largest color id."""
    low = IntervalScore(0.0, 5.0, 0.2, 5, 0.25, {"C": 1.0, "E": 0.0, "M": 0.0, "T": 0.0})
    high = IntervalScore(10.0, 15.0, 0.8, 5, 1.0, {"C": 0.0, "E": 1.0, "M": 1.0, "T": 0.0})

    result = _serialize_result([high, low])

    assert set(result) == {"algorithm_version", "window_seconds", "windows", "clusters"}
    assert result["window_seconds"] == 1.0
    assert result["windows"] == [
        {"start_seconds": 0.0, "end_seconds": 5.0, "cluster": 0},
        {"start_seconds": 10.0, "end_seconds": 15.0, "cluster": 1},
    ]
    assert result["clusters"][1]["label"] == "Suggestion #1 · EDA + Movement"
    assert set(result["clusters"][1]) == {"id", "size", "centroid", "label"}


def test_public_scorer_handles_short_constant_and_implausible_sessions() -> None:
    """Sessions without sufficient valid variation return the stable empty contract."""
    short = _FakeSignalStore({"HR": [[index + 0.5, 70.0 + index] for index in range(5)]})
    constant = _FakeSignalStore({"HR": [[index + 0.5, 70.0] for index in range(30)]})
    implausible = _FakeSignalStore({"HR": [[index + 0.5, 500.0] for index in range(30)]})

    for store, duration in ((short, 5.0), (constant, 30.0), (implausible, 30.0)):
        result = compute_biosignal_clusters(store, "session", duration)  # type: ignore[arg-type]
        assert result == {
            "algorithm_version": "physiological-pattern-score-v3",
            "window_seconds": 1.0,
            "windows": [],
            "clusters": [],
        }


def test_five_minute_partitions_contribute_at_most_one_suggestion() -> None:
    """Full and partial partitions rank locally before returning session times."""
    duration = 720
    points = _partitioned_heart_rate_points(duration)
    array = np.asarray(points, dtype="f8")
    local_candidates = _score_series({"HR": (array[:300, 0], array[:300, 1])}, 300.0)
    store = _FakeSignalStore({"HR": points})

    result = compute_biosignal_clusters(store, "session", float(duration))  # type: ignore[arg-type]

    assert len(local_candidates) > 1
    assert result["algorithm_version"] == "physiological-pattern-score-v3"
    windows = result["windows"]
    assert len(windows) == 3
    assert [int(window["start_seconds"] // 300) for window in windows] == [0, 1, 2]
    assert all(
        window["end_seconds"] <= min((index + 1) * 300.0, float(duration))
        for index, window in enumerate(windows)
    )

    limited = compute_biosignal_clusters(  # type: ignore[arg-type]
        store, "session", float(duration), n_clusters=2
    )
    assert len(limited["windows"]) == 2


def test_partition_winners_keep_the_global_ten_suggestion_limit() -> None:
    """More than ten productive partitions are reduced to the global top ten."""
    duration = 3_600
    store = _FakeSignalStore({"HR": _partitioned_heart_rate_points(duration)})

    result = compute_biosignal_clusters(store, "session", float(duration))  # type: ignore[arg-type]

    assert len(result["windows"]) == 10
    partition_ids = {int(window["start_seconds"] // 300) for window in result["windows"]}
    assert len(partition_ids) == 10


def test_single_temperature_channel_is_supported_and_deterministic() -> None:
    """One available temperature channel can produce repeatable suggestions."""
    points = []
    for index in range(300):
        timestamp = index + 0.5
        value = 30.0 + 0.002 * timestamp + 0.01 * (index % 11)
        if 150.0 <= timestamp < 210.0:
            value += 5.0
        points.append([timestamp, value])
    store = _FakeSignalStore({"T1": points})

    first = compute_biosignal_clusters(store, "session", 300.0)  # type: ignore[arg-type]
    second = compute_biosignal_clusters(store, "session", 300.0)  # type: ignore[arg-type]

    assert first == second
    assert first["windows"]
    assert all("Temperature" in cluster["label"] for cluster in first["clusters"])
