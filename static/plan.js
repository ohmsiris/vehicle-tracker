"use strict";

// แผนซ่อมบำรุงรถ: the vehicle maintenance programme.
//   #plan        every truck: what is overdue / due soon, and (second tab) the intervals the plan uses
//   a truck page shows that truck's own plan underneath its details (renderTruckPlan, called from roster.js)
// Lists are read-only; recording that something was done, or how far a truck drives, happens in a dialog.
// Data: /api/plan/* (app/service_plan.py). Loaded BEFORE app.js: nothing here runs at load time.

const pl = {
  opts: { ready: true, classes: [], items: [], soon_days: 14 },
  overview: null, tab: "due", intervals: null,
  detail: null, detailFor: null,
  dlg: null,                       // what the open dialog is doing: { kind, vehicle, draft }
};

const PL_STATE_TEXT = { overdue: "เลยกำหนด", soon: "ใกล้ถึงกำหนด", ok: "ปกติ", unknown: "ยังไม่มีประวัติ" };
const PL_BADGE = { overdue: "urgent", soon: "warn", ok: "ok", unknown: "" };
const plToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const plKm = (n) => (n === null || n === undefined ? "–" : `${baht(n)} กม.`);

function plDueText(i) {
  if (i.state === "unknown") return "ยังไม่มีประวัติ";
  if (i.days_left === null) return "ปกติ";
  const by = i.due_by === "km" ? "ครบระยะทาง" : "ครบเวลา";
  if (i.days_left < 0) return `${by} เลยมา ${-i.days_left} วัน`;
  if (i.days_left === 0) return `${by} วันนี้`;
  return `${by} อีก ${i.days_left} วัน`;
}

const plDueDate = (i) => (i.due_date ? beDate(i.due_date) : "–");

function plKmPerDay(k) {
  if (!k || !k.value) return "ยังไม่มีข้อมูลระยะทาง";
  const base = `${Math.round(k.value)} กม./วัน`;
  return k.source === "default" ? `ประมาณ ${base} (ค่าเฉลี่ย — ยังไม่มีข้อมูลของคันนี้)` : `${base} (${k.note})`;
}

const plLabel = (v) => [v.vehicle_number ? `#${v.vehicle_number}` : null, v.plate_number, v.nickname].filter(Boolean).join(" · ");

function plChip(i) {
  const when = i.days_left < 0 ? `เลย ${-i.days_left} วัน` : i.days_left === 0 ? "วันนี้" : `อีก ${i.days_left} วัน`;
  return el("span", { class: `badge ${PL_BADGE[i.state]}`.trim(), title: plDueText(i), text: `${i.label} · ${when}` });
}

async function plLoadOptions() {
  try { pl.opts = await api("/api/plan/options"); } catch (err) { return err.message; }
  return true;
}

// ================================================================== the page (#plan)
async function openPlan() {
  showView("plan");
  showErr("plError", "");
  const ok = await plLoadOptions();
  if (ok !== true) { showErr("plError", `โหลดแผนซ่อมบำรุงไม่สำเร็จ: ${ok}`); return; }
  $("plNeedsV10").hidden = pl.opts.ready !== false;
  await ensureVehicles();
  const branches = [...new Set(fleet.vehicles.map((v) => v.branch).filter(Boolean))].sort();
  const keep = $("plBranch").value;
  $("plBranch").replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...branches.map((b) => el("option", { value: b, text: b })));
  $("plBranch").value = keep;
  $("plClass").replaceChildren(el("option", { value: "", text: "รถทุกขนาด" }), ...pl.opts.classes.map((c) => el("option", { value: c, text: c })));
  setPlanTab(pl.tab);
}

function setPlanTab(tab) {
  pl.tab = tab;
  $("plTabDue").classList.toggle("active", tab === "due");
  $("plTabSet").classList.toggle("active", tab === "set");
  $("plDuePane").hidden = tab !== "due";
  $("plSetPane").hidden = tab !== "set";
  if (tab === "due") loadOverview(); else loadIntervals();
}

