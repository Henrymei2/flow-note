# Real FL Studio release validation

Automated tests do not replace this checklist. Run it on both macOS with the IAC Driver
and Windows with loopMIDI before publishing a cross-platform release.

## Setup and safety

- [ ] Observer appears as **Activity Recorder (user)** and reports API 42+
- [ ] Platform setup check finds both virtual MIDI ports
- [ ] Companion connects within five seconds
- [ ] Script Output, dashboard health, and export all report `event-capture-v2`
- [ ] Physical MIDI controllers and keyboard input still behave normally
- [ ] Observer never consumes or modifies incoming user MIDI
- [ ] Start/stop consent rules are respected
- [ ] No project file path appears in the database or export

## Capture scenarios

- [ ] Load, fail to load, and replace a project
- [ ] Start/stop playback and observe `transport/playing_changed`; toggle recording and
      song/pattern mode; change tempo
- [ ] Create, delete, replace, rename, recolor, and select channels; confirm selection and rename
      are exact while recolor is state-derived
- [ ] Toggle steps in the selected channel and pattern
- [ ] Change mixer volume, pan, stereo separation, selection, color, and track name; confirm
      `mixer/selection_changed` is exact
- [ ] Select, rename, and recolor patterns, including a non-current pattern
- [ ] Focus Channel Rack, Playlist, Piano Roll, Mixer, Browser, and plugins
- [ ] Change exposed parameters in a native and a third-party plugin
- [ ] Edit Playlist clips and Piano Roll notes and confirm FL Studio's undo label is recorded
- [ ] Perform an edit, undo, and redo and confirm three specific state-derived events and FL
      Studio's action label are recorded
- [ ] Confirm a named undo entry and its supported state diff are both retained
- [ ] Trigger color, mixer-display, Channel Rack, and Graph Editor refreshes and inspect raw flags
- [ ] Stop recording and confirm later activity is not persisted

## Reliability and performance

- [ ] Events appear in the live dashboard within 500 ms
- [ ] Take the IAC Driver offline or stop loopMIDI and confirm the session becomes interrupted after five seconds
- [ ] Reconnect and start a new session successfully
- [ ] Run a representative 30-minute production session without audible glitches
- [ ] Confirm no material FL Studio CPU increase
- [ ] Export JSONL, restart the companion, and verify completed sessions remain readable
- [ ] Run `fl-activity-validate` on the export and resolve every missing supported event class
- [ ] Corrupt or drop an emulated frame and confirm a diagnostic appears
