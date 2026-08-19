# Troubleshooting

## Dashboard says FL Studio is disconnected

1. Keep loopMIDI running.
2. Confirm both expected ports exist.
3. Confirm the FL input and output use the same FL port number.
4. Confirm **Activity Recorder (user)** is selected for the Control input.
5. Check **View → Script output** for observer errors.
6. Restart the companion after changing loopMIDI ports.

## Unsupported API

Update to FL Studio 26.1.3 or newer. The recorder requires MIDI scripting API 42 for its expected refresh signals and refuses to start a recording on older APIs.

## Timeline shows a generic change

This is expected when FL Studio provides a dirty signal but no object-level read API. The event is labeled `signal only`. Piano Roll notes, Playlist clips, automation points, and some plugin controls cannot be reconstructed.

## Missing events diagnostic

A sequence gap means one or more complete observer messages did not reach the companion. Check loopMIDI routing and avoid sharing the recorder ports with other applications. The recorder reports the gap rather than fabricating activity.