async function loadOverview() {
  const p = new URLSearchParams();
  if ($("plBranch").value) p.set("branch", $("plBranch").value);
  if ($("plClass").value) p.set("vehicle_class", $("plClass").value);
  try {
    pl.overview = await api(`/api/plan/overview?${p}`);
  } catch (err) {
    showErr("plError", `โหลดแผนซ่อมบำรุงไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderOverview();
}

function renderOverview() {
  const o = pl.overview;
  if (!o) return;
  const t = o.totals, k = o.trucks;
  fill($("plKpis"),
    plKpi("รถที่มีรายการเลยกำหนด", k.overdue, `${t.overdue} รายการ`, k.overdue ? "bad" : ""),
    plKpi("รถที่ใกล้ถึงกำหนด", k.soon, `ภายใน ${pl.opts.soon_days} วัน · ${t.soon} รายการ`, k.soon ? "warn" : ""),
    plKpi("รถที่ยังไม่มีประวัติเลย", k.no_history, "บันทึกครั้งล่าสุดเพื่อเริ่มนับ", ""),
    plKpi("รายการที่ปกติ", t.ok, "อยู่ในระยะ", ""));
  const q = $("plQ").value.trim().toLowerCase();
  const show = $("plShow").value;
  const rows = o.vehicles.filter((v) => {
    if (q && ![v.plate_number, v.vehicle_number, v.nickname, v.make, v.model, v.usage].filter(Boolean).join(" ").toLowerCase().includes(q)) return false;
    if (show === "due") return v.counts.overdue || v.counts.soon;
    if (show === "nohistory") return v.has_plan && !(v.counts.overdue + v.counts.soon + v.counts.ok);
    return true;
  });
  const nothing = show === "due" && !rows.length;
  fill($("plCount"), `${rows.length} คัน${nothing ? ` — ไม่มีรถที่ต้องทำตอนนี้${k.no_history ? ` · แต่ยังมี ${k.no_history} คันที่ไม่มีประวัติเลย (เลือก “ที่ยังไม่มีประวัติเลย” เพื่อดู)` : ""}` : ""}`);
  fill($("plList"), ...(rows.length ? rows.map(planCard) : [el("p", { class: "muted empty", text: "ไม่พบรถที่ตรงกับตัวกรอง" })]));
}

function plKpi(label, value, sub, tone) {
  return el("div", { class: `kpi ${tone}`.trim() }, el("div", { class: "kpi-label", text: label }), el("div", { class: "kpi-value", text: String(value) }), el("div", { class: "kpi-sub", text: sub }));
}

function planCard(v) {
  const attention = v.items.filter((i) => i.state === "overdue" || i.state === "soon");
  const known = v.counts.overdue + v.counts.soon + v.counts.ok;
  return el("div", { class: `card plcard ${v.counts.overdue ? "overdue" : v.counts.soon ? "soon" : ""}`.trim() },
    el("div", { class: "pl-top" },
      el("a", { class: "pl-title", href: `#truck=${v.id}` }, el("strong", { text: plLabel(v) || "–" }), v.branch ? el("span", { class: "muted small", text: ` ${v.branch}` }) : null),
      el("span", { class: "badge", text: v.vehicle_class || "ยังไม่ระบุขนาดรถ" }),
      el("span", { class: "grow" }),
      v.has_plan ? el("button", { class: "btn small", type: "button", text: "+ บันทึกว่าทำแล้ว", onclick: () => openRecordDialog(v, attention.map((i) => i.key)) }) : null),
    !v.has_plan ? el("p", { class: "muted small", text: "ยังไม่ได้ระบุว่าเป็นรถเล็กหรือรถใหญ่ — เปิดหน้ารถแล้วกด “แก้ไข” เพื่อเลือก" })
      : attention.length ? el("div", { class: "pl-chips" }, ...attention.map(plChip))
      : el("p", { class: "muted small", text: known ? `ปกติทุกรายการที่มีประวัติ (${known}/${v.items.length} รายการ)` : "ยังไม่มีประวัติเลย — กด “บันทึกว่าทำแล้ว” เพื่อเริ่มนับ" }),
    el("div", { class: "muted small", text: plKmPerDay(v.km_per_day) }));
}

