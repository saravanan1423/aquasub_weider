(() => {
  if (window.ScaleStatus) return;
  const rawPage=document.currentScript.dataset.rawScalePage==='true';
  const listeners=new Set();
  let latest=null, expiryTimer=null, pollTimer=null, polling=false, stopped=false;
  let interval=200, validUntil=0;
  function emit(data) {
    latest=data;
    listeners.forEach(listener=>listener(data));
  }
  function publish(data) {
    clearTimeout(expiryTimer);
    interval=Math.max(100,Math.min(2000,Number(data.refresh_interval_ms || data.config?.refresh_interval_ms)||200));
    const remaining=Math.max(0,(Number(data.reading_timeout_seconds)||2)-(Number(data.reading_age_seconds)||0))*1000;
    validUntil=data.scale_connected ? performance.now()+remaining : 0;
    emit(data);
    if (data.scale_connected) expiryTimer=setTimeout(()=>{
      validUntil=0;
      emit({...latest,scale_connected:false,weight_stable:false,weight:'',error:'No valid scale readings received within the connection timeout.'});
    },remaining);
  }
  function reportError(error) {
    const connected=Boolean(latest?.scale_connected && performance.now()<validUntil);
    emit({...latest,scale_connected:connected,weight_stable:false,weight:connected ? latest.weight : '',error:error.message});
  }
  async function poll() {
    if (polling || stopped || rawPage) return;
    polling=true;
    clearTimeout(pollTimer);
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),5000);
    try {
      const response=await fetch('/api/scale-status',{cache:'no-store',signal:controller.signal});
      if (response.status===401) { stopped=true; throw new Error('Your session expired. Sign in again.'); }
      if (!response.ok) throw new Error(`Scale status unavailable (HTTP ${response.status})`);
      publish(await response.json());
    } catch (error) { reportError(error); }
    finally {
      clearTimeout(timeout);
      polling=false;
      if (!stopped) pollTimer=setTimeout(poll,interval);
    }
  }
  window.ScaleStatus={publish,reportError,subscribe(listener){listeners.add(listener);if(latest)listener(latest);return()=>listeners.delete(listener);}};
  function start() {
    poll();
  }
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(pollTimer);clearTimeout(expiryTimer);});
  window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;poll();}});
})();
