"use strict";

// เครื่องจักร: the machines, an index of what is on them (motors, pumps, belts, bearings, breakers ... with
// their specs), and the maintenance reports.
//   #machinery      the index: the machines as cards + the searchable parts / specs list
//   #machine=<id>   one machine: its details (view / edit), its parts, its latest reports
//   #machine-log    the maintenance reports: filter, add, correct
// Lists are read-only; a row opens a form (parts, reports) or a page (machines) where changes are made and saved
// on purpose. Data: /api/machines, /api/machine-parts, /api/machine-events (app/machinery.py).
// Loaded BEFORE app.js: nothing here runs at load time.

const mc = {
  opts: { kinds: [], tube_types: [], event_kinds: [], part_statuses: [], ready: true },
  machines: [], parts: { rows: [], total: 0, facets: { locations: [], parts: [] } },
  events: { rows: [], total: 0, stats: { count: 0, total_cost: 0 } }, evLimit: 50,
  current: null, editing: false, draft: {}, dirty: false,
  partTarget: null, partDraft: {}, evTarget: null, evDraft: {},
};

const machineSub = (m) => [m.tube_type, [m.make, m.model].filter(Boolean).join(" "), m.capacity_tons ? `${m.capacity_tons} ตัน` : null].filter(Boolean).join(" · ");
const goMachine = (id) => goHash(`#machine=${id}`);
const machineById = (id) => mc.machines.find((m) => m.id === id) || null;
const STATUS_CLASS = { Good: "ok", New: "shop", Adjust: "warn", "?": "" };

async function loadMachineryBase() {
  try {
    const [opts, machines] = await Promise.all([api("/api/machinery/options"), api("/api/machines?include_inactive=true")]);
    mc.opts = opts;
    mc.machines = machines;
    return true;
  } catch (err) {
    return err.message;
  }
}

const needsV9 = () => mc.opts.ready === false;

function showReady(bannerId) {
  const b = $(bannerId);
  if (b) b.hidden = !needsV9();
}

// ================================================================== the index (#machinery)
async function openMachinery() {
  showView("machinery");
  showErr("mcError", "");
  const ok = await loadMachineryBase();
  if (ok !== true) { showErr("mcError", `โหลดข้อมูลเครื่องจักรไม่สำเร็จ: ${ok}`); return; }
  showReady("mcNeedsV9");
  renderMachineCards();
  await loadParts();
}

function renderMachineCards() {
  const live = mc.machines.filter((m) => m.active !== false);
  fill($("mcCards"), ...(live.length ? live.map(machineCard) : [el("p", { class: "muted empty", text: needsV9() ? "" : "ยังไม่มีเครื่อง — กด “+ เพิ่มเครื่อง”" })]));
}

function machineCard(m) {
  return el("a", { class: "mccard", href: `#machine=${m.id}` },
    el("div", { class: "mc-name", text: m.name }),
    el("div", { class: "muted small", text: machineSub(m) || m.branch }),
    el("div", { class: "mc-counts small" },
      el("span", { text: `${m.part_count} รายการอะไหล่` }), el("span", { text: `${m.event_count} งานซ่อม` }),
      m.last_event_date ? el("span", { class: "muted", text: `ล่าสุด ${beDate(m.last_event_date)}` }) : null));
}

// ---- parts / specs list
function partQuery(extra = {}) {
  const f = { q: $("mpQ").value.trim(), location: $("mpLocation").value, part: $("mpPart").value, ...extra };
  const p = new URLSearchParams({ limit: "500" });
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
  return p.toString();
}

let partsTimer = null;
const scheduleParts = (delay = 250) => { clearTimeout(partsTimer); partsTimer = setTimeout(loadParts, delay); };

