# FlowNote

FlowNote is a recording and reflection tool for FL Studio. It alings FL Studio events, window recordings, and EmotiBit physiological signals onto one timeline, so you can revisit what you were doing, annotate a moment, and hear how your body signals change within an audition.

The project runs on macOS and Windows. A MIDI script observes FL Studio, and a Python companion stores sessions locally and serves the workspace at http://127.0.0.1:8765.

## Requirements

| Component | Requirement |
| --- | --- |
| Operating system | macOS supported by FL Studio, or Windows 10/11 |
| FL Studio | 26.1.3+ with MIDI scripting API 42+ |
| Python | 3.12 or newer |
| Browser | Desktop Google Chrome for window capture (Chrome's captured audio is optional for recording, but a usable recorded audio track is required for Overlay preview.) |
| Physiology | EmotiBit Oscilloscope 1.11.1+ with numeric LSL Output enabled |
| Virtual MIDI | macOS IAC Driver, or loopMIDI on Windows |

## Installation

```sh
git clone https://github.com/Henrymei2/flow-note.git
cd flow-note
```

### macOS

1. Open **Audio MIDI Setup → Window → Show MIDI Studio**.
2. Open **IAC Driver**, enable **Device is online**, and add two buses:
   `ActivityRecorder Control` and `ActivityRecorder Events`.
3. Run the installer from the repository:

   ```sh
   ./scripts/install-macos.sh
   ```

If FL Studio uses a custom user data folder, pass that folder to the installer:

```sh
FL_STUDIO_USER_DATA_ROOT="/path/to/FL Studio" ./scripts/install-macos.sh
```

Start the installed companion with:

```sh
"$HOME/Library/Application Support/FLActivityRecorder/.venv/bin/fl-activity-recorder"
```

### Windows

1. Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html).
2. Create two ports: `ActivityRecorder Control` and `ActivityRecorder Events`.
3. Open PowerShell in the repository and run:

   ```powershell
   .\scripts\install.ps1
   ```

Start the installed companion with:

```powershell
& "$env:LOCALAPPDATA\FLActivityRecorder\.venv\Scripts\fl-activity-recorder.exe"
```

The installers copy the observer into FL Studio's user Hardware folder and install the companion. Virtual MIDI and FL Studio MIDI settings must be configured separately. If a repository-local `.env` exists, the installers also copy it to the companion's data directory.

### Connect FL Studio

In **Options → MIDI settings** on either platform:

1. Enable `ActivityRecorder Control` under **Input**. On macOS, its name may include the IAC bus prefix.
2. Choose **Activity Recorder (user)** as the controller type.
3. Assign an unused FL port number, such as `42`.
4. Enable `ActivityRecorder Events` under **Output**, using the same FL port number.
5. Leave Events disabled under Input and Control disabled under Output.
6. Open **View → Script output** and check that the observer initializes. The current
   contract is `schema=3 build=project-title-fallback-v1`.

After an update, rerun the installer, reload the observer or restart FL Studio, and restart the companion. The dashboard blocks recording if the installed observer is out of date.

## Record and review

1. Enable **LSL Output** in EmotiBit Oscilloscope. Stream names must start with `EmotiBit` (an [example](lslOutputSettings.example.json) is provided).
2. Open the local workspace in Chrome and wait for FL Studio and EmotiBit to connect.
3. Press **Start recording** and choose the FL Studio window in Chrome's capture dialog.
4. Make music, then press **Stop recording**.
5. Open the finished session to inspect its video, events, and physiological tracks.

Select a suggested interval or drag across the timeline to add a note and labels. Saved moments appear in **History** and retain their connection to the original session. Notes and labels stay local and are excluded from research archives and cloud sharing.

See [troubleshooting](docs/troubleshooting.md) for connection and capture problems.

## What it can / cannot capture

The observer uses documented FL callbacks and state. It records transport, channel, mixer, pattern and step-grid changes, supported plugin parameters, navigation, hint messages, and available action/undo context. It cannot reconstruct arbitrary Piano Roll notes, Playlist clips, automation points, or private plugin UI state.

## Documentation

- [Capture feasibility and boundaries](docs/feasibility.md)
- [Observer protocol](docs/protocol.md)
- [Unified timeline setup](docs/unified-timeline.md)
- [Pattern suggestion algorithm](docs/pattern-suggestions.md)
- [Physiological Overlay implementation](docs/physiological-overlays.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Real FL Studio validation](docs/real-fl-validation.md)
- [Research archive sharing](docs/data-sharing.md)
- [Privacy](docs/privacy.md)

## Help

You can contact us by creating an [Issue](https://github.com/Henrymei2/flow-note/issues) or sending an email to [hmei24@wisc.edu](mailto:hmei24@wisc.edu)
