const state = { health: null, sharing: null, sessions: [], cloudSessions: [], selected: null, events: [], detail: null, capture: null, currentTime: 0, timeline: null, liveBuffers: new Map(), liveDescriptors: [], clusters: [], clusterLegend: [], clustersVisible: true, patternFilter: null };
const $ = (id) => document.getElementById(id);

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail; throw new Error(detail?.message || detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const element = $("toast"); element.textContent = message; element.classList.add("visible");
  setTimeout(() => element.classList.remove("visible"), 3500);
}

function renderHealth() {
  const health = state.health || {}; const flReady = Boolean(health.connected && health.compatible); const emotibitReady = Boolean(health.emotibit?.connected); const ready = flReady && emotibitReady;
  $("connection").classList.toggle("connected", ready);
  $("connection-label").textContent = !flReady ? "FL Studio disconnected" : !emotibitReady ? "Waiting for EmotiBit LSL" : `FL Studio + EmotiBit connected · ${health.clock_mode}`;
  const active = health.active_session_id; $("record-button").disabled = active ? false : !ready;
  $("record-button").classList.toggle("recording", Boolean(active)); $("record-label").textContent = active ? "Stop recording" : "Start recording";
  $("record-help").textContent = active ? "FL events, video, and live physiology are recording." : ready ? "Chrome will ask you to select the FL Studio window." : "Connect FL Studio and enable fresh EmotiBit LSL data.";
  const silence = health.emotibit?.silence_seconds; $("emotibit-hint").textContent = emotibitReady ? `${health.emotibit.streams.length} live physiological tracks · latest sample ${silence == null ? "now" : `${silence.toFixed(1)}s ago`}` : "Enable LSL Output in EmotiBit Oscilloscope; stream names must begin with EmotiBit.";
}