async function loadParts() {
  try {
    mc.parts = await api(`/api/machine-parts?${partQuery()}`);
  } catch (err) {
    showErr("mcError", `โหลดรายการอะไหล่ไม่สำเร็จ: ${err.message}`);
    return;
  }
  const keepL = $("mpLocation").value, keepP = $("mpPart").value;
  $("mpLocation").replaceChildren(el("option", { value: "", text: "ทุกที่" }), ...mc.parts.facets.locations.map((f) => el("option", { value: f.value, text: `${f.value} (${f.count})` })));
  $("mpPart").replaceChildren(el("option", { value: "", text: "ทุกชิ้นส่วน" }), ...mc.parts.facets.parts.map((f) => el("option", { value: f.value, text: `${f.value} (${f.count})` })));
  $("mpLocation").value = keepL;
  $("mpPart").value = keepP;
  fill($("mpCount"), `พบ ${mc.parts.total} รายการ${mc.parts.total > mc.parts.rows.length ? ` (แสดง ${mc.parts.rows.length} รายการแรก — ค้นให้แคบลง)` : ""}`);
  fill($("mpList"), ...partRows(mc.parts.rows, { emptyText: "ไม่พบรายการที่ตรงกับตัวกรอง" }));
}

// one row per part; the electrical details (hp, amps, breaker, contactor, overload) sit under the spec
function partRows(rows, { emptyText, showLocation = true } = {}) {
  if (!rows.length) return [el("p", { class: "muted empty", text: emptyText || "ยังไม่มีรายการ" })];
  return rows.map((p) => {
    const electric = [p.motor_hp ? `${p.motor_hp} hp` : null, p.delta_a ? `Δ ${p.delta_a} A` : null, p.star_a ? `★ ${p.star_a} A` : null,
      p.breaker ? `Breaker: ${p.breaker.replace(/\n/g, " / ")}` : null, p.contactor ? `Contactor: ${p.contactor.replace(/\n/g, " / ")}` : null,
      p.overload ? `Overload: ${p.overload.replace(/\n/g, " / ")}` : null].filter(Boolean).join(" · ");
    return el("button", { class: "mprow", type: "button", onclick: () => openPartDialog(p) },
      showLocation ? el("div", { class: "mp-loc", text: p.location }) : null,
      el("div", { class: "mp-main" },
        el("div", { class: "mp-title" }, el("strong", { text: p.equipment || p.part || "–" }), p.equipment && p.part ? el("span", { class: "badge", text: p.part }) : null,
          p.status ? el("span", { class: `badge ${STATUS_CLASS[p.status] || ""}`.trim(), text: p.status }) : null),
        p.spec ? el("div", { class: "mp-spec", text: p.spec }) : null,
        electric ? el("div", { class: "muted small", text: electric }) : null,
        p.notes ? el("div", { class: "muted small", text: p.notes }) : null),
      el("div", { class: "mp-qty", text: p.qty ? `${p.qty} ${p.unit || ""}`.trim() : (p.unit || "") }));
  });
}

// ================================================================== a part (form in a dialog)
const PART_FIELDS = [
  ["location", "สถานที่ / เครื่อง", { list: "mpLocationList", placeholder: "เช่น เครื่อง 1, โรงซอง 1, ระบบน้ำ", wide: true }],
  ["equipment", "อุปกรณ์", { placeholder: "เช่น ปั๊มน้ำเย็น, พัดลม, โม่" }],
  ["part", "ชิ้นส่วน", { list: "mpPartList", placeholder: "เช่น มอเตอร์, สายพาน, ลูกปืน" }],
  ["spec", "สเปค", { placeholder: "เช่น Calpeda NM65/12C/B, B-82", wide: true }],
  ["qty", "จำนวน", { type: "number" }],
  ["unit", "หน่วย", { placeholder: "ตัว / เส้น / ตลับ" }],
  ["bearing_bore", "รูลูกปืน", { placeholder: "เช่น หุน" }],
];
const PART_ELECTRIC = [
  ["motor_hp", "แรงม้า (hp)", { type: "number" }], ["delta_a", "Delta A"], ["star_a", "Star A"], ["link", "ลิงก์ datasheet / รุ่นใน catalogue", { wide: true }],
  ["breaker", "Breaker", { wide: true }], ["contactor", "Magnetic Contactor", { area: true, wide: true }], ["overload", "Overload", { wide: true }],
  ["current_contactor", "Contactor ที่ใช้อยู่จริง", { area: true, wide: true }], ["current_overload", "Overload ที่ใช้อยู่จริง", { wide: true }], ["desired_a", "แอมป์ที่ต้องการ (Desired A)"],
];

