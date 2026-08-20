from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from fl_activity_recorder.observer_contract import (
    OBSERVER_BUILD,
    OBSERVER_CAPABILITIES,
    OBSERVER_SCHEMA,
    OBSERVER_VERSION,
)
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
    state: dict[str, Any] = {
        "playing": 0,
        "recording": 0,
        "loop_mode": 1,
        "tempo": 128.0,
        "channel_selected": {0},
        "channel_names": {0: "Synth", 1: "Drums"},
        "channel_colors": {0: 10, 1: 20},
        "pattern": 1,
        "pattern_count": 2,
        "pattern_names": {1: "Verse", 2: "Chorus"},
        "pattern_colors": {1: 30, 2: 40},
        "mixer_selected": {1},
        "mixer_current": 1,
        "mixer_tracks": {
            0: {"name": "Master", "color": 1, "volume": 0.8, "pan": 0.0, "sep": 0.0},
            1: {"name": "Music", "color": 2, "volume": 0.7, "pan": 0.0, "sep": 0.0},
            2: {"name": "Drums", "color": 3, "volume": 0.6, "pan": 0.0, "sep": 0.0},
        },
        "focus": {"id": 5, "caption": "FLEX", "plugin": "FLEX"},
        "active_effect": (1, 0),
        "plugin_valid": True,
        "plugin_values": [index / 600 for index in range(600)],
        "undo": {"position": 3, "count": 4, "last": 3, "hint": "Initial state"},
        "grid": set(),
    }
    modules: dict[str, ModuleType] = {}
    for name in [
        "channels",
        "device",
        "general",
        "midi",
        "mixer",
        "patterns",
        "plugins",
        "transport",
        "ui",
    ]:
        modules[name] = FakeModule(name)

    modules["device"].midiOutSysex = outputs.append  # type: ignore[attr-defined]
    modules["general"].getVersion = lambda: 45  # type: ignore[attr-defined]
    modules["general"].getProjectTitle = lambda: "Test Project"  # type: ignore[attr-defined]
    modules["general"].getProjectAuthor = lambda: "Tester"  # type: ignore[attr-defined]
    modules["general"].getChangedFlag = lambda: 1  # type: ignore[attr-defined]
    modules["general"].getUndoHistoryPos = lambda: state["undo"]["position"]  # type: ignore[attr-defined]
    modules["general"].getUndoHistoryCount = lambda: state["undo"]["count"]  # type: ignore[attr-defined]
    modules["general"].getUndoHistoryLast = lambda: state["undo"]["last"]  # type: ignore[attr-defined]
    modules["general"].getUndoLevelHint = lambda: state["undo"]["hint"]  # type: ignore[attr-defined]

    modules["transport"].isPlaying = lambda: state["playing"]  # type: ignore[attr-defined]
    modules["transport"].isRecording = lambda: state["recording"]  # type: ignore[attr-defined]
    modules["transport"].getLoopMode = lambda: state["loop_mode"]  # type: ignore[attr-defined]

    modules["channels"].channelCount = lambda global_count=0: 2  # type: ignore[attr-defined]
    modules["channels"].isChannelSelected = lambda index, *args: index in state["channel_selected"]  # type: ignore[attr-defined]
    modules["channels"].selectedChannel = lambda *args: min(state["channel_selected"], default=-1)  # type: ignore[attr-defined]
    modules["channels"].getChannelName = lambda index, *args: state["channel_names"].get(index, "")  # type: ignore[attr-defined]
    modules["channels"].getChannelColor = lambda index, *args: state["channel_colors"].get(index, 0)  # type: ignore[attr-defined]
    modules["channels"].getGridBit = lambda channel, position: position in state["grid"]  # type: ignore[attr-defined]

    modules["patterns"].patternNumber = lambda: state["pattern"]  # type: ignore[attr-defined]
    modules["patterns"].patternCount = lambda: state["pattern_count"]  # type: ignore[attr-defined]
    modules["patterns"].patternMax = lambda: 2  # type: ignore[attr-defined]
    modules["patterns"].getPatternName = lambda index: state["pattern_names"].get(index, "")  # type: ignore[attr-defined]
    modules["patterns"].getPatternColor = lambda index: state["pattern_colors"].get(index, 0)  # type: ignore[attr-defined]

    modules["mixer"].getCurrentTempo = lambda: state["tempo"]  # type: ignore[attr-defined]
    modules["mixer"].getTrackCount = lambda: 2  # type: ignore[attr-defined]
    modules["mixer"].trackNumber = lambda: state["mixer_current"]  # type: ignore[attr-defined]
    modules["mixer"].isTrackSelected = lambda index: index in state["mixer_selected"]  # type: ignore[attr-defined]
    modules["mixer"].getTrackName = lambda index: state["mixer_tracks"][index]["name"]  # type: ignore[attr-defined]
    modules["mixer"].getTrackColor = lambda index: state["mixer_tracks"][index]["color"]  # type: ignore[attr-defined]
    modules["mixer"].getTrackVolume = lambda index: state["mixer_tracks"][index]["volume"]  # type: ignore[attr-defined]
    modules["mixer"].getTrackPan = lambda index: state["mixer_tracks"][index]["pan"]  # type: ignore[attr-defined]
    modules["mixer"].getTrackStereoSep = lambda index: state["mixer_tracks"][index]["sep"]  # type: ignore[attr-defined]
    modules["mixer"].getActiveEffectIndex = lambda: state["active_effect"]  # type: ignore[attr-defined]

    modules["ui"].getVersion = lambda: "26.2"  # type: ignore[attr-defined]
    modules["ui"].getFocusedPluginName = lambda: state["focus"]["plugin"]  # type: ignore[attr-defined]
    modules["ui"].getFocusedFormCaption = lambda: state["focus"]["caption"]  # type: ignore[attr-defined]
    modules["ui"].getFocusedFormID = lambda: state["focus"]["id"]  # type: ignore[attr-defined]
    modules["ui"].getFocusedNodeCaption = lambda: ""  # type: ignore[attr-defined]

    modules["plugins"].isValid = lambda *args: state["plugin_valid"]  # type: ignore[attr-defined]
    modules["plugins"].getPluginName = lambda *args: "FLEX"  # type: ignore[attr-defined]
    modules["plugins"].getParamCount = lambda *args: len(state["plugin_values"])  # type: ignore[attr-defined]
    modules["plugins"].getParamName = lambda index, *args: f"Param {index}"  # type: ignore[attr-defined]

    def plugin_value(index: int, *args: object) -> float:
        calls["plugin_values"] += 1
        return float(state["plugin_values"][index])

    modules["plugins"].getParamValue = plugin_value  # type: ignore[attr-defined]
    for name, value in {
        "HW_Dirty_Mixer_Sel": 1,
        "HW_Dirty_Mixer_Controls": 4,
        "HW_Dirty_FocusedWindow": 32,
        "HW_Dirty_LEDs": 256,
        "HW_Dirty_Patterns": 1024,
        "HW_Dirty_Tracks": 2048,
        "HW_Dirty_ControlValues": 4096,
        "HW_Dirty_Colors": 8192,
        "HW_Dirty_Names": 16384,
        "HW_ChannelEvent": 65536,
        "HW_Dirty_Undo": 131072,
    }.items():
        setattr(modules["midi"], name, value)

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
    return observer, outputs, calls, state


