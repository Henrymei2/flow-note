# Physiological Overlay v4

HR controls drums, EDA controls reverb including the original recording, and temperature
controls a non-tonal wind/surf background. Version 4 uses each audition's own typical
level and variation range. It emphasizes changes within a clip; levels in different
clips no longer directly represent their absolute physiological differences. Processing
is local, original recordings remain intact, and no additional manual controls are added.

## Interval normalization

Only the requested interval's raw HR, EA, T1 and TH are read. Samples outside HR 30–220,
EA 0–100 and temperature 10–50 are discarded. At least three valid samples and 50% interval
coverage are required; gaps over five seconds are unsupported. Reliable T1 is selected
for the entire interval, otherwise TH. Missing HR uses fixed drums, missing temperature
uses fixed surf parameters, and missing EDA disables reverb. No reliable channel disables
generation; valid but flat channels still allow generation.

Samples are reduced to one median per second relative to the interval start, interpolated
onto a 10 Hz grid, then smoothed separately inside each supported run. Physical-value
smoothing time constants are HR 0.6 s, EDA 0.8 s and temperature 1.5 s. Each run starts
at its first value, preventing an artificial opening rise and cross-gap derivative spikes.
Grid values are time-weighted equally for the whole-interval median and P10/P90 statistics.

| Channel | Minimum P90–P10 range |
| --- | ---: |
| HR | 2 bpm |
| EDA | 0.02 µS |
| T1 / TH | 0.03 °C |

These are musical noise guards, not clinical thresholds. Below the threshold, the control
is exactly 0.5. Otherwise `scale = (P90 - P10) / 2` and
`u = 0.5 + 0.45 * tanh((value - median) / scale)`. EDA adds a smoothed-value derivative
contribution `0.08 * tanh(derivative / scale)`, evaluated only within each supported run.
Controls are clipped to [0.05, 0.95]. A final 0.15 s control smoother avoids abrupt changes;
unsupported samples target 0.5. Its initial state matches the first target. The UI identifies
flat selected channels with a brief “Little change” message.

## Automatic musical parameters

### HR drums

The musical grid follows constant recorded FL Studio BPM, with a labelled 100 BPM fallback
for historical sessions without tempo. Tempo changes/conflicts still disable generation.
Kick and tap alternate on quarter notes. Velocity is `0.35 + 0.65 * u`.

| HR control | Added shaker offbeats |
| --- | --- |
| Below 0.35 | None |
| 0.35–0.55 | Once every four beats |
| 0.55–0.75 | Once every two beats |
| At least 0.75 | Every eighth-note offbeat |

Density is initialized from the first control, updated every four beats, and held between
updates. A 0.03 hysteresis margin applies to both upward and downward transitions.
Kick decay varies from 240 to 160 ms and tap decay from 120 to 70 ms as HR rises. Kick
fundamental and pitch sweep stay fixed. Tap/shaker brightness rises with a band-pass upper
cutoff from 2200 to 4500 Hz. Samples use finite attack/tail envelopes. Thirty-two articulation
steps are cached per instrument per job (at most 96 mono Float32 samples, about 4.6 MB).
Seeded noise is shared across articulation levels, so changes remain reproducible.

### Temperature wind/surf

The background blends third-order 100–550 Hz and 100–1400 Hz noise bands, retaining filter
state between blocks. Temperature controls bright-band mix and level, plus:

- Swell depth: `0.08 + 0.30 * u`.
- Main swell frequency: `0.06 + 0.06 * u` Hz; a weaker second swell follows at 0.61 times
  that frequency. Both phases are integrated continuously across samples and blocks.
- Stereo width: `0.20 + 0.45 * u`, using equal-power common and independent noise rather
  than phase inversion. The mono centre remains present.

A weaker seeded 0.037 Hz drift adds natural movement. Missing/flat temperature leaves these
parameters neutral while the surf continues moving. Soft saturation and per-stem peak
limits suppress harsh clipping; spectral tests verify continued high-frequency suppression.

### EDA space

The browser reverb graph is unchanged from v3: original, drums and background send into
a damped 2.4-second stereo convolution room with 25 ms predelay and early reflections.
Impulse channels have unit energy and explicit `ConvolverNode.normalize = false`.
Send gain remains `0.6 * Space * (0.4 + 0.6 * EDA_control)`, capped by the metadata maximum;
baseline EDA at default Space sends 12.6%. New interval-relative EDA controls provide the
larger variation. Dry original gain remains unity and mixed output uses the shared limiter.

