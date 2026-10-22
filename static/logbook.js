"use strict";

// สมุดงานซ่อม: every repair job in one list you can filter. The filtering itself happens on
// the server (app/logbook.py, unit-tested); this file collects the filters, asks /api/log,
// and draws the answer. Rows are either a saved repair (a bill or a manual entry: opens the
// bill page) or a request that has no record yet (opens its timeline).
//
// Loaded BEFORE app.js: nothing here runs at load time; helpers from the other scripts are
// used at call time only.

const lg = { f: {}, rows: [], total: 0, facets: null, stats: null, loaded: false, seq: 0, timer: null, limit: 50 };

const LOG_STATUS_BADGE = { done: "ok", done_no_bill: "nobill", awaiting_bill: "warn", at_shop: "shop", open: "urgent", cancelled: "" };
const LOG_STORE = "vt_logfilters";

const FILTER_KEYS = ["q", "date_from", "date_to", "status", "shop", "branch", "category", "vehicle_id", "review", "sort"];

// ------------------------------------------------------------------ filters <-> form
function readFilters() {
  const v = (id) => $(id).value.trim();
  return {
    q: v("lgQ"), date_from: v("lgFrom"), date_to: v("lgTo"), status: v("lgStatus") || "all", shop: v("lgShop"), branch: v("lgBranch"),
    category: v("lgCategory"), vehicle_id: v("lgVehicle"),
    review: $("lgReview").checked ? "1" : "", sort: v("lgSort") || "date_desc",
  };
}

function writeFilters(f) {
  const set = (id, val) => { $(id).value = val || ""; };
  set("lgQ", f.q); set("lgFrom", f.date_from); set("lgTo", f.date_to); set("lgStatus", f.status || "all"); set("lgShop", f.shop);
  set("lgBranch", f.branch); set("lgCategory", f.category);
  if (f.vehicle_id) ensureVehicleOption(f.vehicle_id);        // the list of trucks arrives with the first answer; this one is needed now
  set("lgVehicle", f.vehicle_id);
  $("lgReview").checked = !!f.review;
  set("lgSort", f.sort || "date_desc");
  $("lgFromCap").textContent = dateCaption($("lgFrom").value);
  $("lgToCap").textContent = dateCaption($("lgTo").value);
}

const isDefault = (f) => FILTER_KEYS.every((k) => !f[k] || (k === "status" && f[k] === "all") || (k === "sort" && f[k] === "date_desc"));

