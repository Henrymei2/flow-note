# Privacy

Recording is explicit and local. The observer may run whenever FL Studio is open, but the companion persists activity only between Start recording and Stop recording.

Stored content includes project titles, channel and mixer names, colors and selections, plugin
names, parameter labels and values, focus captions, Browser activity, FL hint text, undo labels,
and timestamps. This information can reveal creative intent or client names. With explicit
Chrome permission, sessions may also store FL Studio window pixels and the audio track Chrome
provides. User-created bookmark notes and labels can contain personal interpretations and should
be treated as sensitive data as well. Project file paths, keystrokes, raw mouse activity, and
physical-controller MIDI are not collected.

The dashboard binds exclusively to `127.0.0.1`. The application contains no cloud endpoint, analytics SDK, updater, or telemetry. Data remains in the local application-data directory until the user permanently deletes a session:

- macOS: `~/Library/Application Support/FLActivityRecorder/activity.db`
- Windows: `%LOCALAPPDATA%\FLActivityRecorder\activity.db`

Per-session video and live EmotiBit LSL signal chunks are stored in
the adjacent `sessions/<session-id>/` directory. Deleting a session permanently deletes both its
database rows and owned asset directory.

Bookmarks are personal, device-local metadata. They are deleted automatically with their source
session and are deliberately excluded from downloaded research archives, imported archives,
Cloudflare R2 uploads, and cloud-session metadata.

JSONL exports are created only on request and inherit the sensitivity of the timeline.
