from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np

from . import __version__
from .database import Database
from .models import ActivityEvent

FORMAT_NAME = "fl-studio-activity-research-archive"
FORMAT_VERSION = 1
MAX_FILES = 10_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024**3


class ResearchArchiveError(ValueError):
    """Raised when a research archive is invalid or unsafe to import."""


class ResearchArchiveManager:
    """Create and restore portable, checksummed research-session ZIP archives."""

    def __init__(self, database: Database, root: Path) -> None:
        self.database = database
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def export(self, session_id: str) -> Path:
        session = self.database.get_session(session_id)
        if session["status"] == "recording":
            raise ResearchArchiveError("Stop the recording before sharing it")
        assets = self.database.list_assets(session_id)
        streams = [
            stream
            for stream in self.database.list_signal_streams(session_id)
            if int(stream.get("sample_count") or 0) > 0
        ]
        events = self._all_events(session_id)
        video = next(
            (item for item in assets if item["kind"] == "video" and item["status"] == "ready"),
            None,
        )
        handle, temporary_name = tempfile.mkstemp(
            prefix=f"research-session-{session_id}-", suffix=".zip", dir=self.root
        )
        os.close(handle)
        target = Path(temporary_name)
        checksums: dict[str, dict[str, Any]] = {}
        signal_entries: list[dict[str, Any]] = []
        video_entry: dict[str, Any] | None = None
        try:
            with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
                events_jsonl = "".join(
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                    for event in events
                ).encode("utf-8")
                self._write_bytes(archive, "events/events.jsonl", events_jsonl, checksums)
                self._write_bytes(archive, "events/events.csv", self._events_csv(events), checksums)
                for index, stream in enumerate(streams):
                    safe_tag = self._safe_name(str(stream["type_tag"]))
                    path = f"physiology/{index:03d}-{safe_tag}.csv"
                    content = self._signal_csv(session_id, str(stream["type_tag"]))
                    self._write_bytes(archive, path, content, checksums)
                    signal_entries.append({**self._portable_row(stream), "file": path})
                if video is not None and video.get("relative_path"):
                    source = self._contained_path(str(video["relative_path"]))
                    suffix = source.suffix if source.suffix else ".webm"
                    path = f"media/video{suffix}"
                    self._write_file(archive, path, source, checksums)
                    video_entry = {**self._portable_row(video), "file": path}
                self._write_bytes(archive, "README.txt", self._readme().encode(), checksums)
                manifest = {
                    "format": FORMAT_NAME,
                    "format_version": FORMAT_VERSION,
                    "exported_at": datetime.now(UTC).isoformat(),
                    "exporter_version": __version__,
                    "session": self._portable_row(session),
                    "events": {
                        "jsonl": "events/events.jsonl",
                        "csv": "events/events.csv",
                        "count": len(events),
                    },
                    "video": video_entry,
                    "physiology": signal_entries,
                    "checksums": checksums,
                }
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                    compress_type=zipfile.ZIP_DEFLATED,
                )
            return target
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def import_archive(self, source: Path) -> dict[str, Any]:
        staging = self.root / f".research-import-{uuid.uuid4()}"
        created_session_id: str | None = None
        try:
            with zipfile.ZipFile(source) as archive:
                manifest = self._validate(archive)
                session = self.database.create_imported_session(dict(manifest["session"]))
                created_session_id = str(session["id"])
                final_root = self.root / created_session_id
                if final_root.exists():
                    raise ResearchArchiveError("The local session directory already exists")
                staging.mkdir()
                video = manifest.get("video")
                video_local: tuple[dict[str, Any], Path] | None = None
                if isinstance(video, dict):
                    member = self._member_name(video.get("file"))
                    suffix = Path(member).suffix or ".webm"
                    output = staging / "video" / f"capture{suffix}"
                    self._copy_member(archive, member, output)
                    video_local = (video, output)
                imported_signals: list[tuple[dict[str, Any], list[Path], list[np.ndarray]]] = []
                for item in manifest.get("physiology", []):
                    if not isinstance(item, dict):
                        raise ResearchArchiveError("Invalid physiology entry")
                    member = self._member_name(item.get("file"))
                    arrays = self._read_signal_csv(archive.read(member))
                    tag = self._signal_directory_name(str(item.get("type_tag") or "signal"))
                    directory = staging / "signals" / tag
                    paths: list[Path] = []
                    chunks: list[np.ndarray] = []
                    for sequence, start in enumerate(range(0, len(arrays), 4096)):
                        chunk = arrays[start : start + 4096]
                        output = directory / f"chunk-{sequence:06d}.npy"
                        output.parent.mkdir(parents=True, exist_ok=True)
                        with output.open("wb") as handle:
                            np.save(handle, chunk)
                        paths.append(output)
                        chunks.append(chunk)
                    imported_signals.append((item, paths, chunks))
                os.replace(staging, final_root)
                self._restore_records(
                    archive, manifest, created_session_id, video_local, imported_signals
                )
            result = self.database.get_session(created_session_id)
            result["source_session_id"] = manifest["session"].get("id")
            return result
        except Exception as exc:
            if created_session_id is not None:
                self.database.delete_session(created_session_id)
                shutil.rmtree(self.root / created_session_id, ignore_errors=True)
            if isinstance(exc, ResearchArchiveError):
                raise
            if isinstance(
                exc,
                (
                    KeyError,
                    AttributeError,
                    TypeError,
                    ValueError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    zipfile.BadZipFile,
                    csv.Error,
                ),
            ):
                raise ResearchArchiveError("Not a valid FL Studio research archive") from exc
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _restore_records(
        self,
        archive: zipfile.ZipFile,
        manifest: dict[str, Any],
        session_id: str,
        video_local: tuple[dict[str, Any], Path] | None,
        signals: list[tuple[dict[str, Any], list[Path], list[np.ndarray]]],
    ) -> None:
        events_path = self._member_name(manifest["events"]["jsonl"])
        for line in archive.read(events_path).decode("utf-8-sig").splitlines():
            raw = json.loads(line)
            event = ActivityEvent.from_payload(
                {
                    "category": raw.get("category"),
                    "action": raw.get("action"),
                    "summary": raw.get("summary"),
                    "confidence": raw.get("confidence"),
                    "entity_type": raw.get("entity_type"),
                    "entity_key": raw.get("entity_key"),
                    "payload": raw.get("payload"),
                }
            )
            self.database.add_event(
                session_id,
                int(raw.get("sequence") or 0),
                str(raw.get("occurred_at") or manifest["session"].get("started_at")),
                float(raw.get("monotonic_seconds") or 0),
                event,
                observed_monotonic_seconds=self._float_or_none(
                    raw.get("observed_monotonic_seconds")
                ),
                received_monotonic_seconds=self._float_or_none(
                    raw.get("received_monotonic_seconds")
                ),
                timeline_seconds=self._float_or_none(raw.get("timeline_seconds")),
                transport_delay_ms=self._float_or_none(raw.get("transport_delay_ms")),
                time_confidence=str(raw.get("time_confidence") or "received"),
            )
        if video_local is not None:
            metadata, staged_path = video_local
            final = self.root / session_id / staged_path.relative_to(staged_path.parents[1])
            values = self._asset_values(metadata)
            values["relative_path"] = str(final.relative_to(self.root))
            values["size_bytes"] = final.stat().st_size
            values["sha256"] = self._sha256(final)
            self.database.upsert_asset(session_id, "video", "ready", **values)
        for metadata, paths, chunks in signals:
            type_tag = str(metadata.get("type_tag") or "signal")
            directory = self.root / session_id / "signals" / self._signal_directory_name(type_tag)
            stream = {
                "type_tag": type_tag,
                "display_name": str(metadata.get("display_name") or type_tag),
                "group_name": str(metadata.get("group_name") or "Physiology"),
                "unit": metadata.get("unit"),
                "nominal_rate": metadata.get("nominal_rate"),
                "relative_path": str(directory.relative_to(self.root)),
                "metadata": dict(metadata.get("metadata") or {}),
            }
            self.database.ensure_live_signal_stream(session_id, stream)
            for sequence, (old_path, chunk) in enumerate(zip(paths, chunks, strict=True)):
                final = self.root / session_id / old_path.relative_to(old_path.parents[2])
                self.database.add_signal_chunk(
                    session_id,
                    {
                        "type_tag": type_tag,
                        "sequence": sequence,
                        "start_seconds": float(chunk[0, 0]),
                        "end_seconds": float(chunk[-1, 0]),
                        "sample_count": len(chunk),
                        "min_value": float(np.min(chunk[:, 1])),
                        "max_value": float(np.max(chunk[:, 1])),
                        "relative_path": str(final.relative_to(self.root)),
                    },
                )
        if signals:
            self.database.upsert_asset(
                session_id,
                "emotibit",
                "ready",
                relative_path=f"{session_id}/signals",
                metadata_json={"source": "research_archive", "stream_count": len(signals)},
            )

    def _validate(self, archive: zipfile.ZipFile) -> dict[str, Any]:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise ResearchArchiveError("Archive contains too many files")
        if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
            raise ResearchArchiveError("Archive expands beyond the supported size")
        names = {item.filename for item in infos}
        if "manifest.json" not in names:
            raise ResearchArchiveError("Archive is missing manifest.json")
        for item in infos:
            path = Path(item.filename)
            if path.is_absolute() or ".." in path.parts or item.is_dir():
                raise ResearchArchiveError("Archive contains an unsafe file path")
        manifest_raw = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest_raw, dict):
            raise ResearchArchiveError("Archive manifest must be a JSON object")
        manifest = cast(dict[str, Any], manifest_raw)
        if (
            manifest.get("format") != FORMAT_NAME
            or manifest.get("format_version") != FORMAT_VERSION
        ):
            raise ResearchArchiveError("Unsupported research archive format")
        if not isinstance(manifest.get("session"), dict) or not isinstance(
            manifest.get("events"), dict
        ):
            raise ResearchArchiveError("Archive manifest is incomplete")
        if manifest.get("video") is not None and not isinstance(manifest.get("video"), dict):
            raise ResearchArchiveError("Archive contains invalid video metadata")
        if not isinstance(manifest.get("physiology"), list):
            raise ResearchArchiveError("Archive contains invalid physiology metadata")
        checksums = manifest.get("checksums")
        if not isinstance(checksums, dict):
            raise ResearchArchiveError("Archive has no checksums")
        referenced = [manifest["events"].get("jsonl")]
        if isinstance(manifest.get("video"), dict):
            referenced.append(manifest["video"].get("file"))
        referenced.extend(
            item.get("file") for item in manifest.get("physiology", []) if isinstance(item, dict)
        )
        for raw_name in referenced:
            name = self._member_name(raw_name)
            if name not in names or name not in checksums:
                raise ResearchArchiveError(f"Archive is missing required file: {name}")
            data = archive.read(name)
            expected = checksums[name]
            if not isinstance(expected, dict):
                raise ResearchArchiveError(f"Archive has an invalid checksum for {name}")
            if hashlib.sha256(data).hexdigest() != expected.get("sha256") or len(
                data
            ) != expected.get("bytes"):
                raise ResearchArchiveError(f"Checksum failed for {name}")
        return manifest

    def _all_events(self, session_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.database.list_events(session_id, limit=5000, offset=offset)
            if not page:
                break
            events.extend(page)
            offset += len(page)
        events.sort(key=lambda item: (int(item.get("sequence") or 0), int(item.get("id") or 0)))
        return events

    def _signal_csv(self, session_id: str, type_tag: str) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["timeline_seconds", "value"])
        for chunk in self.database.list_signal_chunks(session_id, type_tag, -1e300, 1e300):
            array = np.load(self._contained_path(str(chunk["relative_path"])), mmap_mode="r")
            writer.writerows(
                (format(float(row[0]), ".17g"), format(float(row[1]), ".17g")) for row in array
            )
        return output.getvalue().encode("utf-8")

    @staticmethod
    def _read_signal_csv(data: bytes) -> np.ndarray:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        if reader.fieldnames != ["timeline_seconds", "value"]:
            raise ResearchArchiveError("Physiology CSV has unexpected columns")
        rows = [(float(row["timeline_seconds"]), float(row["value"])) for row in reader]
        if not rows:
            raise ResearchArchiveError("Physiology CSV contains no samples")
        array = np.asarray(rows, dtype="f8")
        if not np.all(np.isfinite(array)) or np.any(np.diff(array[:, 0]) < 0):
            raise ResearchArchiveError("Physiology samples are invalid or out of order")
        return array

    @staticmethod
    def _events_csv(events: list[dict[str, Any]]) -> bytes:
        fields = [
            "sequence",
            "timeline_seconds",
            "occurred_at",
            "category",
            "action",
            "entity_type",
            "entity_key",
            "summary",
            "confidence",
            "time_confidence",
            "transport_delay_ms",
            "payload_json",
        ]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for event in events:
            row = {name: event.get(name) for name in fields}
            row["payload_json"] = json.dumps(
                event.get("payload") or {}, ensure_ascii=False, separators=(",", ":")
            )
            writer.writerow(row)
        return output.getvalue().encode("utf-8")

    @staticmethod
    def _readme() -> str:
        return """FL Studio Activity Recorder research archive

All timestamps in events and physiology CSV files use timeline_seconds: seconds after
the session's timeline zero. The video start_offset_seconds in manifest.json maps
video time 0 to the same timeline.

Open events/events.csv and physiology/*.csv in R, Python, MATLAB, Excel, or similar
tools. events/events.jsonl preserves nested FL Studio event payloads. Import this ZIP
from the recorder dashboard to review the synchronized session locally. Verify file
integrity with the SHA-256 values in manifest.json.

Treat project, plugin, channel, video, audio, and physiology content as human-subject
data. Share only through an approved encrypted research-data service and keep consent,
access controls, retention, and de-identification requirements with the archive.
"""

    def _write_bytes(
        self, archive: zipfile.ZipFile, name: str, data: bytes, checksums: dict[str, dict[str, Any]]
    ) -> None:
        archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        checksums[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def _write_file(
        self,
        archive: zipfile.ZipFile,
        name: str,
        source: Path,
        checksums: dict[str, dict[str, Any]],
    ) -> None:
        archive.write(source, name, compress_type=zipfile.ZIP_STORED)
        checksums[name] = {"sha256": self._sha256(source), "bytes": source.stat().st_size}

    def _contained_path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if self.root not in path.parents:
            raise ResearchArchiveError("Stored asset path escapes the data directory")
        return path

    @staticmethod
    def _copy_member(archive: zipfile.ZipFile, member: str, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, output.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)

    @staticmethod
    def _member_name(value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise ResearchArchiveError("Archive manifest contains an invalid file reference")
        return value

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
        return cleaned or "signal"

    @classmethod
    def _signal_directory_name(cls, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return f"{cls._safe_name(value)}-{digest}"

    @staticmethod
    def _portable_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if key not in {"event_count", "id", "session_id", "relative_path"}
        } | ({"id": row["id"]} if "id" in row and "session_id" not in row else {})

    @staticmethod
    def _asset_values(metadata: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "mime_type",
            "started_lsl",
            "ended_lsl",
            "start_offset_seconds",
            "duration_seconds",
            "metadata",
        }
        values = {key: metadata.get(key) for key in allowed if key in metadata}
        if "metadata" in values:
            values["metadata_json"] = values.pop("metadata")
        return values

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        return None if value is None else float(value)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
