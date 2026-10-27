/** Own synchronized preview stems and cancellable requests for the selected audition. */
const overlayPreview = {
  epoch: 0, sessionId: null, audition: null, record: null, buffer: null,
  source: null, sources: [], buffers: null, active: false,
  anchor: null, gain: null, drumGain: null, reverb: null, timer: null, poll: null,
  controller: new AbortController(), waiting: false, loading: false,
};
const auditionTimeline = { items: [], visible: true };

/** Draw audition intervals on the ruler and traces. */
function drawAuditions() {
  const strip = $("audition-strip"), wash = $("audition-overlay");
  strip.replaceChildren(); wash.replaceChildren();
  $("audition-toggle-button").hidden = auditionTimeline.items.length === 0;
  strip.hidden = !auditionTimeline.visible || !auditionTimeline.items.length;
  if (!auditionTimeline.visible) return;
  const timeline = state.timeline;
  const span = timeline ? timeline.end - timeline.start : 0;
  const box = span > 0 ? timelineContentBox() : null;
  for (const [index, audition] of auditionTimeline.items.entries()) {
    const selected = overlayPreview.audition?.id === audition.id;
    const title = `Audition ${index + 1} · ${formatTime(audition.start_time)}–${formatTime(audition.end_time)} · ${audition.duration.toFixed(1)}s${audition.reason ? " · " + audition.reason : ""}`;
    if (!box) continue;
    const start = Math.max(audition.start_time, timeline.start), end = Math.min(audition.end_time, timeline.end);
    if (end <= start) continue;
    const marker = document.createElement("button"); marker.type = "button";
    marker.className = `audition-segment${selected ? " selected" : ""}${audition.eligible ? "" : " unavailable"}`;
    marker.title = title; marker.setAttribute("aria-label", title); marker.setAttribute("aria-pressed", String(selected));
    marker.style.left = `${(start - timeline.start) / span * 100}%`;
    marker.style.width = `${(end - start) / span * 100}%`;
    marker.onclick = (event) => { event.stopPropagation(); selectAudition(audition); }; strip.append(marker);
    const band = document.createElement("div"); band.className = `audition-band${selected ? " selected" : ""}`;
    band.style.left = `${box.left + (start - timeline.start) / span * box.width}px`;
    band.style.width = `${(end - start) / span * box.width}px`; wash.append(band);
  }
}

/** Detach playback when the selected timeline interval no longer identifies this audition. */
function reconcileAuditionSelection(interval) {
  const audition = overlayPreview.audition;
  if (!audition || (interval?.auditionId === audition.id
      && interval.start === audition.start_time && interval.end === audition.end_time)) return;
  const items = auditionTimeline.items;
  resetOverlayPreview(overlayPreview.sessionId);
  auditionTimeline.items = items;
  drawAuditions();
}
/** Own the shared output graph; the original track bypasses limiting when additions are off. */
const reviewAudio = { context: null, bus: null, limiter: null, video: null, limited: null, impulse: null };

/** Unlock audio on a user gesture before attaching the video to the shared graph. */
async function ensureReviewAudio() {
  if (!reviewAudio.context) reviewAudio.context = new AudioContext();
  const ctx = reviewAudio.context;
  await ctx.resume();
  if (ctx.state !== "running") throw new Error("Browser audio could not be unlocked");
  if (!reviewAudio.bus) {
    reviewAudio.bus = ctx.createGain();
    reviewAudio.limiter = ctx.createDynamicsCompressor();
    reviewAudio.limiter.threshold.value = -6;
    reviewAudio.limiter.knee.value = 0;
    reviewAudio.limiter.ratio.value = 20;
    reviewAudio.limiter.attack.value = 0.003;
    reviewAudio.limiter.release.value = 0.15;
    reviewAudio.video = ctx.createMediaElementSource($("session-video"));
    reviewAudio.video.connect(reviewAudio.bus);
    const ceiling = ctx.createWaveShaper();
    ceiling.curve = Float32Array.from({ length: 4097 }, (_, index) => 0.95 * (index / 2048 - 1));
    reviewAudio.limiter.connect(ceiling).connect(ctx.destination);
    updateReviewOutput();
  }
  return ctx;
}