// a labelled input bound to a draft object (so what is typed survives a redraw)
function mfield(draft, key, label, { type = "text", list, placeholder, area = false, wide = false, caption = false, maxlength = 300 } = {}) {
  const input = el(area ? "textarea" : "input", { id: `mf_${key}`, ...(area ? { rows: "2" } : { type, autocomplete: "off" }), maxlength: String(maxlength) });
  input.value = draft[key] ?? "";
  if (list) input.setAttribute("list", list);
  if (placeholder) input.placeholder = placeholder;
  if (type === "number") { input.inputMode = "decimal"; input.step = "any"; }
  const cap = caption ? el("div", { class: "datecap" }) : null;
  if (cap) bindDateField(input, cap);
  input.addEventListener("input", () => { draft[key] = input.value; });
  return el("div", { class: `mfield${wide ? " wide" : ""}` }, el("label", { for: `mf_${key}`, text: label }), input, cap);
}

function mselect(draft, key, label, options, { blank = "— ไม่ระบุ —", wide = false, onChange } = {}) {
  const sel = el("select", { id: `mf_${key}` }, el("option", { value: "", text: blank }), ...options.map(([v, t]) => el("option", { value: v, text: t })));
  const cur = draft[key] || "";
  if (cur && !options.some(([v]) => v === cur)) sel.append(el("option", { value: cur, text: cur }));
  sel.value = cur;
  sel.addEventListener("change", () => { draft[key] = sel.value; if (onChange) onChange(); });
  return el("div", { class: `mfield${wide ? " wide" : ""}` }, el("label", { for: `mf_${key}`, text: label }), sel);
}

function openPartDialog(p, defaults = {}) {
  mc.partTarget = p || null;
  mc.partDraft = p ? { ...p, qty: p.qty ?? "", machine_id: p.machine_id || "" } : { location: "", machine_id: "", status: "", ...defaults };
  $("pdTitle").textContent = p ? "รายละเอียดอะไหล่ / สเปค" : "เพิ่มรายการอะไหล่ / สเปค";
  const d = mc.partDraft;
  const hasElectric = PART_ELECTRIC.some(([k]) => d[k] !== undefined && d[k] !== null && d[k] !== "");
  fill($("pdBody"),
    el("div", { class: "mfields" },
      ...PART_FIELDS.map(([k, l, o]) => mfield(d, k, l, o)),
      mselect(d, "machine_id", "ผูกกับเครื่อง", mc.machines.filter((m) => m.active !== false).map((m) => [m.id, m.name]), { blank: "— ไม่ผูกเครื่อง (พื้นที่ทั่วไป) —" }),
      mselect(d, "status", "สถานะ", mc.opts.part_statuses.map((s) => [s, s]), { blank: "— ไม่ระบุ —" })),
    el("details", { class: "mdetails", open: hasElectric }, el("summary", { text: "ข้อมูลมอเตอร์ / ไฟฟ้า" }),
      el("div", { class: "mfields" }, ...PART_ELECTRIC.map(([k, l, o]) => mfield(d, k, l, o)))),
    el("div", { class: "mfields" }, mfield(d, "notes", "หมายเหตุ", { wide: true })),
    el("datalist", { id: "mpLocationList" }, ...mc.parts.facets.locations.map((f) => el("option", { value: f.value }))),
    el("datalist", { id: "mpPartList" }, ...mc.parts.facets.parts.map((f) => el("option", { value: f.value }))));
  $("pdDelete").hidden = !p;
  showErr("pdError", "");
  $("partDialog").showModal();
}