def decode_messages(outputs: list[bytes]) -> list[Any]:
    decoder = MessageDecoder()
    return [decoded for frame in outputs if (decoded := decoder.feed(frame)) is not None]


def event_messages(outputs: list[bytes]) -> list[Any]:
    return [message for message in decode_messages(outputs) if message.kind is MessageKind.EVENT]


def drain_metadata(recorder: Any) -> None:
    while recorder.metadata_queue:
        recorder.poll_metadata()


def drain_plugin(recorder: Any) -> None:
    recorder.plugin_dirty_at = 0.0
    for _ in range(20):
        recorder.poll_plugin()
        if recorder.plugin_scan is None and recorder.plugin_request_trigger is None:
            return
    raise AssertionError("plugin scan did not finish")


def settle_undo(recorder: Any) -> None:
    for _ in range(4):
        if recorder.poll_undo():
            return
    raise AssertionError("undo state did not settle")


def test_observer_contract_hello_and_full_refresh() -> None:
    observer, outputs, _, _ = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    messages = decode_messages(outputs)
    hello = messages[0].payload
    assert [message.kind for message in messages[:2]] == [MessageKind.HELLO, MessageKind.SNAPSHOT]
    assert hello["observer_version"] == OBSERVER_VERSION == observer.OBSERVER_VERSION
    assert hello["observer_schema"] == OBSERVER_SCHEMA == observer.OBSERVER_SCHEMA
    assert hello["observer_build"] == OBSERVER_BUILD == observer.OBSERVER_BUILD
    assert tuple(hello["capabilities"]) == OBSERVER_CAPABILITIES
    outputs.clear()
    observer.OnDoFullRefresh()  # type: ignore[attr-defined]
    assert [message.kind for message in decode_messages(outputs)] == [MessageKind.SNAPSHOT]


