# FL Studio Activity Recorder

A private, local-first timeline of supported semantic activity in FL Studio 26.1.3+
on macOS and Windows.

The recorder has two parts:

1. A transparent FL Studio MIDI controller script observes documented host callbacks and state.
2. A local companion receives those events over virtual MIDI, stores active sessions in SQLite, and serves a dashboard at `http://127.0.0.1:8765`.

No cloud service, telemetry, accessibility hooks, screenshots, keylogging, audio capture, or physical-controller MIDI routing is included.

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
- Python 3.12
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
   It must print `schema=2 build=event-capture-v2`.

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

The installer copies the observer to your user Hardware folder and installs the local companion. It deliberately does not install loopMIDI or alter FL Studio settings.

In loopMIDI, create these ports:

- `ActivityRecorder Control`
- `ActivityRecorder Events`

In FL Studio, open **Options → MIDI settings**:

1. Enable `ActivityRecorder Control` under Input.
2. Set its Controller type to **Activity Recorder (user)**.
3. Assign it an unused FL port number, such as `42`.
4. Enable `ActivityRecorder Events` under Output and assign the same FL port number.
5. Open **View → Script output** and confirm that Activity Recorder initializes without errors.
   It must print `schema=2 build=event-capture-v2`.

Start the companion:

```powershell
& "$env:LOCALAPPDATA\FLActivityRecorder\.venv\Scripts\fl-activity-recorder.exe"
```

The dashboard opens locally. Recording begins only after you press **Start recording**.

The companion blocks recording when FL Studio is still running an older observer. Run the
installer again, then reload Activity Recorder from **View → Script output** or restart FL Studio.

To check a real-FL JSONL export against the supported event catalog:

```sh
fl-activity-validate /path/to/session.jsonl
```

The report lists observed and missing event classes, confidence levels, and callback provenance.

## Development

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/mypy src
```

On Windows, replace `.venv/bin/` with `.venv\Scripts\` and use `py -3.12` when
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

## Documentation

- [Feasibility and capture boundaries](docs/feasibility.md)
- [Protocol](docs/protocol.md)
- [Privacy](docs/privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Real FL Studio validation](docs/real-fl-validation.md)
