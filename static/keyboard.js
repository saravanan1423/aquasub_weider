(() => {
  let target = null;
  let shifted = false;
  let specialMode = false;
  const textRows = [
    ["1","2","3","4","5","6","7","8","9","0","@"],
    ["q","w","e","r","t","y","u","i","o","p"],
    ["a","s","d","f","g","h","j","k","l",".","-"],
    ["shift","z","x","c","v","b","n","m","backspace"],
    ["special","space","close"]
  ];
  const specialRows = [
    ["!","@","#","$","%","^","&","*","(",")"],
    ["~","`","|","\\","/","{","}","[","]"],
    ["<",">","+","=",":",";","\"","'"],
    ["_","-",",",".","?","backspace"],
    ["letters","space","close"]
  ];
  const numberRows = [["1","2","3","4","5","6","7","8","9","0"],["-",".","backspace"],["close"]];
  const panel = document.createElement("section");
  panel.className = "screen-keyboard";
  panel.hidden = true;

  function accepted(element) {
    return element && !element.disabled && !element.readOnly && (
      element.tagName === "TEXTAREA" ||
      (element.tagName === "INPUT" && !["file","checkbox","radio","button","submit","hidden","datetime-local"].includes(element.type))
    );
  }
  function preview() {
    const value = target?.value || "";
    const display = target?.type === "password" ? "•".repeat(value.length) : value;
    const previewBox = panel.querySelector(".keyboard-preview-value");
    if (previewBox) previewBox.textContent = display || "Type here…";
  }
  function range() {
    try { return [target.selectionStart ?? target.value.length, target.selectionEnd ?? target.value.length]; }
    catch { return [target.value.length, target.value.length]; }
  }
  function setCaret(position) {
    try { target.setSelectionRange(position, position); } catch { /* Number inputs do not expose a selection range. */ }
  }
  function emitInput() {
    target.dispatchEvent(new Event("input", {bubbles:true}));
    target.dispatchEvent(new Event("change", {bubbles:true}));
    preview();
  }
  function insert(text) {
    if (!accepted(target)) return;
    if (target.type === "number" && !/[0-9.-]/.test(text)) return;
    const [start,end] = range();
    target.value = target.value.slice(0,start) + text + target.value.slice(end);
    target.focus({preventScroll:true}); setCaret(start + text.length); emitInput();
  }
  function backspace() {
    if (!accepted(target)) return;
    const [start,end] = range();
    if (start===end && start>0) { target.value=target.value.slice(0,start-1)+target.value.slice(end); setCaret(start-1); }
    else { target.value=target.value.slice(0,start)+target.value.slice(end); setCaret(start); }
    target.focus({preventScroll:true}); emitInput();
  }
  function draw() {
    const rows = target?.type === "number" ? numberRows : specialMode ? specialRows : textRows;
    panel.replaceChildren();
    const previewBar=document.createElement("div"); previewBar.className="keyboard-preview";
    const previewLabel=document.createElement("span"); previewLabel.textContent="INPUT";
    const previewValue=document.createElement("strong"); previewValue.className="keyboard-preview-value";
    previewBar.append(previewLabel,previewValue); panel.append(previewBar);
    rows.forEach(row => {
      const rowElement=document.createElement("div"); rowElement.className="keyboard-row";
      row.forEach(key => {
        const keyButton=document.createElement("button"); keyButton.type="button"; keyButton.dataset.key=key; keyButton.className=`key-${key === "\\" ? "backslash" : key}`;
        keyButton.textContent = key === "backspace" ? "⌫" : key === "shift" ? "⇧ Shift" : key === "special" ? "#+=" : key === "letters" ? "ABC" : key === "space" ? "Space" : key === "close" ? "Close" : shifted ? key.toUpperCase() : key;
        rowElement.append(keyButton);
      });
      panel.append(rowElement);
    });
    preview();
    panel.querySelectorAll("button").forEach(keyButton => keyButton.addEventListener("mousedown", event => {
      event.preventDefault(); const key=keyButton.dataset.key;
      if (key==="close") panel.hidden=true;
      else if (key==="shift") { shifted=!shifted; draw(); }
      else if (key==="special") { specialMode=true; draw(); }
      else if (key==="letters") { specialMode=false; draw(); }
      else if (key==="backspace") backspace();
      else insert(key==="space" ? " " : shifted ? key.toUpperCase() : key);
    }));
  }
  document.addEventListener("focusin", event => {
    if (!accepted(event.target)) return;
    target=event.target; shifted=false; specialMode=false;
    const modal=target.closest("dialog"); (modal || document.body).appendChild(panel);
    panel.hidden=false; draw();
  });
  document.addEventListener("input", event => { if (event.target===target) preview(); });
  document.body.append(panel);
})();
