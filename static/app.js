const $ = (id) => document.getElementById(id);
let pollIntervalMs = 200;
const state = {
  connected: false,
  connecting: false,
  lastId: 0,
  frames: [],
  view: "text",
  framesTotal: 0,
  bytesTotal: 0,
  rawStream: ""
};

// Interactive Live Raw / Address Picker Controller
const picker = {
  frozen: false,
  mode: "start", // "start" | "from" | "to" | "end"
  // Seed with default frame matching the user's reference screenshot:
  // = (61), S (83), G (71), - (45), 0 (48), 0 (48), 1 (49), 4 (52), . (46), 2 (50), 1 (49), k (107), \ (92), x (120), c (99), 5 (53)
  currentBytes: [61, 83, 71, 45, 48, 48, 49, 52, 46, 50, 49, 107, 92, 120, 99, 53],
  charFor(code) {
    if (code === 32) return "␣";
    if (code === 13) return "\\r";
    if (code === 10) return "\\n";
    if (code === 9) return "\\t";
    if (code === 2) return "STX";
    if (code === 3) return "ETX";
    if (code >= 33 && code <= 126) return String.fromCharCode(code);
    return "\\" + code.toString(16).padStart(2, "0");
  }
};

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

async function loadPorts() {
  try {
    const ports = await jsonFetch("/api/ports");
    const portSelect = $("ports");
    if (portSelect) {
      portSelect.innerHTML = ports.map(p => `<option value="${escapeHtml(p.device)}">${escapeHtml(p.description)}</option>`).join("");
    }
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
    $("start-marker").value = saved.start_marker ?? "=";
    $("end-marker").value = saved.end_marker ?? "\\";
    $("start-address").value = saved.start_address ?? 4;
    $("end-address").value = saved.end_address ?? 10;
    renderAddressPicker();
    updateLiveWeight();
  } catch (error) { showError(error.message); }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function showError(message = "") {
  const errEl = $("error");
  if (!errEl) return;
  errEl.hidden = !message;
  errEl.textContent = message;
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
  if (status) {
    const label = data.scale_connected ? "Device connected" : "Device disconnected";
    status.className = `status ${data.scale_connected ? "connected" : "disconnected"}`;
    const strong = status.querySelector("strong");
    if (strong) strong.textContent = label;
  }
  const connectBtn = $("connect");
  if (connectBtn) {
    connectBtn.textContent = data.connected || data.connecting ? "Disconnect" : "Connect";
  }
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
  const terminal = $("terminal");
  if (terminal) terminal.appendChild(row);
}

function decodeBytes(bytes) {
  return bytes.map(value => String.fromCharCode(value)).join("");
}

function setPickerMode(newMode) {
  picker.mode = newMode;
  ["start", "from", "to", "end"].forEach(m => {
    const btn = $(`btn-mode-${m}`);
    if (btn) {
      if (m === newMode) btn.classList.add("active");
      else btn.classList.remove("active");
    }
  });
}

function renderAddressPicker() {
  const strip = $("raw-bytes-strip");
  if (!strip) return;

  const startMarker = $("start-marker").value || "=";
  const endMarker = $("end-marker").value || "\\";
  const startAddress = Math.max(parseInt($("start-address").value, 10) || 1, 1);
  const endAddress = Math.max(parseInt($("end-address").value, 10) || startAddress, startAddress);

  // Update mode labels
  if ($("mode-val-start")) $("mode-val-start").textContent = startMarker;
  if ($("mode-val-from")) $("mode-val-from").textContent = `Pos ${startAddress}`;
  if ($("mode-val-to")) $("mode-val-to").textContent = `Pos ${endAddress}`;
  if ($("mode-val-end")) $("mode-val-end").textContent = endMarker;

  // Convert current bytes to string to locate markers
  const rawStr = picker.currentBytes.map(c => String.fromCharCode(c)).join("");
  let startIdx = rawStr.indexOf(startMarker);
  let endIdx = -1;
  if (startIdx >= 0) {
    endIdx = rawStr.indexOf(endMarker, startIdx + startMarker.length);
  } else {
    endIdx = rawStr.indexOf(endMarker);
  }

  // Calculate content bounds and relative address positions
  let contentStartIdx = -1;
  let fromIdx = -1;
  let toIdx = -1;

  if (startIdx >= 0) {
    contentStartIdx = startIdx + startMarker.length;
    fromIdx = contentStartIdx + (startAddress - 1);
    toIdx = contentStartIdx + (endAddress - 1);
  } else {
    // If start marker not found in frame, calculate from position 1
    fromIdx = startAddress - 1;
    toIdx = endAddress - 1;
  }

  // Generate cards
  const cardsHtml = picker.currentBytes.map((code, idx) => {
    const ch = picker.charFor(code);
    const isSpecial = (code < 33 || code > 126);
    const classes = ["byte-card"];
    let badgeHtml = "";

    const isStart = (idx === startIdx);
    const isEnd = (idx === endIdx);
    const isFrom = (idx === fromIdx);
    const isTo = (idx === toIdx);
    const inRange = (fromIdx >= 0 && toIdx >= fromIdx && idx >= fromIdx && idx <= toIdx);

    if (isStart) {
      classes.push("is-start");
      badgeHtml = `<span class="card-badge badge-start">START</span>`;
    } else if (isEnd) {
      classes.push("is-end");
      badgeHtml = `<span class="card-badge badge-end">END</span>`;
    }

    if (isFrom) {
      classes.push("is-from");
      badgeHtml = `<span class="card-badge badge-from">FROM</span>`;
    } else if (isTo) {
      classes.push("is-to");
      badgeHtml = `<span class="card-badge badge-to">TO</span>`;
    }

    if (inRange && !isFrom && !isTo) {
      classes.push("is-in-range");
    }

    const nextModeName = picker.mode.toUpperCase();
    const tooltip = `Byte #${idx + 1}: '${ch}' (ASCII ${code}) — Click to set as ${nextModeName}`;

    return `
      <div class="${classes.join(" ")}" data-index="${idx}" title="${escapeHtml(tooltip)}">
        <div class="card-badge-slot">${badgeHtml}</div>
        <div class="card-char${isSpecial ? " special" : ""}">${escapeHtml(ch)}</div>
        <div class="card-ascii">${code}</div>
        <div class="card-index">${idx + 1}</div>
      </div>
    `;
  }).join("");

  strip.innerHTML = cardsHtml;

  // Update Summary line:
  // "First ASCII 61 (=) | Last ASCII 92 (\) | Start Pos 4 | End Pos 10"
  const startAscii = (startIdx >= 0 && startIdx < picker.currentBytes.length)
    ? picker.currentBytes[startIdx]
    : (startMarker ? startMarker.charCodeAt(0) : "—");
  const endAscii = (endIdx >= 0 && endIdx < picker.currentBytes.length)
    ? picker.currentBytes[endIdx]
    : (endMarker ? endMarker.charCodeAt(0) : "—");

  const summaryEl = $("picker-summary-text");
  if (summaryEl) {
    summaryEl.textContent = `First ASCII ${startAscii} (${startMarker}) | Last ASCII ${endAscii} (${endMarker}) | Start Pos ${startAddress} | End Pos ${endAddress}`;
  }

  // Extract selected data preview
  let selectedStr = "—";
  if (startIdx >= 0 && endIdx > startIdx) {
    const content = rawStr.slice(startIdx + startMarker.length, endIdx);
    selectedStr = content.slice(startAddress - 1, endAddress);
  } else if (fromIdx >= 0 && toIdx >= fromIdx && toIdx < rawStr.length) {
    selectedStr = rawStr.slice(fromIdx, toIdx + 1);
  }

  const selectedDataEl = $("selected-data");
  if (selectedDataEl) {
    selectedDataEl.textContent = selectedStr || "(empty)";
  }

  const subEl = $("picker-extracted-sub");
  if (subEl) {
    subEl.textContent = `Characters ${startAddress}–${endAddress}`;
  }

  // If live scale is not sending or offline, parse preview weight directly from selected data
  const trimmed = selectedStr.trim();
  if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(trimmed)) {
    if (!state.connected || $("live-weight").textContent === "------") {
      $("live-weight").textContent = trimmed;
      $("weight-state").textContent = `Extracted from characters ${startAddress}–${endAddress}`;
    }
  }
}

