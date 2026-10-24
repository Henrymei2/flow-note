# FL Studio Activity Recorder

A private, local-first timeline of supported semantic activity in FL Studio 26.1.3+
on macOS and Windows.

The recorder has two parts:

1. A transparent FL Studio MIDI controller script observes documented host callbacks and state.
2. A local companion receives those events over virtual MIDI, stores active sessions in SQLite, and serves a dashboard at `http://127.0.0.1:8765`.

No cloud service, telemetry, accessibility hooks, keylogging, or physical-controller MIDI
routing is included. When explicitly authorized in Chrome, a session can also contain a local
FL Studio window recording and the audio track Chrome supplies.

## What it can record

- Project load lifecycle and project metadata exposed by FL Studio
- Playback, recording, loop mode, and tempo transitions
- Channel creation, deletion, replacement, rename, and selection
- Mixer track state, including names, colors, values, and selection
- Pattern state, selection, and selected-channel step-grid changes
- Focused windows, Browser nodes, and FL hint messages
- Focused plugin parameter changes when the plugin exposes host parameters
- Generic Playlist and otherwise inaccessible plugin refresh signals
- Additional host refreshes for mixer display/selection, names, colors, remote links,
  Channel Rack groups/events, performance layout, and Graph Editor changes
- FL Studio's own action label and history state for edits, undo, and redo

The observer favors completeness and retains software-level navigation and state changes even
when they do not directly alter the sound. Named undo events and supported before/after state
diffs are both kept because they provide complementary context. High-frequency beat indicators,
meters, and audio-rate data are excluded. FL Studio still does not expose enough information to
reconstruct arbitrary Piano Roll notes, Playlist clips, automation points, or private plugin UI
state. See [feasibility.md](docs/feasibility.md).

## Requirements

- macOS supported by FL Studio, or Windows 10/11
- FL Studio 26.1.3+ with MIDI scripting API 42+
- Python 3.12 or newer (including Python 3.14)
- Latest desktop Chrome for optional 720p/30fps window recording
- EmotiBit Oscilloscope 1.11.1+ with LSL Output enabled for physiological-data sessions
- Virtual MIDI: the built-in IAC Driver on macOS, or
  [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) on Windows

## Install on macOS

First configure the built-in virtual MIDI device:

1. Open **Audio MIDI Setup** and choose **Window → Show MIDI Studio**.
2. Double-click **IAC Driver** and enable **Device is online**.
3. Add two buses named `ActivityRecorder Control` and `ActivityRecorder Events`.
4. Click **Apply**. If FL Studio was open, refresh its MIDI device list or restart it.

Then install the observer and companion from Terminal:

```sh
./scripts/install-macos.sh
```

The default FL Studio user data folder is used. If you configured a custom one in
FL Studio, pass it to the installer like this:

```sh
FL_STUDIO_USER_DATA_ROOT="/path/to/FL Studio" ./scripts/install-macos.sh
```

In FL Studio, open **Options → MIDI settings**:

1. Enable the IAC bus containing `ActivityRecorder Control` under Input.
2. Set its Controller type to **Activity Recorder (user)**.
3. Assign it an unused FL port number, such as `42`.
4. Enable the IAC bus containing `ActivityRecorder Events` under Output and assign
   the same FL port number.
5. Leave the Events bus disabled under Input and the Control bus disabled under Output.
6. Open **View → Script output** and confirm that Activity Recorder initializes.
   It must print `schema=3 build=project-title-fallback-v1`.

Start the companion:

```sh
"$HOME/Library/Application Support/FLActivityRecorder/.venv/bin/fl-activity-recorder"
```

The dashboard opens locally. Recording begins only after you press **Start recording**.

## Install on Windows

Open PowerShell in this repository and run:

```powershell
.\scripts\install.ps1
```

The installer copies the observer to your user Hardware folder and installs the local companion. If this repository has a `.env` file, the installer copies it to `%LOCALAPPDATA%\FLActivityRecorder\.env` so the installed companion can read its Cloudflare R2 settings. It deliberately does not install loopMIDI or alter FL Studio settings.

In loopMIDI, create these ports:

- `ActivityRecorder Control`
- `ActivityRecorder Events`

In FL Studio, open **Options → MIDI settings**:

1. Enable `ActivityRecorder Control` under Input.
2. Set its Controller type to **Activity Recorder (user)**.
3. Assign it an unused FL port number, such as `42`.
4. Enable `ActivityRecorder Events` under Output and assign the same FL port number.
5. Open **View → Script output** and confirm that Activity Recorder initializes without errors.
   It must print `schema=3 build=project-title-fallback-v1`.

Start the companion:

```powershell
& "$env:LOCALAPPDATA\FLActivityRecorder\.venv\Scripts\fl-activity-recorder.exe"
```

The dashboard opens locally. Recording begins only after you press **Start recording**.

## Unified EmotiBit timeline and screen recording