// ================================================================== the intervals (second tab)
async function loadIntervals() {
  try {
    pl.intervals = await api("/api/plan/intervals");
  } catch (err) {
    showErr("plError", `โหลดตั้งค่าไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderIntervals();
}

function renderIntervals() {
  const data = pl.intervals;
  if (!data) return;
  fill($("plSetList"), ...Object.entries(data.classes).map(([cls, items]) =>
    el("section", { class: "card plset" },
      el("h3", { text: cls }),
      el("p", { class: "muted small", text: cls === "รถเล็ก" ? "D-Max, Revo" : "NLR / NMR / NPR, รถ 6 ล้อ, รถ 10 ล้อ (Hino / Isuzu)" }),
      el("div", { class: "pl-set-head small muted" }, el("span", { text: "รายการ" }), el("span", { text: "ทุกๆ (กม.)" }), el("span", { text: "หรือทุกๆ (เดือน)" }), el("span", {})),
      ...items.map((it) => intervalRow(cls, it)))));
}

function intervalRow(cls, it) {
  const km = el("input", { type: "number", inputmode: "numeric", min: "0", step: "1000", value: it.interval_km ?? "", "aria-label": `${it.label} กม.` });
  const mo = el("input", { type: "number", inputmode: "numeric", min: "0", step: "1", value: it.interval_months ?? "", "aria-label": `${it.label} เดือน` });
  const msg = el("span", { class: "muted small" });
  const save = async () => {
    try {
      pl.intervals = await api("/api/plan/intervals", jsonOpts("PUT", { vehicle_class: cls, item_key: it.key, interval_km: km.value, interval_months: mo.value }));
    } catch (err) { msg.textContent = err.message; msg.className = "error small"; return; }
    renderIntervals();
  };
  const reset = async () => {
    try {
      pl.intervals = await api(`/api/plan/intervals?vehicle_class=${encodeURIComponent(cls)}&item_key=${it.key}`, { method: "DELETE" });
    } catch (err) { msg.textContent = err.message; msg.className = "error small"; return; }
    renderIntervals();
  };
  return el("div", { class: `pl-set-row${it.customised ? " custom" : ""}` },
    el("div", {}, el("strong", { text: it.label }), el("div", { class: "muted small", text: `ค่าเริ่มต้น ${baht(it.default_km)} กม. / ${it.default_months} เดือน` })),
    km, mo,
    el("div", { class: "pl-set-actions" }, el("button", { class: "btn small", type: "button", text: "บันทึก", onclick: save }),
      it.customised ? el("button", { class: "btn small", type: "button", text: "คืนค่าเริ่มต้น", onclick: reset }) : null, msg));
}

// ================================================================== one truck's plan
async function renderTruckPlan(v, editing) {
  const box = $("tkPlan");
  if (!v || editing || !v.vehicle_id) { box.hidden = true; fill(box); return; }
  box.hidden = false;
  fill(box, el("p", { class: "muted small", text: "กำลังโหลดแผนซ่อมบำรุง…" }));
  let d;
  try {
    d = await api(`/api/plan/vehicles/${v.vehicle_id}`);
  } catch (err) {
    fill(box, el("p", { class: "error", text: `โหลดแผนซ่อมบำรุงไม่สำเร็จ: ${err.message}` }));
    return;
  }
  if (!tk.v || tk.v.vehicle_id !== v.vehicle_id || tk.editing) return;      // the page moved on while this was loading
  pl.detail = d;
  fill(box, ...truckPlanNodes(d));
}

const plRefreshTruck = () => (tk.v ? renderTruckPlan(tk.v, tk.editing) : null);

function truckPlanNodes(d) {
  const v = { id: d.vehicle.id, plate_number: d.vehicle.plate_number, vehicle_number: d.vehicle.vehicle_number, vehicle_class: d.vehicle.vehicle_class, items: d.items };
  const head = el("div", { class: "mchead" }, el("h3", { class: "section-title", text: "แผนซ่อมบำรุง" }), el("span", { class: "grow" }),
    d.has_plan ? el("button", { class: "btn small", type: "button", text: "+ บันทึกว่าทำแล้ว", onclick: () => openRecordDialog(v, d.items.filter((i) => i.state === "overdue" || i.state === "soon").map((i) => i.key)) }) : null,
    d.has_plan && d.items.some((i) => i.state === "unknown") ? el("button", { class: "btn small", type: "button", text: "เริ่มนับจากวันนี้", title: "รายการที่ยังไม่มีประวัติ จะถือว่าเพิ่งทำวันนี้", onclick: () => startCounting(v, d) }) : null,
    el("button", { class: "btn small", type: "button", text: "+ ระยะทาง (GPS / เลขไมล์)", onclick: () => openReadingDialog(v) }));
  if (!d.has_plan) {
    return [head, el("p", { class: "muted", text: "ยังไม่ได้ระบุว่าเป็นรถเล็กหรือรถใหญ่ จึงยังไม่มีแผน — กด “แก้ไข” แล้วเลือกขนาดรถ" })];
  }
  const odo = d.odometer ? ` · เลขไมล์ล่าสุด ${plKm(d.odometer.km)} (${beDate(d.odometer.date)})` : "";
  const table = el("div", { class: "card pltable" },
    el("div", { class: "pl-row pl-th small muted" }, el("span", { text: "รายการ" }), el("span", { text: "ทำล่าสุด" }), el("span", { text: "ระยะที่ใช้ไป" }), el("span", { text: "ครั้งต่อไป" }), el("span", { text: "" })),
    ...d.items.map((i) => el("div", { class: `pl-row ${i.state}` },
      el("span", { class: "pl-item" }, el("strong", { text: i.label }), el("span", { class: "muted small", text: `ทุก ${[i.interval_km ? plKm(i.interval_km) : null, i.interval_months ? `${i.interval_months} เดือน` : null].filter(Boolean).join(" หรือ ")}${i.customised ? " (ปรับแล้ว)" : ""}` })),
      el("span", { class: "pl-last" }, i.last_date ? beDate(i.last_date) : "–", i.last_source === "bill" ? el("span", { class: "muted small", text: " จากบิล" }) : i.last_source === "baseline" ? el("span", { class: "muted small", text: " เริ่มนับ" }) : null),
      el("span", { class: "pl-km", text: i.km_since === null ? "–" : `${i.km_basis === "estimate" ? "≈ " : ""}${plKm(i.km_since)}` }),
      el("span", { class: "pl-next" }, el("span", { text: plDueDate(i) }), el("span", { class: "muted small", text: i.km_left !== null && i.state !== "unknown" ? ` · ${i.km_left >= 0 ? `เหลือ ≈ ${plKm(i.km_left)}` : `เกิน ≈ ${plKm(-i.km_left)}`}` : "" })),
      el("span", { class: "pl-state" }, el("span", { class: `badge ${PL_BADGE[i.state]}`.trim(), text: i.state === "unknown" ? PL_STATE_TEXT.unknown : i.state === "ok" ? `อีก ${i.days_left} วัน` : plDueText(i) }))))
  );
  const history = d.history.length ? el("details", { class: "card pl-hist" }, el("summary", { text: `ประวัติที่ทำแล้ว (${d.history.length})` }),
    ...d.history.map((h) => el("div", { class: "pl-hrow" },
      el("span", { text: beDate(h.done_date) }), el("strong", { text: h.label }),
      el("span", { class: "muted small", text: [h.odometer_km ? plKm(h.odometer_km) : null, h.source === "bill" ? "จากบิล" : h.source === "baseline" ? "เริ่มนับ" : "บันทึกเอง", h.notes].filter(Boolean).join(" · ") }),
      h.source === "bill" && h.event_id ? el("a", { class: "btn small", href: `#event=${h.event_id}`, text: "ดูบิล" })
        : h.id ? el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: () => deleteRecord(h) }) : null))) : null;
  const readings = d.readings.length ? el("details", { class: "card pl-hist" }, el("summary", { text: `ข้อมูลระยะทาง (${d.readings.length})` }),
    ...d.readings.map((r) => el("div", { class: "pl-hrow" },
      el("span", { text: r.kind === "gps" ? `${beDate(r.start_date)} – ${beDate(r.end_date)}` : beDate(r.end_date) }),
      el("strong", { text: r.kind === "gps" ? `GPS ${plKm(r.km)}` : `เลขไมล์ ${plKm(r.km)}` }),
      el("span", { class: "muted small", text: r.notes || "" }),
      el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: () => deleteReading(r) })))) : null;
  return [head, el("p", { class: "muted small", text: `ระยะทางที่ใช้คำนวณ: ${plKmPerDay(d.km_per_day)}${odo}` }), table, history, readings];
}

