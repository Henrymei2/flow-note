from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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

    @classmethod
    def from_environment(cls) -> Settings:
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
        )