function formatDate(value) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : ""; }
function formatTime(seconds) { const value = Math.max(0, Number(seconds) || 0); return `${String(Math.floor(value / 60)).padStart(2, "0")}:${(value % 60).toFixed(3).padStart(6, "0")}`; }
function formatBytes(bytes) { const value = Math.max(0, Number(bytes) || 0); if (value < 1024) return `${value} B`; const units = ["KB", "MB", "GB", "TB"]; let amount = value / 1024, index = 0; while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; } return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${units[index]}`; }
function sessionDuration(session) { return Math.max(0, (session.timeline_end_lsl || session.timeline_zero_lsl || 0) - (session.timeline_zero_lsl || 0)); }

function renderSessions() {
  const list = $("session-list"); list.replaceChildren(); $("sessions-empty").hidden = state.sessions.length > 0;
  for (const session of state.sessions) {
    const button = document.createElement("button"); button.className = `session-item ${state.selected?.id === session.id ? "active" : ""}`;
    const title = document.createElement("strong"); title.textContent = session.project_title || "Untitled project";
    const detail = document.createElement("span"); detail.textContent = `${formatDate(session.started_at)} · ${session.event_count} events`;
    button.append(title, detail); button.addEventListener("click", () => selectSession(session)); list.append(button);
  }
}

function renderCloudSessions(message = "") {
  const list = $("cloud-session-list"); const empty = $("cloud-sessions-empty"); list.replaceChildren(); empty.hidden = state.cloudSessions.length > 0; empty.textContent = message || (state.sharing?.configured ? "No sessions have been uploaded yet." : "Cloud sharing is not configured on this device.");
  for (const session of state.cloudSessions) {
    const link = document.createElement("a"); link.className = "session-item cloud"; link.href = `/api/cloud-sessions/download?key=${encodeURIComponent(session.archive_key)}`;
    const title = document.createElement("strong"); title.textContent = session.project_title || "Untitled project";
    const detail = document.createElement("span"); detail.textContent = `${formatDate(session.started_at)} · ${session.event_count} events · ${formatBytes(session.size_bytes)}`;
    link.append(title, detail); list.append(link);
  }
}

function renderSelected() {
  const session = state.detail || state.selected; const shareable = session && session.status !== "recording"; const cloudReady = shareable && state.sharing?.configured; $("event-export-button").classList.toggle("disabled", !session); $("share-button").classList.toggle("disabled", !shareable); $("cloud-share-button").disabled = !cloudReady; $("cloud-share-button").classList.toggle("disabled", !cloudReady); $("share-button").title = shareable ? "Download all available FL Studio, video, and physiology data" : "Stop the recording before sharing it"; $("cloud-share-button").title = !state.sharing?.configured ? "Add Cloudflare R2 credentials to enable private links" : $("share-button").title;
  $("delete-button").disabled = !session || session.status === "recording"; $("review-panel").hidden = !session;
  if (!session) return;
  $("session-status").textContent = session.status.toUpperCase(); $("session-title").textContent = session.project_title || "Untitled project";
  $("session-time").textContent = `${formatDate(session.started_at)} · ${session.event_count} timeline events`; $("event-export-button").href = `/api/sessions/${session.id}/export`; $("share-button").href = `/api/sessions/${session.id}/share`;
  if (!state.detail) return;
  const statuses = [["FL events", "ready"], ["Video", session.video_status], ["EmotiBit", session.emotibit_status], ["Sync", session.sync_quality]];
  const statusBox = $("source-status"); statusBox.replaceChildren();
  for (const [label, status] of statuses) { const pill = document.createElement("span"); pill.className = `source-pill ${status}`; pill.textContent = `${label}: ${status}`; statusBox.append(pill); }
  const video = $("session-video"); const videoAsset = session.assets.find((item) => item.kind === "video" && item.status === "ready"); video.hidden = !videoAsset;
  if (videoAsset) { video.src = `/api/sessions/${session.id}/video`; video.dataset.offset = videoAsset.start_offset_seconds || 0; } else { video.removeAttribute("src"); video.load(); }
  $("playhead").max = Math.max(0.001, sessionDuration(session)); renderTrackPicker();
}

function renderEvents() {
  const filter = state.patternFilter;
  const filterBar = $("pattern-filter"); filterBar.hidden = !filter;
  const events = filter
    ? state.events.filter((event) => event.timeline_seconds != null && event.timeline_seconds >= filter.start && event.timeline_seconds < filter.end)
    : state.events;
  if (filter) $("pattern-filter-text").textContent = `${events.length} event(s) during: ${filter.label} (${formatTime(filter.start)}–${formatTime(filter.end)})`;
  const list = $("event-list"); list.replaceChildren(); $("timeline-empty").hidden = events.length > 0;
  for (const event of events) {
    const row = document.createElement("article"); row.className = "event";
    const time = document.createElement("time"); time.className = "event-time"; time.textContent = event.timeline_seconds == null ? new Date(event.occurred_at).toLocaleTimeString() : formatTime(event.timeline_seconds);
    const dot = document.createElement("div"); dot.className = "event-dot";
    const body = document.createElement("div"); body.className = "event-body"; const summary = document.createElement("strong"); summary.textContent = event.summary;
    const detail = document.createElement("p"); detail.className = "muted"; detail.textContent = [event.category, event.entity_type, event.entity_key].filter(Boolean).join(" · "); body.append(summary, detail);
    const badge = document.createElement("span"); badge.className = `badge ${event.confidence}`; badge.textContent = event.confidence.replace("_", " "); row.append(time, dot, body, badge);
    if (event.timeline_seconds != null) row.addEventListener("click", () => seekTimeline(event.timeline_seconds)); list.append(row);
  }
}

function renderTrackPicker() {
  const picker = $("track-picker"); const selected = new Set(selectedTracks()); picker.replaceChildren();
  const recording = state.detail?.status === "recording"; const streams = recording ? state.liveDescriptors.map((item) => ({ type_tag: item.track_id, display_name: item.display_name, group_name: item.group_name, unit: item.unit })) : state.detail?.signals || [];
  for (const stream of streams) {
    const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = stream.type_tag;
    input.checked = selected.has(stream.type_tag) || (!selected.size && ["EDA", "Heart", "Temperature"].includes(stream.group_name)); input.addEventListener("change", recording ? renderLiveSignals : loadTimeline);
    label.append(input, document.createTextNode(`${stream.display_name}${stream.unit ? ` (${stream.unit})` : ""}`)); picker.append(label);
  }
  recording ? renderLiveSignals() : loadTimeline();
}

function selectedTracks() { return [...$("track-picker").querySelectorAll("input:checked")].map((input) => input.value); }
async function loadTimeline() {
  if (!state.detail || state.detail.status === "recording") return; const duration = Math.max(1, sessionDuration(state.detail)); const zoom = Number($("zoom").value || 1); const width = duration / zoom;
  const start = Math.max(0, Math.min(duration - width, state.currentTime - width / 2)); const params = new URLSearchParams({ start, end: Math.min(duration, start + width), pixels: 1200, tracks: selectedTracks().join(",") });
  state.timeline = await request(`/api/sessions/${state.detail.id}/timeline?${params}`); drawSignals();
}

function appendLiveSamples(item) {
  if (state.selected?.id !== item.session_id) return;
  let latest = 0;
  for (const track of item.tracks) {
    const points = state.liveBuffers.get(track.type_tag) || []; points.push(...track.points); if (points.length) latest = Math.max(latest, points.at(-1)[0]); state.liveBuffers.set(track.type_tag, points);
  }
  const cutoff = Math.max(0, latest - 30); for (const [tag, points] of state.liveBuffers) state.liveBuffers.set(tag, points.filter((point) => point[0] >= cutoff));
  state.currentTime = latest; renderLiveSignals();
}

function renderLiveSignals() {
  if (state.detail?.status !== "recording") return;
  const tracks = selectedTracks(); const all = [...state.liveBuffers.values()].flat(); const end = all.length ? Math.max(...all.map((point) => point[0])) : 30; const start = Math.max(0, end - 30);
  state.timeline = { start, end: Math.max(start + 0.001, end), events: state.events.filter((event) => event.timeline_seconds >= start), signals: tracks.map((type_tag) => ({ type_tag, mode: "raw", points: state.liveBuffers.get(type_tag) || [] })) }; drawSignals(); seekTimeline(end);
}

function drawSignals() {
  const box = $("signal-tracks"); box.replaceChildren(); if (!state.timeline) return;
  const span = state.timeline.end - state.timeline.start;
  for (const signal of state.timeline.signals) {
    const stream = state.detail.signals.find((item) => item.type_tag === signal.type_tag); const row = document.createElement("div"); row.className = "signal-track";
    const label = document.createElement("span"); label.textContent = stream?.display_name || signal.type_tag; const canvas = document.createElement("canvas"); canvas.width = 1200; canvas.height = 72;
    drawSignal(canvas, signal); const seekFromPointer = (event) => { const rect = canvas.getBoundingClientRect(); seekTimeline(state.timeline.start + (event.clientX - rect.left) / rect.width * (state.timeline.end - state.timeline.start)); }; canvas.addEventListener("click", seekFromPointer); canvas.addEventListener("mousemove", seekFromPointer);
    row.append(label, canvas); box.append(row);
  }
  const ruler = $("event-ruler"); ruler.replaceChildren();
  for (const event of state.timeline.events) { const marker = document.createElement("button"); marker.className = "event-marker"; marker.style.left = `${(event.timeline_seconds - state.timeline.start) / span * 100}%`; marker.title = `${formatTime(event.timeline_seconds)} · ${event.summary}`; marker.addEventListener("click", () => seekTimeline(event.timeline_seconds)); ruler.append(marker); }
  drawClusters(span);
}

function clusterLabel(id) { return state.clusterLegend.find((item) => item.id === id)?.label || `pattern ${id + 1}`; }

// Cluster ids come back ranked calm(0) -> activated(last) from the backend, so
// a fixed cool -> warm gradient means the same thing on every session: no
// legend-memorizing required to tell "calmer" from "activated" at a glance.
const CLUSTER_GRADIENT = [
  [0.00, [79, 158, 234]], // calm blue
  [0.30, [83, 211, 147]], // green
  [0.55, [232, 197, 71]], // yellow
  [0.75, [255, 157, 66]], // orange
  [1.00, [255, 93, 108]], // activated red
];
function clusterColor(rank, total) {
  const t = total <= 1 ? 0 : rank / (total - 1);
  for (let i = 0; i < CLUSTER_GRADIENT.length - 1; i++) {
    const [t0, c0] = CLUSTER_GRADIENT[i]; const [t1, c1] = CLUSTER_GRADIENT[i + 1];
    if (t <= t1 || i === CLUSTER_GRADIENT.length - 2) {
      const localT = Math.min(1, Math.max(0, t1 === t0 ? 0 : (t - t0) / (t1 - t0)));
      const mix = (a, b) => Math.round(a + (b - a) * localT);
      return `rgb(${mix(c0[0], c1[0])}, ${mix(c0[1], c1[1])}, ${mix(c0[2], c1[2])})`;
    }
  }
  return "rgb(255, 93, 108)";
}

function drawClusters(span) {
  const toggleRow = $("cluster-toggle-row"); const ruler = $("cluster-ruler");
  toggleRow.hidden = state.clusters.length === 0; ruler.replaceChildren();
  if (!state.clustersVisible) { ruler.hidden = true; renderClusterLegend(); return; }
  const visible = state.clusters.filter((window) => window.end_seconds > state.timeline.start && window.start_seconds < state.timeline.end);
  ruler.hidden = visible.length === 0;
  const total = state.clusterLegend.length;
  for (const window of visible) {
    const start = Math.max(window.start_seconds, state.timeline.start); const end = Math.min(window.end_seconds, state.timeline.end);
    const segment = document.createElement("button"); segment.className = "cluster-segment";
    segment.style.left = `${(start - state.timeline.start) / span * 100}%`; segment.style.width = `${Math.max(0, (end - start) / span * 100)}%`;
    segment.style.background = clusterColor(window.cluster, total);
    segment.title = `${formatTime(window.start_seconds)}–${formatTime(window.end_seconds)} · ${clusterLabel(window.cluster)}`;
    segment.addEventListener("click", () => applyPatternFilter(window));
    ruler.append(segment);
  }
  renderClusterLegend();
}

function renderClusterLegend() {
  const legend = $("cluster-legend"); legend.replaceChildren();
  const used = new Set(state.clusters.map((window) => window.cluster));
  const entries = state.clustersVisible ? state.clusterLegend.filter((item) => used.has(item.id)) : [];
  legend.hidden = entries.length === 0;
  const total = state.clusterLegend.length;
  for (const item of entries) {
    const chip = document.createElement("span"); chip.className = "cluster-chip";
    const dot = document.createElement("i"); dot.style.background = clusterColor(item.id, total);
    chip.append(dot, document.createTextNode(item.label)); legend.append(chip);
  }
}

function applyPatternFilter(window) {
  seekTimeline((window.start_seconds + window.end_seconds) / 2);
  state.patternFilter = { start: window.start_seconds, end: window.end_seconds, label: clusterLabel(window.cluster) };
  renderEvents();
}

$("cluster-toggle").addEventListener("change", (event) => { state.clustersVisible = event.target.checked; drawSignals(); });
$("pattern-filter-clear").addEventListener("click", () => { state.patternFilter = null; renderEvents(); });

function drawSignal(canvas, signal) {
  const ctx = canvas.getContext("2d"); const points = signal.points; if (!points.length) return; const values = points.flatMap((point) => point.slice(1));
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1; const x = (time) => (time - state.timeline.start) / (state.timeline.end - state.timeline.start) * canvas.width; const y = (value) => canvas.height - 4 - (value - min) / span * (canvas.height - 8);
  ctx.strokeStyle = "#ff7a1a"; ctx.lineWidth = 1; ctx.beginPath();
  if (signal.mode === "raw") points.forEach((point, index) => index ? ctx.lineTo(x(point[0]), y(point[1])) : ctx.moveTo(x(point[0]), y(point[1]))); else points.forEach((point) => { ctx.moveTo(x(point[0]), y(point[1])); ctx.lineTo(x(point[0]), y(point[2])); }); ctx.stroke();
}

function seekTimeline(seconds) {
  state.currentTime = Math.max(0, Number(seconds)); $("playhead").value = state.currentTime; $("playhead-label").textContent = formatTime(state.currentTime);
  const video = $("session-video"); if (!video.hidden && Math.abs(video.currentTime - (state.currentTime - Number(video.dataset.offset || 0))) > 0.1) video.currentTime = Math.max(0, state.currentTime - Number(video.dataset.offset || 0));
}

async function synchronizeBrowserClock() {
  const samples = [];
  for (let index = 0; index < 9; index++) { const start = performance.now(); const remote = await request("/api/clock"); const end = performance.now(); samples.push({ rtt_ms: end - start, offset: remote.lsl_seconds - (start + end) / 2000, mode: remote.mode }); }
  const best = samples.sort((a, b) => a.rtt_ms - b.rtt_ms)[0]; return { ...best, model_version: "minimum-rtt-midpoint-v1", estimated_error_ms: best.rtt_ms / 2, sample_count: samples.length };
}
function mediaType() { return ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"].find((type) => MediaRecorder.isTypeSupported(type)) || ""; }

/** Return music-oriented constraints for the display-capture audio track. */
function audioCaptureConstraints() {
  return { channelCount: { ideal: 2 }, sampleRate: { ideal: 48000 }, echoCancellation: false, noiseSuppression: false, autoGainControl: false };
}

/** Return MediaRecorder options that preserve more detail in stereo music. */
function recorderOptions(type) {
  return { ...(type ? { mimeType: type } : {}), audioBitsPerSecond: 256000, audioBitrateMode: "variable" };
}

async function firstVideoFrame(stream) {
  const preview = document.createElement("video"); preview.muted = true; preview.playsInline = true; preview.srcObject = stream;
  try {
    await preview.play();
    return await new Promise((resolve) => { let settled = false; const finish = (value = performance.now()) => { if (!settled) { settled = true; resolve(value); } }; const timer = setTimeout(finish, 1000); if (preview.requestVideoFrameCallback) preview.requestVideoFrameCallback((now) => { clearTimeout(timer); finish(now); }); else preview.addEventListener("loadeddata", () => { clearTimeout(timer); finish(); }, { once: true }); });
  } catch {
    return performance.now();
  } finally {
    preview.pause(); preview.srcObject = null;
  }
}

async function startRecording() {
  $("capture-modal").hidden = false; let stream;
  try { stream = await navigator.mediaDevices.getDisplayMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 }, displaySurface: "window" }, audio: audioCaptureConstraints(), systemAudio: "include", windowAudio: "window", selfBrowserSurface: "exclude" }); }
  catch (error) { $("capture-modal").hidden = true; const session = await request("/api/sessions", { method: "POST" }); state.selected = session; toast("Session started without video"); await refreshAll(); return; }
  $("capture-modal").hidden = true; const videoTrack = stream.getVideoTracks()[0]; const videoSettings = videoTrack.getSettings(); const audioTrack = stream.getAudioTracks()[0] || null; const audioSettings = audioTrack ? audioTrack.getSettings() : null;
  if (videoSettings.displaySurface !== "window") { stream.getTracks().forEach((track) => track.stop()); throw new Error("Select the FL Studio window, not an entire display or tab"); }
  let session = null; let recorder = null; let stopped = Promise.resolve(); let upload = Promise.resolve();
  try {
    const sync = await synchronizeBrowserClock(); session = await request("/api/sessions", { method: "POST" }); state.selected = session; const type = mediaType(); recorder = new MediaRecorder(stream, recorderOptions(type));
    let sequence = 0; let stopping = false; const firstFrame = firstVideoFrame(stream);
    recorder.addEventListener("dataavailable", (event) => { if (event.data.size) upload = upload.then(() => fetch(`/api/sessions/${session.id}/capture/chunks/${sequence++}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: event.data })).then((response) => { if (!response.ok) throw new Error("Video chunk upload failed"); }); });
    stopped = new Promise((resolve) => recorder.addEventListener("stop", resolve, { once: true })); recorder.start(1000); const startedPerformance = await firstFrame;
    await request(`/api/sessions/${session.id}/capture/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mime_type: recorder.mimeType || "video/webm", started_lsl: startedPerformance / 1000 + sync.offset, clock_sync: sync, metadata: { ...videoSettings, has_audio: Boolean(audioTrack), audio_settings: audioSettings, audio_recorder: audioTrack ? { bits_per_second: recorder.audioBitsPerSecond, bitrate_mode: recorder.audioBitrateMode, mime_type: recorder.mimeType } : null } }) });
    videoTrack.addEventListener("ended", async () => { if (!stopping && state.capture) { toast("Window sharing ended; finalizing the partial video"); await stopRecording(); } });
    state.capture = { recorder, stream, upload: () => upload, stopped, sync, startedPerformance, sessionId: session.id, markStopping: () => { stopping = true; } }; await refreshAll();
    if (!audioTrack) toast("Recording started without audio");
    else if (audioSettings.channelCount === 2) toast("Recording started with stereo audio");
    else if (audioSettings.channelCount) toast(`Recording started with ${audioSettings.channelCount}-channel audio; stereo is recommended`);
    else toast("Recording started; Chrome did not report the audio channel count");
  } catch (error) {
    if (recorder?.state !== "inactive") recorder.stop(); await stopped; await upload.catch(() => null); stream.getTracks().forEach((track) => track.stop());
    if (session) { await request(`/api/sessions/${session.id}/capture/abort`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: error.message }) }).catch(() => null); await request(`/api/sessions/${session.id}/stop`, { method: "POST" }).catch(() => null); }
    throw error;
  }
}

async function stopRecording() {
  const active = state.health?.active_session_id; if (!active) return;
  let captureError = null;
  if (state.capture) {
    const capture = state.capture; capture.markStopping();
    try {
      if (capture.recorder.state !== "inactive") capture.recorder.stop(); await capture.stopped; await capture.upload(); const endedPerformance = performance.now();
      await request(`/api/sessions/${active}/capture/complete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ended_lsl: endedPerformance / 1000 + capture.sync.offset, duration_seconds: (endedPerformance - capture.startedPerformance) / 1000, metadata: {} }) });
    } catch (error) {
      captureError = error; await request(`/api/sessions/${active}/capture/abort`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: error.message }) }).catch(() => null);
    } finally {
      capture.stream.getTracks().forEach((track) => track.stop()); state.capture = null;
    }
  }
  await request(`/api/sessions/${active}/stop`, { method: "POST" }); state.liveBuffers.clear(); toast("Recording stopped"); await refreshAll();
  if (captureError) throw captureError;
}

