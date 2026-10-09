(() => {
  document.addEventListener("contextmenu", event => event.preventDefault(), {capture: true});
  document.addEventListener("dragstart", event => {
    if (event.target instanceof Element && event.target.closest("img, a")) event.preventDefault();
  }, {capture: true});
  document.addEventListener("keydown", event => {
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) event.preventDefault();
  }, {capture: true});
  const isAdmin = document.currentScript?.dataset.admin === "true";
  document.documentElement.dataset.admin = String(isAdmin);
  document.querySelectorAll(".sidebar nav .nav-link").forEach(link => {
    if (link.title) return;
    const label = [...link.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent.trim()).filter(Boolean).join(" ");
    if (label) link.title = label;
  });
  if (!isAdmin) return;

  const navigation = document.querySelector(".sidebar nav");
  if (navigation) {
    const logoutForm = navigation.querySelector(".logout-form");
    const deviceLink = document.createElement("a");
    deviceLink.className = `nav-link device-nav-link${window.location.pathname === "/device-settings" ? " active" : ""}`;
    deviceLink.href = "/device-settings";
    deviceLink.title = "Device Settings";
    deviceLink.setAttribute("aria-label", "Device Settings");
    deviceLink.innerHTML = '<span aria-hidden="true">D</span><span class="device-nav-label">Device Settings</span>';
    navigation.insertBefore(deviceLink, navigation.querySelector('a[href="/images"]') || navigation.querySelector('a[href="/users"]') || logoutForm);
    const rustdeskButton = document.createElement("button");
    rustdeskButton.className = "rustdesk-launch";
    rustdeskButton.type = "button";
    rustdeskButton.title = "Open RustDesk on this device";
    rustdeskButton.setAttribute("aria-label", rustdeskButton.title);
    const icon = document.createElement("img");
    icon.src = "/static/rustdesk-logo.svg";
    icon.alt = "";
    rustdeskButton.appendChild(icon);
    navigation.insertBefore(rustdeskButton, logoutForm);

    const status = document.createElement("div");
    status.className = "rustdesk-status";
    status.setAttribute("role", "status");
    status.hidden = true;
    document.body.appendChild(status);
    rustdeskButton.addEventListener("click", async () => {
      rustdeskButton.disabled = true;
      status.hidden = true;
      try {
        const response = await fetch("/api/rustdesk/open", {method: "POST"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not open RustDesk");
        status.className = "rustdesk-status form-success";
        status.textContent = result.message;
      } catch (error) {
        status.className = "rustdesk-status form-error";
        status.textContent = error.message;
      } finally {
        status.hidden = false;
        rustdeskButton.disabled = false;
      }
    });
  }
})();
