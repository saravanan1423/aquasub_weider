const logsBody = document.getElementById("logs-body");
let currentPage = 1;
let currentLogs = [];
let reportData = null;
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
    logsBody.innerHTML = logs.length ? logs.map((item, index) => {
      const key = `melt-${index}`;
      const breakdown = item.captures.map(capture => `<tr><td>${escapeHtml(capture.image_name)}</td><td><strong>${escapeHtml(capture.weight)}</strong> <small class="log-weight-unit">kg</small></td><td>${new Date(capture.captured_at).toLocaleString()}</td><td>${escapeHtml(capture.captured_by_username || "Unknown")}</td>${window.LOG_ADMIN ? `<td class="log-actions"><button type="button" class="edit-log" data-id="${capture.id}">Edit</button><button type="button" class="delete-log" data-id="${capture.id}">Delete</button></td>` : ""}</tr>`).join("");
      return `<tr class="melt-row" data-target="${key}" tabindex="0" role="button" aria-expanded="false" aria-label="Show ${escapeHtml(item.melt_number || "melt")} details"><td>${(result.page - 1) * result.per_page + index + 1}</td><td>${escapeHtml(item.furnace_name || "-")}</td><td><strong>${escapeHtml(item.melt_number || "-")}</strong></td><td>${escapeHtml(item.shift_type || "Unassigned")}</td><td><strong>${formatLogWeight(item.total_weight)}</strong> <small class="log-weight-unit">kg</small></td><td>${item.capture_count}</td><td>${escapeHtml(item.captured_by_username || "Unknown")}</td><td>${new Date(item.captured_at).toLocaleString()}</td></tr><tr class="melt-breakdown" id="${key}" hidden><td colspan="8"><div><table><thead><tr><th>Product</th><th>Weight</th><th>Captured date and time</th><th>Captured by</th>${window.LOG_ADMIN ? "<th>Actions</th>" : ""}</tr></thead><tbody>${breakdown}</tbody></table></div></td></tr>`;
    }).join("") : `<tr><td colspan="8" class="logs-empty">No weights have been captured yet.</td></tr>`;
    document.querySelectorAll(".melt-row").forEach(row => {
      const toggle = () => { const breakdown = document.getElementById(row.dataset.target); const opening = breakdown.hidden; breakdown.hidden = !opening; row.setAttribute("aria-expanded", String(opening)); };
      row.addEventListener("click", toggle);
      row.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
    });
    if (window.LOG_ADMIN) {
      document.querySelectorAll('.edit-log').forEach(button => button.addEventListener('click', () => openLogEditor(Number(button.dataset.id))));
      document.querySelectorAll('.delete-log').forEach(button => button.addEventListener('click', () => deleteLog(Number(button.dataset.id))));
    }
  } catch (error) { const box = document.getElementById("logs-error"); box.textContent = error.message; box.hidden = false; }
}
function formatLogWeight(value) { return Number(value || 0).toFixed(3).replace(/\.?0+$/, ""); }
function localDateTimeValue(value) {
  const date = new Date(value); const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 23);
}
function openLogEditor(id) {
  const item = currentLogs.flatMap(log => log.captures || []).find(log => log.id === id); if (!item) return;
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

const reportDialog=document.getElementById("report-dialog");
const reportQrDialog=document.getElementById("report-qr-dialog");
const reportOutput=document.getElementById("report-output");
const reportType=document.getElementById("report-type");
const backupDrive=document.getElementById("backup-drive");
const generateReport=document.getElementById("generate-report");
let reportBusy=false;
let drivesLoading=false;
function usbSelected() { return reportOutput?.value === "usb"; }
function updateGenerateButton() {
  generateReport.disabled=reportBusy || (usbSelected() && (drivesLoading || !backupDrive.value));
}
function backupStatus(message, error=false) {
  const box=document.getElementById("backup-message");
  box.hidden=!message;
  box.className=`form-message ${error ? "form-error" : "form-success"}`;
  box.textContent=message;
}
async function refreshUsbDrives() {
  const previous=backupDrive.value;
  drivesLoading=true;
  backupDrive.replaceChildren(new Option("Checking connected drives...", ""));
  updateGenerateButton();
  try {
    const response=await fetch("/api/usb-drives");
    const data=await response.json();
    if (!response.ok) throw new Error(data.error || "Could not check USB drives");
    backupDrive.replaceChildren(new Option(data.drives.length ? "Select USB drive" : "No USB drive", ""));
    for (const drive of data.drives) {
      backupDrive.add(new Option(`${drive.label} (${Math.round(drive.free_bytes / 1024 / 1024)} MB free)`, drive.id));
    }
    if (data.drives.some(drive => drive.id === previous)) backupDrive.value=previous;
    backupStatus(data.drives.length ? "" : "Connect and mount a writable USB drive, then refresh.", !data.drives.length);
  } catch (error) {
    backupDrive.replaceChildren(new Option("Unable to list USB drives", ""));
    backupStatus(error.message, true);
  } finally {
    drivesLoading=false;
    updateGenerateButton();
  }
}
if (reportOutput) {
  reportOutput.addEventListener("change", () => {
    document.getElementById("report-usb-controls").hidden=!usbSelected();
    backupDrive.required=usbSelected();
    backupStatus("");
    document.getElementById("report-error").hidden=true;
    document.getElementById("report-preview").hidden=true;
    reportData=null;
    updateGenerateButton();
    if (usbSelected()) refreshUsbDrives();
  });
  document.getElementById("refresh-usb").addEventListener("click", refreshUsbDrives);
  backupDrive.addEventListener("change", () => { backupStatus(""); updateGenerateButton(); });
}
function reportDate(value) { return new Date(value).toLocaleString(); }
function reportTitle(data) { return data.report_type === "individual" ? "Individual Captures Report" : "Melt Report"; }
function reportTableHead(data) {
  const labels=data.report_type === "individual"
    ? ["S.No", "Furnace", "Melt number", "Product", "Weight", "Shift type", "Captured by", "Captured date and time"]
    : ["S.No", "Furnace", "Melt number", "Shift type", "Total weight", "Products", "Captured by", "Last captured"];
  return `<tr>${labels.map(label=>`<th>${label}</th>`).join("")}</tr>`;
}
function reportDetailRows(data) {
  return data.items.map((item,index)=>{
    const cells=data.report_type === "individual"
      ? [index+1,item.furnace_name||"-",item.melt_number||"-",item.image_name,`${formatLogWeight(item.numeric_weight)} kg`,item.shift_type||"Unassigned",item.captured_by_username||"Unknown",reportDate(item.captured_at)]
      : [index+1,item.furnace_name||"-",item.melt_number||"-",item.shift_type||"Unassigned",`${formatLogWeight(item.total_weight)} kg`,item.product_count,item.captured_by_username||"Unknown",reportDate(item.captured_at)];
    return `<tr>${cells.map(cell=>`<td>${escapeHtml(cell)}</td>`).join("")}</tr>`;
  }).join("") || `<tr><td colspan="8">No ${data.report_type === "individual" ? "captures" : "melts"} match these filters.</td></tr>`;
}
function reportSummary(data) {
  return `<table><thead><tr><th>Melts</th><th>${data.report_type === "individual" ? "Captures" : "Products"}</th><th>Total weight</th></tr></thead><tbody><tr><td>${data.total_melts}</td><td>${data.total_products}</td><td>${formatLogWeight(data.total_net_weight)} kg</td></tr></tbody></table>`;
}
reportType.addEventListener("change",()=>{
  reportData=null;
  document.getElementById("report-preview").hidden=true;
  document.getElementById("report-error").hidden=true;
  backupStatus("");
});
async function loadReportOptions() {
  const response=await fetch("/api/capture-report-options"); const data=await response.json();
  if(!response.ok) throw new Error(data.error||"Unable to load report filters");
  document.getElementById("report-shift").innerHTML='<option value="all">All shifts combined</option>'+data.shifts.map(item=>`<option value="${item.id}">${escapeHtml(item.name)} (${item.start_time} to ${item.end_time})</option>`).join('');
  document.getElementById("report-furnace").innerHTML='<option value="all">All furnaces</option>'+data.furnaces.map(item=>`<option value="${escapeHtml(item.furnace_id)}">${escapeHtml(item.furnace_name)}</option>`).join('');
}
function renderReport(data) {
  reportData=data; document.getElementById("report-preview").hidden=false;
  const qrSource=`${data.qr_url}?v=${Date.now()}`;
  document.getElementById("report-qr-image").src=qrSource;
  document.getElementById("report-qr-full-image").src=qrSource;
  document.getElementById("report-download-link").href=data.download_url;
  document.getElementById("report-qr-download-link").href=data.download_url;
  document.getElementById("report-meta").innerHTML=`<div><span>Generated by</span><strong>${escapeHtml(data.generated_by)}</strong></div><div><span>Generated on</span><strong>${reportDate(data.generated_on)}</strong></div><div><span>From</span><strong>${escapeHtml(data.range_from)}</strong></div><div><span>To</span><strong>${escapeHtml(data.range_to)}</strong></div><div><span>Shift</span><strong>${escapeHtml(data.shift)}</strong></div><div><span>Furnace</span><strong>${escapeHtml(data.furnace)}</strong></div>`;
  document.getElementById("report-head").innerHTML=reportTableHead(data);
  document.getElementById("report-body").innerHTML=reportDetailRows(data);
  document.getElementById("report-summary").innerHTML=`<h3>Overall summary</h3>${reportSummary(data)}`;
}
async function showReportQr() {
  if (!reportData) return;
  const errorBox=document.getElementById("report-error");
  try {
    await document.getElementById("report-qr-full-image").decode();
    errorBox.hidden=true;
    if (!reportQrDialog.open) reportQrDialog.showModal();
  } catch {
    errorBox.textContent="The QR code could not load. Use the report download link instead.";
    errorBox.hidden=false;
  }
}
document.getElementById("open-report").addEventListener("click",async()=>{ const now=new Date(); const today=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`; document.getElementById("report-from-date").value ||= today; document.getElementById("report-to-date").value ||= today; document.getElementById("report-error").hidden=true; backupStatus(""); try{await loadReportOptions();reportDialog.showModal();if(usbSelected())refreshUsbDrives();}catch(error){alert(error.message);} });
document.getElementById("close-report").addEventListener("click",()=>reportDialog.close());
document.getElementById("close-report-qr").addEventListener("click",()=>reportQrDialog.close());
document.getElementById("expand-report-qr").addEventListener("click",showReportQr);
document.getElementById("report-range-type").addEventListener("change",event=>{document.querySelectorAll(".report-time-field").forEach(field=>field.hidden=event.target.value!=="custom");});
document.getElementById("report-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (reportBusy || (usbSelected() && (drivesLoading || !backupDrive.value))) return;
  const usb=usbSelected();
  const errorBox=document.getElementById("report-error");
  errorBox.hidden=true;
  backupStatus("");
  const filters={report_type:reportType.value, shift:document.getElementById("report-shift").value,
    custom_time:document.getElementById("report-range-type").value === "custom" ? "1" : "0",
    furnace:document.getElementById("report-furnace").value,
    from_date:document.getElementById("report-from-date").value,
    to_date:document.getElementById("report-to-date").value,
    start_time:document.getElementById("report-start-time").value,
    end_time:document.getElementById("report-end-time").value};
  reportBusy=true;
  updateGenerateButton();
  generateReport.textContent=usb ? "Backing up..." : "Generating...";
  if (reportOutput) reportOutput.disabled=true;
  reportType.disabled=true;
  try {
    const response=usb ? await fetch("/api/report-usb-backup", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({...filters, drive_id:backupDrive.value})
    }) : await fetch(`/api/capture-report?${new URLSearchParams(filters)}`);
    let data;
    try { data=await response.json(); }
    catch { throw new Error(`Report could not finish (server response ${response.status}). Contact admin.`); }
    if (!response.ok) throw new Error(data.error || "Report generation failed");
    if (usb) {
      const summary=data.total_melts === 0 ? "No data: 2 empty CSVs and 2 PDFs saved"
        : `${data.total_melts} melts, ${data.total_products} captures saved as 2 CSVs and 2 PDFs`;
      backupStatus(`${summary} to ${data.drive} / Weider Reports`);
    } else {
      renderReport(data);
      await showReportQr();
    }
  } catch (error) {
    if (usb) { await refreshUsbDrives(); backupStatus(error.message, true); }
    else { errorBox.textContent=error.message; errorBox.hidden=false; }
  } finally {
    reportBusy=false;
    if (reportOutput) reportOutput.disabled=false;
    reportType.disabled=false;
    generateReport.textContent="Generate";
    updateGenerateButton();
  }
});
document.getElementById("download-report").addEventListener("click",()=>{if(reportData?.download_url)window.location.href=reportData.download_url;});
document.getElementById("print-report").addEventListener("click",()=>{
  if(!reportData)return;
  const frame=document.getElementById("report-print-frame");
  const doc=frame.contentDocument;
  const title=reportTitle(reportData);
  doc.open();
  doc.write(`<!doctype html><title>${title}</title><style>body{font:12px Arial;padding:24px;color:#111}.report-brand{display:flex;align-items:center;gap:24px;margin-bottom:20px;padding-bottom:14px;border-bottom:1px solid #ccc}.report-brand img{width:132px;height:44px;object-fit:contain}h1{margin:0}.meta{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:20px}.meta div{border:1px solid #ccc;padding:8px}.meta span{display:block;color:#666;font-size:10px}table{width:100%;border-collapse:collapse;margin:10px 0 22px}th,td{border:1px solid #bbb;padding:7px;text-align:left}th{background:#eee}</style><header class="report-brand"><img src="/static/aqua-group-logo.png" alt="Aqua Group"><h1>${title}</h1></header><div class="meta"><div><span>Generated by</span>${escapeHtml(reportData.generated_by)}</div><div><span>Generated on</span>${reportDate(reportData.generated_on)}</div><div><span>Shift</span>${escapeHtml(reportData.shift)}</div><div><span>From</span>${escapeHtml(reportData.range_from)}</div><div><span>To</span>${escapeHtml(reportData.range_to)}</div><div><span>Furnace</span>${escapeHtml(reportData.furnace)}</div></div><table><thead>${reportTableHead(reportData)}</thead><tbody>${reportDetailRows(reportData)}</tbody></table><h2>Overall summary</h2>${reportSummary(reportData)}`);
  doc.close();
  setTimeout(()=>frame.contentWindow.print(),100);
});
loadCaptureLogs();
async function loadAutomaticBackupStatus() {
  const box=document.getElementById("automatic-backup-status");
  try {
    const response=await fetch("/api/automatic-backups", {cache:"no-store"});
    const data=await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load backup status");
    const remaining=data.pending + data.processing;
    let message=`Automatic backup: ${data.settings.frequency}`;
    if (data.processing) message=`Backup: saving (${remaining} remaining)`;
    else if (data.pending) message=`Backup: ${data.pending} pending - ${data.last_error || (data.local_pending ? `${data.local_pending} saved locally; waiting for USB` : "saving local reports")}`;
    else if (!data.settings.enabled) message="Automatic backup: off";
    else if (!data.monitoring) message="Automatic backup: worker not running";
    else if (data.last_completed) message=`Backup: ${data.last_completed.shift_name} saved to ${data.last_completed.drive}`;
    box.textContent=message;
    box.title=message;
    box.hidden=false;
  } catch {
    box.hidden=true;
  }
}
loadAutomaticBackupStatus();
setInterval(()=>{ if (!document.hidden) loadAutomaticBackupStatus(); }, 15000);
