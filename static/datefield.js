"use strict";
// Every date box in the app shows DD/MM/YYYY (owner, 10 Nov 2026). A plain <input type="date"> shows the browser's own order
// (MM/DD/YYYY on an English Chrome) and a page cannot change that, so each one gets a text box in front of it:
//   * the original date input stays in the page, hidden behind a calendar button -- it still holds the value as YYYY-MM-DD, so
//     every page keeps reading and setting `.value` exactly as before, and its input / change events still fire;
//   * the text box shows and takes DD/MM/YYYY. Digits alone work (01062025, slashes are added), so do 1/6/2025, 1.6.68 and a
//     พ.ศ. year (01/06/2568 -> 2025); a 2-digit year of 50 or more is read as พ.ศ. (68 = 2568 = 2025);
//   * the calendar button opens the browser's own date picker.
// New date inputs (dialogs, forms built later) are picked up as they appear.
(function () {
  const VALUE = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  const pad = (n) => String(n).padStart(2, "0");
  const toText = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || ""); return m ? `${m[3]}/${m[2]}/${m[1]}` : ""; };

  function parseDMY(t) {
    t = (t || "").trim();
    if (/^\d{8}$/.test(t)) t = `${t.slice(0, 2)}/${t.slice(2, 4)}/${t.slice(4)}`;
    const m = /^(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2}|\d{4})$/.exec(t);
    if (!m) return null;
    const d = +m[1], mo = +m[2];
    let y = +m[3];
    if (m[3].length === 2) y = y >= 50 ? 2500 + y : 2000 + y;          // 68 -> 2568 (พ.ศ.), 25 -> 2025
    if (y >= 2400) y -= 543;                                            // พ.ศ. -> ค.ศ.
    const dt = new Date(Date.UTC(y, mo - 1, d));
    if (y < 1900 || dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
    return `${y}-${pad(mo)}-${pad(d)}`;
  }
  window.parseDMY = parseDMY;

  const CAL = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/></svg>';

  function enhance(inp) {
    if (inp.dataset.dmy || inp.type !== "date" || inp.closest(".dmy-off")) return;
    inp.dataset.dmy = "1";
    const wrap = document.createElement("span");
    wrap.className = "dtf";
    const text = document.createElement("input");
    text.type = "text";
    text.className = "dtf-text";
    text.inputMode = "numeric";
    text.autocomplete = "off";
    text.placeholder = "วว/ดด/ปปปป";
    text.setAttribute("aria-label", inp.getAttribute("aria-label") || (inp.labels && inp.labels[0] ? inp.labels[0].textContent.trim() : "วันที่") + " (วัน/เดือน/ปี)");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dtf-cal";
    btn.tabIndex = -1;
    btn.setAttribute("aria-label", "เลือกจากปฏิทิน");
    btn.innerHTML = CAL;
    inp.parentNode.insertBefore(wrap, inp);
    wrap.append(text, btn, inp);
    inp.classList.add("dtf-real");
    inp.tabIndex = -1;
    inp.setAttribute("aria-hidden", "true");

    let committed = VALUE.get.call(inp), busy = false;           // busy: our own events are going out
    const show = () => { text.value = toText(VALUE.get.call(inp)); text.setCustomValidity(""); wrap.classList.remove("bad"); };
    // a page setting .value (a draft loaded, a form reset) updates the text box too
    Object.defineProperty(inp, "value", {
      configurable: true,
      get() { return VALUE.get.call(this); },
      set(v) {
        VALUE.set.call(this, v);
        const now = VALUE.get.call(this);
        if (!(now === committed && (busy || document.activeElement === text))) show();   // never wipe what is being typed
        committed = now;
      },
    });
    const commit = (iso) => {
      if (iso === VALUE.get.call(inp)) return;
      VALUE.set.call(inp, iso);
      committed = iso;
      busy = true;
      try {
        inp.dispatchEvent(new Event("input", { bubbles: true }));
        inp.dispatchEvent(new Event("change", { bubbles: true }));
      } finally { busy = false; }
    };
    text.addEventListener("input", (e) => {
      if (e.inputType === "insertText" && /^\d$/.test(e.data || "") && /^(\d{2}|\d{1,2}\/\d{2})$/.test(text.value)) text.value += "/";
      const t = text.value.trim();
      const iso = t ? parseDMY(t) : "";
      wrap.classList.remove("bad");
      text.setCustomValidity("");
      if (iso !== null) commit(iso);
      else if (VALUE.get.call(inp)) commit("");                          // half-typed: no date until it is a real one
    });
    text.addEventListener("blur", () => {
      const t = text.value.trim();
      if (t && parseDMY(t) === null) { wrap.classList.add("bad"); text.setCustomValidity("ใส่วันที่แบบ วว/ดด/ปปปป เช่น 01/06/2025"); }
      else if (t) show();                                                // tidy: 1/6/68 -> 01/06/2025
    });
    inp.addEventListener("change", () => { if (!busy) show(); });          // picked from the calendar
    inp.addEventListener("focus", () => text.focus());                    // a <label for=...> click lands in the text box
    btn.addEventListener("click", () => {
      if (inp.disabled || inp.readOnly) return;
      try { inp.showPicker(); } catch (e) { inp.focus(); }
    });
    const mirror = () => {
      text.disabled = btn.disabled = inp.disabled;
      text.readOnly = inp.readOnly;
      text.required = inp.required || !!inp.dataset.dmyRequired;
      wrap.hidden = inp.hidden;
      if (inp.title) text.title = inp.title;
    };
    if (inp.required) { inp.required = false; inp.dataset.dmyRequired = "1"; }
    mirror();
    new MutationObserver(mirror).observe(inp, { attributes: true, attributeFilter: ["disabled", "readonly", "hidden", "title"] });
    show();
  }

  const scan = (root) => {
    if (root.nodeType !== 1) return;
    if (root.matches && root.matches('input[type="date"]')) enhance(root);
    root.querySelectorAll && root.querySelectorAll('input[type="date"]').forEach(enhance);
  };
  const start = () => {
    scan(document.body);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach(scan))).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
