const $ = id => document.getElementById(id);
let editingId = null;
let productImages = [];
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function updateClock() { $("capture-time").value = new Date().toLocaleString(); }
function showMessage(message, error = false) { $("form-message").hidden = !message; $("form-message").className = `form-message ${error ? "form-error" : "form-success"}`; $("form-message").textContent = message; }
async function readResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  if (response.status === 413) return {error: "The image is larger than 25 MB"};
  return {error: response.ok ? "Unexpected server response" : `Upload failed (${response.status})`};
}
function updateTypeFields() {
  const isProduct = $("image-type").value === "product";
  $("furnace-field").hidden = !isProduct; $("furnace-id").required = isProduct;
  const nameLabel = $("image-name-label");
  if (nameLabel) nameLabel.textContent = isProduct ? "Product name *" : "Furnace name *";
  $("image-name").placeholder = isProduct ? "Example: Product 1" : "Example: Furnace 1";
  if (editingId === null) $("form-title").textContent = isProduct ? "New product" : "New furnace";
}
$("image-file").addEventListener("change", () => {
  const file = $("image-file").files[0]; if (!file) return;
  $("preview").src = URL.createObjectURL(file); $("preview").hidden = false; $("drop-message").hidden = true;
  if (!$("image-name").value) $("image-name").value = file.name.replace(/\.[^.]+$/, "");
});
async function loadImages() {
  const response = await fetch("/api/product-images"); productImages = await response.json(); $("image-count").textContent = productImages.length;
  const furnaces = productImages.filter(item => item.image_type === "furnace");
  const productOption = $("product-type-option");
  if (productOption) {
    productOption.disabled = furnaces.length === 0;
    productOption.textContent = furnaces.length ? "Product" : "Product (add a furnace first)";
  }
  if (!furnaces.length && $("image-type").value === "product") $("image-type").value = "furnace";
  $("furnace-id").innerHTML = '<option value="">Select furnace</option>' + furnaces.map(item => `<option value="${item.id}">${escapeHtml(item.image_name)}</option>`).join("");
  updateTypeFields();
  $("image-count").textContent = furnaces.length;
  $("gallery").innerHTML = furnaces.length ? furnaces.map(furnace => {
    const products = productImages.filter(item => item.image_type === "product" && Number(item.furnace_id) === Number(furnace.id));
    const productCards = products.length ? products.map(item => `<article class="image-card product-child-card"><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.image_name)}" loading="lazy"><div><h3>${escapeHtml(item.image_name)}</h3><p>${escapeHtml(item.description || "Product")}</p><small>${new Date(item.captured_at).toLocaleString()} | ${escapeHtml(item.created_by)}</small><div class="card-actions"><button type="button" class="edit-image" data-id="${item.id}">Edit</button><button type="button" class="delete-image" data-id="${item.id}">Delete</button></div></div></article>`).join("") : '<p class="furnace-products-empty">No products added</p>';
    return `<section class="furnace-master-group"><div class="furnace-master-row"><img src="${escapeHtml(furnace.image_url)}" alt="${escapeHtml(furnace.image_name)}"><div><span>FURNACE</span><h3>${escapeHtml(furnace.image_name)}</h3><p>${escapeHtml(furnace.description || "Master furnace")}</p></div><strong>${products.length}/6 products</strong><div class="card-actions"><button type="button" class="edit-image" data-id="${furnace.id}">Edit</button><button type="button" class="delete-image" data-id="${furnace.id}">Delete</button></div></div><div class="furnace-products-title">Products</div><div class="furnace-products-grid">${productCards}</div></section>`;
  }).join("") : '<p class="gallery-empty">Add your first furnace to begin.</p>';
  document.querySelectorAll(".edit-image").forEach(button => button.addEventListener("click", () => beginEdit(Number(button.dataset.id))));
  document.querySelectorAll(".delete-image").forEach(button => button.addEventListener("click", () => deleteImage(Number(button.dataset.id))));
}
function finishEdit() {
  editingId = null; $("upload-form").reset(); $("image-file").required = true; $("upload-button").textContent = "Save image"; $("cancel-edit").hidden = true;
  $("preview").hidden = true; $("preview").removeAttribute("src"); $("drop-message").hidden = false; $("form-title").textContent = "New image"; updateTypeFields(); updateClock();
}
function beginEdit(id) {
  const item = productImages.find(image => image.id === id); if (!item) return;
  editingId = id; $("image-name").value = item.image_name; $("description").value = item.description || ""; $("image-type").value = item.image_type; updateTypeFields(); $("furnace-id").value = item.furnace_id || "";
  $("image-file").required = false; $("preview").src = item.image_url; $("preview").hidden = false; $("drop-message").hidden = true;
  $("form-title").textContent = "Edit image"; $("upload-button").textContent = "Update image"; $("cancel-edit").hidden = false; showMessage(""); window.scrollTo({top:0, behavior:"smooth"});
}
async function deleteImage(id) {
  const item = productImages.find(image => image.id === id); if (!item || !confirm(`Delete "${item.image_name}"?`)) return;
  try {
    const response = await fetch(`/api/product-images/${id}`, {method:"DELETE"}); const result = await readResponse(response);
    if (!response.ok) throw new Error(result.error || "Delete failed");
    if (editingId === id) finishEdit(); showMessage("Image deleted successfully"); await loadImages();
  } catch (error) { showMessage(error.message, true); }
}
$("cancel-edit").addEventListener("click", finishEdit);
$("image-type").addEventListener("change", updateTypeFields);
$("upload-form").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget;
  showMessage(""); const button = $("upload-button"); button.disabled = true; button.textContent = "Saving...";
  try {
    const response = await fetch(editingId ? `/api/product-images/${editingId}` : "/api/product-images", {method:editingId ? "PUT" : "POST", body:new FormData(form)}); const result = await readResponse(response);
    if (!response.ok) throw new Error(result.error || "Upload failed");
    const wasEditing = editingId !== null; finishEdit(); showMessage(wasEditing ? "Image updated successfully" : "Image saved successfully"); await loadImages();
  } catch (error) { showMessage(error.message, true); } finally { button.disabled = false; button.textContent = "Save image"; }
});
updateTypeFields(); updateClock(); setInterval(updateClock, 1000);
loadImages().catch(error => showMessage(error.message || "Unable to load Product Master", true));
