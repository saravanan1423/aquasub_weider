const $ = id => document.getElementById(id);
const mainState = {lastId: 0, rawStream: "", settings: null, weight: "", connectingAttempted: false};
let successTimer = null;
let pendingCaptureId = null;
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function requestJson(url, options = {}) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok) throw new Error(data.error || "Request failed"); return data; }
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
  setTimeout(pollScale, 250);
}
async function loadProducts() {
  const products = (await requestJson("/api/product-images")).slice(0, 8); $("product-count").textContent = products.length;
  const grid = $("main-product-grid");
  grid.classList.toggle("layout-six", products.length > 0 && products.length <= 6);
  grid.classList.toggle("layout-eight", products.length > 6);
  grid.innerHTML = products.length ? products.map(item => `<button class="product-choice" data-id="${item.id}" type="button"><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.image_name)}"><span>${escapeHtml(item.image_name)}</span></button>`).join("") : '<p class="gallery-empty">Add product images from Product Master first.</p>';
  document.querySelectorAll(".product-choice").forEach(button => button.addEventListener("click", () => captureWeight(button)));
}
async function captureWeight(button) {
  const message = $("capture-message"); message.hidden = true; button.disabled = true;
  try {
    const result = await requestJson("/api/weight-captures", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product_image_id:Number(button.dataset.id), weight:mainState.weight})});
    document.querySelectorAll(".product-choice").forEach(item => item.classList.remove("selected")); button.classList.add("selected");
    showCaptureSuccess(result);
  } catch (error) { message.className = "capture-message failure"; message.textContent = error.message; message.hidden = false; }
  finally { button.disabled = false; }
}
function showCaptureSuccess(result) {
  clearInterval(successTimer);
  $("success-image").src = result.image_url;
  $("success-image").alt = result.image_name;
  $("success-name").textContent = result.image_name;
  $("success-weight").textContent = result.weight;
  $("success-time").textContent = new Date(result.captured_at).toLocaleString();
  $("capture-success-screen").hidden = false;
  pendingCaptureId = result.id;
  $("cancel-capture-button").disabled = false;
  $("cancel-capture-button").textContent = "Cancel capture";
  let secondsRemaining = 10;
  $("success-countdown").textContent = secondsRemaining;
  successTimer = setInterval(() => {
    secondsRemaining -= 1;
    $("success-countdown").textContent = secondsRemaining;
    if (secondsRemaining <= 0) {
      clearInterval(successTimer);
      $("capture-success-screen").hidden = true;
      pendingCaptureId = null;
    }
  }, 1000);
}
$("cancel-capture-button").addEventListener("click", async () => {
  if (!pendingCaptureId) return;
  const button = $("cancel-capture-button"); button.disabled = true; button.textContent = "Cancelling…";
  try {
    await requestJson(`/api/weight-captures/${pendingCaptureId}/cancel`, {method:"POST"});
    clearInterval(successTimer); pendingCaptureId = null; $("capture-success-screen").hidden = true;
    const message = $("capture-message"); message.className = "capture-message failure"; message.textContent = "Capture cancelled"; message.hidden = false;
    setTimeout(() => { message.hidden = true; }, 2000);
  } catch (error) { button.disabled = false; button.textContent = error.message; }
});
async function initializeMain() {
  try {
    mainState.settings = await requestJson("/api/settings");
    if (!mainState.settings.id) throw new Error("Save communication settings before using the Main Screen");
    const status = await requestJson("/api/data?after=0");
    if (!status.connected && !status.connecting) await requestJson("/api/connect", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(mainState.settings)});
  } catch (error) { setStatus(false, false, error.message); }
  await loadProducts(); pollScale();
}
initializeMain();
