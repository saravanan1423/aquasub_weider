(() => {
  const form = document.getElementById("device-settings-form");
  const status = document.getElementById("device-settings-message");

  function showMessage(text, error = false) {
    status.hidden = !text;
    status.className = `form-message ${error ? "form-error" : "form-success"}`;
    status.textContent = text;
  }

  async function requestSettings(options) {
    const response = await fetch("/api/device-settings", options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not load device settings");
    return result;
  }

  requestSettings().then(settings => {
    document.getElementById("pi-serial").value = settings.pi_serial || "Unavailable on this device";
    form.elements.device_id.value = settings.device_id;
    form.elements.location.value = settings.location;
    form.elements.installed_date.value = settings.installed_date;
  }).catch(error => showMessage(error.message, true));

  form.addEventListener("submit", async event => {
    event.preventDefault();
    showMessage("");
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const settings = await requestSettings({
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          device_id: form.elements.device_id.value,
          location: form.elements.location.value,
          installed_date: form.elements.installed_date.value
        })
      });
      form.elements.device_id.value = settings.device_id;
      showMessage("Device settings saved");
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  const backupForm = document.getElementById("usb-backup-form");
  const backupMessage = document.getElementById("backup-message");
  const driveSelect = document.getElementById("backup-drive");
  const backupSubmit = document.getElementById("backup-submit");
  const tabs = [document.getElementById("device-tab"), document.getElementById("backup-tab")];

  function backupStatus(message, error = false) {
    backupMessage.hidden = !message;
    backupMessage.className = `form-message ${error ? "form-error" : "form-success"}`;
    backupMessage.textContent = message;
  }

  function selectTab(tab) {
    for (const button of tabs) {
      const selected = button === tab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      document.getElementById(button.getAttribute("aria-controls")).hidden = !selected;
    }
    if (tab.id === "backup-tab") refreshDrives();
  }
  tabs.forEach(tab => tab.addEventListener("click", () => selectTab(tab)));

  async function refreshDrives() {
    const previous = driveSelect.value;
    backupSubmit.disabled = true;
    driveSelect.replaceChildren(new Option("Checking connected drives...", ""));
    try {
      const response = await fetch("/api/usb-drives");
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not check USB drives");
      driveSelect.replaceChildren(new Option(data.drives.length ? "Select USB drive" : "No USB drive", ""));
      for (const drive of data.drives) {
        const free = Math.round(drive.free_bytes / 1024 / 1024);
        driveSelect.add(new Option(`${drive.label} (${free} MB free)`, drive.id));
      }
      if (data.drives.some(drive => drive.id === previous)) driveSelect.value = previous;
      backupSubmit.disabled = !driveSelect.value;
      if (!data.drives.length) backupStatus("Connect and mount a writable USB drive, then refresh.", true);
      else backupStatus("");
    } catch (error) {
      driveSelect.replaceChildren(new Option("Unable to list USB drives", ""));
      backupStatus(error.message, true);
    }
  }
  document.getElementById("refresh-usb").addEventListener("click", refreshDrives);
  driveSelect.addEventListener("change", () => { backupSubmit.disabled = !driveSelect.value; backupStatus(""); });

  function fillOptions(select, firstLabel, items, value, label) {
    select.replaceChildren(new Option(firstLabel, "all"));
    for (const item of items) select.add(new Option(label(item), String(value(item))));
  }
  fetch("/api/capture-report-options").then(async response => {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load report filters");
    fillOptions(document.getElementById("backup-shift"), "All shifts combined", data.shifts, item => item.id, item => `${item.name} (${item.start_time} to ${item.end_time})`);
    fillOptions(document.getElementById("backup-furnace"), "All furnaces", data.furnaces, item => item.furnace_id, item => item.furnace_name);
  }).catch(error => backupStatus(error.message, true));

  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  document.getElementById("backup-from-date").value = today;
  document.getElementById("backup-to-date").value = today;
  document.getElementById("backup-range-type").addEventListener("change", event => {
    document.querySelectorAll(".backup-time-field").forEach(field => { field.hidden = event.target.value !== "custom"; });
  });

  backupForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!driveSelect.value) return;
    backupStatus("");
    backupSubmit.disabled = true;
    backupSubmit.textContent = "Backing up...";
    const payload = {
      drive_id: driveSelect.value,
      from_date: document.getElementById("backup-from-date").value,
      to_date: document.getElementById("backup-to-date").value,
      shift: document.getElementById("backup-shift").value,
      furnace: document.getElementById("backup-furnace").value,
      custom_time: document.getElementById("backup-range-type").value === "custom" ? "1" : "0",
      start_time: document.getElementById("backup-start-time").value,
      end_time: document.getElementById("backup-end-time").value
    };
    try {
      const response = await fetch("/api/report-usb-backup", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
      });
      let result;
      try {
        result = await response.json();
      } catch {
        throw new Error(`Backup could not finish (server response ${response.status}). Contact the administrator to check the application log.`);
      }
      if (!response.ok) throw new Error(result.error || "Backup failed");
      const reportStatus = result.total_melts === 0 ? "No data: empty CSV and PDF saved" : `${result.total_melts} melts saved as CSV and PDF`;
      backupStatus(`${reportStatus} to ${result.drive} / Weider Reports`);
    } catch (error) {
      await refreshDrives();
      backupStatus(error.message, true);
    } finally {
      backupSubmit.textContent = "Back up CSV + PDF";
      backupSubmit.disabled = !driveSelect.value;
    }
  });
})();
