"use strict";

// --------------------------------------------------------------- state
const state = {
  token: "",
  categories: [],
  vehicles: [],          // roster rows, index = option value in every vehicle <select>
  billFiles: [],         // [{blob, name, type, url}]  -- sent for extraction
  refFiles: [],          // [{blob, name, type, url}]  -- stored as-is, never extracted
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
async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), "X-App-Token": state.token } });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch (_) { /* not json */ }
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

const VIEW_NAMES = ["board", "dash", "capture", "busy", "review", "done", "roster", "event", "log", "where", "machinery"];
// which sidebar entry lights up for each view
const NAV_OF = {
  board: "navBoard", dash: "navDash", capture: "navBills", busy: "navBills", review: "navBills", done: "navBills",
  roster: "navRoster", event: "navLog", log: "navLog", where: "navWhere", machinery: "navMachinery",
};

function showView(name) {
  for (const v of VIEW_NAMES) $(v + "View").hidden = v !== name;
  if (["capture", "busy", "review", "done"].includes(name)) lastBillView = name === "busy" ? "capture" : name;
  for (const id of new Set(Object.values(NAV_OF))) $(id).classList.toggle("active", id === NAV_OF[name]);
  // The truck pages are addressed by the URL hash (so Back works); every other page clears it.
  if (!FLEET_VIEWS.includes(name) && location.hash) history.replaceState(null, "", location.pathname + location.search);
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
  list.replaceChildren(...state.billFiles.map((f, i) =>
    el("li", {},
      el("span", { class: "name", text: (f.type === "application/pdf" ? "📄 " : "🧾 ") + f.name }),
      el("span", { class: "muted small", text: `${Math.max(1, Math.round(f.blob.size / 1024))} KB` }),
      el("button", { class: "btn small", text: "ลบ", onclick: () => { state.billFiles.splice(i, 1); renderPicked(); } }))));
  $("refThumbs").replaceChildren(...state.refFiles.map((f, i) =>
    el("div", { class: "thumb" },
      el("img", { src: f.url, alt: f.name }),
      el("button", { text: "×", title: "ลบ", onclick: () => { state.refFiles.splice(i, 1); renderPicked(); } }))));
  $("extractBtn").disabled = state.billFiles.length === 0;
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

// --------------------------------------------------------------- extraction
async function runExtract() {
  $("captureError").hidden = true;
  const veh = selectedVehicle($("vehicleSelect"));
  const fd = new FormData();
  state.billFiles.forEach((f) => fd.append("files", f.blob, f.name));
  fd.append("plate", veh ? veh.plate : "");

  showView("busy");
  const t0 = Date.now();
  const timer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $("busyText").textContent = `กำลังอ่านบิล ${state.billFiles.length} ไฟล์… ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 500);
  try {
    state.draft = await api("/api/extract", { method: "POST", body: fd });
    render(state.draft, veh);
    showView("review");
  } catch (err) {
    showView("capture");
    $("captureError").textContent = err.status === 403 ? "รหัสเข้าใช้งานไม่ถูกต้อง" : `อ่านบิลไม่สำเร็จ: ${err.message}`;
    $("captureError").hidden = false;
  } finally {
    clearInterval(timer);
  }
}

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
    eventField("ร้าน / อู่", flagInput(inp("evVendor", "text", ev.vendor), flags.has("vendor"))),
    eventField("สาขา", flagInput(inp("evBranch", "text", ev.branch), flags.has("branch"))),
    eventField("เลขไมล์", flagInput(inp("evMileage", "number", ev.mileage, { inputmode: "numeric" }), flags.has("mileage"))),
    eventField("ไมล์ครั้งต่อไป", flagInput(inp("evNext", "number", ev.next_service_mileage, { inputmode: "numeric" }), flags.has("next_service_mileage"))),
    eventField("ยอดรวมในบิล (บาท)", flagInput(inp("evTotal", "number", ev.total_cost, { step: "0.01", inputmode: "decimal" }), flags.has("total_cost"))),
    eventField("หมายเหตุ", el("textarea", { id: "evNotes", text: ev.notes || "" }), { wide: true }),
  );
  state.totalManual = false;
  setCatStatus("");
  form.querySelectorAll("input,select").forEach((n) => n.addEventListener("input", updateTotals));
  $("evTotal").addEventListener("input", () => { state.totalManual = true; });
  bindDateField($("evDate"), $("evDateCap"));
  vSel.addEventListener("change", renderFixes);   // the "which repairs does this close" list follows the truck
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
  else if (file.type === "application/pdf") preview.append(el("iframe", { src: file.url, title: bill.filename }), el("a", { href: file.url, target: "_blank", rel: "noopener", text: "เปิดในแท็บใหม่" }));
  else preview.append(el("a", { href: file.url, target: "_blank", rel: "noopener" }, el("img", { src: file.url, alt: bill.filename })));

  const right = el("div", {}, el("h3", {}, `บิลที่ ${bill.file_index + 1}`, el("span", { class: "muted small", text: bill.filename })));
  if (!bill.ok) {
    right.append(el("p", { class: "bill-error", text: `อ่านไฟล์นี้ไม่สำเร็จ: ${bill.error}` }));
  } else {
    const tbody = el("tbody", { "data-file": String(bill.file_index) });
    items.forEach((it) => tbody.append(lineRow(it)));
    const head = el("tr", {}, ...["#", "รายการ", "จำนวน", "หน่วย", "ราคา/หน่วย", "รวม", "หมวด", ""].map((t) => el("th", { text: t })));
    right.append(
      el("div", { class: "tablewrap" }, el("table", { class: "lines" }, el("thead", {}, head), tbody)),
      el("button", { class: "btn addrow", text: "+ เพิ่มรายการ", onclick: () => { tbody.append(lineRow({})); renumber(); linesChanged(); } }),
    );
  }
  return el("div", { class: "card bill" }, preview, right);
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

// --------------------------------------------------------------- init
async function init() {
  const bootHash = location.hash;       // a bookmarked / reloaded truck or bill page: kept through start-up, routed to once the data is loaded
  const params = new URLSearchParams(location.search);
  state.token = params.get("token") || sessionStorage.getItem("vt_token") || "";
  if (params.get("token")) { sessionStorage.setItem("vt_token", state.token); history.replaceState(null, "", location.pathname + (params.get("sample") ? "?sample=1" : "") + location.hash); }

  wireDrop("billDrop", "billInput", state.billFiles, true);
  wireDrop("refDrop", "refInput", state.refFiles, false);
  $("extractBtn").addEventListener("click", runExtract);
  $("backBtn").addEventListener("click", () => showView("capture"));
  $("confirmBtn").addEventListener("click", confirmSave);
  $("newBtn").addEventListener("click", () => showView("capture"));
  $("toBoardBtn").addEventListener("click", () => showView("board"));
  $("navBoard").addEventListener("click", () => showView("board"));
  $("navBills").addEventListener("click", () => showView(lastBillView));
  $("navDash").addEventListener("click", () => { showView("dash"); loadDashboard(); });
  $("navRoster").addEventListener("click", () => goHash("#roster"));
  $("navLog").addEventListener("click", () => goHash("#log"));
  $("navWhere").addEventListener("click", () => goHash("#where"));
  $("navMachinery").addEventListener("click", () => showView("machinery"));
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
  $("tokenSave").addEventListener("click", () => { state.token = $("tokenInput").value.trim(); sessionStorage.setItem("vt_token", state.token); loadMeta().then(loadBoard); });

  showView("board");                    // (clears the hash, as every non-truck page does)
  if (bootHash) history.replaceState(null, "", location.pathname + location.search + bootHash);
  await loadMeta();
  await initBoard();
  initDashboard();
  initFleet();
  initLog();
  initRoster();
  initManual();
  if (location.hash) route();
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
    state.usages = meta.usages || [];
    state.branches = meta.branches || [];
    state.fleetReady = meta.fleet_ready;
    $("addShop").replaceChildren(el("option", { value: "", text: "ยังไม่ได้ส่งอู่ (รอซ่อม)" }), ...board.shops.map((n) => el("option", { value: n, text: `ส่งอู่เลย: ${n}` })));
    $("tokenBox").hidden = true;
    fillVehicleSelect($("vehicleSelect"));
    fillVehicleSelect($("addVehicle"));
  } catch (err) {
    $("tokenBox").hidden = false;
    $("vehicleSelect").replaceChildren(el("option", { value: "", text: err.status === 403 || err.status === 503 ? "ต้องใส่รหัสเข้าใช้งานก่อน" : "โหลดรายชื่อรถไม่สำเร็จ" }));
  }
}

init();
