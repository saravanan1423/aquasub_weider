const $ = (id) => document.getElementById(id);
let pollIntervalMs = 200;
const state = { connected: false, connecting: false, lastId: 0, frames: [], view: "text", framesTotal: 0, bytesTotal: 0, rawStream: "" };

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

async function loadPorts() {
  try {
    const ports = await jsonFetch("/api/ports");
    $("ports").innerHTML = ports.map(p => `<option value="${escapeHtml(p.device)}">${escapeHtml(p.description)}</option>`).join("");
    // Prefer a detected Linux USB/ACM serial device over the placeholder.
    const preferred = ports.find(p => /^\/dev\/tty(USB|ACM)/.test(p.device)) || ports[0];
    if (preferred && ($("port").value === "/dev/ttyUSB0" || !$("port").value)) {
      $("port").value = preferred.device;
    }
  } catch (error) { showError(error.message); }
}

async function loadSettings() {
  try {
    const saved = await jsonFetch("/api/settings");
    if (!saved.id) return;
    $("port").value = saved.port;
    $("baud").value = String(saved.baud_rate);
    $("bits").value = String(saved.data_bits);
    $("parity").value = saved.parity;
    $("stop").value = saved.stop_bits;
    $("read-timeout").value = saved.frame_timeout ?? 1;
    $("refresh-interval").value = saved.refresh_interval_ms ?? 200;
    $("frame-gap").value = saved.frame_gap_ms ?? 250;
    pollIntervalMs = Number($("refresh-interval").value);
    $("start-marker").value = saved.start_marker;
    $("end-marker").value = saved.end_marker;
    $("start-address").value = saved.start_address;
    $("end-address").value = saved.end_address;
  } catch (error) { showError(error.message); }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function showError(message = "") {
  $("error").hidden = !message;
  $("error").textContent = message;
}

function settingsPayload() {
  return {
    port: $("port").value,
    baud_rate: $("baud").value,
    data_bits: $("bits").value,
    parity: $("parity").value,
    stop_bits: $("stop").value,
    frame_timeout: $("read-timeout").value,
    refresh_interval_ms: $("refresh-interval").value,
    frame_gap_ms: $("frame-gap").value,
    start_marker: $("start-marker").value,
    end_marker: $("end-marker").value,
    start_address: $("start-address").value,
    end_address: $("end-address").value
  };
}

function updateStatus(data) {
  state.connected = data.connected;
  state.connecting = data.connecting;
  const status = $("status");
  const label = data.connected ? "Connected" : data.connecting ? "Connecting…" : "Disconnected";
  status.className = `status ${data.connected ? "connected" : data.connecting ? "connecting" : "disconnected"}`;
  status.querySelector("strong").textContent = label;
  $("connect").textContent = data.connected || data.connecting ? "Disconnect" : "Connect";
  document.querySelectorAll(".controls input, .controls select").forEach(el => el.disabled = data.connected || data.connecting);
  if (data.error) showError(data.error);
}

function frameValue(frame) {
  if (state.view === "hex") return frame.hex || "(empty)";
  if (state.view === "bytes") return frame.bytes.join(" ") || "(empty)";
  return frame.text || "(empty / line ending only)";
}

function appendFrame(frame) {
  $("empty")?.remove();
  const row = document.createElement("div");
  row.className = "frame";
  row.dataset.id = frame.id;
  const localTime = new Date(frame.time).toLocaleTimeString([], {hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3});
  row.innerHTML = `<span class="time">${localTime}</span><span class="raw">${escapeHtml(frameValue(frame))}</span><span class="length">${frame.length} B</span>`;
  $("terminal").appendChild(row);
}

function decodeBytes(bytes) {
  // A serial protocol is byte-oriented. Latin-1 gives every byte a stable
  // one-character representation without silently removing anything.
  return bytes.map(value => String.fromCharCode(value)).join("");
}

function updateLiveWeight() {
  const startMarker = $("start-marker").value;
  const endMarker = $("end-marker").value;
  const startAddress = Math.max(parseInt($("start-address").value, 10) || 1, 1);
  const endAddress = Math.max(parseInt($("end-address").value, 10) || startAddress, startAddress);
  if (!startMarker || !endMarker) {
    $("weight-state").textContent = "Enter both start and end characters";
    return;
  }

  // Search backward so the display always uses the newest complete reading.
  let searchBefore = state.rawStream.length;
  while (searchBefore > 0) {
    const endIndex = state.rawStream.lastIndexOf(endMarker, searchBefore - 1);
    if (endIndex < 0) break;
    const startIndex = state.rawStream.lastIndexOf(startMarker, endIndex - 1);
    if (startIndex < 0) break;
    const selected = state.rawStream.slice(startIndex + startMarker.length, endIndex);
    const weight = selected.slice(startAddress - 1, endAddress).trim();
    if (weight) {
      $("selected-data").textContent = selected;
      $("live-weight").textContent = weight;
      $("weight-state").textContent = `Characters ${startAddress}–${endAddress}`;
      return;
    }
    searchBefore = startIndex;
  }
  $("weight-state").textContent = "Waiting for a matching value";
}

function redraw() {
  $("terminal").innerHTML = "";
  if (!state.frames.length) $("terminal").innerHTML = '<div class="empty" id="empty"><div class="pulse"></div><p>Waiting for raw serial data…</p></div>';
  state.frames.forEach(appendFrame);
}

async function poll() {
  try {
    const data = await jsonFetch(`/api/data?after=${state.lastId}`);
    updateStatus(data);
    if (data.frames.length) {
      data.frames.forEach(frame => {
        state.frames.push(frame);
        if (state.frames.length > 1000) state.frames.shift();
        state.lastId = Math.max(state.lastId, frame.id);
        state.framesTotal += 1;
        state.bytesTotal += frame.length;
        state.rawStream = (state.rawStream + decodeBytes(frame.bytes)).slice(-16384);
        appendFrame(frame);
      });
      updateLiveWeight();
      $("frame-count").textContent = state.framesTotal.toLocaleString();
      $("byte-count").textContent = state.bytesTotal.toLocaleString();
      $("latest").textContent = new Date(data.frames.at(-1).time).toLocaleTimeString();
      if ($("autoscroll").checked) $("terminal").scrollTop = $("terminal").scrollHeight;
    }
  } catch (error) { showError(error.message); }
  setTimeout(poll, pollIntervalMs);
}

$("connect").addEventListener("click", async () => {
  showError();
  try {
    if (state.connected || state.connecting) {
      await jsonFetch("/api/disconnect", {method: "POST"});
    } else {
      const payload = settingsPayload();
      await jsonFetch("/api/connect", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)});
      pollIntervalMs = Number(payload.refresh_interval_ms);
    }
  } catch (error) { showError(error.message); }
});