function handleCardClick(idx) {
  if (idx < 0 || idx >= picker.currentBytes.length) return;
  const byteCode = picker.currentBytes[idx];
  const char = String.fromCharCode(byteCode);

  const startMarkerInput = $("start-marker");
  const endMarkerInput = $("end-marker");
  const startAddressInput = $("start-address");
  const endAddressInput = $("end-address");

  const rawStr = picker.currentBytes.map(c => String.fromCharCode(c)).join("");

  if (picker.mode === "start") {
    startMarkerInput.value = char;
    setPickerMode("from");
  } else if (picker.mode === "from") {
    const sMarker = startMarkerInput.value || "=";
    const sIdx = rawStr.indexOf(sMarker);
    const contentStart = (sIdx >= 0) ? sIdx + sMarker.length : 0;
    const pos = Math.max(1, idx - contentStart + 1);
    startAddressInput.value = pos;
    // ensure end address is at least start address
    const currentEnd = parseInt(endAddressInput.value, 10) || 1;
    if (currentEnd < pos) endAddressInput.value = pos;
    setPickerMode("to");
  } else if (picker.mode === "to") {
    const sMarker = startMarkerInput.value || "=";
    const sIdx = rawStr.indexOf(sMarker);
    const contentStart = (sIdx >= 0) ? sIdx + sMarker.length : 0;
    const currentStart = parseInt(startAddressInput.value, 10) || 1;
    const pos = Math.max(currentStart, idx - contentStart + 1);
    endAddressInput.value = pos;
    setPickerMode("end");
  } else if (picker.mode === "end") {
    endMarkerInput.value = char;
    setPickerMode("start");
  }

  renderAddressPicker();
  updateLiveWeight();
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

  // Search backward in state.rawStream so display always uses newest complete reading
  let searchBefore = state.rawStream.length;
  while (searchBefore > 0) {
    const endIndex = state.rawStream.lastIndexOf(endMarker, searchBefore - 1);
    if (endIndex < 0) break;
    const startIndex = state.rawStream.lastIndexOf(startMarker, endIndex - 1);
    if (startIndex < 0) break;
    const selected = state.rawStream.slice(startIndex + startMarker.length, endIndex);
    const weight = selected.slice(startAddress - 1, endAddress).trim();
    if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(weight)) {
      $("selected-data").textContent = selected.slice(startAddress - 1, endAddress);
      $("live-weight").textContent = weight;
      $("weight-state").textContent = `Characters ${startAddress}–${endAddress}`;
      return;
    }
    searchBefore = startIndex;
  }
  if (state.connected) {
    $("weight-state").textContent = "Waiting for a matching value";
  }
}

