from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .assets import CaptureManager
from .database import Database

STANDARD_COLUMNS = {
    "LocalTimestamp",
    "LslLocalTimestamp",
    "LslMarkerSourceTimestamp",
    "EmotiBitTimestamp",
    "PacketNumber",
    "DataLength",
    "TypeTag",
    "ProtocolVersion",
    "DataReliability",
    "Data",
}

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


class EmotiBitImporter:
    """Validate and normalize official EmotiBit DataParser output."""

    def __init__(self, database: Database, assets: CaptureManager) -> None:
        self.database = database
        self.assets = assets

    def import_files(
        self, session_id: str, source_files: list[Path], *, replace: bool = False
    ) -> dict[str, Any]:
        """Import a complete parsed recording with an atomic directory commit."""
        session = self.database.get_session(session_id)
        if session.get("clock_mode") != "lsl" or self.database.count_sync_markers(session_id) < 2:
            raise ValueError("This session does not contain the required LSL marker coverage")
        csv_files = sorted(path for path in source_files if path.suffix.casefold() == ".csv")
        info_files = [
            path
            for path in source_files
            if path.name.casefold().endswith("_info.json") or path.suffix.casefold() == ".json"
        ]
        if not csv_files:
            raise ValueError("Select at least one parsed EmotiBit CSV file")
        if not info_files:
            raise ValueError("Select the recording's _info.json file")
        bundle_hash, file_manifest = _bundle_digest(source_files)
        try:
            existing = self.database.get_asset(session_id, "emotibit")
            if existing["metadata"].get("bundle_sha256") == bundle_hash:
                return {
                    "status": "ready",
                    "idempotent": True,
                    "streams": self.database.list_signal_streams(session_id),
                }
            if not replace:
                raise FileExistsError("Different EmotiBit data is already attached; use Replace")
        except KeyError:
            pass

        metadata = _read_info(info_files[0])
        stats = self._scan(csv_files)
        if not stats:
            raise ValueError("The selected CSV files contain no numeric signal samples")
        zero = float(session["timeline_zero_lsl"])
        end = float(session.get("timeline_end_lsl") or zero)
        first = min(item["first_lsl"] for item in stats.values())
        last = max(item["last_lsl"] for item in stats.values())
        if first > zero or last < end:
            raise ValueError("EmotiBit timestamps do not cover the complete session")

        session_dir = self.assets.session_directory(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".emotibit-", dir=session_dir))
        try:
            original = staging / "original"
            original.mkdir()
            for source in source_files:
                shutil.copy2(source, original / source.name)
            signal_root = staging / "signals"
            signal_root.mkdir()
            arrays, streams = self._allocate(signal_root, stats, metadata, zero)
            self._populate(csv_files, arrays, zero)
            for stream in streams:
                path = signal_root / str(stream["type_tag"])
                _write_pyramids(path)
                stream["relative_path"] = str(
                    Path(session_id) / "emotibit" / "signals" / str(stream["type_tag"])
                )
            for values in arrays.values():
                for array in values.values():
                    array.flush()
                    _close_memmap(array)
            arrays.clear()
            final = session_dir / "emotibit"
            backup = session_dir / ".emotibit-backup"
            if final.exists():
                if backup.exists():
                    shutil.rmtree(backup)
                os.replace(final, backup)
            os.replace(staging, final)
            try:
                asset = self.database.commit_emotibit_import(
                    session_id,
                    streams,
                    relative_path=str(Path(session_id) / "emotibit"),
                    bundle_sha256=bundle_hash,
                    metadata={
                        "bundle_sha256": bundle_hash,
                        "files": file_manifest,
                        "info": metadata["raw"],
                        "lsl_column": "LslMarkerSourceTimestamp",
                    },
                )
            except Exception:
                if final.exists():
                    shutil.rmtree(final)
                if backup.exists():
                    os.replace(backup, final)
                raise
            if backup.exists():
                shutil.rmtree(backup)
            return {"status": asset["status"], "idempotent": False, "streams": streams}
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def query(
        self,
        session_id: str,
        type_tag: str,
        start: float,
        end: float,
        pixels: int,
    ) -> dict[str, Any]:
        """Return raw or min/max decimated values for a visible time range."""
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
        directory = self.assets.root / str(stream["relative_path"])
        times = np.load(directory / "time.npy", mmap_mode="r")
        left = int(np.searchsorted(times, start, side="left"))
        right = int(np.searchsorted(times, end, side="right"))
        count = max(0, right - left)
        if count <= max(1, pixels) * 4:
            values = np.load(directory / "value.npy", mmap_mode="r")
            result = {
                "type_tag": type_tag,
                "mode": "raw",
                "points": [[float(times[i]), float(values[i])] for i in range(left, right)],
            }
            _close_memmap(values)
            _close_memmap(times)
            return result
        bucket = max(1, count // max(1, pixels))
        pyramid_bucket = 1024 if bucket >= 1024 else 32 if bucket >= 32 else None
        if pyramid_bucket is not None:
            pyramid = np.load(directory / f"pyramid-{pyramid_bucket}.npy", mmap_mode="r")
            values = np.load(directory / "value.npy", mmap_mode="r")
            first_full_bucket = (left + pyramid_bucket - 1) // pyramid_bucket
            final_full_bucket = right // pyramid_bucket
            points: list[list[float]] = []
            cursor = left
            prefix_end = min(right, first_full_bucket * pyramid_bucket)
            if cursor < prefix_end:
                section = values[cursor:prefix_end]
                points.append(
                    [float(times[cursor]), float(np.min(section)), float(np.max(section))]
                )
                cursor = prefix_end
            if first_full_bucket < final_full_bucket:
                points.extend(
                    [float(row[0]), float(row[1]), float(row[2])]
                    for row in pyramid[first_full_bucket:final_full_bucket]
                )
                cursor = final_full_bucket * pyramid_bucket
            if cursor < right:
                section = values[cursor:right]
                points.append(
                    [float(times[cursor]), float(np.min(section)), float(np.max(section))]
                )
            _close_memmap(pyramid)
            _close_memmap(values)
            _close_memmap(times)
            return {
                "type_tag": type_tag,
                "mode": "minmax",
                "bucket_size": pyramid_bucket,
                "points": points,
            }
        values = np.load(directory / "value.npy", mmap_mode="r")
        points = []
        for index in range(left, right, bucket):
            stop = min(right, index + bucket)
            section = values[index:stop]
            points.append([float(times[index]), float(np.min(section)), float(np.max(section))])
        _close_memmap(values)
        _close_memmap(times)
        return {"type_tag": type_tag, "mode": "minmax", "points": points}

    def _scan(self, files: list[Path]) -> dict[str, dict[str, Any]]:
        """Validate source rows and count each monotonic signal stream."""
        stats: dict[str, dict[str, Any]] = {}
        last_by_tag: dict[str, float] = {}
        for path in files:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                _validate_headers(reader.fieldnames)
                for line_number, row in enumerate(reader, 2):
                    parsed = _parse_row(row, reader.fieldnames or [], path, line_number)
                    if parsed is None:
                        continue
                    tag, lsl, *_ = parsed
                    if tag in last_by_tag and lsl < last_by_tag[tag]:
                        raise ValueError(
                            f"{path.name}:{line_number} has a non-monotonic LSL timestamp"
                        )
                    last_by_tag[tag] = lsl
                    item = stats.setdefault(tag, {"count": 0, "first_lsl": lsl, "last_lsl": lsl})
                    item["count"] += 1
                    item["last_lsl"] = lsl
        return stats

    def _allocate(
        self,
        root: Path,
        stats: dict[str, dict[str, Any]],
        metadata: dict[str, Any],
        zero: float,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Allocate memory-mapped arrays and authoritative stream metadata."""
        arrays: dict[str, dict[str, Any]] = {}
        streams = []
        for tag, stat in stats.items():
            directory = root / tag
            directory.mkdir()
            count = int(stat["count"])
            arrays[tag] = {
                "time": _open_array(directory / "time.npy", "f8", count),
                "value": _open_array(directory / "value.npy", "f8", count),
                "emotibit": _open_array(directory / "emotibit.npy", "f8", count),
                "packet": _open_array(directory / "packet.npy", "i8", count),
                "reliability": _open_array(directory / "reliability.npy", "f4", count),
            }
            info = metadata["tags"].get(tag, {})
            display_name, group = SIGNALS.get(tag, (tag, "Other"))
            streams.append(
                {
                    "type_tag": tag,
                    "display_name": display_name,
                    "group_name": group,
                    "unit": info.get("units"),
                    "nominal_rate": info.get("nominal_srate"),
                    "sample_count": count,
                    "start_seconds": float(stat["first_lsl"]) - zero,
                    "end_seconds": float(stat["last_lsl"]) - zero,
                    "metadata": info,
                }
            )
        return arrays, streams

    def _populate(self, files: list[Path], arrays: dict[str, dict[str, Any]], zero: float) -> None:
        """Stream validated CSV samples into their allocated arrays."""
        offsets: dict[str, int] = defaultdict(int)
        for path in files:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for line_number, row in enumerate(reader, 2):
                    parsed = _parse_row(row, reader.fieldnames or [], path, line_number)
                    if parsed is None:
                        continue
                    tag, lsl, value, device_time, packet, reliability = parsed
                    index = offsets[tag]
                    arrays[tag]["time"][index] = lsl - zero
                    arrays[tag]["value"][index] = value
                    arrays[tag]["emotibit"][index] = device_time
                    arrays[tag]["packet"][index] = packet
                    arrays[tag]["reliability"][index] = reliability
                    offsets[tag] += 1


def _validate_headers(fieldnames: Sequence[str] | None) -> None:
    """Require timestamp and audit columns from official DataParser output."""
    if not fieldnames or "LslMarkerSourceTimestamp" not in fieldnames:
        raise ValueError("Every parsed CSV must include LslMarkerSourceTimestamp")
    required = {"EmotiBitTimestamp", "PacketNumber", "DataReliability"}
    if not required <= set(fieldnames):
        raise ValueError(
            f"Parsed CSV is missing required columns: {sorted(required - set(fieldnames))}"
        )


def _parse_row(
    row: dict[str, str], fieldnames: Sequence[str], path: Path, line_number: int
) -> tuple[str, float, float, float, int, float] | None:
    """Parse one signal row while allowing empty samples and unknown numeric tags."""
    tag = (row.get("TypeTag") or "").strip()
    value_column = tag if tag in row else "Data" if "Data" in row else ""
    if not value_column:
        candidates = [name for name in fieldnames if name not in STANDARD_COLUMNS]
        value_column = candidates[-1] if candidates else ""
        tag = tag or value_column
    raw_value = (row.get(value_column) or "").strip()
    if not raw_value:
        return None
    try:
        return (
            tag,
            float(row["LslMarkerSourceTimestamp"]),
            float(raw_value),
            float(row["EmotiBitTimestamp"]),
            int(float(row["PacketNumber"])),
            float(row["DataReliability"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path.name}:{line_number} contains invalid numeric data") from exc


def _read_info(path: Path) -> dict[str, Any]:
    """Read TypeTag metadata from the recording's authoritative info file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("The selected _info.json is invalid") from exc
    tags: dict[str, dict[str, Any]] = {}
    records = raw if isinstance(raw, list) else [raw]
    for record in records:
        info = record.get("info", record) if isinstance(record, dict) else {}
        if not isinstance(info, dict):
            continue
        type_tags = info.get("typeTags", [])
        if isinstance(type_tags, str):
            type_tags = [type_tags]
        for tag in type_tags:
            tags[str(tag)] = dict(info)
    return {"raw": raw, "tags": tags}


def _bundle_digest(paths: list[Path]) -> tuple[str, list[dict[str, Any]]]:
    """Hash a bundle without loading long recordings into memory."""
    digest = hashlib.sha256()
    manifest = []
    for path in sorted(paths, key=lambda item: item.name):
        file_digest = _file_digest(path)
        size = path.stat().st_size
        digest.update(path.name.encode())
        digest.update(file_digest.encode())
        manifest.append({"name": path.name, "size": size, "sha256": file_digest})
    return digest.hexdigest(), manifest


def _file_digest(path: Path) -> str:
    """Calculate a file SHA-256 incrementally."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_pyramids(directory: Path) -> None:
    """Precompute fixed min/max levels for interactive signal rendering."""
    values = np.load(directory / "value.npy", mmap_mode="r")
    times = np.load(directory / "time.npy", mmap_mode="r")
    for bucket in (32, 1024):
        length = (len(values) + bucket - 1) // bucket
        result = np.empty((length, 3), dtype="f8")
        for index in range(length):
            start = index * bucket
            stop = min(len(values), start + bucket)
            result[index] = (times[start], np.min(values[start:stop]), np.max(values[start:stop]))
        np.save(directory / f"pyramid-{bucket}.npy", result)
    _close_memmap(values)
    _close_memmap(times)


def _open_array(path: Path, dtype: str, count: int) -> Any:
    """Create one NumPy-format memory-mapped vector."""
    return np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=(count,))  # type: ignore[no-untyped-call]


def _close_memmap(array: Any) -> None:
    """Close the backing map eagerly so Windows can rename its directory."""
    mmap = getattr(array, "_mmap", None)
    if mmap is not None:
        mmap.close()
