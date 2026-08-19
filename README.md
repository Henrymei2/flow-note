# FL Studio Activity Recorder

A private, local-first timeline of supported semantic activity in FL Studio 26.1.3+
on macOS and Windows.

The recorder has two parts:

1. A transparent FL Studio MIDI controller script observes documented host callbacks and state.
2. A local companion receives those events over virtual MIDI, stores active sessions in SQLite, and serves a dashboard at `http://127.0.0.1:8765`.

No cloud service, telemetry, accessibility hooks, screenshots, keylogging, audio capture, or physical-controller MIDI routing is included.

## What it can record

- Project load lifecycle and project metadata exposed by FL Studio
- Recording and tempo transitions
- Channel creation, deletion, and replacement
- Audible mixer control changes
- Pattern creation/deletion and selected-channel step-grid changes
- Focused plugin parameter changes when the plugin exposes host parameters
- FL Studio's own action label for relevant edits, undo, and redo when no more specific
  supported state difference is available

The observer deliberately ignores window focus, tab and item selection, Browser activity,
hint messages, colors, renames, playback navigation, and other activity that does not change
the music. FL Studio does not expose enough information to reconstruct arbitrary Piano Roll
notes, Playlist clips, automation points, or private plugin UI state. Undo labels describe the
operation but not its complete before/after data. See [feasibility.md](docs/feasibility.md).

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

Start the companion:

```powershell
& "$env:LOCALAPPDATA\FLActivityRecorder\.venv\Scripts\fl-activity-recorder.exe"
```

The dashboard opens locally. Recording begins only after you press **Start recording**.

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

FL Studio is not needed for automated tests. Tests inject fake FL modules and emulate fragmented SysEx messages. A real FL Studio run remains a release requirement; use [real-fl-validation.md](docs/real-fl-validation.md).

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
