"use strict";

// --------------------------------------------------------------- state
const state = {
  token: "",
  categories: [],
  vehicles: [],          // roster rows, index = option value in every vehicle <select>
  billFiles: [],         // [{blob, name, type, url}]  -- the bill being checked (its files), sent with it when saved
  refFiles: [],          // [{blob, name, type, url}]  -- stored as-is, never extracted
  pickBills: [],         // the bill files picked on the page, not read yet (they become bills in the queue: billqueue.js)
  pickRefs: [],          // the other photos picked on the page (they go with the bill, or with the mechanic's own entry)
  draft: null,
  totalManual: false,     // true once the user types their own header total -- stop auto-following the line sum
};

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const fmtMoney = (n) => Number(n || 0).toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (v) => { const n = parseFloat(String(v).replace(/,/g, "")); return Number.isFinite(n) ? n : 0; };

// --------------------------------------------------------------- api
// The พนักงาน section's own PIN (when the server has one): kept for this browser tab only, sent with every request.
const hrPinValue = () => { try { const v = sessionStorage.getItem("vt_hr_pin") || ""; return isPlainAscii(v) ? v : ""; } catch (e) { return ""; } };
// A request header can only carry plain keyboard characters: a code typed with the Thai keyboard on (ๆไำพะ...) would make every request fail with "String contains non ISO-8859-1 code point".
const isPlainAscii = (s) => /^[ -~]*$/.test(String(s || ""));
const NOT_ENGLISH_KEYS = "รหัสมีตัวอักษรภาษาไทย — สลับแป้นพิมพ์เป็นภาษาอังกฤษ (กด Alt + Shift ซ้าย หรือ ~) แล้วใส่ใหม่";
const saveHrPin = (pin) => { try { sessionStorage.setItem("vt_hr_pin", isPlainAscii(pin) ? pin : ""); } catch (e) { /* storage blocked: asked again on the next page */ } };
async function api(path, opts = {}) {
  if (!isPlainAscii(state.token)) { const e = new Error(NOT_ENGLISH_KEYS); e.status = 403; throw e; }
  const res = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), "X-App-Token": state.token, ...(hrPinValue() ? { "X-HR-Pin": hrPinValue() } : {}) } });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch (_) { /* not json */ }
    if (res.status === 403) detail = "รหัสเข้าใช้งานไม่ถูกต้อง หรือยังไม่ได้ใส่ — ใส่รหัสในช่อง “รหัสเข้าใช้งาน” ด้านบนแล้วกดตกลง (หรือเปิดลิงก์ที่มี ?token=รหัส ต่อท้าย)";
    else if (res.status === 503 && /APP_TOKEN/.test(detail)) detail = "เซิร์ฟเวอร์ยังไม่ได้ตั้งค่า APP_TOKEN (ตั้งใน Railway › Variables แล้วรอให้ deploy เสร็จ)";
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// --------------------------------------------------------------- views
// Light is the default; the toggle in the top bar switches to dark and remembers the choice on this device.
function initTheme() {
  const btn = $("themeBtn");
  const paint = () => { btn.textContent = document.documentElement.dataset.theme === "dark" ? "☀" : "☾"; };
  btn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    if (dark) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme;
    try { localStorage.setItem("vt_theme", dark ? "dark" : "light"); } catch (e) { /* storage blocked: applies for this visit only */ }
    paint();
  });
  paint();
}

let lastBillView = "capture";   // where "บันทึกบิล" in the menu returns to, so a half-reviewed bill isn't abandoned by a peek at the board

const VIEW_NAMES = ["home", "employees", "employee", "loans", "loan", "attendance", "documents", "document", "board", "dash", "capture", "busy", "review", "done", "roster", "truck", "event", "log", "where", "plan", "machinery", "machine", "machineLog", "mgr", "mgrRecurring", "mgrSettings", "settings", "purchases", "purchaseNew", "prices", "priceItem", "stock", "sheet"];
// which sidebar entry lights up for each view
const NAV_OF = {
  home: "navHome", employees: "navEmployees", employee: "navEmployees", loans: "navLoans", loan: "navLoans", attendance: "navAttendance", documents: "navDocs", document: "navDocs", board: "navBoard", dash: "navDash", capture: "navBills", busy: "navBills", review: "navBills", done: "navBills",
  roster: "navRoster", truck: "navRoster", event: "navLog", log: "navLog", where: "navWhere", plan: "navPlan", mgr: "navMgr", mgrRecurring: "navMgrRecurring", mgrSettings: "navMgrSettings", settings: "navSettings", machinery: "navMachIndex", machine: "navMachIndex", machineLog: "navMachLog", purchases: "navPurchases", purchaseNew: "navPurchases", prices: "navPrices", priceItem: "navPrices", stock: "navStock", sheet: "navStock",
};

const HR_VIEWS = ["employees", "employee", "loans", "loan", "attendance", "documents", "document"];

function showView(name) {
  for (const v of VIEW_NAMES) $(v + "View").hidden = v !== name;
  if (["capture", "busy", "review", "done"].includes(name)) lastBillView = name === "busy" ? "capture" : name;
  for (const id of new Set(Object.values(NAV_OF))) $(id).classList.toggle("active", id === NAV_OF[name]);
  // The truck pages are addressed by the URL hash (so Back works); every other page clears it.
  if (!FLEET_VIEWS.includes(name) && location.hash) history.replaceState(null, "", location.pathname + location.search);
  if (!HR_VIEWS.includes(name)) $("hrLockBox").hidden = true;       // the PIN box belongs to the พนักงาน pages only, it must not follow you to the others
  closeSidebarIfDrawer();
  window.scrollTo(0, 0);
}