async function savePart() {
  const body = {};
  for (const [k] of [...PART_FIELDS, ...PART_ELECTRIC, ["machine_id"], ["status"], ["notes"]]) body[k] = mc.partDraft[k] ?? "";
  try {
    if (mc.partTarget) await api(`/api/machine-parts/${mc.partTarget.id}`, jsonOpts("PATCH", body));
    else await api("/api/machine-parts", jsonOpts("POST", body));
  } catch (err) {
    showErr("pdError", err.message);
    return;
  }
  $("partDialog").close();
  await refreshMachineryViews();
}

async function deletePart() {
  const p = mc.partTarget;
  if (!p || !confirm(`ลบรายการ “${[p.equipment, p.part, p.spec].filter(Boolean).join(" · ")}” ออกจากดัชนีใช่ไหม?`)) return;
  try {
    await api(`/api/machine-parts/${p.id}`, { method: "DELETE" });
  } catch (err) {
    showErr("pdError", err.message);
    return;
  }
  $("partDialog").close();
  await refreshMachineryViews();
}

// whatever machinery page is showing reloads (counts, lists)
async function refreshMachineryViews() {
  await loadMachineryBase();
  if (!$("machineryView").hidden) { renderMachineCards(); await loadParts(); }
  if (!$("machineView").hidden && mc.current) await renderMachinePage();
  if (!$("machineLogView").hidden) await loadEvents(true);
}

// ================================================================== one machine (#machine=<id>)
async function openMachinePage(id) {
  showView("machine");
  showErr("mdError", "");
  mc.dirty = false;
  const ok = await loadMachineryBase();
  if (ok !== true) { showErr("mdError", `โหลดข้อมูลเครื่องจักรไม่สำเร็จ: ${ok}`); return; }
  if (id === "new") {
    mc.current = null;
    mc.editing = true;
    mc.draft = { branch: (state.branches || [])[0] || "", kind: "เครื่อง", number: "", tube_type: "", make: "", model: "", capacity_tons: "", notes: "", active: true, name: "" };
    await renderMachinePage();
    return;
  }
  mc.current = machineById(id);
  if (!mc.current) { showErr("mdError", "ไม่พบเครื่องนี้"); fill($("mdHead")); fill($("mdBody")); fill($("mdActions")); return; }
  mc.editing = false;
  mc.draft = machineDraft(mc.current);
  await renderMachinePage();
}

const machineDraft = (m) => ({ branch: m.branch, kind: m.kind, number: m.number ?? "", tube_type: m.tube_type || "", make: m.make || "", model: m.model || "",
  capacity_tons: m.capacity_tons ?? "", notes: m.notes || "", active: m.active !== false, name: m.name });