## Preview, API and cache compatibility

Show auditions toggles teal ruler markers and trace washes independently of suggestions
and audio. There is no separate list of duration buttons. Choosing another interval,
resizing its boundaries or clearing selection detaches playback. Existing Drums, Space
and Background controls remain at 30%; A/B, heartbeat exclusion, seek, pause, buffering,
speed and interval boundaries retain their existing behavior. Drums default to +180 ms,
with live −600 to +600 ms adjustment. The generated grid starts at the audition boundary.

`preset: "physiological"` still returns stereo 48 kHz 16-bit PCM `ambient` and `drums` stems.
Existing `ambient` requests and `.../audio?stem=ambient|drums` remain supported. Ready results
add `normalization: {scope: "audition", channels: {...}}`; each channel includes validity,
selection, baseline, P10/P90, threshold and flat status. Missing/unselected statistics are
null. Audition eligibility metadata also includes this summary so flat data can be explained
before generation. Existing BPM, channel provenance, spatial curves and timings remain.

Cache identity includes interval bounds, source fingerprint (including temperature),
`physiology-v4`, configuration, preset and seed. Features and normalization summaries share
a bounded 128-entry interval cache; the full-session baseline scan/cache has been removed.
Audio validation remains separately cached to 128 entries. Old ready results prompt for
explicit generation and are never offered as v4 previews. Both stems must finish before
ready publication; cancellation, failed replacements, restart recovery and deletion retain
the existing cleanup behavior. Original recordings and FL Studio projects are not modified.

## Verification and measured performance

On macOS arm64, Python 3.14.7, NumPy 2.5.2 and SciPy 1.18.1, seed 0 and smoothly varying
controls, local WAV synthesis measured as follows. V3 was loaded from commit `743a5cf`;
import/startup costs are excluded. These measurements are not a cross-machine guarantee.

| Duration | V3 background + drums | V4 background + drums |
| --- | ---: | ---: |
| 10 s | 0.064 s | 0.076 s |
| 60 s | 0.369 s | 0.416 s |
| 300 s | 1.840 s | 1.981 s |

A synthetic session with three auditions and real Opus/WebM audio measured manager request
to ready (including validation, features, disk writes and a 5 ms completion poll):

| Duration | First request to ready | Ready-cache hit |
| --- | ---: | ---: |
| 10 s | 0.158 s | 1.00 ms |
| 60 s | 0.540 s | 1.35 ms |
| 300 s | 2.499 s | 2.87 ms |

Feature extraction took approximately 0.009–0.016 s. UI polling remains 700 ms and browser
loading/decoding is additional; its existing timing log is retained. V4's 10-second synthesis
remains well below the original approximately 1.45-second background-only reference.

120 Python tests, 10 frontend tests, Ruff and strict mypy pass locally. New coverage includes
absolute-offset invariance, interval-only raw queries, equal-time weighting, low-variation
noise guards, valid-run initialization, gaps/trend reset, temperature selection, four drum
densities and hysteresis, fixed kick tuning, envelope/brightness changes, sample reuse,
wind width/depth/rate, mono compatibility and block continuity. Existing duration, fade,
peak, deterministic seed, cancellation, cache, recovery and preview-clock checks also pass.

An approximately 38.8-second actual local recording was read without modification to make
v3/v4 offline A/B files in the ignored `.preview/overlay-v4/` directory. Both use seed 0,
identical original audio, 30% controls, +180 ms drums and the unchanged stereo impulse.
A common headroom gain applies to both (1.0 for this clip); the offline comparison does
not emulate the browser's dynamic compressor. No data was uploaded. Control spans changed
from approximately HR/EDA/temperature 0.107/0.175/0.165 to 0.822/0.752/0.742. Mixed RMS
was 0.07314 versus 0.07317, so the comparison does not rely on an overall loudness increase.
The files and private statistics stay local. Subjective listening approval has not been
established; the user can compare the A/B with their own ears.

The unchanged browser convolution/synchronization graph was verified natively in the
previous v3 work; this revision runs its frontend regression tests. The new revision has
not been repackaged or executed on Windows in this macOS session.
