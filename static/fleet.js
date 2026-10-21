"use strict";

// Cars > ค้นหารถ / ประวัติ: search a truck, see its repair history as a list,
// open one event to read it like a bill. Read-only here (editing is a
// separate mode on the event page). Data comes from /api/vehicles,
// /api/vehicles/{id}/events and /api/events/{id} (app/history.py).
//
// Navigation is by URL hash (#search, #vehicle=<id>, #event=<id>) so the
// browser's Back button walks list -> truck -> bill, and a view can be
// bookmarked or shared. Loaded BEFORE app.js: nothing runs at load time;
// helpers from board.js / dashboard.js / app.js are used at call time only.

const fleet = { vehicles: [], loaded: false, current: null, event: null };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FLEET_VIEWS = ["search", "vehicle", "event"];

// ------------------------------------------------------------------ routing
function route() {
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (h === "search") return openSearch();
  const m = /^(vehicle|event)=(.+)$/.exec(h);
  if (m && UUID_RE.test(m[2])) return m[1] === "vehicle" ? openVehicle(m[2]) : openEvent(m[2]);
  return null;
}

function goHash(hash) {
  if (location.hash === hash) route();      // same hash: no hashchange fires, so route by hand
  else location.hash = hash;
}

const goSearch = () => goHash("#search");
const goVehicle = (id) => goHash(`#vehicle=${id}`);
const goEvent = (id) => goHash(`#event=${id}`);

// ----------------------------------------------------------------- loading
async function ensureVehicles(force = false) {
  if (fleet.loaded && !force) return true;
  try {
    fleet.vehicles = await api("/api/vehicles");
    fleet.loaded = true;
    return true;
  } catch (err) {
    fleet.loadError = err.message;
    return false;
  }
}

// replaceChildren() turns a null/undefined argument into the TEXT "null": drop empty slots first.
const fill = (container, ...nodes) => container.replaceChildren(...nodes.filter(Boolean));

const showErr = (id, text) => { const n = $(id); n.hidden = !text; n.textContent = text || ""; };

// ------------------------------------------------------------------ search
const vehNorm = (s) => String(s || "").toLowerCase().replace(/[\s\-_.]/g, "");
const vehHay = (v) => [v.truck_number, v.plate, v.province, v.make, v.model, v.branch].map(vehNorm).join("|");

// 0 = no match. An exact truck number beats a plate prefix beats a match anywhere.
function vehScore(v, tokens) {
  if (!tokens.length) return 1;
  const hay = vehHay(v), num_ = vehNorm(v.truck_number), plate = vehNorm(v.plate);
  let total = 0;
  for (const t of tokens) {
    if (!hay.includes(t)) return 0;
    total += t === num_ ? 100 : plate.startsWith(t) ? 60 : plate.includes(t) ? 40 : 20;
  }
  return total;
}

const numKey = (v) => { const n = parseInt(v.truck_number, 10); return Number.isFinite(n) ? n : 1e9; };

const SORTS = {
  number: (a, b) => (a.branch || "").localeCompare(b.branch || "", "th") || numKey(a) - numKey(b) || a.plate.localeCompare(b.plate),
  recent: (a, b) => (b.last_service_date || "").localeCompare(a.last_service_date || "") || numKey(a) - numKey(b),
  spend: (a, b) => b.spend_total - a.spend_total || numKey(a) - numKey(b),
  count: (a, b) => b.repairs_total - a.repairs_total || numKey(a) - numKey(b),
  stale: (a, b) => (a.last_service_date || "0000").localeCompare(b.last_service_date || "0000") || numKey(a) - numKey(b),
};

async function openSearch() {
  showView("search");
  showErr("searchError", "");
  if (!(await ensureVehicles())) {
    showErr("searchError", `โหลดรายชื่อรถไม่สำเร็จ: ${fleet.loadError}`);
    return;
  }
  const sel = $("vehBranch");
  if (!sel.dataset.filled) {
    const branches = [...new Set(fleet.vehicles.map((v) => v.branch).filter(Boolean))].sort((a, b) => a.localeCompare(b, "th"));
    sel.replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...branches.map((b) => el("option", { value: b, text: b })));
    sel.dataset.filled = "1";
  }
  renderVehicles();
  $("vehSearch").focus();
}

