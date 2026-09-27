const state = { health: null, sharing: null, sessions: [], cloudSessions: [], bookmarks: [], bookmarkLabels: [], selectedBookmark: null, selectedInterval: null, focusWindow: null, selected: null, events: [], detail: null, capture: null, currentTime: 0, timeline: null, liveBuffers: new Map(), liveDescriptors: [], clusters: [], clusterLegend: [], clusterMeta: null, clustersVisible: true, collapsedTracks: new Set(), selectedTrackTags: null };
const $ = (id) => document.getElementById(id);

function showWorkspaceView() {
  const requested = location.hash.slice(1);
  const view = ["sessions", "review", "notebook"].includes(requested) ? requested : "sessions";
  for (const name of ["sessions", "review", "notebook"]) $(`${name}-view`).hidden = name !== view;
  document.querySelectorAll("[data-view-link]").forEach((link) => {
    if (link.dataset.viewLink === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (view !== "review") $("session-video").pause();
  if (view === "review" && state.timeline) requestAnimationFrame(drawSignals);
}
window.addEventListener("hashchange", showWorkspaceView);
showWorkspaceView();

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
// Untitled sessions default to a time-based name (rather than a static
// "Untitled project" label) so the list stays distinguishable at a glance;
// the user can still rename any session from the review page.
function defaultSessionTitle(session) { return session.project_title || `Session – ${formatDate(session.started_at)}`; }

function renderSessions() {
  const list = $("session-list"); list.replaceChildren(); $("sessions-empty").hidden = state.sessions.length > 0;
  for (const session of state.sessions) {
    const button = document.createElement("button"); button.className = `session-item ${state.selected?.id === session.id ? "active" : ""}`;
    const title = document.createElement("strong"); title.textContent = defaultSessionTitle(session);
    const detail = document.createElement("span"); detail.textContent = `${formatDate(session.started_at)} · ${session.event_count} events · ${session.bookmark_count || 0} bookmarks`;
    button.append(title, detail); button.addEventListener("click", () => selectSession(session)); list.append(button);
  }
}

function renderBookmarks() {
  const list = $("bookmark-list"); list.replaceChildren(); $("bookmark-count").textContent = state.bookmarks.length;
  $("bookmarks-empty").hidden = state.bookmarks.length > 0;
  $("bookmarks-empty").textContent = ($("bookmark-search").value || $("bookmark-label-filter").value) ? "No bookmarks match these filters." : "No saved moments yet. Select an interval in a session to save one.";
  for (const bookmark of state.bookmarks) {
    const button = document.createElement("button"); button.className = `bookmark-card ${state.selectedBookmark?.id === bookmark.id ? "active" : ""}`;
    const note = document.createElement("strong"); note.textContent = bookmark.note;
    const bookmarkSessionTitle = bookmark.project_title || `Session – ${formatDate(bookmark.session_started_at)}`;
    const detail = document.createElement("small"); detail.textContent = `${bookmark.source_type === "pattern" ? "Pattern · " : ""}${bookmarkSessionTitle} · ${formatTime(bookmark.start_seconds)}–${formatTime(bookmark.end_seconds)}`;
    const tags = document.createElement("div"); tags.className = "bookmark-tags";
    bookmark.labels.forEach((label) => { const tag = document.createElement("span"); tag.textContent = label; tags.append(tag); });
    const action = document.createElement("span"); action.className = "bookmark-open"; action.textContent = "Open in session review →";
    button.append(note, detail, tags, action); button.addEventListener("click", () => openBookmark(bookmark)); list.append(button);
  }
}

function renderBookmarkLabels() {
  const filter = $("bookmark-label-filter"); const selected = filter.value; filter.replaceChildren(new Option("All labels", ""));
  const options = $("bookmark-label-options"); options.replaceChildren();
  for (const item of state.bookmarkLabels) { filter.append(new Option(`${item.label} (${item.count})`, item.label)); options.append(new Option(item.label)); }
  filter.value = state.bookmarkLabels.some((item) => item.label === selected) ? selected : "";
}

function renderCloudSessions(message = "") {
  const list = $("cloud-session-list"); const empty = $("cloud-sessions-empty"); list.replaceChildren(); empty.hidden = state.cloudSessions.length > 0; empty.textContent = message || (state.sharing?.configured ? "No sessions have been uploaded yet." : "Cloud sharing is not configured on this device.");
  for (const session of state.cloudSessions) {
    const link = document.createElement("a"); link.className = "session-item cloud"; link.href = `/api/cloud-sessions/download?key=${encodeURIComponent(session.archive_key)}`;
    const title = document.createElement("strong"); title.textContent = defaultSessionTitle(session);
    const detail = document.createElement("span"); detail.textContent = `${formatDate(session.started_at)} · ${session.event_count} events · ${formatBytes(session.size_bytes)}`;
    link.append(title, detail); list.append(link);
  }
}

function renderSelected() {
  const session = state.detail || state.selected; const shareable = session && session.status !== "recording"; const cloudReady = shareable && state.sharing?.configured; $("event-export-button").classList.toggle("disabled", !session); $("share-button").classList.toggle("disabled", !shareable); $("cloud-share-button").disabled = !cloudReady; $("cloud-share-button").classList.toggle("disabled", !cloudReady); $("share-button").title = shareable ? "Download all available FL Studio, video, and physiology data" : "Stop the recording before sharing it"; $("cloud-share-button").title = !state.sharing?.configured ? "Add Cloudflare R2 credentials to enable private links" : $("share-button").title;
  $("delete-button").disabled = !session || session.status === "recording"; $("review-panel").hidden = !session;
  $("session-title-edit").hidden = !session || session.status === "recording";
  if (!session) {
    $("session-status").textContent = "NO SESSION OPEN";
    $("session-title").textContent = "Open a session to begin";
    $("session-time").textContent = "Go to Sessions to choose a recording. Your saved moments are in History.";
    $("session-video").pause();
    return;
  }
  $("session-status").textContent = session.status.toUpperCase(); $("session-title").textContent = defaultSessionTitle(session);
  $("session-time").textContent = `${formatDate(session.started_at)} · ${session.event_count} timeline events`; $("event-export-button").href = `/api/sessions/${session.id}/export`; $("share-button").href = `/api/sessions/${session.id}/share`;
  if (!state.detail) return;
  const statuses = [["FL events", "ready"], ["Video", session.video_status], ["EmotiBit", session.emotibit_status], ["Sync", session.sync_quality]];
  const statusBox = $("source-status"); statusBox.replaceChildren();
  for (const [label, status] of statuses) { if (["ready", "aligned_unverified"].includes(status)) continue; const pill = document.createElement("span"); pill.className = `source-pill ${status}`; pill.textContent = `${label}: ${status}`; statusBox.append(pill); }
  statusBox.hidden = statusBox.childElementCount === 0;
  const video = $("session-video"); const videoAsset = session.assets.find((item) => item.kind === "video" && item.status === "ready"); video.hidden = !videoAsset;
  if (videoAsset) { video.src = `/api/sessions/${session.id}/video`; video.dataset.offset = videoAsset.start_offset_seconds || 0; } else { video.removeAttribute("src"); video.load(); }
  updateListenControl();
  renderTrackPicker();
}

function transportMarker(event) {
  if (event.category !== "transport") return null;
  const after = event.payload?.after;
  const summary = String(event.summary || "").toLowerCase();
  if (event.action === "playing_changed" || summary === "playback started" || summary === "playback stopped") {
    if (after === true || summary === "playback started") return ["playback-start", "▶", "Start playback"];
    if (after === false || summary === "playback stopped") return ["playback-stop", "❚❚", "Stop playback"];
  }
  if (event.action === "recording_changed" || summary === "fl studio recording enabled" || summary === "fl studio recording disabled") {
    if (after === true || summary === "fl studio recording enabled") return ["recording-start", "●", "Start recording"];
    if (after === false || summary === "fl studio recording disabled") return ["recording-stop", "■", "Stop recording"];
  }
  return null;
}

// Selected tracks are chips the user can remove, plus a single "+ Add track"
// dropdown for the rest, instead of a wall of checkboxes for every stream
// (most sessions only care about 3-4 of the dozen or so available signals).
function renderTrackPicker() {
  const picker = $("track-picker"); picker.replaceChildren();
  const recording = state.detail?.status === "recording"; const streams = recording ? state.liveDescriptors.map((item) => ({ type_tag: item.track_id, display_name: item.display_name, group_name: item.group_name, unit: item.unit })) : state.detail?.signals || [];
  if (state.selectedTrackTags === null) state.selectedTrackTags = new Set(streams.filter((item) => ["EDA", "Heart"].includes(item.group_name)).map((item) => item.type_tag));
  const known = new Set(streams.map((item) => item.type_tag));
  for (const tag of state.selectedTrackTags) if (!known.has(tag)) state.selectedTrackTags.delete(tag);
  for (const stream of streams) {
    if (!state.selectedTrackTags.has(stream.type_tag)) continue;
    const chip = document.createElement("span"); chip.className = "track-chip";
    chip.append(document.createTextNode(`${stream.display_name}${stream.unit ? ` (${stream.unit})` : ""}`));
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "track-chip-remove"; remove.setAttribute("aria-label", `Remove ${stream.display_name}`); remove.textContent = "×";
    remove.addEventListener("click", () => { state.selectedTrackTags.delete(stream.type_tag); renderTrackPicker(); });
    chip.append(remove); picker.append(chip);
  }
  const remaining = streams.filter((item) => !state.selectedTrackTags.has(item.type_tag));
  const adder = document.createElement("select"); adder.className = "track-adder"; adder.disabled = remaining.length === 0;
  adder.append(new Option(remaining.length ? "+ Add track" : "All tracks added", ""));
  for (const stream of remaining) adder.append(new Option(`${stream.display_name}${stream.unit ? ` (${stream.unit})` : ""}`, stream.type_tag));
  adder.addEventListener("change", () => { if (!adder.value) return; state.selectedTrackTags.add(adder.value); renderTrackPicker(); });
  picker.append(adder);
  recording ? renderLiveSignals() : loadTimeline();
}

function selectedTracks() { return [...(state.selectedTrackTags || [])]; }

// The zoom slider is a continuous exponential control (like a video editor's
// timeline zoom), not a handful of fixed presets: each whole step doubles how
// tight the view is, so dragging it feels smooth from the full session down
// to a few-second window.
function zoomFactor() { return Math.pow(2, Number($("zoom").value || 0)); }
function updateZoomLabel() { const factor = zoomFactor(); $("zoom-label").textContent = `${factor < 10 ? factor.toFixed(1) : Math.round(factor)}×`; }
function stepZoom(delta) {
  const zoom = $("zoom"); zoom.value = Math.min(Number(zoom.max), Math.max(Number(zoom.min), Number(zoom.value) + delta));
  state.focusWindow = null; updateZoomLabel(); loadTimeline();
}

async function loadTimeline() {
  if (!state.detail || state.detail.status === "recording") return; const duration = Math.max(1, sessionDuration(state.detail)); let start; let end;
  if (state.focusWindow) { start = state.focusWindow.start; end = state.focusWindow.end; }
  else { const width = duration / zoomFactor(); start = Math.max(0, Math.min(duration - width, state.currentTime - width / 2)); end = Math.min(duration, start + width); }
  const params = new URLSearchParams({ start, end, pixels: 1200, tracks: selectedTracks().join(",") });
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

// Each signal group gets a fixed accent (from the existing palette, not new
// colors) so tracks read as distinct lanes at a glance, the way a video
// editor color-codes audio/video/text tracks.
const TRACK_GROUP_COLOR = { EDA: "--sig-eda", SCR: "--sig-scr", PPG: "--sig-ppg", Temperature: "--sig-temp", Heart: "--sig-heart", Motion: "--sig-motion" };
function trackColor(groupName) {
  const name = TRACK_GROUP_COLOR[groupName] || "--orange";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawSignals() {
  const box = $("signal-tracks"); box.replaceChildren(); state.trackReadouts = []; $("timeline-hover").hidden = true; if (!state.timeline) return;
  const span = state.timeline.end - state.timeline.start;
  for (const signal of state.timeline.signals) {
    const stream = state.detail.signals.find((item) => item.type_tag === signal.type_tag)
      || state.liveDescriptors.find((item) => item.track_id === signal.type_tag);
    const name = stream?.display_name || signal.type_tag; const color = trackColor(stream?.group_name);
    const collapsed = state.collapsedTracks.has(signal.type_tag);
    const row = document.createElement("div"); row.className = `signal-track${collapsed ? " collapsed" : ""}`; row.dataset.tag = signal.type_tag;
    row.style.setProperty("--track-color", color);
    const label = document.createElement("button"); label.type = "button"; label.className = "lane-label lane-toggle"; label.setAttribute("aria-expanded", String(!collapsed));
    const chevron = document.createElement("span"); chevron.className = "lane-chevron"; chevron.setAttribute("aria-hidden", "true");
    const dot = document.createElement("span"); dot.className = "lane-dot"; dot.style.background = color;
    const title = document.createElement("span"); title.className = "lane-title"; title.textContent = name;
    const value = document.createElement("span"); value.className = "lane-value";
    title.append(value); label.append(chevron, dot, title);
    label.addEventListener("click", () => {
      const next = !state.collapsedTracks.has(signal.type_tag);
      if (next) state.collapsedTracks.add(signal.type_tag); else state.collapsedTracks.delete(signal.type_tag);
      row.classList.toggle("collapsed", next); label.setAttribute("aria-expanded", String(!next));
    });
    const canvas = document.createElement("canvas"); canvas.width = 1200; canvas.height = 72;
    const headline = selectedHeadline(); const marked = headline?.band && !collapsed && [signal.type_tag, stream?.type_tag].includes(headline.tag);
    const range = drawSignal(canvas, signal, color, marked ? headline.band : null); enableIntervalDrag(canvas);
    const unit = stream?.unit || DEFAULT_UNITS[signal.type_tag] || "";
    const scale = document.createElement("div"); scale.className = "track-scale";
    if (range) scale.append(Object.assign(document.createElement("span"), { textContent: formatReading(range.max, unit, range) }), Object.assign(document.createElement("span"), { textContent: formatReading(range.min, unit, range) }));
    const marker = document.createElement("i"); marker.className = "track-hover-dot"; marker.style.background = color; marker.hidden = true;
    row.append(label, canvas, scale, marker); box.append(row);
    if (marked && range) drawHeadlineMarks(row, canvas, range, headline);
    state.trackReadouts.push({ signal, unit, range, canvas, value, marker });
  }
  drawTimelineGrid(span);
  if (state.hoverEvent) showTrackHover(state.hoverEvent); else showReadingsAt(state.currentTime);
  const ruler = $("event-ruler"); ruler.replaceChildren(); state.eventMarkers = [];
  for (const event of state.timeline.events) {
    const marker = document.createElement("button"); state.eventMarkers.push({ event, marker }); const transport = transportMarker(event);
    marker.className = transport ? `event-marker transport-marker ${transport[0]}` : "event-marker";
    if (transport) marker.textContent = transport[1];
    marker.style.left = `${(event.timeline_seconds - state.timeline.start) / span * 100}%`;
    marker.title = `${formatTime(event.timeline_seconds)} · ${transport ? transport[2] : event.summary}`;
    marker.setAttribute("aria-label", marker.title);
    marker.addEventListener("click", (event_) => { event_.stopPropagation(); seekTimeline(event.timeline_seconds); }); ruler.append(marker);
  }
  enableIntervalDrag(ruler);
  drawTimelineRuler(span); drawPatternStrip(span); drawPatternOverlay(span);
  updateTimelinePlayhead(); updatePlayheadEvent(); updateTimelineSelection();
}

function niceRulerStep(rawStep) {
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
  return steps.find((value) => value >= rawStep) || rawStep;
}
function drawTimelineRuler(span) {
  const ruler = $("timeline-ruler"); ruler.replaceChildren(); if (!state.timeline || span <= 0) return;
  const step = niceRulerStep(span / 6); const first = Math.ceil(state.timeline.start / step) * step;
  for (let time = first; time <= state.timeline.end + 0.0001; time += step) {
    const tick = document.createElement("div"); tick.className = "ruler-tick"; tick.style.left = `${(time - state.timeline.start) / span * 100}%`;
    const label = document.createElement("span"); label.textContent = formatTime(time); tick.append(label); ruler.append(tick);
  }
}

// A grid line under every ruler tick, running the full height of the stack
// (not just the ruler row), so zooming in/out reads as an obvious change in
// line spacing across every lane at once, not just in the thin ruler.
function drawTimelineGrid(span) {
  const grid = $("timeline-grid"); grid.replaceChildren(); if (!state.timeline || span <= 0) return;
  const step = niceRulerStep(span / 6); const first = Math.ceil(state.timeline.start / step) * step;
  const { left, width } = timelineContentBox();
  for (let time = first; time <= state.timeline.end + 0.0001; time += step) {
    const line = document.createElement("div"); line.className = "grid-line";
    line.style.left = `${left + (time - state.timeline.start) / span * width}px`; grid.append(line);
  }
}

// The playhead line and selection box are single elements overlaid on the whole
// timeline-stack (not redrawn per track), so every lane and signal trace stays
// visually stitched to one shared time axis, like a video editor's cursor.
function timelineContentBox() {
  const stack = $("timeline-stack"); const stackRect = stack.getBoundingClientRect(); const contentRect = $("timeline-ruler").getBoundingClientRect();
  return { left: contentRect.left - stackRect.left, width: contentRect.width };
}
function updateTimelinePlayhead() {
  const line = $("timeline-playhead"); if (!state.timeline) { line.hidden = true; return; }
  const span = state.timeline.end - state.timeline.start; const ratio = (state.currentTime - state.timeline.start) / span;
  if (!Number.isFinite(ratio) || ratio < 0 || ratio > 1) { line.hidden = true; return; }
  const { left, width } = timelineContentBox(); line.hidden = false; line.style.left = `${left + ratio * width}px`;
}
function updateTimelineSelection() {
  const box = $("timeline-selection"); if (!state.selectedInterval || !state.timeline) { box.hidden = true; return; }
  const start = Math.max(state.selectedInterval.start, state.timeline.start); const end = Math.min(state.selectedInterval.end, state.timeline.end);
  if (end <= start) { box.hidden = true; return; }
  const span = state.timeline.end - state.timeline.start; const { left, width } = timelineContentBox();
  box.hidden = false; box.style.left = `${left + (start - state.timeline.start) / span * width}px`; box.style.width = `${(end - start) / span * width}px`;
}

function clusterHeadline(id) { return state.clusterLegend.find((item) => item.id === id)?.headline || null; }
// One line per suggestion: what changed, then the number the scorer compared.
function clusterLabel(id) {
  const headline = clusterHeadline(id);
  if (headline) return headline.title;
  return state.clusterLegend.find((item) => item.id === id)?.label || `pattern ${id + 1}`;
}
function selectedHeadline() {
  const interval = state.selectedInterval;
  return interval?.sourceType === "pattern" ? clusterHeadline(interval.patternContext?.cluster_id) : null;
}
// Show the explanation on the curve itself: a grey band for the usual level in
// that 5-minute block, so the stretch that left it is visible at a glance.
function drawHeadlineMarks(row, canvas, range, headline) {
  const span = state.timeline.end - state.timeline.start;
  const x = (time) => canvas.offsetLeft + (Math.min(Math.max(time, state.timeline.start), state.timeline.end) - state.timeline.start) / span * canvas.clientWidth;
  const y = (value) => canvas.offsetTop + canvas.clientHeight - 4 - (value - range.min) / (range.span || 1) * (canvas.clientHeight - 8);
  if (headline.band) {
    const band = document.createElement("div"); band.className = "usual-band";
    const [low, high] = headline.band.map((value) => Math.min(Math.max(value, range.min), range.max));
    band.style.left = `${x(headline.block[0])}px`; band.style.width = `${Math.max(0, x(headline.block[1]) - x(headline.block[0]))}px`;
    band.style.top = `${y(high)}px`; band.style.height = `${Math.max(2, y(low) - y(high))}px`;
    band.title = "Usual level in this 5-minute block (middle half of readings)";
    row.append(band);
  }
}

// Every suggestion uses one neutral ivory; only its strength changes, so a
// darker (more opaque) band always means a more pronounced stretch. Strength
// comes from the scorer's own score on a fixed scale (real sessions score
// about 0.15-1.1), so the same shade means the same thing in every session.
const PATTERN_SCORE_RANGE = [0.15, 1.0];
function patternStrength(id) {
  const score = state.clusterLegend.find((item) => item.id === id)?.centroid?.score ?? PATTERN_SCORE_RANGE[0];
  const [low, high] = PATTERN_SCORE_RANGE; return Math.min(1, Math.max(0, (score - low) / (high - low)));
}
const STRENGTH_WORDS = [[1 / 3, "Mild"], [2 / 3, "Clear"], [Infinity, "Strong"]];
function strengthWord(id) { const strength = patternStrength(id); return STRENGTH_WORDS.find(([limit]) => strength < limit)[1]; }
function patternColor(id, faint, strong) {
  const alpha = faint + (strong - faint) * patternStrength(id); return `rgba(236, 232, 223, ${alpha.toFixed(3)})`;
}

function visiblePatternWindows() {
  return state.clusters.filter((window) => window.end_seconds > state.timeline.start && window.start_seconds < state.timeline.end);
}

// The slim clickable strip lives on the time ruler (like chapter markers in a
// video editor) so a pattern window is always one click away, independent of
// whether the full-height overlay tint below is switched on.
function drawPatternStrip(span) {
  const hasClusters = state.clusters.length > 0; $("pattern-toggle-button").hidden = !hasClusters; $("pattern-legend-row").hidden = !hasClusters;
  const strip = $("pattern-strip"); strip.replaceChildren(); if (!state.clustersVisible) { renderClusterLegend(); return; }
  for (const window of visiblePatternWindows()) {
    const start = Math.max(window.start_seconds, state.timeline.start); const end = Math.min(window.end_seconds, state.timeline.end);
    const segment = document.createElement("button"); segment.className = "cluster-segment";
    segment.style.left = `${(start - state.timeline.start) / span * 100}%`; segment.style.width = `${Math.max(0, (end - start) / span * 100)}%`;
    segment.style.background = patternColor(window.cluster, .3, 1);
    segment.title = `${formatTime(window.start_seconds)}–${formatTime(window.end_seconds)} · ${clusterLabel(window.cluster)} · ${strengthWord(window.cluster)}`;
    segment.addEventListener("click", () => applyPatternFilter(window));
    strip.append(segment);
  }
  renderClusterLegend();
}

// The overlay is a passive color wash sitting behind the event lane and every
// biosignal canvas (negative z-index, see CSS), so a pattern suggestion reads
// directly on top of the trace it was detected from instead of living in its
// own separate row.
function drawPatternOverlay(span) {
  const overlay = $("pattern-overlay"); overlay.replaceChildren(); if (!state.clustersVisible) return;
  const { left, width } = timelineContentBox();
  for (const window of visiblePatternWindows()) {
    const start = Math.max(window.start_seconds, state.timeline.start); const end = Math.min(window.end_seconds, state.timeline.end);
    const band = document.createElement("div"); band.className = "pattern-band";
    band.style.left = `${left + (start - state.timeline.start) / span * width}px`; band.style.width = `${Math.max(0, (end - start) / span * width)}px`;
    band.style.background = patternColor(window.cluster, .04, .2); band.style.borderColor = patternColor(window.cluster, .2, .75);
    overlay.append(band);
  }
}

function renderClusterLegend() {
  const legend = $("cluster-legend"); legend.replaceChildren();
  const used = new Set(state.clusters.map((window) => window.cluster));
  const entries = state.clustersVisible ? state.clusterLegend.filter((item) => used.has(item.id)) : [];
  legend.hidden = entries.length === 0;
  for (const item of entries) {
    const chip = document.createElement("button"); chip.type = "button"; chip.className = "cluster-chip";
    chip.addEventListener("click", () => { const interval = state.clusters.find((window) => window.cluster === item.id); if (interval) applyPatternFilter(interval); });
    const dot = document.createElement("i"); dot.style.background = patternColor(item.id, .3, 1);
    const interval = state.clusters.find((window) => window.cluster === item.id);
    const time = document.createElement("time"); time.textContent = interval ? formatTime(interval.start_seconds).split(".")[0] : "";
    chip.classList.toggle("active", state.selectedInterval?.patternContext?.cluster_id === item.id);
    chip.title = `${strengthWord(item.id)} change`;
    chip.append(dot, time, document.createTextNode(clusterLabel(item.id))); legend.append(chip);
  }
}

function applyPatternFilter(window) {
  const headline = clusterHeadline(window.cluster);
  selectInterval({
    start: headline ? headline.window[0] : window.start_seconds,
    end: headline ? headline.window[1] : window.end_seconds,
    sourceType: "pattern",
    label: clusterLabel(window.cluster),
    patternContext: { label: clusterLabel(window.cluster), cluster_id: window.cluster, window_seconds: state.clusterMeta?.window_seconds, cluster_count: state.clusterLegend.length, algorithm_version: state.clusterMeta?.algorithm_version },
  });
}

$("pattern-toggle-button").addEventListener("click", () => {
  state.clustersVisible = !state.clustersVisible;
  $("pattern-toggle-button").classList.toggle("active", state.clustersVisible); $("pattern-toggle-button").setAttribute("aria-pressed", String(state.clustersVisible));
  drawSignals();
});

for (const button of document.querySelectorAll(".lane-toggle")) {
  button.addEventListener("click", () => {
    const row = button.closest(".timeline-row"); const collapsed = !row.classList.contains("collapsed");
    row.classList.toggle("collapsed", collapsed); button.setAttribute("aria-expanded", String(!collapsed));
  });
}

function drawSignal(canvas, signal, color, include = null) {
  const ctx = canvas.getContext("2d"); const points = signal.points; if (!points.length) return null; const values = points.flatMap((point) => point.slice(1)).concat(include || []);
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1; const x = (time) => (time - state.timeline.start) / (state.timeline.end - state.timeline.start) * canvas.width; const y = (value) => canvas.height - 4 - (value - min) / span * (canvas.height - 8);
  ctx.strokeStyle = color || getComputedStyle(document.documentElement).getPropertyValue("--text").trim(); ctx.lineWidth = 1.25; ctx.beginPath();
  if (signal.mode === "raw") points.forEach((point, index) => index ? ctx.lineTo(x(point[0]), y(point[1])) : ctx.moveTo(x(point[0]), y(point[1]))); else points.forEach((point) => { ctx.moveTo(x(point[0]), y(point[1])); ctx.lineTo(x(point[0]), y(point[2])); }); ctx.stroke();
  return { min, max, span };
}

// EmotiBit LSL streams often omit units, so fall back to the documented ones.
const DEFAULT_UNITS = { HR: "bpm", EA: "µS", TH: "°C", T1: "°C", T0: "°C" };
// Readings shown on hover: precision follows the visible range so small
// skin-conductance changes are not rounded away while heart rate stays whole.
function formatReading(value, unit, range) {
  const decimals = range.span >= 20 ? 0 : range.span >= 2 ? 1 : range.span >= 0.2 ? 2 : 3;
  return `${Number(value).toFixed(decimals)}${unit ? ` ${unit}` : ""}`;
}
function nearestPoint(points, time) {
  let low = 0, high = points.length - 1;
  while (low < high) { const mid = (low + high) >> 1; if (points[mid][0] < time) low = mid + 1; else high = mid; }
  if (low > 0 && Math.abs(points[low - 1][0] - time) <= Math.abs(points[low][0] - time)) low -= 1;
  return points[low];
}
function hideTrackHover() {
  $("timeline-hover").hidden = true;
  showReadingsAt(state.currentTime);
}
// Hovering the signal lanes shows one shared time cursor and every track's
// reading at that instant, so values can be compared across signals.
function showTrackHover(event) {
  if (!state.timeline || !state.trackReadouts?.length) return;
  const { left, width } = timelineContentBox(); const stackLeft = $("timeline-stack").getBoundingClientRect().left;
  const ratio = (event.clientX - stackLeft - left) / width; if (ratio < 0 || ratio > 1) { hideTrackHover(); return; }
  const span = state.timeline.end - state.timeline.start; const time = state.timeline.start + ratio * span;
  const cursor = $("timeline-hover"); cursor.hidden = false; cursor.style.left = `${left + ratio * width}px`;
  $("timeline-hover-time").textContent = formatTime(time);
  showReadingsAt(time);
}
// Every lane's reading at one instant: the hover cursor while pointing at the
// lanes, otherwise the playhead (so scrubbing the video shows its values).
function showReadingsAt(time) {
  if (!state.timeline || !state.trackReadouts?.length) return;
  const span = state.timeline.end - state.timeline.start;
  if (time < state.timeline.start || time > state.timeline.end) { for (const item of state.trackReadouts) { item.value.textContent = ""; item.marker.hidden = true; } return; }
  const tolerance = Math.max(span * 0.01, 1.5);
  for (const item of state.trackReadouts) {
    const point = item.signal.points.length ? nearestPoint(item.signal.points, time) : null;
    if (!point || !item.range || Math.abs(point[0] - time) > tolerance) { item.value.textContent = "—"; item.marker.hidden = true; continue; }
    const envelope = item.signal.mode !== "raw" && point.length > 2;
    const reading = envelope ? (point[1] + point[2]) / 2 : point[1];
    const low = formatReading(point[1], "", item.range), high = envelope ? formatReading(point[2], "", item.range) : low;
    item.value.textContent = low === high ? formatReading(reading, item.unit, item.range) : `${low}–${high}${item.unit ? ` ${item.unit}` : ""}`;
    const canvas = item.canvas;
    item.marker.hidden = canvas.offsetParent === null;
    item.marker.style.left = `${canvas.offsetLeft + (point[0] - state.timeline.start) / span * canvas.clientWidth}px`;
    item.marker.style.top = `${canvas.offsetTop + canvas.clientHeight - 4 - (reading - item.range.min) / (item.range.span || 1) * (canvas.clientHeight - 8)}px`;
  }
}
$("signal-tracks").addEventListener("pointermove", (event) => { state.hoverEvent = { clientX: event.clientX }; showTrackHover(event); });
$("signal-tracks").addEventListener("pointerleave", () => { state.hoverEvent = null; hideTrackHover(); });

function pointerTime(event, element) { const rect = element.getBoundingClientRect(); const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)); return state.timeline.start + ratio * (state.timeline.end - state.timeline.start); }

// A live dashed preview box (with a running duration readout) follows the
// pointer while dragging, instead of the selection only appearing once the
// mouse is released — dragging a precise interval was otherwise a guessing
// game with no feedback until you let go.
function showDragPreview(start, end) {
  const { left, width } = timelineContentBox(); const span = state.timeline.end - state.timeline.start;
  const preview = $("timeline-drag-preview"); preview.hidden = false;
  preview.style.left = `${left + (start - state.timeline.start) / span * width}px`;
  preview.style.width = `${Math.max(1, (end - start) / span * width)}px`;
  $("drag-duration").textContent = `${(end - start).toFixed(1)}s`;
}
function hideDragPreview() { $("timeline-drag-preview").hidden = true; }

function enableIntervalDrag(element) {
  let anchor = null;
  element.addEventListener("pointerdown", (event) => { if (!state.detail || state.detail.status === "recording") return; anchor = pointerTime(event, element); element.setPointerCapture?.(event.pointerId); });
  element.addEventListener("pointermove", (event) => { if (anchor == null) return; const current = pointerTime(event, element); showDragPreview(Math.min(anchor, current), Math.max(anchor, current)); });
  element.addEventListener("pointerup", (event) => { if (anchor == null) return; const current = pointerTime(event, element); const start = Math.min(anchor, current); const end = Math.max(anchor, current); anchor = null; hideDragPreview(); if (end - start < 0.05) { seekTimeline(current); return; } selectInterval({ start, end, sourceType: "manual", label: "Manual selection", patternContext: null }); });
  element.addEventListener("pointercancel", () => { anchor = null; hideDragPreview(); });
}

// Once an interval is selected, its edges become draggable handles so a
// slightly-off selection can be nudged into place instead of redrawn from
// scratch. Disabled while viewing an already-saved bookmark, matching the
// start/end number inputs (also disabled there).
function enableSelectionResize() {
  for (const handle of document.querySelectorAll(".selection-handle")) {
    handle.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      if (!state.selectedInterval || !state.timeline || state.selectedBookmark) return;
      handle.setPointerCapture?.(event.pointerId); const edge = handle.dataset.edge;
      const onMove = (moveEvent) => {
        const time = pointerTime(moveEvent, $("timeline-ruler")); const interval = state.selectedInterval;
        let { start, end } = interval;
        if (edge === "start") start = Math.max(0, Math.min(time, end - 0.05)); else end = Math.max(time, start + 0.05);
        state.selectedInterval = { ...interval, start, end }; updateTimelineSelection();
        $("annotation-start").value = start.toFixed(3); $("annotation-end").value = end.toFixed(3); updateDurationDisplay();
      };
      const onUp = () => { handle.removeEventListener("pointermove", onMove); selectInterval(state.selectedInterval); };
      handle.addEventListener("pointermove", onMove); handle.addEventListener("pointerup", onUp, { once: true });
    });
  }
}
enableSelectionResize();

// Scroll-to-zoom on the timeline, centered on whatever time is under the
// cursor (not the playhead), the way a video editor or map lets you zoom
// toward the thing you're pointing at instead of always toward the middle.
let wheelZoomTimer;
$("timeline-stack").addEventListener("wheel", (event) => {
  if (!state.timeline || !state.detail || state.detail.status === "recording") return;
  event.preventDefault();
  const zoom = $("zoom"); const next = Math.min(Number(zoom.max), Math.max(Number(zoom.min), Number(zoom.value) + (event.deltaY < 0 ? 0.5 : -0.5)));
  if (next === Number(zoom.value)) return; zoom.value = next; updateZoomLabel();
  const center = pointerTime(event, $("timeline-ruler")); const duration = Math.max(1, sessionDuration(state.detail)); const width = duration / zoomFactor();
  const end = Math.min(duration, Math.max(width, center + width / 2)); const start = Math.max(0, end - width);
  state.focusWindow = { start, end: Math.min(duration, start + width) };
  clearTimeout(wheelZoomTimer); wheelZoomTimer = setTimeout(loadTimeline, 60);
}, { passive: false });

function selectInterval(interval, bookmark = null) {
  const duration = sessionDuration(state.detail); const start = Math.max(0, Math.min(duration, Number(interval.start))); const end = Math.max(start, Math.min(duration, Number(interval.end)));
  if (end - start < 0.001) return; state.selectedInterval = { ...interval, start, end }; state.selectedBookmark = bookmark;
  const padding = Math.max(interval.sourceType === "pattern" ? 30 : 1, (end - start) * 0.2); state.focusWindow = { start: Math.max(0, start - padding), end: Math.min(duration, end + padding) }; seekTimeline(start); renderComposer(bookmark); renderMomentCard(); loadTimeline(); renderBookmarks();
}

function clearIntervalSelection() { state.selectedInterval = null; state.selectedBookmark = null; state.focusWindow = null; $("annotation-composer").hidden = true; renderMomentCard(); renderBookmarks(); loadTimeline(); }

function updateDurationDisplay() {
  const start = Number($("annotation-start").value), end = Number($("annotation-end").value);
  $("annotation-duration").innerHTML = Number.isFinite(start) && Number.isFinite(end) && end > start ? `<strong>${(end - start).toFixed(1)}s</strong> selected` : "";
}

// Frequently-used labels render as one-click toggle chips (from the existing
// label vocabulary in Notebook) so tagging a moment usually means clicking,
// not retyping a label you already coined for an earlier one.
function renderLabelSuggestions() {
  const box = $("label-suggestions"); box.replaceChildren();
  const current = new Set(labelsFromInput());
  for (const item of state.bookmarkLabels.slice(0, 12)) {
    const chip = document.createElement("button"); chip.type = "button"; chip.className = `label-suggestion${current.has(item.label) ? " active" : ""}`;
    chip.textContent = item.label;
    chip.addEventListener("click", () => {
      const labels = labelsFromInput(); const index = labels.indexOf(item.label);
      if (index === -1) labels.push(item.label); else labels.splice(index, 1);
      $("annotation-labels").value = labels.join(", "); renderLabelSuggestions();
    });
    box.append(chip);
  }
}

function renderComposer(bookmark = null) {
  const interval = state.selectedInterval; if (!interval) { $("annotation-composer").hidden = true; return; }
  const wasHidden = $("annotation-composer").hidden;
  $("annotation-composer").hidden = false;
  $("annotation-title").textContent = bookmark ? "Edit saved moment" : "Save this moment";
  $("annotation-context").textContent = `${formatTime(interval.start).split(".")[0]}–${formatTime(interval.end).split(".")[0]}`;
  $("annotation-start").value = interval.start.toFixed(3); $("annotation-end").value = interval.end.toFixed(3);
  $("annotation-start").disabled = Boolean(bookmark); $("annotation-end").disabled = Boolean(bookmark);
  updateDurationDisplay();
  $("annotation-note").value = bookmark?.note || ""; $("annotation-labels").value = bookmark?.labels.join(", ") || "";
  renderLabelSuggestions();
  $("annotation-save").textContent = bookmark ? "Save changes" : "Save to History"; $("annotation-delete").hidden = !bookmark;
  if (wasHidden) {
    $("annotation-composer").scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("annotation-note").focus();
  }
}

function labelsFromInput() { return $("annotation-labels").value.split(",").map((item) => item.trim()).filter(Boolean); }

// Sessions default to a time-based name (see defaultSessionTitle) but the
// title is a click-to-edit field: clicking the pencil swaps the heading for
// a text input; Enter/blur commits, Escape reverts without saving.
let cancellingTitleEdit = false;
function startTitleEdit() {
  const session = state.detail || state.selected; if (!session || session.status === "recording") return;
  const input = $("session-title-input"); input.value = defaultSessionTitle(session);
  $("session-title").hidden = true; $("session-title-edit").hidden = true; input.hidden = false;
  input.focus(); input.select();
}
function cancelTitleEdit() {
  cancellingTitleEdit = true; $("session-title-input").hidden = true; $("session-title").hidden = false;
  renderSelected(); cancellingTitleEdit = false;
}
async function commitTitleEdit() {
  if (cancellingTitleEdit) return;
  const session = state.detail || state.selected; const value = $("session-title-input").value.trim();
  $("session-title-input").hidden = true; $("session-title").hidden = false;
  if (!session || value === defaultSessionTitle(session)) { renderSelected(); return; }
  try {
    const updated = await request(`/api/sessions/${session.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_title: value }) });
    if (state.detail?.id === updated.id) state.detail = { ...state.detail, project_title: updated.project_title };
    if (state.selected?.id === updated.id) state.selected = { ...state.selected, project_title: updated.project_title };
    renderSelected(); renderSessions(); toast("Session renamed");
  } catch (error) { toast(error.message); renderSelected(); }
}

async function saveAnnotation(event) {
  event.preventDefault(); if (!state.detail || !state.selectedInterval) return;
  const note = $("annotation-note").value; const labels = labelsFromInput();
  try {
    if (state.selectedBookmark) {
      const updated = await request(`/api/bookmarks/${state.selectedBookmark.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note, labels }) }); state.selectedBookmark = updated; state.selectedInterval.label = updated.pattern_context?.label || "Saved bookmark"; toast("Saved moment updated");
    } else {
      const interval = state.selectedInterval; const created = await request("/api/bookmarks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: state.detail.id, start_seconds: Number($("annotation-start").value), end_seconds: Number($("annotation-end").value), note, labels, source_type: interval.sourceType, pattern_context: interval.patternContext }) }); state.selectedBookmark = created; state.selectedInterval.start = created.start_seconds; state.selectedInterval.end = created.end_seconds; toast("Moment saved to History");
    }
    await Promise.all([loadBookmarks(), loadBookmarkLabels(), loadSessions()]); renderComposer(state.selectedBookmark);
  } catch (error) { toast(error.message); }
}

