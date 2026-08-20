from __future__ import annotations

from pathlib import Path

from fl_activity_recorder.config import default_data_directory
from fl_activity_recorder.setup_check import check_ports, setup_hint


def test_macos_data_directory() -> None:
    assert default_data_directory(
        platform="darwin", environment={}, home=Path("/Users/tester")
    ) == Path("/Users/tester/Library/Application Support/FLActivityRecorder")


def test_windows_data_directory_preserves_local_app_data() -> None:
    assert default_data_directory(
        platform="win32",
        environment={"LOCALAPPDATA": "C:/Users/tester/AppData/Local"},
        home=Path("C:/Users/tester"),
    ) == Path("C:/Users/tester/AppData/Local/FLActivityRecorder")


def test_iac_driver_port_names_are_recognized() -> None:
    status = check_ports(
        ["IAC Driver ActivityRecorder Events"],
        ["IAC Driver ActivityRecorder Control"],
    )
    assert status.ready


def test_macos_setup_hint_uses_iac_driver() -> None:
    assert "IAC Driver" in setup_hint("darwin")
    assert "loopMIDI" not in setup_hint("darwin")


def test_installers_verify_observer_and_require_reload() -> None:
    root = Path(__file__).parents[1]
    macos = (root / "scripts" / "install-macos.sh").read_text(encoding="utf-8")
    windows = (root / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert "checksum" in macos.lower() and "reload Activity Recorder" in macos
    assert "checksum" in windows.lower() and "reload Activity Recorder" in windows
    assert "companion=" in macos and "companion=" in windows
