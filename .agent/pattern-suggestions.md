# Pattern suggestion algorithm

Pattern suggestions identify stretches of physiological data worth revisiting on the session
timeline. They provide an entry point for reflection and annotation; they do not infer an
emotion, identify a creative state, or establish that a particular FL Studio action caused a
physiological change.

The current algorithm version is **`physiological-pattern-score-v4`**. It is a deterministic
statistical scoring pipeline, not a trained clustering model. Names such as
`biosignal_clusters`, `clusters`, and `centroid` are retained for API compatibility. “Pattern”
here refers to physiological changes, not FL Studio's musical patterns.

Implementation references:

- [Scoring and explanations](../src/fl_activity_recorder/biosignal_clustering.py)
- [HTTP endpoint](../src/fl_activity_recorder/api.py)
- [Timeline and explanation card](../src/fl_activity_recorder/static/app.js)
- [Algorithm regression tests](../tests/test_biosignal_clustering.py)

## 1. Inputs and five-minute partitions

The scorer reads the following recorded signals over the session timeline:

| Modality | Type tags | Input filtering |
| --- | --- | --- |
| Cardio (`C`) | `HR` | Finite values in 30–220 bpm |
| Electrodermal (`E`) | `EA` | Finite values in 0–100 µS |
| Temperature (`T`) | `T1`, `TH` | Finite values; no additional temperature range filter in this scorer |

Timestamps must also be finite. Samples are sorted by timestamp. Movement channels, video,
FL Studio events, user notes, and audio are not inputs to the score. FL Studio events are
shown separately as context in the explanation card.

The session is split into non-overlapping, half-open **300-second partitions**:
`[0, 300)`, `[300, 600)`, and so on. A final partial partition is processed too. A sample at
exactly 300 seconds belongs to the second partition. Windows and score baselines are
computed independently within each partition; no analysis window crosses a partition edge.

Each partition contributes **at most one suggestion**. The session returns at most ten,
chosen from those partition winners. A partition with insufficient data or no qualifying
change contributes nothing; the algorithm does not fill a quota.

The score baseline is the distribution of window features within that partition. It is
neither a whole-session baseline nor the audition-local baseline used by
[Physiological Overlay](physiological-overlays.md). Suggestions do not have to coincide with
FL Studio playback or audition intervals.

## 2. Fixed windows and features

Each modality uses its own complete, half-open sliding windows:

| Modality | Window length | Step | Features and weights |
| --- | ---: | ---: | --- |
| HR | 10 s | 5 s | median 0.5, slope 0.3, delta 0.2 |
| EDA | 60 s | 15 s | median 0.4, slope 0.2, variance 0.4 |
| Temperature | 120 s | 30 s | slope 0.5, delta 0.3, median 0.2 |

Only full windows that fit inside the partition are considered. For example, a 19.9-second
partition has HR windows `[0, 10)` and `[5, 15)`, but none starting at 10 seconds.

Features have these definitions:

- **Median:** the sample median inside the window.
- **Slope:** least-squares slope against actual timestamps, in signal units per second.
  Specifically, `sum((t - mean(t)) * (x - mean(x))) / sum((t - mean(t))²)`; zero timestamp
  variance yields slope zero.
- **Delta:** the current window's median minus the preceding accepted window's median.
  The first window, and the first window after a rejected window, use delta zero. This is
  not the difference between the first and last sample of the current window.
- **Variance:** population variance of the samples inside the window (`numpy.var`).

### Window acceptance

Let `r = number of valid samples in the partition / partition duration`, and let `L` be the
window length. The expected sample count is `max(2, r * L)`. A window is accepted only if
it has at least two samples and its count is at least 50% of that expected count.

This is a **sample-count coverage heuristic**, not a guarantee of continuous temporal
coverage. The scorer does not apply Overlay's separate five-second gap rule, per-second
median reduction, or smoothing time constants. Rejecting a window resets delta continuity.

## 3. Robust scores and candidate envelopes

### Feature standardization

For each feature, use all its accepted windows in the current partition:

```text
m = median(feature values)
MAD = median(abs(feature values - m))
z = 0.675 * (feature value - m) / MAD
```

If MAD is zero or non-finite, every z-score for that feature becomes zero. Non-finite
computed z-scores also become zero. There is no alternative standard-deviation fallback.

The modality's window score is the weighted sum of **absolute** feature z-scores:

```text
HR score          = 0.5 |z_median| + 0.3 |z_slope| + 0.2 |z_delta|
EDA score         = 0.4 |z_median| + 0.2 |z_slope| + 0.4 |z_variance|
Temperature score = 0.5 |z_slope| + 0.3 |z_delta| + 0.2 |z_median|
```

