# Real FL Studio release validation

Automated tests do not replace this checklist. Run it on both macOS with the IAC Driver
and Windows with loopMIDI before publishing a cross-platform release.

## Setup and safety

- [ ] Observer appears as **Activity Recorder (user)** and reports API 42+
- [ ] Platform setup check finds both virtual MIDI ports
- [ ] Companion connects within five seconds
- [ ] Physical MIDI controllers and keyboard input still behave normally
- [ ] Observer never consumes or modifies incoming user MIDI
- [ ] Start/stop consent rules are respected
- [ ] No project file path appears in the database or export

## Capture scenarios

- [ ] Load, fail to load, and replace a project
- [ ] Start/stop playback and change window focus; confirm neither produces timeline noise
- [ ] Toggle recording and change tempo
- [ ] Create, delete, and replace channels; confirm rename and selection are ignored
- [ ] Toggle steps in the selected channel and pattern
- [ ] Change mixer volume, pan, and stereo separation; confirm selection and track names are ignored
- [ ] Focus Channel Rack, Playlist, Piano Roll, Mixer, and Browser; confirm focus is ignored
- [ ] Change exposed parameters in a native and a third-party plugin
- [ ] Edit Playlist clips and Piano Roll notes and confirm FL Studio's undo label is recorded
- [ ] Perform undo and redo and confirm direction and FL Studio's action label are recorded
- [ ] Confirm a supported state diff suppresses its duplicate undo-label entry
- [ ] Stop recording and confirm later activity is not persisted

## Reliability and performance

- [ ] Events appear in the live dashboard within 500 ms
- [ ] Take the IAC Driver offline or stop loopMIDI and confirm the session becomes interrupted after five seconds
- [ ] Reconnect and start a new session successfully
- [ ] Run a representative 30-minute production session without audible glitches
- [ ] Confirm no material FL Studio CPU increase
- [ ] Export JSONL, restart the companion, and verify completed sessions remain readable
- [ ] Corrupt or drop an emulated frame and confirm a diagnostic appears
