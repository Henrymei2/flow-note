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


def load_observer() -> tuple[ModuleType, list[bytes], dict[str, int], dict[str, Any]]:
    outputs: list[bytes] = []
    calls = {"plugin_values": 0}
    undo_state: dict[str, Any] = {
        "position": 3,
        "count": 4,
        "last": 3,
        "hint": "Initial project state",
    }
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
    modules["general"].getUndoHistoryPos = lambda: undo_state["position"]  # type: ignore[attr-defined]
    modules["general"].getUndoHistoryCount = lambda: undo_state["count"]  # type: ignore[attr-defined]
    modules["general"].getUndoHistoryLast = lambda: undo_state["last"]  # type: ignore[attr-defined]
    modules["general"].getUndoLevelHint = lambda: undo_state["hint"]  # type: ignore[attr-defined]
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
    return observer, outputs, calls, undo_state


def decode_messages(outputs: list[bytes]) -> list[Any]:
    decoder = MessageDecoder()
    return [decoded for frame in outputs if (decoded := decoder.feed(frame)) is not None]


def event_messages(outputs: list[bytes]) -> list[Any]:
    return [message for message in decode_messages(outputs) if message.kind is MessageKind.EVENT]


def clear_initial_dirty_state(recorder: Any) -> None:
    recorder.dirty_mixer_tracks = []
    recorder.dirty_pattern = False
    recorder.dirty_steps = False
    recorder.dirty_plugin = False
    recorder.plugin_scan = None


def test_observer_hello_snapshot_and_callback_mapping() -> None:
    observer, outputs, _, _ = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    messages = decode_messages(outputs)
    assert [message.kind for message in messages[:2]] == [MessageKind.HELLO, MessageKind.SNAPSHOT]
    assert messages[0].payload["api_version"] == 42
    assert not hasattr(observer, "OnMidiIn")
    before = len(outputs)
    observer.OnDirtyChannel(0, 0)  # type: ignore[attr-defined]
    assert len(outputs) > before


def test_undo_history_uses_official_hint_and_direction() -> None:
    observer, outputs, _, undo = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    outputs.clear()
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    clear_initial_dirty_state(recorder)

    undo.update(position=4, count=5, last=4, hint="Add notes to Piano roll")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    recorder.flush_pending_undo(recorder.pending_undo["captured_at"] + 1)

    undo.update(position=3, count=5, last=4, hint="Previous edit")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    recorder.flush_pending_undo(recorder.pending_undo["captured_at"] + 1)

    undo.update(position=4, count=5, last=4, hint="Add notes to Piano roll")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    recorder.flush_pending_undo(recorder.pending_undo["captured_at"] + 1)

    events = event_messages(outputs)
    assert [(event.payload["action"], event.payload["summary"]) for event in events] == [
        ("edit", "Add notes to Piano roll"),
        ("undo", "Undo: Add notes to Piano roll"),
        ("redo", "Redo: Add notes to Piano roll"),
    ]


def test_undo_hint_is_suppressed_when_state_diff_explains_edit() -> None:
    observer, outputs, _, undo = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    outputs.clear()
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    clear_initial_dirty_state(recorder)

    undo.update(position=4, count=5, last=4, hint="Add channel")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    observer.OnDirtyChannel(0, 0)  # type: ignore[attr-defined]
    recorder.flush_pending_undo(recorder.pending_undo["captured_at"] + 1)

    events = event_messages(outputs)
    assert [(event.payload["category"], event.payload["action"]) for event in events] == [
        ("channel", "created")
    ]


def test_navigation_and_non_musical_changes_are_not_emitted() -> None:
    observer, outputs, _, undo = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    outputs.clear()

    observer.OnDirtyChannel(0, 3)  # type: ignore[attr-defined]
    observer.OnDirtyChannel(0, 4)  # type: ignore[attr-defined]
    observer.OnRefresh(32 | 2048)  # type: ignore[attr-defined]
    observer.OnSendTempMsg("Focused Mixer", 1000)  # type: ignore[attr-defined]
    undo.update(position=4, count=5, last=4, hint="Rename pattern")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]

    assert event_messages(outputs) == []


def test_playback_and_pattern_selection_are_not_emitted() -> None:
    observer, outputs, _, _ = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    clear_initial_dirty_state(recorder)
    recorder.poll_transport()
    recorder.poll_pattern()
    outputs.clear()

    observer.transport.isPlaying = lambda: 1  # type: ignore[attr-defined]
    observer.patterns.patternNumber = lambda: 2  # type: ignore[attr-defined]
    recorder.poll_transport()
    recorder.poll_pattern()

    assert event_messages(outputs) == []


def test_tempo_change_is_still_emitted() -> None:
    observer, outputs, _, _ = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    recorder.poll_transport()
    outputs.clear()

    observer.mixer.getCurrentTempo = lambda: 132.5  # type: ignore[attr-defined]
    recorder.poll_transport()

    events = event_messages(outputs)
    assert len(events) == 1
    assert events[0].payload["action"] == "tempo_changed"
    assert events[0].payload["payload"] == {"before": 128.0, "after": 132.5}


def test_plugin_scan_is_bounded_per_idle_and_capped() -> None:
    observer, _, calls, _ = load_observer()
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    recorder.dirty_pattern = False
    recorder.dirty_steps = False
    recorder.dirty_plugin = True
    recorder.poll_plugin()
    recorder.poll_plugin()
    assert calls["plugin_values"] == 64
    for _ in range(10):
        recorder.poll_plugin()
    assert calls["plugin_values"] == 512
