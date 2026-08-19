# Privacy

Recording is explicit and local. The observer may run whenever FL Studio is open, but the companion persists activity only between Start recording and Stop recording.

Stored content includes project titles, channel and mixer names, plugin names, parameter labels and values, focus captions, FL hint text, and timestamps. This information can reveal creative intent or client names. Project file paths, audio, screenshots, keystrokes, mouse activity, and physical-controller MIDI are not collected.

The dashboard binds exclusively to `127.0.0.1`. The application contains no cloud endpoint, analytics SDK, updater, or telemetry. Data remains in the local application-data directory until the user permanently deletes a session:

- macOS: `~/Library/Application Support/FLActivityRecorder/activity.db`
- Windows: `%LOCALAPPDATA%\FLActivityRecorder\activity.db`

JSONL exports are created only on request and inherit the sensitivity of the timeline.
