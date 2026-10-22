"use strict";

// แผนซ่อมบำรุงรถ: the vehicle maintenance programme.
//   #plan        every truck: what is overdue / due soon, and (second tab) the settings: months per group, km per item
//   a truck page shows that truck's own plan underneath its details (renderTruckPlan, called from roster.js):
//   its group, an Isuzu-style chart (what the latest event did, what is overdue / unknown, the next rounds) and its history
// Lists are read-only; recording that something was done, or how far a truck drives, happens in a dialog.
// Data: /api/plan/* (app/service_plan.py). Loaded BEFORE app.js: nothing here runs at load time.

const pl = {
  opts: { ready: true, rule_sets: [], items: [], soon_days: 14 },
  overview: null, tab: "due", settings: null,
  detail: null,
  dlg: null,                       // what the open dialog is doing: { kind, vehicle, draft }
};

const PL_STATE_TEXT = { overdue: "เลยกำหนด", soon: "ใกล้ถึงกำหนด", ok: "ปกติ", unknown: "ยังไม่มีประวัติ" };
const PL_BADGE = { overdue: "urgent", soon: "warn", ok: "ok", unknown: "" };
const plToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const plKm = (n) => (n === null || n === undefined ? "–" : `${baht(n)} กม.`);
const plMonths = (m) => (m === null || m === undefined ? "–" : `${Number.isInteger(m) ? m : (Math.round(m * 10) / 10)} เดือน`);

function plDueText(i) {
  if (i.state === "unknown") return "ยังไม่มีประวัติ";
  if (i.days_left === null) return "ปกติ";
  const by = i.due_by === "km" ? "ครบระยะทาง" : "ครบเวลา";
  if (i.days_left < 0) return `${by} เลยมา ${-i.days_left} วัน`;
  if (i.days_left === 0) return `${by} วันนี้`;
  return `${by} อีก ${i.days_left} วัน`;
}

function plKmPerDay(k) {
  if (!k || !k.value) return "ยังไม่มีข้อมูลระยะทาง";
  const base = `${Math.round(k.value)} กม./วัน`;
  return k.source === "default" ? `ประมาณ ${base} (ตามกลุ่มของรถคันนี้ — ยังไม่มีข้อมูลระยะทางจริง)` : `${base} (${k.note})`;
}

const plSourceText = (src) => (src === "bill" ? "จากบิล" : src === "baseline" ? "เริ่มนับ" : "ช่างทำเอง / บันทึกเอง");
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
  const keepClass = $("plClass").value;
  $("plClass").replaceChildren(el("option", { value: "", text: "รถทุกขนาด" }), ...pl.opts.rule_sets.map((c) => el("option", { value: c, text: c })));
  $("plClass").value = keepClass;
  setPlanTab(pl.tab);
}

function setPlanTab(tab) {
  pl.tab = tab;
  $("plTabDue").classList.toggle("active", tab === "due");
  $("plTabSet").classList.toggle("active", tab === "set");
  $("plDuePane").hidden = tab !== "due";
  $("plSetPane").hidden = tab !== "set";
  if (tab === "due") loadOverview(); else loadSettings();
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
  fill($("plCount"), `${rows.length} คัน${nothing ? ` — ไม่มีรถที่ต้องทำตอนนี้${k.no_history ? ` · แต่ยังมี ${k.no_history} คันที่ไม่มีประวัติเลย (เลือก “ที่ยังไม่มีประวัติเลย” เพื่อดู)` : ""}` : ""}${k.no_group ? ` · ${k.no_group} คันยังไม่ได้เลือกกลุ่มแผน (เปิดหน้ารถเพื่อเลือก)` : ""}`);
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
      el("span", { class: "badge", title: v.group ? v.group.label : "", text: v.group ? `${v.group.code} · ${plMonths(v.group.months)} ต่อ ${baht(v.group.step_km)} กม.` : "ยังไม่ได้เลือกกลุ่มแผน" }),
      el("span", { class: "grow" }),
      v.has_plan ? el("button", { class: "btn small", type: "button", text: "+ บันทึกว่าทำแล้ว", onclick: () => openRecordDialog(v, attention.map((i) => i.key)) }) : null),
    !v.has_plan ? el("p", { class: "muted small", text: "ยังไม่ได้เลือกกลุ่มแผน — เปิดหน้ารถแล้วเลือกกลุ่ม (หรือปล่อยว่างถ้าไม่ต้องทำตาราง)" })
      : attention.length ? el("div", { class: "pl-chips" }, ...attention.map(plChip))
      : el("p", { class: "muted small", text: known ? `ปกติทุกรายการที่มีประวัติ (${known}/${v.items.length} รายการ)` : "ยังไม่มีประวัติเลย — กด “บันทึกว่าทำแล้ว” เพื่อเริ่มนับ" }),
    v.has_plan ? el("div", { class: "muted small", text: plKmPerDay(v.km_per_day) }) : null);
}

