from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from .database import Database

NUMERIC_LSL_FORMATS = {1, 2, 4, 5, 6, 7}
SIGNALS = {
    "EA": ("EDA", "EDA"),
    "EL": ("EDA level", "EDA"),
    "ER": ("EDA resistance", "EDA"),
    "PI": ("PPG infrared", "PPG"),
    "PR": ("PPG red", "PPG"),
    "PG": ("PPG green", "PPG"),
    "T1": ("PPG temperature", "Temperature"),
    "TH": ("Thermopile temperature", "Temperature"),
    "HR": ("Heart rate", "Heart"),
    "BI": ("Inter-beat interval", "Heart"),
    "SA": ("SCR amplitude", "SCR"),
    "SR": ("SCR rise time", "SCR"),
    "SF": ("SCR frequency", "SCR"),
    **{tag: (f"Accelerometer {tag[-1]}", "Motion") for tag in ("AX", "AY", "AZ")},
    **{tag: (f"Gyroscope {tag[-1]}", "Motion") for tag in ("GX", "GY", "GZ")},
    **{tag: (f"Magnetometer {tag[-1]}", "Motion") for tag in ("MX", "MY", "MZ")},
}


@dataclass(frozen=True, slots=True)
class SignalDescriptor:
    """Describe one numeric channel within an EmotiBit LSL stream."""

    track_id: str
    type_tag: str
    display_name: str
    group_name: str
    unit: str | None
    nominal_rate: float
    stream_name: str
    source_id: str
    channel_index: int
    channel_format: str

    def public(self) -> dict[str, Any]:
        """Return JSON-compatible stream metadata."""
        return asdict(self)