function renderVehicles() {
  const tokens = $("vehSearch").value.trim().split(/\s+/).map(vehNorm).filter(Boolean);
  const branch = $("vehBranch").value;
  let rows = fleet.vehicles
    .filter((v) => !branch || v.branch === branch)
    .map((v) => [v, vehScore(v, tokens)])
    .filter(([, s]) => s > 0);
  rows = tokens.length
    ? rows.sort((a, b) => b[1] - a[1] || SORTS.number(a[0], b[0]))     // best match first
    : rows.sort((a, b) => SORTS[$("vehSort").value](a[0], b[0]));
  $("searchCount").textContent = `พบ ${rows.length} คัน จากทั้งหมด ${fleet.vehicles.length} คัน`;
  $("vehList").replaceChildren(...(rows.length ? rows.map(([v]) => vehCard(v)) : [el("p", { class: "muted empty", text: "ไม่พบรถที่ตรงกับคำค้น ลองพิมพ์ทะเบียนบางส่วน เช่น 4786" })]));
}

function ago(days) { return days === 0 ? "วันนี้" : `${days} วันก่อน`; }

function vehCard(v) {
  const last = v.last_service_date ? `ซ่อมล่าสุด ${beDate(v.last_service_date)} · ${ago(v.days_since_service)}` : "ยังไม่มีประวัติการซ่อม";
  return el("button", { class: "veh", type: "button", onclick: () => goVehicle(v.vehicle_id) },
    el("div", { class: "veh-top" },
      el("strong", { class: "veh-no", text: v.truck_number ? `#${v.truck_number}` : "–" }),
      el("span", { class: "veh-plate", text: v.plate }),
      el("span", { class: "grow" }),
      v.open_requests ? el("span", { class: "badge warn", text: `รอซ่อม ${v.open_requests}` }) : null,
      v.awaiting_bill_requests ? el("span", { class: "badge warn", text: `รอบิล ${v.awaiting_bill_requests}` }) : null),
    el("div", { class: "muted small", text: [v.branch, `${v.make} ${v.model}`.trim()].filter(Boolean).join(" · ") }),
    el("div", { class: "veh-last small", text: last }),
    v.repairs_total ? el("div", { class: "veh-stats small" }, el("span", { text: `ซ่อม ${v.repairs_total} ครั้ง` }), el("span", { class: "veh-spend", text: `รวม ${baht(v.spend_total)} ฿` })) : null);
}

// ----------------------------------------------------------------- vehicle
const vehTitle = (v) => [v.truck_number ? `#${v.truck_number}` : null, v.plate].filter(Boolean).join(" ");

async function openVehicle(id) {
  showView("vehicle");
  showErr("vehicleError", "");
  $("vehHead").replaceChildren();
  $("vehEventCount").textContent = "";
  $("vehEvents").replaceChildren(el("p", { class: "muted", text: "กำลังโหลด…" }));
  if (!(await ensureVehicles())) { showErr("vehicleError", `โหลดข้อมูลรถไม่สำเร็จ: ${fleet.loadError}`); return; }
  const v = fleet.vehicles.find((x) => x.vehicle_id === id);
  if (!v) { showErr("vehicleError", "ไม่พบรถคันนี้"); $("vehEvents").replaceChildren(); return; }
  fleet.current = v;
  renderVehHead(v);
  try {
    const events = await api(`/api/vehicles/${id}/events`);
    if (fleet.current !== v) return;            // the user already moved on to another truck
    renderEvents(events);
  } catch (err) {
    $("vehEvents").replaceChildren();
    showErr("vehicleError", `โหลดประวัติไม่สำเร็จ: ${err.message}`);
  }
}

function stat(label, value) {
  return el("div", { class: "vstat" }, el("div", { class: "vstat-label", text: label }), el("div", { class: "vstat-value", text: value }));
}

