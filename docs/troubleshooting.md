# Troubleshooting

## Dashboard says FL Studio is disconnected

1. On macOS, confirm the IAC Driver is online. On Windows, keep loopMIDI running.
2. Confirm both expected ports exist.
3. Confirm the FL input and output use the same FL port number.
4. Confirm **Activity Recorder (user)** is selected for the Control input.
5. Check **View → Script output** for observer errors.
6. Restart the companion after changing virtual MIDI ports.

## Dashboard says the observer must be updated

Run the platform installer again and verify its source/destination checksum succeeds. In FL
Studio, reload Activity Recorder from **View → Script output** or restart FL Studio. Script Output
and dashboard health must both show `schema=3 build=project-title-fallback-v1`.

The installer must also print `companion=0.2.1`. Stop any dashboard process that was running
before the install and launch it again.

Run the setup check with the installed companion's Python:

```sh
# macOS
"$HOME/Library/Application Support/FLActivityRecorder/.venv/bin/python" \
  -m fl_activity_recorder.setup_check
```

## Unsupported API

Update to FL Studio 26.1.3 or newer. The recorder requires MIDI scripting API 42 for its expected refresh signals and refuses to start a recording on older APIs.

## Timeline shows a generic change

FL Studio's undo label can name an operation without exposing its changed objects. Detailed changes such as 
Piano Roll notes cannot be reconstructed live, so
their refresh events remain generic.

## Missing events diagnostic

A sequence gap means one or more complete observer messages did not reach the companion.
Check the IAC Driver or loopMIDI routing and avoid sharing the recorder ports with other
applications.