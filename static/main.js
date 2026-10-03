const $ = id => document.getElementById(id);
let pollIntervalMs = 200;
const mainState = {lastId: 0, rawStream: "", settings: null, weight: "", connectingAttempted: false, furnace: null, products: [], activeMelt: null, successSeconds: 10};
let successTimer = null;
let pendingCaptureId = null;
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function requestJson(url, options = {}) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok) { const error = new Error(data.error || "Request failed"); error.code = data.code; throw error; } return data; }
function numericWeight(value) {
  const match = String(value ?? "").replace(/,/g, "").match(/[-+]?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
}
function formatWeight(value) {
  return Number(value || 0).toFixed(3).replace(/\.?0+$/, "");
}
function updateCompleteButtons() {
  const count = mainState.activeMelt?.captureIds.length || 0;
  const visible = count > 0;
  const label = `Mark as completed (${count})`;
  $("complete-melt-button").hidden = !visible;
  $("complete-melt-button").textContent = label;
  if (mainState.activeMelt) {
    $("product-selector-title").textContent = `${mainState.furnace.name} products - ${mainState.activeMelt.number}`;
  } else if (mainState.furnace) {
    $("product-selector-title").textContent = `${mainState.furnace.name} products`;
  }
}
function setStatus(connected, connecting = false, error = "") {
  const status = $("main-status"); status.className = `status ${connected ? "connected" : connecting ? "connecting" : "disconnected"}`;
  status.querySelector("strong").textContent = connected ? "Scale connected" : connecting ? "Connecting…" : "Scale disconnected";
  if (error) $("main-weight-message").textContent = error;
}
function parseLiveWeight() {
  const settings = mainState.settings; if (!settings) return;
  const startMarker = settings.start_marker, endMarker = settings.end_marker;
  let searchBefore = mainState.rawStream.length;
  while (searchBefore > 0) {
    const end = mainState.rawStream.lastIndexOf(endMarker, searchBefore - 1); if (end < 0) return;
    const start = mainState.rawStream.lastIndexOf(startMarker, end - 1); if (start < 0) return;
    const selected = mainState.rawStream.slice(start + startMarker.length, end);
    const weight = selected.slice(settings.start_address - 1, settings.end_address).trim();
    if (weight) { mainState.weight = weight; $("main-live-weight").textContent = weight; $("main-weight-message").textContent = "Live reading"; return; }
    searchBefore = start;
  }
}
async function pollScale() {
  try {
    const data = await requestJson(`/api/data?after=${mainState.lastId}`); setStatus(data.connected, data.connecting, data.error);
    for (const frame of data.frames) {
      mainState.lastId = Math.max(mainState.lastId, frame.id);
      mainState.rawStream = (mainState.rawStream + frame.bytes.map(byte => String.fromCharCode(byte)).join("")).slice(-16384);
    }
    if (data.frames.length) parseLiveWeight();
  } catch (error) { setStatus(false, false, error.message); }
  setTimeout(pollScale, pollIntervalMs);
}
async function loadProducts() {
  mainState.products = (await requestJson("/api/product-images")).filter(item => item.image_type === "product");
}
function showFurnaceProducts() {
  const products = mainState.products.filter(item => Number(item.furnace_id) === Number(mainState.furnace.id)).slice(0, 6); $("product-count").textContent = products.length;
  const grid = $("main-product-grid");
  grid.classList.add("layout-six");
  grid.classList.remove("layout-eight");
  grid.innerHTML = products.length ? products.map(item => `<button class="product-choice" data-id="${item.id}" type="button"><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.image_name)}"><span>${escapeHtml(item.image_name)}</span></button>`).join("") : '<p class="gallery-empty">No products have been added to this furnace.</p>';
  document.querySelectorAll(".product-choice").forEach(button => button.addEventListener("click", () => captureWeight(button)));
}
async function loadFurnaces() {
  const furnaces = await requestJson("/api/furnaces"); $("furnace-count").textContent = furnaces.length;
  $("furnace-grid").innerHTML = furnaces.length ? furnaces.map(item => `<button class="furnace-choice" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}" data-image-url="${escapeHtml(item.image_url)}" type="button"><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.name)}"><span>${escapeHtml(item.name)}</span></button>`).join("") : '<p class="gallery-empty">Add furnace images from Product Master first.</p>';
  document.querySelectorAll(".furnace-choice").forEach(button => button.addEventListener("click", () => selectFurnace(button)));
}
function selectFurnace(button) {
  mainState.furnace = {id: button.dataset.id, name: button.dataset.name, imageUrl: button.dataset.imageUrl};
  document.querySelectorAll(".furnace-choice").forEach(item => item.classList.remove("selected"));
  button.classList.add("selected");
  $("product-selector-title").textContent = `${mainState.furnace.name} products`;
  showFurnaceProducts();
  $("furnace-selector").hidden = true;
  $("product-selector").hidden = false;
  $("main-product-context").hidden = false;
}
async function captureWeight(button) {
  const message = $("capture-message"); message.hidden = true; button.disabled = true;
  try {
    if (!mainState.furnace) throw new Error("Select a furnace before selecting an image");
    const payload = {product_image_id:Number(button.dataset.id), furnace_id:mainState.furnace.id, weight:mainState.weight};
    if (mainState.activeMelt) {
      payload.melt_number = mainState.activeMelt.number;
      payload.melt_serial = mainState.activeMelt.serial;
    }
    const result = await requestJson("/api/weight-captures", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    if (!mainState.activeMelt) {
      mainState.activeMelt = {number:result.melt_number, serial:result.melt_serial, furnaceId:String(result.furnace_id), captureIds:[], totalWeight:0};
    }
    mainState.activeMelt.captureIds.push(result.id);
    mainState.activeMelt.totalWeight = result.melt_total_weight ?? (mainState.activeMelt.totalWeight + numericWeight(result.weight));
    mainState.successSeconds = result.success_display_seconds ?? mainState.successSeconds;
    updateCompleteButtons();
    document.querySelectorAll(".product-choice").forEach(item => item.classList.remove("selected")); button.classList.add("selected");
    showCaptureSuccess(result);
  } catch (error) {
    if (error.code === "threshold_exceeded") {
      $("threshold-dialog-message").textContent = error.message;
      $("threshold-dialog").showModal();
    } else {
      message.className = "capture-message failure"; message.textContent = error.message; message.hidden = false;
    }
  }
  finally { button.disabled = false; }
}
function showCaptureSuccess(result) {
  clearInterval(successTimer);
  $("success-image").src = result.image_url;
  $("success-image").alt = result.image_name;
  $("success-name").textContent = result.image_name;
  $("success-furnace").textContent = result.furnace_name || "Furnace not selected";
  $("success-weight").textContent = result.weight;
  $("success-melt-line").textContent = `Melt: ${result.melt_number || "-"} | Total: ${formatWeight(result.melt_total_weight)} kg`;
  $("success-time").textContent = new Date(result.captured_at).toLocaleString();
  $("capture-success-screen").hidden = false;
  pendingCaptureId = result.id;
  $("cancel-capture-button").disabled = false;
  $("cancel-capture-button").textContent = "Cancel capture";
  const deadline = Date.now() + mainState.successSeconds * 1000;
  $("success-countdown").textContent = mainState.successSeconds;
  successTimer = setInterval(() => {
    const secondsRemaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    $("success-countdown").textContent = secondsRemaining;
    if (secondsRemaining <= 0) {
      clearInterval(successTimer);
      $("capture-success-screen").hidden = true;
      pendingCaptureId = null;
    }
  }, 250);
}
$("cancel-capture-button").addEventListener("click", async () => {
  if (!pendingCaptureId) return;
  const button = $("cancel-capture-button"); button.disabled = true; button.textContent = "Cancelling…";
  try {
    const result = await requestJson(`/api/weight-captures/${pendingCaptureId}/cancel`, {method:"POST"});
    if (mainState.activeMelt) {
      mainState.activeMelt.captureIds = mainState.activeMelt.captureIds.filter(id => id !== result.id);
      mainState.activeMelt.totalWeight = Math.max(0, mainState.activeMelt.totalWeight - numericWeight($("success-weight").textContent));
      if (!mainState.activeMelt.captureIds.length) mainState.activeMelt = null;
      updateCompleteButtons();
    }
    clearInterval(successTimer); pendingCaptureId = null; $("capture-success-screen").hidden = true;
    const message = $("capture-message"); message.className = "capture-message failure"; message.textContent = "Capture cancelled"; message.hidden = false;
    setTimeout(() => { message.hidden = true; }, 2000);
  } catch (error) { button.disabled = false; button.textContent = error.message; }
});
function requestMeltCompletion() {
  if (!mainState.activeMelt || !mainState.activeMelt.captureIds.length) return;
  clearInterval(successTimer);
  $("complete-dialog-title").textContent = `Complete melt ${mainState.activeMelt.number}?`;
  $("complete-dialog-message").textContent = `This will complete ${mainState.activeMelt.captureIds.length} captured product(s).`;
  $("complete-dialog-melt").textContent = mainState.activeMelt.number;
  $("complete-dialog-weight").textContent = formatWeight(mainState.activeMelt.totalWeight);
  $("complete-melt-dialog").showModal();
}
async function completeActiveMelt() {
  if (!mainState.activeMelt || !mainState.activeMelt.captureIds.length) return;
  $("complete-melt-dialog").close();
  const buttons = [$("complete-melt-button")];
  buttons.forEach(button => { button.disabled = true; button.textContent = "Completing..."; });
  try {
    const result = await requestJson("/api/melts/complete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({furnace_id:mainState.furnace.id, melt_number:mainState.activeMelt.number, capture_ids:mainState.activeMelt.captureIds})});
    clearInterval(successTimer);
    $("capture-success-screen").hidden = true;
    pendingCaptureId = null;
    mainState.activeMelt = null;
    mainState.furnace = null;
    $("product-selector").hidden = true;
    $("main-product-context").hidden = true;
    $("furnace-selector").hidden = false;
    updateCompleteButtons();
    const message = $("capture-message");
    message.className = "capture-message success";
    message.textContent = `${result.melt_number} completed: ${result.melt_count} product(s), ${formatWeight(result.melt_total_weight)} kg total`;
    message.hidden = false;
    setTimeout(() => { message.hidden = true; }, 3500);
  } catch (error) {
    const message = $("capture-message");
    message.className = "capture-message failure";
    message.textContent = error.message;
    message.hidden = false;
  } finally {
    buttons.forEach(button => { button.disabled = false; });
    updateCompleteButtons();
  }
}
$("complete-melt-button").addEventListener("click", requestMeltCompletion);
$("confirm-complete-melt").addEventListener("click", completeActiveMelt);
$("furnace-back").addEventListener("click", () => {
  if (mainState.activeMelt?.captureIds.length) {
    const message = $("capture-message");
    message.className = "capture-message failure";
    message.textContent = "Mark the current melt as completed before changing furnace";
    message.hidden = false;
    return;
  }
  mainState.furnace = null;
  $("product-selector").hidden = true;
  $("main-product-context").hidden = true;
  $("furnace-selector").hidden = false;
});
async function initializeMain() {
  try {
    mainState.settings = await requestJson("/api/settings");
    const displaySettings = await requestJson("/api/capture-display-settings").catch(() => ({success_display_seconds: 10}));
    mainState.successSeconds = displaySettings.success_display_seconds;
    if (!mainState.settings.id) throw new Error("Save communication settings before using the Main Screen");
    pollIntervalMs = Number(mainState.settings.refresh_interval_ms) || 200;
    const status = await requestJson("/api/data?after=0");
    if (!status.connected && !status.connecting) await requestJson("/api/connect", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(mainState.settings)});
  } catch (error) { setStatus(false, false, error.message); }
  await loadFurnaces(); await loadProducts(); pollScale();
}
initializeMain();
