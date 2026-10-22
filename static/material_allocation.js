const $ = id => document.getElementById(id);
let allocation = null;
let loadVersion = 0;
let saving = false;
let verificationTarget = null;
let furnaces = [];
let productImages = [];
let messageTimer = null;
let additionalCard = null;
let additionalBase = 0;
let actionNotice = null;
let checkingPendingAlerts = false;
const acknowledgedActions = new Set();
function updateAllocationClock() {
  const now = new Date();
  $("allocation-date-time").dateTime = now.toISOString();
  $("allocation-date").textContent = now.toLocaleDateString([], {year:"numeric", month:"short", day:"numeric"});
  $("allocation-time").textContent = now.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"});
}
updateAllocationClock();
setInterval(updateAllocationClock, 1000);
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function formatCapturedKg(value) {
  const amount = Number(value ?? 0);
  return (Number.isFinite(amount) && amount >= 0 ? amount : 0).toFixed(3).replace(/\.?0+$/, "");
}
function normalizeAllocation(data) {
  const materials = (data.materials || []).map(item => ({
    ...item, allocated_kg: item.allocated_kg ?? null,
    captured_kg: formatCapturedKg(item.captured_kg)
  })).sort((first, second) => Number(second.id) - Number(first.id));
  const total = data.total_captured_kg ?? materials.reduce((sum, item) => sum + Number(item.captured_kg), 0);
  return {...data, materials, total_captured_kg: formatCapturedKg(total)};
}
async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.headers.get("content-type")?.includes("application/json")) {
    if (response.redirected && new URL(response.url).pathname === "/login") throw new Error("Your session expired. Sign in again.");
    if (response.status === 404 || response.status === 405) throw new Error("This feature is not loaded in the running server. Restart the app and reload this screen.");
    throw new Error(`Server returned an unexpected response (HTTP ${response.status}). Try again or contact admin.`);
  }
  let data;
  try { data = await response.json(); }
  catch { throw new Error("Server returned an invalid response. Reload this screen and try again."); }
  if (!data || typeof data !== "object") throw new Error("Server returned an invalid response. Reload this screen and try again.");
  if (!response.ok) throw new Error(data.error || "Request failed");
  return url.startsWith("/api/material-allocations/") ? normalizeAllocation(data) : data;
}
function showMessage(message = "", error = false) {
  clearTimeout(messageTimer);
  $("allocation-message").textContent = message;
  $("allocation-message").className = `shift-message ${error ? "form-error" : "form-success"}`;
  $("allocation-message").hidden = !message;
  if (message && !error) messageTimer = setTimeout(() => { $("allocation-message").hidden = true; }, 3000);
}
function renderAllocation(data) {
  allocation = data;
  const meltEl = $("allocation-melt");
  if (meltEl) {
    meltEl.textContent = data.melt_number;
    meltEl.hidden = false;
  }
  const furnace = furnaces.find(item => item.id === data.furnace_id);
  const titleEl = $("allocation-title");
  if (titleEl) {
    titleEl.textContent = `${furnace?.name || "Furnace"} - Material allocation`;
    titleEl.hidden = false;
  }
  const backEl = $("allocation-back");
  if (backEl) backEl.hidden = false;
  const infoEl = $("allocation-info-button");
  if (infoEl) infoEl.hidden = false;
  const stageEl = $("allocation-furnace-stage");
  if (stageEl) stageEl.hidden = true;
  const editorEl = $("allocation-editor");
  if (editorEl) editorEl.hidden = false;
  const materialsEl = $("allocation-materials");
  if (materialsEl) {
    materialsEl.innerHTML = data.materials.map(item => {
      const imageUrl = item.image_url || productImages.find(product => product.id === item.id)?.image_url || "";
      const selected = item.allocated_kg !== null;
      return `<article class="product-choice allocation-material-card ${selected ? "selected" : ""}" data-id="${item.id}"><div class="allocation-material-heading"><button type="button" class="allocation-material-media" aria-label="Enter allocated weight for ${escapeHtml(item.image_name)}" aria-pressed="${selected}"><img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(item.image_name)}"></button><strong class="allocation-material-name">${escapeHtml(item.image_name)}</strong></div><div class="allocation-material-values"><div class="allocation-allocated-label"><label for="allocation-weight-${item.id}">Allocated (kg)</label><button class="allocation-add-button" type="button" data-add-weight title="Add additional kg" aria-label="Add additional kg to ${escapeHtml(item.image_name)}"><i class="ui-icon" style="--icon:url('/static/icons/plus.svg')" aria-hidden="true"></i></button></div><input id="allocation-weight-${item.id}" aria-label="${escapeHtml(item.image_name)} allocated kg" type="text" inputmode="decimal" data-allocated-weight pattern="[0-9]+([.][0-9]{1,3})?" maxlength="13" value="${escapeHtml(item.allocated_kg)}" readonly ${selected ? "required" : "disabled"}></div><progress class="allocation-progress" max="100" value="0" aria-label="${escapeHtml(item.image_name)} progress"></progress><div class="allocation-material-ratio"><strong data-weight-ratio>0 kg / 0 kg</strong><strong data-percent>0%</strong></div></article>`;
    }).join("") || '<p class="gallery-empty">No materials added to this furnace.</p>';
  }
  const formEl = $("allocation-form");
  if (formEl) formEl.hidden = false;
  const saveEl = $("allocation-save");
  if (saveEl) saveEl.disabled = !data.materials.length;
  updateAllocationStatus(data);
  updateAllocationProgress();
}
function updateAllocationProgress() {
  document.querySelectorAll(".allocation-material-card").forEach(card => {
    const input = card.querySelector("[data-allocated-weight]");
    const allocated = Number(formatCapturedKg(input.disabled ? 0 : input.value));
    const captured = Number(allocation?.materials.find(item => Number(item.id) === Number(card.dataset.id))?.captured_kg || 0);
    const percent = allocated > 0 ? Math.round(captured / allocated * 100) : 0;
    card.querySelector("progress").value = Math.min(100, percent);
    card.querySelector("[data-weight-ratio]").textContent = `${formatCapturedKg(captured)} kg / ${formatCapturedKg(allocated)} kg`;
    card.querySelector("[data-percent]").textContent = `${percent}%`;
    card.classList.toggle("allocation-over-limit", allocated > 0 && captured > allocated);
  });
  updateAllocationDashboard();
}
function updateAllocationDashboard() {
  if (!allocation) return;
  let allocatedUnits = 0;
  document.querySelectorAll("[data-allocated-weight]").forEach(input => {
    allocatedUnits += input.disabled ? 0 : (kgUnits(input.value) || 0);
  });
  const captured = Number(allocation.total_captured_kg || 0);
  const percent = allocatedUnits > 0 ? Math.round(captured / (allocatedUnits / 1000) * 100) : 0;
  const furnace = furnaces.find(item => item.id === allocation.furnace_id);
  $("allocation-dashboard-context").textContent = `${furnace?.name || "Furnace"} - Melt ${allocation.melt_number}`;
  $("allocation-dashboard-allocated").textContent = `${formatKgUnits(allocatedUnits)} kg`;
  $("allocation-dashboard-captured").textContent = `${formatCapturedKg(captured)} kg`;
  $("allocation-dashboard-percent").textContent = `${percent}%`;
  const ring = $("allocation-dashboard-ring");
  ring.style.setProperty("--completion", Math.min(100, percent));
  ring.querySelector("strong").textContent = `${percent}%`;
  ring.setAttribute("aria-valuenow", String(Math.min(100, percent)));
  ring.setAttribute("aria-valuetext", `${percent}% completed`);
}
function openAllocationDashboard() {
  if (!allocation) return;
  const keyboard = document.querySelector(".screen-keyboard");
  if (keyboard) keyboard.hidden = true;
  updateAllocationDashboard();
  $("allocation-dashboard-dialog").showModal();
}
$("allocation-info-button").addEventListener("click", openAllocationDashboard);
function awaitingVerification(data) {
  return Boolean(data?.status === "awaiting_verification" && data.main_completed_at);
}
function actionKey(data) {
  return `${data.furnace_id}:${data.melt_number}:${data.main_completed_at}`;
}
function showActionRequired(data) {
  if (!awaitingVerification(data) || acknowledgedActions.has(actionKey(data)) || document.querySelector("dialog[open]")) return;
  const keyboard = document.querySelector(".screen-keyboard");
  if (keyboard) keyboard.hidden = true;
  actionNotice = data;
  const furnace = furnaces.find(item => item.id === data.furnace_id);
  $("allocation-action-message").textContent = `Melt ${data.melt_number} (${furnace?.name || "this furnace"}) was marked as completed on the Main Screen and is awaiting verification.`;
  $("allocation-action-melt").textContent = data.melt_number;
  $("allocation-action-total").textContent = `${data.total_captured_kg} kg`;
  $("allocation-action-dialog").showModal();
}
async function refreshPendingAlerts() {
  if (allocation || saving || document.hidden || checkingPendingAlerts || !furnaces.length) return;
  const version = loadVersion;
  checkingPendingAlerts = true;
  try {
    const results = await Promise.all(furnaces.map(furnace =>
      jsonFetch(`/api/material-allocations/${furnace.id}`).catch(() => null)));
    if (version !== loadVersion || allocation || saving) return;
    results.forEach(data => {
      if (!data) return;
      const button = $("allocation-furnace-grid").querySelector(`[data-id="${data.furnace_id}"]`);
      const furnace = furnaces.find(item => item.id === data.furnace_id);
      if (!button || !furnace) return;
      const pending = awaitingVerification(data);
      button.classList.toggle("allocation-review-required", pending);
      button.querySelector("span").innerHTML = `${escapeHtml(furnace.name)}${pending ? '<small class="allocation-furnace-alert">Action required</small>' : ""}`;
    });
    const pending = results.find(data => awaitingVerification(data) && !acknowledgedActions.has(actionKey(data)));
    if (pending) showActionRequired(pending);
  } finally { checkingPendingAlerts = false; }
}
$("allocation-action-dialog").addEventListener("close", () => {
  if (actionNotice) acknowledgedActions.add(actionKey(actionNotice));
  actionNotice = null;
});
$("allocation-action-later").addEventListener("click", () => $("allocation-action-dialog").close());
$("allocation-action-review").addEventListener("click", async () => {
  const notice = actionNotice;
  if (!notice) return;
  acknowledgedActions.add(actionKey(notice));
  $("allocation-action-dialog").close();
  if (allocation?.furnace_id !== notice.furnace_id || allocation?.melt_number !== notice.melt_number) {
    $("allocation-furnace").value = String(notice.furnace_id);
    await selectAllocationFurnace();
  }
  if (allocation?.furnace_id === notice.furnace_id && allocation?.melt_number === notice.melt_number && !document.querySelector("dialog[open]")) openAllocationDashboard();
});
function updateAllocationStatus(data) {
  const pending = awaitingVerification(data);
  $("allocation-status").textContent = pending
    ? `Action required: Main Screen completed ${data.melt_number} - Awaiting verification. Total: ${data.total_captured_kg} kg`
    : data.configured ? `Allocated - ${data.total_captured_kg} kg captured` : "Allocation not saved";
  $("allocation-status").classList.toggle("awaiting-verification", pending);
  $("allocation-status").hidden = !pending;
  $("allocation-complete").disabled = !pending || saving;
  $("allocation-complete").hidden = !pending;
  $("allocation-dashboard-complete").disabled = !pending || saving;
  $("allocation-dashboard-complete").hidden = !pending;
  for (const id of ["allocation-continue", "allocation-dashboard-continue"]) {
    $(id).hidden = !pending;
    $(id).disabled = !pending || saving;
  }
  $("allocation-save-label").textContent = pending ? "Save and reopen melt" : "Save allocation";
  if (pending && !saving) showActionRequired(data);
}
async function refreshStatus() {
  if (saving || document.hidden) return;
  if (!allocation) { await refreshPendingAlerts(); return; }
  const version = loadVersion;
  try {
    const data = await jsonFetch(`/api/material-allocations/${allocation.furnace_id}`);
    if (version !== loadVersion || saving) return;
    if (data.melt_number !== allocation.melt_number) {
      $("allocation-complete").disabled = true;
      $("allocation-save").disabled = true;
      showMessage("The melt number has changed. Select the furnace again.", true);
      return;
    }
    allocation = data;
    updateAllocationStatus(data);
    updateAllocationProgress();
  } catch (error) { showMessage(error.message, true); }
}
setInterval(refreshStatus, 5000);
document.addEventListener("visibilitychange", refreshStatus);
async function selectAllocationFurnace() {
  const version = ++loadVersion;
  allocation = null;
  if ($("allocation-form")) $("allocation-form").hidden = true;
  if ($("allocation-melt")) $("allocation-melt").textContent = "-";
  showMessage();
  const id = $("allocation-furnace") ? $("allocation-furnace").value : "";
  if (!id) return;
  try {
    const data = await jsonFetch(`/api/material-allocations/${id}`);
    if (version === loadVersion) renderAllocation(data);
  } catch (error) { if (version === loadVersion) showMessage(error.message, true); }
}
$("allocation-furnace").addEventListener("change", selectAllocationFurnace);
$("allocation-furnace-grid").addEventListener("click", event => {
  const button = event.target.closest(".furnace-choice");
  if (!button || saving) return;
  $("allocation-furnace").value = button.dataset.id;
  $("allocation-furnace").dispatchEvent(new Event("change"));
});
$("allocation-back").addEventListener("click", () => {
  if (saving) return;
  ++loadVersion;
  allocation = null;
  const furnaceSelect = $("allocation-furnace");
  if (furnaceSelect) furnaceSelect.value = "";
  if ($("allocation-editor")) $("allocation-editor").hidden = true;
  if ($("allocation-form")) $("allocation-form").hidden = true;
  if ($("allocation-furnace-stage")) $("allocation-furnace-stage").hidden = false;
  if ($("allocation-melt")) $("allocation-melt").hidden = true;
  if ($("allocation-back")) $("allocation-back").hidden = true;
  if ($("allocation-info-button")) $("allocation-info-button").hidden = true;
  const titleEl = $("allocation-title");
  if (titleEl) {
    titleEl.textContent = "Melt Material Allocation";
    titleEl.hidden = true;
  }
  const keyboard = document.querySelector(".screen-keyboard");
  if (keyboard) keyboard.hidden = true;
  showMessage();
  refreshPendingAlerts();
});
$("allocation-materials").addEventListener("click", event => {
  const card = event.target.closest(".allocation-material-card");
  if (!card || saving) return;
  if (event.target.closest("[data-add-weight]")) {
    const base = kgUnits(card.querySelector("[data-allocated-weight]").value);
    if (base === null || base === 0) {
      openSetWeight(card);
    } else {
      openAdditionalWeight(card);
    }
    return;
  }
  openSetWeight(card);
});
$("allocation-materials").addEventListener("input", event => {
  if (!event.target.matches("[data-allocated-weight]")) return;
  const selected = event.target.value.trim() !== "";
  const card = event.target.closest(".allocation-material-card");
  card.classList.toggle("selected", selected);
  card.querySelector(".allocation-material-media").setAttribute("aria-pressed", String(selected));
  event.target.required = selected;
  updateAllocationProgress();
});