function redraw() {
  const terminal = $("terminal");
  if (!terminal) return;
  terminal.innerHTML = "";
  if (!state.frames.length) terminal.innerHTML = '<div class="empty" id="empty"><div class="pulse"></div><p>Waiting for raw serial data…</p></div>';
  state.frames.forEach(appendFrame);
}

async function poll() {
  try {
    const data = await jsonFetch(`/api/data?after=${state.lastId}`);
    if (window.ScaleStatus) window.ScaleStatus.publish(data);
    pollIntervalMs = Number(data.refresh_interval_ms) || pollIntervalMs;
    updateStatus(data);

    if (data.frames && data.frames.length) {
      data.frames.forEach(frame => {
        state.frames.push(frame);
        if (state.frames.length > 1000) state.frames.shift();
        state.lastId = Math.max(state.lastId, frame.id);
        state.framesTotal += 1;
        state.bytesTotal += frame.length;
        state.rawStream = (state.rawStream + decodeBytes(frame.bytes)).slice(-16384);
        appendFrame(frame);
      });

      // Update Live Address Picker with incoming frame if not frozen
      if (!picker.frozen) {
        const latestFrame = data.frames[data.frames.length - 1];
        if (latestFrame.bytes && latestFrame.bytes.length > 0) {
          picker.currentBytes = latestFrame.bytes.slice(0, 64);
          renderAddressPicker();
        }
      }

      updateLiveWeight();
      if ($("frame-count")) $("frame-count").textContent = state.framesTotal.toLocaleString();
      if ($("byte-count")) $("byte-count").textContent = state.bytesTotal.toLocaleString();
      if ($("latest")) $("latest").textContent = new Date(data.frames.at(-1).time).toLocaleTimeString();
      if ($("autoscroll") && $("autoscroll").checked && $("terminal")) {
        $("terminal").scrollTop = $("terminal").scrollHeight;
      }
    }

    state.lastId = data.last_frame_id;
    if (data.scale_connected) {
      $("live-weight").textContent = data.weight;
    } else if (!state.connected && $("live-weight").textContent === "------") {
      $("live-weight").textContent = "------";
      $("weight-state").textContent = "Waiting for a valid weight";
      state.rawStream = "";
    }
  } catch (error) {
    if (window.ScaleStatus) window.ScaleStatus.reportError(error);
    showError(error.message);
  }
  setTimeout(poll, pollIntervalMs);
}

if (window.ScaleStatus) {
  window.ScaleStatus.subscribe(data => {
    updateStatus(data);
    if (data.scale_connected) {
      $("live-weight").textContent = data.weight;
    }
  });
}