function queryString(f, extra = {}) {
  const p = new URLSearchParams();
  for (const k of FILTER_KEYS) if (f[k]) p.set(k, f[k]);
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

// ---------------------------------------------------------------------- loading
async function openLog() {
  showView("log");
  if (!lg.loaded) {
    let saved = {};
    try { saved = JSON.parse(sessionStorage.getItem(LOG_STORE) || "{}"); } catch (_) { /* ignore */ }
    lg.f = saved;
    writeFilters(lg.f);
  }
  await loadLog(true);
  if (!lg.loaded) { lg.loaded = true; }
}

async function loadLog(reset) {
  const mySeq = ++lg.seq;
  if (reset) lg.rows = [];
  lg.f = readFilters();
  try { sessionStorage.setItem(LOG_STORE, JSON.stringify(lg.f)); } catch (_) { /* ignore */ }
  $("lgError").hidden = true;
  $("lgMore").disabled = true;
  try {
    const res = await api(`/api/log?${queryString(lg.f, { limit: lg.limit, offset: lg.rows.length })}`);
    if (mySeq !== lg.seq) return;                       // a newer filter change already took over
    lg.rows = lg.rows.concat(res.rows);
    lg.total = res.total;
    lg.stats = res.stats;
    lg.facets = res.facets;
    fillFacets(res.facets);
    updateStatusCounts(res.facets);
    renderLog();
    renderTruckHeader();
  } catch (err) {
    if (mySeq !== lg.seq) return;
    $("lgError").textContent = `โหลดสมุดงานซ่อมไม่สำเร็จ: ${err.message}`;
    $("lgError").hidden = false;
  }
}

function scheduleLoad(delay = 250) {
  clearTimeout(lg.timer);
  lg.timer = setTimeout(() => loadLog(true), delay);
}

// ------------------------------------------------------------------ drop-downs
function fillFacets(fc) {
  const opts = (sel, items, label) => {
    const cur = sel.value;
    sel.replaceChildren(el("option", { value: "", text: label }), ...items.map((i) => el("option", { value: i.value, text: `${i.value} (${i.count})` })));
    sel.value = cur;
  };
  opts($("lgShop"), fc.shops, "ทุกร้าน / ช่าง");
  opts($("lgBranch"), fc.branches, "ทุกสาขา");
  opts($("lgCategory"), fc.categories, "ทุกหมวด");
  fillTruckOptions(fc.vehicles);
}

// ---- the truck picker: grouped by branch, only trucks that have history (plus the one currently chosen)
const truckOptionText = (t, count) => `${truckLabel({ truck_number: t.truck_number, plate: t.plate })}${count === undefined ? "" : ` (${count})`}`;

function fillTruckOptions(trucks) {
  const sel = $("lgVehicle"), cur = sel.value;
  const items = [...trucks];
  if (cur && !items.some((t) => t.value === cur)) {            // chosen from the roster but it has no jobs yet
    const v = (fleet.vehicles || []).find((x) => x.vehicle_id === cur);
    if (v) items.push({ value: cur, plate: v.plate, truck_number: v.truck_number, branch: v.branch, count: 0 });
  }
  const groups = new Map();
  for (const t of items) {
    if (!groups.has(t.branch)) groups.set(t.branch, []);
    groups.get(t.branch).push(t);
  }
  sel.replaceChildren(el("option", { value: "", text: "ทุกคัน" }),
    ...[...groups.entries()].map(([branch, ts]) => el("optgroup", { label: branch ? `สาขา${branch}` : "ไม่ระบุสาขา" },
      ...ts.map((t) => el("option", { value: t.value, text: truckOptionText(t, t.count) })))));
  sel.value = cur;
}

function ensureVehicleOption(id) {
  const sel = $("lgVehicle");
  if ([...sel.options].some((o) => o.value === id)) return;
  const v = (fleet.vehicles || []).find((x) => x.vehicle_id === id);
  sel.append(el("option", { value: id, text: v ? truckOptionText({ truck_number: v.truck_number, plate: v.plate }) : "รถที่เลือก" }));
}

// Open the log showing one truck's whole history (from the roster, from a bill's back button ...).
function showTruckLog(vehicleId) {
  writeFilters({ vehicle_id: vehicleId });
  fleet.backTo = null;
  lg.loaded = true;                       // the form already holds what we want; don't restore the old filters over it
  goHash("#log");
}

const ago = (days) => (days === 0 ? "วันนี้" : `${days} วันก่อน`);

// The card above the list when one truck is chosen: who it is, what it is for, its driver, lifetime totals, shortcuts.
async function renderTruckHeader() {
  const box = $("lgTruck"), id = lg.f.vehicle_id;
  if (!id) { box.hidden = true; return; }
  await ensureVehicles();
  const v = fleet.vehicles.find((x) => x.vehicle_id === id);
  if (!v || lg.f.vehicle_id !== id) { box.hidden = true; return; }
  const stat = (label, value) => el("div", { class: "vstat" }, el("div", { class: "vstat-label", text: label }), el("div", { class: "vstat-value", text: value }));
  box.hidden = false;
  fill(box,
    el("div", { class: "lgtruck-top" },
      el("div", {},
        el("div", { class: "req-head" }, ...truckHead({ truck_number: v.truck_number, plate: v.plate }),
          v.branch ? el("span", { class: "muted", text: v.branch }) : null,
          v.usage ? el("span", { class: "badge shop", text: v.usage }) : null,
          v.active === false ? el("span", { class: "badge", text: "เลิกใช้แล้ว" }) : null),
        el("div", { class: "muted", text: [`${v.make} ${v.model}`.trim(), v.province, v.driver ? `คนขับหลัก ${v.driver}` : null].filter(Boolean).join(" · ") })),
      el("div", { class: "lgtruck-actions" },
        el("button", { class: "btn small primary", type: "button", text: "+ บันทึกบิล", onclick: () => billForVehicle(v) }),
        el("button", { class: "btn small", type: "button", text: "+ แจ้งซ่อม", onclick: () => requestForVehicle(v) }),
        el("button", { class: "btn small", type: "button", text: "ข้อมูลรถทั้งหมด", onclick: () => goTruck(v.vehicle_id) }))),
    el("div", { class: "vstats" },
      stat("ซ่อมล่าสุด", v.last_service_date ? `${beDate(v.last_service_date)} · ${ago(v.days_since_service)}` : "ยังไม่มีประวัติ"),
      stat("ซ่อมทั้งหมด", `${v.repairs_total} ครั้ง`),
      stat("ค่าซ่อมรวม", `${baht(v.spend_total)} ฿`),
      stat("ไมล์ล่าสุดที่บันทึก", v.last_known_mileage ? `${baht(v.last_known_mileage)} กม.${v.next_service_mileage ? ` → ${baht(v.next_service_mileage)}` : ""}` : "–")));
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
  $("addVehicle").dispatchEvent(new Event("change"));        // fills in the truck's main driver as the reporter
  $("addProblem").focus();
}

function updateStatusCounts(fc) {
  const byValue = Object.fromEntries(fc.statuses.map((s) => [s.value, s]));
  const active = ["open", "at_shop", "awaiting_bill"].reduce((n, k) => n + (byValue[k]?.count || 0), 0);
  const done = (byValue.done?.count || 0) + (byValue.done_no_bill?.count || 0);
  const cur = $("lgStatus").value || "all";
  const all = fc.statuses.reduce((n, s) => n + s.count, 0);
  const items = [["all", `ทั้งหมด (${all})`], ["active", `ยังทำอยู่ (${active})`], ["open", `รอซ่อม (${byValue.open?.count || 0})`],
    ["at_shop", `อยู่ที่อู่ (${byValue.at_shop?.count || 0})`], ["awaiting_bill", `รอบิล (${byValue.awaiting_bill?.count || 0})`],
    ["done", `เสร็จแล้ว (${done})`], ["cancelled", `ยกเลิก (${byValue.cancelled?.count || 0})`]];
  $("lgStatus").replaceChildren(...items.map(([v, t]) => el("option", { value: v, text: t })));
  $("lgStatus").value = cur;
}

// ------------------------------------------------------------------- quick dates
function quickRange(kind) {
  const today = new Date();
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  let from = "";
  if (kind === "30") from = iso(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 30));
  if (kind === "90") from = iso(new Date(today.getFullYear(), today.getMonth() - 3, today.getDate()));
  if (kind === "month") from = iso(new Date(today.getFullYear(), today.getMonth(), 1));
  if (kind === "year") from = iso(new Date(today.getFullYear(), 0, 1));
  $("lgFrom").value = from;
  $("lgTo").value = "";
  $("lgFromCap").textContent = dateCaption(from);
  $("lgToCap").textContent = "";
  scheduleLoad(0);
}