async function renderMachinePage() {
  const m = mc.current;
  if (m) mc.current = machineById(m.id) || m;
  fill($("mdHead"), ...(m ? [el("h2", { class: "tk-title", text: mc.current.name }), el("div", { class: "muted", text: [machineSub(mc.current), mc.current.branch].filter(Boolean).join(" · ") }),
    mc.current.active === false ? el("div", { class: "rr-badges" }, el("span", { class: "badge", text: "เลิกใช้แล้ว" })) : null]
    : [el("h2", { class: "tk-title", text: "เพิ่มเครื่องใหม่" })]));
  const btn = (text, onclick, cls = "") => el("button", { class: `btn${cls ? ` ${cls}` : ""}`, type: "button", text, onclick });
  fill($("mdActions"), ...(mc.editing
    ? [btn("ยกเลิก", cancelMachineEdit), btn("บันทึก", saveMachine, "primary")]
    : [btn("บันทึกงานซ่อมเครื่องนี้", () => openEventDialog(null, { machine_id: m.id })), btn("แก้ไข", startMachineEdit, "primary")]));
  const cards = [];
  if (mc.editing) {
    const d = mc.draft;
    cards.push(tkCard("ข้อมูลเครื่อง", el("div", { class: "mfields" },
      mselect(d, "branch", "สาขา", (state.branches || []).map((b) => [b, b]), { blank: "— เลือกสาขา —" }),
      mselect(d, "kind", "ประเภท", mc.opts.kinds.map((k) => [k, k]), { blank: "เครื่อง", onChange: () => { mc.dirty = true; renderMachinePage(); } }),
      d.kind === "บ่อซอง" ? null : mfield(d, "number", "เลขเครื่อง", { type: "number" }),
      mfield(d, "tube_type", "ชนิดหลอด", { list: "mdTubeList", placeholder: "หลอดใหญ่ / หลอดเหลี่ยม / หลอดเล็ก" }),
      mfield(d, "make", "ยี่ห้อ", { placeholder: "MYCOM / Sabroe" }), mfield(d, "model", "รุ่น", { placeholder: "8WB-HE" }),
      mfield(d, "capacity_tons", "ขนาด (ตัน)", { type: "number" }),
      mfield(d, "name", "ชื่อที่แสดง (ว่างไว้ = ตั้งให้เอง)", { wide: true, maxlength: 80 }),
      el("datalist", { id: "mdTubeList" }, ...mc.opts.tube_types.map((t) => el("option", { value: t })))),
      m ? el("label", { class: "check tk-active" }, el("input", { type: "checkbox", checked: d.active, onchange: (e) => { d.active = e.target.checked; mc.dirty = true; } }), " ใช้งานอยู่") : null),
      tkCard("หมายเหตุ", el("div", { class: "mfields" }, mfield(d, "notes", "หมายเหตุ (ไม่บังคับ)", { wide: true }))));
    document.querySelectorAll("#mdBody input, #mdBody select, #mdBody textarea").forEach((n) => n.addEventListener("input", () => { mc.dirty = true; }));
  } else {
    const c = mc.current;
    cards.push(tkCard("ข้อมูลเครื่อง", kvAll("สาขา", c.branch), kvAll("ประเภท", c.kind), kvAll("เลขเครื่อง", c.number), kvAll("ชนิดหลอด", c.tube_type), kvAll("ยี่ห้อ", c.make), kvAll("รุ่น", c.model),
      kvAll("ขนาด", c.capacity_tons ? `${c.capacity_tons} ตัน` : ""), kvAll("สถานะ", c.active === false ? "เลิกใช้แล้ว" : "ใช้งานอยู่")));
    if (c.notes) cards.push(tkCard("หมายเหตุ", el("p", { class: "tk-notes", text: c.notes })));
  }
  fill($("mdBody"), ...cards);
  // the machine's parts and latest reports (only for a saved machine)
  $("mdParts").hidden = !m || mc.editing;
  $("mdEvents").hidden = !m || mc.editing;
  if (m && !mc.editing) {
    const [parts, events] = await Promise.all([api(`/api/machine-parts?machine_id=${m.id}&limit=500`), api(`/api/machine-events?machine_id=${m.id}&limit=8`)]);
    $("mdPartCount").textContent = parts.total ? `(${parts.total})` : "";
    fill($("mdPartList"), ...partRows(parts.rows, { emptyText: "ยังไม่มีรายการอะไหล่ของเครื่องนี้ — กด “+ เพิ่มรายการ”", showLocation: false }));
    $("mdEventCount").textContent = events.total ? `(${events.total})` : "";
    fill($("mdEventList"), ...(events.rows.length ? events.rows.map(eventRow) : [el("p", { class: "muted empty", text: "ยังไม่มีบันทึกงานซ่อมของเครื่องนี้" })]));
    $("mdEventAll").hidden = events.total <= events.rows.length;
  }
}

function startMachineEdit() { mc.editing = true; mc.draft = machineDraft(mc.current); mc.dirty = false; renderMachinePage(); }

