from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

APP_DIRECTORY = "FLActivityRecorder"


def default_data_directory(
    *,
    platform: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the conventional per-user data directory for the current platform."""
    current_platform = platform or sys.platform
    current_environment = environment if environment is not None else os.environ
    current_home = home or Path.home()

    if current_platform == "win32":
        local_app_data = current_environment.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / APP_DIRECTORY
        return current_home / "AppData" / "Local" / APP_DIRECTORY
    if current_platform == "darwin":
        return current_home / "Library" / "Application Support" / APP_DIRECTORY

    xdg_data_home = current_environment.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / APP_DIRECTORY
    return current_home / ".local" / "share" / APP_DIRECTORY


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    data_directory: Path | None = None
    midi_event_port: str = "ActivityRecorder Events"
    host: str = "127.0.0.1"
    port: int = 8765
    enable_midi: bool = True
    emotibit_lsl_prefix: str = "EmotiBit"
    r2_account_id: str | None = None
    r2_bucket_name: str | None = None
    r2_access_key_id: str | None = field(default=None, repr=False)
    r2_secret_access_key: str | None = field(default=None, repr=False)
    r2_endpoint: str | None = None
    r2_region: str = "auto"
    r2_share_expiry_seconds: int = 7 * 24 * 60 * 60
    disable_cloud_sharing: bool = False

    @classmethod
    def from_environment(cls) -> Settings:
        # Prefer an explicit project-local file during development, then fall back
        # to the per-user application directory used by installed companions.
        load_dotenv(Path.cwd() / ".env", override=False)
        load_dotenv(default_data_directory() / ".env", override=False)
        database = Path(
            os.environ.get(
                "FL_ACTIVITY_DATABASE",
                default_data_directory() / "activity.db",
            )
        )
        return cls(
            database_path=database,
            data_directory=Path(
                os.environ.get("FL_ACTIVITY_DATA_DIRECTORY", database.parent / "sessions")
            ),
            midi_event_port=os.environ.get("FL_ACTIVITY_MIDI_PORT", "ActivityRecorder Events"),
            host="127.0.0.1",
            port=int(os.environ.get("FL_ACTIVITY_PORT", "8765")),
            enable_midi=os.environ.get("FL_ACTIVITY_DISABLE_MIDI") != "1",
            emotibit_lsl_prefix=os.environ.get("FL_ACTIVITY_EMOTIBIT_LSL_PREFIX", "EmotiBit"),
            r2_account_id=os.environ.get("R2_ACCOUNT_ID"),
            r2_bucket_name=os.environ.get("R2_BUCKET_NAME"),
            r2_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
            r2_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
            r2_endpoint=os.environ.get("R2_ENDPOINT"),
            r2_region=os.environ.get("R2_REGION", "auto"),
            r2_share_expiry_seconds=int(
                os.environ.get("R2_SHARE_EXPIRY_SECONDS", str(7 * 24 * 60 * 60))
            ),
            disable_cloud_sharing=os.environ.get("FL_ACTIVITY_DISABLE_CLOUD_SHARING") == "1",
        )