/** Route all three sounds together, preserving unity gain for unmodified original playback. */
function updateReviewOutput() {
  if (!reviewAudio.bus) return;
  const limited = heartbeat.enabled || overlayPreview.active;
  if (limited === reviewAudio.limited) return;
  reviewAudio.bus.disconnect();
  reviewAudio.bus.connect(limited ? reviewAudio.limiter : reviewAudio.context.destination);
  reviewAudio.limited = limited;
}

/** Fade and discard the old wet graph so seeks never reuse a previous reverb tail. */
function clearOverlaySpace() {
  const graph = overlayPreview.reverb;
  if (!graph) return;
  reviewAudio.video.disconnect(graph.input);
  overlayPreview.gain?.disconnect(graph.input);
  overlayPreview.drumGain?.disconnect(graph.input);
  graph.send.disconnect();
  graph.input.disconnect();
  graph.output.gain.setTargetAtTime(0, reviewAudio.context.currentTime, 0.005);
  setTimeout(() => { graph.convolver.disconnect(); graph.output.disconnect(); }, 30);
  overlayPreview.reverb = null;
}

/** Make a damped stereo room with explicit unit-energy impulses and early reflections. */
function createOverlaySpace() {
  const ctx = reviewAudio.context;
  const spatial = overlayPreview.record?.spatial;
  if (!spatial?.values?.length) return;
  const input = ctx.createGain();
  const send = ctx.createGain();
  const convolver = ctx.createConvolver();
  const output = ctx.createGain();
  const seconds = spatial.reverb_seconds || 2.4;
  const impulseKey = `${ctx.sampleRate}:${seconds}:room-v3`;
  let impulse = reviewAudio.impulse;
  if (!impulse || reviewAudio.impulseKey !== impulseKey) {
    impulse = ctx.createBuffer(2, Math.round(ctx.sampleRate * seconds), ctx.sampleRate);
    let seed = 0x13579bdf;
    for (let channel = 0; channel < 2; channel++) {
      const samples = impulse.getChannelData(channel);
      let low = 0, bass = 0, energy = 0;
      const lowAlpha = 1 - Math.exp(-2 * Math.PI * 4000 / ctx.sampleRate);
      const bassAlpha = 1 - Math.exp(-2 * Math.PI * 150 / ctx.sampleRate);
      for (let i = 0; i < samples.length; i++) {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        const t = i / ctx.sampleRate;
        low += lowAlpha * (seed / 2147483648 - 1 - low);
        bass += bassAlpha * (low - bass);
        samples[i] = t < 0.025 ? 0 : (low - bass) * Math.exp(-4.5 * (t - 0.025) / seconds);
        energy += samples[i] * samples[i];
      }
      const diffuseScale = 1 / Math.sqrt(energy || 1);
      for (let i = 0; i < samples.length; i++) samples[i] *= diffuseScale;
      for (const [delay, gain] of [[0.04, .35], [0.065, .24], [0.095, .16]]) {
        const index = Math.round((delay + channel * .003) * ctx.sampleRate);
        if (index < samples.length) samples[index] += gain;
      }
      energy = samples.reduce((sum, sample) => sum + sample * sample, 0);
      const scale = 1 / Math.sqrt(energy || 1);
      for (let i = 0; i < samples.length; i++) samples[i] *= scale;
    }
    reviewAudio.impulse = impulse;
    reviewAudio.impulseKey = impulseKey;
  }
  convolver.normalize = false;
  convolver.buffer = impulse;
  send.gain.value = 0;
  input.connect(send).connect(convolver).connect(output).connect(reviewAudio.limiter);
  // A dedicated send can be disconnected without disturbing the video's dry path.
  reviewAudio.video.connect(input);
  overlayPreview.gain.connect(input);
  if (overlayPreview.drumGain) overlayPreview.drumGain.connect(input);
  overlayPreview.reverb = { input, send, convolver, output };
}

/** Evaluate EDA against session playback time, including changed playback rates. */
function updateOverlaySpace(offset) {
  const spatial = overlayPreview.record?.spatial;
  if (!spatial?.values?.length) return;
  if (!overlayPreview.reverb) createOverlaySpace();
  const position = Math.max(0, Math.min(spatial.values.length - 1,
    offset / spatial.duration_seconds * (spatial.values.length - 1)));
  const left = Math.floor(position);
  const value = spatial.values[left] + (spatial.values[Math.min(left + 1, spatial.values.length - 1)]
    - spatial.values[left]) * (position - left);
  const wet = Math.min(0.6, spatial.max_wet ?? 0.6) * Number($("overlay-space").value)
    * (0.4 + 0.6 * Math.max(0, Math.min(1, value)));
  overlayPreview.reverb.send.gain.setTargetAtTime(wet, reviewAudio.context.currentTime, 0.04);
}

