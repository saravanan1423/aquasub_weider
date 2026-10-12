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

  const wifiForm=document.getElementById("wifi-form");
  const wifiNetwork=document.getElementById("wifi-network");
  const wifiPassword=document.getElementById("wifi-password");
  const wifiAuto=document.getElementById("wifi-autoconnect");
  let networks=[];
  let currentWifi=null;
  let wifiBusy=false;
  let refreshing=false;
  let autoChanged=false;
  const tabs=[document.getElementById("device-tab"), document.getElementById("wifi-tab")];

  function wifiMessage(message, error=false) {
    const box=document.getElementById("wifi-message");
    box.hidden=!message;
    box.className=`form-message ${error ? "form-error" : "form-success"}`;
    box.textContent=message;
  }
  function selectedSsid() {
    if (!wifiNetwork.value) return "";
    return wifiNetwork.value === "manual" ? document.getElementById("wifi-ssid").value
      : networks[Number(wifiNetwork.value)]?.ssid || "";
  }
  function updateWifiControls() {
    const blocked=wifiBusy || refreshing;
    document.getElementById("wifi-connect").disabled=blocked || !wifiNetwork.value || !selectedSsid();
    document.getElementById("wifi-save-auto").disabled=blocked || !currentWifi?.connected || selectedSsid() !== currentWifi.ssid;
    document.getElementById("wifi-refresh").disabled=blocked;
    wifiForm.querySelectorAll("input, select").forEach(input=>input.disabled=wifiBusy);
  }
  async function wifiRequest(path, options) {
    let response;
    try { response=await fetch(`/api/wifi/${path}`, options); }
    catch { throw new Error("Connection to the Pi changed. Reopen the app using its current Wi-Fi IP address."); }
    let result;
    try { result=await response.json(); }
    catch { throw new Error("Connection to the Pi changed. Reopen the app using its current Wi-Fi IP address."); }
    if (!response.ok) throw new Error(result.error || "Wi-Fi operation failed");
    return result;
  }
  async function refreshWifiStatus() {
    const data=await wifiRequest("status");
    currentWifi=data;
    document.getElementById("wifi-current-network").textContent=data.ssid || "Wi-Fi";
    const badge=document.getElementById("wifi-status");
    badge.className=`status ${data.connected ? "connected" : "disconnected"}`;
    badge.querySelector("strong").textContent=data.connected ? "Connected" : "Disconnected";
    document.getElementById("wifi-interface").textContent=data.interface;
    document.getElementById("wifi-ip-address").textContent=data.ip_addresses.join(", ") || (data.connected ? "Waiting for IP" : "-");
    document.getElementById("wifi-current-auto").textContent=data.connected ? data.autoconnect ? "On" : "Off" : "-";
    if (!autoChanged && selectedSsid() === data.ssid) wifiAuto.checked=data.autoconnect;
    updateWifiControls();
  }
  async function refreshWifi() {
    if (wifiBusy || refreshing) return;
    refreshing=true;
    updateWifiControls();
    const previous=selectedSsid();
    const manual=wifiNetwork.value === "manual";
    let statusLoaded=false;
    try {
      await refreshWifiStatus();
      statusLoaded=true;
      const data=await wifiRequest("networks");
      networks=data.networks;
      wifiNetwork.replaceChildren(new Option("Select network", ""));
      networks.forEach((network,index)=>wifiNetwork.add(new Option(`${network.ssid} - ${network.signal}% - ${network.security || "Open"}`, String(index))));
      wifiNetwork.add(new Option("Other / hidden network", "manual"));
      const index=networks.findIndex(network=>network.ssid === (previous || currentWifi.ssid));
      if (manual) wifiNetwork.value="manual";
      else if (index >= 0) wifiNetwork.value=String(index);
      if (!autoChanged && selectedSsid() === currentWifi.ssid) wifiAuto.checked=currentWifi.autoconnect;
      if (!networks.length) wifiMessage("No Wi-Fi networks found. Refresh or select Other / hidden network.", true);
    } catch (error) {
      wifiMessage(error.message, true);
      if (!statusLoaded) clearWifiStatus();
    } finally {
      refreshing=false;
      updateWifiControls();
    }
  }
  function clearWifiStatus() {
    const badge=document.getElementById("wifi-status");
    badge.className="status disconnected";
    badge.querySelector("strong").textContent="Unavailable";
    currentWifi=null;
    document.getElementById("wifi-current-network").textContent="Wi-Fi";
    document.getElementById("wifi-interface").textContent="-";
    document.getElementById("wifi-ip-address").textContent="-";
    document.getElementById("wifi-current-auto").textContent="-";
    updateWifiControls();
  }
  tabs.forEach(tab=>tab.addEventListener("click",()=>{
    tabs.forEach(button=>{
      const selected=button === tab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      document.getElementById(button.getAttribute("aria-controls")).hidden=!selected;
    });
    if (tab.id === "wifi-tab") { wifiMessage(""); refreshWifi(); }
  }));
  wifiNetwork.addEventListener("change",()=>{
    const manual=wifiNetwork.value === "manual";
    document.querySelectorAll(".wifi-manual-field").forEach(field=>field.hidden=!manual);
    document.getElementById("wifi-ssid").required=manual;
    resetWifiPassword();
    wifiAuto.checked=selectedSsid() === currentWifi?.ssid ? currentWifi.autoconnect : true;
    autoChanged=false;
    wifiMessage("");
    updateWifiControls();
  });
  document.getElementById("wifi-ssid").addEventListener("input", updateWifiControls);
  wifiAuto.addEventListener("change",()=>{ autoChanged=true; });
  document.getElementById("wifi-refresh").addEventListener("click",()=>{wifiMessage("");refreshWifi();});
  const eye=document.getElementById("wifi-password-toggle");
  function resetWifiPassword() {
    wifiPassword.value="";
    wifiPassword.type="password";
    eye.classList.remove("password-visible");
    eye.title="Show password";
    eye.setAttribute("aria-label", eye.title);
    eye.setAttribute("aria-pressed", "false");
    wifiPassword.dispatchEvent(new Event("input", {bubbles:true}));
  }
  eye.addEventListener("mousedown",event=>event.preventDefault());
  eye.addEventListener("click",()=>{
    const visible=wifiPassword.type === "password";
    wifiPassword.type=visible ? "text" : "password";
    eye.classList.toggle("password-visible", visible);
    eye.setAttribute("aria-pressed", String(visible));
    eye.title=visible ? "Hide password" : "Show password";
    eye.setAttribute("aria-label", eye.title);
    wifiPassword.dispatchEvent(new Event("input", {bubbles:true}));
  });
  async function changeWifi(path, payload) {
    if (wifiBusy || refreshing) return;
    wifiBusy=true;
    wifiMessage("");
    updateWifiControls();
    const button=document.getElementById(path === "connect" ? "wifi-connect" : "wifi-save-auto");
    button.textContent=path === "connect" ? "Connecting..." : "Saving...";
    try {
      await wifiRequest(path, {method:path === "connect" ? "POST" : "PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      resetWifiPassword();
      autoChanged=false;
      await refreshWifiStatus();
      wifiMessage(path === "connect" ? "Wi-Fi connected" : "Auto-connect saved");
    } catch (error) {
      wifiMessage(error.message || "Connection to the Pi changed. Reopen the app using its new Wi-Fi IP.", true);
    } finally {
      wifiBusy=false;
      button.textContent=path === "connect" ? "Connect" : "Save auto-connect";
      updateWifiControls();
    }
  }
  wifiForm.addEventListener("submit",event=>{
    event.preventDefault();
    changeWifi("connect", {ssid:selectedSsid(), password:wifiPassword.value, autoconnect:wifiAuto.checked,
      hidden:wifiNetwork.value === "manual" && document.getElementById("wifi-hidden").checked});
  });
  document.getElementById("wifi-save-auto").addEventListener("click",()=>{
    if (currentWifi?.connected) changeWifi("autoconnect", {profile_id:currentWifi.profile_id, autoconnect:wifiAuto.checked});
  });
  setInterval(()=>{
    if (!document.hidden && !document.getElementById("wifi-panel").hidden && !wifiBusy && !refreshing) {
      refreshWifiStatus().catch(error=>{clearWifiStatus();wifiMessage(error.message, true);});
    }
  }, 5000);
})();
