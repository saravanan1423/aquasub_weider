const $ = id => document.getElementById(id);
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character])); }
async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function showMessage(message = "", error = false) {
  const element = $("melt-settings-message");
  element.hidden = !message;
  element.className = `shift-message ${error ? "form-error" : "form-success"}`;
  element.textContent = message;
}
function showThresholdMessage(message = "", error = false) {
  const element = $("melt-threshold-message");
  element.hidden = !message;
  element.className = `shift-message ${error ? "form-error" : "form-success"}`;
  element.textContent = message;
}
async function loadCaptureDisplaySettings() {
  const settings = await jsonFetch("/api/capture-display-settings");
  $("success-display-seconds").value = settings.success_display_seconds;
}
async function loadThresholdSettings() {
  const form = $("melt-threshold-form");
  if (!form) return;
  const settings = await jsonFetch("/api/melt-threshold-settings");
  form.elements.individual_threshold_kg.value = settings.individual_threshold_kg ?? "";
  form.elements.melt_threshold_kg.value = settings.melt_threshold_kg ?? "";
}
async function loadMeltSettings() {
  const furnaces = await jsonFetch("/api/melt-number-settings");
  $("melt-settings-list").innerHTML = furnaces.length ? furnaces.map(furnace => `
    <form class="melt-setting-row" data-furnace-id="${furnace.furnace_id}">
      <div class="melt-setting-furnace"><span>Furnace</span><strong>${escapeHtml(furnace.furnace_name)}</strong></div>
      <div class="melt-setting-next"><span>Next melt</span><strong>${escapeHtml(furnace.next_melt_number)}</strong></div>
      <div class="field"><label for="melt-start-${furnace.furnace_id}">Starting serial</label><input id="melt-start-${furnace.furnace_id}" name="start_serial" type="number" min="1" max="999999999" step="1" value="${furnace.start_serial}" required></div>
      <button class="primary" type="submit">Save</button>
    </form>`).join("") : '<p class="gallery-empty">Add a furnace in Product Master first.</p>';
  document.querySelectorAll(".melt-setting-row").forEach(form => form.addEventListener("submit", async event => {
    event.preventDefault();
    showMessage();
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const result = await jsonFetch(`/api/melt-number-settings/${form.dataset.furnaceId}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({start_serial: form.elements.start_serial.value})
      });
      showMessage(`Furnace ${form.querySelector(".melt-setting-furnace strong").textContent} will use ${result.next_melt_number} next`);
      await loadMeltSettings();
    } catch (error) { showMessage(error.message, true); }
    finally { button.disabled = false; }
  }));
}
loadMeltSettings().catch(error => showMessage(error.message, true));
if ($("capture-display-form")) {
  loadCaptureDisplaySettings().catch(error => {
    $("capture-display-message").hidden = false;
    $("capture-display-message").textContent = error.message;
  });
  $("capture-display-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    const message = $("capture-display-message");
    message.hidden = true;
    button.disabled = true;
    try {
      await jsonFetch("/api/capture-display-settings", {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({success_display_seconds: $("success-display-seconds").value})
      });
      message.className = "shift-message form-success";
      message.textContent = "Display duration saved";
    } catch (error) {
      message.className = "shift-message form-error";
      message.textContent = error.message;
    } finally {
      message.hidden = false;
      button.disabled = false;
    }
  });
}
if ($("melt-threshold-form")) {
  loadThresholdSettings().catch(error => showThresholdMessage(error.message, true));
  $("melt-threshold-form").addEventListener("submit", async event => {
    event.preventDefault();
    showThresholdMessage();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await jsonFetch("/api/melt-threshold-settings", {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          individual_threshold_kg: form.elements.individual_threshold_kg.value,
          melt_threshold_kg: form.elements.melt_threshold_kg.value
        })
      });
      showThresholdMessage("Weight thresholds saved");
    } catch (error) { showThresholdMessage(error.message, true); }
    finally { button.disabled = false; }
  });
}
