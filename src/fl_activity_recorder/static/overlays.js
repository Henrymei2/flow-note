/** Own a single preview buffer and cancellable requests for the selected audition. */
const overlayPreview = {
  epoch: 0, sessionId: null, audition: null, record: null, buffer: null,
  source: null, anchor: null, gain: null, timer: null, poll: null,
  controller: new AbortController(), waiting: false, loading: false,
};
/** Own the shared output graph; the original track bypasses limiting when additions are off. */
const reviewAudio = { context: null, bus: null, limiter: null, video: null, limited: null };

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
  const limited = heartbeat.enabled || $("overlay-mix").checked;
  if (limited === reviewAudio.limited) return;
  reviewAudio.bus.disconnect();
  reviewAudio.bus.connect(limited ? reviewAudio.limiter : reviewAudio.context.destination);
  reviewAudio.limited = limited;
}

/** Stop the one-shot buffer source; resumed playback always gets a fresh source. */
function stopOverlaySource() {
  if (overlayPreview.source) {
    overlayPreview.source.stop();
    overlayPreview.source.disconnect();
  }
  overlayPreview.source = null;
  overlayPreview.anchor = null;
}

/** Cancel selected-target requests and release decoded audio without affecting backend tasks. */
function resetOverlayPreview(sessionId = null) {
  overlayPreview.epoch++;
  overlayPreview.controller.abort();
  overlayPreview.controller = new AbortController();
  clearTimeout(overlayPreview.poll);
  clearInterval(overlayPreview.timer);
  stopOverlaySource();
  Object.assign(overlayPreview, { sessionId, audition: null, record: null, buffer: null,
    timer: null, poll: null, loading: false, waiting: false });
  $("overlay-mix").checked = false;
  $("overlay-panel").hidden = true;
  $("audition-list").replaceChildren();
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
      ? "Choose one continuous playback interval (8 seconds–5 minutes)."
      : "No auditions of at least 8 seconds found.";
    for (const [index, audition] of result.auditions.entries()) {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-pressed", "false");
      button.textContent = `${index + 1} · ${formatTime(audition.start_time)}–${formatTime(audition.end_time)} · ${audition.duration.toFixed(1)}s${audition.complete ? "" : " · Incomplete"}`;
      button.title = audition.reason || "Select audition";
      button.onclick = () => selectAudition(audition, button);
      $("audition-list").append(button);
    }
    if (result.diagnostics.length) {
      $("audition-status").textContent += ` ${result.diagnostics.join("; ")}`;
    }
  } catch (error) {
    if (epoch === overlayPreview.epoch && error.name !== "AbortError") {
      $("audition-status").textContent = error.message;
    }
  }
}

/** Select an interval, seek its original video, and load only an existing cached result. */
async function selectAudition(audition, button) {
  overlayPreview.epoch++;
  overlayPreview.controller.abort();
  overlayPreview.controller = new AbortController();
  clearTimeout(overlayPreview.poll);
  clearInterval(overlayPreview.timer);
  stopOverlaySource();
  overlayPreview.audition = audition;
  overlayPreview.buffer = null;
  overlayPreview.record = audition.overlay;
  overlayPreview.loading = false;
  $("overlay-mix").checked = false;
  updateReviewOutput();
  $("session-video").pause();
  seekTimeline(audition.start_time);
  for (const item of $("audition-list").children) item.setAttribute("aria-pressed", String(item === button));
  $("overlay-panel").hidden = false;
  renderOverlayControls(audition.reason || "Ready to generate Ambient Texture.");
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
}

/** Generate explicitly and poll only while the original selection remains active. */
async function requestOverlay(regenerate) {
  const epoch = overlayPreview.epoch;
  const sessionId = overlayPreview.sessionId;
  overlayPreview.loading = true;
  renderOverlayControls("Generating Ambient Texture…");
  try {
    const record = await overlayRequest(`/api/sessions/${sessionId}/overlays`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audition_id: overlayPreview.audition.id, preset: "ambient", regenerate }),
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
  renderOverlayControls(record.status === "pending" ? "Queued…" : "Generating Ambient Texture…");
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
    const response = await fetch(`/api/sessions/${record.session_id}/overlays/${record.overlay_id}/audio`,
      { signal: overlayPreview.controller.signal });
    if (!response.ok) throw new Error("Overlay audio could not be loaded");
    // Decoding does not require routing the original media or unlocking output.
    if (!reviewAudio.context) reviewAudio.context = new AudioContext();
    const buffer = await reviewAudio.context.decodeAudioData(await response.arrayBuffer());
    if (epoch !== overlayPreview.epoch) return;
    stopOverlaySource();
    overlayPreview.buffer = buffer;
    overlayPreview.record = record;
    overlayPreview.audition.overlay = record;
    overlayPreview.loading = false;
    renderOverlayControls("Ambient ready. Enable Original + Overlay to compare.");
    syncOverlay();
  } catch (error) {
    if (epoch !== overlayPreview.epoch || error.name === "AbortError") return;
    overlayPreview.loading = false;
    renderOverlayControls(error.message);
  }
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
  if (!force && overlayPreview.source && drift <= 0.1 && anchor.rate === rate) return;
  stopOverlaySource();
  try {
    if (!overlayPreview.gain) {
      overlayPreview.gain = context.createGain();
      overlayPreview.gain.connect(reviewAudio.bus);
    }
    overlayPreview.gain.gain.value = Number($("overlay-gain").value);
    const source = context.createBufferSource();
    source.buffer = overlayPreview.buffer;
    source.playbackRate.value = rate;
    source.connect(overlayPreview.gain);
    source.start(0, offset);
    overlayPreview.source = source;
    overlayPreview.anchor = { offset, clock: context.currentTime, rate };
  } catch (error) { failOverlayPlayback(error.message); }
}

/** Return to original playback after an output failure without hiding the failure reason. */
function failOverlayPlayback(message) {
  $("overlay-mix").checked = false;
  stopOverlaySource();
  if (heartbeat.enabled) toggleHeartbeat(false);
  updateReviewOutput();
  renderOverlayControls(`Original only: ${message}`);
}

$("overlay-generate").onclick = () => requestOverlay(false);
$("overlay-regenerate").onclick = () => requestOverlay(true);
$("overlay-mix").onchange = async () => {
  const epoch = overlayPreview.epoch;
  if (!$("overlay-mix").checked) { stopOverlaySource(); updateReviewOutput(); return; }
  try {
    if ($("session-video").error) throw new Error("The browser could not play the recorded audio/video");
    await ensureReviewAudio();
    if (epoch !== overlayPreview.epoch) return;
    updateReviewOutput();
    clearInterval(overlayPreview.timer);
    overlayPreview.timer = setInterval(syncOverlay, 50);
    syncOverlay(true);
  } catch (error) { if (epoch === overlayPreview.epoch) failOverlayPlayback(error.message); }
};
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
