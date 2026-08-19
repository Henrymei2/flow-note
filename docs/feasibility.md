# Feasibility and capture boundaries

## Supported approach

FL Studio's documented MIDI scripting API provides the only supported, always-on Python callback surface with broad DAW context. A VST or native generator/effect plugin sees its own host interaction, not a complete global edit history. The controller observer therefore uses only documented getters and callbacks and performs bounded work during `OnIdle`.

The continuous-script sandbox is expected to block file and socket writes. The observer sends 7-bit-safe SysEx to a separate local process instead. This must be confirmed on a real FL Studio 26.1.3 installation before release.

## Fidelity classes

| Confidence | Meaning | Examples |
| --- | --- | --- |
| Exact | FL emitted a callback that names the operation or state transition | Project load, channel creation/deletion, recording state, named undo/redo action |
| State-derived | A refresh signal caused a supported before/after state comparison | Tempo, mixer controls, steps, focused plugin parameters |
| Signal-only | FL reported that an area changed without exposing useful detail | Not persisted; vague refresh-only activity is discarded |

The observer reads `general.getUndoLevelHint()` together with the undo history position,
count, and last position. This distinguishes new edits, undo, and redo and preserves FL
Studio's own action label. It does not expose the affected notes, clips, automation points,
or complete before/after state. When a supported state comparison explains the same edit,
the separate undo label is suppressed.

Window focus, Browser and hint-bar activity, item selection, colors, renames, playback
navigation, and generic Playlist refreshes are intentionally not recorded.

## Known unavailable data

- Mouse clicks, keyboard shortcuts, screenshots, and window pixels
- Raw performance MIDI from unrelated physical controller ports
- Arbitrary Piano Roll note objects and complete note histories
- Playlist clip placement, movement, trimming, or per-clip properties
- Automation point geometry and object-level data behind undo-history descriptions
- Audio samples, waveform data, or meter streams
- State internal to a third-party plugin that is not exposed as host parameters
- Project file paths

The product must never imply that a signal-only event describes the exact edit.

## Performance rules

- Never read meters or audio-rate state.
- Never mark incoming user MIDI as handled.
- Process one dirty entity per idle callback.
- Process at most 64 plugin parameters per idle callback and 512 per focused plugin.
- Coalesce host refreshes and retain the final before/after state.
- Surface missing SysEx sequence numbers instead of hiding event loss.