function cancelMachineEdit() {
  if (mc.dirty && !confirm("ยังไม่ได้บันทึก ยกเลิกการแก้ไขใช่ไหม?")) return;
  if (!mc.current) { goHash("#machinery"); return; }
  mc.editing = false;
  showErr("mdError", "");
  renderMachinePage();
}

async function saveMachine() {
  showErr("mdError", "");
  const body = { ...mc.draft };
  if (!mc.current) delete body.active;
  try {
    const res = mc.current ? await api(`/api/machines/${mc.current.id}`, jsonOpts("PATCH", body)) : await api("/api/machines", jsonOpts("POST", body));
    mc.dirty = false;
    await loadMachineryBase();
    if (!mc.current) { goMachine(res.machine_id); return; }
    mc.editing = false;
    await renderMachinePage();
    window.scrollTo(0, 0);
  } catch (err) {
    showErr("mdError", err.message);
    window.scrollTo(0, 0);
  }
}

// ================================================================== maintenance reports (#machine-log)
async function openMachineLog() {
  showView("machineLog");
  showErr("mlError", "");
  const ok = await loadMachineryBase();
  if (ok !== true) { showErr("mlError", `โหลดข้อมูลเครื่องจักรไม่สำเร็จ: ${ok}`); return; }
  showReady("mlNeedsV9");
  fillEventFilters();
  await loadEvents(true);
}

function fillEventFilters() {
  const keepM = $("mlMachine").value, keepK = $("mlKind").value;
  $("mlMachine").replaceChildren(el("option", { value: "", text: "ทุกเครื่อง / พื้นที่" }),
    ...mc.machines.filter((m) => m.active !== false).map((m) => el("option", { value: m.id, text: m.name })));
  $("mlKind").replaceChildren(el("option", { value: "", text: "ทุกประเภทงาน" }), ...mc.opts.event_kinds.map((k) => el("option", { value: k, text: k })));
  $("mlMachine").value = keepM;
  $("mlKind").value = keepK;
}

function eventQuery(extra = {}) {
  const f = { q: $("mlQ").value.trim(), machine_id: $("mlMachine").value, kind: $("mlKind").value, date_from: $("mlFrom").value, date_to: $("mlTo").value, ...extra };
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
  return p.toString();
}

let eventsTimer = null;
const scheduleEvents = (delay = 250) => { clearTimeout(eventsTimer); eventsTimer = setTimeout(() => loadEvents(true), delay); };

async function loadEvents(reset) {
  const offset = reset ? 0 : mc.events.rows.length;
  try {
    const res = await api(`/api/machine-events?${eventQuery({ limit: mc.evLimit, offset })}`);
    mc.events = reset ? res : { ...res, rows: mc.events.rows.concat(res.rows) };
  } catch (err) {
    showErr("mlError", `โหลดบันทึกซ่อมบำรุงไม่สำเร็จ: ${err.message}`);
    return;
  }
  fill($("mlSummary"), el("strong", { text: `พบ ${mc.events.total} รายการ${mc.events.stats.total_cost ? ` · ค่าใช้จ่ายรวม ${baht(mc.events.stats.total_cost)} ฿` : ""}` }));
  fill($("mlList"), ...(mc.events.rows.length ? mc.events.rows.map(eventRow) : [el("p", { class: "muted empty", text: "ยังไม่มีบันทึก — กด “+ บันทึกงาน”" })]));
  const left = mc.events.total - mc.events.rows.length;
  $("mlMore").hidden = left <= 0;
  $("mlMore").textContent = `แสดงเพิ่ม (อีก ${left} รายการ)`;
}