/** Fade one voice, including a voice still waiting for its delayed start. */
function stopOverlayVoice(source) {
  const ctx = reviewAudio.context;
  if (source.fade) {
    source.fade.gain.setTargetAtTime(0, ctx.currentTime, 0.005);
    source.stop(ctx.currentTime + 0.025);
    setTimeout(() => { source.disconnect(); source.fade.disconnect(); }, 30);
  } else {
    source.stop();
    source.disconnect();
  }
}

/** Stop both one-shot sources and clear their effect history. */
function stopOverlaySource() {
  for (const source of overlayPreview.sources) stopOverlayVoice(source);
  overlayPreview.sources = [];
  overlayPreview.source = null;
  overlayPreview.anchor = null;
  overlayPreview.active = false;
  clearOverlaySpace();
  updateReviewOutput();
}

/** Cancel selected-target requests and release decoded audio without affecting backend tasks. */
function resetOverlayPreview(sessionId = null) {
  overlayPreview.epoch++;
  overlayPreview.controller.abort();
  overlayPreview.controller = new AbortController();
  clearTimeout(overlayPreview.poll);
  clearInterval(overlayPreview.timer);
  stopOverlaySource();
  Object.assign(overlayPreview, { sessionId, audition: null, record: null, buffer: null, buffers: null,
    timer: null, poll: null, loading: false, waiting: false });
  $("overlay-mix").checked = false;
  $("overlay-panel").hidden = true;
  auditionTimeline.items = [];
  $("audition-strip").replaceChildren();
  $("audition-overlay").replaceChildren();
  $("audition-strip").hidden = true;
  $("audition-toggle-button").hidden = true;
  updateReviewOutput();
}

/** Read API metadata using the selection's abort signal. */
async function overlayRequest(url, options = {}) {
  const response = await fetch(url, { ...options, signal: overlayPreview.controller.signal });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Overlay request failed");
  return body;
}

/** List server-detected auditions without automatically generating any sound. */
async function loadAuditions(session) {
  const epoch = overlayPreview.epoch;
  if (session.status === "recording") {
    $("audition-status").textContent = "Stop recording to review auditions.";
    return;
  }
  $("audition-status").textContent = "Checking auditions and recorded audio…";
  try {
    const result = await overlayRequest(`/api/sessions/${session.id}/auditions`);
    if (epoch !== overlayPreview.epoch) return;
    $("audition-status").textContent = result.auditions.length
      ? "Click an audition interval on the timeline (8 seconds–5 minutes)."
      : "No auditions of at least 8 seconds found.";
    auditionTimeline.items = result.auditions;
    drawAuditions();
    if (result.diagnostics.length) {
      $("audition-status").textContent += ` ${result.diagnostics.join("; ")}`;
    }
  } catch (error) {
    if (epoch === overlayPreview.epoch && error.name !== "AbortError") {
      $("audition-status").textContent = error.message;
    }
  }
}

/** Select the audition in the timeline/bookmark composer and load its cached preview. */
async function selectAudition(audition) {
  overlayPreview.epoch++;
  overlayPreview.controller.abort();
  overlayPreview.controller = new AbortController();
  clearTimeout(overlayPreview.poll);
  clearInterval(overlayPreview.timer);
  stopOverlaySource();
  overlayPreview.audition = audition;
  overlayPreview.buffer = null;
  overlayPreview.buffers = null;
  overlayPreview.waiting = false;
  overlayPreview.loading = false;
  overlayPreview.record = audition.overlay;
  $("overlay-drum-offset").value = String(audition.drumOffsetMs ?? 180);
  updateDrumTimingLabel();
  $("overlay-mix").checked = false;
  updateReviewOutput();
  $("session-video").pause();
  selectInterval({
    start: audition.start_time,
    end: audition.end_time,
    sourceType: "manual",
    auditionId: audition.id,
    label: "Audition segment",
    patternContext: null,
  });
  drawAuditions();
  $("overlay-panel").hidden = false;
  $("overlay-heading").textContent = `${formatTime(audition.start_time)}–${formatTime(audition.end_time)} · ${audition.bpm || 100} BPM${audition.bpm_source === "default" ? " (default)" : ""}`;
  const tempoNote = audition.bpm_source === "default"
    ? ` Using default ${audition.bpm} BPM (no recorded tempo).` : "";
  renderOverlayControls(audition.reason || `${audition.needs_regeneration ? "Updated sound available. Generate the new overlay." : "Ready to generate physiological overlay."}${tempoNote}`);
  if (audition.overlay) await loadOverlayBuffer(audition.overlay, overlayPreview.epoch);
}

