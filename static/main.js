const $ = id => document.getElementById(id);
const mainState = {lastId: 0, settings: null, weight: "", weightStable: false, connectingAttempted: false, furnace: null, products: [], activeMelt: null, allocation: null, successSeconds: 10};
const mainHeader = document.querySelector(".sidebar");
mainHeader.id = "main-navigation";
mainHeader.appendChild($("complete-melt-button"));
function setMainMaximized(maximized) {
  document.body.classList.toggle("main-maximized", maximized);
  mainHeader.hidden = maximized;
  const controls = $("maximized-live-controls");
  const weight = document.querySelector(".main-inline-weight");
  const settings = $("main-settings-button");
  controls.hidden = !maximized;
  if (maximized) {
    controls.before($("complete-melt-button"));
    controls.append(weight, $("main-status"), $("main-date-time"));
    if (settings) controls.append(settings);
  } else {
    $("main-weight-message").before(weight, $("main-status"), $("main-date-time"));
    if (settings) $("main-weight-message").after(settings);
    mainHeader.appendChild($("complete-melt-button"));
  }
}
setMainMaximized(true);
function updateMainClock() {
  const clock = $("main-date-time");
  const now = new Date();
  clock.dateTime = now.toISOString();
  $("main-date").textContent = now.toLocaleDateString([], {year:"numeric", month:"short", day:"numeric"});
  $("main-time").textContent = now.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"});
}
updateMainClock();
setInterval(updateMainClock, 1000);
$("main-status").addEventListener("click", () => {
  const status = $("main-status");
  if (!status.classList.contains("disconnected")) return;
  const message = $("main-weight-message").textContent.trim() || "The scale is disconnected.";
  $("scale-error-message").textContent = message;
  $("scale-error-dialog").showModal();
});
let successTimer = null;
let pendingCaptureId = null;
let selectingFurnace = false;
let capturingWeight = false;
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function requestJson(url, options = {}) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok) { const error = new Error(data.error || "Request failed"); error.code = data.code; throw error; } return data; }
function numericWeight(value) {
  const match = String(value ?? "").replace(/,/g, "").match(/[-+]?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
}
function formatWeight(value) {
  return Number(value || 0).toFixed(3).replace(/\.?0+$/, "");
}
function formatAllowanceWeight(value) {
  if (value === null || value === undefined || value === "") return "0";
  const num = Number(value);
  return isNaN(num) ? String(value) : num.toFixed(3).replace(/\.?0+$/, "");
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
  const message = $("main-weight-message");
  message.hidden = true;
  message.textContent = error || "The scale is disconnected.";
}
window.ScaleStatus.subscribe(data => {
  setStatus(data.scale_connected, data.connecting, data.error);
  mainState.lastId = data.last_frame_id;
  const currentWeight = (data.scale_connected && data.weight)
    ? data.weight
    : (data.saved_data || mainState.settings?.saved_data || data.weight || "");
  mainState.weight = currentWeight;
  mainState.weightStable = Boolean(data.scale_connected ? data.weight_stable : (currentWeight !== ""));
  $("main-live-weight").textContent = currentWeight || "------";
});
async function loadProducts() {
  mainState.products = (await requestJson("/api/product-images")).filter(item => item.image_type === "product");
}
function showFurnaceProducts() {
  const products = mainState.products.filter(item => Number(item.furnace_id) === Number(mainState.furnace.id)).slice(0, 6); $("product-count").textContent = products.length;
  const grid = $("main-product-grid");
  grid.classList.add("layout-six");
  grid.classList.remove("layout-eight");
  grid.innerHTML = products.length ? products.map(item => {
    const material = mainState.allocation?.materials.find(row => row.id === item.id);
    const disabled = !mainState.allocation?.configured || mainState.allocation?.status === "awaiting_verification" || !material || material.allocated_kg === null;
    let weight = "";
    if (material?.allocated_kg !== null && material?.allocated_kg !== undefined) {
      const capturedNum = Number(material.captured_kg || 0);
      const allocatedNum = Number(material.allocated_kg || 0);
      const isExcessive = capturedNum > allocatedNum;
      const allowanceClass = isExcessive ? "material-allowance excessive" : "material-allowance";
      weight = `<small class="${allowanceClass}">${escapeHtml(formatAllowanceWeight(material.captured_kg))} / ${escapeHtml(formatAllowanceWeight(material.allocated_kg))} kg</small>`;
    }
    return `<button class="product-choice" data-id="${item.id}" type="button" ${disabled ? 'disabled title="Not allocated to this melt"' : ""}><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.image_name)}"><span>${escapeHtml(item.image_name)}${weight}</span></button>`;
  }).join("") : '<p class="gallery-empty">No products have been added to this furnace.</p>';
  document.querySelectorAll(".product-choice").forEach(button => button.addEventListener("click", () => captureWeight(button)));
}
async function refreshAllocation() {
  if (!mainState.furnace) return;
  mainState.allocation = await requestJson(`/api/material-allocations/${mainState.furnace.id}`);
  showFurnaceProducts();
  if (!mainState.activeMelt && mainState.allocation.configured) {
    $("product-selector-title").textContent = `${mainState.furnace.name} products - ${mainState.allocation.melt_number}`;
  }
}
let syncingAllocation = false;
async function syncMaterialAllocation() {
  if (!mainState.furnace || selectingFurnace || capturingWeight || syncingAllocation || !$("capture-success-screen").hidden || document.hidden) return;
  syncingAllocation = true;
  const furnaceId = mainState.furnace.id;
  try {
    const allocation = await requestJson(`/api/material-allocations/${furnaceId}`);
    if (JSON.stringify(allocation) === JSON.stringify(mainState.allocation)) return;
    const {melt} = await requestJson(`/api/melts/unfinished?furnace_id=${encodeURIComponent(furnaceId)}`);
    if (mainState.furnace?.id !== furnaceId || selectingFurnace || capturingWeight) return;
    mainState.allocation = allocation;
    restoreActiveMelt(melt);
    showFurnaceProducts();
    updateCompleteButtons();
    if (!melt) $("product-selector-title").textContent = `${mainState.furnace.name} products - ${allocation.melt_number}`;
  } catch { /* Retry when allocation status becomes available. */ }
  finally { syncingAllocation = false; }
}
setInterval(syncMaterialAllocation, 5000);
async function loadFurnaces() {
  const furnaces = await requestJson("/api/furnaces"); $("furnace-count").textContent = furnaces.length;
  $("furnace-grid").innerHTML = furnaces.length ? furnaces.map(item => `<button class="furnace-choice" data-id="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}" data-image-url="${escapeHtml(item.image_url)}" type="button"><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.name)}"><span>${escapeHtml(item.name)}</span></button>`).join("") : '<p class="gallery-empty">Add furnace images from Product Master first.</p>';
  document.querySelectorAll(".furnace-choice").forEach(button => button.addEventListener("click", () => selectFurnace(button)));
}
function restoreActiveMelt(melt) {
  mainState.activeMelt = melt ? {number:melt.melt_number, serial:melt.melt_serial,
    furnaceId:String(melt.furnace_id), captureIds:melt.capture_ids, captures:melt.captures || [], totalWeight:melt.melt_total_weight} : null;
}
function updateProductStageActions() {
  const settings = $("main-settings-button");
  if (settings) settings.hidden = false;
  const info = $("main-product-info-button");
  if (info) info.hidden = true;
}
async function showProductInfo() {
  if (mainState.activeMelt && !mainState.activeMelt.captures?.length) {
    const result = await requestJson(`/api/melts/unfinished?furnace_id=${encodeURIComponent(mainState.furnace.id)}`);
    restoreActiveMelt(result.melt);
  }
  const list = $("product-info-list");
  const captures = mainState.activeMelt?.captures || [];
  list.innerHTML = captures.length ? captures.map((item, index) => `<div class="product-info-row"><strong>${index + 1}. ${escapeHtml(item.product_name || "Product")}</strong><span>${escapeHtml(item.weight)} kg</span></div>`).join("") : '<p class="empty">No products captured in this melt yet.</p>';
  $("product-info-dialog")?.showModal();
}
$("main-product-info-button")?.addEventListener("click", showProductInfo);
async function selectFurnace(button, recoveredMelt) {
  if (selectingFurnace) return;
  selectingFurnace = true;
  button.disabled = true;
  try {
    const melt = recoveredMelt === undefined
      ? (await requestJson(`/api/melts/unfinished?furnace_id=${encodeURIComponent(button.dataset.id)}`)).melt
      : recoveredMelt;
    const allocation = await requestJson(`/api/material-allocations/${encodeURIComponent(button.dataset.id)}`);
    mainState.furnace = {id: button.dataset.id, name: button.dataset.name, imageUrl: button.dataset.imageUrl};
    mainState.allocation = allocation;
    restoreActiveMelt(melt);
    document.querySelectorAll(".furnace-choice").forEach(item => item.classList.remove("selected"));
    button.classList.add("selected");
    $("product-selector-title").textContent = `${mainState.furnace.name} products`;
    showFurnaceProducts();
    $("furnace-selector").hidden = true;
    $("product-selector").hidden = false;
    $("main-product-context").hidden = false;
    if (melt) {
      const selected = $("main-product-grid").querySelector(`[data-id="${melt.last_product_id}"]`);
      if (selected) selected.classList.add("selected");
    }
    updateCompleteButtons();
    if (!melt && allocation.configured) $("product-selector-title").textContent = `${mainState.furnace.name} products - ${allocation.melt_number}`;
    updateProductStageActions();
  } catch (error) {
    const message = $("capture-message");
    message.className = "capture-message failure";
    message.textContent = error.message;
    message.hidden = false;
  } finally {
    selectingFurnace = false;
    button.disabled = false;
  }
}
async function captureWeight(button) {
  if (capturingWeight) return;
  capturingWeight = true;
  $("furnace-back").disabled = true;
  $("complete-melt-button").disabled = true;
  const message = $("capture-message"); message.hidden = true; button.disabled = true;
  try {
    if (!mainState.furnace) throw new Error("Select a furnace before selecting an image");
    const material = mainState.allocation?.materials.find(row => row.id === Number(button.dataset.id));
    const threshPct = Number(mainState.allocation?.threshold_percent) || 0;
    if (material && material.allocated_kg !== null && material.allocated_kg !== undefined) {
      const allocatedKg = Number(material.allocated_kg);
      const capturedKg = Number(material.captured_kg || 0);
      const limitKg = allocatedKg * (1 + threshPct / 100);
      if (capturedKg >= limitKg) {
        const prodName = button.querySelector("span")?.childNodes[0]?.textContent?.trim() || "Material";
        const err = new Error(
          threshPct > 0
            ? `${prodName} in melt ${mainState.allocation?.melt_number || ""} has already reached its limit of ${formatAllowanceWeight(limitKg)} kg (${formatAllowanceWeight(allocatedKg)} kg allocated + ${threshPct}% threshold). Contact admin.`
            : `${prodName} in melt ${mainState.allocation?.melt_number || ""} has already reached its allocated ${formatAllowanceWeight(allocatedKg)} kg. Contact admin.`
        );
        err.code = "allocation_exceeded";
        throw err;
      }
    }
    if (!mainState.weight) throw new Error("Wait for a live weight before capturing");
    if (Number(mainState.weight) <= 0) throw new Error("Weight must be greater than zero to capture");
    if (!mainState.weightStable) throw new Error("Wait for the weight to remain unchanged for 2 seconds before capturing");
    const payload = {product_image_id:Number(button.dataset.id), furnace_id:mainState.furnace.id, weight:mainState.weight};
    if (mainState.activeMelt) {
      payload.melt_number = mainState.activeMelt.number;
      payload.melt_serial = mainState.activeMelt.serial;
    }
    const result = await requestJson("/api/weight-captures", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    restoreActiveMelt(result);
    mainState.successSeconds = result.success_display_seconds ?? mainState.successSeconds;
    updateCompleteButtons();
    document.querySelectorAll(".product-choice").forEach(item => item.classList.remove("selected")); button.classList.add("selected");
    showCaptureSuccess(result);
    await refreshAllocation();
  } catch (error) {
    if (error.code === "threshold_exceeded" || error.code === "allocation_exceeded" || error.code === "allocation_required") {
      $("threshold-dialog-message").textContent = error.message;
      $("threshold-dialog").showModal();
    } else {
      message.className = "capture-message failure"; message.textContent = error.message; message.hidden = false;
    }
  }
  finally {
    button.disabled = false;
    capturingWeight = false;
    $("furnace-back").disabled = false;
    $("complete-melt-button").disabled = false;
  }
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
      mainState.activeMelt.captures = mainState.activeMelt.captures.filter(item => item.id !== result.id);
      if (!mainState.activeMelt.captureIds.length) mainState.activeMelt = null;
      updateCompleteButtons();
    }
    clearInterval(successTimer); pendingCaptureId = null; $("capture-success-screen").hidden = true;
    await refreshAllocation();
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
  $("furnace-back").disabled = true;
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
    $("furnace-back").disabled = false;
    buttons.forEach(button => { button.disabled = false; });
    updateCompleteButtons();
  }
}
$("complete-melt-button").addEventListener("click", requestMeltCompletion);
$("confirm-complete-melt").addEventListener("click", completeActiveMelt);
$("furnace-back").addEventListener("click", () => {
  if (capturingWeight || $("furnace-back").disabled) return;
  mainState.furnace = null;
  mainState.activeMelt = null;
  $("capture-message").hidden = true;
  $("product-selector").hidden = true;
  $("main-product-context").hidden = true;
  $("furnace-selector").hidden = false;
  updateCompleteButtons();
  updateProductStageActions();
});
async function initializeMain() {
  try {
    await loadFurnaces();
    await loadProducts();
    const {melt} = await requestJson("/api/melts/unfinished");
    if (melt) {
      const button = $("furnace-grid").querySelector(`[data-id="${melt.furnace_id}"]`);
      if (button) await selectFurnace(button, melt);
    }
  } catch (error) {
    const message = $("capture-message");
    message.className = "capture-message failure";
    message.textContent = error.message;
    message.hidden = false;
    return;
  }
  try {
    mainState.settings = await requestJson("/api/settings");
    const displaySettings = await requestJson("/api/capture-display-settings").catch(() => ({success_display_seconds: 10}));
    mainState.successSeconds = displaySettings.success_display_seconds;
    if (mainState.settings?.saved_data) {
      if ($("main-live-weight").textContent === "------" || !$("main-live-weight").textContent) {
        $("main-live-weight").textContent = mainState.settings.saved_data;
      }
      if (!mainState.weight) {
        mainState.weight = mainState.settings.saved_data;
        mainState.weightStable = true;
      }
    }
    if (!mainState.settings.id) throw new Error("Save communication settings before using the Main Screen");
    const status = await requestJson("/api/data?after=0");
    if (!status.connected && !status.connecting) await requestJson("/api/connect", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(mainState.settings)});
  } catch (error) { setStatus(false, false, error.message); }
}
initializeMain();
