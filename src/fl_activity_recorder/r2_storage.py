from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3  # type: ignore[import-untyped]
from boto3.s3.transfer import TransferConfig  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from .config import Settings

MAX_PRESIGNED_EXPIRY_SECONDS = 7 * 24 * 60 * 60


class R2StorageError(RuntimeError):
    """Raised when private Cloudflare R2 sharing cannot be completed."""


class R2Storage:
    """Upload immutable research archives and issue time-limited download URLs."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.account_id = settings.r2_account_id
        self.bucket = settings.r2_bucket_name
        self.access_key_id = settings.r2_access_key_id
        self.secret_access_key = settings.r2_secret_access_key
        self.endpoint = settings.r2_endpoint
        self.region = settings.r2_region
        self.expiry_seconds = max(
            60, min(settings.r2_share_expiry_seconds, MAX_PRESIGNED_EXPIRY_SECONDS)
        )
        self._client = client

    @property
    def configured(self) -> bool:
        return all(
            (
                self.account_id,
                self.bucket,
                self.access_key_id,
                self.secret_access_key,
                self.endpoint,
            )
        )

    def public_status(self) -> dict[str, Any]:
        """Return non-secret configuration state for the local dashboard."""
        return {
            "configured": self.configured,
            "bucket": self.bucket if self.configured else None,
            "share_expiry_seconds": self.expiry_seconds,
        }

    def check_connection(self) -> dict[str, Any]:
        """Verify that the configured credential can access the selected bucket."""
        client = self._get_client()
        try:
            client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)
        except Exception as exc:
            raise R2StorageError("R2 credentials could not access the configured bucket") from exc
        return {**self.public_status(), "connected": True}

    def upload_archive(
        self, session_id: str, archive: Path, session: dict[str, Any]
    ) -> dict[str, Any]:
        """Upload one content-addressed ZIP and publish it in the cloud catalog."""
        client = self._get_client()
        digest = self._sha256(archive)
        key = f"sessions/{session_id}/{digest}.zip"
        size_bytes = archive.stat().st_size
        catalog_key = f"catalog/{session_id}.json"
        uploaded_at = datetime.now(UTC).isoformat()
        catalog_entry = {
            "catalog_version": 1,
            "session_id": session_id,
            "project_title": session.get("project_title"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "event_count": int(session.get("event_count") or 0),
            "sync_quality": session.get("sync_quality"),
            "archive_key": key,
            "sha256": digest,
            "size_bytes": size_bytes,
            "uploaded_at": uploaded_at,
        }
        try:
            client.upload_file(
                str(archive),
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": "application/zip",
                    "ContentDisposition": (
                        f'attachment; filename="fl-studio-research-session-{session_id}.zip"'
                    ),
                    "Metadata": {
                        "sha256": digest,
                        "session-id": session_id,
                        "archive-format": "fl-studio-activity-research-archive-v1",
                    },
                },
                Config=TransferConfig(
                    multipart_threshold=100 * 1024**2,
                    multipart_chunksize=16 * 1024**2,
                    max_concurrency=4,
                    use_threads=True,
                ),
            )
            client.put_object(
                Bucket=self.bucket,
                Key=catalog_key,
                Body=json.dumps(catalog_entry, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                ),
                ContentType="application/json",
                CacheControl="no-store",
            )
            url = self._presign(key)
        except Exception as exc:
            raise R2StorageError("The research archive could not be uploaded to R2") from exc
        return {
            "object_key": key,
            "catalog_key": catalog_key,
            "sha256": digest,
            "size_bytes": size_bytes,
            "download_url": str(url),
            "expires_in_seconds": self.expiry_seconds,
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        """List valid session descriptors published in the private R2 catalog."""
        client = self._get_client()
        keys: list[str] = []
        continuation: str | None = None
        try:
            while True:
                arguments: dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Prefix": "catalog/",
                    "MaxKeys": 1000,
                }
                if continuation:
                    arguments["ContinuationToken"] = continuation
                response = client.list_objects_v2(**arguments)
                keys.extend(
                    str(item["Key"])
                    for item in response.get("Contents", [])
                    if str(item.get("Key", "")).endswith(".json")
                )
                if not response.get("IsTruncated"):
                    break
                continuation = str(response["NextContinuationToken"])
            sessions: list[dict[str, Any]] = []
            for key in keys:
                try:
                    body = client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
                    item = json.loads(body)
                    if self._valid_catalog_entry(item):
                        sessions.append(item)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        except Exception as exc:
            raise R2StorageError("Cloud sessions could not be loaded from R2") from exc
        sessions.sort(key=lambda item: str(item.get("uploaded_at") or ""), reverse=True)
        return sessions

    def create_download_url(self, archive_key: str) -> str:
        """Create a temporary URL for one validated research archive key."""
        if not self._valid_archive_key(archive_key):
            raise R2StorageError("Invalid cloud archive reference")
        try:
            return str(self._presign(archive_key))
        except Exception as exc:
            raise R2StorageError("A cloud download link could not be created") from exc

    def _presign(self, key: str) -> Any:
        return self._get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.expiry_seconds,
        )

    @classmethod
    def _valid_catalog_entry(cls, item: Any) -> bool:
        return (
            isinstance(item, dict)
            and item.get("catalog_version") == 1
            and isinstance(item.get("session_id"), str)
            and cls._valid_archive_key(item.get("archive_key"))
            and isinstance(item.get("sha256"), str)
            and isinstance(item.get("size_bytes"), int)
        )

    @staticmethod
    def _valid_archive_key(key: Any) -> bool:
        return isinstance(key, str) and bool(
            re.fullmatch(
                r"sessions/[0-9a-fA-F-]{36}/[0-9a-f]{64}\.zip",
                key,
            )
        )

    def _get_client(self) -> Any:
        if not self.configured:
            raise R2StorageError("Cloudflare R2 sharing is not configured")
        self._validate_endpoint()
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region,
                config=Config(signature_version="s3v4"),
            )
        return self._client

    def _validate_endpoint(self) -> None:
        parsed = urlparse(str(self.endpoint))
        hostname = parsed.hostname or ""
        expected_suffix = ".r2.cloudflarestorage.com"
        if (
            parsed.scheme != "https"
            or parsed.path not in {"", "/"}
            or not hostname.endswith(expected_suffix)
            or not hostname.startswith(f"{self.account_id}.")
        ):
            raise R2StorageError("R2_ENDPOINT does not match the configured Cloudflare account")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