async function loadHealth() { state.health = await request("/api/health"); renderHealth(); }
async function loadSharing() { state.sharing = await request("/api/sharing"); renderSelected(); }
async function loadCloudSessions() { if (!state.sharing?.configured) { state.cloudSessions = []; renderCloudSessions(); return; } try { state.cloudSessions = await request("/api/cloud-sessions"); renderCloudSessions(); } catch (error) { state.cloudSessions = []; renderCloudSessions(error.message); } }
async function loadSessions() { state.sessions = await request("/api/sessions"); if (state.selected) state.selected = state.sessions.find((item) => item.id === state.selected.id) || null; renderSessions(); renderSelected(); }
async function selectSession(session) { state.selected = session; state.detail = await request(`/api/sessions/${session.id}`); state.clusters = []; state.patternFilter = null; renderSessions(); renderSelected(); await Promise.all([loadCategories(), loadEvents(), loadBiosignalClusters()]); drawSignals(); }

async function loadBiosignalClusters() {
  if (!state.detail || state.detail.status === "recording") { state.clusters = []; state.clusterLegend = []; return; }
  try {
    const result = await request(`/api/sessions/${state.detail.id}/biosignal-clusters`);
    state.clusters = result.windows; state.clusterLegend = result.clusters;
  } catch { state.clusters = []; state.clusterLegend = []; }
}
async function loadCategories() { const select = $("category"), selected = select.value; const categories = state.selected ? await request(`/api/sessions/${state.selected.id}/categories`) : []; select.replaceChildren(new Option("All activity", "")); categories.forEach((value) => select.append(new Option(value, value))); select.value = categories.includes(selected) ? selected : ""; }
async function loadEvents() { if (!state.selected) return; const params = new URLSearchParams(); if ($("category").value) params.set("category", $("category").value); if ($("confidence").value) params.set("confidence", $("confidence").value); if ($("search").value) params.set("search", $("search").value); state.events = await request(`/api/sessions/${state.selected.id}/events?${params}`); renderEvents(); }
async function refreshAll() { await Promise.all([loadHealth(), loadSharing(), loadSessions()]); await loadCloudSessions(); if (state.selected) await selectSession(state.selected); }