The dashboard records numeric EmotiBit LSL streams live alongside FL Studio events and a
user-authorized Chrome window recording. EmotiBit must be producing recent LSL samples before a
session can start; no SD-card recording or post-session DataParser import is required. See
[unified-timeline.md](docs/unified-timeline.md) for setup, collection, quality labels, and platform
validation steps.

After a session, select a suggested physiological pattern or drag across the synchronized event
ruler or signal traces to annotate an interval. Saved notes and free-form labels appear in the
cross-session Bookmarks notebook and reopen the original video, FL Studio events, and physiology
in context. Bookmarks stay on this device and are not included in research archives or cloud
uploads.

In a finished session, the **Auditions** list identifies separate continuous FL Studio
playback intervals. Choose an interval of 8 seconds to 5 minutes and click **Generate Overlay**
to create a local **Ambient Texture** WAV guided by recorded heart rate (HR) and EDA (EA).
Enable **Original + Overlay** to compare it with the original recording; Overlay Gain defaults
to 30%. Playback, seeking, pausing, buffering, and playback speed follow the video. Heartbeat
listening remains independently available and can play at the same time. Added sounds share a
limited output; original-only playback with heartbeat off retains unity gain.

Overlay generation requires a complete playback interval, recorded audio that FFmpeg can decode,
video coverage of the entire interval, and reliable HR or EA samples. Missing one physiological
channel uses a neutral control for that channel. Incomplete intervals and known playback-event
gaps remain unavailable. The first version provides a non-tonal ambient preset, without beat or
harmonic analysis or inference about emotions. HR uses a robust session baseline of one-second
sample medians; long signal gaps return gently to neutral rather than interpolating across them.

FFmpeg is supplied by the `imageio-ffmpeg` platform wheel and included by the build scripts.
Generation happens only on request, keeps the original recording and FL Studio project intact,
and caches WAV files under the session's `overlays` directory. **Regenerate** uses a new seed;
the last ready preview remains available if regeneration fails. Deleting a session cancels its
generation work and removes its overlays. Overlays are not included in research archives or
cloud uploads; restored sessions can generate their own local overlays.

For development, run `python -m pytest tests/test_overlays.py` for real FFmpeg WebM decoding,
WAV generation, cache/ownership checks, and cancellation/recovery scenarios. Before release,
verify Chrome playback and seeking on a real recording on Windows and macOS, including audio
unlock, buffering, rate changes, independent heartbeat mixing, five-minute memory use, and
drift (target: at most 100 ms during ordinary local playback; not sample-accurate sync).
`node --test tests/overlay_playback.test.cjs` additionally checks playback offsets, drift recovery,
pause/seek, rate changes, buffering, interval boundaries, and the shared output routing using
controlled media/audio clocks without a browser dependency.

The companion blocks recording when FL Studio is still running an older observer. Run the
installer again, then reload Activity Recorder from **View → Script output** or restart FL Studio.

To check a real-FL JSONL export against the supported event catalog:

```sh
fl-activity-validate /path/to/session.jsonl
```

The report lists observed and missing event classes, confidence levels, and callback provenance.

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/mypy src
```

On Windows, replace `.venv/bin/` with `.venv\Scripts\` and use `py -3` when
creating the environment.

Build a standalone executable with `scripts/build-macos.sh` on macOS or
`scripts/build.ps1` on Windows. PyInstaller artifacts are platform-specific and must
be built on the operating system where they will run.

FL Studio is not needed for automated tests. Tests inject fake FL modules and emulate fragmented
SysEx messages. A real FL Studio run on both platforms remains a release requirement; use
[real-fl-validation.md](docs/real-fl-validation.md).

## Privacy and deletion

The service binds only to `127.0.0.1`. Sessions remain in the platform's local app-data
folder until deleted from the dashboard. Project titles, channel names, plugin names,
and parameter labels are stored; project paths are not collected. See
[privacy.md](docs/privacy.md).

## Sharing with researchers

For any finished or interrupted session, choose **Download archive** to save a versioned,
checksummed ZIP containing the available WebM video, CSV physiological channels, and CSV/JSONL FL
Studio events. Missing or incomplete sources remain identified by the session metadata rather than
blocking recovery of the usable data. When private Cloudflare R2 sharing is configured, **Upload to
cloud** stores the same immutable archive in a shared catalog. Collaborators can refresh **Cloud sessions**, download
the ZIP, and restore it with **Import research archive** on their own recorder. See
[data-sharing.md](docs/data-sharing.md) for R2 setup, the archive layout, timeline convention, and
handling recommendations for human-subject data.

## Documentation

- [Feasibility and capture boundaries](docs/feasibility.md)
- [Protocol](docs/protocol.md)
- [Privacy](docs/privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Real FL Studio validation](docs/real-fl-validation.md)
- [Unified timeline setup and validation](docs/unified-timeline.md)
- [Sharing research sessions](docs/data-sharing.md)