/** Update controls from authoritative eligibility and the current local request state. */
function renderOverlayControls(message) {
  const ready = !!overlayPreview.buffer;
  const busy = overlayPreview.loading;
  $("overlay-status").textContent = message;
  $("overlay-generate").disabled = !overlayPreview.audition?.eligible || busy;
  $("overlay-regenerate").disabled = !overlayPreview.audition?.eligible || busy || !ready;
  $("overlay-mix").disabled = !ready;
  const values = overlayPreview.record?.spatial?.values || [];
  $("overlay-space").disabled = !ready || !values.length;
  $("overlay-space-status").textContent = !ready ? "EDA → original + overlay"
    : !values.length ? "EDA unavailable · space off"
    : Math.max(...values) - Math.min(...values) < 0.02 ? "EDA steady · consistent space" : "EDA → original + overlay";
}

/** Generate explicitly and poll only while the original selection remains active. */
async function requestOverlay(regenerate) {
  const epoch = overlayPreview.epoch;
  const sessionId = overlayPreview.sessionId;
  overlayPreview.loading = true;
  renderOverlayControls("Generating drums and background…");
  try {
    const record = await overlayRequest(`/api/sessions/${sessionId}/overlays`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audition_id: overlayPreview.audition.id, preset: "physiological", regenerate }),
    });
    if (epoch !== overlayPreview.epoch) return;
    await pollOverlay(record, epoch);
  } catch (error) {
    if (epoch !== overlayPreview.epoch || error.name === "AbortError") return;
    overlayPreview.loading = false;
    renderOverlayControls(error.message);
  }
}

/** Follow a generation task without attaching its result to a different audition. */
async function pollOverlay(record, epoch) {
  if (epoch !== overlayPreview.epoch) return;
  if (record.status === "failed") {
    overlayPreview.loading = false;
    renderOverlayControls(`Generation failed: ${record.error}. You can retry.`);
    return;
  }
  if (record.status === "ready") {
    await loadOverlayBuffer(record, epoch);
    return;
  }
  renderOverlayControls(record.status === "pending" ? "Queued…" : "Generating drums and background…");
  overlayPreview.poll = setTimeout(async () => {
    try {
      const next = await overlayRequest(`/api/sessions/${record.session_id}/overlays/${record.overlay_id}`);
      await pollOverlay(next, epoch);
    } catch (error) {
      if (epoch === overlayPreview.epoch && error.name !== "AbortError") {
        overlayPreview.loading = false;
        renderOverlayControls(error.message);
      }
    }
  }, 700);
}

/** Decode just the selected WAV, retaining the previous preview if replacement fails. */
async function loadOverlayBuffer(record, epoch) {
  try {
    const started = performance.now();
    if (!reviewAudio.context) reviewAudio.context = new AudioContext();
    const entries = await Promise.all((record.stems || ["ambient"]).map(async (stem) => {
      const response = await fetch(`/api/sessions/${record.session_id}/overlays/${record.overlay_id}/audio?stem=${stem}`,
        { signal: overlayPreview.controller.signal });
      if (!response.ok) throw new Error("Overlay audio could not be loaded");
      const buffer = await reviewAudio.context.decodeAudioData(await response.arrayBuffer());
      return [stem, buffer];
    }));
    if (epoch !== overlayPreview.epoch) return;
    stopOverlaySource();
    overlayPreview.buffers = Object.fromEntries(entries);
    overlayPreview.buffer = overlayPreview.buffers.ambient;
    console.info("Overlay load/decode", { seconds: (performance.now() - started) / 1000,
      generation: record.timings });
    overlayPreview.record = record;
    updateDrumTimingLabel();
    overlayPreview.audition.overlay = record;
    overlayPreview.loading = false;
    const missing = [];
    if (record.bpm_source === "default") missing.push(`No recorded tempo: using default ${record.bpm} BPM`);
    if (!record.channels?.HR) missing.push("HR unavailable: fixed gentle rhythm");
    if (!record.channels?.EA) missing.push("EDA unavailable: spatial effect off");
    if (!record.temperature_source) missing.push("Temperature unavailable: fixed background");
    renderOverlayControls(`Ready. Enable Original + Overlay to compare.${missing.length ? " " + missing.join("; ") + "." : ""}`);
    syncOverlay();
  } catch (error) {
    if (epoch !== overlayPreview.epoch || error.name === "AbortError") return;
    overlayPreview.loading = false;
    renderOverlayControls(error.message);
  }
}