function eventRow(e) {
  const d = beDate(e.event_date).split(" ");
  const where = e.machine_name || e.area || "ทั่วไป";
  const meta = [e.performed_by ? `โดย ${e.performed_by}` : null, e.vendor ? `ร้าน ${e.vendor}` : null, [e.brand, e.model].filter(Boolean).join(" ") || null, e.serial_number ? `S/N ${e.serial_number}` : null].filter(Boolean).join(" · ");
  return el("button", { class: "logrow done", type: "button", onclick: () => openEventDialog(e) },
    el("div", { class: "evdate" }, el("div", { class: "evday", text: d[0] }), el("div", { class: "evmon", text: `${d[1]} ${d[2]}` })),
    el("div", { class: "evmain" },
      el("div", { class: "evtitle" }, el("strong", { text: where }), e.equipment ? el("span", { class: "muted small", text: e.equipment }) : null,
        el("span", { class: "badge shop", text: e.kind }), e.branch ? el("span", { class: "muted small", text: e.branch }) : null),
      el("div", { class: "evsummary", text: e.summary }),
      e.reason ? el("div", { class: "muted small", text: `เหตุผล: ${e.reason}` }) : null,
      meta ? el("div", { class: "muted small", text: meta }) : null),
    el("div", { class: "evtotal", text: e.cost ? `${baht(e.cost)} ฿` : "–" }));
}

// ---- a report (form in a dialog)
const EVENT_DETAIL = [["brand", "ยี่ห้อ"], ["model", "รุ่น"], ["serial_number", "Serial Number"], ["vendor", "ร้าน / ผู้ขาย"], ["vendor_contact", "เบอร์ติดต่อ / ไลน์ร้าน"]];

function openEventDialog(e, defaults = {}) {
  mc.evTarget = e || null;
  mc.evDraft = e ? { ...e, machine_id: e.machine_id || "", cost: e.cost ?? "" }
    : { event_date: todayISO(), machine_id: "", branch: (state.branches || [])[0] || "", area: "", equipment: "", kind: "ซ่อม", summary: "", reason: "", brand: "", model: "",
        serial_number: "", vendor: "", vendor_contact: "", performed_by: "", cost: "", notes: "", ...defaults };
  $("evdTitle").textContent = e ? "แก้ไขบันทึกซ่อมบำรุง" : "บันทึกงานซ่อมบำรุง";
  drawEventForm();
  showErr("evdError", "");
  $("evDialog").showModal();
}

function drawEventForm() {
  const d = mc.evDraft;
  const noMachine = !d.machine_id;
  const hasDetail = EVENT_DETAIL.some(([k]) => d[k]);
  // the equipment suggestions come from the parts index of the chosen machine
  const equipment = [...new Set(mc.parts.rows.filter((p) => !d.machine_id || p.machine_id === d.machine_id).map((p) => p.equipment).filter(Boolean))];
  fill($("evdBody"),
    el("div", { class: "mfields" },
      mfield(d, "event_date", "วันที่ทำ", { type: "date", caption: true }),
      mselect(d, "kind", "ประเภทงาน", mc.opts.event_kinds.map((k) => [k, k]), { blank: "ซ่อม" }),
      mselect(d, "machine_id", "เครื่อง", mc.machines.filter((m) => m.active !== false || m.id === d.machine_id).map((m) => [m.id, m.name]),
        { blank: "— ไม่ใช่เครื่องใดเครื่องหนึ่ง (ระบุพื้นที่) —", wide: true, onChange: () => drawEventForm() }),
      noMachine ? mselect(d, "branch", "สาขา", (state.branches || []).map((b) => [b, b]), { blank: "— เลือกสาขา —" }) : null,
      noMachine ? mfield(d, "area", "พื้นที่ / ระบบ", { placeholder: "เช่น ระบบน้ำ, ตู้แพ็ค, โรงซอง" }) : null,
      mfield(d, "equipment", "อุปกรณ์", { list: "evEquipList", placeholder: "เช่น พัดลม, ปั๊มน้ำเย็น", wide: true }),
      mfield(d, "summary", "ทำอะไร", { placeholder: "เช่น เปลี่ยนสายพานพัดลม", wide: true, maxlength: 500 }),
      mfield(d, "reason", "สาเหตุ / เหตุผล", { wide: true, maxlength: 500 }),
      mfield(d, "performed_by", "ผู้ทำ", { placeholder: "เช่น ช่างเอ๋ / ชื่อร้าน" }), mfield(d, "cost", "ค่าใช้จ่าย (฿)", { type: "number" })),
    el("details", { class: "mdetails", open: hasDetail }, el("summary", { text: "ถ้าเปลี่ยนอะไหล่: ยี่ห้อ รุ่น ร้าน" }),
      el("div", { class: "mfields" }, ...EVENT_DETAIL.map(([k, l]) => mfield(d, k, l, { wide: k === "vendor_contact" })))),
    el("div", { class: "mfields" }, mfield(d, "notes", "หมายเหตุ", { wide: true, maxlength: 500 })),
    el("datalist", { id: "evEquipList" }, ...equipment.map((n) => el("option", { value: n }))));
}

