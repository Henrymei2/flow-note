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
playback intervals. Choose an interval of 8 seconds to 5 minutes and click **Generate Overlay**.
The local **Physiological Overlay** combines three mappings:

- **HR → drums:** soft synthesized kick, tap and shaker follow recorded FL Studio BPM.
  Heart rate relative to the session baseline controls velocity and eighth-note fills.
- **EDA → space:** smoothed skin response controls a short stereo reverb on the original
  recording as well as the added sounds. Missing EDA leaves the mix dry.
- **Temperature → background:** temperature relative to the session baseline controls the
  brightness and level of a non-tonal noise texture. Reliable T1 takes priority over TH.

Enable **Original + Overlay** to compare with the original. **Drums**, **Background**, and
**Space** have separate controls, each initially 30%; changing them never regenerates audio.
Audition intervals appear as teal ruler markers and trace washes beside Pattern suggestions.
**Show auditions** only hides/shows these timeline markers; click a marker to select an interval. The selected Overlay
settings sit below the timeline. Selecting a different interval stops the old preview.
Space uses a damped 2.4-second stereo reverb on the original and stems. Background is
a soft, locally synthesized wind/distant-surf texture instead of bright noise. Old sound
caches prompt for explicit generation of the updated version.
Playback, seeking, pausing, buffering, and playback speed follow the video. Overlay and the
separate heartbeat listener are mutually exclusive. Original-only playback with heartbeat off
retains unity gain; mixed playback has a shared output limiter.

Generation requires a complete interval, full recorded-audio coverage verified with FFmpeg,
at least one reliable HR, EA, T1 or TH channel, and no recorded BPM changes. Historical
intervals without recorded BPM temporarily use **100 BPM**, labelled as a default in the UI.
Recorded BPM takes priority. Contradictory tempo evidence and playback-event gaps disable generation
with an explanation. The generated beat grid starts at the audition boundary. Preview initially delays drums by
**180 ms** to compensate for the observed early beat phase. Use **Drum timing** to adjust from
600 ms earlier to 600 ms later without regenerating;
positive values mean later. The adjustment affects only drums and follows seeking and speed changes. No harmonic analysis or emotional inference is performed.
Missing HR uses a fixed gentle rhythm; missing temperature uses a fixed background texture.
Long physiological gaps return gently to neutral rather than being interpolated across.

The backend streams separate 48 kHz stereo PCM WAV stems and caches versioned controls and
results under the session's `overlays` directory. EDA reverb runs in Web Audio during preview,
so the original recording does not need another offline render. SciPy batch filters and cached
source validation replace per-sample Python filtering and repeated full-session feature work.
FFmpeg is supplied by the `imageio-ffmpeg` platform wheel and included by the build scripts.

Generation is explicit and keeps the original recording and FL Studio project intact.
**Regenerate** uses a new seed; a failed replacement preserves the last ready preview.
Session deletion cancels generation and removes every stem. Overlays are not included in
research archives or cloud uploads; restored sessions generate their own local overlays.
Legacy API clients can still request `ambient`; the new UI requests `physiological` and does
not reuse legacy ambient previews.

Run `python -m pytest tests/test_overlays.py` for audio, tempo, physiology, cache and lifecycle
tests, and `node --test tests/overlay_playback.test.cjs` for synchronized stems, routing,
independent gains, and effect cleanup. Before release, audition real recordings on Windows
and macOS, checking subjective balance and beat phase. Ordinary local playback targets
at most 100 ms drift, not sample-accurate sync.
See [implementation notes and measured performance](docs/physiological-overlays.md).

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