// ---- Sidebar: open by default on a wide screen (the toggle collapses it and remembers),
// a slide-in drawer on a phone. It never takes room from the page unless you want it to.
const isNarrow = () => window.matchMedia("(max-width: 900px)").matches;

function closeSidebarIfDrawer() {
  if (isNarrow()) { document.body.classList.remove("side-open"); $("menuBtn").setAttribute("aria-expanded", "false"); }
}

function initSidebar() {
  const body = document.body, btn = $("menuBtn");
  let stored = null;
  try { stored = localStorage.getItem("vt_side"); } catch (e) { /* storage blocked */ }
  if (!isNarrow() && stored === "closed") body.classList.add("side-collapsed");
  const sync = () => btn.setAttribute("aria-expanded", String(isNarrow() ? body.classList.contains("side-open") : !body.classList.contains("side-collapsed")));
  btn.addEventListener("click", () => {
    if (isNarrow()) body.classList.toggle("side-open");
    else {
      body.classList.toggle("side-collapsed");
      try { localStorage.setItem("vt_side", body.classList.contains("side-collapsed") ? "closed" : "open"); } catch (e) { /* ignore */ }
    }
    sync();
  });
  $("sideScrim").addEventListener("click", () => { body.classList.remove("side-open"); sync(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && body.classList.contains("side-open")) { body.classList.remove("side-open"); sync(); } });
  window.addEventListener("resize", sync);
  document.querySelectorAll(".side-head[aria-controls]").forEach((h) => h.addEventListener("click", () => {
    const open = h.getAttribute("aria-expanded") !== "true";
    h.setAttribute("aria-expanded", String(open));
    $(h.getAttribute("aria-controls")).hidden = !open;
  }));
  sync();
}

// --------------------------------------------------------------- vehicles
function vehicleLabel(v) {
  const bits = [`#${v.truck_number}`, v.plate, v.province].filter(Boolean).join(" ");
  const mm = `${v.make || ""} ${v.model || ""}`.trim();
  return mm ? `${bits} · ${mm}` : bits;
}

function fillVehicleSelect(select, selectedIdx = "") {
  select.replaceChildren(el("option", { value: "", text: "— เลือกรถ —" }));
  const byBranch = new Map();
  state.vehicles.forEach((v, i) => {
    if (!byBranch.has(v.branch)) byBranch.set(v.branch, []);
    byBranch.get(v.branch).push([i, v]);
  });
  for (const [branch, rows] of byBranch) {
    rows.sort((a, b) => num(a[1].truck_number) - num(b[1].truck_number));
    const group = el("optgroup", { label: `สาขา${branch}` });
    for (const [i, v] of rows) group.append(el("option", { value: String(i), text: vehicleLabel(v) }));
    select.append(group);
  }
  select.value = String(selectedIdx);
}

const selectedVehicle = (select) => (select.value === "" ? null : state.vehicles[Number(select.value)]);

// --------------------------------------------------------------- file picking
async function prepareImage(file) {
  // Phone photos are often 3-5 MB; shrink before upload (faster on a weak
  // signal, and the server caps size anyway). Small files (LINE-sized,
  // ~350 KB) go through untouched.
  if (!file.type.startsWith("image/") || file.size < 900 * 1024) return file;
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.86));
    return blob && blob.size < file.size ? new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
  } catch (_) {
    return file; // e.g. a format the browser can't decode -- let the server decide
  }
}

async function addFiles(list, target, allowPdf) {
  for (const f of list) {
    const isPdf = f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
    if (!(f.type.startsWith("image/") || (allowPdf && isPdf))) continue;
    const blob = isPdf ? f : await prepareImage(f);
    target.push({ blob, name: f.name, type: isPdf ? "application/pdf" : blob.type || "image/jpeg", url: URL.createObjectURL(blob) });
  }
  renderPicked();
}

function renderPicked() {
  const list = $("billList");
  list.replaceChildren(...state.pickBills.map((f, i) =>
    el("li", {},
      el("span", { class: "name", text: (f.type === "application/pdf" ? "📄 " : "🧾 ") + f.name }),
      el("span", { class: "muted small", text: `${Math.max(1, Math.round(f.blob.size / 1024))} KB` }),
      el("button", { class: "btn small", text: "ลบ", onclick: () => { state.pickBills.splice(i, 1); renderPicked(); } }))));
  $("refThumbs").replaceChildren(...state.pickRefs.map((f, i) =>
    el("div", { class: "thumb" },
      el("img", { src: f.url, alt: f.name }),
      el("button", { text: "×", title: "ลบ", onclick: () => { state.pickRefs.splice(i, 1); renderPicked(); } }))));
  $("extractBtn").disabled = state.pickBills.length === 0;
  rqRenderModes();
}

function wireDrop(dropId, inputId, target, allowPdf) {
  const drop = $(dropId), input = $(inputId);
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { addFiles([...input.files], target, allowPdf); input.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); addFiles([...e.dataTransfer.files], target, allowPdf); });
}

// --------------------------------------------------------------- extraction: see billqueue.js (the bills are read in the background, then checked one by one)

// --------------------------------------------------------------- review rendering
function flagInput(node, flagged) {
  if (flagged) {
    node.classList.add("flagged");
    const clear = () => node.classList.remove("flagged");
    node.addEventListener("focus", clear, { once: true });
    node.addEventListener("input", clear, { once: true });
  }
  return node;
}