async function deleteSelectedBookmark() {
  if (!state.selectedBookmark || !confirm("Delete this saved moment from History? The session recording will remain.")) return;
  try { await request(`/api/bookmarks/${state.selectedBookmark.id}`, { method: "DELETE" }); clearIntervalSelection(); await Promise.all([loadBookmarks(), loadBookmarkLabels(), loadSessions()]); toast("Saved moment deleted"); } catch (error) { toast(error.message); }
}

async function openBookmark(bookmark) {
  const session = state.sessions.find((item) => item.id === bookmark.session_id); if (!session) { toast("The source session is no longer available"); return; }
  await selectSession(session); selectInterval({ start: bookmark.start_seconds, end: bookmark.end_seconds, sourceType: bookmark.source_type, label: bookmark.pattern_context?.label || "Saved bookmark", patternContext: bookmark.pattern_context }, bookmark);
}

function seekTimeline(seconds) {
  state.currentTime = Math.max(0, Number(seconds));
  const video = $("session-video"); if (!video.hidden && Math.abs(video.currentTime - (state.currentTime - Number(video.dataset.offset || 0))) > 0.1) video.currentTime = Math.max(0, state.currentTime - Number(video.dataset.offset || 0));
  updateTimelinePlayhead(); updatePlayheadEvent(); if (!state.hoverEvent) showReadingsAt(state.currentTime);
}