function renderVehHead(v) {
  const open = (board.active || []).filter((r) => r.vehicle_id === v.vehicle_id);
  fill($("vehHead"),
    el("div", { class: "vehhead-top" },
      el("div", {},
        el("div", { class: "vehhead-title" }, el("strong", { text: vehTitle(v) }), v.branch ? el("span", { class: "muted", text: v.branch }) : null),
        el("div", { class: "muted", text: [`${v.make} ${v.model}`.trim(), v.province].filter(Boolean).join(" · ") })),
      el("div", { class: "vehhead-actions" },
        el("button", { class: "btn primary", type: "button", text: "+ บันทึกบิลของรถคันนี้", onclick: () => billForVehicle(v) }),
        el("button", { class: "btn", type: "button", text: "+ เพิ่มงานซ่อม", onclick: () => requestForVehicle(v) }))),
    el("div", { class: "vstats" },
      stat("ซ่อมล่าสุด", v.last_service_date ? `${beDate(v.last_service_date)} · ${ago(v.days_since_service)}` : "ยังไม่มีประวัติ"),
      stat("จำนวนครั้งที่ซ่อม", `${v.repairs_total} ครั้ง`),
      stat("ค่าซ่อมรวม", `${baht(v.spend_total)} ฿`),
      stat("ไมล์ล่าสุดที่บันทึก", v.last_known_mileage ? `${baht(v.last_known_mileage)} กม.${v.next_service_mileage ? ` → ${baht(v.next_service_mileage)}` : ""}` : "–")),
    open.length ? el("div", { class: "vehopen" },
      el("div", { class: "vstat-label", text: "งานที่ยังค้างของรถคันนี้" }),
      ...open.map((r) => el("div", { class: "vehopen-row small" },
        el("span", { class: `badge ${r.status === "awaiting_bill" ? "warn" : "urgent"}`, text: r.status === "awaiting_bill" ? "รอบิล" : "รอซ่อม" }),
        el("span", { text: ` #${r.request_no} ${r.problem}` })))) : null);
}

function billForVehicle(v) {
  const idx = state.vehicles.findIndex((x) => x.id === v.vehicle_id);
  $("vehicleSelect").value = idx >= 0 ? String(idx) : "";
  showView("capture");
}

function requestForVehicle(v) {
  showView("board");
  const idx = state.vehicles.findIndex((x) => x.id === v.vehicle_id);
  $("addVehicle").value = idx >= 0 ? String(idx) : "";
  $("addProblem").focus();
}

// ------------------------------------------------------------ event list
const srcLabel = { bill: "บิล", manual: "บันทึกเอง" };

function renderEvents(events) {
  $("vehEventCount").textContent = events.length ? `(${events.length})` : "";
  if (!events.length) {
    $("vehEvents").replaceChildren(el("p", { class: "muted empty", text: "ยังไม่มีประวัติการซ่อมของคันนี้ — บันทึกบิลแรกได้จากปุ่มด้านบน" }));
    return;
  }
  $("vehEvents").replaceChildren(...events.map(evRow));
}

function evRow(e) {
  const d = beDate(e.event_date).split(" ");              // ["20", "ต.ค.", "2569"]
  const who = e.vendor || e.performed_by || "ไม่ระบุร้าน";
  const meta = [`${e.line_count} รายการ`, e.file_count ? `${e.file_count} ไฟล์` : null, e.mileage ? `ไมล์ ${baht(e.mileage)}` : null].filter(Boolean).join(" · ");
  return el("button", { class: "evrow", type: "button", onclick: () => goEvent(e.event_id) },
    el("div", { class: "evdate" }, el("div", { class: "evday", text: d[0] }), el("div", { class: "evmon", text: `${d[1]} ${d[2]}` })),
    el("div", { class: "evmain" },
      el("div", { class: "evtitle" }, el("strong", { text: who }),
        el("span", { class: `badge${e.source === "manual" ? " nobill" : ""}`, text: srcLabel[e.source] || e.source }),
        e.needs_review ? el("span", { class: "badge warn", text: "ควรตรวจ" }) : null),
      el("div", { class: e.summary ? "evsummary" : "evpreview", text: e.summary || e.preview || "(ไม่มีรายการ)" }),
      el("div", { class: "muted small", text: meta })),
    el("div", { class: "evtotal", text: e.total_cost ? `${baht(e.total_cost)} ฿` : "–" }),
    el("span", { class: "evchev", text: "›", "aria-hidden": "true" }));
}

