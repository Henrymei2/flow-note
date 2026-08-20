const state = { health: null, sessions: [], selected: null, events: [] };
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
  const element = $("toast");
  element.textContent = message;
  element.classList.add("visible");
  setTimeout(() => element.classList.remove("visible"), 2800);
}

function renderHealth() {
  const health = state.health || {};
  const connected = Boolean(health.connected && health.compatible);
  $("connection").classList.toggle("connected", connected);
  const compatibilityLabels = {
    api_unsupported: "Unsupported FL Studio API",
    observer_schema_mismatch: "FL observer schema mismatch",
    observer_outdated: "FL observer update required",
  };
  $("connection-label").textContent = !health.connected
    ? "FL Studio disconnected"
    : health.compatible
      ? `FL Studio connected · observer ${health.observer?.observer_build}`
      : compatibilityLabels[health.compatibility_reason] || "Incompatible FL observer";
  const active = health.active_session_id;
  $("record-button").disabled = !connected;
  $("record-button").classList.toggle("recording", Boolean(active));
  $("record-label").textContent = active ? "Stop recording" : "Start recording";
  $("record-help").textContent = active
    ? "Your local timeline is recording."
    : connected
      ? "Recording starts only when you choose."
      : health.connected && health.compatibility_reason
        ? `Reinstall and reload Activity Recorder. Required build: ${health.required_observer?.build || "current"}.`
        : "Connect the FL Studio observer to begin.";
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function renderSessions() {
  const list = $("session-list");
  list.replaceChildren();
  $("sessions-empty").hidden = state.sessions.length > 0;
  for (const session of state.sessions) {
    const button = document.createElement("button");
    button.className = `session-item ${state.selected?.id === session.id ? "active" : ""}`;
    const title = document.createElement("strong");
    title.textContent = session.project_title || "Untitled project";
    const details = document.createElement("span");
    details.textContent = `${formatDate(session.started_at)} · ${session.event_count} events`;
    button.append(title, details);
    button.addEventListener("click", () => selectSession(session));
    list.append(button);
  }
}

function renderSelected() {
  const session = state.selected;
  $("export-button").classList.toggle("disabled", !session);
  $("delete-button").disabled = !session || session.status === "recording";
  if (!session) return;
  $("session-status").textContent = session.status.toUpperCase();
  $("session-title").textContent = session.project_title || "Untitled project";
  $("session-time").textContent = `${formatDate(session.started_at)} · ${session.event_count} timeline events`;
  $("export-button").href = `/api/sessions/${session.id}/export`;
}

function renderEvents() {
  const list = $("event-list");
  list.replaceChildren();
  $("timeline-empty").hidden = state.events.length > 0;
  for (const event of state.events) {
    const row = document.createElement("article");
    row.className = "event";
    const time = document.createElement("time");
    time.className = "event-time";
    time.textContent = new Date(event.occurred_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const dot = document.createElement("div"); dot.className = "event-dot";
    const body = document.createElement("div"); body.className = "event-body";
    const summary = document.createElement("strong"); summary.textContent = event.summary;
    const detail = document.createElement("p"); detail.className = "muted";
    detail.textContent = [event.category, event.entity_type, event.entity_key].filter(Boolean).join(" · ");
    body.append(summary, detail);
    const badge = document.createElement("span"); badge.className = `badge ${event.confidence}`;
    badge.textContent = event.confidence.replace("_", " ");
    row.append(time, dot, body, badge); list.append(row);
  }
}

async function loadHealth() { state.health = await request("/api/health"); renderHealth(); }
async function loadSessions() {
  state.sessions = await request("/api/sessions");
  if (state.selected) state.selected = state.sessions.find((s) => s.id === state.selected.id) || null;
  renderSessions(); renderSelected();
}
async function selectSession(session) {
  state.selected = session; renderSessions(); renderSelected();
  await Promise.all([loadCategories(), loadEvents()]);
}
async function loadCategories() {
  const select = $("category");
  const selected = select.value;
  const categories = state.selected
    ? await request(`/api/sessions/${state.selected.id}/categories`)
    : [];
  select.replaceChildren(new Option("All activity", ""));
  for (const category of categories) select.append(new Option(category, category));
  select.value = categories.includes(selected) ? selected : "";
}
async function loadEvents() {
  if (!state.selected) return;
  const params = new URLSearchParams();
  if ($("category").value) params.set("category", $("category").value);
  if ($("confidence").value) params.set("confidence", $("confidence").value);
  if ($("search").value) params.set("search", $("search").value);
  state.events = await request(`/api/sessions/${state.selected.id}/events?${params}`);
  renderEvents();
}

$("record-button").addEventListener("click", async () => {
  try {
    if (state.health?.active_session_id) {
      await request(`/api/sessions/${state.health.active_session_id}/stop`, { method: "POST" });
      toast("Recording stopped");
    } else {
      const session = await request("/api/sessions", { method: "POST" });
      state.selected = session; await loadCategories(); toast("Recording started");
    }
    await Promise.all([loadHealth(), loadSessions()]);
    if (state.selected) await loadEvents();
  } catch (error) { toast(error.message); }
});
$("refresh-sessions").addEventListener("click", loadSessions);
$("delete-button").addEventListener("click", async () => {
  if (!state.selected || !confirm("Permanently delete this local recording?")) return;
  await request(`/api/sessions/${state.selected.id}`, { method: "DELETE" });
  state.selected = null; state.events = []; renderEvents(); renderSelected(); await loadSessions(); toast("Session deleted");
});
for (const id of ["category", "confidence"]) $(id).addEventListener("change", loadEvents);
let searchTimer;
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadEvents, 250); });

const live = new EventSource("/api/live");
live.onmessage = async (message) => {
  const item = JSON.parse(message.data);
  if (item.health) { state.health = item.health; renderHealth(); }
  if (["session_started", "session_stopped"].includes(item.type)) await Promise.all([loadHealth(), loadSessions()]);
  if (item.type === "event" && state.selected?.id === item.event.session_id) {
    state.events.unshift(item.event); renderEvents();
    if (![...$("category").options].some((option) => option.value === item.event.category)) {
      $("category").append(new Option(item.event.category, item.event.category));
    }
  }
};
live.onerror = () => { $("connection").classList.remove("connected"); };

Promise.all([loadHealth(), loadSessions()]).catch((error) => toast(error.message));
