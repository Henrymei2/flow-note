# name=Activity Recorder
"""Transparent FL Studio 26.1.3+ activity observer.

This script never handles incoming user MIDI. It reads supported host state and
emits 7-bit-safe SysEx messages to the output interface linked by FL port number.
"""

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
MANUFACTURER_ID = 0x7D
MAX_FRAME_SIZE = 1024
HEADER_SIZE = 18
FRAME_OVERHEAD = HEADER_SIZE + 1
MAX_PLUGIN_PARAMS = 512
PLUGIN_PARAMS_PER_IDLE = 64
HEARTBEAT_SECONDS = 1.0
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


def _flag(name, default):
    value = getattr(midi, name, default)
    return value if isinstance(value, int) else default


def _event(category, action, summary, confidence, entity_type=None, entity_key=None, payload=None):
    return {
        "category": category,
        "action": action,
        "summary": summary,
        "confidence": confidence,
        "entity_type": entity_type,
        "entity_key": entity_key,
        "payload": payload or {},
    }


class ActivityRecorder:
    def __init__(self):
        self.sequence = 0
        self.last_heartbeat = 0.0
        self.dirty_mixer_tracks = []
        self.dirty_focus = True
        self.dirty_pattern = True
        self.dirty_steps = True
        self.dirty_plugin = True
        self.transport_state = None
        self.focus_state = None
        self.pattern_state = None
        self.step_state = {}
        self.mixer_state = {}
        self.plugin_state = {}
        self.plugin_scan = None
        self.plugin_dirty_at = 0.0

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
            frame = bytes(header) + chunk + bytes([0xF7])
            try:
                device.midiOutSysex(frame)
            except Exception as error:
                print("Activity Recorder SysEx error: " + str(error))
                return

    def emit(self, event):
        self.send(KIND_EVENT, event)

    def initialize(self):
        api_version = _safe(general.getVersion, 0)
        hello = {
            "observer_version": "0.1.0",
            "api_version": api_version,
            "fl_version": _safe(getattr(ui, "getVersion", lambda: ""), ""),
            "project_title": _safe(getattr(general, "getProjectTitle", lambda: ""), ""),
        }
        self.send(KIND_HELLO, hello)
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

    def snapshot(self):
        channel_index = _safe(channels.selectedChannel, -1, 1)
        pattern_index = _safe(patterns.patternNumber, -1)
        return {
            "project_title": _safe(getattr(general, "getProjectTitle", lambda: ""), ""),
            "project_author": _safe(getattr(general, "getProjectAuthor", lambda: ""), ""),
            "changed_flag": _safe(general.getChangedFlag, 0),
            "playing": bool(_safe(transport.isPlaying, 0)),
            "recording": bool(_safe(transport.isRecording, 0)),
            "loop_mode": _safe(transport.getLoopMode, 0),
            "tempo": _safe(mixer.getCurrentTempo, 0.0),
            "selected_channel": channel_index,
            "selected_channel_name": _safe(channels.getChannelName, "", channel_index)
            if channel_index >= 0
            else "",
            "selected_pattern": pattern_index,
            "selected_pattern_name": _safe(patterns.getPatternName, "", pattern_index)
            if pattern_index >= 0
            else "",
            "focused_caption": _safe(ui.getFocusedFormCaption, ""),
            "focused_form_id": _safe(ui.getFocusedFormID, -1),
            "focused_plugin": _safe(ui.getFocusedPluginName, ""),
        }

    def heartbeat(self, now):
        if now - self.last_heartbeat < HEARTBEAT_SECONDS:
            return
        self.last_heartbeat = now
        self.send(
            KIND_HEARTBEAT,
            {
                "project_title": _safe(getattr(general, "getProjectTitle", lambda: ""), ""),
                "api_version": _safe(general.getVersion, 0),
                "playing": bool(_safe(transport.isPlaying, 0)),
                "recording": bool(_safe(transport.isRecording, 0)),
            },
        )

    def poll_transport(self):
        current = {
            "playing": bool(_safe(transport.isPlaying, 0)),
            "recording": bool(_safe(transport.isRecording, 0)),
            "loop_mode": _safe(transport.getLoopMode, 0),
            "tempo": _safe(mixer.getCurrentTempo, 0.0),
        }
        previous = self.transport_state
        self.transport_state = current
        if previous is None:
            return
        for key in ("playing", "recording", "loop_mode", "tempo"):
            if previous.get(key) != current.get(key):
                exact = key != "tempo"
                self.emit(
                    _event(
                        "transport",
                        key + "_changed",
                        self._transport_summary(key, current[key]),
                        "exact" if exact else "state_diff",
                        "transport",
                        key,
                        {"before": previous.get(key), "after": current.get(key)},
                    )
                )

    def _transport_summary(self, key, value):
        if key == "playing":
            return "Playback started" if value else "Playback stopped"
        if key == "recording":
            return "FL Studio recording enabled" if value else "FL Studio recording disabled"
        if key == "loop_mode":
            return "Playback mode changed to " + ("song" if value else "pattern")
        return "Tempo changed to " + str(round(float(value), 2)) + " BPM"

    def poll_focus(self):
        current = {
            "form_id": _safe(ui.getFocusedFormID, -1),
            "caption": _safe(ui.getFocusedFormCaption, ""),
            "plugin": _safe(ui.getFocusedPluginName, ""),
            "browser_node": _safe(getattr(ui, "getFocusedNodeCaption", lambda: ""), ""),
        }
        previous = self.focus_state
        self.focus_state = current
        self.dirty_focus = False
        if previous is not None and previous != current:
            label = (
                current["plugin"] or current["caption"] or current["browser_node"] or "FL Studio"
            )
            self.emit(
                _event(
                    "navigation",
                    "focus_changed",
                    "Focused " + str(label),
                    "exact",
                    "window",
                    str(current["form_id"]),
                    {"before": previous, "after": current},
                )
            )

    def poll_pattern(self):
        index = _safe(patterns.patternNumber, -1)
        current = {
            "index": index,
            "name": _safe(patterns.getPatternName, "", index) if index >= 0 else "",
            "count": _safe(patterns.patternCount, 0),
        }
        previous = self.pattern_state
        self.pattern_state = current
        self.dirty_pattern = False
        if previous is not None and previous != current:
            self.emit(
                _event(
                    "pattern",
                    "pattern_changed",
                    "Pattern state changed",
                    "state_diff",
                    "pattern",
                    str(index),
                    {"before": previous, "after": current},
                )
            )

    def poll_steps(self):
        channel_index = _safe(channels.selectedChannel, -1, 1)
        pattern_index = _safe(patterns.patternNumber, -1)
        if channel_index < 0 or pattern_index < 0:
            self.dirty_steps = False
            return
        key = str(pattern_index) + ":" + str(channel_index)
        current = []
        for position in range(64):
            current.append(int(bool(_safe(channels.getGridBit, 0, channel_index, position))))
        previous = self.step_state.get(key)
        self.step_state[key] = current
        self.dirty_steps = False
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
                    {"changed_steps": changed, "before": previous, "after": current},
                )
            )

    def poll_mixer_track(self):
        if not self.dirty_mixer_tracks:
            return
        index = self.dirty_mixer_tracks.pop(0)
        if index < 0:
            index = _safe(mixer.trackNumber, 0)
        current = {
            "name": _safe(mixer.getTrackName, "", index),
            "volume": _safe(mixer.getTrackVolume, 0.0, index),
            "pan": _safe(mixer.getTrackPan, 0.0, index),
            "stereo_separation": _safe(mixer.getTrackStereoSep, 0.0, index),
            "selected": bool(_safe(mixer.isTrackSelected, 0, index)),
        }
        previous = self.mixer_state.get(index)
        self.mixer_state[index] = current
        if previous is not None and previous != current:
            changed = {}
            for key in current:
                if previous.get(key) != current.get(key):
                    changed[key] = {"before": previous.get(key), "after": current.get(key)}
            self.emit(
                _event(
                    "mixer",
                    "track_changed",
                    "Mixer track " + str(index) + " changed",
                    "state_diff",
                    "mixer_track",
                    str(index),
                    {"changes": changed, "state": current},
                )
            )
        elif previous is None:
            self.emit(
                _event(
                    "mixer",
                    "track_changed",
                    "Mixer track " + str(index) + " changed",
                    "signal_only",
                    "mixer_track",
                    str(index),
                    {"state": current},
                )
            )

    def start_plugin_scan(self):
        active_effect = _safe(getattr(mixer, "getActiveEffectIndex", lambda: None), None)
        plugin_name = _safe(ui.getFocusedPluginName, "")
        target = None
        if active_effect and len(active_effect) == 2:
            target = ("effect", int(active_effect[0]), int(active_effect[1]), plugin_name)
        elif plugin_name:
            channel_index = _safe(channels.selectedChannel, -1, 1)
            if channel_index >= 0:
                target = ("generator", channel_index, -1, plugin_name)
        if target is None:
            self.emit(
                _event(
                    "plugin", "controls_changed", "Plugin controls changed", "signal_only", "plugin"
                )
            )
            self.dirty_plugin = False
            return
        if target[0] == "effect":
            count = _safe(plugins.getParamCount, 0, target[1], target[2])
        else:
            count = _safe(plugins.getParamCount, 0, target[1])
        count = max(0, min(int(count), MAX_PLUGIN_PARAMS))
        self.plugin_scan = {"target": target, "count": count, "cursor": 0, "values": []}
        self.dirty_plugin = False

    def poll_plugin(self):
        if self.plugin_scan is None:
            if self.dirty_plugin:
                if time.monotonic() - self.plugin_dirty_at < 0.1:
                    return
                self.start_plugin_scan()
            return
        scan = self.plugin_scan
        target = scan["target"]
        stop = min(scan["cursor"] + PLUGIN_PARAMS_PER_IDLE, scan["count"])
        for parameter in range(scan["cursor"], stop):
            if target[0] == "effect":
                value = _safe(plugins.getParamValue, 0.0, parameter, target[1], target[2])
                name = _safe(plugins.getParamName, "", parameter, target[1], target[2])
            else:
                value = _safe(plugins.getParamValue, 0.0, parameter, target[1])
                name = _safe(plugins.getParamName, "", parameter, target[1])
            scan["values"].append([name, value])
        scan["cursor"] = stop
        if stop < scan["count"]:
            return
        identity = str(target[0]) + ":" + str(target[1]) + ":" + str(target[2])
        previous = self.plugin_state.get(identity)
        current = scan["values"]
        self.plugin_state[identity] = current
        self.plugin_scan = None
        if previous is not None and previous != current:
            changes = []
            common = min(len(previous), len(current))
            for index in range(common):
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
                    {"changes": changes, "parameter_count": scan["count"]},
                )
            )

    def idle(self):
        now = time.monotonic()
        self.heartbeat(now)
        self.poll_transport()
        if self.dirty_focus:
            self.poll_focus()
        elif self.dirty_mixer_tracks:
            self.poll_mixer_track()
        elif self.dirty_pattern:
            self.poll_pattern()
        elif self.dirty_steps:
            self.poll_steps()
        else:
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
            payload={"status": status},
        )
    )
    if status == 100:
        RECORDER.send(KIND_SNAPSHOT, RECORDER.snapshot())
        RECORDER.dirty_pattern = True
        RECORDER.dirty_steps = True