def test_playback_uses_indicator_trigger_and_idle_fallback() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    outputs.clear()
    state["playing"] = 1
    observer.OnUpdateBeatIndicator(1)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]
    event = next(
        item for item in event_messages(outputs) if item.payload["action"] == "playing_changed"
    )
    assert event.payload["confidence"] == "state_diff"
    assert event.payload["payload"]["trigger"]["callback"] == "OnUpdateBeatIndicator"

    outputs.clear()
    state["playing"] = 0
    observer.OnIdle()  # type: ignore[attr-defined]
    event = next(
        item for item in event_messages(outputs) if item.payload["action"] == "playing_changed"
    )
    assert event.payload["payload"]["trigger"]["fallback_poll"] is True


def test_channel_pattern_and_mixer_selection_semantics() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    outputs.clear()

    state["channel_selected"] = {1}
    observer.OnDirtyChannel(1, 4)  # type: ignore[attr-defined]
    assert event_messages(outputs) == []
    observer.OnRefresh(65536)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]

    state["pattern"] = 2
    observer.OnRefresh(1024)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]

    state["mixer_selected"] = {2}
    state["mixer_current"] = 2
    observer.OnRefresh(1)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]

    events = event_messages(outputs)
    selected = {(item.payload["category"], item.payload["action"]): item for item in events}
    assert selected[("channel", "selection_changed")].payload["confidence"] == "exact"
    assert selected[("pattern", "selection_changed")].payload["confidence"] == "state_diff"
    assert selected[("mixer", "selection_changed")].payload["confidence"] == "exact"


def test_channel_callback_is_deferred_and_metadata_diffs_cover_noncurrent_entities() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    drain_metadata(recorder)
    outputs.clear()

    state["channel_names"][1] = "Percussion"
    observer.OnDirtyChannel(1, 3)  # type: ignore[attr-defined]
    assert event_messages(outputs) == []
    observer.OnRefresh(65536)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]

    state["channel_colors"][1] = 99
    state["pattern_names"][2] = "Bridge"
    state["pattern_colors"][2] = 88
    state["mixer_tracks"][2]["name"] = "Perc Bus"
    state["mixer_tracks"][2]["color"] = 77
    observer.OnRefresh(8192 | 16384)  # type: ignore[attr-defined]
    drain_metadata(recorder)

    events = event_messages(outputs)
    kinds = {
        (item.payload["category"], item.payload["action"], item.payload["entity_key"]): item
        for item in events
    }
    assert kinds[("channel", "renamed", "1")].payload["confidence"] == "exact"
    assert kinds[("channel", "recolored", "1")].payload["confidence"] == "state_diff"
    assert kinds[("pattern", "renamed", "2")].payload["confidence"] == "state_diff"
    assert kinds[("pattern", "recolored", "2")].payload["confidence"] == "state_diff"
    assert kinds[("mixer", "renamed", "2")].payload["confidence"] == "state_diff"
    assert kinds[("mixer", "recolored", "2")].payload["confidence"] == "state_diff"


def test_mixer_controls_wait_for_matching_refresh() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    recorder.mixer_state[1] = recorder.read_mixer_track(1)
    outputs.clear()
    state["mixer_tracks"][1]["volume"] = 0.55
    observer.OnDirtyMixerTrack(1)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]
    assert not any(item.payload["action"] == "track_changed" for item in event_messages(outputs))
    observer.OnRefresh(4)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]
    event = next(
        item for item in event_messages(outputs) if item.payload["action"] == "track_changed"
    )
    assert event.payload["payload"]["changes"]["volume"] == {"before": 0.7, "after": 0.55}


