# name=Activity Recorder
"""Transparent FL Studio activity observer using documented MIDI scripting APIs."""

import json
import time

import channels
import device
import general
import midi
import mixer
import patterns
import plugins
import transport
import ui

PROTOCOL_VERSION = 1
MINIMUM_API_VERSION = 42
OBSERVER_VERSION = "0.3.1"
OBSERVER_SCHEMA = 3
OBSERVER_BUILD = "project-title-fallback-v1"
OBSERVER_CAPABILITIES = (
    "transport_state",
    "channel_selection",
    "pattern_selection",
    "mixer_selection",
    "entity_metadata",
    "focused_plugin_parameters",
    "undo_direction",
    "trigger_provenance",
)

MANUFACTURER_ID = 0x7D
MAX_FRAME_SIZE = 1024
HEADER_SIZE = 18
FRAME_OVERHEAD = HEADER_SIZE + 1
MAX_PLUGIN_PARAMS = 512
PLUGIN_PARAMS_PER_IDLE = 64
MAX_CHANNELS = 2048
MAX_PATTERNS = 999
MAX_MIXER_TRACKS = 128
HEARTBEAT_SECONDS = 1.0
CALLBACK_SETTLE_SECONDS = 0.25
B64URL_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

KIND_HELLO = 1
KIND_HEARTBEAT = 2
KIND_EVENT = 3
KIND_SNAPSHOT = 4
KIND_DIAGNOSTIC = 5


def _pack7(value, width):
    return [(value >> (7 * offset)) & 0x7F for offset in reversed(range(width))]