// ------------------------------------------------------------- event "bill"
async function openEvent(id) {
  showView("event");
  showErr("eventError", "");
  $("evPaper").replaceChildren(el("p", { class: "muted", text: "กำลังโหลด…" }));
  try {
    const d = await api(`/api/events/${id}`);
    fleet.event = d;
    renderPaper(d);
  } catch (err) {
    $("evPaper").replaceChildren();
    showErr("eventError", err.status === 404 ? "ไม่พบรายการนี้" : `โหลดรายการไม่สำเร็จ: ${err.message}`);
  }
}

const qtyText = (q) => (q === null || q === undefined ? "" : Number(q).toLocaleString("th-TH", { maximumFractionDigits: 3 }));
const moneyText = (n) => (n === null || n === undefined ? "" : fmtMoney(n));

function renderPaper(d) {
  const e = d.event, v = d.vehicle;
  const lineSum = d.lines.reduce((s, l) => s + Number(l.line_total || 0), 0);
  const total = Number(e.total_cost || 0);
  const kv = (k, val) => (val ? el("div", { class: "kv" }, el("span", { class: "kv-k", text: k }), el("span", { class: "kv-v", text: val })) : null);
  const ymd = /^(\d{4})/.exec(e.event_date);

  const table = el("table", { class: "ro" },
    el("thead", {}, el("tr", {}, ...["#", "รายการ", "จำนวน", "หน่วย", "ราคา/หน่วย", "รวม", "หมวด"].map((t, i) => el("th", { class: i >= 2 && i <= 5 ? "r" : "", text: t })))),
    el("tbody", {}, ...d.lines.map((l) => el("tr", { class: l.was_low_confidence ? "lowconf" : "" },
      el("td", { class: "n", text: String(l.line_number) }),
      el("td", { class: "d", text: l.description }),
      el("td", { class: "r", text: qtyText(l.quantity) }),
      el("td", { text: l.unit || "" }),
      el("td", { class: "r", text: moneyText(l.unit_cost) }),
      el("td", { class: "r", text: moneyText(l.line_total) }),
      el("td", {}, l.category ? el("span", { class: "catchip", text: l.category }) : null)))));

  const files = d.attachments.map((a) => a.mime_type === "application/pdf"
    ? el("a", { class: "filecard", href: a.url, target: "_blank", rel: "noopener" }, el("span", { class: "pdfico", text: "PDF" }), el("span", { class: "small", text: a.type === "bill" ? "บิล (เปิด)" : "ไฟล์ (เปิด)" }))
    : el("a", { class: "filethumb", href: a.url, target: "_blank", rel: "noopener", title: "เปิดรูปเต็ม" },
        el("img", { src: a.url, alt: a.type === "bill" ? "รูปบิล" : "รูปอื่นๆ", loading: "lazy" }),
        el("span", { class: "filetag", text: a.type === "bill" ? "บิล" : "รูปอื่นๆ" })));

  fleet.editing = false;
  $("evEdit").disabled = false;
  fill($("evPaper"),
    (fleet.paperCard = el("div", { class: "card paper" },
      el("div", { class: "paper-head" },
        el("div", {},
          el("div", { class: "paper-kind muted small", text: e.source === "manual" ? "บันทึกการซ่อม (กรอกเอง)" : "บิล / ใบเสร็จ" }),
          el("h2", { class: "paper-shop", text: e.vendor || e.performed_by || "ไม่ระบุร้าน" }),
          e.summary ? el("div", { class: "paper-summary", text: e.summary }) : null),
        el("div", { class: "paper-date" }, el("div", { class: "paper-date-be", text: beDate(e.event_date) }),
          el("div", { class: "muted small", text: `พ.ศ. · ค.ศ. ${ymd ? ymd[1] : ""}` }))),
      el("div", { class: "paper-meta" },
        el("div", { class: "kv" }, el("span", { class: "kv-k", text: "รถ" }),
          el("a", { class: "kv-v link", href: `#vehicle=${v.vehicle_id}`, text: [vehTitle(v), v.branch, `${v.make} ${v.model}`.trim()].filter(Boolean).join(" · ") })),
        kv("เลขไมล์", e.mileage ? `${baht(e.mileage)} กม.` : ""),
        kv("ไมล์ครั้งต่อไป", e.next_service_mileage ? `${baht(e.next_service_mileage)} กม.` : ""),
        kv("ผู้ซ่อม", e.performed_by),
        kv("สาขา", e.branch)),
      el("div", { class: "tablewrap" }, table),
      el("div", { class: "paper-total" },
        el("span", { text: "รวมทั้งสิ้น" }), el("strong", { text: `${fmtMoney(total)} ฿` })),
      Math.abs(lineSum - total) > 1 && d.lines.length
        ? el("p", { class: "muted small paper-note", text: `ผลรวมของรายการคือ ${fmtMoney(lineSum)} ต่างจากยอดรวมที่บันทึก (อาจมี VAT/ส่วนลด)` }) : null,
      e.notes ? el("div", { class: "paper-notes" }, el("div", { class: "kv-k", text: "หมายเหตุ" }), el("div", { text: e.notes })) : null)),
    d.requests.length ? el("div", { class: "card" },
      el("h2", { text: "งานซ่อมที่บิลนี้ปิด" }),
      ...d.requests.map((r) => el("div", { class: "small", text: `#${r.seq} ${r.problem}${r.reported_by ? ` · ผู้แจ้ง ${r.reported_by}` : ""}` }))) : null,
    files.length ? el("div", { class: "card" }, el("h2", { text: `ไฟล์แนบ (${files.length})` }), el("div", { class: "files" }, ...files)) : null,
    el("p", { class: "muted small", text: `บันทึกเมื่อ ${beDateOf(new Date(e.confirmed_at || e.created_at))}${e.extraction_model ? " · อ่านบิลโดย AI แล้วตรวจสอบโดยคน" : ""}` }));
}