// ---------------------------------------------------------------------- results
const daysAgo = (iso) => (iso ? daysSince(iso) : null);

function stageLine(r) {
  if (r.status === "at_shop") {
    const d = daysAgo(r.sent_date);
    const late = r.expected_return && r.expected_return < todayISO() ? ` · เลยกำหนด ${daysSince(r.expected_return)} วัน` : "";
    return `ส่ง${r.shop_raw ? ` ${r.shop_raw}` : ""} ${r.sent_date ? beDate(r.sent_date) : ""}${d !== null ? ` · อยู่ที่อู่ ${d} วัน` : ""}${late}`;
  }
  if (r.status === "open") return `แจ้งเมื่อ ${beDate(r.request_date)} · รอมา ${daysSince(r.request_date)} วัน${r.reported_by ? ` · ผู้แจ้ง ${r.reported_by}` : ""}`;
  if (r.status === "awaiting_bill") return `กลับมา/ซ่อมเสร็จ ${beDate(r.fixed_date)}${r.shop_raw ? ` · ${r.shop_raw}` : ""} · รอบิลมา ${daysSince(r.fixed_date)} วัน`;
  if (r.status === "done_no_bill") return `ซ่อมเสร็จ ${beDate(r.fixed_date)}${r.shop_raw ? ` · ${r.shop_raw}` : ""}${r.no_bill_reason ? ` · ${r.no_bill_reason}` : " · ไม่มีบิล"}`;
  if (r.status === "cancelled") return `ยกเลิก · แจ้งเมื่อ ${beDate(r.request_date)}`;
  return "";
}

