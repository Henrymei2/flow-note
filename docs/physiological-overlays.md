# Physiological Overlay v3

This implementation supersedes the original prototype's HR/EDA ambient mappings and
Ambient/Soft Percussion preset split. It implements the revised HR → drums, EDA → space,
and temperature → background design. All processing stays local and recordings stay intact.

## Timeline and controls

Show auditions independently toggles teal ruler markers and trace washes; it does not
change audio playback or Pattern suggestions. Click a ruler marker to select its interval.
There is no separate list of per-interval duration buttons, keeping long sessions compact.
Markers remain aligned while zooming. Ineligible intervals remain selectable so
their reason can be read. Selected settings sit below the timeline in responsive Drums,
Space and Background groups. Choosing another interval, editing boundaries or clearing
selection detaches the old preview; hiding markers leaves it running. Selecting an audition
does not focus the annotation textarea. Session changes discard the old marker collection.

## Generation and preview

The UI requests `preset: "physiological"` on the existing overlay POST endpoint. Requests
without a preset, or with `preset: "ambient"`, remain supported for legacy clients. The
new preset uses recorded constant BPM when available, or a labelled **100 BPM** temporary
fallback for historical intervals without tempo evidence. An actual tempo change, even one that later
returns to the starting BPM, disables the whole audition. Repeated identical BPM values
are accepted. A change exactly at the start establishes the starting BPM; a change exactly
at the stop belongs to the next interval. The generated beat grid starts at audition time zero. Preview defaults to delaying the
drums by **+180 ms**. The Drum timing slider ranges from −600 to +600 ms in 10 ms steps;
positive values delay, negative values advance. Its label also shows the BPM-relative beat
fraction. This is a manual
phase correction, not automatic beat detection. Adjustments are retained on the selected
audition object while its session review remains loaded, and require no regeneration.
Delayed starts, seeks and playback-rate changes apply the same millisecond offset;
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
- `spatial`: the EDA control values, their duration, a 2.4-second room response setting,
  and maximum wet gain of 0.6. Send gain is `max_wet × Space × (0.4 + 0.6 × EDA)`,
  with EDA clamped to [0, 1]. Baseline EDA with default Space sends 12.6%, versus
  5.25% previously. Missing EDA disables Space and explicitly reports the reason.
- `timings`: audio/BPM validation, feature extraction, and stem synthesis seconds.

Both stems must finish before the ready metadata is published. Interrupted or failed
jobs clean up partial files; restart recovery preserves every referenced ready stem.
Cache identity includes source references (including temperature), version `physiology-v3`,
configuration, preset, and seed. Old results are not offered as v3 UI previews. An audition with an older ready result
sets `needs_regeneration`; the UI prompts for explicit generation without starting a job.
Feature, baseline and audio-validation caches are bounded to 128, 8 and 128 entries.
POST validates only the requested interval, even in sessions containing many auditions.

In the browser, both decoded stems follow video time plus the capture offset. Drums and
background have separate gains, initially 0.3. EDA controls a wet send from the original
and both stems into a deterministic stereo convolution room; Space initially 0.3. The impulse has
25 ms predelay, early reflections, bass/high-frequency damping, unit energy per channel
and explicit `ConvolverNode.normalize = false`; its cache includes length and sample rate.
The previous weak send and short room made the effect subtle; steady EDA also produces a
steady room rather than a changing effect, which the UI now identifies.
The original dry track stays at unity. The wet return and dry mix share output limiting.
A/B, pause, seek, buffering, and interval changes clear the old effect history; short gain
ramps avoid abrupt transitions. Outside the interval the original bypasses overlay limiting.
The independent heartbeat listener and Overlay disable each other when enabled.

No fully mixed render or export is added. Both stem files are stereo 16-bit PCM at 48 kHz;
browsers may resample them when decoding. Only the selected audition is retained in memory.
At five minutes, two decoded stereo Float32 stems use about 230 MB at 48 kHz, plus transient
fetch/decode buffers; the Python generator streams 4096-frame blocks.

