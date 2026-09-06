const state = { health: null, sessions: [], selected: null, events: [], detail: null, capture: null, currentTime: 0, timeline: null };
const $ = (id) => document.getElementById(id);

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const element = $("toast"); element.textContent = message; element.classList.add("visible");
  setTimeout(() => element.classList.remove("visible"), 3500);
}

function renderHealth() {
  const health = state.health || {}; const connected = Boolean(health.connected && health.compatible);
  $("connection").classList.toggle("connected", connected);
  $("connection-label").textContent = !health.connected ? "FL Studio disconnected" : health.compatible ? `FL Studio connected · ${health.clock_mode}` : "Incompatible FL observer";
  const active = health.active_session_id; $("record-button").disabled = !connected;
  $("record-button").classList.toggle("recording", Boolean(active)); $("record-label").textContent = active ? "Stop recording" : "Start recording";
  $("record-help").textContent = active ? "FL events and timeline markers are recording." : connected ? "Chrome will ask you to select the FL Studio window." : "Connect the FL Studio observer to begin.";
}

function formatDate(value) { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : ""; }
function formatTime(seconds) { const value = Math.max(0, Number(seconds) || 0); return `${String(Math.floor(value / 60)).padStart(2, "0")}:${(value % 60).toFixed(3).padStart(6, "0")}`; }
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

function renderSelected() {
  const session = state.detail || state.selected; $("export-button").classList.toggle("disabled", !session);
  $("delete-button").disabled = !session || session.status === "recording"; $("import-button").disabled = !session || session.status === "recording"; $("review-panel").hidden = !session;
  if (!session) return;
  $("session-status").textContent = session.status.toUpperCase(); $("session-title").textContent = session.project_title || "Untitled project";
  $("session-time").textContent = `${formatDate(session.started_at)} · ${session.event_count} timeline events`; $("export-button").href = `/api/sessions/${session.id}/export`;
  if (!state.detail) return;
  const statuses = [["FL events", "ready"], ["Video", session.video_status], ["EmotiBit", session.emotibit_status], ["Sync", session.sync_quality]];
  const statusBox = $("source-status"); statusBox.replaceChildren();
  for (const [label, status] of statuses) { const pill = document.createElement("span"); pill.className = `source-pill ${status}`; pill.textContent = `${label}: ${status}`; statusBox.append(pill); }
  const video = $("session-video"); const videoAsset = session.assets.find((item) => item.kind === "video" && item.status === "ready"); video.hidden = !videoAsset;
  if (videoAsset) { video.src = `/api/sessions/${session.id}/video`; video.dataset.offset = videoAsset.start_offset_seconds || 0; } else { video.removeAttribute("src"); video.load(); }
  $("playhead").max = Math.max(0.001, sessionDuration(session)); renderTrackPicker();
}

function renderEvents() {
  const list = $("event-list"); list.replaceChildren(); $("timeline-empty").hidden = state.events.length > 0;
  for (const event of state.events) {
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
  const picker = $("track-picker"); picker.replaceChildren();
  for (const stream of state.detail.signals || []) {
    const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = stream.type_tag;
    input.checked = ["EDA", "Heart", "Temperature"].includes(stream.group_name); input.addEventListener("change", loadTimeline);
    label.append(input, document.createTextNode(`${stream.display_name}${stream.unit ? ` (${stream.unit})` : ""}`)); picker.append(label);
  }
  loadTimeline();
}

function selectedTracks() { return [...$("track-picker").querySelectorAll("input:checked")].map((input) => input.value); }
async function loadTimeline() {
  if (!state.detail) return; const duration = Math.max(1, sessionDuration(state.detail)); const zoom = Number($("zoom").value || 1); const width = duration / zoom;
  const start = Math.max(0, Math.min(duration - width, state.currentTime - width / 2)); const params = new URLSearchParams({ start, end: Math.min(duration, start + width), pixels: 1200, tracks: selectedTracks().join(",") });
  state.timeline = await request(`/api/sessions/${state.detail.id}/timeline?${params}`); drawSignals();
}

function drawSignals() {
  const box = $("signal-tracks"); box.replaceChildren(); if (!state.timeline) return;
  for (const signal of state.timeline.signals) {
    const stream = state.detail.signals.find((item) => item.type_tag === signal.type_tag); const row = document.createElement("div"); row.className = "signal-track";
    const label = document.createElement("span"); label.textContent = stream?.display_name || signal.type_tag; const canvas = document.createElement("canvas"); canvas.width = 1200; canvas.height = 72;
    drawSignal(canvas, signal); const seekFromPointer = (event) => { const rect = canvas.getBoundingClientRect(); seekTimeline(state.timeline.start + (event.clientX - rect.left) / rect.width * (state.timeline.end - state.timeline.start)); }; canvas.addEventListener("click", seekFromPointer); canvas.addEventListener("mousemove", seekFromPointer);
    row.append(label, canvas); box.append(row);
  }
  const ruler = $("event-ruler"); ruler.replaceChildren(); const span = state.timeline.end - state.timeline.start;
  for (const event of state.timeline.events) { const marker = document.createElement("button"); marker.className = "event-marker"; marker.style.left = `${(event.timeline_seconds - state.timeline.start) / span * 100}%`; marker.title = `${formatTime(event.timeline_seconds)} · ${event.summary}`; marker.addEventListener("click", () => seekTimeline(event.timeline_seconds)); ruler.append(marker); }
}

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
  try { stream = await navigator.mediaDevices.getDisplayMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 }, displaySurface: "window" }, audio: true, systemAudio: "include", windowAudio: "window", selfBrowserSurface: "exclude" }); }
  catch (error) { $("capture-modal").hidden = true; const session = await request("/api/sessions", { method: "POST" }); state.selected = session; toast("Session started without video"); await refreshAll(); return; }
  $("capture-modal").hidden = true; const videoTrack = stream.getVideoTracks()[0]; const settings = videoTrack.getSettings();
  if (settings.displaySurface !== "window") { stream.getTracks().forEach((track) => track.stop()); throw new Error("Select the FL Studio window, not an entire display or tab"); }
  let session = null; let recorder = null; let stopped = Promise.resolve(); let upload = Promise.resolve();
  try {
    const sync = await synchronizeBrowserClock(); session = await request("/api/sessions", { method: "POST" }); state.selected = session; const type = mediaType(); recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
    let sequence = 0; let stopping = false; const firstFrame = firstVideoFrame(stream);
    recorder.addEventListener("dataavailable", (event) => { if (event.data.size) upload = upload.then(() => fetch(`/api/sessions/${session.id}/capture/chunks/${sequence++}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: event.data })).then((response) => { if (!response.ok) throw new Error("Video chunk upload failed"); }); });
    stopped = new Promise((resolve) => recorder.addEventListener("stop", resolve, { once: true })); recorder.start(1000); const startedPerformance = await firstFrame;
    await request(`/api/sessions/${session.id}/capture/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mime_type: recorder.mimeType || "video/webm", started_lsl: startedPerformance / 1000 + sync.offset, clock_sync: sync, metadata: { ...settings, has_audio: stream.getAudioTracks().length > 0 } }) });
    videoTrack.addEventListener("ended", async () => { if (!stopping && state.capture) { toast("Window sharing ended; finalizing the partial video"); await stopRecording(); } });
    state.capture = { recorder, stream, upload: () => upload, stopped, sync, startedPerformance, sessionId: session.id, markStopping: () => { stopping = true; } }; await refreshAll(); toast(stream.getAudioTracks().length ? "Recording started" : "Recording started without audio");
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
  await request(`/api/sessions/${active}/stop`, { method: "POST" }); toast("Recording stopped"); await refreshAll();
  if (captureError) throw captureError;
}