def OnDirtyChannel(index, flag):
    actions = {0: "created", 1: "deleted", 2: "replaced", 3: "renamed", 4: "selected"}
    action = actions.get(flag, "changed")
    name = _safe(channels.getChannelName, "", index, True) if index >= 0 else ""
    RECORDER.emit(
        _event(
            "channel",
            action,
            "Channel " + (name or str(index)) + " " + action,
            "exact",
            "channel",
            str(index),
            {"flag": flag, "name": name},
        )
    )
    RECORDER.dirty_steps = True


def OnDirtyMixerTrack(index):
    if index not in RECORDER.dirty_mixer_tracks:
        RECORDER.dirty_mixer_tracks.append(index)


def OnRefresh(flags):
    if flags & _flag("HW_Dirty_FocusedWindow", 32):
        RECORDER.dirty_focus = True
    if flags & _flag("HW_Dirty_Patterns", 1024):
        RECORDER.dirty_pattern = True
        RECORDER.dirty_steps = True
    if flags & _flag("HW_Dirty_Tracks", 2048):
        RECORDER.emit(
            _event(
                "playlist",
                "playlist_changed",
                "Playlist content or tracks changed",
                "signal_only",
                "playlist",
                payload={"refresh_flags": flags},
            )
        )
    if flags & _flag("HW_Dirty_ControlValues", 4096):
        RECORDER.dirty_plugin = True
        RECORDER.plugin_dirty_at = time.monotonic()
    if flags & _flag("HW_Dirty_Undo", 131072):
        RECORDER.emit(
            _event(
                "undo",
                "history_changed",
                "Undo or redo history changed",
                "signal_only",
                "undo_history",
                payload={"refresh_flags": flags},
            )
        )


def OnUpdateBeatIndicator(value):
    # Beat events are intentionally not persisted; transport state is polled at a safe rate.
    pass


def OnSendTempMsg(message, duration):
    RECORDER.emit(
        _event(
            "navigation",
            "hint",
            str(message),
            "signal_only",
            "hint",
            payload={"duration_ms": duration},
        )
    )


def OnIdle():
    RECORDER.idle()