// Picking a truck fills in its branch; and once there is a mileage, "the mileage of the next time" is that + the truck's oil-change distance
// (10,000 small / 20,000 big, from the plan: roster.oil_km). A next-mileage the bill itself gave, or one you typed, is never overwritten;
// one this filled in follows the mileage as you change it.
function bindVehicleAutofill({ vehicleSel, branchEl, mileageEl, nextEl, hintEl, fillBranchNow = false }) {
  const veh = () => selectedVehicle(vehicleSel);
  const hint = (text) => { if (hintEl) hintEl.textContent = text; };
  nextEl.dataset.auto = nextEl.value === "" ? "1" : "";
  const suggest = () => {
    if (nextEl.dataset.auto !== "1") return;
    const v = veh(), km = v && v.oil_km, mileage = num(mileageEl.value);
    if (km && mileage > 0) { nextEl.value = String(Math.round(mileage + km)); hint(`= เลขไมล์ + ${baht(km)} (รอบน้ำมันเครื่องของรถคันนี้) — แก้ได้`); }
    else { nextEl.value = ""; hint(""); }
  };
  nextEl.addEventListener("input", () => { nextEl.dataset.auto = nextEl.value === "" ? "1" : ""; hint(""); });
  mileageEl.addEventListener("input", suggest);
  vehicleSel.addEventListener("change", () => {
    const v = veh();
    if (v && v.branch) branchEl.value = v.branch;
    suggest();
  });
  if (fillBranchNow && veh() && veh().branch && !branchEl.value) branchEl.value = veh().branch;
  suggest();
}

// the shops to pick from: the shops you send trucks to, then the other names used before (the many spellings of one shop are already folded into it by the server)
const vendorNames = () => [...new Set([...(board.shops || []), ...(board.fixers || [])])];

const AT_MILEAGE_ROWS = 2;                         // readings shown on each side of this bill's date
// Under the mileage box: what this truck's other bills say, just before and just after this bill's date -- so a mistyped or misread odometer is seen at once.
// A mileage lower than an earlier bill's, or higher than a later one's, is flagged. `excludeId` = the bill being edited (it is not its own neighbour).
function bindMileageHistory({ vehicleSel, dateEl, mileageEl, hintEl, excludeId = "" }) {
  const cache = new Map();
  const load = (v) => { if (!cache.has(v.id)) cache.set(v.id, api(`/api/vehicles/${v.id}/mileage`).catch(() => [])); return cache.get(v.id); };
  let seq = 0;
  const paint = async () => {
    const my = ++seq, v = selectedVehicle(vehicleSel);
    if (!v) { fill(hintEl, ""); return; }
    const rows = (await load(v)).filter((r) => r.event_id !== excludeId);
    if (my !== seq) return;                         // the truck or date changed while this was loading
    const date = dateEl.value, mileage = num(mileageEl.value);
    if (!rows.length) { fill(hintEl, "ยังไม่มีเลขไมล์จากบิลอื่นของรถคันนี้"); return; }
    const before = rows.filter((r) => !date || r.event_date <= date).slice(0, AT_MILEAGE_ROWS);
    const after = rows.filter((r) => date && r.event_date > date).slice(-AT_MILEAGE_ROWS).reverse();      // the nearest later ones, newest first
    const fmt = (r) => `${beDate(r.event_date)} · ${Number(r.mileage).toLocaleString("th-TH")}`;
    const parts = [...after.map(fmt), "▶ บิลนี้", ...before.map(fmt)];
    const problems = [];
    if (mileage && before.length && mileage < before[0].mileage) problems.push(`น้อยกว่าบิล ${beDate(before[0].event_date)} (${Number(before[0].mileage).toLocaleString("th-TH")}) ที่เก่ากว่า`);
    if (mileage && after.length && mileage > after[after.length - 1].mileage) problems.push(`มากกว่าบิล ${beDate(after[after.length - 1].event_date)} (${Number(after[after.length - 1].mileage).toLocaleString("th-TH")}) ที่ใหม่กว่า`);
    fill(hintEl, el("div", { text: `ไมล์ที่เคยบันทึก: ${parts.join("  |  ")}` }),
      problems.length ? el("div", { class: "mileage-warn", text: `⚠ ${problems.join(" · ")} — ตรวจเลขไมล์อีกที` }) : null);
  };
  vehicleSel.addEventListener("change", paint);
  dateEl.addEventListener("input", paint);
  mileageEl.addEventListener("input", paint);
  paint();
}

function eventField(label, node, { wide = false } = {}) {
  return el("label", { class: "field" + (wide ? " wide" : "") }, el("span", { text: label }), node);
}