Temperature controls a locally synthesized distant wind/surf texture. Two third-order
band-pass filters (100–550 and 100–1400 Hz) blend smoothly while preserving filter state
across blocks. Three seeded, incommensurate slow swells add motion without a beat. Warmer
controls add brightness and a little level; soft saturation avoids abrupt clipping.
A 10-second seed-42 comparison at temperature controls 0.2/0.8 measured RMS 0.0328/0.0369,
spectral centroids 340/514 Hz and energy above 3 kHz 0.012%/0.147%. This verifies spectral
softening, rather than establishing a subjective preference. At neutral controls, the
previous v2 texture had 9.43% energy above 3 kHz, versus 0.060% for v3 (same 10-second
seed-42 setup). New isolated comparison WAVs were produced for listening; no claim of
subjective approval with the user’s own music is made.

## Measured performance

Measured locally on macOS arm64, Python 3.14.7, NumPy 2.5.2, SciPy 1.18.1, against the
original generator from commit `118e1f6`. Constant controls, 48 kHz stereo, seed 0, WAV files
on local temporary storage; import/startup costs excluded. These are local measurements,
not guaranteed timings on other machines.

| Audio length | Old background only | New background + drums | Synthesis speedup |
| --- | ---: | ---: | ---: |
| 10 s | 1.331 s | 0.066 s | 20.1× |
| 60 s | 8.131 s | 0.372 s | 21.8× |
| 300 s | 40.751 s | 1.882 s | 21.7× |

A separate end-to-end run used one synthetic session with three distinct 10/60/300-second
auditions, a real Opus/WebM recording, and HR, EDA and T1 at 1 Hz. Baseline computation is
shared across intervals. Times are measured from manager request to ready, including
FFmpeg audio validation, feature extraction, disk writes and a 5 ms completion poll:

| Audio length | First request to ready | Matching ready cache hit |
| --- | ---: | ---: |
| 10 s | 0.160 s | 1.10 ms |
| 60 s | 0.518 s | 1.20 ms |
| 300 s | 2.490 s | 2.18 ms |

Browser network loading and WAV decoding are additional and logged separately in the
browser console. The UI polls generation every 700 ms, so very short jobs can appear to
finish on the following poll. Generate does not render or decode the original recording
again once its source validation is cached.

## Verification

104 Python tests and 9 frontend tests, Ruff and strict mypy checks passed locally.
Automated coverage includes tempo recovery/changes/conflicts/boundaries, missing channels,
temperature priority and cache invalidation, EDA trends and gaps, grid-aligned HR density,
PCM length/peaks/fades/seeds/cancellation, stem serving and ownership, cache reuse,
regeneration failure, restart recovery, session deletion and malformed requests.
Frontend clock tests cover synchronized stems, offsets, pause, seek, drift, buffering,
rate changes, independent controls, original routing, wet-send cleanup and heartbeat exclusion. Phase tests
cover delayed onset, seek, speed, recorded BPM, live drum-only adjustment and buffer boundaries.
Timeline tests cover clipping, marker selection, visibility-only toggles, ineligible
intervals, edited boundaries and session reset. Spectral tests cover hiss suppression, temperature
response and continuity at block boundaries; stale-version results require explicit regeneration.

A macOS in-app browser with native Web Audio and real WebM/PCM fixtures passed decoding,
convolution, seek offset, cleared reverb history, rate, pause, A/B and interval-boundary
checks. A native offline convolution produced a measurable post-source tail (RMS 0.00661
from a 100 ms, 440 Hz burst with peak 0.2 and default baseline send). Production UI was
checked at a 592 px viewport without horizontal overflow. The temporary test server
supported byte ranges, matching the production media API. Pattern coexistence checks use
an explicitly synthetic suggestion fixture.
The prior v2 macOS arm64 one-file PyInstaller build successfully decoded source audio, generated
both stems through its API and served them as WAVs. Windows test/build remains covered by
the existing CI matrix; a Windows execution and subjective listening with actual musical
recordings were not performed in this macOS session. The v3 changes were not repackaged.