function logRow(r) {
  const d = beDate(r.date).split(" ");
  const truck = truckLabel(r);
  const isEvent = r.kind === "event";
  const what = isEvent ? (r.summary || r.preview || "(ไม่มีรายการ)") : r.problem;
  const meta = isEvent
    ? [r.source === "manual" ? "ช่างทำเอง" : "บิล", `${r.line_count} รายการ`, r.file_count ? `${r.file_count} ไฟล์` : null, r.mileage ? `ไมล์ ${baht(r.mileage)}` : null,
       r.categories.length ? r.categories.slice(0, 3).join(" · ") + (r.categories.length > 3 ? ` +${r.categories.length - 3}` : "") : null]
    : [stageLine(r)];
  const who = r.shop || r.shop_raw;
  return el("button", { class: `logrow ${r.status}`, type: "button", onclick: () => openLogRow(r) },
    el("div", { class: "evdate" }, el("div", { class: "evday", text: d[0] || "–" }), el("div", { class: "evmon", text: d.length > 2 ? `${d[1]} ${d[2]}` : "" })),
    el("div", { class: "evmain" },
      el("div", { class: "evtitle" },
        el("strong", { text: truck }),
        r.branch ? el("span", { class: "muted small", text: r.branch }) : null,
        who ? el("span", { class: "badge shop", text: who }) : null,
        el("span", { class: `badge ${LOG_STATUS_BADGE[r.status]}`.trim(), text: r.status_label }),
        r.urgent ? el("span", { class: "badge urgent", text: "ด่วน" }) : null,
        r.needs_review ? el("span", { class: "badge warn", text: "ควรตรวจ", title: r.review_reasons.join("\n") }) : null),
      el("div", { class: isEvent && r.summary ? "evsummary" : "evpreview", text: what }),
      el("div", { class: "muted small", text: meta.filter(Boolean).join(" · ") }),
      r.from_requests && r.from_requests.length ? el("div", { class: "muted small", text: `จากงานแจ้งซ่อม: ${r.from_requests.map((q) => q.problem).join(" · ")}` }) : null),
    el("div", { class: "evtotal", text: r.total_cost ? `${baht(r.total_cost)} ฿` : "–" }));
}

function openLogRow(r) {
  if (r.kind === "event") { fleet.backTo = "#log"; goEvent(r.id); }
  else showRequestDialog(r);
}

function renderLog() {
  const s = lg.stats;
  const parts = [`พบ ${lg.total} งาน`];
  if (s.total_cost) parts.push(`ค่าซ่อมรวม ${baht(s.total_cost)} ฿`);
  if (s.trucks) parts.push(`${s.trucks} คัน`);
  $("lgSummary").replaceChildren(el("strong", { text: parts.join(" · ") }),
    ...Object.entries(s.by_status).map(([k, n]) => el("span", { class: `badge ${LOG_STATUS_BADGE[k]}`.trim(), text: `${logStatusLabel(k)} ${n}` })));
  $("lgClear").hidden = isDefault(lg.f);
  $("lgList").replaceChildren(...(lg.rows.length ? lg.rows.map(logRow)
    : [el("p", { class: "muted empty", text: isDefault(lg.f) ? "ยังไม่มีงานซ่อมในระบบ" : "ไม่พบงานที่ตรงกับตัวกรอง ลองกด “ล้างตัวกรอง” หรือค้นด้วยคำที่สั้นลง" })]));
  const left = lg.total - lg.rows.length;
  $("lgMore").hidden = left <= 0;
  $("lgMore").disabled = false;
  $("lgMore").textContent = `แสดงเพิ่ม (อีก ${left} งาน)`;
}

const logStatusLabel = (k) => ({ done: "เสร็จแล้ว", done_no_bill: "เสร็จ (ไม่มีบิล)", awaiting_bill: "รอบิล", at_shop: "อยู่ที่อู่", open: "รอซ่อม", cancelled: "ยกเลิก" }[k] || k);