function render(draft, pickedVehicle) {
  const ev = draft.event;
  const flags = new Set(ev.low_confidence_fields);

  // warnings
  const box = $("warnings");
  box.hidden = draft.warnings.length === 0;
  box.replaceChildren(el("strong", { text: "ควรตรวจสอบ" }), el("ul", {}, ...draft.warnings.map((w) => el("li", { text: w }))));

  // vehicle: the one the user picked, else the roster match from the bill's plate
  let vIdx = "";
  if (pickedVehicle) vIdx = state.vehicles.indexOf(pickedVehicle);
  else if (draft.bill_vehicle.roster_match) vIdx = state.vehicles.findIndex((v) => v.plate === draft.bill_vehicle.roster_match.plate && v.branch === draft.bill_vehicle.roster_match.branch);
  const vSel = el("select", { id: "evVehicle" });
  fillVehicleSelect(vSel, vIdx < 0 ? "" : vIdx);
  flagInput(vSel, flags.has("vehicle") || vSel.value === "");

  const inp = (id, type, value, extra = {}) => el("input", { id, type, value: value ?? "", ...extra });
  const form = $("eventForm");
  form.replaceChildren(
    eventField("รถ", vSel),
    eventField("วันที่ (ในบิลเป็น พ.ศ.)", el("div", {}, flagInput(inp("evDate", "date", ev.event_date), flags.has("event_date")), el("div", { id: "evDateCap", class: "datecap" }))),
    // a free text box that also offers the shops (and the other names used before) -- for when the bill's name was not read, or is written another way
    eventField("ร้าน / อู่", el("div", {}, flagInput(inp("evVendor", "text", ev.vendor, { list: "evVendorList", autocomplete: "off", placeholder: "พิมพ์ หรือเลือกจากรายการ" }), flags.has("vendor")),
      el("datalist", { id: "evVendorList" }, ...vendorNames().map((n) => el("option", { value: n }))),
      ev.vendor_read && ev.vendor && ev.vendor_read !== ev.vendor ? el("div", { class: "datecap", text: `บิลเขียนว่า “${ev.vendor_read}” — รวมเป็นร้านนี้ (แก้ได้)` }) : null)),
    eventField("สาขา", flagInput(inp("evBranch", "text", ev.branch), flags.has("branch"))),
    eventField("เลขไมล์", el("div", {}, flagInput(inp("evMileage", "number", ev.mileage, { inputmode: "numeric" }), flags.has("mileage")), el("div", { id: "evMileHint", class: "datecap mileage-hint" }))),
    eventField("ไมล์ครั้งต่อไป", el("div", {}, flagInput(inp("evNext", "number", ev.next_service_mileage, { inputmode: "numeric" }), flags.has("next_service_mileage")), el("div", { id: "evNextHint", class: "datecap" }))),
    eventField("ยอดรวมในบิล (บาท)", flagInput(inp("evTotal", "number", ev.total_cost, { step: "0.01", inputmode: "decimal" }), flags.has("total_cost"))),
    eventField("หมายเหตุ", el("textarea", { id: "evNotes", text: ev.notes || "" }), { wide: true }),
  );
  state.totalManual = false;
  setCatStatus("");
  form.querySelectorAll("input,select").forEach((n) => n.addEventListener("input", updateTotals));
  $("evTotal").addEventListener("input", () => { state.totalManual = true; });
  bindDateField($("evDate"), $("evDateCap"));
  vSel.addEventListener("change", renderFixes);   // the "which repairs does this close" list follows the truck
  bindVehicleAutofill({ vehicleSel: vSel, branchEl: $("evBranch"), mileageEl: $("evMileage"), nextEl: $("evNext"), hintEl: $("evNextHint"), fillBranchNow: true });
  bindMileageHistory({ vehicleSel: vSel, dateEl: $("evDate"), mileageEl: $("evMileage"), hintEl: $("evMileHint") });
  renderFixes();

  // one block per bill file, photo beside its own rows
  const blocks = $("billBlocks");
  blocks.replaceChildren(...draft.bills.map((b) => billBlock(b, draft.line_items.filter((li) => li.file_index === b.file_index))));
  updateTotals();
}

function billBlock(bill, items) {
  const file = state.billFiles[bill.file_index];
  const preview = el("div", { class: "preview" });
  if (!file) preview.append(el("p", { class: "muted small", text: "(ไม่มีตัวอย่างไฟล์)" }));
  else if (file.type === "application/pdf") preview.append(el("iframe", { src: file.url, title: bill.filename }), el("button", { class: "btn small", type: "button", text: "ดูแบบเต็มจอ", onclick: () => buView(file.url, file.type, bill.filename) }));
  else preview.append(buThumb({ url: file.url, type: file.type, name: bill.filename, turns: file.turns || 0 }));      // turn it (↻) or click for the big pop-up with zoom

  const right = el("div", {}, el("h3", {}, `บิลที่ ${bill.file_index + 1}`, el("span", { class: "muted small", text: bill.filename })));
  if (!bill.ok) {
    right.append(el("p", { class: "bill-error", text: `อ่านไฟล์นี้ไม่สำเร็จ: ${bill.error}` }));
  } else {
    const tbody = el("tbody", { "data-file": String(bill.file_index) });
    items.forEach((it) => tbody.append(lineRow(it)));
    const head = el("tr", {}, ...["#", "รายการ", "จำนวน", "หน่วย", "ราคา/หน่วย", "รวม", "หมวด", ""].map((t) => el("th", { text: t })));
    right.append(
      el("div", { class: "tablewrap" }, el("table", { class: "lines" }, el("thead", {}, head), tbody)),
      el("div", { class: "addrow-bar" },
        el("button", { class: "btn addrow", text: "+ เพิ่มรายการ", onclick: () => { tbody.append(lineRow({})); renumber(); linesChanged(); } }),
        vatToggle(tbody)),
    );
  }
  return el("div", { class: "card bill" }, preview, right);
}