function kgUnits(value) {
  const text = String(value ?? "").trim();
  if (!text) return 0;
  if (!/^[0-9]+(?:\.[0-9]{1,3})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const units = Number(whole) * 1000 + Number(fraction.padEnd(3, "0"));
  return Number.isSafeInteger(units) && units <= 999999999999 ? units : null;
}
function formatKgUnits(units) {
  const whole = Math.floor(units / 1000);
  const fraction = String(units % 1000).padStart(3, "0").replace(/0+$/, "");
  return `${whole}${fraction ? `.${fraction}` : ""}`;
}

let setCard = null;
function openSetWeight(card) {
  if ($("allocation-set-dialog")?.open || $("allocation-add-dialog")?.open) return;
  setCard = card;
  const currentVal = card.querySelector("[data-allocated-weight]").value;
  const base = kgUnits(currentVal);
  $("allocation-set-form").reset();
  const materialName = card.querySelector(".allocation-material-name")?.textContent || "Material";
  $("allocation-set-title").textContent = `Set weight - ${materialName}`;
  $("allocation-set-current").textContent = `${formatKgUnits(base !== null ? base : 0)} kg`;
  $("allocation-set-kg").value = base !== null && base > 0 ? formatKgUnits(base) : "";
  updateSetPreview();
  $("allocation-set-dialog").showModal();
  $("allocation-set-kg").focus({preventScroll:true});
  $("allocation-set-kg").select();
}
function updateSetPreview() {
  const text = $("allocation-set-kg").value.trim();
  const units = kgUnits(text);
  const valid = units !== null && units >= 0 && units <= 999999999999;
  if (text === "" || units === 0) {
    $("allocation-set-total").textContent = "0 kg";
    $("allocation-set-apply").disabled = false;
    const applyText = $("allocation-set-apply-text");
    if (applyText) applyText.textContent = "Clear";
    $("allocation-set-error").hidden = true;
    return;
  }
  $("allocation-set-total").textContent = `${formatKgUnits(valid ? units : 0)} kg`;
  $("allocation-set-apply").disabled = !valid;
  const applyText = $("allocation-set-apply-text");
  if (applyText) applyText.textContent = "Set kg";
  const invalid = text !== "" && !valid;
  $("allocation-set-error").hidden = !invalid;
  $("allocation-set-error").textContent = invalid ? "Enter positive allocated kg with up to 3 decimal places." : "";
}
$("allocation-set-kg").addEventListener("input", updateSetPreview);
$("allocation-set-form").addEventListener("submit", event => {
  event.preventDefault();
  if (!setCard) return;
  const text = $("allocation-set-kg").value.trim();
  const units = kgUnits(text);
  const input = setCard.querySelector("[data-allocated-weight]");
  if (text === "" || units === 0) {
    input.value = "";
    input.disabled = true;
    input.required = false;
    setCard.classList.remove("selected");
    setCard.querySelector(".allocation-material-media")?.setAttribute("aria-pressed", "false");
  } else if (units !== null && units > 0 && units <= 999999999999) {
    input.disabled = false;
    input.value = formatKgUnits(units);
    input.required = true;
    setCard.classList.add("selected");
    setCard.querySelector(".allocation-material-media")?.setAttribute("aria-pressed", "true");
  } else {
    return;
  }
  input.dispatchEvent(new Event("input", {bubbles:true}));
  input.dispatchEvent(new Event("change", {bubbles:true}));
  updateAllocationProgress();
  $("allocation-set-dialog").close();
});
$("allocation-set-cancel").addEventListener("click", () => $("allocation-set-dialog").close());
$("allocation-set-dialog").addEventListener("close", () => {
  const keyboard = $("allocation-set-dialog").querySelector(".screen-keyboard");
  if (keyboard) keyboard.hidden = true;
  setCard = null;
});

function openAdditionalWeight(card) {
  if ($("allocation-set-dialog")?.open || $("allocation-add-dialog")?.open) return;
  const base = kgUnits(card.querySelector("[data-allocated-weight]").value);
  if (base === null || base === 0) {
    openSetWeight(card);
    return;
  }
  additionalCard = card;
  additionalBase = base;
  $("allocation-add-form").reset();
  const materialName = card.querySelector(".allocation-material-name")?.textContent || "Material";
  $("allocation-add-title").textContent = `Add weight - ${materialName}`;
  $("allocation-add-current").textContent = `${formatKgUnits(base)} kg`;
  updateAdditionalPreview();
  $("allocation-add-dialog").showModal();
  $("allocation-add-kg").focus({preventScroll:true});
}

function updateAdditionalPreview() {
  const extra = kgUnits($("allocation-add-kg").value);
  const valid = extra !== null && extra > 0 && additionalBase + extra <= 999999999999;
  $("allocation-add-total").textContent = `${formatKgUnits(valid ? additionalBase + extra : additionalBase)} kg`;
  $("allocation-add-apply").disabled = !valid;
  const invalid = $("allocation-add-kg").value !== "" && !valid;
  $("allocation-add-error").hidden = !invalid;
  $("allocation-add-error").textContent = invalid ? "Enter positive additional kg with up to 3 decimal places, within the allocation limit." : "";
}

$("allocation-add-kg").addEventListener("input", updateAdditionalPreview);
$("allocation-add-form").addEventListener("submit", event => {
  event.preventDefault();
  const extra = kgUnits($("allocation-add-kg").value);
  if (!additionalCard || extra === null || extra <= 0 || additionalBase + extra > 999999999999) return;
  const input = additionalCard.querySelector("[data-allocated-weight]");
  input.disabled = false;
  input.value = formatKgUnits(additionalBase + extra);
  input.dispatchEvent(new Event("input", {bubbles:true}));
  input.dispatchEvent(new Event("change", {bubbles:true}));
  updateAllocationProgress();
  $("allocation-add-dialog").close();
});
$("allocation-add-cancel").addEventListener("click", () => $("allocation-add-dialog").close());
$("allocation-add-dialog").addEventListener("close", () => {
  const keyboard = $("allocation-add-dialog").querySelector(".screen-keyboard");
  if (keyboard) keyboard.hidden = true;
  additionalCard = null;
});
$("allocation-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!allocation || saving) return;
  const materials = Array.from($("allocation-materials").querySelectorAll(".allocation-material-card[data-id]"))
    .filter(row => row.classList.contains("selected"))
    .map(row => ({product_image_id: Number(row.dataset.id), allocated_kg: row.querySelector("[data-allocated-weight]").value}));
  $("allocation-save").disabled = true;
  $("allocation-furnace").disabled = true;
  saving = true;
  ++loadVersion;
  $("allocation-complete").disabled = true;
  try {
    const data = await jsonFetch(`/api/material-allocations/${allocation.furnace_id}`, {
      method: "PUT", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({melt_number: allocation.melt_number, materials})
    });
    renderAllocation(data);
    showMessage(`${data.melt_number} allocation saved`);
  } catch (error) { showMessage(error.message, true); }
  finally { saving = false; $("allocation-save").disabled = false; $("allocation-furnace").disabled = false; updateAllocationStatus(allocation); }
});
async function continueAllocationMelt() {
  if (!awaitingVerification(allocation) || saving) return;
  const target = {melt_number: allocation.melt_number, main_completed_at: allocation.main_completed_at};
  saving = true;
  ++loadVersion;
  $("allocation-save").disabled = true;
  $("allocation-furnace").disabled = true;
  updateAllocationStatus(allocation);
  try {
    const data = await jsonFetch(`/api/material-allocations/${allocation.furnace_id}/continue`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(target)
    });
    if ($("allocation-dashboard-dialog").open) $("allocation-dashboard-dialog").close();
    renderAllocation(data);
    showMessage(`${data.melt_number} continued with the existing allocation`);
  } catch (error) {
    if ($("allocation-dashboard-dialog").open) $("allocation-dashboard-dialog").close();
    showMessage(error.message, true);
  }
  finally {
    saving = false;
    $("allocation-save").disabled = !allocation.materials.length;
    $("allocation-furnace").disabled = false;
    updateAllocationStatus(allocation);
  }
}
$("allocation-continue").addEventListener("click", continueAllocationMelt);
$("allocation-dashboard-continue").addEventListener("click", continueAllocationMelt);
function requestAllocationCompletion() {
  if (!allocation?.main_completed_at || saving) return;
  verificationTarget = {melt_number: allocation.melt_number, main_completed_at: allocation.main_completed_at};
  $("allocation-complete-title").textContent = `Complete melt ${allocation.melt_number}?`;
  $("allocation-complete-summary").textContent = `Verified total: ${allocation.total_captured_kg} kg. Continue to the next melt number.`;
  if ($("allocation-dashboard-dialog").open) $("allocation-dashboard-dialog").close();
  $("allocation-complete-dialog").showModal();
}
$("allocation-complete").addEventListener("click", requestAllocationCompletion);
$("allocation-dashboard-complete").addEventListener("click", requestAllocationCompletion);
$("allocation-confirm").addEventListener("click", async () => {
  if (!allocation || saving) return;
  saving = true;
  ++loadVersion;
  $("allocation-confirm").disabled = true;
  $("allocation-furnace").disabled = true;
  try {
    const data = await jsonFetch(`/api/material-allocations/${allocation.furnace_id}/complete`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(verificationTarget)
    });
    renderAllocation(data);
    $("allocation-complete-dialog").close();
    showMessage(`${data.completed_melt_number} verified and completed`);
  } catch (error) {
    $("allocation-complete-dialog").close();
    showMessage(error.message, true);
  } finally {
    saving = false;
    $("allocation-confirm").disabled = false;
    $("allocation-furnace").disabled = false;
    updateAllocationStatus(allocation);
  }
});
Promise.all([jsonFetch("/api/furnaces"), jsonFetch("/api/product-images")]).then(([items, products]) => {
  productImages = products;
  furnaces = items;
  $("allocation-furnace").innerHTML += furnaces.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
  $("allocation-furnace-count").textContent = furnaces.length;
  $("allocation-furnace-grid").innerHTML = furnaces.map(item => `<button class="furnace-choice" data-id="${item.id}" type="button"><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.name)}"><span>${escapeHtml(item.name)}</span></button>`).join("") || '<p class="gallery-empty">No furnaces configured.</p>';
  refreshPendingAlerts();
}).catch(error => showMessage(error.message, true));