Both rises and falls can therefore produce high scores. Signed z-scores are retained for
the explanation, although ranking uses their magnitudes.

Temperature channels are scored separately. For each matching window, their available
scores are averaged into one temperature score. A single available channel is sufficient.
The stronger channel supplies that window's explanation. **Unlike Overlay, this algorithm
does not prefer T1 over TH.**

### Qualification and strength

Within each modality, a window qualifies only when:

1. Its score is at least the partition's modality-specific 90th percentile.
2. Its score is **strictly greater than 2.0**.
3. Its mapped strength is positive.

Strength uses the score's empirical percentile rank:

```text
rank = 100 * count(scores <= this score) / number of scores
strength = clip((rank - 90) / 10, 0, 1)
```

Ties use the upper rank (`searchsorted(..., side="right")`), so qualifying windows are not
necessarily exactly 10% of all windows. The absolute score threshold and percentile rank
serve different roles: the former rejects weak deviations, while the latter determines
relative strength within the partition and modality.

Qualifying windows can overlap. Their start/end boundaries split the timeline into constant
segments, and each segment keeps the **maximum** active strength for its modality. Adjacent
segments with equal strength are merged. Overlapping windows are not added together, which
prevents the sliding-window overlap itself from increasing strength.

## 4. Fusion, interval ranking, and selection

Modality envelopes are projected onto one-second grid points. Let `C(t)`, `E(t)`, and `T(t)`
be their strengths, with zero for absent or non-qualifying modalities. Let `N(t)` count the
modalities whose envelopes are greater than zero:

```text
weighted(t) = 0.40 C(t) + 0.40 E(t) + 0.20 T(t)
F(t) = (0.8 + 0.2 N(t)) * weighted(t)
```

One active modality has multiplier 1.0, two have 1.2, and three have 1.4. Missing modality
weights are **not renormalized**. Temperature-only strength 1 produces `F = 0.2`, whereas
HR-only or EDA-only strength 1 produces `F = 0.4`.

The candidate interval set is the distinct `(start, end)` pairs from the modality envelope
segments. It is not a search over every possible interval and does not simply take the
union of all non-zero fused regions. A candidate retains the contribution of any modality
that overlaps it.

For a candidate, collect its fused grid values and take the largest `ceil(count * 0.25)`
values, with a minimum of one:

```text
interval score = 0.50 * max(F) + 0.30 * mean(top-quarter F values)
```

The coefficients intentionally reflect the current code and sum to **0.80**, not 1.0. No
additional duration bonus is applied. `sample_count` is the number of fused one-second grid
points in the interval, not the number of original physiological samples.

Candidates are sorted by descending score, then earlier start, then shorter duration.
Greedy selection rejects positive overlap; intervals that only touch are compatible.
The partition pass uses limit one. After translating winners back to session time, the
same selection logic returns the global top ten, or the smaller requested limit.

### Worked example

Suppose qualifying envelopes have strength 1 over these intervals in one partition:

- HR: `[0, 4)`
- EDA: `[2, 6)`
- Temperature: `[6, 8)`

Their fused values at seconds 0 through 7 are:

```text
0.40, 0.40, 0.96, 0.96, 0.40, 0.40, 0.20, 0.20
```

For `[0, 4)`, the top quarter contains one value, 0.96:
`score = 0.50 * 0.96 + 0.30 * 0.96 = 0.768`.
The EDA candidate ties at 0.768; the temperature candidate scores 0.160. The earlier HR
candidate wins the partition. Although temperature does not overlap it, no second result
is returned from that partition because the partition limit is one.

## 5. Explanations and timeline display

The headline chooses the modality with the largest `modality weight * envelope peak` within
the selected interval. Within that modality's stored source window, it chooses the feature
with the largest `feature weight * abs(z)`.

Median features become “higher/lower”; slopes become “rising/falling fast”; deltas become
“jumped up/dropped”; variance becomes “more jumpy/steadier”. The numeric explanation uses
original units. A slope is multiplied by the source window's length; the variance comparison
uses the ratio of standard deviations, `sqrt(variance / usual variance)`.

For HR slope/delta headlines, a change exceeding 30 bpm adds **possible sensor glitch**.
This is an explanatory flag, not a filter or a score penalty.

### Explanation-card baselines

These summaries explain the chosen moment and do not feed back into ranking:

- **HR and temperature:** compare the source window's median with the raw samples in its
  five-minute block. The grey band is block P25–P75. Arrow strength uses
  `(window median - block median) / max((P75 - P25) / 1.349, 1e-9)`, with single arrows at
  ±1 and double arrows at ±2.
