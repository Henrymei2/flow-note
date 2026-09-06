# Privacy

Recording is explicit and local. The observer may run whenever FL Studio is open, but the companion persists activity only between Start recording and Stop recording.

Stored content includes project titles, channel and mixer names, colors and selections, plugin
names, parameter labels and values, focus captions, Browser activity, FL hint text, undo labels,
and timestamps. This information can reveal creative intent or client names. With explicit
Chrome permission, sessions may also store FL Studio window pixels and the audio track Chrome
provides. Project file paths, keystrokes, raw mouse activity, and physical-controller MIDI are
not collected.

The dashboard binds exclusively to `127.0.0.1`. The application contains no cloud endpoint, analytics SDK, updater, or telemetry. Data remains in the local application-data directory until the user permanently deletes a session:

- macOS: `~/Library/Application Support/FLActivityRecorder/activity.db`
- Windows: `%LOCALAPPDATA%\FLActivityRecorder\activity.db`

Per-session video, imported EmotiBit source files, and normalized signal arrays are stored in
the adjacent `sessions/<session-id>/` directory. Deleting a session permanently deletes both its
database rows and owned asset directory.

JSONL exports are created only on request and inherit the sensitivity of the timeline.