def test_plugin_baseline_parameter_diff_and_unresolved_fallback() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    drain_plugin(recorder)
    outputs.clear()

    state["plugin_values"][5] = 0.99
    observer.OnRefresh(4096)  # type: ignore[attr-defined]
    drain_plugin(recorder)
    event = next(
        item for item in event_messages(outputs) if item.payload["action"] == "parameters_changed"
    )
    assert event.payload["confidence"] == "state_diff"
    assert event.payload["payload"]["changes"][0]["index"] == 5

    outputs.clear()
    state["active_effect"] = None
    state["focus"] = {"id": 0, "caption": "Mixer", "plugin": ""}
    observer.OnRefresh(4096)  # type: ignore[attr-defined]
    drain_plugin(recorder)
    event = next(
        item for item in event_messages(outputs) if item.payload["action"] == "controls_changed"
    )
    assert event.payload["payload"]["reason"] == "plugin_target_unavailable"


def test_first_plugin_change_without_baseline_is_not_lost() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    recorder.plugin_request_trigger = None
    recorder.plugin_state = {}
    outputs.clear()
    state["plugin_values"][1] = 0.5
    observer.OnRefresh(4096)  # type: ignore[attr-defined]
    drain_plugin(recorder)
    event = next(
        item for item in event_messages(outputs) if item.payload["action"] == "controls_changed"
    )
    assert event.payload["payload"]["reason"] == "baseline_unavailable"


def test_plugin_scan_is_bounded_and_dirty_generation_is_retained() -> None:
    observer, outputs, calls, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    recorder.plugin_dirty_at = 0.0
    recorder.poll_plugin()
    recorder.poll_plugin()
    assert calls["plugin_values"] == 64
    observer.OnRefresh(4096)  # type: ignore[attr-defined]
    for _ in range(20):
        recorder.poll_plugin()
    assert calls["plugin_values"] >= 512
    assert calls["plugin_values"] <= 1024
    assert len(state["plugin_values"]) == 600


def test_undo_edit_undo_and_redo_are_deferred_and_specific() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    outputs.clear()

    state["undo"].update(position=4, count=5, last=4, hint="Add notes")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    assert event_messages(outputs) == []
    settle_undo(recorder)

    state["undo"].update(position=3, count=5, last=4, hint="Previous edit")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    settle_undo(recorder)

    state["undo"].update(position=4, count=5, last=4, hint="Add notes")
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    settle_undo(recorder)

    events = [
        item for item in event_messages(outputs) if item.payload["category"] == "project_edit"
    ]
    assert [(item.payload["action"], item.payload["summary"]) for item in events] == [
        ("edit", "Add notes"),
        ("undo", "Undo: Add notes"),
        ("redo", "Redo: Add notes"),
    ]
    assert all(item.payload["confidence"] == "state_diff" for item in events)


def test_unchanged_undo_refresh_stays_signal_only() -> None:
    observer, outputs, _, _ = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    outputs.clear()
    observer.OnRefresh(131072)  # type: ignore[attr-defined]
    settle_undo(recorder)
    event = next(
        item for item in event_messages(outputs) if item.payload["action"] == "history_changed"
    )
    assert event.payload["confidence"] == "signal_only"


def test_step_changes_and_raw_refreshes_keep_provenance_without_led_event() -> None:
    observer, outputs, _, state = load_observer()
    observer.OnInit()  # type: ignore[attr-defined]
    recorder = observer.RECORDER  # type: ignore[attr-defined]
    recorder.poll_steps()
    outputs.clear()
    state["grid"] = {1, 4}
    observer.OnRefresh(1024)  # type: ignore[attr-defined]
    observer.OnIdle()  # type: ignore[attr-defined]
    observer.OnRefresh(1 | 256 | 8192)  # type: ignore[attr-defined]
    events = event_messages(outputs)
    step = next(item for item in events if item.payload["action"] == "steps_changed")
    assert step.payload["payload"]["changed_steps"] == [1, 4]
    refresh = next(
        item
        for item in events
        if item.payload["action"] == "host_refresh" and "colors" in item.payload["payload"]["names"]
    )
    assert refresh.payload["payload"]["trigger"]["refresh_flags"] == 1 | 256 | 8192
    assert "led" not in " ".join(refresh.payload["payload"]["names"])
