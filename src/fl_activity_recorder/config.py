from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    midi_event_port: str = "ActivityRecorder Events"
    host: str = "127.0.0.1"
    port: int = 8765
    enable_midi: bool = True

    @classmethod
    def from_environment(cls) -> Settings:
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".fl-activity-recorder"))
        database = Path(
            os.environ.get("FL_ACTIVITY_DATABASE", app_data / "FLActivityRecorder" / "activity.db")
        )
        return cls(
            database_path=database,
            midi_event_port=os.environ.get("FL_ACTIVITY_MIDI_PORT", "ActivityRecorder Events"),
            host="127.0.0.1",
            port=int(os.environ.get("FL_ACTIVITY_PORT", "8765")),
            enable_midi=os.environ.get("FL_ACTIVITY_DISABLE_MIDI") != "1",
        )