async function loadHealth() { state.health = await request("/api/health"); renderHealth(); }
async function loadSessions() { state.sessions = await request("/api/sessions"); if (state.selected) state.selected = state.sessions.find((item) => item.id === state.selected.id) || null; renderSessions(); renderSelected(); }
async function selectSession(session) { state.selected = session; state.detail = await request(`/api/sessions/${session.id}`); renderSessions(); renderSelected(); await Promise.all([loadCategories(), loadEvents()]); }
async function loadCategories() { const select = $("category"), selected = select.value; const categories = state.selected ? await request(`/api/sessions/${state.selected.id}/categories`) : []; select.replaceChildren(new Option("All activity", "")); categories.forEach((value) => select.append(new Option(value, value))); select.value = categories.includes(selected) ? selected : ""; }
async function loadEvents() { if (!state.selected) return; const params = new URLSearchParams(); if ($("category").value) params.set("category", $("category").value); if ($("confidence").value) params.set("confidence", $("confidence").value); if ($("search").value) params.set("search", $("search").value); state.events = await request(`/api/sessions/${state.selected.id}/events?${params}`); renderEvents(); }
async function refreshAll() { await Promise.all([loadHealth(), loadSessions()]); if (state.selected) await selectSession(state.selected); }

$("record-button").addEventListener("click", async () => { try { state.health?.active_session_id ? await stopRecording() : await startRecording(); } catch (error) { toast(error.message); } });
$("refresh-sessions").addEventListener("click", refreshAll);
$("delete-button").addEventListener("click", async () => { if (!state.selected || !confirm("Permanently delete this local recording and its media?")) return; await request(`/api/sessions/${state.selected.id}`, { method: "DELETE" }); state.selected = null; state.detail = null; state.events = []; renderEvents(); renderSelected(); await loadSessions(); toast("Session deleted"); });
$("import-button").addEventListener("click", () => $("emotibit-files").click());
$("emotibit-files").addEventListener("change", async (event) => { if (!state.selected || !event.target.files.length) return; const body = new FormData(); [...event.target.files].forEach((file) => body.append("files", file)); const job = await request(`/api/sessions/${state.selected.id}/emotibit/import`, { method: "POST", body }); toast("EmotiBit import started"); const timer = setInterval(async () => { const status = await request(`/api/import-jobs/${job.job_id}`); if (status.status !== "running") { clearInterval(timer); toast(status.status === "completed" ? "EmotiBit import complete" : status.error); await selectSession(state.selected); } }, 1000); });
for (const id of ["category", "confidence"]) $(id).addEventListener("change", loadEvents);
let searchTimer; $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadEvents, 250); });
$("playhead").addEventListener("input", (event) => seekTimeline(event.target.value)); $("zoom").addEventListener("change", loadTimeline); $("session-video").addEventListener("timeupdate", (event) => seekTimeline(event.target.currentTime + Number(event.target.dataset.offset || 0)));
const live = new EventSource("/api/live"); live.onmessage = async (message) => { const item = JSON.parse(message.data); if (item.health) { state.health = item.health; renderHealth(); } if (["session_started", "session_stopped", "emotibit_imported"].includes(item.type)) await refreshAll(); if (item.type === "event" && state.selected?.id === item.event.session_id) { state.events.unshift(item.event); renderEvents(); } }; live.onerror = () => $("connection").classList.remove("connected");
Promise.all([loadHealth(), loadSessions()]).catch((error) => toast(error.message));