// Video scrubbing drives the timeline: when the video time leaves the visible
// window, page the window to it (keeping the zoom), one reload at a time.
let followLoading = false, followPending = false;
async function followVideo(video) {
  seekTimeline(video.currentTime + Number(video.dataset.offset || 0));
  const timeline = state.timeline; if (!timeline || state.detail?.status === "recording") return;
  if (state.currentTime >= timeline.start && state.currentTime <= timeline.end) return;
  if (followLoading) { followPending = true; return; }
  followLoading = true;
  try {
    if (state.focusWindow) { const width = state.focusWindow.end - state.focusWindow.start; const start = Math.max(0, state.currentTime - width / 2); state.focusWindow = { start, end: start + width }; }
    await loadTimeline();
  } finally { followLoading = false; }
  if (followPending) { followPending = false; followVideo(video); }
}

// The FL Studio event at (or just before) the playhead: lit on the event lane
// and named under the video, so scrubbing shows what was happening in FL.
function updatePlayheadEvent() {
  const readout = $("playhead-event"); if (!readout) return;
  let current = null;
  for (const item of state.eventMarkers || []) { item.marker.classList.remove("current"); if (item.event.timeline_seconds <= state.currentTime + 0.05 && (!current || item.event.timeline_seconds > current.event.timeline_seconds)) current = item; }
  const recent = current && state.currentTime - current.event.timeline_seconds <= 30 ? current : null;
  if (recent) recent.marker.classList.add("current");
  readout.hidden = !state.timeline || state.detail?.status === "recording";
  if (!recent) { readout.textContent = `${formatTime(state.currentTime).split(".")[0]} · no FL Studio action in the previous 30 s`; return; }
  const ago = state.currentTime - recent.event.timeline_seconds; const transport = transportMarker(recent.event);
  readout.replaceChildren(Object.assign(document.createElement("time"), { textContent: formatTime(state.currentTime).split(".")[0] }), ` · FL Studio: `, Object.assign(document.createElement("strong"), { textContent: transport ? transport[2] : recent.event.summary }), ago >= 1 ? ` (${Math.round(ago)} s earlier)` : "");
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
async function loadBookmarks() { const params = new URLSearchParams(); if ($("bookmark-search").value) params.set("search", $("bookmark-search").value); if ($("bookmark-label-filter").value) params.set("label", $("bookmark-label-filter").value); state.bookmarks = await request(`/api/bookmarks?${params}`); renderBookmarks(); }
async function loadBookmarkLabels() { state.bookmarkLabels = await request("/api/bookmark-labels"); renderBookmarkLabels(); }
async function selectSession(session) { location.hash = "review"; showWorkspaceView(); state.selected = session; state.detail = await request(`/api/sessions/${session.id}`); state.clusters = []; state.clusterMeta = null; state.selectedInterval = null; state.selectedBookmark = null; state.focusWindow = null; state.selectedTrackTags = null; $("annotation-composer").hidden = true; renderSessions(); renderBookmarks(); renderSelected(); await Promise.all([loadEvents(), loadBiosignalClusters()]); drawSignals(); }

async function loadBiosignalClusters() {
  if (!state.detail || state.detail.status === "recording") { state.clusters = []; state.clusterLegend = []; return; }
  try {
    const result = await request(`/api/sessions/${state.detail.id}/biosignal-clusters`);
    state.clusters = result.windows; state.clusterLegend = result.clusters; state.clusterMeta = { algorithm_version: result.algorithm_version, window_seconds: result.window_seconds };
  } catch { state.clusters = []; state.clusterLegend = []; state.clusterMeta = null; }
}
// Moment card: one tile per signal (arrow + sparkline against the usual band)
// and one for FL Studio activity, so a suggestion reads at a glance.
const SIGNAL_TILES = { HR: { icon: "heart", name: "Heart rate", color: "--sig-heart" }, EA: { icon: "bolt", name: "Skin response (EDA)", color: "--sig-eda", hint: "Higher = body more tense or excited" }, T1: { icon: "thermo", name: "Temperature", color: "--sig-temp" }, TH: { icon: "thermo", name: "Temperature", color: "--sig-temp" } };
// Tile icons share one 8x8 pixel grid ("X" = lit, "o" = highlight) so every
// icon renders at the same whole-pixel scale (2 screen px per cell at 16 px).
const PIXEL_ICONS = {
  heart: [".XX..XX.", "XoXXXXXX", "XXXXXXXX", "XXXXXXXX", ".XXXXXX.", "..XXXX..", "...XX...", "........"],
  bolt: ["....XXX.", "...XXX..", "..XXX...", ".XXXXXX.", "...XXX..", "..XXX...", ".XXX....", ".X......"],
  thermo: ["...XX...", "...XX.X.", "...XX...", "...XX.X.", "...XX...", "..XXXX..", "..XoXX..", "..XXXX.."],
  keys: ["X..X..XX", "X..X..XX", "X..X..XX", "X..X..XX", "XX.XX.XX", "XX.XX.XX", "XX.XX.XX", "XXXXXXXX"],
};
function pixelIcon(name, color) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 8 8"); svg.setAttribute("class", "pixel-icon"); svg.setAttribute("aria-hidden", "true"); svg.style.color = color;
  PIXEL_ICONS[name].forEach((row, y) => [...row].forEach((cell, x) => {
    if (cell === ".") return; const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    Object.entries({ x, y, width: 1, height: 1, fill: cell === "o" ? "#fff" : "currentColor", "fill-opacity": cell === "o" ? .8 : 1 }).forEach(([key, value]) => rect.setAttribute(key, value)); svg.append(rect);
  }));
  return svg;
}
// Tile icons are switched off for now; flip this to bring the pixel icons back.
const SHOW_TILE_ICONS = false;
function tileName(icon, color, text) { const name = document.createElement("span"); name.className = "moment-tile-name"; if (SHOW_TILE_ICONS) name.append(pixelIcon(icon, color)); name.append(text); return name; }
const TREND_TEXT = { up2: ["↑↑", "much higher"], up: ["↑", "higher"], steady: ["→", "steady"], down: ["↓", "lower"], down2: ["↓↓", "much lower"] };
function formatSignal(tag, value) { const decimals = tag === "HR" ? 0 : ["T1", "TH"].includes(tag) ? 1 : 2; return `${Number(value).toFixed(decimals)} ${DEFAULT_UNITS[tag] || ""}`.trim(); }
// The grey band spans the whole sparkline, or only the 30 s before the stretch
// when that is what the signal is compared with (skin responses).
function drawSparkline(canvas, points, band, window, color, bandFrom = null) {
  const ratio = window.devicePixelRatio || 1; const width = canvas.clientWidth || 200, height = canvas.clientHeight || 44;
  canvas.width = width * ratio; canvas.height = height * ratio; const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio);
  if (!points.length) return;
  const start = points[0][0], end = points[points.length - 1][0], span = Math.max(1e-6, end - start);
  const values = points.map((point) => point[1]).concat(band); const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const x = (time) => (time - start) / span * width; const y = (value) => height - 3 - (value - min) / range * (height - 6);
  const bandLeft = bandFrom === null ? 0 : Math.max(0, x(bandFrom)); const bandRight = bandFrom === null ? width : x(window[0]);
  ctx.fillStyle = "rgba(255,255,255,.12)"; ctx.fillRect(bandLeft, y(band[1]), Math.max(1, bandRight - bandLeft), Math.max(1, y(band[0]) - y(band[1])));
  ctx.fillStyle = "rgba(255,255,255,.08)"; ctx.fillRect(x(window[0]), 0, Math.max(1, x(window[1]) - x(window[0])), height);
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
  points.forEach(([time, value], index) => index ? ctx.lineTo(x(time), y(value)) : ctx.moveTo(x(time), y(value))); ctx.stroke();
}
function activityTile(start, end) {
  const inside = state.events.filter((event) => event.timeline_seconds >= start && event.timeline_seconds < end).sort((a, b) => a.timeline_seconds - b.timeline_seconds);
  const tile = document.createElement("div"); tile.className = "moment-tile activity";
  const head = document.createElement("div"); head.className = "moment-tile-head"; head.append(tileName("keys", "var(--text)", "In FL Studio")); tile.append(head);
  const icons = inside.map(transportMarker).filter(Boolean);
  const edits = inside.filter((event) => ["pattern", "channel", "mixer", "plugin"].includes(event.category) && event.summary);
  if (icons.length) {
    const strip = document.createElement("div"); strip.className = "moment-transport";
    for (const [kind, glyph, label] of icons.slice(0, 10)) { const mark = document.createElement("span"); mark.className = `transport-marker ${kind}`; mark.textContent = glyph; mark.title = label; strip.append(mark); }
    tile.append(strip);
  }
  const plays = icons.filter(([kind]) => kind === "playback-start").length;
  const lines = [];
  if (plays > 1) lines.push(`Replayed ${plays}×`); else if (plays) lines.push("Started playback");
  if (icons.some(([kind]) => kind === "recording-start")) lines.push("Recording on");
  if (edits.length) lines.push(`→ ${edits[edits.length - 1].summary.replace(/^Selected /, "")}`);
  const text = document.createElement("div"); text.className = "moment-tile-sub"; text.textContent = lines.join(" · ") || "No playback or edits";
  tile.append(text);
  return tile;
}
let momentCardToken = 0;
async function renderMomentCard() {
  const token = ++momentCardToken; const card = $("moment-card"); const headline = selectedHeadline();
  card.hidden = !headline; if (!headline || !state.detail) return;
  const [start, end] = headline.window; const sessionId = state.detail.id;
  $("moment-card-time").textContent = `${formatTime(start).split(".")[0]}–${formatTime(end).split(".")[0]}`;
  $("moment-card-title").textContent = headline.title;
  const listen = $("moment-card-listen"); listen.hidden = !heartbeatSupported();
  listen.onclick = async () => { const video = $("session-video"); if (!heartbeat.enabled) await toggleHeartbeat(true); seekTimeline(Math.max(0, start - 5)); video.scrollIntoView({ behavior: "smooth", block: "center" }); video.play().catch(() => {}); };
  const tiles = $("moment-tiles"); tiles.replaceChildren();
  const canvases = [];
  for (const signal of headline.signals || []) {
    const meta = SIGNAL_TILES[signal.tag]; if (!meta) continue;
    const [arrow, word] = TREND_TEXT[signal.trend] || TREND_TEXT.steady;
    const tile = document.createElement("div"); tile.className = `moment-tile${signal.flagged ? " flagged" : ""}`;
    tile.style.setProperty("--tile-color", `var(${meta.color})`);
    const head = document.createElement("div"); head.className = "moment-tile-head";
    head.append(tileName(meta.icon, `var(${meta.color})`, meta.name));
    const trend = document.createElement("span"); trend.className = `moment-trend ${signal.trend}`; trend.textContent = arrow; trend.title = word; head.append(trend);
    const canvas = document.createElement("canvas"); canvas.className = "moment-spark";
    const sub = document.createElement("div"); sub.className = "moment-tile-sub";
    const compared = signal.baseline === "before" ? "before" : "usual";
    const reading = signal.baseline === "before" && signal.trend !== "steady" ? (signal.trend.startsWith("up") ? "peak " : "low ") : "";
    sub.textContent = signal.trend === "steady" ? `steady · ${formatSignal(signal.tag, signal.value)}` : `${reading}${formatSignal(signal.tag, signal.value)} · ${compared} ${formatSignal(signal.tag, signal.usual)}`;
    tile.append(head, canvas, sub);
    if (meta.hint) tile.append(Object.assign(document.createElement("div"), { className: "moment-tile-hint", textContent: meta.hint }));
    tiles.append(tile); canvases.push([canvas, signal, meta]);
  }
  tiles.append(activityTile(start, end));
  const context = 30; const params = new URLSearchParams({ start: Math.max(0, start - context), end: end + context, pixels: 240, tracks: canvases.map(([, signal]) => signal.tag).join(",") });
  let result; try { result = await request(`/api/sessions/${sessionId}/timeline?${params}`); } catch { return; }
  if (token !== momentCardToken) return;
  for (const [canvas, signal, meta] of canvases) {
    const data = result.signals.find((item) => item.type_tag === signal.tag);
    const points = (data?.points || []).map((point) => [point[0], data.mode === "raw" ? point[1] : (point[1] + point[2]) / 2]);
    drawSparkline(canvas, points, signal.band, headline.window, getComputedStyle(document.documentElement).getPropertyValue(meta.color).trim(), signal.baseline === "before" ? headline.window[0] - 30 : null);
  }
}