// ---- VAT: one tick adds the "VAT 7%" line of a bill (7 % of its other lines) and keeps it up to date as the lines change;
// type your own amount in that line and it stays as typed; untick (or delete the line) to take it away. A VAT line the
// reader already found counts as ticked, so the tick can never add VAT twice.
const VAT_LINE_RE = /^\s*(vat|ภาษีมูลค่าเพิ่ม|ภาษี)/i;
const vatRowOf = (tbody) => [...tbody.querySelectorAll("tr")].find((tr) => tr.dataset.vat);
const rowTotal = (tr) => num(tr.querySelector("[data-f=line_total]").value);

function vatToggle(tbody) {
  for (const tr of tbody.querySelectorAll("tr")) {
    if (VAT_LINE_RE.test(tr.querySelector("[data-f=description]").value) && !vatRowOf(tbody)) tr.dataset.vat = "manual";    // found on the bill: its amount is the bill's
  }
  const box = el("input", { type: "checkbox", id: `vat_${tbody.dataset.file}` });
  box.checked = !!vatRowOf(tbody);
  tbody._vatBox = box;
  box.addEventListener("change", () => {
    const existing = vatRowOf(tbody);
    if (!box.checked) { if (existing) existing.remove(); renumber(); linesChanged(); return; }
    if (existing) return;
    const base = [...tbody.querySelectorAll("tr")].reduce((s, tr) => s + rowTotal(tr), 0);
    const tr = lineRow({ description: "VAT 7%", category: "อื่นๆ", line_total: round2(base * 0.07) });
    tr.dataset.vat = "auto";
    tr.dataset.catManual = "1";                         // not for the automatic category sorting
    tr.addEventListener("input", (e) => { if (e.target.dataset.f === "line_total") tr.dataset.vat = "manual"; }, true);     // typed by hand: stop following the lines
    tbody.append(tr);
    renumber();
    linesChanged();
  });
  return el("label", { class: "mgcheck vat-toggle" }, box, "บิลนี้ยังไม่รวม VAT — เพิ่มบรรทัด VAT 7% ให้ (คิดจากรายการข้างบน)");
}

// keeps the automatic VAT lines at 7 % of their bill's other lines, and each tick in step with its table
function syncVat() {
  document.querySelectorAll("#billBlocks tbody").forEach((tbody) => {
    const row = vatRowOf(tbody);
    if (row && row.dataset.vat === "auto") {
      const base = [...tbody.querySelectorAll("tr")].filter((tr) => tr !== row).reduce((s, tr) => s + rowTotal(tr), 0);
      row.querySelector("[data-f=line_total]").value = String(round2(base * 0.07));
    }
    if (tbody._vatBox) tbody._vatBox.checked = !!row;
  });
}

// `hooks` lets another page (the bill editor on the truck history) reuse this exact row with its own
// totals and numbering; the review page uses the defaults.
function lineRow(it, hooks = { changed: () => linesChanged(), renumber: () => renumber() }) {
  const flags = new Set(it.low_confidence_fields || []);
  const cell = (cls, node) => el("td", { class: cls }, node);
  const text = (name, v, fl) => flagInput(el("input", { type: "text", "data-f": name, value: v ?? "" }), fl);
  const numIn = (name, v, fl, step = "any") => flagInput(el("input", { type: "number", step, inputmode: "decimal", "data-f": name, value: v ?? "" }), fl);
  const cat = flagInput(el("select", { "data-f": "category" }, el("option", { value: "", text: "—" }), ...state.categories.map((c) => el("option", { value: c, text: c }))), flags.has("category"));
  cat.value = it.category || "";
  // Picking a category by hand takes this row out of automatic re-sorting.
  cat.addEventListener("change", () => { tr.dataset.catManual = "1"; cat.classList.remove("auto"); });

  const tr = el("tr", {},
    cell("num", el("span", { class: "n" })),
    cell("desc", text("description", it.description, flags.has("description"))),
    cell("qty", numIn("quantity", it.quantity, flags.has("quantity"))),
    cell("unit", text("unit", it.unit, flags.has("unit"))),
    cell("price", numIn("unit_cost", it.unit_cost, false, "0.01")),
    cell("total", numIn("line_total", it.line_total, flags.has("cost"), "0.01")),
    cell("cat", cat),
    cell("", el("button", { class: "btn danger", title: "ลบแถว", text: "✕", onclick: () => { tr.remove(); hooks.renumber(); hooks.changed(); } })),
  );
  const f = (n) => tr.querySelector(`[data-f=${n}]`);
  // Corrected the wording -> the old category belonged to the OLD wording; re-decide it.
  f("description").addEventListener("input", () => { tr.dataset.descEdited = "1"; });
  f("description").addEventListener("change", () => recategorize([tr]));
  if (flags.has("category")) tr.dataset.catUncertain = "1";   // the model itself wasn't sure of this one
  const setIfFinite = (node, v) => { node.value = Number.isFinite(v) && v > 0 ? String(Math.round(v * 100) / 100) : node.value; };
  f("quantity").addEventListener("input", () => { if (num(f("unit_cost").value) > 0) setIfFinite(f("line_total"), num(f("quantity").value) * num(f("unit_cost").value)); hooks.changed(); });
  f("unit_cost").addEventListener("input", () => { if (num(f("quantity").value) > 0) setIfFinite(f("line_total"), num(f("quantity").value) * num(f("unit_cost").value)); hooks.changed(); });
  f("line_total").addEventListener("input", () => { if (num(f("quantity").value) > 0) setIfFinite(f("unit_cost"), num(f("line_total").value) / num(f("quantity").value)); hooks.changed(); });
  tr.addEventListener("input", () => { tr.dataset.edited = "1"; });      // any touch: the AI-uncertainty flags no longer apply to this line
  queueMicrotask(() => hooks.renumber());
  return tr;
}

