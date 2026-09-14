from __future__ import annotations

from pathlib import Path

import pytest

from fl_activity_recorder.config import Settings
from fl_activity_recorder.r2_storage import R2Storage, R2StorageError


def settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_path": tmp_path / "test.db",
        "enable_midi": False,
        "r2_account_id": "0123456789abcdef0123456789abcdef",
        "r2_bucket_name": "research-sessions",
        "r2_access_key_id": "access",
        "r2_secret_access_key": "secret",
        "r2_endpoint": "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_public_status_never_exposes_credentials(tmp_path: Path) -> None:
    storage = R2Storage(settings(tmp_path))
    assert storage.public_status() == {
        "configured": True,
        "bucket": "research-sessions",
        "share_expiry_seconds": 604800,
    }
    assert "secret" not in repr(settings(tmp_path))


def test_endpoint_must_match_account(tmp_path: Path) -> None:
    storage = R2Storage(settings(tmp_path, r2_endpoint="https://other.r2.cloudflarestorage.com"))
    with pytest.raises(R2StorageError, match="does not match"):
        storage._get_client()


def test_rejects_non_archive_download_keys(tmp_path: Path) -> None:
    storage = R2Storage(settings(tmp_path), client=object())
    with pytest.raises(R2StorageError, match="Invalid cloud archive"):
        storage.create_download_url("catalog/not-an-archive.json")
