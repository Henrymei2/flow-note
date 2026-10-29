# FlowNote

FlowNote is a wearable system that synchronizes a creator’s physiological data with their music revision history in [FL Studio](https://www.image-line.com/). It aligns [FL Studio events](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/midi_scripting.htm), window recordings, and [EmotiBit](https://www.emotibit.com/) physiological signals onto one timeline, so you can revisit what you were doing, annotate a moment, and hear how your body signals change within an audition. For stable physiological signal collection, please wear the EmotiBit on your forehead.

The project runs on macOS and Windows. A MIDI script observes FL Studio, and a Python companion stores sessions locally and serves the workspace at http://127.0.0.1:8765.


## Installation

### Requirements

| Component | Requirement |
| --- | --- |
| Operating system | macOS supported by FL Studio, or Windows 10/11 |
| FL Studio | 26.1.3+ with MIDI scripting API 42+ |
| Python | 3.12 or newer |
| Browser | Desktop Google Chrome for window capture (audio capture is required for usable overlay preview.) |
| Physiology | EmotiBit Oscilloscope 1.11.1+ with numeric LSL Output enabled |
| Virtual MIDI | macOS IAC Driver, or loopMIDI on Windows |

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

## Record and review

1. Enable **LSL Output** in EmotiBit Oscilloscope. Stream names must start with `EmotiBit` (an [example](lslOutputSettings.example.json) is provided). See [reference](https://github.com/EmotiBit/EmotiBit_Docs/blob/master/EmotiBitOscilloscope_reference_manual.md).
2. Open the local workspace in Chrome and wait for FL Studio and EmotiBit to connect.
3. Press **Start recording** and choose the FL Studio window in Chrome's capture dialog.
4. Make music, then press **Stop recording**.
5. Open the finished session to inspect its video, events, and physiological tracks.

Select a suggested interval or drag across the timeline to add a note and labels. Saved moments appear in **History**.

![screenshot of session review screen showing window recording, fl studio events, and physiological data on the same timeline. Choosing a pattern suggestion allows labeling and saving the moment, and choosing an audition section allows generating overlay sound effects based on physiological data](docs/fl1.png)

## Documentation

- [Troubleshooting](docs/troubleshooting.md)
- [Research archive sharing](docs/data-sharing.md)

## Helpful Resources

FL Studio API: https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/midi_scripting.htm