function renumber() {
  let n = 0;
  document.querySelectorAll("#billBlocks tbody tr").forEach((tr) => { tr.querySelector(".n").textContent = String(++n); });
}

// --------------------------------------------------------------- totals + payload
function readLines() {
  const out = [];
  document.querySelectorAll("#billBlocks tbody").forEach((tbody) => {
    tbody.querySelectorAll("tr").forEach((tr) => {
      const g = (n) => tr.querySelector(`[data-f=${n}]`).value.trim();
      const description = g("description");
      if (!description && !num(g("line_total"))) return; // an untouched blank row
      out.push({
        file_index: Number(tbody.dataset.file),
        description,
        category: g("category") || null,
        quantity: num(g("quantity")) || null,
        unit: g("unit") || null,
        unit_cost: num(g("unit_cost")) || null,
        line_total: num(g("line_total")) || null,
      });
    });
  });
  return out.map((l, i) => ({ line_number: i + 1, ...l }));
}

// --------------------------------------------------------------- re-categorize
let catSeq = 0;

// The status line sits next to the re-categorize button: one on the review page, one on the manual form.
function setCatStatus(text, isError = false) {
  for (const id of ["catStatus", "mCatStatus"]) {
    const n = $(id);
    if (!n) continue;
    n.textContent = text;
    n.classList.toggle("err", isError);
  }
}

// Re-decide categories from the CURRENT description text (server: /api/categorize).
// Rows whose category the user picked by hand are left alone. A response is
// ignored if the user has since touched that row again (stale) -- latest wins.
async function recategorize(trs) {
  const all = trs.filter((tr) => tr.querySelector("[data-f=description]").value.trim());
  const rows = all.filter((tr) => tr.dataset.catManual !== "1");
  const skipped = all.length - rows.length;
  if (!rows.length) {
    if (skipped) setCatStatus(`ข้าม ${skipped} รายการที่คุณเลือกหมวดเอง`);
    return;
  }
  const stamp = String(++catSeq);
  const sels = rows.map((tr) => tr.querySelector("[data-f=category]"));
  rows.forEach((tr, i) => { tr.dataset.catSeq = stamp; sels[i].classList.add("busy"); });
  setCatStatus("กำลังจัดหมวด…");
  try {
    const res = await api("/api/categorize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: rows.map((tr) => ({
        description: tr.querySelector("[data-f=description]").value.trim(),
        unit: tr.querySelector("[data-f=unit]").value.trim() || null,
      })) }),
    });
    let changed = 0;
    rows.forEach((tr, i) => {
      const sel = sels[i];
      sel.classList.remove("busy");
      if (tr.dataset.catSeq !== stamp || tr.dataset.catManual === "1") return; // superseded or user took over
      const cat = res.categories[i];
      if (cat && sel.value !== cat) {
        sel.value = cat;
        sel.classList.remove("flagged");
        sel.classList.add("auto");
        sel.addEventListener("focus", () => sel.classList.remove("auto"), { once: true });
        changed++;
      }
    });
    setCatStatus((changed ? `จัดหมวดใหม่ ${changed} รายการ` : "หมวดถูกต้องแล้ว ไม่มีอะไรเปลี่ยน")
      + (skipped ? ` · ข้าม ${skipped} รายการที่คุณเลือกเอง` : ""));
  } catch (err) {
    sels.forEach((s) => s.classList.remove("busy"));
    setCatStatus("จัดหมวดอัตโนมัติไม่สำเร็จ — เลือกหมวดเองได้ หรือกดปุ่มจัดหมวดใหม่อีกครั้ง", true);
  }
}

const round2 = (n) => Math.round(n * 100) / 100;
const lineSum = () => readLines().reduce((s, l) => s + (l.line_total || 0), 0);

// Any edit to the line items: the header total follows the new sum, unless
// the user has typed their own total (then it's theirs to keep).
function linesChanged() {
  syncVat();
  if (!state.totalManual) {
    const t = $("evTotal");
    t.value = String(round2(lineSum()));
    t.classList.remove("flagged");
  }
  updateTotals();
}

function updateTotals() {
  if (!state.draft) return;
  const sum = round2(lineSum());
  const header = round2(num($("evTotal")?.value));
  // The header total follows the line items (see linesChanged), so the only
  // time the two differ is the moment the page opens -- the total the model
  // read hasn't been replaced yet; the top warning box already explains that
  // mismatch -- or after you type your own total. Show it only then.
  const parts = [el("span", {}, "ผลรวมรายการ ", el("span", { class: "big", text: fmtMoney(sum) }))];
  if (Math.abs(header - sum) > 0.005) {
    parts.push(el("span", {}, "ยอดรวมที่ตั้งไว้ ", el("span", { class: "big", text: fmtMoney(header) })));
    parts.push(el("button", {
      class: "btn", text: "ตั้งยอดรวม = ผลรวมรายการ",
      onclick: () => { state.totalManual = false; linesChanged(); },
    }));
  }
  $("totalsBox").replaceChildren(...parts);
}