/** Positive millisecond offset delays drums; preserve background and EDA's session clock. */
function drumDelaySeconds() {
  return Number($("overlay-drum-offset").value) / 1000;
}

/** Show the phase in milliseconds and beats without requiring another render. */
function updateDrumTimingLabel() {
  const milliseconds = Number($("overlay-drum-offset").value);
  const bpm = Number(overlayPreview.record?.bpm || overlayPreview.audition?.bpm || 100);
  const beats = milliseconds * bpm / 60000;
  $("overlay-drum-offset-value").textContent = `${milliseconds >= 0 ? "+" : ""}${milliseconds} ms · ${beats >= 0 ? "+" : ""}${beats.toFixed(2)} beat`;
}

/** Schedule an individual stem at its phase-correct offset, clipping at the audition end. */
function startOverlayStem(stem, buffer, offset, rate, clock) {
  const context = reviewAudio.context;
  const audition = overlayPreview.audition;
  const position = offset - (stem === "drums" ? drumDelaySeconds() : 0);
  const wait = Math.max(0, -position);
  const readOffset = Math.max(0, position);
  const bufferDuration = buffer.duration ?? (audition.end_time - audition.start_time);
  const duration = Math.min(bufferDuration - readOffset,
    audition.end_time - audition.start_time - offset - wait);
  if (duration <= 0) return;
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.stem = stem;
  source.playbackRate.value = rate;
  const fade = context.createGain();
  fade.gain.value = 0;
  fade.gain.setTargetAtTime(1, clock + wait / rate, 0.005);
  fade.gain.setTargetAtTime(0, Math.max(clock + wait / rate,
    clock + (wait + duration) / rate - 0.015), 0.003);
  source.fade = fade;
  source.connect(fade).connect(stem === "drums" ? overlayPreview.drumGain : overlayPreview.gain);
  source.start(clock + wait / rate, readOffset, duration);
  overlayPreview.sources.push(source);
}

/** Apply phase edits only to drums; background and reverb continue uninterrupted. */
function retimeOverlayDrums() {
  updateDrumTimingLabel();
  if (overlayPreview.audition) {
    overlayPreview.audition.drumOffsetMs = Number($("overlay-drum-offset").value);
  }
  if (!overlayPreview.active || !overlayPreview.buffers?.drums) return;
  const video = $("session-video");
  const offset = video.currentTime + Number(video.dataset.offset || 0)
    - overlayPreview.audition.start_time;
  const retained = [];
  for (const source of overlayPreview.sources) {
    if (source.stem === "drums") stopOverlayVoice(source);
    else retained.push(source);
  }
  overlayPreview.sources = retained;
  startOverlayStem("drums", overlayPreview.buffers.drums, offset, video.playbackRate,
    reviewAudio.context.currentTime);
}