// ------------------------------------------------------------------ CSV download
async function downloadLog() {
  const btn = $("lgCsv");
  btn.disabled = true;
  try {
    const res = await fetch(`/api/log.csv?${queryString(readFilters())}`, { headers: { "X-App-Token": state.token } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: `สมุดงานซ่อม-${todayISO()}.csv` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) {
    $("lgError").textContent = `ดาวน์โหลดไม่สำเร็จ: ${err.message}`;
    $("lgError").hidden = false;
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------- one request's timeline
// What happened to a repair that has no bill/record (or hasn't yet): reported -> sent -> back -> bill.
// Also used by the repair board.
function requestSteps(r) {
  const steps = [];
  const mk = (done, title, when, detail) => steps.push({ done, title, when, detail });
  mk(true, "แจ้งซ่อม", r.request_date, [r.reported_by ? `ผู้แจ้ง ${r.reported_by}` : null, r.urgent ? "ด่วน" : null].filter(Boolean).join(" · "));
  const sent = !!r.sent_date;
  mk(sent, "ส่งซ่อม", r.sent_date, sent ? [r.shop_raw || r.shop, r.expected_return ? `คาดว่ากลับ ${beDate(r.expected_return)}` : null].filter(Boolean).join(" · ") : "ยังไม่ได้ส่ง (หรือซ่อมเอง)");
  const back = !!r.fixed_date;
  const days = sent && back ? Math.round((new Date(r.fixed_date) - new Date(r.sent_date)) / 86400000) : null;
  mk(back, "กลับมา / ซ่อมเสร็จ", r.fixed_date, back ? [r.shop_raw && !sent ? r.shop_raw : null, days !== null ? `อยู่ที่อู่ ${days} วัน` : null].filter(Boolean).join(" · ") : (r.status === "at_shop" ? `อยู่ที่อู่มา ${daysSince(r.sent_date)} วัน` : ""));
  const billDone = r.status === "done_no_bill" || r.kind === "event";
  const billText = r.status === "done_no_bill" ? (r.no_bill_reason ? `ปิดโดยไม่มีบิล — ${r.no_bill_reason}` : "ปิดโดยไม่มีบิล") : r.status === "awaiting_bill" ? `รอบิลมา ${daysSince(r.fixed_date)} วัน` : "";
  mk(billDone, "บิล / บันทึก", null, billText);
  return steps;
}

function showRequestDialog(r) {
  const dlg = $("reqDialog");
  const truck = truckLabel(r);
  $("reqDlgTitle").textContent = `งานซ่อม · ${truck}`;
  $("reqDlgProblem").textContent = r.problem || r.preview || "";
  const steps = requestSteps(r);
  $("reqDlgSteps").replaceChildren(...steps.map((s) => el("li", { class: s.done ? "done" : "" },
    el("div", { class: "tl-dot", "aria-hidden": "true" }),
    el("div", {}, el("strong", { text: s.title }), s.when ? el("span", { class: "muted small", text: ` · ${beDate(s.when)}` }) : null,
      s.detail ? el("div", { class: "muted small", text: s.detail }) : null))));
  $("reqDlgStatus").replaceChildren(el("span", { class: `badge ${LOG_STATUS_BADGE[r.status] || ""}`.trim(), text: r.status_label || logStatusLabel(r.status) }));
  dlg.showModal();
}

// ------------------------------------------------------------------------ init
function initLog() {
  const reload = () => scheduleLoad(0);
  if (isNarrow()) $("lgMore2").open = false;           // on a phone the extra filters start folded away
  $("lgQ").addEventListener("input", () => scheduleLoad(300));
  for (const id of ["lgVehicle", "lgStatus", "lgShop", "lgBranch", "lgCategory", "lgSort", "lgReview"]) $(id).addEventListener("change", reload);
  bindDateField($("lgFrom"), $("lgFromCap"));
  bindDateField($("lgTo"), $("lgToCap"));
  $("lgFrom").addEventListener("change", reload);
  $("lgTo").addEventListener("change", reload);
  document.querySelectorAll("#lgQuick [data-range]").forEach((b) => b.addEventListener("click", () => quickRange(b.dataset.range)));
  $("lgClear").addEventListener("click", () => { writeFilters({}); scheduleLoad(0); });
  $("lgMore").addEventListener("click", () => loadLog(false));
  $("lgCsv").addEventListener("click", downloadLog);
  $("reqDlgClose").addEventListener("click", () => $("reqDialog").close());
  $("reqDlgBoard").addEventListener("click", () => { $("reqDialog").close(); showView("board"); });
}