function buildPayload() {
  const veh = selectedVehicle($("evVehicle"));
  const v = (id) => $(id).value.trim();
  // Bill files first, then reference photos -- the server relies on this
  // order (each line's file_index is an index into `attachments`).
  const bill_extractions = {};
  (state.draft?.bills || []).forEach((b) => { if (b.ok && b.extraction) bill_extractions[String(b.file_index)] = b.extraction; });
  return {
    vehicle_id: veh ? veh.id : null,
    source: "bill",
    event: {
      event_date: v("evDate") || null, vendor: v("evVendor") || null, branch: v("evBranch") || null,
      mileage: num(v("evMileage")) || null, next_service_mileage: num(v("evNext")) || null,
      total_cost: num(v("evTotal")) || null, notes: v("evNotes"),
      low_confidence_fields: state.draft ? state.draft.event.low_confidence_fields : [],
    },
    line_items: readLines(),
    attachments: [
      ...state.billFiles.map((f) => ({ type: "bill", filename: f.name })),
      ...state.refFiles.map((f) => ({ type: "reference_photo", filename: f.name })),
    ],
    bill_extractions,
    fixes_request_ids: selectedFixIds(),
  };
}

function showReviewError(text) {
  const err = $("reviewError");
  err.hidden = !text;
  err.textContent = text || "";
}

let saving = false;

async function confirmSave() {
  if (saving) return;
  const p = buildPayload();
  const problems = [];
  if (!p.vehicle_id) problems.push("ยังไม่ได้เลือกรถ");
  if (!p.event.event_date) problems.push("ยังไม่ได้ใส่วันที่");
  if (!p.line_items.length) problems.push("ไม่มีรายการในบิล");
  if (!state.billFiles.length) problems.push("ไม่มีไฟล์บิล (ตัวอย่างข้อมูล บันทึกไม่ได้)");
  showReviewError(problems.join(" · "));
  if (problems.length) return;

  saving = true;
  const btn = $("confirmBtn");
  btn.disabled = true;
  btn.textContent = "กำลังบันทึก…";
  try {
    let res = await postEvent(p, false);
    if (res.status === 409) {
      const dup = (await res.json()).duplicates || [];
      const names = dup.map((d) => `${d.filename} (บันทึกไว้เมื่อ ${beDateOf(new Date(d.saved_at))})`).join("\n");
      if (!confirm(`บิลนี้เคยบันทึกไว้แล้ว:\n${names}\n\nต้องการบันทึกซ้ำอีกครั้งไหม?`)) return;
      res = await postEvent(p, true);
    }
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch (_) { /* not json */ }
      showReviewError(detail || `บันทึกไม่สำเร็จ (HTTP ${res.status}) — ข้อมูลที่กรอกยังอยู่ ลองอีกครั้ง`);
      return;
    }
    const saved = await res.json();
    const veh = selectedVehicle($("evVehicle"));
    $("doneText").textContent =
      `${veh ? vehicleLabel(veh) : ""} · ${beDate(p.event.event_date)} · ${p.line_items.length} รายการ · ${state.billFiles.length + state.refFiles.length} ไฟล์ (รหัส ${saved.event_id.slice(0, 8)})`;
    const closed = p.fixes_request_ids.length;
    $("doneClosed").hidden = closed === 0;
    $("doneClosed").textContent = closed ? `ปิดงานซ่อมให้แล้ว ${closed} งาน` : "";
    resetAfterSave();
    rqSaved(veh ? veh.id : null);                          // off the queue of bills waiting to be checked
    showView("done");
  } catch (err) {
    showReviewError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ — ข้อมูลที่กรอกยังอยู่ ลองอีกครั้ง");
  } finally {
    saving = false;
    btn.disabled = false;
    btn.textContent = "ยืนยันและบันทึก";
  }
}

function postEvent(payload, allowDuplicates, files = [...state.billFiles, ...state.refFiles]) {
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  fd.append("allow_duplicates", allowDuplicates ? "true" : "false");
  files.forEach((f) => fd.append("files", f.blob, f.name));
  return fetch("/api/events", { method: "POST", body: fd, headers: { "X-App-Token": state.token } });
}

function resetAfterSave() {
  [...state.billFiles, ...state.refFiles].forEach((f) => URL.revokeObjectURL(f.url));
  state.billFiles.length = 0;
  state.refFiles.length = 0;
  state.draft = null;
  showReviewError("");
  renderPicked();
  $("vehicleSelect").value = "";
  clearAttachment();
  loadBoard();            // the requests this bill closed move off the board
}

// --------------------------------------------------------------- the access key
// Kept on this device (localStorage) so a phone does not ask for it again every time the tab is closed; an older key
// kept for the tab only (sessionStorage) is picked up once. Storage can be blocked (private window): then it is just
// remembered until the tab closes.
function storedToken() {
  try { return localStorage.getItem("vt_token") || sessionStorage.getItem("vt_token") || ""; } catch (e) { return ""; }
}

function saveToken(token) {
  try { localStorage.setItem("vt_token", token); } catch (e) { /* blocked: the in-memory copy still works for this visit */ }
  try { sessionStorage.setItem("vt_token", token); } catch (e) { /* ignore */ }
}

