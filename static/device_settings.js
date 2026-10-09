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

})();
