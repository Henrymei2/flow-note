# Physiological Overlay v2

This implementation supersedes the original prototype's HR/EDA ambient mappings and
Ambient/Soft Percussion preset split. It implements the revised HR → drums, EDA → space,
and temperature → background design. All processing stays local and recordings stay intact.

## Generation and preview

The UI requests `preset: "physiological"` on the existing overlay POST endpoint. Requests
without a preset, or with `preset: "ambient"`, remain supported for legacy clients. The
new preset uses recorded constant BPM when available, or a labelled **100 BPM** temporary
fallback for historical intervals without tempo evidence. An actual tempo change, even one that later
returns to the starting BPM, disables the whole audition. Repeated identical BPM values
are accepted. A change exactly at the start establishes the starting BPM; a change exactly
at the stop belongs to the next interval. The generated beat grid starts at audition time zero. Preview defaults to delaying the
drums by **+0.5 beat** (300 ms at 100 BPM). The Drum timing slider ranges from −1 to +1
beat in 0.05-beat steps; positive values delay, negative values advance. This is a manual
phase correction, not automatic beat detection. Adjustments are retained on the selected
audition object while its session review remains loaded, and require no regeneration.
Delayed starts, seeks and playback-rate changes apply the same BPM-derived phase;
background and EDA controls retain their original session clock. Live adjustment restarts
only drums, retaining the background and reverb history. Drum playback is bounded by both
the source buffer and audition end, with a short end fade.

Reliable samples cover at least half the interval with at least three samples and no
interpolated gaps longer than five seconds. HR values outside 30–220 bpm, EDA outside
0–100 µS, and temperatures outside 10–50 °C are discarded. Per-second medians produce
robust session baselines; smoothed controls are sampled at approximately 10 Hz. Reliable
T1 is preferred for the entire audition; otherwise TH is used. Missing HR produces a
fixed rhythm, missing temperature a fixed texture, and missing EDA no reverb. All channels
missing disables generation. These mappings describe musical controls, not emotions.

The result adds these fields to the existing ready metadata:

- `stems`: `["ambient", "drums"]`; retrieve each with `.../audio?stem=ambient|drums`.
  Omitting `stem` still serves the ambient WAV. Local stem paths are never exposed.
- `bpm`, `bpm_source` (`recorded` or `default`), `channels`, and `temperature_source`: provenance and conservative fallbacks.
- `spatial`: the EDA control values, their duration, a 1.2-second room response setting,
  and maximum wet gain of 0.35. The user Space control further scales the wet send.
- `timings`: audio/BPM validation, feature extraction, and stem synthesis seconds.

Both stems must finish before the ready metadata is published. Interrupted or failed
jobs clean up partial files; restart recovery preserves every referenced ready stem.
Cache identity includes source references (including temperature), version `physiology-v2`,
configuration, preset, and seed. Old ambient results are not offered as v2 UI previews.
Feature, baseline and audio-validation caches are bounded to 128, 8 and 128 entries.
POST validates only the requested interval, even in sessions containing many auditions.

In the browser, both decoded stems follow video time plus the capture offset. Drums and
background have separate gains, initially 0.3. EDA controls a wet send from the original
and both stems into a deterministic short stereo convolution room; Space initially 0.3.
The original dry track stays at unity. The wet return and dry mix share output limiting.
A/B, pause, seek, buffering, and interval changes clear the old effect history; short gain
ramps avoid abrupt transitions. Outside the interval the original bypasses overlay limiting.
The independent heartbeat listener and Overlay disable each other when enabled.

No fully mixed render or export is added. Both stem files are stereo 16-bit PCM at 48 kHz;
browsers may resample them when decoding. Only the selected audition is retained in memory.
At five minutes, two decoded stereo Float32 stems use about 230 MB at 48 kHz, plus transient
fetch/decode buffers; the Python generator streams 4096-frame blocks.

## Measured performance

Measured locally on macOS arm64, Python 3.14.7, NumPy 2.5.2, SciPy 1.18.1, against the
original generator from commit `118e1f6`. Constant controls, 48 kHz stereo, seed 0, WAV files
on local temporary storage; import/startup costs excluded. These are local measurements,
not guaranteed timings on other machines.

| Audio length | Old background only | New background + drums | Synthesis speedup |
| --- | ---: | ---: | ---: |
| 10 s | 1.372 s | 0.041 s | 33.3× |
| 60 s | 8.255 s | 0.235 s | 35.2× |
| 300 s | 40.590 s | 1.150 s | 35.3× |

A separate end-to-end run used one synthetic session with three distinct 10/60/300-second
auditions, a real Opus/WebM recording, and HR, EDA and T1 at 1 Hz. Baseline computation is
shared across intervals. Times are measured from manager request to ready, including
FFmpeg audio validation, feature extraction, disk writes and a 5 ms completion poll:

| Audio length | First request to ready | Matching ready cache hit |
| --- | ---: | ---: |
| 10 s | 0.151 s | 1.26 ms |
| 60 s | 0.361 s | 1.12 ms |
| 300 s | 1.634 s | 1.55 ms |

Browser network loading and WAV decoding are additional and logged separately in the
browser console. The UI polls generation every 700 ms, so very short jobs can appear to
finish on the following poll. Generate does not render or decode the original recording
again once its source validation is cached.

## Verification

Python and frontend tests, Ruff and strict mypy checks passed locally.
Automated coverage includes tempo recovery/changes/conflicts/boundaries, missing channels,
temperature priority and cache invalidation, EDA trends and gaps, grid-aligned HR density,
PCM length/peaks/fades/seeds/cancellation, stem serving and ownership, cache reuse,
regeneration failure, restart recovery, session deletion and malformed requests.
Frontend clock tests cover synchronized stems, offsets, pause, seek, drift, buffering,
rate changes, independent controls, original routing, wet-send cleanup and heartbeat exclusion. Phase tests
cover delayed onset, seek, speed, recorded BPM, live drum-only adjustment and buffer boundaries.

A macOS in-app browser with native Web Audio and real WebM/PCM fixtures passed decoding,
convolution, seek offset, cleared reverb history, rate, pause, A/B and interval-boundary
checks. The temporary test server supported byte ranges, matching the production media API.
A macOS arm64 one-file PyInstaller build also successfully decoded source audio, generated
both stems through its API and served them as WAVs. Windows test/build remains covered by
the existing CI matrix; a Windows execution and subjective listening with actual musical
recordings were not performed in this macOS session.
