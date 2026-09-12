const logsBody = document.getElementById("logs-body");
let currentPage = 1;
let currentLogs = [];
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character])); }
async function loadCaptureLogs(page = 1) {
  try {
    const response = await fetch(`/api/weight-captures?page=${page}&per_page=5`); const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Unable to load capture logs");
    const logs = result.items; currentLogs = logs; currentPage = result.page;
    document.getElementById("log-count").textContent = result.total;
    document.getElementById("current-page").textContent = result.page;
    document.getElementById("total-pages").textContent = result.pages;
    document.getElementById("previous-page").disabled = result.page <= 1;
    document.getElementById("next-page").disabled = result.page >= result.pages;
    logsBody.innerHTML = logs.length ? logs.map((item, index) => `<tr><td>${(result.page - 1) * result.per_page + index + 1}</td><td><img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.image_name)}"></td><td>${escapeHtml(item.image_name)}</td><td><strong>${escapeHtml(item.weight)}</strong> <small class="log-weight-unit">kg</small></td><td>${escapeHtml(item.captured_by_username || "Unknown")}</td><td>${new Date(item.captured_at).toLocaleString()}</td>${window.LOG_ADMIN ? `<td class="log-actions"><button type="button" class="edit-log" data-id="${item.id}">Edit</button><button type="button" class="delete-log" data-id="${item.id}">Delete</button></td>` : ""}</tr>`).join("") : `<tr><td colspan="${window.LOG_ADMIN ? 7 : 6}" class="logs-empty">No weights have been captured yet.</td></tr>`;
    if (window.LOG_ADMIN) {
      document.querySelectorAll('.edit-log').forEach(button => button.addEventListener('click', () => openLogEditor(Number(button.dataset.id))));
      document.querySelectorAll('.delete-log').forEach(button => button.addEventListener('click', () => deleteLog(Number(button.dataset.id))));
    }
  } catch (error) { const box = document.getElementById("logs-error"); box.textContent = error.message; box.hidden = false; }
}
function localDateTimeValue(value) {
  const date = new Date(value); const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 23);
}
function openLogEditor(id) {
  const item = currentLogs.find(log => log.id === id); if (!item) return;
  document.getElementById('edit-log-id').value = id; document.getElementById('edit-log-name').value = item.image_name;
  document.getElementById('edit-log-weight').value = item.weight; document.getElementById('edit-log-time').value = localDateTimeValue(item.captured_at);
  document.getElementById('edit-log-error').hidden = true; document.getElementById('edit-log-dialog').showModal(); document.getElementById('edit-log-name').focus();
}
async function deleteLog(id) {
  if (!confirm('Delete this capture log permanently?')) return;
  try { await requestLogChange(`/api/weight-captures/${id}`, {method:'DELETE'}); await loadCaptureLogs(currentPage); }
  catch (error) { alert(error.message); }
}
async function requestLogChange(url, options) { const response=await fetch(url,options); const data=await response.json(); if(!response.ok) throw new Error(data.error||'Request failed'); return data; }
if (window.LOG_ADMIN) {
  const dialog=document.getElementById('edit-log-dialog');
  document.getElementById('close-log-dialog').addEventListener('click',()=>dialog.close()); document.getElementById('cancel-log-edit').addEventListener('click',()=>dialog.close());
  document.getElementById('edit-log-form').addEventListener('submit',async event=>{ event.preventDefault(); const errorBox=document.getElementById('edit-log-error'); errorBox.hidden=true;
    try { await requestLogChange(`/api/weight-captures/${document.getElementById('edit-log-id').value}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_name:document.getElementById('edit-log-name').value,weight:document.getElementById('edit-log-weight').value,captured_at:new Date(document.getElementById('edit-log-time').value).toISOString()})}); dialog.close(); await loadCaptureLogs(currentPage); }
    catch(error){ errorBox.textContent=error.message; errorBox.className='form-message form-error'; errorBox.hidden=false; }
  });
}
document.getElementById("previous-page").addEventListener("click", () => loadCaptureLogs(currentPage - 1));
document.getElementById("next-page").addEventListener("click", () => loadCaptureLogs(currentPage + 1));
loadCaptureLogs();