/** Keep the independent WAV aligned to video time, including rate changes and buffering. */
function syncOverlay(force = false) {
  const video = $("session-video");
  const audition = overlayPreview.audition;
  const context = reviewAudio.context;
  const time = video.currentTime + Number(video.dataset.offset || 0);
  if (!$("overlay-mix").checked || !overlayPreview.buffer || !audition || !context
      || video.paused || video.ended || video.seeking || video.readyState < 3
      || overlayPreview.waiting || time < audition.start_time || time >= audition.end_time) {
    stopOverlaySource();
    return;
  }
  if (context.state !== "running") return;
  const offset = time - audition.start_time;
  const rate = video.playbackRate;
  const anchor = overlayPreview.anchor;
  const drift = anchor ? Math.abs(anchor.offset + (context.currentTime - anchor.clock) * anchor.rate - offset) : Infinity;
  if (!force && overlayPreview.source && drift <= 0.1 && anchor.rate === rate) {
    updateOverlaySpace(offset);
    return;
  }
  stopOverlaySource();
  try {
    if (!overlayPreview.gain) {
      overlayPreview.gain = context.createGain();
      overlayPreview.gain.connect(reviewAudio.bus);
    }
    if (!overlayPreview.drumGain) {
      overlayPreview.drumGain = context.createGain();
      overlayPreview.drumGain.connect(reviewAudio.bus);
    }
    overlayPreview.gain.gain.value = Number($("overlay-gain").value);
    overlayPreview.drumGain.gain.value = Number($("overlay-drums").value);
    const buffers = overlayPreview.buffers || { ambient: overlayPreview.buffer };
    const clock = context.currentTime;
    for (const [stem, buffer] of Object.entries(buffers)) {
      startOverlayStem(stem, buffer, offset, rate, clock);
    }
    overlayPreview.source = overlayPreview.sources[0];
    overlayPreview.active = true;
    updateReviewOutput();
    updateOverlaySpace(offset);
    overlayPreview.anchor = { offset, clock, rate };
  } catch (error) { failOverlayPlayback(error.message); }
}

/** Return to original playback after an output failure without hiding the failure reason. */
function failOverlayPlayback(message) {
  $("overlay-mix").checked = false;
  clearInterval(overlayPreview.timer);
  overlayPreview.timer = null;
  stopOverlaySource();
  if (heartbeat.enabled) toggleHeartbeat(false);
  updateReviewOutput();
  renderOverlayControls(`Original only: ${message}`);
}

$("overlay-generate").onclick = () => requestOverlay(false);
$("overlay-regenerate").onclick = () => requestOverlay(true);
$("overlay-mix").onchange = async () => {
  const epoch = overlayPreview.epoch;
  if (!$("overlay-mix").checked) {
    clearInterval(overlayPreview.timer);
    overlayPreview.timer = null;
    stopOverlaySource();
    updateReviewOutput();
    return;
  }
  try {
    if ($("session-video").error) throw new Error("The browser could not play the recorded audio/video");
    if (heartbeat.enabled) await toggleHeartbeat(false);
    await ensureReviewAudio();
    if (epoch !== overlayPreview.epoch) return;
    updateReviewOutput();
    clearInterval(overlayPreview.timer);
    overlayPreview.timer = setInterval(syncOverlay, 50);
    syncOverlay(true);
  } catch (error) { if (epoch === overlayPreview.epoch) failOverlayPlayback(error.message); }
};
$("overlay-drum-offset").oninput = retimeOverlayDrums;
$("audition-toggle-button").onclick = () => {
  auditionTimeline.visible = !auditionTimeline.visible;
  $("audition-toggle-button").classList.toggle("active", auditionTimeline.visible);
  $("audition-toggle-button").setAttribute("aria-pressed", String(auditionTimeline.visible));
  drawAuditions();
};
$("overlay-drums").oninput = () => {
  if (overlayPreview.drumGain) overlayPreview.drumGain.gain.setTargetAtTime(
    Number($("overlay-drums").value), reviewAudio.context.currentTime, 0.02);
};
$("overlay-space").oninput = () => { if (overlayPreview.active) syncOverlay(); };
$("overlay-gain").oninput = () => {
  if (overlayPreview.gain) overlayPreview.gain.gain.setTargetAtTime(
    Number($("overlay-gain").value), reviewAudio.context.currentTime, 0.02);
};
for (const name of ["play", "playing", "pause", "seeking", "seeked", "ended", "ratechange", "waiting", "stalled", "error"]) {
  $("session-video").addEventListener(name, () => {
    if (name === "play" && reviewAudio.context?.state === "suspended") {
      reviewAudio.context.resume().then(() => syncOverlay(true)).catch(
        () => failOverlayPlayback("Browser audio could not resume"));
    }
    if (name === "waiting" || (name === "stalled" && $("session-video").readyState < 3)) {
      overlayPreview.waiting = true;
    }
    if (["playing", "seeked"].includes(name)) overlayPreview.waiting = false;
    if (name === "error" && $("overlay-mix").checked) failOverlayPlayback("Recorded audio/video playback failed");
    syncOverlay(true);
  });
}