async function saveEvent() {
  const d = mc.evDraft;
  const body = { event_date: d.event_date, kind: d.kind || "ซ่อม", machine_id: d.machine_id || "", equipment: d.equipment, summary: d.summary, reason: d.reason,
    performed_by: d.performed_by, cost: d.cost, notes: d.notes };
  for (const [k] of EVENT_DETAIL) body[k] = d[k];
  if (!d.machine_id) { body.branch = d.branch; body.area = d.area; }
  try {
    if (mc.evTarget) await api(`/api/machine-events/${mc.evTarget.id}`, jsonOpts("PATCH", body));
    else await api("/api/machine-events", jsonOpts("POST", body));
  } catch (err) {
    showErr("evdError", err.message);
    return;
  }
  $("evDialog").close();
  await refreshMachineryViews();
}

// ================================================================== init
function initMachinery() {
  $("mpQ").addEventListener("input", () => scheduleParts());
  for (const id of ["mpLocation", "mpPart"]) $(id).addEventListener("change", () => scheduleParts(0));
  $("mpAdd").addEventListener("click", () => openPartDialog(null));
  $("mcAddMachine").addEventListener("click", () => goMachine("new"));
  $("pdCancel").addEventListener("click", () => $("partDialog").close());
  $("pdSave").addEventListener("click", savePart);
  $("pdDelete").addEventListener("click", deletePart);
  $("mdBack").addEventListener("click", () => {
    if (mc.editing && mc.dirty && !confirm("ยังไม่ได้บันทึก ออกจากหน้านี้ใช่ไหม?")) return;
    mc.editing = false;
    goHash("#machinery");
  });
  $("mdPartAdd").addEventListener("click", () => openPartDialog(null, { location: mc.current.name, machine_id: mc.current.id }));
  $("mdEventAll").addEventListener("click", () => { $("mlMachine").value = mc.current.id; goHash("#machine-log"); });
  for (const id of ["mlQ"]) $(id).addEventListener("input", () => scheduleEvents());
  for (const id of ["mlMachine", "mlKind"]) $(id).addEventListener("change", () => scheduleEvents(0));
  bindDateField($("mlFrom"), $("mlFromCap"));
  bindDateField($("mlTo"), $("mlToCap"));
  $("mlFrom").addEventListener("change", () => scheduleEvents(0));
  $("mlTo").addEventListener("change", () => scheduleEvents(0));
  $("mlClear").addEventListener("click", () => { for (const id of ["mlQ", "mlMachine", "mlKind", "mlFrom", "mlTo"]) $(id).value = ""; $("mlFromCap").textContent = ""; $("mlToCap").textContent = ""; scheduleEvents(0); });
  $("mlMore").addEventListener("click", () => loadEvents(false));
  $("mlAdd").addEventListener("click", () => openEventDialog(null, $("mlMachine").value ? { machine_id: $("mlMachine").value } : {}));
  $("evdCancel").addEventListener("click", () => $("evDialog").close());
  $("evdSave").addEventListener("click", saveEvent);
}