// ================================================================== the settings (second tab)
async function loadSettings() {
  try {
    pl.settings = await api("/api/plan/intervals");
  } catch (err) {
    showErr("plError", `โหลดตั้งค่าไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderSettings();
}

function plSettingMsg(msg, text) { msg.textContent = text; msg.className = "error small"; }

function renderSettings() {
  const data = pl.settings;
  if (!data) return;
  const groups = el("section", { class: "card plset" },
    el("h3", { text: "กลุ่มของรถ: ระยะกม. หนึ่งรอบ เท่ากับกี่เดือน" }),
    el("p", { class: "muted small", text: "รถแต่ละคันอยู่ในหนึ่งกลุ่ม (เลือกที่หน้ารถ) เดือนของทุกรายการคำนวณจากตรงนี้ เช่น 2 เดือน ต่อ 10,000 กม. แปลว่ารายการ 40,000 กม. ทำทุก 8 เดือน ใส่เดือน หรือ กม./วัน อย่างใดอย่างหนึ่ง (ถ้าใส่ทั้งสองอย่างใช้เดือน)" }),
    el("div", { class: "pl-grp-head small muted" }, el("span", { text: "กลุ่ม" }), el("span", { text: "เดือนต่อรอบ" }), el("span", { text: "หรือ กม./วัน" }), el("span", {})),
    ...data.groups.map(groupRow));
  const rules = Object.entries(data.rule_sets).map(([rule, items]) =>
    el("section", { class: "card plset" },
      el("h3", { text: `${rule}: ระยะกม. ของแต่ละรายการ` }),
      el("p", { class: "muted small", text: rule === "รถเล็ก" ? "D-Max, Revo" : "NLR / NMR / NPR, รถ 6 ล้อ, รถ 10 ล้อ (Hino / Isuzu)" }),
      ...items.map((it) => kmRow(rule, it))));
  fill($("plSetList"), groups, ...rules);
}

function groupRow(g) {
  const mo = el("input", { type: "number", inputmode: "decimal", min: "0", step: "0.5", value: g.months_per_step ?? "", "aria-label": `${g.code} เดือน` });
  const kd = el("input", { type: "number", inputmode: "decimal", min: "0", step: "1", value: g.km_per_day ?? "", "aria-label": `${g.code} กม./วัน` });
  const msg = el("span", { class: "muted small" });
  const save = async () => {
    try {
      pl.settings = await api("/api/plan/groups", jsonOpts("PUT", { code: g.code, months_per_step: mo.value, km_per_day: kd.value }));
    } catch (err) { plSettingMsg(msg, err.message); return; }
    renderSettings();
  };
  return el("div", { class: "pl-grp-row" },
    el("div", {}, el("strong", { text: `${g.code}` }), el("span", { text: ` ${g.label}` }),
      el("div", { class: "muted small", text: `${g.trucks} คัน · ตอนนี้ ${plMonths(g.months)} ต่อ ${baht(g.step_km)} กม. ≈ ${g.implied_km_per_day ? Math.round(g.implied_km_per_day) : "–"} กม./วัน` })),
    mo, kd, el("div", { class: "pl-set-actions" }, el("button", { class: "btn small", type: "button", text: "บันทึก", onclick: save }), msg));
}

function kmRow(rule, it) {
  const km = el("input", { type: "number", inputmode: "numeric", min: "0", step: "1000", value: it.interval_km ?? "", "aria-label": `${it.label} กม.` });
  const msg = el("span", { class: "muted small" });
  const save = async () => {
    try {
      pl.settings = await api("/api/plan/intervals", jsonOpts("PUT", { vehicle_class: rule, item_key: it.key, interval_km: km.value }));
    } catch (err) { plSettingMsg(msg, err.message); return; }
    renderSettings();
  };
  const reset = async () => {
    try {
      pl.settings = await api(`/api/plan/intervals?vehicle_class=${encodeURIComponent(rule)}&item_key=${it.key}`, { method: "DELETE" });
    } catch (err) { plSettingMsg(msg, err.message); return; }
    renderSettings();
  };
  return el("div", { class: `pl-km-row${it.customised ? " custom" : ""}` },
    el("div", {}, el("strong", { text: it.label }), el("div", { class: "muted small", text: `ค่าเริ่มต้น ${baht(it.default_km)} กม.` })),
    km, el("div", { class: "pl-set-actions" }, el("button", { class: "btn small", type: "button", text: "บันทึก", onclick: save }),
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
  const v = { id: d.vehicle.id, plate_number: d.vehicle.plate_number, vehicle_number: d.vehicle.vehicle_number, items: d.items };
  const head = el("div", { class: "mchead" }, el("h3", { class: "section-title", text: "แผนซ่อมบำรุง" }), el("span", { class: "grow" }),
    d.has_plan ? el("button", { class: "btn small", type: "button", text: "+ บันทึกว่าทำแล้ว", onclick: () => openRecordDialog(v, []) }) : null,
    el("button", { class: "btn small", type: "button", text: "+ ระยะทาง (GPS / เลขไมล์)", onclick: () => openReadingDialog(v) }));
  // The group decides every interval of this truck, so it is shown as plain text: changing it takes a button, a dialog and a confirmation.
  const groupBar = el("div", { class: "card pl-groupbar" },
    el("div", { class: "pl-groupinfo" },
      el("div", { class: "muted small", text: "กลุ่มของรถคันนี้" }),
      el("div", {}, d.group ? el("strong", { text: `${d.group.code} · ${d.group.label}` }) : el("strong", { class: "muted", text: "ยังไม่ได้เลือกกลุ่ม (ไม่ทำตาราง)" })),
      d.group ? el("div", { class: "muted small", text: `ทุกรายการนับ ${plMonths(d.group.months)} ต่อ ${baht(d.group.step_km)} กม. (อย่างไหนถึงก่อน)` }) : null),
    el("button", { class: "btn small", type: "button", text: d.group ? "เปลี่ยนกลุ่ม…" : "เลือกกลุ่ม…", onclick: () => openGroupDialog(d) }));
  if (!d.has_plan) {
    return [head, groupBar, el("p", { class: "muted", text: "ยังไม่ได้เลือกกลุ่ม จึงยังไม่มีตาราง — กด “เลือกกลุ่ม…” (หรือปล่อยว่างถ้ารถคันนี้ไม่ต้องทำตาราง)" })];
  }
  const odo = d.odometer ? ` · เลขไมล์ล่าสุด ${plKm(d.odometer.km)} (${beDate(d.odometer.date)})` : "";
  // History = the repair records themselves (bills and jobs recorded by hand): the same records as สมุดงานซ่อม, edited there.
  const history = d.history.length ? el("details", { class: "card pl-hist", open: true }, el("summary", { text: `ประวัติที่ทำแล้ว (${d.history.length})` }),
    el("p", { class: "muted small", text: "ทุกรายการอยู่ใน “สมุดงานซ่อม” ด้วย — กด “ดู / แก้ไข” เพื่อแก้วันที่ รายการ หรือลบ (ถ้ากดผิด)" }),
    ...d.history.map((h) => el("div", { class: "pl-hrow" },
      el("span", { text: beDate(h.done_date) }), el("strong", { text: h.label }),
      el("span", { class: "muted small", text: [h.odometer_km ? plKm(h.odometer_km) : null, plSourceText(h.source), h.notes].filter(Boolean).join(" · ") }),
      h.event_id ? el("a", { class: "btn small", href: `#event=${h.event_id}`, text: "ดู / แก้ไข" })
        : h.id ? el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: () => deleteRecord(h) }) : null))) : null;
  const readings = d.readings.length ? el("details", { class: "card pl-hist" }, el("summary", { text: `ข้อมูลระยะทาง (${d.readings.length})` }),
    ...d.readings.map((r) => el("div", { class: "pl-hrow" },
      el("span", { text: r.kind === "gps" ? `${beDate(r.start_date)} – ${beDate(r.end_date)}` : beDate(r.end_date) }),
      el("strong", { text: r.kind === "gps" ? `GPS ${plKm(r.km)}` : `เลขไมล์ ${plKm(r.km)}` }),
      el("span", { class: "muted small", text: r.notes || "" }),
      el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: () => deleteReading(r) })))) : null;
  return [head, groupBar, el("p", { class: "muted small", text: `ระยะทางต่อวัน: ${plKmPerDay(d.km_per_day)}${odo}` }), planChart(d), history, readings];
}

