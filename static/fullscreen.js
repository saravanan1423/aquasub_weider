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

  const rustdeskButton = document.querySelector("[data-rustdesk-launch]");
  if (rustdeskButton) {
    const status = document.getElementById("rustdesk-status");
    rustdeskButton.addEventListener("click", async () => {
      rustdeskButton.disabled = true;
      status.hidden = true;
      try {
        const response = await fetch("/api/rustdesk/open", {method: "POST"});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Could not open RustDesk");
        status.className = "form-message form-success";
        status.textContent = result.message;
      } catch (error) {
        status.className = "form-message form-error";
        status.textContent = error.message;
      } finally {
        status.hidden = false;
        rustdeskButton.disabled = false;
      }
    });
  }
})();
