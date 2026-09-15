"use strict";

const byId = (id) => document.getElementById(id);
const tokenDialog = byId("token-dialog");
const tokenForm = byId("token-form");
let apiToken = sessionStorage.getItem("phono-console-token") || "";
let pollTimer = null;
let requestActive = false;

const routeLabels = {
  idle: ["Ready", "IDLE", "Waiting for an audio source"],
  local_phono: ["Playing record", "LOCAL FALLBACK", "Turntable → console speakers (MA unavailable)"],
  local_bluetooth: ["Bluetooth", "LOCAL FALLBACK", "Bluetooth → console speakers (MA unavailable)"],
  ma_playback: ["Music Assistant", "MA PLAYBACK", "Network audio → console speakers"],
  distributed_phono: ["Playing record", "DOWNSTAIRS", "Turntable → Music Assistant → Downstairs"],
  distributed_bluetooth: ["Bluetooth", "DOWNSTAIRS", "Bluetooth → Music Assistant → Downstairs"],
};

function headers(json = false) {
  const value = {};
  if (apiToken) value.Authorization = `Bearer ${apiToken}`;
  if (json) value["Content-Type"] = "application/json";
  return value;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: {...headers(Boolean(options.body)), ...(options.headers || {})},
  });
  if (response.status === 401) {
    showTokenDialog();
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    const detail = (await response.text()).trim();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function showTokenDialog(message = "") {
  byId("token-error").textContent = message;
  if (!tokenDialog.open) tokenDialog.showModal();
  setTimeout(() => byId("token-input").focus(), 0);
}

function setDot(id, on, pending = false) {
  const dot = byId(id);
  dot.className = `status-dot ${pending ? "warn" : on ? "on" : "offline"}`;
}

function formatDb(value) {
  if (!Number.isFinite(value) || value <= -119.9) return "−∞";
  return `${value.toFixed(1)}`;
}

function meterPosition(value) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, ((value + 60) / 60) * 100));
}

function setChannel(prefix, data, available = true) {
  const track = byId(`${prefix}-rms`).parentElement;
  track.classList.toggle("unavailable", !available);
  if (!available || !data) {
    byId(`${prefix}-rms`).style.width = "0%";
    byId(`${prefix}-peak`).style.left = "0%";
    byId(`${prefix}-value`).textContent = "— dBFS";
    byId(`${prefix}-rms-value`).textContent = "RMS —";
    byId(`${prefix}-max`).textContent = "MAX —";
    return;
  }
  const peak = Number(data.peak_dbfs);
  const rms = Number(data.rms_dbfs);
  byId(`${prefix}-rms`).style.width = `${meterPosition(rms)}%`;
  byId(`${prefix}-peak`).style.left = `${meterPosition(peak)}%`;
  byId(`${prefix}-value`).textContent = `${formatDb(peak)} dBFS`;
  byId(`${prefix}-rms-value`).textContent = `RMS ${formatDb(rms)}`;
  byId(`${prefix}-max`).textContent = `MAX ${formatDb(Number(data.max_peak_dbfs))}`;
  const clip = byId(`${prefix}-clip`);
  if (clip) clip.classList.toggle("active", Boolean(data.clipped));
}

function setFlag(dot, enabled) {
  byId(dot).className = `status-dot ${enabled ? "on" : ""}`;
}

function renderRoute(status, wholeHouse) {
  const route = status?.route || "idle";
  const labels = routeLabels[route] || [route, route.toUpperCase(), "Unknown route"];
  byId("route-title").textContent = labels[0];
  byId("route-name").textContent = labels[1];
  byId("route-detail").textContent = labels[2];
  setFlag("phono-dot", Boolean(status?.phono_active));
  setFlag("bluetooth-dot", Boolean(status?.bluetooth_active));
  setFlag("ma-play-dot", Boolean(status?.ma_playing));
  setFlag("house-dot", status?.distribution === "ma_downstairs");
}

function renderMeters(status, levels) {
  setChannel("in-l", levels?.left, Boolean(levels));
  setChannel("in-r", levels?.right, Boolean(levels));

  const route = status?.route;
  if (route === "local_phono" && levels) {
    setChannel("out-l", levels.left, true);
    setChannel("out-r", levels.right, true);
    byId("output-badge").textContent = "INPUT MIRROR";
    byId("output-badge").className = "source-badge active";
    byId("output-note").textContent = "Digital loopback is unity gain; shown from the same PCM input (estimated output).";
  } else if (status?.ma_playing) {
    setChannel("out-l", null, false);
    setChannel("out-r", null, false);
    byId("output-badge").textContent = "UNAVAILABLE";
    byId("output-badge").className = "source-badge unavailable";
    byId("output-note").textContent = "Sendspin is rendering output; its player does not expose live PCM levels.";
  } else {
    const silence = {peak_dbfs: -120, rms_dbfs: -120, max_peak_dbfs: -120};
    setChannel("out-l", silence, true);
    setChannel("out-r", silence, true);
    byId("output-badge").textContent = "IDLE";
    byId("output-badge").className = "source-badge";
    byId("output-note").textContent = "No local output route is active.";
  }
}

