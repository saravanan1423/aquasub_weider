const byId=id=>document.getElementById(id);
const historyOnly=document.querySelector('.backup-settings-page').dataset.historyOnly==='true';
let historyPage=1, historyBusy=false;
function closeKeyboard() {
  document.querySelectorAll('.screen-keyboard').forEach(panel=>{panel.hidden=true;});
}
function message(text, error=false) {
  const box=byId('backup-settings-message');
  box.textContent=text;
  box.hidden=!text;
  box.classList.toggle('error', error);
}
async function api(path, options={}) {
  const response=await fetch(path, {cache:'no-store', ...options});
  const data=await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not load backup settings');
  return data;
}
function scheduleChanged() {
  const frequency=byId('backup-frequency').value;
  byId('backup-time-field').hidden=frequency==='shift';
  byId('backup-month-field').hidden=frequency!=='monthly';
  const time=byId('backup-time').value;
  const day=byId('backup-month-day').value;
  byId('backup-schedule-summary').textContent=!byId('backup-enabled').checked ? 'Automatic scheduling is off. Queued backups will still retry when a USB drive is connected.' :
    (frequency==='shift' ? 'At every shift end. Pending backups wait for a USB drive.' : frequency==='daily' ? `Daily at ${time}, covering the previous 24 hours.` : `Monthly on day ${day} at ${time} (last day for shorter months).`);
}
function formatDate(value) {
  return value ? new Date(value).toLocaleString([], {day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
}
function cell(parent, text, tag='td') {
  const node=document.createElement(tag);
  node.textContent=text;
  parent.append(node);
  return node;
}
async function loadHistory() {
  if (historyBusy) return;
  historyBusy=true;
  byId('backup-history-refresh').disabled=true;
  try {
    const data=await api(`/api/backup-history?page=${historyPage}`);
    historyPage=data.page;
    const body=byId('backup-history-body');
    body.replaceChildren();
    for (const job of data.items) {
      const row=document.createElement('tr');
      const period=cell(row,'');
      cell(period,job.schedule_type==='shift' ? `Shift: ${job.shift_name}` : job.schedule_type==='daily' ? 'Daily' : 'Monthly','strong');
      cell(period,`${formatDate(job.starts_at)} to ${formatDate(job.ends_at)}`,'small');
      const status=cell(row,'');
      const failed=job.status==='pending' && Boolean(job.last_error);
      const label=job.status==='completed' ? 'USB completed' : job.status==='processing' ? 'Saving' : failed ? 'Failed - retrying' : job.local_saved_at ? 'Waiting for USB' : 'Local save pending';
      cell(status,label,'span').className=`backup-job-status ${failed ? 'failed' : job.status}`;
      cell(status,`Attempts: ${job.attempts}`,'small');
      const result=cell(row,job.status==='completed' ? job.drive : job.last_error || (job.status==='processing' ? 'Writing report files...' : job.local_saved_at ? 'Saved in system; USB pending' : 'Not saved yet'));
      if (job.local_saved_at) cell(result,`Local: ${formatDate(job.local_saved_at)}`,'small');
      if (job.completed_at) cell(result,formatDate(job.completed_at),'small');
      result.title=[job.local_folder,job.folder].filter(Boolean).join('\n');
      body.append(row);
    }
    if (!data.items.length) cell(body.insertRow(),'No automatic backups yet.').colSpan=3;
    byId('backup-history-count').textContent=`${data.total} automatic backup${data.total===1 ? '' : 's'}`;
    byId('backup-history-page').textContent=`${data.page} / ${data.pages}`;
    byId('backup-history-previous').disabled=data.page<=1;
    byId('backup-history-next').disabled=data.page>=data.pages;
    message('');
  } catch (error) { message(error.message,true); }
  finally { historyBusy=false; byId('backup-history-refresh').disabled=false; }
}
if (!historyOnly) {
  const tabs=[byId('backup-schedule-tab'),byId('backup-history-tab')];
  function selectTab(index) {
    closeKeyboard();
    tabs.forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index ? 0 : -1;});
    byId('backup-schedule-panel').hidden=index!==0;
    byId('backup-history-panel').hidden=index!==1;
    message('');
    if (index===1) loadHistory();
  }
  tabs.forEach((tab,index)=>{
    tab.addEventListener('click',()=>selectTab(index));
    tab.addEventListener('keydown',event=>{
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const target=event.key==='Home' ? 0 : event.key==='End' ? 1 : 1-index;
      selectTab(target); tabs[target].focus();
    });
  });
  for (const id of ['backup-enabled','backup-frequency','backup-time','backup-month-day']) byId(id).addEventListener('change',scheduleChanged);
  api('/api/backup-settings').then(data=>{
    const settings=data.settings;
    byId('backup-enabled').checked=Boolean(settings.enabled);
    byId('backup-frequency').value=settings.frequency;
    byId('backup-time').value=settings.backup_time;
    byId('backup-month-day').value=settings.month_day;
    scheduleChanged(); byId('backup-settings-save').disabled=false;
  }).catch(error=>message(error.message,true));
  byId('backup-settings-form').addEventListener('submit',async event=>{
    event.preventDefault();
    closeKeyboard();
    const button=byId('backup-settings-save');
    button.disabled=true;
    try {
      await api('/api/backup-settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:byId('backup-enabled').checked,frequency:byId('backup-frequency').value,backup_time:byId('backup-time').value,month_day:Number(byId('backup-month-day').value)})});
      message('Backup schedule saved.');
      showBackupScheduleSuccess();
    } catch (error) { message(error.message,true); }
    finally { button.disabled=false; }
  });
} else loadHistory();
byId('backup-history-refresh').addEventListener('click',loadHistory);
byId('backup-history-previous').addEventListener('click',()=>{historyPage--;loadHistory();});
byId('backup-history-next').addEventListener('click',()=>{historyPage++;loadHistory();});
setInterval(()=>{if (!document.hidden && !byId('backup-history-panel').hidden) loadHistory();},15000);

let backupSuccessTimer = null;
function showBackupScheduleSuccess() {
  const screen = byId('backup-success-screen');
  if (!screen) return;
  if (backupSuccessTimer) {
    clearInterval(backupSuccessTimer);
    backupSuccessTimer = null;
  }

  const freq = byId('backup-frequency').value;
  const isEnabled = byId('backup-enabled').checked;
  const summaryEl = byId('backup-success-summary');
  if (summaryEl) {
    summaryEl.textContent = !isEnabled 
      ? 'Automatic backup disabled' 
      : freq === 'shift' 
        ? 'Automatic backup active: after every shift' 
        : freq === 'daily' 
          ? `Automatic backup active: daily at ${byId('backup-time').value}` 
          : `Automatic backup active: monthly on day ${byId('backup-month-day').value} at ${byId('backup-time').value}`;
  }

  const timeEl = byId('backup-success-time');
  if (timeEl) timeEl.textContent = new Date().toLocaleString();

  const countdownEl = byId('backup-success-countdown');
  if (countdownEl) countdownEl.textContent = "2";

  screen.hidden = false;

  const deadline = Date.now() + 2000;
  backupSuccessTimer = setInterval(() => {
    const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    if (countdownEl) countdownEl.textContent = remaining;
    if (Date.now() >= deadline) {
      clearInterval(backupSuccessTimer);
      backupSuccessTimer = null;
      screen.hidden = true;
    }
  }, 100);

  screen.onclick = () => {
    if (backupSuccessTimer) {
      clearInterval(backupSuccessTimer);
      backupSuccessTimer = null;
    }
    screen.hidden = true;
  };
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const screen = byId('backup-success-screen');
    if (screen && !screen.hidden) {
      if (backupSuccessTimer) {
        clearInterval(backupSuccessTimer);
        backupSuccessTimer = null;
      }
      screen.hidden = true;
    }
  }
});