// The Isuzu-style chart: columns = the latest event, then the next rounds; rows = the items.
const PL_CELL = { done: ["✓", "pl-done", "ทำไปแล้วในครั้งล่าสุด"], due: ["●", "pl-due", "ถึงกำหนดในรอบนี้"], overdue: ["●", "pl-over", "เลยกำหนดแล้ว ควรทำในครั้งถัดไป"], unknown: ["?", "pl-unk", "ไม่ทราบว่าเคยทำเมื่อไหร่ ควรตรวจ"] };

function planChart(d) {
  const c = d.chart;
  if (!c) {
    return el("div", { class: "card pl-empty" },
      el("strong", { text: "ยังไม่มีประวัติ" }),
      el("p", { class: "muted", text: "ยังไม่ได้ทำ / ยังไม่มีบันทึกของรถคันนี้ — พอมีบิลหรือกด “บันทึกว่าทำแล้ว” ตารางจะขึ้นเองและขยับตามทุกครั้งที่บันทึกเพิ่ม" }));
  }
  const heads = c.columns.map((col) => el("th", { class: `pl-ch${col.n === 0 ? " latest" : ""}` },
    el("div", { class: "pl-ch-label", text: col.label }),
    el("div", { class: "pl-ch-date small", text: beDate(col.date) }),
    el("div", { class: "pl-ch-km small", text: col.km !== null && col.km !== undefined ? `${baht(col.km)} กม.` : (col.n === 0 ? "ไม่ทราบเลขไมล์" : "") })));
  const body = c.rows.map((r) => el("tr", { class: r.state },
    el("th", { class: "pl-rowh", scope: "row" }, el("div", { class: "pl-rowname", text: r.label }),
      el("div", { class: "muted small", text: `ทุก ${[r.interval_km ? plKm(r.interval_km) : null, r.interval_months ? plMonths(r.interval_months) : null].filter(Boolean).join(" / ")}` })),
    el("td", { class: "pl-last" }, r.last_date ? beDate(r.last_date) : "–", r.last_date ? el("div", { class: "muted small", text: plSourceText(r.last_source) }) : null),
    ...r.cells.map((cell) => {
      const spec = cell ? PL_CELL[cell] : null;
      return el("td", { class: `pl-cell${spec ? ` ${spec[1]}` : ""}`, title: spec ? `${r.label}: ${spec[2]}` : "", text: spec ? spec[0] : "" });
    })));
  return el("div", { class: "card pl-chartbox" },
    el("div", { class: "pl-chartscroll" },
      el("table", { class: "pl-chart" },
        el("thead", {}, el("tr", {}, el("th", { class: "pl-rowh", text: "รายการ" }), el("th", { class: "pl-ch", text: "ทำล่าสุด" }), ...heads)),
        el("tbody", {}, ...body))),
    el("div", { class: "pl-legend small muted" },
      el("span", {}, el("b", { class: "pl-done", text: "✓" }), " ทำในครั้งล่าสุด"),
      el("span", {}, el("b", { class: "pl-due", text: "●" }), " ถึงกำหนดในรอบนั้น"),
      el("span", {}, el("b", { class: "pl-over", text: "●" }), " เลยกำหนดแล้ว"),
      el("span", {}, el("b", { class: "pl-unk", text: "?" }), " ไม่ทราบประวัติ"),
      el("span", { text: "แต่ละรอบห่างกันตามกลุ่มของรถ · พอใส่เลขไมล์ จะขึ้นกม. ของแต่ละรอบให้" })));
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

// ================================================================== dialogs
async function plAfterSave() {
  $("planDialog").close();
  if (!$("planView").hidden) await loadOverview();
  await plRefreshTruck();
}

function plOpenDialog(title, saveText) {
  $("pdlgTitle").textContent = title;
  $("pdlgSave").textContent = saveText;
  $("pdlgSave").disabled = false;
  showErr("pdlgError", "");
  $("planDialog").showModal();
}

function openRecordDialog(v, preset = []) {
  const items = v.items || [];
  pl.dlg = { kind: "record", vehicle: v, draft: { done_date: plToday(), odometer_km: "", performed_by: "", notes: "" }, picked: new Set(preset) };
  const boxes = [];
  const all = el("input", { type: "checkbox", id: "plp_all" });
  const syncAll = () => { all.checked = boxes.length > 0 && boxes.every((b) => b.checked); };
  all.addEventListener("change", () => {
    for (const b of boxes) { b.checked = all.checked; if (all.checked) pl.dlg.picked.add(b.dataset.key); else pl.dlg.picked.delete(b.dataset.key); }
  });
  const picks = el("div", { class: "pl-picks" }, ...items.map((i) => {
    const box = el("input", { type: "checkbox", id: `plp_${i.key}`, "data-key": i.key });
    box.checked = pl.dlg.picked.has(i.key);
    box.addEventListener("change", () => { if (box.checked) pl.dlg.picked.add(i.key); else pl.dlg.picked.delete(i.key); syncAll(); });
    boxes.push(box);
    return el("label", { class: "pl-pick", for: `plp_${i.key}` }, box, el("span", { text: i.label }),
      i.state === "overdue" || i.state === "soon" ? el("span", { class: `badge ${PL_BADGE[i.state]}`, text: PL_STATE_TEXT[i.state] }) : null);
  }));
  syncAll();
  fill($("pdlgBody"),
    el("p", { class: "muted small", text: `${plLabel(v)} — เลือกสิ่งที่ทำ แล้วใส่วันที่ที่ทำจริง รายการนี้จะไปอยู่ใน “สมุดงานซ่อม” ด้วย และแก้ไขหรือลบได้ที่นั่น` }),
    el("label", { class: "pl-pick pl-pickall", for: "plp_all" }, all, el("strong", { text: "เลือกทั้งหมด" })),
    picks,
    el("div", { class: "mfields" },
      mfield(pl.dlg.draft, "done_date", "วันที่ทำ", { type: "date", caption: true }),
      mfield(pl.dlg.draft, "odometer_km", "เลขไมล์ (กม.) ถ้ามี", { type: "number" }),
      mfield(pl.dlg.draft, "performed_by", "ผู้ทำ / ร้าน (ถ้ามี)"),
      mfield(pl.dlg.draft, "notes", "หมายเหตุ")));
  plOpenDialog("บันทึกว่าทำแล้ว", "บันทึก");
}

// Changing a truck's group changes every interval of that truck, so it is a deliberate step: pick, read the warning, confirm.
function openGroupDialog(d) {
  pl.dlg = { kind: "group", vehicle: d.vehicle, draft: { group_code: d.group_code || "" }, current: d.group_code || "" };
  const sel = el("select", { id: "plGroupSel", "aria-label": "กลุ่มใหม่" },
    el("option", { value: "", text: "— ไม่ทำตาราง —" }),
    ...d.groups.map((g) => el("option", { value: g.code, text: `${g.code} · ${g.label}` })));
  sel.value = pl.dlg.current;
  const save = $("pdlgSave");
  sel.addEventListener("change", () => { pl.dlg.draft.group_code = sel.value; save.disabled = sel.value === pl.dlg.current; });
  fill($("pdlgBody"),
    el("p", { class: "muted small", text: plLabel({ ...d.vehicle }) }),
    el("div", { class: "edit-banner", text: "เปลี่ยนกลุ่มแล้ว รอบเวลาของทุกรายการของรถคันนี้จะคำนวณใหม่ทั้งหมด ถ้าไม่แน่ใจ ให้กดยกเลิก" }),
    el("div", { class: "mfield" }, el("label", { for: "plGroupSel", text: "กลุ่มใหม่" }), sel));
  plOpenDialog(d.group_code ? "เปลี่ยนกลุ่มของรถคันนี้" : "เลือกกลุ่มของรถคันนี้", "ยืนยันเปลี่ยนกลุ่ม");
  save.disabled = true;                              // nothing to confirm until a different group is picked
}

function openReadingDialog(v) {
  pl.dlg = { kind: "reading", vehicle: v, draft: { kind: "gps", start_date: "", end_date: plToday(), km: "", notes: "" } };
  const d = pl.dlg.draft;
  const gpsOnly = el("div", { class: "mfields" }, mfield(d, "start_date", "ตั้งแต่วันที่", { type: "date", caption: true }));
  const draw = () => {
    gpsOnly.hidden = d.kind !== "gps";
    $("mf_end_date").previousSibling.textContent = d.kind === "gps" ? "ถึงวันที่" : "วันที่อ่านเลขไมล์";
    $("mf_km").previousSibling.textContent = d.kind === "gps" ? "ระยะทางที่วิ่งในช่วงนี้ (กม.)" : "เลขไมล์ (กม.)";
  };
  fill($("pdlgBody"),
    el("p", { class: "muted small", text: `${plLabel(v)} — ระยะทางจากแอป GPS (รวมทั้งช่วง) หรือเลขไมล์จากหน้าปัด ถ้ามี ระบบจะใช้บอกกม. ของแต่ละรอบ และเตือนก่อนถ้ารถวิ่งมากกว่าที่กลุ่มสมมติไว้` }),
    el("div", { class: "mfields" }, mselect(d, "kind", "ชนิดข้อมูล", [["gps", "ระยะทางจาก GPS (ทั้งช่วง)"], ["odometer", "เลขไมล์จากหน้าปัด"]], { blank: "—", onChange: draw })),
    gpsOnly,
    el("div", { class: "mfields" }, mfield(d, "end_date", "ถึงวันที่", { type: "date", caption: true }), mfield(d, "km", "ระยะทาง (กม.)", { type: "number" }), mfield(d, "notes", "หมายเหตุ", { wide: true })));
  draw();
  plOpenDialog("บันทึกระยะทางที่วิ่ง", "บันทึก");
}

async function savePlanDialog() {
  const dlg = pl.dlg;
  const btn = $("pdlgSave");
  if (!dlg || btn.disabled) return;
  const d = dlg.draft;
  btn.disabled = true;                               // one click, one save
  try {
    if (dlg.kind === "record") {
      await api("/api/plan/services", jsonOpts("POST", { vehicle_id: dlg.vehicle.id, items: [...dlg.picked], done_date: d.done_date, odometer_km: d.odometer_km,
        performed_by: d.performed_by, notes: d.notes }));
    } else if (dlg.kind === "group") {
      await api(`/api/plan/vehicles/${dlg.vehicle.id}/group`, jsonOpts("PUT", { group_code: d.group_code }));
    } else {
      await api("/api/plan/readings", jsonOpts("POST", { vehicle_id: dlg.vehicle.id, kind: d.kind, start_date: d.kind === "gps" ? d.start_date : "", end_date: d.end_date, km: d.km, notes: d.notes }));
    }
  } catch (err) {
    showErr("pdlgError", err.message);
    btn.disabled = false;
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