// Heartbeat sonification: while the session video plays, synthesize a
// "lub-dub" at the recorded heart rate so the body can be heard, not read.
const heartbeat = { enabled: false, context: null, master: null, points: null, range: null, sessionId: null, nextBeat: null, lastTime: null, timer: null };
function heartbeatSupported() { return !!state.detail?.signals?.some((signal) => signal.type_tag === "HR") && !$("session-video").hidden; }
function updateListenControl() {
  $("listen-control").hidden = !heartbeatSupported();
  if ($("listen-control").hidden && heartbeat.enabled) toggleHeartbeat(false);
}
async function loadHeartbeatPoints() {
  if (heartbeat.sessionId === state.detail.id && heartbeat.points) return;
  const params = new URLSearchParams({ start: 0, end: Math.max(1, sessionDuration(state.detail)), pixels: 5000, tracks: "HR" });
  const result = await request(`/api/sessions/${state.detail.id}/timeline?${params}`);
  const signal = result.signals.find((item) => item.type_tag === "HR");
  heartbeat.points = (signal?.points || []).map((point) => [point[0], signal.mode === "raw" ? point[1] : (point[1] + point[2]) / 2]);
  heartbeat.sessionId = state.detail.id;
  // Pitch spans this session's own 5th–95th percentile, so even small changes are audible.
  const sorted = heartbeat.points.map((point) => point[1]).filter((bpm) => bpm >= 35 && bpm <= 200).sort((a, b) => a - b);
  heartbeat.range = sorted.length ? [sorted[Math.floor(sorted.length * 0.05)], sorted[Math.floor(sorted.length * 0.95)]] : null;
}
// The session's usual range spans one octave from 55 Hz; peaks beyond it keep
// rising (up to 1.5 octaves) because those are the moments worth hearing.
function heartbeatPitch(bpm) {
  const [low, high] = heartbeat.range || [bpm, bpm];
  const position = high > low ? Math.min(1.5, Math.max(-0.25, (bpm - low) / (high - low))) : 0.5;
  return 55 * 2 ** position;
}
// Median of readings within ±2 s keeps a single glitchy reading from stuttering the beat.
function heartRateAt(time) {
  const points = heartbeat.points; if (!points?.length) return null;
  const nearest = nearestPoint(points, time); if (Math.abs(nearest[0] - time) > 5) return null;
  let low = 0, high = points.length; while (low < high) { const mid = (low + high) >> 1; if (points[mid][0] < time - 2) low = mid + 1; else high = mid; }
  const nearby = []; for (let index = low; index < points.length && points[index][0] <= time + 2; index++) nearby.push(points[index][1]);
  const values = (nearby.length ? nearby : [nearest[1]]).sort((a, b) => a - b);
  const bpm = values[Math.floor(values.length / 2)];
  return bpm >= 35 && bpm <= 200 ? bpm : null;
}
function playThump(at, strength, pitch) {
  const ctx = heartbeat.context;
  const body = ctx.createOscillator(); const bodyGain = ctx.createGain();
  body.type = "sine"; body.frequency.setValueAtTime(pitch, at); body.frequency.exponentialRampToValueAtTime(pitch * 0.55, at + 0.12);
  bodyGain.gain.setValueAtTime(0.0001, at); bodyGain.gain.exponentialRampToValueAtTime(strength, at + 0.008); bodyGain.gain.exponentialRampToValueAtTime(0.0001, at + 0.18);
  body.connect(bodyGain).connect(heartbeat.master); body.start(at); body.stop(at + 0.2);
  // A short filtered click gives the thump an attack laptop speakers can reproduce.
  const click = ctx.createBufferSource(); const buffer = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * 0.03), ctx.sampleRate);
  const data = buffer.getChannelData(0); for (let index = 0; index < data.length; index++) data[index] = (Math.random() * 2 - 1) * (1 - index / data.length);
  click.buffer = buffer; const filter = ctx.createBiquadFilter(); filter.type = "lowpass"; filter.frequency.value = 900;
  const clickGain = ctx.createGain(); clickGain.gain.value = strength * 0.35;
  click.connect(filter).connect(clickGain).connect(heartbeat.master); click.start(at);
}
function pulseHeartLane(delaySeconds) {
  setTimeout(() => {
    const dot = document.querySelector('.signal-track[data-tag="HR"] .lane-dot'); if (!dot) return;
    dot.classList.remove("beat"); void dot.offsetWidth; dot.classList.add("beat");
  }, Math.max(0, delaySeconds * 1000));
}
function scheduleHeartbeats() {
  const video = $("session-video"); if (!heartbeat.enabled || video.paused || video.hidden) { heartbeat.nextBeat = null; return; }
  const now = video.currentTime + Number(video.dataset.offset || 0); const rate = video.playbackRate || 1;
  // Re-align after a seek, a jump, or resuming playback.
  if (heartbeat.nextBeat === null || heartbeat.lastTime === null || now < heartbeat.lastTime - 0.05 || now - heartbeat.lastTime > 1 || heartbeat.nextBeat < now - 0.5) heartbeat.nextBeat = now + 0.05;
  heartbeat.lastTime = now;
  while (heartbeat.nextBeat < now + 0.2 * rate) {
    const bpm = heartRateAt(heartbeat.nextBeat);
    if (bpm === null) { heartbeat.nextBeat += 0.5; continue; }
    const interval = 60 / bpm; const at = heartbeat.context.currentTime + Math.max(0, (heartbeat.nextBeat - now) / rate);
    const pitch = heartbeatPitch(bpm);
    playThump(at, 0.9, pitch); playThump(at + Math.min(0.28, interval * 0.33) / rate, 0.55, pitch * 1.17);
    pulseHeartLane(at - heartbeat.context.currentTime);
    heartbeat.nextBeat += interval;
  }
}
async function toggleHeartbeat(on = !heartbeat.enabled) {
  heartbeat.enabled = on;
  $("listen-button").classList.toggle("active", on); $("listen-button").setAttribute("aria-pressed", String(on));
  clearInterval(heartbeat.timer); heartbeat.nextBeat = null; heartbeat.lastTime = null;
  if (!on) return;
  if (!heartbeat.context) {
    heartbeat.context = new AudioContext(); heartbeat.master = heartbeat.context.createGain();
    heartbeat.master.connect(heartbeat.context.destination);
  }
  heartbeat.master.gain.value = Number($("listen-volume").value);
  await heartbeat.context.resume();
  try { await loadHeartbeatPoints(); } catch { toast("Heart-rate data could not be loaded"); toggleHeartbeat(false); return; }
  heartbeat.timer = setInterval(scheduleHeartbeats, 25);
  if ($("session-video").paused) toast("Play the video to hear your heartbeat");
}
$("listen-button").addEventListener("click", () => toggleHeartbeat());
$("listen-volume").addEventListener("input", (event) => { if (heartbeat.master) heartbeat.master.gain.value = Number(event.target.value); });