$("record-button").addEventListener("click", async () => { try { state.health?.active_session_id ? await stopRecording() : await startRecording(); } catch (error) { toast(error.message); } });
$("refresh-sessions").addEventListener("click", refreshAll);
$("import-button").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async (event) => {
  const file = event.target.files[0]; if (!file) return;
  $("import-button").disabled = true; $("import-button").textContent = "Verifying and importing…";
  try { const imported = await request("/api/research-archives", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file }); await loadSessions(); const session = state.sessions.find((item) => item.id === imported.id); if (session) await selectSession(session); toast("Research session imported and verified"); }
  catch (error) { toast(error.message); }
  finally { event.target.value = ""; $("import-button").disabled = false; $("import-button").textContent = "Import research archive"; }
});
$("cloud-share-button").addEventListener("click", async () => {
  if (!state.selected) return; const button = $("cloud-share-button"); button.disabled = true; button.textContent = "Uploading…";
  try { const shared = await request(`/api/sessions/${state.selected.id}/cloud-share`, { method: "POST" }); await loadCloudSessions(); $("share-link").value = shared.download_url; const days = shared.expires_in_seconds / 86400; $("share-expiry").textContent = `Uploaded to Cloud sessions. This direct link expires in ${Number.isInteger(days) ? days : days.toFixed(1)} days.`; $("share-modal").hidden = false; await navigator.clipboard.writeText(shared.download_url).then(() => toast("Session uploaded and link copied"), () => toast("Session uploaded to cloud")); }
  catch (error) { toast(error.message); }
  finally { button.textContent = "Upload to cloud"; renderSelected(); }
});
$("copy-share-link").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("share-link").value); toast("Private link copied"); } catch { $("share-link").select(); toast("Select and copy the link"); } });
$("close-share-modal").addEventListener("click", () => { $("share-modal").hidden = true; });
$("delete-button").addEventListener("click", async () => { if (!state.selected || !confirm("Permanently delete this local recording and its media?")) return; await request(`/api/sessions/${state.selected.id}`, { method: "DELETE" }); state.selected = null; state.detail = null; state.events = []; renderEvents(); renderSelected(); await loadSessions(); toast("Session deleted"); });
for (const id of ["category", "confidence"]) $(id).addEventListener("change", loadEvents);
let searchTimer; $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadEvents, 250); });
$("playhead").addEventListener("input", (event) => seekTimeline(event.target.value)); $("zoom").addEventListener("change", loadTimeline); $("session-video").addEventListener("timeupdate", (event) => seekTimeline(event.target.currentTime + Number(event.target.dataset.offset || 0)));
const live = new EventSource("/api/live"); live.onmessage = async (message) => { const item = JSON.parse(message.data); if (item.health) { state.health = { ...state.health, ...item.health }; renderHealth(); } if (item.type === "emotibit_status") { state.health = { ...state.health, emotibit: item.emotibit }; state.liveDescriptors = item.emotibit.streams || []; renderHealth(); if (state.detail?.status === "recording") renderTrackPicker(); } if (["session_started", "session_stopped"].includes(item.type)) await refreshAll(); if (item.type === "emotibit_samples") appendLiveSamples(item); if (item.type === "event" && state.selected?.id === item.event.session_id) { state.events.unshift(item.event); renderEvents(); if (state.detail?.status === "recording") renderLiveSignals(); } }; live.onerror = () => $("connection").classList.remove("connected");
Promise.all([loadHealth(), loadSharing(), loadSessions()]).then(loadCloudSessions).catch((error) => toast(error.message));