// ------------------------------------------------------------ editing a saved bill
// The "แก้ไข" button turns the bill into a form: header fields, the truck, a short written
// summary, and the line items (the same row as the review page, with the same AI
// category re-check when a name is corrected). Saving replaces the lines in one database
// transaction (PATCH /api/events/{id} -> update_maintenance_event in schema_v4.sql).
// Photos and the requests the bill closed are not touched.
const editRows = () => [...document.querySelectorAll("#editLines tbody tr")];

function editLineSum() {
  return editRows().reduce((s, tr) => s + num(tr.querySelector("[data-f=line_total]").value), 0);
}

// A line was changed: the header total follows the lines (unless the person typed their own total).
function editTotalsChanged() {
  if (!fleet.totalManual) {
    const t = $("edTotal");
    t.value = String(Math.round(editLineSum() * 100) / 100);
  }
  editTotalsBar();
}

// Only redraws the "sum of lines / total" strip. Used when the form first opens, so the SAVED
// total is shown untouched until something is actually edited.
function editTotalsBar() {
  const sum = Math.round(editLineSum() * 100) / 100;
  const header = Math.round(num($("edTotal").value) * 100) / 100;
  const box = $("edTotals");
  box.replaceChildren(el("span", {}, "ผลรวมรายการ ", el("span", { class: "big", text: fmtMoney(sum) })));
  if (Math.abs(header - sum) > 0.005) {
    box.append(el("span", {}, "ยอดรวมที่ตั้งไว้ ", el("span", { class: "big", text: fmtMoney(header) })),
      el("button", { class: "btn small", type: "button", text: "ตั้งยอดรวม = ผลรวมรายการ", onclick: () => { fleet.totalManual = false; editTotalsChanged(); } }));
  }
}

const editHooks = { changed: () => editTotalsChanged(), renumber: () => editRows().forEach((tr, i) => { tr.querySelector(".n").textContent = String(i + 1); }) };