def _crc32(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def _b64url_encode(data):
    result = bytearray()
    accumulator = 0
    bits = 0
    for byte in data:
        accumulator = (accumulator << 8) | byte
        bits += 8
        while bits >= 6:
            bits -= 6
            result.append(B64URL_ALPHABET[(accumulator >> bits) & 0x3F])
    if bits:
        result.append(B64URL_ALPHABET[(accumulator << (6 - bits)) & 0x3F])
    return bytes(result)


def _safe(function, default=None, *args):
    try:
        return function(*args)
    except Exception:
        return default


def _safe_int(function, default=-1, *args):
    value = _safe(function, default, *args)
    return value if isinstance(value, int) else default


def _read_project_title():
    """Return project metadata title, falling back to the FL Studio window title."""
    project_title = _safe(getattr(general, "getProjectTitle", lambda: ""), "")
    if isinstance(project_title, str) and project_title.strip():
        return project_title.strip()
    window_title = _safe(getattr(ui, "getProgTitle", lambda: ""), "")
    return window_title.strip() if isinstance(window_title, str) else ""


def _flag(name, default):
    value = getattr(midi, name, default)
    return value if isinstance(value, int) else default


def _event(
    category,
    action,
    summary,
    confidence,
    entity_type=None,
    entity_key=None,
    payload=None,
    observed_monotonic_seconds=None,
):
    return {
        "category": category,
        "action": action,
        "summary": summary,
        "confidence": confidence,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "payload": payload or {},
        "observed_monotonic_seconds": (
            time.monotonic() if observed_monotonic_seconds is None else observed_monotonic_seconds
        ),
    }


def _with_trigger(payload, callback, flags=0, fallback_poll=False):
    result = dict(payload or {})
    result["trigger"] = {
        "callback": callback,
        "refresh_flags": int(flags or 0),
        "fallback_poll": bool(fallback_poll),
    }
    return result


def _unhandled_refresh_details(flags):
    definitions = (
        ("mixer_selection", "HW_Dirty_Mixer_Sel", 1),
        ("mixer_display", "HW_Dirty_Mixer_Display", 2),
        ("remote_links", "HW_Dirty_RemoteLinks", 16),
        ("performance_layout", "HW_Dirty_Performance", 64),
        ("remote_link_values", "HW_Dirty_RemoteLinkValues", 512),
        ("colors", "HW_Dirty_Colors", 8192),
        ("names", "HW_Dirty_Names", 16384),
        ("channel_rack_group", "HW_Dirty_ChannelRackGroup", 32768),
        ("channel_rack", "HW_Dirty_ChannelRack", 262144),
        ("graph_editor", "HW_Dirty_GraphEditor", 524288),
    )
    names = []
    mask = 0
    for label, constant, default in definitions:
        value = _flag(constant, default)
        if flags & value:
            names.append(label)
            mask |= value
    return mask, names


class ActivityRecorder:
    def __init__(self):
        self.sequence = 0
        self.last_heartbeat = 0.0
        self.transport_state = None
        self.transport_trigger = None
        self.focus_state = None
        self.focus_trigger = None
        self.channel_selection = None
        self.pattern_selection = None
        self.mixer_selection = None
        self.pattern_state = None
        self.step_state = {}
        self.channel_metadata = {}
        self.pattern_metadata = {}
        self.mixer_metadata = {}
        self.mixer_state = {}
        self.metadata_queue = []
        self.pending_channel_events = []
        self.channel_flush = False
        self.pending_mixer_tracks = []
        self.mixer_flush = False
        self.mixer_selection_trigger = None
        self.pattern_trigger = None
        self.dirty_pattern_state = False
        self.dirty_steps = True
        self.plugin_state = {}
        self.plugin_scan = None
        self.plugin_generation = 0
        self.plugin_request_report = False
        self.plugin_request_trigger = None
        self.plugin_dirty_at = 0.0
        self.undo_state = None
        self.undo_hints = {}
        self.pending_undo = None

    def send(self, kind, payload):
        self.sequence += 1
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encoded = _b64url_encode(raw)
        crc = _crc32(raw)
        capacity = MAX_FRAME_SIZE - FRAME_OVERHEAD
        chunks = [
            encoded[index : index + capacity] for index in range(0, len(encoded), capacity)
        ] or [b""]
        for index, chunk in enumerate(chunks):
            header = [0xF0, MANUFACTURER_ID, PROTOCOL_VERSION, kind]
            header += _pack7(self.sequence, 5)
            header += _pack7(index, 2)
            header += _pack7(len(chunks), 2)
            header += _pack7(crc, 5)
            try:
                device.midiOutSysex(bytes(header) + chunk + bytes([0xF7]))
            except Exception as error:
                print("Activity Recorder SysEx error: " + str(error))
                return

    def emit(self, event):
        self.send(KIND_EVENT, event)

    def initialize(self):
        self.reset_state()
        api_version = _safe(general.getVersion, 0)
        print(
            f"Activity Recorder observer {OBSERVER_VERSION} "
            f"schema={OBSERVER_SCHEMA} build={OBSERVER_BUILD}"
        )
        self.send(
            KIND_HELLO,
            {
                "observer_version": OBSERVER_VERSION,
                "observer_schema": OBSERVER_SCHEMA,
                "observer_build": OBSERVER_BUILD,
                "capabilities": list(OBSERVER_CAPABILITIES),
                "api_version": api_version,
                "fl_version": _safe(getattr(ui, "getVersion", lambda: ""), ""),
                "project_title": _read_project_title(),
            },
        )
        if api_version < MINIMUM_API_VERSION:
            self.send(
                KIND_DIAGNOSTIC,
                {
                    "level": "error",
                    "code": "unsupported_api",
                    "message": "FL Studio MIDI scripting API 42 or newer is required",
                    "api_version": api_version,
                },
            )
        self.send(KIND_SNAPSHOT, self.snapshot())
        self.last_heartbeat = time.monotonic()

    def reset_state(self):
        self.transport_state = self.read_transport()
        self.focus_state = self.read_focus()
        self.channel_selection = self.read_channel_selection()
        self.pattern_selection = _safe_int(patterns.patternNumber, -1)
        self.mixer_selection = self.read_mixer_selection()
        self.pattern_state = self.read_pattern_state(self.pattern_selection)
        self.step_state = {}
        self.channel_metadata = {}
        self.pattern_metadata = {}
        self.mixer_metadata = {}
        self.mixer_state = {}
        self.metadata_queue = []
        self.pending_channel_events = []
        self.channel_flush = False
        self.pending_mixer_tracks = []
        self.mixer_flush = False
        self.transport_trigger = None
        self.focus_trigger = None
        self.mixer_selection_trigger = None
        self.pattern_trigger = None
        self.dirty_pattern_state = False
        self.dirty_steps = True
        self.plugin_state = {}
        self.plugin_scan = None
        self.plugin_generation = 0
        self.plugin_request_report = False
        self.plugin_request_trigger = ("OnInit", 0, False)
        self.plugin_dirty_at = 0.0
        self.undo_state = self.read_undo_state()
        self.undo_hints = {}
        if self.undo_state["position"] >= 0 and self.undo_state["hint"]:
            self.undo_hints[self.undo_state["position"]] = self.undo_state["hint"]
        self.pending_undo = None
        self.schedule_full_metadata_scan(False, "OnInit", 0)

    def snapshot(self):
        channel_index = self.channel_selection[0] if self.channel_selection else -1
        pattern_index = self.pattern_selection if self.pattern_selection is not None else -1
        mixer_current = _safe_int(mixer.trackNumber, -1)
        return {
            "project_title": _read_project_title(),
            "project_author": _safe(getattr(general, "getProjectAuthor", lambda: ""), ""),
            "changed_flag": _safe(general.getChangedFlag, 0),
            "playing": self.transport_state["playing"],
            "recording": self.transport_state["recording"],
            "loop_mode": self.transport_state["loop_mode"],
            "tempo": self.transport_state["tempo"],
            "selected_channels": list(self.channel_selection or ()),
            "selected_channel": channel_index,
            "selected_channel_name": self.read_channel_metadata(channel_index).get("name", "")
            if channel_index >= 0
            else "",
            "selected_pattern": pattern_index,
            "selected_pattern_name": _safe(patterns.getPatternName, "", pattern_index)
            if pattern_index >= 0
            else "",
            "selected_mixer_tracks": list(self.mixer_selection or ()),
            "selected_mixer_track": mixer_current,
            "focused_caption": self.focus_state["caption"],
            "focused_form_id": self.focus_state["form_id"],
            "focused_plugin": self.focus_state["plugin"],
        }

    def heartbeat(self, now):
        if now - self.last_heartbeat < HEARTBEAT_SECONDS:
            return
        self.last_heartbeat = now
        self.send(
            KIND_HEARTBEAT,
            {
                "project_title": _read_project_title(),
                "api_version": _safe(general.getVersion, 0),
                "playing": bool(_safe(transport.isPlaying, 0)),
                "recording": bool(_safe(transport.isRecording, 0)),
            },
        )

    def read_transport(self):
        return {
            "playing": bool(_safe(transport.isPlaying, 0)),
            "recording": bool(_safe(transport.isRecording, 0)),
            "loop_mode": _safe(transport.getLoopMode, 0),
            "tempo": _safe(mixer.getCurrentTempo, 0.0),
        }

    def poll_transport(self):
        current = self.read_transport()
        previous = self.transport_state
        self.transport_state = current
        callback, flags, fallback = self.transport_trigger or ("OnIdle", 0, True)
        self.transport_trigger = None
        if previous is None:
            return
        summaries = {
            "playing": "Playback started" if current["playing"] else "Playback stopped",
            "recording": "FL Studio recording enabled"
            if current["recording"]
            else "FL Studio recording disabled",
            "loop_mode": "Playback mode changed to "
            + ("song" if current["loop_mode"] else "pattern"),
            "tempo": "Tempo changed to " + str(round(float(current["tempo"]), 2)) + " BPM",
        }
        for key in ("playing", "recording", "loop_mode", "tempo"):
            if previous.get(key) != current.get(key):
                self.emit(
                    _event(
                        "transport",
                        key + "_changed",
                        summaries[key],
                        "state_diff",
                        "transport",
                        key,
                        _with_trigger(
                            {"before": previous.get(key), "after": current.get(key)},
                            callback,
                            flags,
                            fallback,
                        ),
                    )
                )

    def read_focus(self):
        return {
            "form_id": _safe(ui.getFocusedFormID, -1),
            "caption": _safe(ui.getFocusedFormCaption, ""),
            "plugin": _safe(ui.getFocusedPluginName, ""),
            "browser_node": _safe(getattr(ui, "getFocusedNodeCaption", lambda: ""), ""),
        }

    def poll_focus(self):
        current = self.read_focus()
        previous = self.focus_state
        self.focus_state = current
        callback, flags, fallback = self.focus_trigger or ("OnIdle", 0, True)
        self.focus_trigger = None
        if previous is not None and previous != current:
            label = (
                current["plugin"] or current["caption"] or current["browser_node"] or "FL Studio"
            )
            self.emit(
                _event(
                    "navigation",
                    "focus_changed",
                    "Focused " + str(label),
                    "exact" if callback == "OnRefresh" else "state_diff",
                    "window",
                    str(current["form_id"]),
                    _with_trigger(
                        {"before": previous, "after": current}, callback, flags, fallback
                    ),
                )
            )
            if current["plugin"]:
                self.request_plugin_scan(False, callback, flags, fallback)

    def read_channel_selection(self):
        count = max(0, min(_safe_int(channels.channelCount, 0, 1), MAX_CHANNELS))
        selected = []
        for index in range(count):
            if bool(_safe(channels.isChannelSelected, 0, index, True)):
                selected.append(index)
        if not selected:
            index = _safe_int(channels.selectedChannel, -1, 1, 0, 1)
            if index >= 0:
                selected.append(index)
        return tuple(selected)

    def poll_channel_selection(self, callback="OnIdle", flags=0, fallback=True, exact=False):
        current = self.read_channel_selection()
        previous = self.channel_selection
        self.channel_selection = current
        if previous is not None and previous != current:
            self.emit(
                _event(
                    "channel",
                    "selection_changed",
                    "Channel selection changed",
                    "exact" if exact else "state_diff",
                    "channel_selection",
                    payload=_with_trigger(
                        {"before": list(previous), "after": list(current)},
                        callback,
                        flags,
                        fallback,
                    ),
                )
            )
            self.dirty_steps = True

    def poll_pattern_selection(self):
        current = _safe_int(patterns.patternNumber, -1)
        previous = self.pattern_selection
        self.pattern_selection = current
        callback, flags, fallback = self.pattern_trigger or ("OnIdle", 0, True)
        self.pattern_trigger = None
        if previous is not None and previous != current:
            self.emit(
                _event(
                    "pattern",
                    "selection_changed",
                    "Selected pattern " + str(current),
                    "state_diff",
                    "pattern_selection",
                    str(current),
                    _with_trigger(
                        {"before": previous, "after": current}, callback, flags, fallback
                    ),
                )
            )
            self.dirty_steps = True
        if self.dirty_pattern_state:
            self.poll_pattern_state(callback, flags, fallback)
            self.dirty_pattern_state = False

    def read_mixer_selection(self):
        count = max(
            0, min(_safe_int(getattr(mixer, "getTrackCount", lambda: 0), 0) + 1, MAX_MIXER_TRACKS)
        )
        selected = [index for index in range(count) if bool(_safe(mixer.isTrackSelected, 0, index))]
        current = _safe_int(mixer.trackNumber, -1)
        if current >= 0 and current not in selected:
            selected.append(current)
        return tuple(sorted(selected))

    def poll_mixer_selection(self):
        current = self.read_mixer_selection()
        previous = self.mixer_selection
        self.mixer_selection = current
        trigger = self.mixer_selection_trigger
        self.mixer_selection_trigger = None
        callback, flags, fallback = trigger or ("OnIdle", 0, True)
        if previous is not None and previous != current:
            self.emit(
                _event(
                    "mixer",
                    "selection_changed",
                    "Mixer selection changed",
                    "exact" if trigger else "state_diff",
                    "mixer_selection",
                    payload=_with_trigger(
                        {"before": list(previous), "after": list(current)},
                        callback,
                        flags,
                        fallback,
                    ),
                )
            )

    def read_pattern_state(self, index):
        if index is None or index < 0:
            return {"index": -1, "name": "", "color": 0, "count": _safe(patterns.patternCount, 0)}
        return {
            "index": index,
            "name": _safe(patterns.getPatternName, "", index),
            "color": _safe(patterns.getPatternColor, 0, index),
            "count": _safe(patterns.patternCount, 0),
        }

    def poll_pattern_state(self, callback="OnIdle", flags=0, fallback=True):
        current = self.read_pattern_state(self.pattern_selection)
        previous = self.pattern_state
        self.pattern_state = current
        if previous is not None and previous != current:
            changed = {
                key: {"before": previous.get(key), "after": current.get(key)}
                for key in current
                if previous.get(key) != current.get(key)
            }
            non_metadata = {
                key: value
                for key, value in changed.items()
                if key not in ("index", "name", "color")
            }
            if non_metadata:
                self.emit(
                    _event(
                        "pattern",
                        "state_changed",
                        "Pattern state changed",
                        "state_diff",
                        "pattern",
                        str(current["index"]),
                        _with_trigger(
                            {"changes": non_metadata, "state": current}, callback, flags, fallback
                        ),
                    )
                )

    def poll_steps(self):
        channel_index = self.channel_selection[0] if self.channel_selection else -1
        pattern_index = self.pattern_selection if self.pattern_selection is not None else -1
        self.dirty_steps = False
        if channel_index < 0 or pattern_index < 0:
            return
        key = str(pattern_index) + ":" + str(channel_index)
        current = [
            int(bool(_safe(channels.getGridBit, 0, channel_index, position)))
            for position in range(64)
        ]
        previous = self.step_state.get(key)
        self.step_state[key] = current
        if previous is not None and previous != current:
            changed = [index for index in range(64) if previous[index] != current[index]]
            self.emit(
                _event(
                    "pattern",
                    "steps_changed",
                    "Step sequence changed",
                    "state_diff",
                    "step_grid",
                    key,
                    _with_trigger(
                        {"changed_steps": changed, "before": previous, "after": current},
                        "OnRefresh",
                        0,
                        False,
                    ),
                )
            )

    def read_channel_metadata(self, index):
        count = max(0, min(_safe_int(channels.channelCount, 0, 1), MAX_CHANNELS))
        if index < 0 or index >= count:
            return {}
        return {
            "name": _safe(channels.getChannelName, "", index, True),
            "color": _safe(channels.getChannelColor, 0, index, True),
        }

    def read_pattern_metadata(self, index):
        maximum = max(0, min(_safe_int(patterns.patternMax, 0), MAX_PATTERNS))
        if index < 1 or index > maximum:
            return {}
        return {
            "name": _safe(patterns.getPatternName, "", index),
            "color": _safe(patterns.getPatternColor, 0, index),
        }

    def read_mixer_metadata(self, index):
        count = max(
            0, min(_safe_int(getattr(mixer, "getTrackCount", lambda: 0), 0) + 1, MAX_MIXER_TRACKS)
        )
        if index < 0 or index >= count:
            return {}
        return {
            "name": _safe(mixer.getTrackName, "", index),
            "color": _safe(mixer.getTrackColor, 0, index),
        }

    def schedule_metadata(self, kind, index, report_unknown, callback, flags):
        key = (kind, index)
        for queued in self.metadata_queue:
            if queued[0:2] == key:
                if report_unknown:
                    queued[2] = True
                    queued[3] = callback
                    queued[4] = flags
                return
        self.metadata_queue.append([kind, index, report_unknown, callback, flags])

    def schedule_full_metadata_scan(self, report_unknown, callback, flags):
        channel_count = max(0, min(_safe_int(channels.channelCount, 0, 1), MAX_CHANNELS))
        pattern_max = max(0, min(_safe_int(patterns.patternMax, 0), MAX_PATTERNS))
        mixer_count = max(
            0, min(_safe_int(getattr(mixer, "getTrackCount", lambda: 0), 0) + 1, MAX_MIXER_TRACKS)
        )
        for index in range(channel_count):
            self.schedule_metadata("channel", index, report_unknown, callback, flags)
        for index in range(1, pattern_max + 1):
            self.schedule_metadata("pattern", index, report_unknown, callback, flags)
        for index in range(mixer_count):
            self.schedule_metadata("mixer", index, report_unknown, callback, flags)

    def poll_metadata(self):
        if not self.metadata_queue:
            return False
        kind, index, report_unknown, callback, flags = self.metadata_queue.pop(0)
        states = {
            "channel": self.channel_metadata,
            "pattern": self.pattern_metadata,
            "mixer": self.mixer_metadata,
        }
        readers = {
            "channel": self.read_channel_metadata,
            "pattern": self.read_pattern_metadata,
            "mixer": self.read_mixer_metadata,
        }
        state = states[kind]
        current = readers[kind](index)
        previous = state.get(index)
        if current:
            state[index] = current
        else:
            state.pop(index, None)
        if previous is None:
            if report_unknown and current:
                self.emit(
                    _event(
                        kind,
                        "metadata_changed",
                        kind.title() + " metadata changed",
                        "signal_only",
                        kind,
                        str(index),
                        _with_trigger(
                            {"before": None, "after": current, "reason": "baseline_unavailable"},
                            callback,
                            flags,
                            False,
                        ),
                    )
                )
            return True
        for field, action in (("name", "renamed"), ("color", "recolored")):
            if previous.get(field) != current.get(field):
                summary_value = current.get(field)
                summary = kind.title() + " " + str(index) + " " + action
                if field == "name" and summary_value:
                    summary += " to " + str(summary_value)
                self.emit(
                    _event(
                        kind,
                        action,
                        summary,
                        "state_diff",
                        kind,
                        str(index),
                        _with_trigger(
                            {"before": previous.get(field), "after": current.get(field)},
                            callback,
                            flags,
                            False,
                        ),
                    )
                )
        return True

    def process_channel_event(self):
        if not self.pending_channel_events:
            self.channel_flush = False
            return False
        index, flag, queued_at = self.pending_channel_events.pop(0)
        if not self.pending_channel_events:
            self.channel_flush = False
        if flag == 4:
            self.poll_channel_selection(
                "OnDirtyChannel", _flag("HW_ChannelEvent", 65536), False, True
            )
            return True
        actions = {0: "created", 1: "deleted", 2: "replaced", 3: "renamed"}
        action = actions.get(flag, "changed")
        previous = self.channel_metadata.get(index)
        current = self.read_channel_metadata(index)
        if current:
            self.channel_metadata[index] = current
        else:
            self.channel_metadata.pop(index, None)
        self.emit(
            _event(
                "channel",
                action,
                "Channel " + str(current.get("name") or index) + " " + action,
                "exact",
                "channel",
                str(index),
                _with_trigger(
                    {"flag": flag, "before": previous, "after": current},
                    "OnDirtyChannel",
                    _flag("HW_ChannelEvent", 65536),
                    False,
                ),
                observed_monotonic_seconds=queued_at,
            )
        )
        self.dirty_steps = True
        return True

    def read_mixer_track(self, index):
        return {
            "name": _safe(mixer.getTrackName, "", index),
            "color": _safe(mixer.getTrackColor, 0, index),
            "volume": _safe(mixer.getTrackVolume, 0.0, index),
            "pan": _safe(mixer.getTrackPan, 0.0, index),
            "stereo_separation": _safe(mixer.getTrackStereoSep, 0.0, index),
            "selected": bool(_safe(mixer.isTrackSelected, 0, index)),
        }

    def process_mixer_track(self):
        if not self.pending_mixer_tracks:
            self.mixer_flush = False
            return False
        index, queued_at = self.pending_mixer_tracks.pop(0)
        if not self.pending_mixer_tracks:
            self.mixer_flush = False
        if index < 0:
            index = _safe_int(mixer.trackNumber, 0)
        current = self.read_mixer_track(index)
        previous = self.mixer_state.get(index)
        self.mixer_state[index] = current
        self.mixer_metadata[index] = {"name": current["name"], "color": current["color"]}
        if previous is None:
            self.emit(
                _event(
                    "mixer",
                    "track_changed",
                    "Mixer track " + str(index) + " changed",
                    "signal_only",
                    "mixer_track",
                    str(index),
                    _with_trigger(
                        {"before": None, "after": current, "reason": "baseline_unavailable"},
                        "OnDirtyMixerTrack",
                        _flag("HW_Dirty_Mixer_Controls", 4),
                        False,
                    ),
                    observed_monotonic_seconds=queued_at,
                )
            )
            return True
        changes = {
            key: {"before": previous.get(key), "after": current.get(key)}
            for key in current
            if previous.get(key) != current.get(key)
        }
        for field, action in (("name", "renamed"), ("color", "recolored")):
            if field in changes:
                self.emit(
                    _event(
                        "mixer",
                        action,
                        "Mixer track " + str(index) + " " + action,
                        "state_diff",
                        "mixer_track",
                        str(index),
                        _with_trigger(
                            changes[field],
                            "OnDirtyMixerTrack",
                            _flag("HW_Dirty_Mixer_Controls", 4),
                            False,
                        ),
                        observed_monotonic_seconds=queued_at,
                    )
                )
        controls = {
            key: value for key, value in changes.items() if key not in ("name", "color", "selected")
        }
        if controls:
            self.emit(
                _event(
                    "mixer",
                    "track_changed",
                    "Mixer track " + str(index) + " changed",
                    "state_diff",
                    "mixer_track",
                    str(index),
                    _with_trigger(
                        {"changes": controls, "state": current},
                        "OnDirtyMixerTrack",
                        _flag("HW_Dirty_Mixer_Controls", 4),
                        False,
                    ),
                    observed_monotonic_seconds=queued_at,
                )
            )
        return True

    def resolve_plugin_target(self):
        active_effect = _safe(getattr(mixer, "getActiveEffectIndex", lambda: None), None)
        plugin_name = _safe(ui.getFocusedPluginName, "")
        if active_effect is not None and len(active_effect) == 2:
            track, slot = int(active_effect[0]), int(active_effect[1])
            if bool(_safe(plugins.isValid, 0, track, slot)):
                name = plugin_name or _safe(plugins.getPluginName, "", track, slot)
                return ("effect", track, slot, name)
        if plugin_name:
            form_id = _safe_int(ui.getFocusedFormID, -1)
            candidates = [form_id, _safe_int(channels.selectedChannel, -1, 1, 0, 1)]
            for index in candidates:
                if index >= 0 and bool(_safe(plugins.isValid, 0, index, -1, True)):
                    return ("generator", index, -1, plugin_name)
        return None

    def request_plugin_scan(self, report_changes, callback, flags=0, fallback=False):
        self.plugin_generation += 1
        self.plugin_request_report = self.plugin_request_report or report_changes
        self.plugin_request_trigger = (callback, flags, fallback)
        self.plugin_dirty_at = time.monotonic()

    def start_plugin_scan(self):
        target = self.resolve_plugin_target()
        report = self.plugin_request_report
        callback, flags, fallback = self.plugin_request_trigger or ("OnIdle", 0, True)
        self.plugin_request_report = False
        self.plugin_request_trigger = None
        if target is None:
            if report:
                self.emit(
                    _event(
                        "plugin",
                        "controls_changed",
                        "Plugin controls changed",
                        "signal_only",
                        "plugin",
                        payload=_with_trigger(
                            {"reason": "plugin_target_unavailable"}, callback, flags, fallback
                        ),
                    )
                )
            return
        if target[0] == "effect":
            count = _safe(plugins.getParamCount, 0, target[1], target[2])
        else:
            count = _safe(plugins.getParamCount, 0, target[1], -1, True)
        count = max(0, min(int(count), MAX_PLUGIN_PARAMS))
        if count == 0:
            if report:
                self.emit(
                    _event(
                        "plugin",
                        "controls_changed",
                        str(target[3] or "Plugin") + " controls changed",
                        "signal_only",
                        "plugin",
                        payload=_with_trigger(
                            {"reason": "no_exposed_parameters"}, callback, flags, fallback
                        ),
                    )
                )
            return
        self.plugin_scan = {
            "target": target,
            "count": count,
            "cursor": 0,
            "values": [],
            "generation": self.plugin_generation,
            "report": report,
            "trigger": (callback, flags, fallback),
            "observed_at": self.plugin_dirty_at or time.monotonic(),
        }

    def poll_plugin(self):
        if self.plugin_scan is None:
            if (
                self.plugin_request_trigger is not None
                and time.monotonic() - self.plugin_dirty_at >= 0.1
            ):
                self.start_plugin_scan()
                return True
            return False
        scan = self.plugin_scan
        target = scan["target"]
        stop = min(scan["cursor"] + PLUGIN_PARAMS_PER_IDLE, scan["count"])
        for parameter in range(scan["cursor"], stop):
            if target[0] == "effect":
                value = _safe(plugins.getParamValue, 0.0, parameter, target[1], target[2])
                name = _safe(plugins.getParamName, "", parameter, target[1], target[2])
            else:
                value = _safe(plugins.getParamValue, 0.0, parameter, target[1], -1, True)
                name = _safe(plugins.getParamName, "", parameter, target[1], -1, True)
            scan["values"].append([name, value])
        scan["cursor"] = stop
        if stop < scan["count"]:
            return True
        identity = str(target[0]) + ":" + str(target[1]) + ":" + str(target[2])
        previous = self.plugin_state.get(identity)
        current = scan["values"]
        self.plugin_scan = None
        if self.plugin_generation > scan["generation"]:
            self.plugin_request_report = True
            if self.plugin_request_trigger is None:
                self.plugin_request_trigger = (
                    "OnRefresh",
                    _flag("HW_Dirty_ControlValues", 4096),
                    False,
                )
            self.plugin_dirty_at = 0.0
            return True
        self.plugin_state[identity] = current
        callback, flags, fallback = scan["trigger"]
        if scan["report"] and previous is None:
            self.emit(
                _event(
                    "plugin",
                    "controls_changed",
                    str(target[3] or "Plugin") + " controls changed",
                    "signal_only",
                    "plugin",
                    identity,
                    _with_trigger(
                        {"reason": "baseline_unavailable", "parameter_count": scan["count"]},
                        callback,
                        flags,
                        fallback,
                    ),
                    observed_monotonic_seconds=scan["observed_at"],
                )
            )
        elif previous is not None and previous != current:
            changes = []
            for index in range(min(len(previous), len(current))):
                if previous[index][1] != current[index][1]:
                    changes.append(
                        {
                            "index": index,
                            "name": current[index][0],
                            "before": previous[index][1],
                            "after": current[index][1],
                        }
                    )
            self.emit(
                _event(
                    "plugin",
                    "parameters_changed",
                    str(target[3] or "Plugin") + " parameters changed",
                    "state_diff",
                    "plugin",
                    identity,
                    _with_trigger(
                        {"changes": changes, "parameter_count": scan["count"]},
                        callback,
                        flags,
                        fallback,
                    ),
                    observed_monotonic_seconds=scan["observed_at"],
                )
            )
        return True

    def read_undo_state(self):
        return {
            "position": _safe_int(getattr(general, "getUndoHistoryPos", lambda: -1), -1),
            "count": _safe_int(getattr(general, "getUndoHistoryCount", lambda: -1), -1),
            "last": _safe_int(getattr(general, "getUndoHistoryLast", lambda: -1), -1),
            "hint": str(_safe(getattr(general, "getUndoLevelHint", lambda: ""), "") or ""),
        }

    def queue_undo(self, flags):
        if self.pending_undo is None:
            self.pending_undo = {
                "sample": None,
                "stable": 0,
                "attempts": 0,
                "flags": flags,
                "observed_at": time.monotonic(),
            }
        else:
            self.pending_undo["flags"] |= flags
            self.pending_undo["stable"] = 0

    def poll_undo(self):
        pending = self.pending_undo
        if pending is None:
            return False
        current = self.read_undo_state()
        pending["attempts"] += 1
        if pending["sample"] == current:
            pending["stable"] += 1
        else:
            pending["sample"] = current
            pending["stable"] = 0
        if pending["stable"] < 1 and pending["attempts"] < 4:
            return False
        self.pending_undo = None
        self.capture_undo_change(current, pending["flags"], pending["observed_at"])
        return True

    def capture_undo_change(self, current, flags, observed_at):
        previous = self.undo_state
        self.undo_state = current
        if previous is None:
            return
        if current == previous:
            self.emit(
                _event(
                    "project_edit",
                    "history_changed",
                    "Undo history changed",
                    "signal_only",
                    "undo_history",
                    str(current["position"]),
                    _with_trigger(
                        {
                            "before": previous,
                            "after": current,
                            "reason": "history_getters_unchanged",
                        },
                        "OnRefresh",
                        flags,
                        False,
                    ),
                    observed_monotonic_seconds=observed_at,
                )
            )
            return
        if current["position"] < previous["position"]:
            action = "undo"
            hint = previous["hint"] or self.undo_hints.get(previous["position"], "")
        elif (
            current["position"] > previous["position"]
            and current["count"] == previous["count"]
            and current["last"] == previous["last"]
        ):
            action = "redo"
            hint = self.undo_hints.get(current["position"], "") or current["hint"]
        else:
            action = "edit"
            hint = current["hint"]
        if current["position"] >= 0 and current["hint"]:
            self.undo_hints[current["position"]] = current["hint"]
        prefix = "Undo: " if action == "undo" else "Redo: " if action == "redo" else ""
        summary = prefix + hint if hint else (prefix.rstrip(": ") or "Project edited")
        self.emit(
            _event(
                "project_edit",
                action,
                summary,
                "state_diff",
                "undo_history",
                str(current["position"]),
                _with_trigger(
                    {
                        "hint": hint,
                        "steps": abs(current["position"] - previous["position"]),
                        "before": previous,
                        "after": current,
                    },
                    "OnRefresh",
                    flags,
                    False,
                ),
                observed_monotonic_seconds=observed_at,
            )
        )

    def idle(self):
        now = time.monotonic()
        self.heartbeat(now)
        if (
            self.pending_channel_events
            and not self.channel_flush
            and now - self.pending_channel_events[0][2] >= CALLBACK_SETTLE_SECONDS
        ):
            self.channel_flush = True
        if (
            self.pending_mixer_tracks
            and not self.mixer_flush
            and now - self.pending_mixer_tracks[0][1] >= CALLBACK_SETTLE_SECONDS
        ):
            self.mixer_flush = True
        self.poll_transport()
        if self.channel_flush:
            self.process_channel_event()
        else:
            self.poll_channel_selection()
        self.poll_pattern_selection()
        self.poll_mixer_selection()
        self.poll_focus()
        if self.poll_undo():
            return
        if self.mixer_flush and self.process_mixer_track():
            return
        if self.dirty_steps:
            self.poll_steps()
            return
        if (
            self.plugin_scan is not None or self.plugin_request_trigger is not None
        ) and self.poll_plugin():
            return
        if self.poll_metadata():
            return
        self.poll_plugin()


RECORDER = ActivityRecorder()


def OnInit():
    RECORDER.initialize()


def OnDeInit():
    RECORDER.send(
        KIND_DIAGNOSTIC,
        {"level": "info", "code": "observer_stopped", "message": "Activity observer stopped"},
    )


def OnProjectLoad(status):
    names = {0: "started", 100: "loaded", 101: "failed"}
    state = names.get(status, "changed")
    RECORDER.emit(
        _event(
            "project",
            "project_load_" + state,
            "Project load " + state,
            "exact",
            "project",
            payload=_with_trigger({"status": status}, "OnProjectLoad", 0, False),
        )
    )
    if status == 100:
        RECORDER.reset_state()
        RECORDER.send(KIND_SNAPSHOT, RECORDER.snapshot())


def OnDoFullRefresh():
    RECORDER.reset_state()
    RECORDER.send(KIND_SNAPSHOT, RECORDER.snapshot())


def OnDirtyChannel(index, flag):
    RECORDER.pending_channel_events.append([index, flag, time.monotonic()])


def OnDirtyMixerTrack(index):
    if not any(item[0] == index for item in RECORDER.pending_mixer_tracks):
        RECORDER.pending_mixer_tracks.append([index, time.monotonic()])


def OnRefresh(flags):
    if flags & _flag("HW_Dirty_LEDs", 256):
        RECORDER.transport_trigger = ("OnRefresh", flags, False)
    if flags & _flag("HW_Dirty_FocusedWindow", 32):
        RECORDER.focus_trigger = ("OnRefresh", flags, False)
    if flags & _flag("HW_Dirty_Mixer_Sel", 1):
        RECORDER.mixer_selection_trigger = ("OnRefresh", flags, False)
    if flags & _flag("HW_Dirty_Mixer_Controls", 4):
        RECORDER.mixer_flush = True
    if flags & _flag("HW_ChannelEvent", 65536):
        RECORDER.channel_flush = True
    if flags & _flag("HW_Dirty_Patterns", 1024):
        RECORDER.pattern_trigger = ("OnRefresh", flags, False)
        RECORDER.dirty_pattern_state = True
        RECORDER.dirty_steps = True
        RECORDER.schedule_full_metadata_scan(True, "OnRefresh", flags)
    if flags & _flag("HW_Dirty_Tracks", 2048):
        RECORDER.emit(
            _event(
                "playlist",
                "playlist_changed",
                "Playlist content or tracks changed",
                "signal_only",
                "playlist",
                payload=_with_trigger({}, "OnRefresh", flags, False),
            )
        )
    if flags & _flag("HW_Dirty_ControlValues", 4096):
        RECORDER.request_plugin_scan(True, "OnRefresh", flags, False)
    if flags & _flag("HW_Dirty_Undo", 131072):
        RECORDER.queue_undo(flags)
    if flags & (_flag("HW_Dirty_Names", 16384) | _flag("HW_Dirty_Colors", 8192)):
        RECORDER.schedule_full_metadata_scan(True, "OnRefresh", flags)
    refresh_mask, refresh_names = _unhandled_refresh_details(flags)
    if refresh_mask:
        RECORDER.emit(
            _event(
                "system",
                "host_refresh",
                "FL Studio state refreshed: " + ", ".join(refresh_names),
                "signal_only",
                "host_state",
                payload=_with_trigger(
                    {"captured_flags": refresh_mask, "names": refresh_names},
                    "OnRefresh",
                    flags,
                    False,
                ),
            )
        )


def OnUpdateBeatIndicator(value):
    RECORDER.transport_trigger = ("OnUpdateBeatIndicator", 0, False)


def OnSendTempMsg(message, duration):
    RECORDER.emit(
        _event(
            "navigation",
            "hint",
            str(message),
            "signal_only",
            "hint",
            payload=_with_trigger({"duration_ms": duration}, "OnSendTempMsg", 0, False),
        )
    )


def OnIdle():
    RECORDER.idle()
