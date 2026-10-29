# Live FL Studio, EmotiBit, and video timeline

The companion owns session lifecycle and maps FL events, Chrome video, and live physiological
samples into seconds relative to one LSL `local_clock()` value. EmotiBit data is captured only from
the live LSL output; this version does not use SD-card recordings or DataParser imports.

## Requirements and one-time setup

- Windows 10/11 or macOS 13 or later
- Latest desktop Google Chrome
- Official stock EmotiBit firmware and EmotiBit Oscilloscope 1.11.1 or later
- FL Studio, Oscilloscope, and the companion on the same computer

Open EmotiBit Oscilloscope, connect the EmotiBit, and enable **LSL** in **Output List**. Configure
numeric outputs in `lslOutputSettings.json` and make every stream name begin with `EmotiBit`.
Settings are normally under `C:\Program Files\EmotiBit\EmotiBit Oscilloscope\data\` on Windows. On
macOS, use **Show Package Contents** and open `Contents/Resources/`.

The companion accepts every numeric channel in matching streams. Known EmotiBit TypeTags receive
friendly labels; other channel labels remain available under the Other group. A custom prefix can
be supplied through `FL_ACTIVITY_EMOTIBIT_LSL_PREFIX`.

## Collection

1. Wear the battery-powered EmotiBit and connect it in Oscilloscope. Never wear EmotiBit while it
   is connected by USB or another conductor to equipment attached to AC mains.
2. Confirm the dashboard reports fresh EmotiBit LSL samples. The Oscilloscope SD-card Record button
   is not used by this workflow.
3. Press **Start recording** and choose only the FL Studio window in Chrome.
4. During recording, the dashboard displays a rolling 30-second physiological view. Chrome audio
   remains optional; a missing audio track produces a diagnostic but does not stop the session.
5. Press **Stop recording**. The companion seals the final signal chunks before returning, then the
   page loads the complete synchronized timeline immediately.

Recording cannot start unless FL Studio is ready, the real LSL clock is available, and at least one
matching stream produced a sample within two seconds. A gap longer than two seconds during a
session does not stop FL or video capture; it is recorded without interpolation and lowers the
session synchronization quality.

## Quality and validation

New recordings combine consecutive mixer volume, pan, and stereo-separation changes per track.
An adjustment is stored and displayed after its values stop changing for one second, using the
last change's source and receipt timestamps. Intermediate values are not stored. Fields that
return to their starting values are omitted; an adjustment with no net change produces no event.
Other event categories and mixer selection, naming, and color changes remain immediate.

Stopping a recording, losing the observer connection, or rebuilding project state commits any
received pending adjustment immediately. A forced process termination can lose pending changes.
This completion rule also applies to automation and approximates the end of an adjustment rather
than detecting mouse release. MIDI state messages remain immediate, and existing recordings are
unchanged.

`aligned_unverified` means all recorded sources used valid same-host LSL timestamps and no live
gap was detected. It does not claim measured 50 ms accuracy. A session with any EmotiBit gap is
`degraded`.

A release may claim the target only after Windows and macOS real-hardware runs compare visible FL
changes, video frames, and known EmotiBit changes. Report median, p95, and maximum alignment error;
the target is p95 at or below 50 ms. Also verify live browser updates remain at or below 500 ms,
long recordings keep stable memory use, and disconnect/reconnect intervals appear as diagnostics.