$("save-settings").addEventListener("click", async () => {
  showError();
  const button = $("save-settings");
  try {
    const payload = settingsPayload();
    await jsonFetch("/api/settings", {method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)});
    pollIntervalMs = Number(payload.refresh_interval_ms);
    button.textContent = "Saved ✓";
    setTimeout(() => button.textContent = "Save", 1500);
  } catch (error) { showError(error.message); }
});

$("refresh").addEventListener("click", loadPorts);
$("clear").addEventListener("click", async () => {
  await jsonFetch("/api/clear", {method: "POST"});
  Object.assign(state, {lastId: 0, frames: [], framesTotal: 0, bytesTotal: 0, rawStream: ""});
  $("frame-count").textContent = $("byte-count").textContent = "0"; $("latest").textContent = "—"; redraw();
  $("live-weight").textContent = "------"; $("selected-data").textContent = "—"; $("weight-state").textContent = "Waiting for a matching value";
});
$("copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText(state.frames.map(f => `${f.time}\t${frameValue(f)}`).join("\n"));
  $("copy").textContent = "Copied"; setTimeout(() => $("copy").textContent = "Copy", 1000);
});
document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active")); tab.classList.add("active"); state.view = tab.dataset.view; redraw();
}));
document.querySelectorAll("#start-marker, #end-marker, #start-address, #end-address").forEach(input => input.addEventListener("input", updateLiveWeight));

loadSettings().then(loadPorts); poll();