async function deleteRecord(h) {
  if (!confirm(`ลบบันทึก “${h.label}” วันที่ ${beDate(h.done_date)} ใช่ไหม?`)) return;
  try { await api(`/api/plan/records/${h.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
  await plRefreshTruck();
}

async function deleteReading(r) {
  if (!confirm("ลบข้อมูลระยะทางนี้ใช่ไหม?")) return;
  try { await api(`/api/plan/readings/${r.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
  await plRefreshTruck();
}

async function startCounting(v, d) {
  const keys = d.items.filter((i) => i.state === "unknown").map((i) => i.key);
  if (!confirm(`รายการที่ยังไม่มีประวัติ ${keys.length} รายการ จะถือว่าเพิ่งทำวันนี้ แล้วเริ่มนับระยะจากวันนี้ — ถ้าจริงๆ เพิ่งทำไปแล้วนานกว่านั้น ให้กด “บันทึกว่าทำแล้ว” ใส่วันที่จริงแทน ใช่ไหม?`)) return;
  try {
    await api("/api/plan/records", jsonOpts("POST", { vehicle_id: v.id, items: keys, done_date: plToday(), source: "baseline", notes: "เริ่มนับจากวันที่ใช้ระบบ" }));
  } catch (err) { alert(err.message); return; }
  await plRefreshTruck();
}

// ================================================================== dialogs
async function plAfterSave() {
  $("planDialog").close();
  if (!$("planView").hidden) await loadOverview();
  await plRefreshTruck();
}

function openRecordDialog(v, preset = []) {
  const items = v.items || [];
  pl.dlg = { kind: "record", vehicle: v, draft: { done_date: plToday(), odometer_km: "", notes: "" }, picked: new Set(preset) };
  $("pdlgTitle").textContent = "บันทึกว่าทำแล้ว";
  const picks = el("div", { class: "pl-picks" }, ...items.map((i) => {
    const box = el("input", { type: "checkbox", id: `plp_${i.key}` });
    box.checked = pl.dlg.picked.has(i.key);
    box.addEventListener("change", () => { if (box.checked) pl.dlg.picked.add(i.key); else pl.dlg.picked.delete(i.key); });
    return el("label", { class: "pl-pick", for: `plp_${i.key}` }, box, el("span", { text: i.label }),
      i.state === "overdue" || i.state === "soon" ? el("span", { class: `badge ${PL_BADGE[i.state]}`, text: PL_STATE_TEXT[i.state] }) : null);
  }));
  fill($("pdlgBody"),
    el("p", { class: "muted small", text: `${plLabel(v)} — เลือกสิ่งที่ทำ แล้วใส่วันที่ที่ทำจริง` }), picks,
    el("div", { class: "mfields" },
      mfield(pl.dlg.draft, "done_date", "วันที่ทำ", { type: "date", caption: true }),
      mfield(pl.dlg.draft, "odometer_km", "เลขไมล์ (กม.) ถ้ามี", { type: "number" }),
      mfield(pl.dlg.draft, "notes", "หมายเหตุ", { wide: true })));
  showErr("pdlgError", "");
  $("planDialog").showModal();
}

function openReadingDialog(v) {
  pl.dlg = { kind: "reading", vehicle: v, draft: { kind: "gps", start_date: "", end_date: plToday(), km: "", notes: "" } };
  $("pdlgTitle").textContent = "บันทึกระยะทางที่วิ่ง";
  const d = pl.dlg.draft;
  const gpsOnly = el("div", { class: "mfields" }, mfield(d, "start_date", "ตั้งแต่วันที่", { type: "date", caption: true }));
  const draw = () => {
    gpsOnly.hidden = d.kind !== "gps";
    $("mf_end_date").previousSibling.textContent = d.kind === "gps" ? "ถึงวันที่" : "วันที่อ่านเลขไมล์";
    $("mf_km").previousSibling.textContent = d.kind === "gps" ? "ระยะทางที่วิ่งในช่วงนี้ (กม.)" : "เลขไมล์ (กม.)";
  };
  fill($("pdlgBody"),
    el("p", { class: "muted small", text: `${plLabel(v)} — ระยะทางจากแอป GPS (รวมทั้งช่วง) หรือเลขไมล์จากหน้าปัด ใช้คำนวณว่าถึงรอบเมื่อไหร่` }),
    el("div", { class: "mfields" }, mselect(d, "kind", "ชนิดข้อมูล", [["gps", "ระยะทางจาก GPS (ทั้งช่วง)"], ["odometer", "เลขไมล์จากหน้าปัด"]], { blank: "—", onChange: draw })),
    gpsOnly,
    el("div", { class: "mfields" }, mfield(d, "end_date", "ถึงวันที่", { type: "date", caption: true }), mfield(d, "km", "ระยะทาง (กม.)", { type: "number" }), mfield(d, "notes", "หมายเหตุ", { wide: true })));
  draw();
  showErr("pdlgError", "");
  $("planDialog").showModal();
}

async function savePlanDialog() {
  const dlg = pl.dlg;
  if (!dlg) return;
  const d = dlg.draft;
  try {
    if (dlg.kind === "record") {
      await api("/api/plan/records", jsonOpts("POST", { vehicle_id: dlg.vehicle.id, items: [...dlg.picked], done_date: d.done_date, odometer_km: d.odometer_km, notes: d.notes }));
    } else {
      await api("/api/plan/readings", jsonOpts("POST", { vehicle_id: dlg.vehicle.id, kind: d.kind, start_date: d.kind === "gps" ? d.start_date : "", end_date: d.end_date, km: d.km, notes: d.notes }));
    }
  } catch (err) {
    showErr("pdlgError", err.message);
    return;
  }
  await plAfterSave();
}

// ================================================================== init
function initPlan() {
  $("plTabDue").addEventListener("click", () => setPlanTab("due"));
  $("plTabSet").addEventListener("click", () => setPlanTab("set"));
  $("plQ").addEventListener("input", renderOverview);
  $("plShow").addEventListener("change", renderOverview);
  $("plBranch").addEventListener("change", loadOverview);
  $("plClass").addEventListener("change", loadOverview);
  $("pdlgCancel").addEventListener("click", () => $("planDialog").close());
  $("pdlgSave").addEventListener("click", savePlanDialog);
}
