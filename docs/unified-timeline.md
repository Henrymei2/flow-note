# Unified FL Studio, EmotiBit, and video timeline

The companion owns session lifecycle and maps all sources into seconds relative to the session's
LSL `local_clock()` value. A session remains useful when video is unavailable or EmotiBit data has
not yet been imported; the dashboard reports each source separately.

## Requirements and one-time setup

- Windows 10/11 or macOS 13 or later
- Latest desktop Google Chrome
- Official stock EmotiBit firmware, EmotiBit Oscilloscope, and EmotiBit DataParser
- FL Studio, Oscilloscope, and the companion on the same computer

Close EmotiBit Oscilloscope before editing its settings. In `emotibitCommSettings.json`, configure
the LSL marker source as follows. On Windows the settings are normally under
`C:\Program Files\EmotiBit\EmotiBit Oscilloscope\data\`; on macOS use **Show Package Contents** and
open `Contents/Resources/`.

```json
{
  "lsl": {
    "marker": {
      "name": "DataSyncMarker",
      "sourceId": "12345"
    }
  }
}
```

Preserve the other existing settings in that file. In DataParser's `parsedDataFormat.json`, set
both `LslLocalTimestamp` and `LslMarkerSourceTimestamp` entries to `"addToOutput": true`. The
importer requires `LslMarkerSourceTimestamp`; it intentionally does not guess an alignment from
wall-clock time.

## Collection and import

1. Wear the battery-powered EmotiBit and connect it in Oscilloscope. Never wear EmotiBit while it
   is connected by USB or another conductor to equipment attached to AC mains.
2. Start recording in Oscilloscope and keep it connected so periodic time-sync samples span the
   recording.
3. Open the local dashboard in Chrome, press **Start recording**, and choose only the FL Studio
   window. If Chrome does not provide an audio track, the session continues with silent video and
   records a diagnostic.
4. Stop the dashboard session, then stop the EmotiBit recording.
5. Run the official DataParser. On the completed session page choose **Import EmotiBit** and select
   all parsed signal CSV files plus the matching `_info.json` in one operation.

The importer copies the supplied files for provenance, checks their SHA-256 hashes, requires
monotonic LSL source timestamps that cover the session, and generates memory-mapped signal arrays
plus display decimation levels. Reimporting the exact same bundle is a no-op. A different bundle
requires the explicit `replace=true` API option.

## Quality and validation

`aligned_unverified` means that required LSL timestamps passed structural and coverage checks; it
does not claim measured 50 ms accuracy. A release may claim the target only after the real-hardware
procedure below passes independently on Windows and macOS:

- record visible FL state changes and compare their observer timestamps with the first changed
  video frame;
- keep Oscilloscope connected for multiple time-sync pulses across the recording;
- verify both LSL columns appear in DataParser output;
- calculate median, p95, and maximum alignment error, requiring p95 at or below 50 ms.

Record browser clock RTT, FL MIDI transport delay, dropped packet diagnostics, capture
interruptions, and any missing audio in the session report. Mark results above the threshold as
degraded rather than silently adjusting timestamps.
