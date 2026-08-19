# Real FL Studio release validation

Automated tests do not replace this checklist. Run it on Windows with FL Studio 26.1.3+ and loopMIDI before publishing a release.

## Setup and safety

- [ ] Observer appears as **Activity Recorder (user)** and reports API 42+
- [ ] Companion connects within five seconds
- [ ] Physical MIDI controllers and keyboard input still behave normally
- [ ] Observer never consumes or modifies incoming user MIDI
- [ ] Start/stop consent rules are respected
- [ ] No project file path appears in the database or export

## Capture scenarios

- [ ] Load, fail to load, and replace a project
- [ ] Start/stop playback; toggle recording and song/pattern mode; change tempo
- [ ] Create, delete, replace, rename, and select channels
- [ ] Toggle steps in the selected channel and pattern
- [ ] Change mixer volume, pan, stereo separation, selection, and track name
- [ ] Focus Channel Rack, Playlist, Piano Roll, Mixer, Browser, and plugins
- [ ] Change exposed parameters in a native and a third-party plugin
- [ ] Edit Playlist clips and Piano Roll notes and confirm they are labeled signal-only
- [ ] Perform undo and redo and confirm history activity is labeled signal-only
- [ ] Stop recording and confirm later activity is not persisted

## Reliability and performance

- [ ] Events appear in the live dashboard within 500 ms
- [ ] Disconnect loopMIDI and confirm the session becomes interrupted after five seconds
- [ ] Reconnect and start a new session successfully
- [ ] Run a representative 30-minute production session without audible glitches
- [ ] Confirm no material FL Studio CPU increase
- [ ] Export JSONL, restart the companion, and verify completed sessions remain readable
- [ ] Corrupt or drop an emulated frame and confirm a diagnostic appears