function startEdit() {
  const d = fleet.event;
  if (!d || fleet.editing) return;
  fleet.editing = true;
  fleet.totalManual = false;              // the total follows the lines as soon as one is changed (the saved total is shown until then)
  $("evEdit").disabled = true;
  showErr("eventError", "");
  const e = d.event;
  const input = (id, type, value, extra = {}) => el("input", { id, type, value: value ?? "", ...extra });
  const field = (label, node, wide = false) => el("label", { class: "field" + (wide ? " wide" : "") }, el("span", { text: label }), node);

  const vIdx = state.vehicles.findIndex((v) => v.id === d.vehicle.vehicle_id);
  const vSel = el("select", { id: "edVehicle" });
  fillVehicleSelect(vSel, vIdx < 0 ? "" : vIdx);

  const tbody = el("tbody", {});
  d.lines.forEach((l) => {
    const tr = lineRow({
      description: l.description, quantity: l.quantity, unit: l.unit, unit_cost: l.unit_cost, line_total: l.line_total,
      category: l.category, low_confidence_fields: l.low_confidence_fields || [],
    }, editHooks);
    tr.dataset.attachment = l.attachment_id || "";
    tr._flags = l.low_confidence_fields || [];
    tbody.append(tr);
  });
  const head = el("tr", {}, ...["#", "รายการ", "จำนวน", "หน่วย", "ราคา/หน่วย", "รวม", "หมวด", ""].map((t) => el("th", { text: t })));

  const dateInput = input("edDate", "date", e.event_date);
  const form = el("div", { class: "card paper editing" },
    el("div", { class: "edit-banner" }, el("strong", { text: "กำลังแก้ไขบิลนี้" }), el("span", { class: "muted small", text: " — กด “บันทึกการแก้ไข” ด้านล่างเมื่อเสร็จ ยังไม่บันทึกอะไรจนกว่าจะกด" })),
    el("div", { class: "grid" },
      field("รถ", vSel),
      field("วันที่ (ในบิลเป็น พ.ศ.)", el("div", {}, dateInput, el("div", { id: "edDateCap", class: "datecap" }))),
      field("ร้าน / อู่", input("edVendor", "text", e.vendor, { maxlength: "200" })),
      field("ผู้ซ่อม", input("edPerformedBy", "text", e.performed_by, { maxlength: "200" })),
      field("สาขา", input("edBranch", "text", e.branch, { maxlength: "100" })),
      field("เลขไมล์", input("edMileage", "number", e.mileage, { inputmode: "numeric" })),
      field("ไมล์ครั้งต่อไป", input("edNext", "number", e.next_service_mileage, { inputmode: "numeric" })),
      field("ยอดรวม (บาท)", input("edTotal", "number", e.total_cost, { step: "0.01", inputmode: "decimal" })),
      field("สรุปสั้นๆ ว่าซ่อมอะไร (ไม่บังคับ — แสดงในรายการประวัติ)", el("div", { class: "row" },
        input("edSummary", "text", e.summary, { maxlength: "500", placeholder: "เช่น ถ่ายน้ำมันเครื่อง + เปลี่ยนผ้าเบรกหลัง" }),
        el("button", { class: "btn", id: "edAiSummary", type: "button", text: "✨ ให้ AI เขียนให้", onclick: draftSummary })), true),
      field("หมายเหตุ", el("textarea", { id: "edNotes", text: e.notes || "" }), true)),
    el("h2", { class: "edit-h2", text: "รายการในบิล" }),
    el("div", { class: "tablewrap" }, el("table", { class: "lines", id: "editLines" }, el("thead", {}, head), tbody)),
    el("button", { class: "btn addrow", type: "button", text: "+ เพิ่มรายการ", onclick: () => { tbody.append(lineRow({}, editHooks)); editHooks.renumber(); editTotalsChanged(); } }),
    el("div", { class: "totals", id: "edTotals" }),
    el("div", { class: "actions" },
      el("button", { class: "btn", type: "button", text: "ยกเลิกการแก้ไข", onclick: () => renderPaper(fleet.event) }),
      el("button", { class: "btn primary big", id: "edSave", type: "button", text: "บันทึกการแก้ไข", onclick: saveEdit })));

  fleet.paperCard.replaceWith(form);
  fleet.paperCard = form;
  bindDateField($("edDate"), $("edDateCap"));
  $("edTotal").addEventListener("input", () => { fleet.totalManual = true; editTotalsChanged(); });
  editHooks.renumber();
  editTotalsBar();
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Drafts the summary from the lines as they are on the form right now (so it follows your edits).
// Fills the box only -- nothing is saved until "บันทึกการแก้ไข".
async function draftSummary() {
  const btn = $("edAiSummary"), box = $("edSummary");
  if (box.value.trim() && !confirm("ช่องสรุปมีข้อความอยู่แล้ว ให้ AI เขียนใหม่ทับไหม?")) return;
  const lines = readEdit().line_items.map((l) => ({ description: l.description, category: l.category, quantity: l.quantity, unit: l.unit }));
  if (!lines.length) { showErr("eventError", "ยังไม่มีรายการให้สรุป"); return; }
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "กำลังเขียน…";
  showErr("eventError", "");
  try {
    const res = await api("/api/summarize", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lines, performed_by: $("edPerformedBy").value.trim() || null }) });
    box.value = res.summary;
  } catch (err) {
    showErr("eventError", `ให้ AI สรุปไม่สำเร็จ: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

function readEdit() {
  const v = (id) => $(id).value.trim();
  const veh = selectedVehicle($("edVehicle"));
  return {
    vehicle_id: veh ? veh.id : null,
    event: {
      event_date: v("edDate") || null, vendor: v("edVendor") || null, performed_by: v("edPerformedBy") || null, branch: v("edBranch") || null,
      mileage: num(v("edMileage")) || null, next_service_mileage: num(v("edNext")) || null, total_cost: num(v("edTotal")) || null,
      notes: $("edNotes").value.trim() || null, summary: v("edSummary") || null,
    },
    line_items: editRows().flatMap((tr) => {
      const g = (n) => tr.querySelector(`[data-f=${n}]`).value.trim();
      if (!g("description") && !num(g("line_total"))) return [];                  // an untouched blank row
      return [{
        attachment_id: tr.dataset.attachment || null, description: g("description"), category: g("category") || null,
        quantity: num(g("quantity")) || null, unit: g("unit") || null, unit_cost: num(g("unit_cost")) || null, line_total: num(g("line_total")) || null,
        // lines nobody touched keep the AI's "not sure" flags; edited lines are now human-checked
        low_confidence_fields: tr.dataset.edited === "1" ? [] : tr._flags || [],
      }];
    }),
  };
}

let savingEdit = false;

async function saveEdit() {
  if (savingEdit) return;
  const body = readEdit();
  const problems = [];
  if (!body.vehicle_id) problems.push("ยังไม่ได้เลือกรถ");
  if (!body.event.event_date) problems.push("ยังไม่ได้ใส่วันที่");
  if (!body.line_items.length) problems.push("ไม่มีรายการในบิล");
  showErr("eventError", problems.join(" · "));
  if (problems.length) return;

  savingEdit = true;
  const btn = $("edSave");
  btn.disabled = true;
  btn.textContent = "กำลังบันทึก…";
  try {
    await api(`/api/events/${fleet.event.event.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    fleet.loaded = false;                      // the truck's totals / last-repair date just changed
    await openEvent(fleet.event.event.id);     // show the saved bill again, as stored
    showErr("eventError", "");
  } catch (err) {
    showErr("eventError", err.status === 503 || err.status === 422 || err.status === 404 ? err.message : `บันทึกการแก้ไขไม่สำเร็จ: ${err.message}`);
    btn.disabled = false;
    btn.textContent = "บันทึกการแก้ไข";
  } finally {
    savingEdit = false;
  }
}

// --------------------------------------------------------------------- init
function initFleet() {
  $("vehSearch").addEventListener("input", renderVehicles);
  $("vehBranch").addEventListener("change", renderVehicles);
  $("vehSort").addEventListener("change", renderVehicles);
  $("vehSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const first = $("vehList").querySelector(".veh"); if (first) first.click(); }
  });
  $("vehBack").addEventListener("click", goSearch);
  $("evEdit").addEventListener("click", startEdit);
  $("evBack").addEventListener("click", () => (fleet.event ? goVehicle(fleet.event.vehicle.vehicle_id) : goSearch()));
  window.addEventListener("hashchange", route);
}
