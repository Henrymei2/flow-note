from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from fl_activity_recorder.protocol import MessageDecoder, MessageKind


class FakeModule(ModuleType):
    def __getattr__(self, name: str) -> Any:
        if name.startswith("is"):
            return lambda *args: 0
        if name.startswith("get"):
            return lambda *args: 0
        return lambda *args: None


def load_observer() -> tuple[ModuleType, list[bytes], dict[str, int]]:
    outputs: list[bytes] = []
    calls = {"plugin_values": 0}
    modules: dict[str, ModuleType] = {}
    for name in [
        "channels",
        "device",
        "general",
        "midi",
        "mixer",
        "patterns",
        "playlist",
        "plugins",
        "transport",
        "ui",
    ]:
        modules[name] = FakeModule(name)
    modules["device"].midiOutSysex = outputs.append  # type: ignore[attr-defined]
    modules["general"].getVersion = lambda: 42  # type: ignore[attr-defined]
    modules["general"].getProjectTitle = lambda: "Test Project"  # type: ignore[attr-defined]
    modules["transport"].isPlaying = lambda: 0  # type: ignore[attr-defined]
    modules["transport"].isRecording = lambda: 0  # type: ignore[attr-defined]
    modules["transport"].getLoopMode = lambda: 1  # type: ignore[attr-defined]
    modules["mixer"].getCurrentTempo = lambda: 128.0  # type: ignore[attr-defined]
    modules["mixer"].getActiveEffectIndex = lambda: (1, 0)  # type: ignore[attr-defined]
    modules["channels"].selectedChannel = lambda *args: 0  # type: ignore[attr-defined]
    modules["channels"].getChannelName = lambda index, *args: "Synth"  # type: ignore[attr-defined]
    modules["channels"].getGridBit = lambda *args: 0  # type: ignore[attr-defined]
    modules["patterns"].patternNumber = lambda: 1  # type: ignore[attr-defined]
    modules["patterns"].patternCount = lambda: 2  # type: ignore[attr-defined]
    modules["patterns"].getPatternName = lambda index: "Verse"  # type: ignore[attr-defined]
    modules["ui"].getFocusedPluginName = lambda: "FLEX"  # type: ignore[attr-defined]
    modules["ui"].getFocusedFormCaption = lambda: "FLEX"  # type: ignore[attr-defined]
    modules["ui"].getFocusedFormID = lambda: 5  # type: ignore[attr-defined]
    modules["plugins"].getParamCount = lambda *args: 600  # type: ignore[attr-defined]
    modules["plugins"].getParamName = lambda index, *args: f"Param {index}"  # type: ignore[attr-defined]

    def plugin_value(index: int, *args: object) -> float:
        calls["plugin_values"] += 1
        return index / 512

    modules["plugins"].getParamValue = plugin_value  # type: ignore[attr-defined]
    modules["midi"].HW_Dirty_ControlValues = 4096  # type: ignore[attr-defined]
    modules["midi"].HW_Dirty_Undo = 131072  # type: ignore[attr-defined]
    old_modules = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    path = (
        Path(__file__).parents[1] / "fl_studio" / "ActivityRecorder" / "device_ActivityRecorder.py"
    )
    spec = importlib.util.spec_from_file_location("activity_observer_test", path)
    assert spec and spec.loader
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    for name, previous in old_modules.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return observer, outputs, calls


def test_observer_hello_snapshot_and_callback_mapping() -> None:
    observer, outputs, _ = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    decoder = MessageDecoder()
    messages = [decoded for frame in outputs if (decoded := decoder.feed(frame)) is not None]
    assert [message.kind for message in messages[:2]] == [MessageKind.HELLO, MessageKind.SNAPSHOT]
    assert messages[0].payload["api_version"] == 42
    assert not hasattr(observer, "OnMidiIn")
    before = len(outputs)
    observer.OnDirtyChannel(0, 3)  # type: ignore[attr-defined]
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    assert len(outputs) >= before + 2


def test_plugin_scan_is_bounded_per_idle_and_capped() -> None:
    observer, _, calls = load_observer()
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    recorder.dirty_focus = False
    recorder.dirty_pattern = False
    recorder.dirty_steps = False
    recorder.dirty_plugin = True
    recorder.poll_plugin()
    recorder.poll_plugin()
    assert calls["plugin_values"] == 64
    for _ in range(10):
        recorder.poll_plugin()
    assert calls["plugin_values"] == 512