- **EDA:** when at least two preceding samples are available, use the **30 seconds before
  the source window** as the reference. Compare its median with the window's peak rise or
  fall. Changes of at least 0.05 µS get an arrow; at least 0.2 µS get a double arrow.
  If both directions qualify, an equal-or-larger rise takes precedence. The grey band is
  the preceding samples' P25–P75. Without enough preceding samples, fall back to the
  block-level comparison.

The EDA card's preceding-data reference can cross a five-minute boundary; the ranking
baseline cannot. These EDA thresholds are display heuristics, not a separate validated
skin-response detector. The headline's feature baseline and the card's raw-signal baseline
can also differ, so their descriptions need not use identical comparison numbers.

HR and EDA are shown as context when enough data exists. Temperature is included only when
it contributed to the suggestion. Contributing signals are flagged and listed first.

The timeline uses an ivory marker strip and translucent wash. Opacity derives from the
interval score on a fixed UI scale:

```text
visual strength = clip((score - 0.15) / (1.0 - 0.15), 0, 1)
Mild: below 1/3; Clear: below 2/3; Strong: otherwise
```

These labels express algorithmic strength, not confidence or a clinical severity. A common
color scale does not make independently normalized blocks physiologically equivalent.
**Show suggestions** toggles the markers, wash, and legend independently of teal audition
markers and Overlay audio.

The scored interval (`windows[].start_seconds/end_seconds`) and the triggering feature
window (`headline.window`) can differ. When a headline exists, clicking a suggestion selects
its **headline window**, which is also used by the explanation card; otherwise it selects
the scored interval. This explains why a selected interval can be wider than its marker.

## 6. API and compatibility fields

```http
GET /api/sessions/{session_id}/biosignal-clusters?clusters=10
```

The HTTP `clusters` parameter accepts 2–10 and limits the total number of suggestions; it
does not request a number of learned clusters. The Python function also accepts zero/one
and clamps its limit to 0–10. The optional HTTP `window_seconds` parameter accepts 5–120
but is ignored for scoring to preserve old clients; it does not change the fixed windows.

The response contains:

| Field | Meaning |
| --- | --- |
| `algorithm_version` | `physiological-pattern-score-v4` |
| `window_seconds` | `1.0`: the fusion grid step, not a modality window length |
| `windows` | Chronologically ordered scored intervals with a `cluster` reference |
| `clusters[].id` | Response-local identifier; assigned in ascending score order, not a stable pattern class |
| `clusters[].label` | `Suggestion #rank · contributing modalities`; rank 1 is strongest |
| `clusters[].size` | Number of one-second grid points in the scored interval |
| `clusters[].centroid` | Legacy container for interval score, fused peak, and modality peaks |
| `clusters[].headline` | Explanation, trigger window, block, grey band, and signal-card summaries |

`movement_peak` remains zero for compatibility. Empty results keep the same top-level
contract with empty `windows` and `clusters` arrays. Results use session-relative seconds
and are recomputed from recorded signals; cluster IDs should not be treated as persistent
physiological categories.

## Limitations and regression coverage

- A session or partial partition shorter than a modality's window cannot use that modality.
  A single accepted window has zero MAD for every feature and cannot establish a deviation.
- Zero MAD suppresses a feature even if a rare outlier exists among mostly identical values.
  Enough windows alone do not guarantee a suggestion.
- Count-based coverage does not reject every long gap or compensate for irregular sampling.
  Temperature has no plausibility bounds here; large HR changes are flagged only in the
  explanation. Sensor artefacts can still create suggestions.
- Loading requests `pixels=1_000_000` per channel. The store normally returns raw samples;
  above its four-million-sample cutoff it returns min/max buckets. The current scorer uses
  the second point column, which is the minimum in that mode, rather than reconstructing
  raw samples. Very large recordings therefore need care when interpreting results.
- Five-minute boundaries, fixed modality windows, per-block selection, percentile ties, and
  fixed missing-modality weights influence what appears. A strong second event in the same
  block can be omitted, and scores are not calibrated probabilities or absolute measures.
- Suggestions should be checked against the recording and signal traces. They are aids for
  finding moments, not evidence of emotion, intent, causality, or sensor correctness.

Existing regression tests cover actual-timestamp features, missing-window delta reset,
zero MAD, partition boundaries, percentile strength, maximum overlap envelopes, fusion,
interval scores and ties, the one-per-partition/global-ten limits, temperature-only input,
explanation provenance, deterministic results, and exclusion of movement channels. To run
those checks during algorithm development:

```sh
python -m pytest tests/test_biosignal_clustering.py
```
