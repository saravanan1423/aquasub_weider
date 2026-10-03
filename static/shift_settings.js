const $ = id => document.getElementById(id);
let shifts = [];
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function jsonFetch(url, options = {}) { const response=await fetch(url,options); const data=await response.json(); if(!response.ok) throw new Error(data.error || `Request failed (${response.status})`); return data; }
function showMessage(message="",error=false) { const element=$("shift-message"); element.hidden=!message; element.className=`shift-message ${error?"form-error":"form-success"}`; element.textContent=message; }
function resetForm() { $("shift-form").reset(); $("shift-id").value=""; $("save-shift").textContent="Add shift"; $("cancel-shift").hidden=true; }
function editShift(id) { const shift=shifts.find(item=>item.id===id); if(!shift)return; $("shift-id").value=shift.id; $("shift-name").value=shift.name; $("shift-start").value=shift.start_time; $("shift-end").value=shift.end_time; $("save-shift").textContent="Update shift"; $("cancel-shift").hidden=false; showMessage(); $("shift-name").focus(); }
async function deleteShift(id) { const shift=shifts.find(item=>item.id===id); if(!shift||!confirm(`Delete ${shift.name}?`))return; try{await jsonFetch(`/api/shifts/${id}`,{method:"DELETE"});resetForm();showMessage("Shift deleted");await loadShifts();}catch(error){showMessage(error.message,true);} }
async function loadShifts() {
  shifts=await jsonFetch("/api/shifts"); $("shift-count").textContent=shifts.length;
  $("shift-list").innerHTML=shifts.length?shifts.map(shift=>`<article class="shift-row"><div><strong>${escapeHtml(shift.name)}</strong></div><time>${escapeHtml(shift.start_time)} <span>to</span> ${escapeHtml(shift.end_time)}</time><div class="shift-actions"><button class="ghost edit-shift" type="button" data-id="${shift.id}">Edit</button><button class="ghost danger delete-shift" type="button" data-id="${shift.id}">Delete</button></div></article>`).join(""):'<p class="gallery-empty">No shifts configured.</p>';
  document.querySelectorAll(".edit-shift").forEach(button=>button.addEventListener("click",()=>editShift(Number(button.dataset.id)))); document.querySelectorAll(".delete-shift").forEach(button=>button.addEventListener("click",()=>deleteShift(Number(button.dataset.id))));
}
$("cancel-shift").addEventListener("click",resetForm);
$("shift-form").addEventListener("submit",async event=>{event.preventDefault();showMessage();const id=$("shift-id").value;const button=$("save-shift");button.disabled=true;const payload={name:$("shift-name").value,start_time:$("shift-start").value,end_time:$("shift-end").value};try{await jsonFetch(id?`/api/shifts/${id}`:"/api/shifts",{method:id?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});resetForm();showMessage(id?"Shift updated":"Shift added");await loadShifts();}catch(error){showMessage(error.message,true);}finally{button.disabled=false;}});
loadShifts().catch(error=>showMessage(error.message,true));
