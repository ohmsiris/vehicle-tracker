"use strict";

// แผนซ่อมบำรุงรถ: the vehicle maintenance programme.
//   #plan        every truck: what is overdue / due soon, and (second tab) the settings: months per group, km per item
//   a truck page shows that truck's own plan underneath its details (renderTruckPlan, called from roster.js):
//   its group, an Isuzu-style chart (what the latest event did, what is overdue / unknown, the next rounds) and its history
// Lists are read-only; recording that something was done, or how far a truck drives, happens in a dialog.
// Data: /api/plan/* (app/service_plan.py). Loaded BEFORE app.js: nothing here runs at load time.

const pl = {
  opts: { ready: true, rule_sets: [], items: [], soon_days: 10 },
  overview: null, tab: "due", settings: null, reminder: null,
  simDays: 0, simTimer: null,                  // the "look ahead" slider: how many days from today
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

// The day the page is looking at: today, or (slider) some days ahead. Nothing is saved by looking ahead.
const plAsOf = () => (pl.simDays > 0 ? addDaysISO(plToday(), pl.simDays) : "");
const plAsOfQuery = (sep = "?") => (pl.simDays > 0 ? `${sep}as_of=${plAsOf()}` : "");

function plChip(i, v) {
  const when = i.days_left < 0 ? `เลย ${-i.days_left} วัน` : i.days_left === 0 ? "วันนี้" : `อีก ${i.days_left} วัน`;
  if (i.snoozed_until) return el("span", { class: "badge", title: "เลื่อนเตือนอยู่", text: `💤 ${i.label} · เลื่อนเตือนถึง ${beDate(i.snoozed_until)}` });
  if (!v) return el("span", { class: `badge ${PL_BADGE[i.state]}`.trim(), title: plDueText(i), text: `${i.label} · ${when}` });
  return el("button", { class: `badge ${PL_BADGE[i.state]} plchip-btn`.trim(), type: "button", title: `${plDueText(i)} — กดเพื่อเลื่อนเตือน`, text: `${i.label} · ${when}`, onclick: () => openSnoozeDialog(v, i) });
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
  if (pl.simDays > 0) p.set("as_of", plAsOf());
  try {
    pl.overview = await api(`/api/plan/overview?${p}`);
  } catch (err) {
    showErr("plError", `โหลดแผนซ่อมบำรุงไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderOverview();
  if ($("plPreview").open) loadPreview();
}

// ---- look ahead: slide the day forward and see which trucks fall due, and what the reminder would say that day
function setSim(days) {
  pl.simDays = days;
  $("plSim").value = String(days);
  $("plSimLabel").textContent = days === 0 ? "วันนี้" : `อีก ${days} วัน · ${beDate(plAsOf())} — จำลองเฉยๆ ไม่ได้บันทึกอะไร`;
  $("plSimLabel").classList.toggle("warn", days > 0);
  $("plSimReset").hidden = days === 0;
  clearTimeout(pl.simTimer);
  pl.simTimer = setTimeout(loadOverview, 250);
}

async function loadPreview() {
  fill($("plPreviewText"), "กำลังคำนวณ…");
  let r;
  try {
    r = await api(`/api/plan/reminders${plAsOfQuery()}`);
  } catch (err) {
    fill($("plPreviewText"), `โหลดไม่สำเร็จ: ${err.message}`);
    return;
  }
  fill($("plPreviewText"), r.text || `ไม่มีรายการที่ต้องเตือนในวันนั้น (เตือนเฉพาะที่เหลือไม่เกิน ${r.soon_days} วัน)`);
  fill($("plPreviewMeta"), `${r.items.length} รายการที่จะเตือน${r.snoozed.length ? ` · ที่เลื่อนเตือนไว้ ${r.snoozed.length}` : ""} · ${r.enabled ? "เปิดเตือนอยู่" : "ปิดการเตือนอยู่"} — ตัวเลขข้างเมนู “แผนซ่อมบำรุง” นับรายการเหล่านี้ (ไม่ส่ง LINE)`);
}

function renderOverview() {
  const o = pl.overview;
  if (!o) return;
  const t = o.totals, k = o.trucks;
  fill($("plKpis"),
    plKpi("รถที่มีรายการเลยกำหนด", k.overdue, `${t.overdue} รายการ`, k.overdue ? "bad" : ""),
    plKpi("รถที่ใกล้ถึงกำหนด", k.soon, `ภายใน ${o.soon_days || pl.opts.soon_days} วัน · ${t.soon} รายการ`, k.soon ? "warn" : ""),
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
  fill($("plCount"), `${rows.length} คัน${nothing ? ` — ไม่มีรถที่ต้องทำตอนนี้${k.no_history ? ` · แต่ยังมี ${k.no_history} คันที่ไม่มีประวัติเลย (เลือก “ที่ยังไม่มีประวัติเลย” เพื่อดู)` : ""}` : ""}${k.no_group ? ` · ${k.no_group} คันยังไม่ได้เลือกกลุ่มแผน (เลือกได้ที่แท็บ “ตั้งค่าระยะ”)` : ""}`);
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
    !v.has_plan ? el("p", { class: "muted small" }, "ยังไม่ได้เลือกกลุ่มแผน — เลือกได้ที่ ", el("a", { href: "#plan-settings", text: "ตั้งค่าระยะ › รถแต่ละคันใช้กลุ่มไหน" }), " (หรือปล่อยว่างถ้าไม่ต้องทำตาราง)")
      : attention.length ? el("div", { class: "pl-chips" }, ...attention.map((i) => plChip(i, v)))
      : el("p", { class: "muted small", text: known ? `ปกติทุกรายการที่มีประวัติ (${known}/${v.items.length} รายการ)` : "ยังไม่มีประวัติเลย — กด “บันทึกว่าทำแล้ว” เพื่อเริ่มนับ" }),
    v.has_plan ? el("div", { class: "muted small", text: plKmPerDay(v.km_per_day) }) : null);
}

// ================================================================== the settings (second tab)
async function loadSettings() {
  try {
    [pl.settings, pl.reminder] = await Promise.all([api("/api/plan/intervals"), api("/api/plan/reminder-settings")]);
  } catch (err) {
    showErr("plError", `โหลดตั้งค่าไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderSettings();
}

// the reminders: how many days ahead "coming close" starts, and which items are reminded about by LINE
function reminderCard() {
  const r = pl.reminder;
  if (!r) return null;
  const enabled = el("input", { type: "checkbox", id: "plrOn" });
  enabled.checked = !!r.enabled;
  const soon = el("input", { type: "number", id: "plrSoon", min: "1", max: "60", step: "1", value: String(r.soon_days) });
  const all = el("input", { type: "checkbox" });
  const boxes = r.all_items.map((it) => {
    const b = el("input", { type: "checkbox", "data-key": it.key });
    b.checked = r.items.includes(it.key);
    b.addEventListener("change", () => { all.checked = boxes.every((l) => l.querySelector("input").checked); });
    return el("label", { class: "pl-pick" }, b, el("span", { text: it.label }));
  });
  all.checked = boxes.every((l) => l.querySelector("input").checked);
  all.addEventListener("change", () => { for (const l of boxes) l.querySelector("input").checked = all.checked; });
  const msg = el("span", { class: "muted small" });
  return el("section", { class: "card plset" },
    el("h3", { text: "เตือนรอบซ่อมบำรุง" }),
    el("p", { class: "muted small", text: "เตือน 3 ขั้น: เหลือไม่เกิน N วัน → ถึงวันครบกำหนด → เลยกำหนด แสดงเป็นตัวเลขข้างเมนู “แผนซ่อมบำรุง” จนกว่าจะทำหรือกด “เลื่อนเตือน” — ลองดูล่วงหน้าได้ที่แถบเลื่อนในหน้าแผนซ่อมบำรุง" }),
    el("label", { class: "inline" }, enabled, "เปิดการเตือนรอบซ่อมบำรุง"),
    el("div", { class: "mgset-time" }, el("label", { for: "plrSoon", text: "เริ่มเตือนเมื่อเหลือไม่เกิน (วัน)" }), soon),
    el("p", { class: "muted small", text: "เตือนเรื่องอะไรบ้าง (ปกติเตือนทุกเรื่อง)" }),
    el("label", { class: "pl-pick pl-pickall" }, all, el("strong", { text: "เลือกทั้งหมด" })),
    el("div", { class: "pl-picks" }, ...boxes),
    el("div", { class: "pl-set-actions" }, el("button", { class: "btn small primary", type: "button", text: "บันทึก", onclick: async () => {
      try {
        pl.reminder = await api("/api/plan/reminder-settings", jsonOpts("PUT", { enabled: enabled.checked, soon_days: soon.value,
          items: boxes.map((l) => l.querySelector("input")).filter((b) => b.checked).map((b) => b.dataset.key) }));
        pl.opts.soon_days = pl.reminder.soon_days;
        msg.textContent = "บันทึกแล้ว"; msg.className = "muted small";
      } catch (err) { msg.textContent = err.message; msg.className = "error small"; }
    } }), msg));
}

function plSettingMsg(msg, text) { msg.textContent = text; msg.className = "error small"; }

function renderSettings() {
  const data = pl.settings;
  if (!data) return;
  const groups = el("section", { class: "card plset pl-wide" },
    el("h3", { text: "กลุ่มของรถ: ระยะกม. หนึ่งรอบ เท่ากับกี่เดือน" }),
    el("p", { class: "muted small", text: "รถแต่ละคันอยู่ในหนึ่งกลุ่ม (เลือกได้ที่ “รถแต่ละคันใช้กลุ่มไหน” ด้านล่าง) เดือนของทุกรายการคำนวณจากตรงนี้ เช่น 2 เดือน ต่อ 10,000 กม. แปลว่ารายการ 40,000 กม. ทำทุก 8 เดือน ใส่เดือน หรือ กม./วัน อย่างใดอย่างหนึ่ง (ถ้าใส่ทั้งสองอย่างใช้เดือน)" }),
    el("div", { class: "pl-grp-head small muted" }, el("span", { text: "กลุ่ม" }), el("span", { text: "เดือนต่อรอบ" }), el("span", { text: "หรือ กม./วัน" }), el("span", {})),
    ...data.groups.map(groupRow));
  const rules = Object.entries(data.rule_sets).map(([rule, items]) =>
    el("section", { class: "card plset" },
      el("h3", { text: `${rule}: ระยะกม. ของแต่ละรายการ` }),
      el("p", { class: "muted small", text: rule === "รถเล็ก" ? "D-Max, Revo" : "NLR / NMR / NPR, รถ 6 ล้อ, รถ 10 ล้อ (Hino / Isuzu)" }),
      ...items.map((it) => kmRow(rule, it)),
      el("div", { class: "pl-add-row" }, el("button", { class: "btn small", type: "button", text: "+ เพิ่มรายการ", onclick: () => openOwnItemDialog(null, rule) }))));
  fill($("plSetList"), reminderCard(), groups, ...rules, trucksCard());
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
  const reset = async () => {
    try {
      pl.settings = await api(`/api/plan/groups?code=${encodeURIComponent(g.code)}`, { method: "DELETE" });
    } catch (err) { plSettingMsg(msg, err.message); return; }
    renderSettings();
  };
  const def = g.default || {};
  const defText = def.months_per_step ? `${plMonths(def.months_per_step)}` : def.km_per_day ? `${def.km_per_day} กม./วัน` : "";
  return el("div", { class: `pl-grp-row${g.customised ? " custom" : ""}` },
    el("div", {}, el("strong", { text: `${g.code}` }), el("span", { text: ` ${g.label}` }),
      el("div", { class: "muted small", text: `${g.trucks} คัน · ตอนนี้ ${plMonths(g.months)} ต่อ ${baht(g.step_km)} กม. ≈ ${g.implied_km_per_day ? Math.round(g.implied_km_per_day) : "–"} กม./วัน${defText ? ` · ค่าเริ่มต้น ${defText}` : ""}` }),
      g.oil_km ? el("div", { class: "small", text: `→ น้ำมันเครื่องของกลุ่มนี้ทุก ${baht(g.oil_km)} กม. ≈ ${plMonths(g.oil_months === null ? null : Math.round(g.oil_months * 10) / 10)} (เปลี่ยนกม.ได้ที่รายการด้านล่าง)` }) : null),
    mo, kd, el("div", { class: "pl-set-actions" }, el("button", { class: "btn small", type: "button", text: "บันทึก", onclick: save }),
      g.customised ? el("button", { class: "btn small", type: "button", text: "ค่าเริ่มต้น", onclick: reset }) : null, msg));
}

// Items we add ourselves (a timing belt, the battery, the fire extinguisher): added at the bottom of the รถเล็ก / รถใหญ่ list they
// belong to; they join every matching truck's plan table and reminders like the built-in ones, and bills find them by their words.
function openOwnItemDialog(row, rule = null) {
  const data = pl.settings;
  // Added from the รถเล็ก / รถใหญ่ list = for that kind of truck only, so the form just asks "how many km" and "how many months".
  // (The choice stays in "ตัวเลือกเพิ่มเติม" for an item that is for both.)
  const scope = row ? (row.applies_to || "all") : rule === "รถเล็ก" ? "small" : rule === "รถใหญ่" ? "big" : "all";
  const kmOf = (r) => (r ? (scope === "big" ? r.km_big : scope === "small" ? r.km_small : (r.km_small || r.km_big)) : "") || "";
  pl.dlg = { kind: "ownitem", key: row ? row.key : null, draft: {
    scope, label: row ? row.label : "", group_name: row ? row.group_name : "อื่นๆ", km: kmOf(row),
    months: row && row.months ? Number(row.months) : "", words: row ? (row.words || []).join(", ") : "", category: row ? row.category : "อื่นๆ" } };
  const d = pl.dlg.draft;
  const forWhom = { small: "รถเล็ก (D-Max, Revo)", big: "รถใหญ่ (NLR, 6 / 10 ล้อ)", all: "รถเล็กและรถใหญ่" };
  const who = el("p", { class: "muted small", id: "ownWho" });
  const showWho = () => { who.textContent = `ใช้กับ: ${forWhom[d.scope]}`; };
  showWho();
  fill($("pdlgBody"),
    who,
    el("div", { class: "mfields" },
      mfield(d, "label", "ชื่อรายการ", { wide: true, maxlength: 80, placeholder: "เช่น สายพานไทม์มิ่ง" }),
      mfield(d, "km", "ทุกกี่ กม.", { type: "number", placeholder: "เช่น 100000" }),
      mfield(d, "months", "หรือทุกกี่เดือน", { type: "number", placeholder: "เช่น 12" }),
      mfield(d, "words", "คำในบิลที่นับว่าทำแล้ว (คั่นด้วย ,)", { wide: true, maxlength: 400, placeholder: "ชื่อรายการนับอยู่แล้ว เช่น ไทม์มิ่ง, timing belt" })),
    el("p", { class: "muted small", text: "ใส่กม. หรือเดือน อย่างใดอย่างหนึ่งก็ได้ ถ้าใส่ทั้งสองอย่างจะนับอย่างไหนถึงก่อน (ใส่กม. อย่างเดียว เดือนคิดจากกลุ่มของรถแต่ละคัน)" }),
    el("details", { class: "pl-more" }, el("summary", { class: "small", text: "ตัวเลือกเพิ่มเติม (ใช้กับรถกลุ่มไหน · กลุ่มในตาราง · หมวดของบิล)" }),
      el("div", { class: "mfields" },
        mselect(d, "scope", "ใช้กับ", [["small", forWhom.small], ["big", forWhom.big], ["all", forWhom.all]], { blank: forWhom.all, onChange: showWho }),
        mselect(d, "group_name", "อยู่ในกลุ่มไหนของตาราง", (data.item_groups || []).map((g) => [g, g]), { blank: "อื่นๆ" }),
        mselect(d, "category", "หมวดของบิลเมื่อกด “บันทึกว่าทำแล้ว”", (data.categories || []).map((c) => [c, c]), { blank: "อื่นๆ" }))));
  plOpenDialog(row ? "แก้ไขรายการ" : `เพิ่มรายการ — ${rule || "รถทุกกลุ่ม"}`, "บันทึก");
  setTimeout(() => { const f = $("mf_label"); if (f && !row) f.focus(); }, 50);
}

async function removeOwnItem(row) {
  if (!confirm(`เอา “${row.label}” ออกจากแผนใช่ไหม?\n(ประวัติที่ทำไปแล้วยังอยู่ ตารางและการเตือนจะไม่มีรายการนี้อีก)`)) return;
  try { pl.settings = await api(`/api/plan/custom-items/${row.key}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
  await loadSettings();
}

// Which truck follows which group. Picking only marks the row; nothing changes until "บันทึก" and a confirmation,
// because the group decides every interval of the truck.
function trucksCard() {
  const data = pl.settings;
  if (!data.vehicles || !data.vehicles.length) return null;
  const staged = new Map();                                       // vehicle id -> new group code ("" = no plan)
  const byCode = Object.fromEntries(data.groups.map((g) => [g.code, g]));
  const name = (code) => (code ? `${code}${byCode[code] ? ` ${byCode[code].label}` : ""}` : "ไม่ทำตาราง");
  const msg = el("span", { class: "muted small" });
  const saveBtn = el("button", { class: "btn small primary", type: "button", text: "บันทึก", disabled: true });
  const undoBtn = el("button", { class: "btn small", type: "button", text: "ยกเลิกที่แก้", hidden: true });
  const rows = [];
  const sync = () => {
    saveBtn.disabled = staged.size === 0;
    saveBtn.textContent = staged.size ? `บันทึกการเปลี่ยนกลุ่ม (${staged.size} คัน)` : "บันทึก";
    undoBtn.hidden = staged.size === 0;
  };
  const list = el("div", { class: "pl-trucks" });
  let branch = null;
  for (const v of data.vehicles) {
    if (v.branch !== branch) { branch = v.branch; list.append(el("div", { class: "pl-trucks-branch muted small", text: branch || "ไม่ระบุสาขา" })); }
    const sel = el("select", { "aria-label": `กลุ่มของ ${plLabel(v)}` },
      el("option", { value: "", text: "— ไม่ทำตาราง —" }), ...data.groups.map((g) => el("option", { value: g.code, text: `${g.code} ${g.label}` })));
    sel.value = v.group_code || "";
    const row = el("label", { class: "pl-truck" }, el("span", { class: "pl-truck-name", text: plLabel(v) || "–" }), sel);
    sel.addEventListener("change", () => {
      if (sel.value === (v.group_code || "")) staged.delete(v.id); else staged.set(v.id, sel.value);
      row.classList.toggle("changed", staged.has(v.id));
      sync();
    });
    rows.push({ v, row, sel });
    list.append(row);
  }
  undoBtn.addEventListener("click", () => {
    for (const { v, row, sel } of rows) { sel.value = v.group_code || ""; row.classList.remove("changed"); }
    staged.clear();
    sync();
  });
  saveBtn.addEventListener("click", async () => {
    const changes = [...staged].map(([id, code]) => ({ id, code, v: data.vehicles.find((x) => x.id === id) }));
    const lines = changes.slice(0, 15).map((c) => `• ${plLabel(c.v)}: ${name(c.v.group_code)} → ${name(c.code)}`);
    if (changes.length > 15) lines.push(`และอีก ${changes.length - 15} คัน`);
    if (!confirm(`เปลี่ยนกลุ่มของรถ ${changes.length} คัน\n\n${lines.join("\n")}\n\nรอบเวลาของทุกรายการของรถเหล่านี้จะคำนวณใหม่ทั้งหมด ยืนยันไหม?`)) return;
    saveBtn.disabled = true;
    try {
      pl.settings = await api("/api/plan/vehicle-groups", jsonOpts("PUT", { changes: changes.map((c) => ({ vehicle_id: c.id, group_code: c.code })) }));
    } catch (err) { plSettingMsg(msg, err.message); saveBtn.disabled = false; return; }
    if (typeof loadToday === "function") loadToday(true);
    renderSettings();
  });
  return el("section", { class: "card plset pl-trucks-card" },
    el("h3", { text: "รถแต่ละคันใช้กลุ่มไหน" }),
    el("p", { class: "muted small", text: "เลือกกลุ่มของแต่ละคัน แถวที่แก้จะเป็นสีเหลือง ยังไม่เปลี่ยนจริงจนกว่าจะกด “บันทึกการเปลี่ยนกลุ่ม” และยืนยัน" }),
    list, el("div", { class: "pl-set-actions pl-trucks-actions" }, saveBtn, undoBtn, msg));
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
  const row = it.own ? (pl.settings.own_items || []).find((r) => r.key === it.key) : null;
  const ownButtons = row ? [el("button", { class: "btn small", type: "button", text: "แก้ไข", onclick: () => openOwnItemDialog(row, rule) }),
    el("button", { class: "btn small danger", type: "button", text: "เอาออก", onclick: () => removeOwnItem(row) })] : [];
  if (it.default_km === null) {                           // our own item counted in months only (applies to every truck): edit / remove it here
    return el("div", { class: "pl-km-row own-months" }, el("div", {}, el("strong", { text: it.label }), el("div", { class: "muted small", text: `ทุก ${plMonths(it.months)}` })), el("span", {}), el("div", { class: "pl-set-actions" }, ...ownButtons));
  }
  return el("div", { class: `pl-km-row${it.customised ? " custom" : ""}` },
    el("div", {}, el("strong", { text: it.label }), el("div", { class: "muted small", text: `ค่าเริ่มต้น ${baht(it.default_km)} กม.${it.months ? ` · ทุก ${plMonths(it.months)} ด้วย` : ""}` })),
    km, el("div", { class: "pl-set-actions" }, el("button", { class: "btn small", type: "button", text: "บันทึก", onclick: save }),
      it.customised ? el("button", { class: "btn small", type: "button", text: "คืนค่าเริ่มต้น", onclick: reset }) : null, ...ownButtons, msg));
}

// ================================================================== one truck's plan
async function renderTruckPlan(v, editing) {
  const box = $("tkPlan");
  if (!v || editing || !v.vehicle_id) { box.hidden = true; fill(box); return; }
  box.hidden = false;
  fill(box, el("p", { class: "muted small", text: "กำลังโหลดแผนซ่อมบำรุง…" }));
  let d;
  try {
    d = await api(`/api/plan/vehicles/${v.vehicle_id}${plAsOfQuery()}`);
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
  // The group decides every interval of this truck: shown here, changed on the plan's settings (all trucks in one place, with a confirmation).
  const groupBar = el("div", { class: "card pl-groupbar" },
    el("div", { class: "pl-groupinfo" },
      el("div", { class: "muted small", text: "กลุ่มของรถคันนี้" }),
      el("div", {}, d.group ? el("strong", { text: `${d.group.code} · ${d.group.label}` }) : el("strong", { class: "muted", text: "ยังไม่ได้เลือกกลุ่ม (ไม่ทำตาราง)" })),
      d.group ? el("div", { class: "muted small", text: `ทุกรายการนับ ${plMonths(d.group.months)} ต่อ ${baht(d.group.step_km)} กม. (อย่างไหนถึงก่อน)` }) : null),
    el("a", { class: "muted small", href: "#plan-settings", text: "เปลี่ยนกลุ่มได้ที่ แผนซ่อมบำรุง › ตั้งค่าระยะ" }));
  if (!d.has_plan) {
    return [head, groupBar, el("p", { class: "muted", text: "ยังไม่ได้เลือกกลุ่ม จึงยังไม่มีตาราง — เลือกได้ที่ แผนซ่อมบำรุง › ตั้งค่าระยะ (หรือปล่อยว่างถ้ารถคันนี้ไม่ต้องทำตาราง)" })];
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
  const simBanner = d.simulated ? el("div", { class: "edit-banner", text: `กำลังดูล่วงหน้า ณ ${beDate(d.as_of)} (จำลองเฉยๆ ไม่ได้บันทึกอะไร) — เลื่อนแถบที่หน้า “แผนซ่อมบำรุง” กลับมาวันนี้เพื่อเลิกจำลอง` }) : null;
  return [head, simBanner, groupBar, el("p", { class: "muted small", text: `ระยะทางต่อวัน: ${plKmPerDay(d.km_per_day)}${odo}` }), planCalc(d), planChart(d), history, readings];
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
  const noHistory = c.no_history ? el("div", { class: "edit-banner" }, "ยังไม่มีประวัติของรถคันนี้ — ตารางนับรอบจากวันนี้ ช่อง ? คือไม่ทราบว่าแต่ละรายการเคยทำเมื่อไหร่ (ควรตรวจ) ส่วนรอบถัดไปบอกว่าแต่ละรอบต้องทำอะไร พอมีบิลหรือกด “+ บันทึกว่าทำแล้ว” ตารางจะนับจากวันที่ทำจริง") : null;
  const body = c.rows.map((r) => el("tr", { class: r.state },
    el("th", { class: "pl-rowh", scope: "row" }, el("div", { class: "pl-rowname", text: r.label }),
      el("div", { class: "muted small", text: `ทุก ${[r.interval_km ? plKm(r.interval_km) : null, r.interval_months ? plMonths(r.interval_months) : null].filter(Boolean).join(" / ")}` })),
    el("td", { class: "pl-last" }, r.last_date ? beDate(r.last_date) : "–", r.last_date ? el("div", { class: "muted small", text: plSourceText(r.last_source) }) : null),
    ...r.cells.map((cell) => {
      const spec = cell ? PL_CELL[cell] : null;
      return el("td", { class: `pl-cell${spec ? ` ${spec[1]}` : ""}`, title: spec ? `${r.label}: ${spec[2]}` : "", text: spec ? spec[0] : "" });
    })));
  return el("div", { class: "card pl-chartbox" }, noHistory,
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
  if (typeof loadToday === "function") loadToday(true);
  if (!$("planView").hidden) await loadOverview();
  await plRefreshTruck();
}

// ---- the calculator: for each item how much of its interval is used, and how many km / days are left
function planCalc(d) {
  const rows = d.items.filter((i) => i.progress !== null && i.progress !== undefined).sort((a, b) => b.progress - a.progress);
  if (!rows.length) return null;
  return el("div", { class: "card plcalc" },
    el("h4", { class: "mgh", text: "เหลืออีกเท่าไหร่ถึงรอบ" }),
    el("p", { class: "muted small", text: `แถบเต็ม = ถึงรอบ · เหลือง = เหลือไม่เกิน ${d.soon_days} วัน · แดง = เลยกำหนด · กม. ที่มี ≈ เป็นค่าประมาณจากกลุ่มของรถ (ใส่เลขไมล์จะแม่นขึ้น)` }),
    ...rows.map((i) => calcRow(d, i)));
}

function calcRow(d, i) {
  const pct = Math.round(i.progress * 100);
  const tone = i.state === "overdue" ? "bad" : i.state === "soon" ? "warn" : "ok";
  const left = (n, approx) => `${n >= 0 ? "เหลือ" : "เกิน"} ${approx ? "≈ " : ""}${baht(Math.abs(n))} กม.${approx ? " (ประมาณ)" : ""}`;
  const km = i.km_left !== null ? left(i.km_left, false) : i.est_km_left !== null ? left(i.est_km_left, true) : null;
  const days = i.days_left === null ? null : i.days_left < 0 ? `เลยมา ${-i.days_left} วัน` : i.days_left === 0 ? "ครบวันนี้" : `อีก ${i.days_left} วัน`;
  const v = { id: d.vehicle.id, plate_number: d.vehicle.plate_number, vehicle_number: d.vehicle.vehicle_number, nickname: d.vehicle.nickname };
  const snooze = i.snoozed_until
    ? el("span", { class: "plcalc-snz" }, el("span", { class: "badge", text: `💤 เลื่อนเตือนถึง ${beDate(i.snoozed_until)}` }),
        el("button", { class: "btn small", type: "button", text: "ยกเลิกการเลื่อน", onclick: () => clearSnooze(v, i) }))
    : (i.state === "soon" || i.state === "overdue") && !d.simulated ? el("button", { class: "btn small", type: "button", text: "💤 เลื่อนเตือน", onclick: () => openSnoozeDialog(v, i) }) : null;
  return el("div", { class: `plcalc-row ${tone}` },
    el("div", { class: "plcalc-head" }, el("strong", { text: i.label }), el("span", { class: "muted small", text: `ทุก ${[i.interval_km ? plKm(i.interval_km) : null, i.interval_months ? plMonths(i.interval_months) : null].filter(Boolean).join(" / ")}` }), el("span", { class: "grow" }), snooze),
    el("div", { class: `pl-bar ${tone}`, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.min(pct, 100)), title: `ใช้ไป ${pct}%` },
      el("span", { style: `width:${Math.min(pct, 100)}%` })),
    el("div", { class: "plcalc-text small" }, el("span", { text: `ใช้ไป ${pct}%` }), km ? el("span", { text: km }) : null,
      days ? el("span", { class: `plcalc-days ${tone}`, text: `${days}${i.due_date ? ` (${beDate(i.due_date)}${i.due_by === "km" ? " · ครบระยะทาง" : ""})` : ""}` }) : null));
}

// ---- snooze: "do not remind me about this for N days"
function openSnoozeDialog(v, i) {
  pl.dlg = { kind: "snooze", vehicle: v, item: i, draft: { days: "3" } };
  const d = pl.dlg.draft;
  const chips = [1, 3, 7, 14, 30].map((n) => el("button", { class: "chipbtn", type: "button", text: `${n} วัน`, onclick: () => { d.days = String(n); $("mf_days").value = d.days; } }));
  fill($("pdlgBody"),
    el("p", { class: "muted small", text: `${plLabel(v)} — ${i.label}${i.due_date ? ` · ครบกำหนด ${beDate(i.due_date)}` : ""}` }),
    el("div", { class: "edit-banner", text: "เลื่อนเฉพาะการเตือน — รายการนี้ยังเป็นสถานะเดิมในแผน พอครบวันที่เลื่อน ระบบจะเตือนใหม่ และถ้าทำรายการนี้ไปแล้ว การเลื่อนจะหมดไปเอง" }),
    el("div", { class: "chips" }, ...chips),
    el("div", { class: "mfields" }, mfield(d, "days", "เลื่อนเตือนกี่วัน", { type: "number" })));
  plOpenDialog("เลื่อนเตือน", "เลื่อนเตือน");
}

async function clearSnooze(v, i) {
  try { await api(`/api/plan/snooze?vehicle_id=${v.id}&item_key=${i.key}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
  await plAfterSave();
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
    } else if (dlg.kind === "snooze") {
      await api("/api/plan/snooze", jsonOpts("POST", { vehicle_id: dlg.vehicle.id, item_key: dlg.item.key, days: d.days }));
    } else if (dlg.kind === "ownitem") {
      pl.settings = await api(dlg.key ? `/api/plan/custom-items/${dlg.key}` : "/api/plan/custom-items", jsonOpts(dlg.key ? "PATCH" : "POST", {
        label: d.label, group_name: d.group_name, applies_to: d.scope || "all", km_small: d.scope === "big" ? "" : d.km, km_big: d.scope === "small" ? "" : d.km,
        months: d.months, words: d.words, category: d.category }));
      $("planDialog").close();
      await loadSettings();                          // (also re-reads the reminder list: the new item is in it)
      return;
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
  $("plSim").addEventListener("input", () => setSim(Number($("plSim").value)));
  $("plSimReset").addEventListener("click", () => setSim(0));
  $("plPreview").addEventListener("toggle", () => { if ($("plPreview").open) loadPreview(); });
  $("plTabDue").addEventListener("click", () => setPlanTab("due"));
  $("plTabSet").addEventListener("click", () => setPlanTab("set"));
  $("plQ").addEventListener("input", renderOverview);
  $("plShow").addEventListener("change", renderOverview);
  $("plBranch").addEventListener("change", loadOverview);
  $("plClass").addEventListener("change", loadOverview);
  $("pdlgCancel").addEventListener("click", () => $("planDialog").close());
  $("pdlgSave").addEventListener("click", savePlanDialog);
}