function renderConnections(data) {
  const ma = data.music_assistant || {};
  const source = data.sendspin_source || {};
  const system = data.system || {};
  const health = data.health || {};
  const components = data.components || {};
  const capture = components.capture || {};
  const output = components.local_output || {};
  const player = components.sendspin_player || {};
  setDot("health-dot", Boolean(health.operational), health.status === "degraded");
  byId("health-status").textContent = health.operational ? "Operational" : "Degraded";
  setDot("capture-dot", capture.status === "ok", capture.status === "degraded");
  byId("capture-status").textContent = capture.message || "Unknown";
  setDot("output-dot", output.status === "ok", output.status === "degraded");
  byId("output-status").textContent = output.message || "Unknown";
  setDot("player-dot", player.status === "ok", player.status === "degraded");
  byId("player-status").textContent = player.message || "Unknown";
  setDot("ma-dot", Boolean(ma.connected));
  byId("ma-status").textContent = ma.connected ? "Connected" : (ma.error ? "Offline" : "Connecting");
  setDot("source-dot", Boolean(source.connected));
  byId("source-status").textContent = source.connected ? "Connected" : "Offline";
  byId("source-pairing-token").textContent = source.pairing_token || "—";
  setDot("stream-dot", Boolean(source.streaming), source.connected && !source.streaming);
  byId("stream-status").textContent = source.streaming ? "Streaming" : "Idle";
  byId("host-name").textContent = system.hostname || "—";
  byId("host-address").textContent = (system.addresses || []).join(", ") || "—";
  byId("app-version").textContent = `${system.version || "—"} · ${Math.floor(Number(health.uptime_seconds || 0))}s up`;
  byId("audio-devices").textContent = `${system.capture_device || "—"} → ${system.playback_device || "—"}`;
}

function renderEvents(events) {
  const list = byId("event-list");
  const recent = (events || []).slice(-6).reverse();
  list.replaceChildren();
  if (!recent.length) {
    const item = document.createElement("li");
    item.className = "muted";
    item.textContent = "No events yet";
    list.append(item);
    return;
  }
  for (const event of recent) {
    const item = document.createElement("li");
    const time = document.createElement("time");
    const date = new Date(event.timestamp);
    time.textContent = Number.isNaN(date.valueOf()) ? "—" : date.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
    const name = document.createElement("span");
    const detail = event.details?.error || event.details?.device || "";
    name.textContent = `${String(event.event || "event").replaceAll("_", " ")}${detail ? ` — ${detail}` : ""}`;
    item.append(time, name);
    list.append(item);
  }
}

function render(data) {
  setDot("live-dot", Boolean(data.health?.operational), !data.health?.operational);
  byId("live-label").textContent = data.health?.operational ? "OPERATIONAL" : "DEGRADED";
  renderRoute(data.status, data.whole_house_requested);
  renderMeters(data.status, data.input_levels);
  renderConnections(data);
  renderEvents(data.events);
  const pairing = Boolean(data.bluetooth?.pairing);
  const localOnly = Boolean(data.local_playback_only);
  byId("local-only").classList.toggle("active", localOnly);
  byId("local-only").textContent = localOnly ? "LOCAL ONLY: ON" : "LOCAL ONLY";
  byId("pairing-open").disabled = pairing;
  byId("pairing-close").disabled = !pairing;
  byId("control-result").textContent = localOnly
    ? "Local-only mode is on. Phono and Bluetooth bypass Music Assistant."
    : pairing
    ? "Bluetooth pairing is open temporarily. Select PhonoConsole on your phone."
    : "Sources are selected automatically from their signal.";
}

async function setPairing(enabled) {
  try {
    await api("/v1/bluetooth/pairing", {method: "PUT", body: JSON.stringify({enabled})});
    await poll();
  } catch (error) {
    byId("control-result").textContent = error.message;
  }
}

async function setLocalOnly() {
  const enabled = !byId("local-only").classList.contains("active");
  try {
    await api("/v1/local-only", {method: "PUT", body: JSON.stringify({enabled})});
    await poll();
  } catch (error) {
    byId("control-result").textContent = error.message;
  }
}

async function poll() {
  if (requestActive || tokenDialog.open) return;
  requestActive = true;
  try {
    render(await api("/v1/status"));
  } catch (error) {
    if (!tokenDialog.open) {
      setDot("live-dot", false);
      byId("live-label").textContent = "OFFLINE";
    }
  } finally {
    requestActive = false;
  }
}


tokenForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  apiToken = byId("token-input").value.trim();
  try {
    const data = await api("/v1/status");
    sessionStorage.setItem("phono-console-token", apiToken);
    byId("token-error").textContent = "";
    tokenDialog.close();
    render(data);
  } catch (error) {
    byId("token-error").textContent = "That token was not accepted.";
  }
});

byId("reset-levels").addEventListener("click", async () => {
  try {
    await api("/v1/levels/reset", {method: "POST"});
    await poll();
  } catch (error) {
    byId("control-result").textContent = error.message;
  }
});
byId("pairing-open").addEventListener("click", () => setPairing(true));
byId("pairing-close").addEventListener("click", () => setPairing(false));
byId("local-only").addEventListener("click", setLocalOnly);

poll();
pollTimer = setInterval(poll, 250);
window.addEventListener("pagehide", () => clearInterval(pollTimer));