// --------------------------------------------------------------- init
async function init() {
  const bootHash = location.hash;       // a bookmarked / reloaded truck or bill page: kept through start-up, routed to once the data is loaded
  const params = new URLSearchParams(location.search);
  state.token = params.get("token") || storedToken();
  if (!isPlainAscii(state.token)) { state.token = ""; saveToken(""); }          // a code saved with the Thai keyboard on can never work: forget it
  if (params.get("token")) { saveToken(state.token); history.replaceState(null, "", location.pathname + (params.get("sample") ? "?sample=1" : "") + location.hash); }

  wireDrop("billDrop", "billInput", state.pickBills, true);
  wireDrop("refDrop", "refInput", state.pickRefs, false);
  $("extractBtn").addEventListener("click", rqEnqueue);
  $("backBtn").addEventListener("click", () => { rq.cur = null; showView("capture"); });
  $("confirmBtn").addEventListener("click", confirmSave);
  $("newBtn").addEventListener("click", rqNext);
  initBillQueue();
  $("toBoardBtn").addEventListener("click", () => showView("board"));
  $("navBoard").addEventListener("click", () => { showView("board"); loadDueStrip(); });
  $("navBills").addEventListener("click", () => showView(lastBillView));
  $("navDash").addEventListener("click", () => { showView("dash"); loadDashboard(); });
  $("navRoster").addEventListener("click", () => goHash("#roster"));
  $("navLog").addEventListener("click", () => goHash("#log"));
  $("navWhere").addEventListener("click", () => goHash("#where"));
  $("navPlan").addEventListener("click", () => goHash("#plan"));
  $("navSettings").addEventListener("click", () => goHash("#settings"));
  $("navMgr").addEventListener("click", () => goHash("#mgr"));
  $("navEmployees").addEventListener("click", () => goHash("#employees"));
  $("navLoans").addEventListener("click", () => goHash("#loans"));
  $("navAttendance").addEventListener("click", () => goHash("#attendance"));
  $("navDocs").addEventListener("click", () => goHash("#documents"));
  $("navMgrRecurring").addEventListener("click", () => goHash("#mgr-recurring"));
  $("navMgrSettings").addEventListener("click", () => goHash("#mgr-settings"));
  $("navMachIndex").addEventListener("click", () => goHash("#machinery"));
  $("navMachLog").addEventListener("click", () => goHash("#machine-log"));
  $("navPurchases").addEventListener("click", () => goHash("#purchases"));
  $("navPrices").addEventListener("click", () => goHash("#prices"));
  $("navStock").addEventListener("click", () => goHash("#stock"));
  initSidebar();
  initTheme();
  // The button only re-checks lines where it can help: ones whose wording the
  // user edited, or whose category the model flagged as unsure. Everything else
  // was categorized from the PHOTO at extraction time, which a text-only
  // re-check can't beat -- re-sorting those could only make them worse.
  $("recatBtn").addEventListener("click", () => {
    const rows = [...document.querySelectorAll("#billBlocks tbody tr")];
    const eligible = rows.filter((tr) => tr.dataset.descEdited === "1" || tr.dataset.catUncertain === "1");
    if (!eligible.length) {
      setCatStatus("ไม่มีรายการที่ต้องจัดหมวดใหม่ — ยังไม่ได้แก้ชื่อรายการไหน และระบบมั่นใจหมวดทุกรายการ");
      return;
    }
    recategorize(eligible);
  });
  $("tokenSave").addEventListener("click", () => {
    const typed = $("tokenInput").value.trim();
    $("tokenError").hidden = isPlainAscii(typed);
    if (!isPlainAscii(typed)) { $("tokenInput").value = ""; $("tokenInput").focus(); return; }
    state.token = typed; saveToken(state.token); loadMeta().then(loadBoard).then(() => { if (!$("homeView").hidden) loadHome(); }); });

  showView("home");                     // the app opens on the home page (clears the hash, as every non-truck page does)
  if (bootHash) history.replaceState(null, "", location.pathname + location.search + bootHash);
  await loadMeta();
  await initBoard();
  initDashboard();
  initHome();
  initFleet();
  initLog();
  initRoster();
  initEmployees();
  initLoans();
  initAttendance();
  initDocuments();
  initMachinery();
  initBuy();
  initPlan();
  initMgr();
  initManual();
  if (location.hash) route();
  else if (params.get("tab") !== "dash" && !params.get("sample")) loadHome();
  if (params.get("tab") === "dash") { showView("dash"); loadDashboard(); }
  if (params.get("sample")) {
    state.draft = await api("/api/dev/sample-draft");
    render(state.draft, null);
    showView("review");
  }
}

async function loadMeta() {
  try {
    const meta = await api("/api/meta");
    state.categories = meta.categories;
    state.vehicles = meta.vehicles;
    board.fixers = meta.fixers || [];
    board.shops = meta.shops || [];
    board.shopContacts = meta.shop_contacts || {};
    state.usages = meta.usages || [];
    state.branches = meta.branches || [];
    state.fleetReady = meta.fleet_ready;
    state.fleetV7 = !!meta.fleet_v7;
    state.fleetV8 = !!meta.fleet_v8;
    state.vehicleClasses = meta.vehicle_classes || [];
    $("addShop").replaceChildren(el("option", { value: "", text: "ยังไม่ได้ส่งอู่ (รอซ่อม)" }), ...board.shops.map((n) => el("option", { value: n, text: `ส่งอู่เลย: ${n}` })));
    $("tokenBox").hidden = true;
    fillVehicleSelect($("vehicleSelect"));
    fillVehicleSelect($("addVehicle"));
    if (typeof startToday === "function") startToday();          // the bar at the top and the numbers beside the menu
  } catch (err) {
    $("tokenBox").hidden = false;
    $("vehicleSelect").replaceChildren(el("option", { value: "", text: err.status === 403 || err.status === 503 ? "ต้องใส่รหัสเข้าใช้งานก่อน" : "โหลดรายชื่อรถไม่สำเร็จ" }));
  }
}

init();