async function loadEvents() { if (!state.selected) return; state.events = await request(`/api/sessions/${state.selected.id}/events`); }
async function refreshAll() { await Promise.all([loadHealth(), loadSharing(), loadSessions(), loadBookmarkLabels()]); await Promise.all([loadCloudSessions(), loadBookmarks()]); if (state.selected) { const view = location.hash; await selectSession(state.selected); location.hash = view; showWorkspaceView(); } }

$("record-button").addEventListener("click", async () => { try { state.health?.active_session_id ? await stopRecording() : await startRecording(); } catch (error) { toast(error.message); } });
$("refresh-sessions").addEventListener("click", refreshAll);
$("annotation-composer").addEventListener("submit", saveAnnotation); $("annotation-cancel").addEventListener("click", clearIntervalSelection); $("annotation-delete").addEventListener("click", deleteSelectedBookmark);
$("annotation-composer").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); clearIntervalSelection(); }
  else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); $("annotation-composer").requestSubmit(); }
});
$("annotation-labels").addEventListener("input", renderLabelSuggestions);
for (const id of ["annotation-start", "annotation-end"]) {
  $(id).addEventListener("input", updateDurationDisplay);
  $(id).addEventListener("change", () => {
    if (!state.selectedInterval || state.selectedBookmark) return;
    const start = Number($("annotation-start").value), end = Number($("annotation-end").value);
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      const note = $("annotation-note").value, labels = $("annotation-labels").value;
      selectInterval({ ...state.selectedInterval, start, end });
      $("annotation-note").value = note; $("annotation-labels").value = labels;
    }
  });
}
let bookmarkSearchTimer; $("bookmark-search").addEventListener("input", () => { clearTimeout(bookmarkSearchTimer); bookmarkSearchTimer = setTimeout(loadBookmarks, 250); }); $("bookmark-label-filter").addEventListener("change", loadBookmarks);
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
$("delete-button").addEventListener("click", async () => { if (!state.selected || !confirm("Permanently delete this local recording, its bookmarks, and its media?")) return; await request(`/api/sessions/${state.selected.id}`, { method: "DELETE" }); state.selected = null; state.detail = null; state.events = []; clearIntervalSelection(); renderSelected(); await Promise.all([loadSessions(), loadBookmarks(), loadBookmarkLabels()]); location.hash = "sessions"; toast("Session deleted"); });
let zoomTimer; $("zoom").addEventListener("input", () => { state.focusWindow = null; updateZoomLabel(); clearTimeout(zoomTimer); zoomTimer = setTimeout(loadTimeline, 60); });
$("zoom-in").addEventListener("click", () => stepZoom(0.5)); $("zoom-out").addEventListener("click", () => stepZoom(-0.5));
for (const type of ["timeupdate", "seeking", "seeked"]) $("session-video").addEventListener(type, (event) => followVideo(event.target));
const live = new EventSource("/api/live"); live.onmessage = async (message) => { const item = JSON.parse(message.data); if (item.health) { state.health = { ...state.health, ...item.health }; renderHealth(); } if (item.type === "emotibit_status") { state.health = { ...state.health, emotibit: item.emotibit }; state.liveDescriptors = item.emotibit.streams || []; renderHealth(); if (state.detail?.status === "recording") renderTrackPicker(); } if (["session_started", "session_stopped"].includes(item.type)) await refreshAll(); if (item.type === "emotibit_samples") appendLiveSamples(item); if (item.type === "event" && state.selected?.id === item.event.session_id) { state.events.unshift(item.event); if (state.detail?.status === "recording") renderLiveSignals(); } }; live.onerror = () => $("connection").classList.remove("connected");
Promise.all([loadHealth(), loadSharing(), loadSessions(), loadBookmarkLabels()]).then(() => Promise.all([loadCloudSessions(), loadBookmarks()])).catch((error) => toast(error.message));