class LiveSignalStore:
    """Persist live LSL samples as immutable, indexed NumPy chunks."""

    def __init__(
        self,
        database: Database,
        root: Path,
        *,
        chunk_samples: int = 4096,
        flush_seconds: float = 1.0,
    ) -> None:
        self.database = database
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.chunk_samples = chunk_samples
        self.flush_seconds = flush_seconds
        self._lock = threading.RLock()
        self._session_id: str | None = None
        self._zero = 0.0
        self._buffers: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._descriptors: dict[str, SignalDescriptor] = {}
        self._sequences: dict[str, int] = defaultdict(int)
        self._last_flush: dict[str, float] = defaultdict(time.monotonic)

    def start_session(self, session_id: str, timeline_zero_lsl: float) -> None:
        """Begin accepting live samples for one authoritative session."""
        with self._lock:
            if self._session_id is not None:
                raise RuntimeError("Live EmotiBit storage is already active")
            target = self._signal_root(session_id)
            target.mkdir(parents=True, exist_ok=True)
            self._session_id = session_id
            self._zero = timeline_zero_lsl
            self._buffers.clear()
            self._descriptors.clear()
            self._sequences.clear()
            self._last_flush.clear()
            self.database.upsert_asset(
                session_id,
                "emotibit",
                "recording",
                relative_path=str(target.relative_to(self.root)),
                started_lsl=timeline_zero_lsl,
                metadata_json={"source": "live_lsl"},
            )

    def append(
        self,
        descriptor: SignalDescriptor,
        timestamps: Sequence[float],
        values: Sequence[float],
    ) -> list[list[float]]:
        """Validate, buffer, and periodically seal one channel's live samples."""
        if len(timestamps) != len(values):
            raise ValueError("Signal timestamps and values must have equal lengths")
        accepted: list[list[float]] = []
        with self._lock:
            if self._session_id is None:
                return accepted
            self._ensure_stream(descriptor)
            buffer = self._buffers[descriptor.track_id]
            previous = buffer[-1][0] if buffer else None
            for source_time, value in zip(timestamps, values, strict=True):
                relative = float(source_time) - self._zero
                numeric = float(value)
                if relative < 0 or not math.isfinite(relative) or not math.isfinite(numeric):
                    continue
                if previous is not None and relative < previous:
                    continue
                buffer.append((relative, numeric))
                accepted.append([relative, numeric])
                previous = relative
            now = time.monotonic()
            if len(buffer) >= self.chunk_samples or (
                buffer and now - self._last_flush[descriptor.track_id] >= self.flush_seconds
            ):
                self._flush_track(descriptor.track_id)
        return accepted

    def finish_session(self, ended_lsl: float, *, interrupted: bool, degraded: bool) -> None:
        """Stop accepting samples, flush every track, and publish terminal status."""
        with self._lock:
            session_id = self._session_id
            if session_id is None:
                return
            for track_id in tuple(self._buffers):
                self._flush_track(track_id)
            streams = self.database.list_signal_streams(session_id)
            status = "interrupted" if interrupted or not streams else "ready"
            self.database.upsert_asset(
                session_id,
                "emotibit",
                status,
                ended_lsl=ended_lsl,
                metadata_json={"source": "live_lsl", "stream_count": len(streams)},
            )
            self.database.set_sync_quality(
                session_id,
                "degraded" if degraded or status != "ready" else "aligned_unverified",
            )
            self._session_id = None
            self._buffers.clear()
            self._descriptors.clear()
            self._sequences.clear()

    def query(
        self, session_id: str, type_tag: str, start: float, end: float, pixels: int
    ) -> dict[str, Any]:
        """Return raw or pixel-level min/max points for a visible timeline window."""
        stream = next(
            (
                item
                for item in self.database.list_signal_streams(session_id)
                if item["type_tag"] == type_tag
            ),
            None,
        )
        if stream is None:
            raise KeyError(type_tag)
        chunks = self.database.list_signal_chunks(session_id, type_tag, start, end)
        count = sum(int(item["sample_count"]) for item in chunks)
        if count <= max(1, pixels) * 4:
            points: list[list[float]] = []
            for chunk in chunks:
                array = np.load(self._contained_path(str(chunk["relative_path"])), mmap_mode="r")
                selected = array[(array[:, 0] >= start) & (array[:, 0] <= end)]
                points.extend([[float(row[0]), float(row[1])] for row in selected])
                _close_memmap(array)
            return {"type_tag": type_tag, "mode": "raw", "points": points}
        # Storage chunks are an implementation detail and can span several
        # minutes. Reducing each chunk to one point makes a continuous signal
        # look like a few isolated vertical strokes at wide zoom levels. Read
        # the intersecting samples and aggregate them into screen-width time
        # buckets instead, preserving the visible envelope without returning
        # the entire recording.
        bucket_count = max(1, pixels)
        bucket_width = (end - start) / bucket_count
        minima = np.full(bucket_count, np.inf)
        maxima = np.full(bucket_count, -np.inf)
        for chunk in chunks:
            array = np.load(self._contained_path(str(chunk["relative_path"])), mmap_mode="r")
            selected = array[(array[:, 0] >= start) & (array[:, 0] <= end)]
            if len(selected):
                indices = np.floor((selected[:, 0] - start) / bucket_width).astype(np.int64)
                indices = np.clip(indices, 0, bucket_count - 1)
                np.minimum.at(minima, indices, selected[:, 1])
                np.maximum.at(maxima, indices, selected[:, 1])
            _close_memmap(array)
        points = [
            [start + (index + 0.5) * bucket_width, float(minima[index]), float(maxima[index])]
            for index in np.flatnonzero(np.isfinite(minima))
        ]
        return {"type_tag": type_tag, "mode": "minmax", "points": points}

    def _ensure_stream(self, descriptor: SignalDescriptor) -> None:
        if descriptor.track_id in self._descriptors:
            return
        if self._session_id is None:
            return
        directory = self._signal_root(self._session_id) / descriptor.track_id
        directory.mkdir(parents=True, exist_ok=True)
        self._descriptors[descriptor.track_id] = descriptor
        self.database.ensure_live_signal_stream(
            self._session_id,
            {
                "type_tag": descriptor.track_id,
                "display_name": descriptor.display_name,
                "group_name": descriptor.group_name,
                "unit": descriptor.unit,
                "nominal_rate": descriptor.nominal_rate,
                "relative_path": str(directory.relative_to(self.root)),
                "metadata": {"source": "live_lsl", **descriptor.public()},
            },
        )

    def _flush_track(self, track_id: str) -> None:
        if self._session_id is None or not self._buffers[track_id]:
            return
        rows = self._buffers[track_id]
        self._buffers[track_id] = []
        sequence = self._sequences[track_id]
        directory = self._signal_root(self._session_id) / track_id
        final = directory / f"chunk-{sequence:06d}.npy"
        temporary = directory / f".chunk-{sequence:06d}.tmp"
        array = np.asarray(rows, dtype="f8")
        with temporary.open("wb") as handle:
            np.save(handle, array)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, final)
        self.database.add_signal_chunk(
            self._session_id,
            {
                "type_tag": track_id,
                "sequence": sequence,
                "start_seconds": float(array[0, 0]),
                "end_seconds": float(array[-1, 0]),
                "sample_count": len(array),
                "min_value": float(np.min(array[:, 1])),
                "max_value": float(np.max(array[:, 1])),
                "relative_path": str(final.relative_to(self.root)),
            },
        )
        self._sequences[track_id] = sequence + 1
        self._last_flush[track_id] = time.monotonic()

    def _signal_root(self, session_id: str) -> Path:
        target = (self.root / session_id / "signals").resolve()
        if self.root not in target.parents:
            raise ValueError("Invalid signal storage path")
        return target

    def _contained_path(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents:
            raise ValueError("Stored signal path escapes the asset root")
        return path


class EmotiBitLslReceiver:
    """Discover numeric EmotiBit LSL streams and route them into active sessions."""

    def __init__(
        self,
        store: LiveSignalStore,
        publish: Callable[[dict[str, Any]], None],
        *,
        stream_prefix: str = "EmotiBit",
        lsl: Any | None = None,
        silence_seconds: float = 2.0,
        batch_seconds: float = 0.25,
    ) -> None:
        self.store = store
        self.publish = publish
        self.stream_prefix = stream_prefix
        self.lsl = lsl if lsl is not None else _load_pylsl()
        self.silence_seconds = silence_seconds
        self.batch_seconds = batch_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._manager: threading.Thread | None = None
        self._workers: dict[str, threading.Thread] = {}
        self._descriptors: dict[str, list[SignalDescriptor]] = {}
        self._last_any_monotonic: float | None = None
        self._last_sample_lsl: float | None = None
        self._last_by_track: dict[str, float] = {}
        self._pending: dict[str, list[list[float]]] = defaultdict(list)
        self._active_session_id: str | None = None
        self._session_tracks: set[str] = set()
        self._degraded = False
        self._global_gap = False
        self._track_gaps: set[str] = set()

    @property
    def ready(self) -> bool:
        """Return whether a matching stream produced a sample recently."""
        with self._lock:
            return self._is_connected(time.monotonic())

    def start(self) -> None:
        """Start background stream discovery and live batching."""
        if self._manager is not None:
            return
        self._stop.clear()
        self._manager = threading.Thread(target=self._run, name="emotibit-lsl", daemon=True)
        self._manager.start()

    def stop(self) -> None:
        """Stop discovery and all inlet workers."""
        self._stop.set()
        if self._manager is not None:
            self._manager.join(timeout=3.0)
        for worker in tuple(self._workers.values()):
            worker.join(timeout=1.0)
        self._manager = None
        self._workers.clear()

    def begin_session(self, session_id: str, timeline_zero_lsl: float) -> None:
        """Attach subsequent LSL samples to a newly-created session."""
        if not self.ready:
            raise RuntimeError("emotibit_lsl_unavailable")
        self.store.start_session(session_id, timeline_zero_lsl)
        with self._lock:
            self._active_session_id = session_id
            self._degraded = False
            self._global_gap = False
            self._track_gaps.clear()
            self._pending.clear()
            now = time.monotonic()
            self._session_tracks = {
                track_id
                for track_id, last_seen in self._last_by_track.items()
                if now - last_seen <= self.silence_seconds
            }
        self._publish_status()

    def finish_session(self, ended_lsl: float, *, interrupted: bool) -> None:
        """Detach and finalize the active session without stopping discovery."""
        with self._lock:
            if self._active_session_id is None:
                return
            degraded = self._degraded or self._global_gap or bool(self._track_gaps)
            self._active_session_id = None
            self._session_tracks.clear()
            self._pending.clear()
        self.store.finish_session(ended_lsl, interrupted=interrupted, degraded=degraded)
        self._publish_status()

    def health(self) -> dict[str, Any]:
        """Return current discovery, freshness, and stream metadata."""
        now = time.monotonic()
        with self._lock:
            silence = (
                None
                if self._last_any_monotonic is None
                else max(0.0, now - self._last_any_monotonic)
            )
            descriptors = [item.public() for group in self._descriptors.values() for item in group]
            return {
                "available": self.lsl is not None,
                "connected": self._is_connected(now),
                "stream_prefix": self.stream_prefix,
                "last_sample_lsl": self._last_sample_lsl,
                "silence_seconds": silence,
                "streams": descriptors,
            }

    def accept_samples(
        self,
        descriptors: Sequence[SignalDescriptor],
        samples: Sequence[Sequence[float]],
        timestamps: Sequence[float],
    ) -> None:
        """Accept one pulled LSL chunk; exposed for deterministic integration tests."""
        if len(samples) != len(timestamps):
            return
        now = time.monotonic()
        with self._lock:
            if descriptors:
                descriptor_key = (
                    f"{descriptors[0].source_id}:{descriptors[0].stream_name}"
                )
                if not any(group == list(descriptors) for group in self._descriptors.values()):
                    self._descriptors[descriptor_key] = list(descriptors)
            self._last_any_monotonic = now
            if timestamps:
                self._last_sample_lsl = float(timestamps[-1])
            active = self._active_session_id is not None
            for descriptor in descriptors:
                self._last_by_track[descriptor.track_id] = now
                if active:
                    self._session_tracks.add(descriptor.track_id)
        if not active:
            return
        for channel, descriptor in enumerate(descriptors):
            values = []
            channel_times = []
            for sample, timestamp in zip(samples, timestamps, strict=True):
                if channel >= len(sample):
                    continue
                try:
                    value = float(sample[channel])
                    source_time = float(timestamp)
                except (TypeError, ValueError):
                    continue
                values.append(value)
                channel_times.append(source_time)
            accepted = self.store.append(descriptor, channel_times, values)
            if accepted:
                with self._lock:
                    self._pending[descriptor.track_id].extend(accepted)

    def _run(self) -> None:
        last_resolve = 0.0
        last_batch = 0.0
        last_status: tuple[bool, bool] | None = None
        while not self._stop.wait(0.05):
            now = time.monotonic()
            if self.lsl is not None and now - last_resolve >= 1.0:
                self._discover()
                last_resolve = now
            if now - last_batch >= self.batch_seconds:
                self._flush_live_batch()
                self._check_gaps(now)
                status = (self._is_connected(now), self._global_gap)
                if status != last_status:
                    self._publish_status()
                    last_status = status
                last_batch = now

    def _discover(self) -> None:
        lsl = self.lsl
        if lsl is None:
            return
        try:
            infos = lsl.resolve_streams(wait_time=0.1)
        except (OSError, RuntimeError):
            return
        for info in infos:
            try:
                name = str(info.name())
                channel_format = int(info.channel_format())
                key = str(info.uid() or info.source_id() or name)
            except (TypeError, ValueError, RuntimeError):
                continue
            if not name.startswith(self.stream_prefix) or channel_format not in NUMERIC_LSL_FORMATS:
                continue
            worker = self._workers.get(key)
            if worker is not None and worker.is_alive():
                continue
            descriptors = describe_stream(info)
            used = {
                item.track_id
                for stream_key, group in self._descriptors.items()
                if stream_key != key
                for item in group
            }
            unique_descriptors = []
            for descriptor in descriptors:
                track_id = descriptor.track_id
                suffix = 2
                while track_id in used:
                    track_id = f"{descriptor.track_id}_{suffix}"
                    suffix += 1
                used.add(track_id)
                unique_descriptors.append(replace(descriptor, track_id=track_id))
            descriptors = unique_descriptors
            self._descriptors[key] = descriptors
            worker = threading.Thread(
                target=self._consume,
                args=(key, info, descriptors),
                name=f"emotibit-inlet-{len(self._workers)}",
                daemon=True,
            )
            self._workers[key] = worker
            worker.start()

    def _consume(self, key: str, info: Any, descriptors: list[SignalDescriptor]) -> None:
        lsl = self.lsl
        if lsl is None:
            return
        try:
            inlet = lsl.StreamInlet(info, max_buflen=5)
            while not self._stop.is_set():
                samples, timestamps = inlet.pull_chunk(timeout=0.2, max_samples=1024)
                if timestamps:
                    self.accept_samples(descriptors, samples, timestamps)
        except (OSError, RuntimeError):
            pass
        finally:
            self._workers.pop(key, None)

    def _flush_live_batch(self) -> None:
        with self._lock:
            if self._active_session_id is None or not self._pending:
                return
            tracks = [
                {"type_tag": track_id, "points": points}
                for track_id, points in self._pending.items()
                if points
            ]
            self._pending = defaultdict(list)
            session_id = self._active_session_id
        if tracks:
            self.publish(
                {"type": "emotibit_samples", "session_id": session_id, "tracks": tracks}
            )

    def _check_gaps(self, now: float) -> None:
        with self._lock:
            session_id = self._active_session_id
            if session_id is None:
                return
            connected = self._is_connected(now)
            if not connected and not self._global_gap:
                self._global_gap = True
                self._degraded = True
                self.store.database.add_diagnostic(
                    session_id=session_id,
                    level="warning",
                    code="emotibit_lsl_disconnected",
                    message="No EmotiBit LSL samples were received for more than two seconds",
                )
            elif connected and self._global_gap:
                self._global_gap = False
                self.store.database.add_diagnostic(
                    session_id=session_id,
                    level="info",
                    code="emotibit_lsl_recovered",
                    message="EmotiBit LSL samples resumed",
                )
            for group in self._descriptors.values():
                for descriptor in group:
                    if descriptor.track_id not in self._session_tracks:
                        continue
                    last = self._last_by_track.get(descriptor.track_id)
                    threshold = max(
                        self.silence_seconds,
                        3.0 / descriptor.nominal_rate if descriptor.nominal_rate > 0 else math.inf,
                    )
                    missing = last is not None and now - last > threshold
                    if missing and descriptor.track_id not in self._track_gaps:
                        self._track_gaps.add(descriptor.track_id)
                        self._degraded = True
                        self.store.database.add_diagnostic(
                            session_id=session_id,
                            level="warning",
                            code="emotibit_track_disconnected",
                            message=(
                                f"EmotiBit track {descriptor.track_id} stopped producing samples"
                            ),
                            payload={"type_tag": descriptor.track_id},
                        )
                    elif not missing and descriptor.track_id in self._track_gaps:
                        self._track_gaps.remove(descriptor.track_id)

    def _publish_status(self) -> None:
        self.publish({"type": "emotibit_status", "emotibit": self.health()})

    def _is_connected(self, now: float) -> bool:
        return (
            self.lsl is not None
            and self._last_any_monotonic is not None
            and now - self._last_any_monotonic <= self.silence_seconds
        )


def describe_stream(info: Any) -> list[SignalDescriptor]:
    """Convert one pylsl StreamInfo into stable per-channel descriptors."""
    name = str(info.name())
    source_id = str(info.source_id() or "")
    count = int(info.channel_count())
    nominal_rate = float(info.nominal_srate())
    channel_format = str(info.channel_format())
    labels, units = _channel_metadata(info, count)
    descriptors = []
    used: set[str] = set()
    for index in range(count):
        label = labels[index] or (name if count == 1 else f"{name}-{index + 1}")
        tag = _extract_type_tag(label, name)
        base = tag or _safe_track_id(label)
        track_id = base
        suffix = 2
        while track_id in used:
            track_id = f"{base}_{suffix}"
            suffix += 1
        used.add(track_id)
        display_name, group = SIGNALS.get(tag, (label, "Other"))
        descriptors.append(
            SignalDescriptor(
                track_id=track_id,
                type_tag=tag or track_id,
                display_name=display_name,
                group_name=group,
                unit=units[index],
                nominal_rate=nominal_rate,
                stream_name=name,
                source_id=source_id,
                channel_index=index,
                channel_format=channel_format,
            )
        )
    return descriptors


def _channel_metadata(info: Any, count: int) -> tuple[list[str], list[str | None]]:
    labels = [""] * count
    units: list[str | None] = [None] * count
    try:
        channel = info.desc().child("channels").child("channel")
        for index in range(count):
            labels[index] = str(channel.child_value("label") or "")
            units[index] = str(channel.child_value("unit") or "") or None
            channel = channel.next_sibling()
    except (AttributeError, RuntimeError):
        pass
    return labels, units


def _extract_type_tag(label: str, stream_name: str) -> str:
    for text in (label, stream_name):
        for token in reversed(re.split(r"[^A-Za-z0-9]+", text.upper())):
            if token in SIGNALS:
                return token
    return ""


def _safe_track_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return normalized[:80] or "signal"


def _load_pylsl() -> Any | None:
    try:
        import pylsl  # type: ignore[import-untyped]

        return pylsl
    except (ImportError, OSError):
        return None


def _close_memmap(array: Any) -> None:
    mmap = getattr(array, "_mmap", None)
    if mmap is not None:
        mmap.close()