// Event Listeners Initialization
function initListeners() {
  // Connect / Disconnect
  $("connect")?.addEventListener("click", async () => {
    showError();
    try {
      if (state.connected || state.connecting) {
        await jsonFetch("/api/disconnect", {method: "POST"});
      } else {
        const payload = settingsPayload();
        await jsonFetch("/api/connect", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        pollIntervalMs = Number(payload.refresh_interval_ms);
      }
    } catch (error) { showError(error.message); }
  });

  // Save Settings from Sidebar Form
  $("save-settings")?.addEventListener("click", async () => {
    showError();
    const button = $("save-settings");
    try {
      const payload = settingsPayload();
      await jsonFetch("/api/settings", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      pollIntervalMs = Number(payload.refresh_interval_ms);
      button.textContent = "Saved ✓";
      setTimeout(() => button.textContent = "Save", 1500);
      renderAddressPicker();
    } catch (error) { showError(error.message); }
  });

  // Save from Live Address Picker & Apply to Main Screen
  $("save-from-picker")?.addEventListener("click", async () => {
    showError();
    const button = $("save-from-picker");
    const originalText = button.innerHTML;
    try {
      const payload = settingsPayload();
      await jsonFetch("/api/settings", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      pollIntervalMs = Number(payload.refresh_interval_ms);
      button.classList.add("saved");
      button.innerHTML = `<i class="ui-icon" style="--icon:url('/static/icons/check.svg')" aria-hidden="true"></i> Saved & Active on Main Screen ✓`;
      setTimeout(() => {
        button.classList.remove("saved");
        button.innerHTML = originalText;
      }, 2500);
      renderAddressPicker();
    } catch (error) { showError(error.message); }
  });

  // Freeze Frame Button
  $("freeze-frame-btn")?.addEventListener("click", () => {
    picker.frozen = !picker.frozen;
    const btn = $("freeze-frame-btn");
    const icon = $("freeze-icon");
    const label = $("freeze-label");
    const badge = $("picker-badge");
    if (picker.frozen) {
      btn.classList.add("active");
      if (icon) icon.textContent = "▶";
      if (label) label.textContent = "Resume Live";
      if (badge) {
        badge.className = "picker-badge frozen";
        badge.textContent = "Frame frozen (Click to pick)";
      }
    } else {
      btn.classList.remove("active");
      if (icon) icon.textContent = "⏸";
      if (label) label.textContent = "Freeze Frame";
      if (badge) {
        badge.className = "picker-badge ready";
        badge.textContent = "Live preview ready";
      }
    }
  });

  // Mode Selector Buttons (START, FROM, TO, END)
  ["start", "from", "to", "end"].forEach(m => {
    $(`btn-mode-${m}`)?.addEventListener("click", () => {
      setPickerMode(m);
      renderAddressPicker();
    });
  });

  // Click on Byte Cards in Strip (Delegated)
  $("raw-bytes-strip")?.addEventListener("click", (evt) => {
    const card = evt.target.closest(".byte-card");
    if (!card) return;
    const idx = parseInt(card.dataset.index, 10);
    handleCardClick(idx);
  });

  // Toggle Manual Fields
  $("toggle-manual-fields")?.addEventListener("click", () => {
    const row = $("manual-fields-row");
    const btn = $("toggle-manual-fields");
    if (row && btn) {
      const isHidden = (row.style.display === "none");
      row.style.display = isHidden ? "grid" : "none";
      btn.textContent = isHidden ? "⚙ Hide Manual Settings" : "⚙ Show / Edit Manual Settings";
    }
  });

  // Ports, Clear, Copy, Tabs
  $("refresh")?.addEventListener("click", loadPorts);
  $("clear")?.addEventListener("click", async () => {
    await jsonFetch("/api/clear", {method: "POST"});
    Object.assign(state, {lastId: 0, frames: [], framesTotal: 0, bytesTotal: 0, rawStream: ""});
    if ($("frame-count")) $("frame-count").textContent = "0";
    if ($("byte-count")) $("byte-count").textContent = "0";
    if ($("latest")) $("latest").textContent = "—";
    redraw();
    $("live-weight").textContent = "------";
    $("selected-data").textContent = "—";
    $("weight-state").textContent = "Waiting for a matching value";
  });

  $("copy")?.addEventListener("click", async () => {
    await navigator.clipboard.writeText(state.frames.map(f => `${f.time}\t${frameValue(f)}`).join("\n"));
    const copyBtn = $("copy");
    copyBtn.textContent = "Copied";
    setTimeout(() => copyBtn.textContent = "Copy", 1000);
  });

  document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    state.view = tab.dataset.view;
    redraw();
  }));

  // Two-way sync: typing into manual inputs updates Address Picker visual cards
  document.querySelectorAll("#start-marker, #end-marker, #start-address, #end-address").forEach(input => {
    input.addEventListener("input", () => {
      renderAddressPicker();
      updateLiveWeight();
    });
  });
}

// Initialize on page load
initListeners();
loadSettings().then(loadPorts);
renderAddressPicker();
poll();
