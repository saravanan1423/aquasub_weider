const $ = id => document.getElementById(id);
let editingId = null;
let productImages = [];
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function updateClock() { $("capture-time").value = new Date().toLocaleString(); }
function showMessage(message, error = false) { $("form-message").hidden = !message; $("form-message").className = `form-message ${error ? "form-error" : "form-success"}`; $("form-message").textContent = message; }
$("image-file").addEventListener("change", () => {
  const file = $("image-file").files[0]; if (!file) return;
  $("preview").src = URL.createObjectURL(file); $("preview").hidden = false; $("drop-message").hidden = true;
  if (!$("image-name").value) $("image-name").value = file.name.replace(/\.[^.]+$/, "");
});
async function loadImages() {
  const response = await fetch("/api/product-images"); productImages = await response.json(); $("image-count").textContent = productImages.length;
  $("gallery").innerHTML = productImages.length ? productImages.map(item => `<article class="image-card"><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.image_name)}" loading="lazy"><div><h3>${escapeHtml(item.image_name)}</h3><p>${escapeHtml(item.description || "No description")}</p><small>${new Date(item.captured_at).toLocaleString()} · ${escapeHtml(item.created_by)}</small><div class="card-actions"><button type="button" class="edit-image" data-id="${item.id}">Edit</button><button type="button" class="delete-image" data-id="${item.id}">Delete</button></div></div></article>`).join("") : '<p class="gallery-empty">No images uploaded yet.</p>';
  document.querySelectorAll(".edit-image").forEach(button => button.addEventListener("click", () => beginEdit(Number(button.dataset.id))));
  document.querySelectorAll(".delete-image").forEach(button => button.addEventListener("click", () => deleteImage(Number(button.dataset.id))));
}

function finishEdit() {
  editingId = null; $("upload-form").reset(); $("image-file").required = true; $("upload-button").textContent = "Save image"; $("cancel-edit").hidden = true;
  $("preview").hidden = true; $("preview").removeAttribute("src"); $("drop-message").hidden = false; updateClock();
}
function beginEdit(id) {
  const item = productImages.find(image => image.id === id); if (!item) return;
  editingId = id; $("image-name").value = item.image_name; $("description").value = item.description || "";
  $("image-file").required = false; $("preview").src = item.image_url; $("preview").hidden = false; $("drop-message").hidden = true;
  $("upload-button").textContent = "Update image"; $("cancel-edit").hidden = false; showMessage(""); window.scrollTo({top:0, behavior:"smooth"});
}
async function deleteImage(id) {
  const item = productImages.find(image => image.id === id); if (!item || !confirm(`Delete “${item.image_name}”?`)) return;
  try {
    const response = await fetch(`/api/product-images/${id}`, {method:"DELETE"}); const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Delete failed");
    if (editingId === id) finishEdit(); showMessage("Image deleted successfully"); await loadImages();
  } catch (error) { showMessage(error.message, true); }
}
$("cancel-edit").addEventListener("click", finishEdit);
$("upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  showMessage(""); const button = $("upload-button"); button.disabled = true; button.textContent = "Saving…";
  try {
    const response = await fetch(editingId ? `/api/product-images/${editingId}` : "/api/product-images", {method:editingId ? "PUT" : "POST", body:new FormData(form)}); const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Upload failed");
    const wasEditing = editingId !== null; finishEdit(); showMessage(wasEditing ? "Image updated successfully" : "Image saved successfully"); await loadImages();
  } catch (error) { showMessage(error.message, true); } finally { button.disabled = false; button.textContent = "Save image"; }
});
updateClock(); setInterval(updateClock, 1000); loadImages();
