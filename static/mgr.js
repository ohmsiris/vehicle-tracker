"use strict";

// ผู้จัดการ: the manager's own ticket board, the recurring tickets and the settings.
//   #mgr             the board: ต้องทำ -> กำลังทำ -> รอคนอื่น / รอผล -> เสร็จ (drag a card, or use its buttons)
//   #mgr-recurring   recurring tickets (every Monday, the 1st and 15th, every 3 months ...)
//   #mgr-settings    the reminders (on screen), the ticket types, the places
// Data: /api/mgr/* (app/mgr.py). The type of a new ticket is picked by the AI unless chosen here.
// Loaded BEFORE app.js: nothing here runs at load time.

const mg = {
  data: null, q: "", filterType: "", filterPlace: "", showOlder: false,
  dragId: null,
  edit: null, draft: {}, waiting: null,
  rec: [], recEdit: null, recDraft: {},
  set: null,
};

const MG_COLS = [["todo", "ต้องทำ"], ["doing", "กำลังทำ"], ["waiting", "รอคนอื่น / รอผล"], ["done", "เสร็จแล้ว"]];
const MG_WEEKDAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"];
const MG_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
const mgTrim = (t) => (t ? String(t).slice(0, 5) : "");
const mgTypeOf = (key) => (mg.data ? mg.data.types.find((t) => t.key === key) : null) || null;
const mgClosedDay = (t) => (t.done_at ? new Date(new Date(t.done_at).getTime() + 7 * 3600000).toISOString().slice(0, 10) : "");   // the Thai calendar day it was closed
const mgDaysTo = (iso) => Math.round((new Date(`${iso}T00:00:00`) - new Date(`${mg.data.today}T00:00:00`)) / 86400000);

// ============================================================ the board (#mgr)
async function openMgr() {
  showView("mgr");
  showErr("mgError", "");
  await loadMgrBoard();
}

async function loadMgrBoard() {
  try {
    mg.data = await api("/api/mgr/board");
  } catch (err) {
    showErr("mgError", `โหลดบอร์ดไม่สำเร็จ: ${err.message}`);
    return false;
  }
  $("mgNeedsV12").hidden = mg.data.ready !== false;
  fillMgSelects();
  renderMgBoard();
  if (typeof loadToday === "function") loadToday(true);
  return true;
}

// Places by kind: the branches first, then the shops / garages, then the rest.
const MG_KINDS = [["branch", "สาขา"], ["shop", "ร้าน / อู่ / ศูนย์"], ["other", "อื่นๆ"]];
const mgPlacesSorted = () => [...mg.data.places].sort((a, b) => MG_KINDS.findIndex(([k]) => k === a.kind) - MG_KINDS.findIndex(([k]) => k === b.kind) || a.sort_order - b.sort_order);

function mgPlaceOptions() {
  const groups = MG_KINDS.map(([k, title]) => [title, mg.data.places.filter((p) => (p.kind || "other") === k).sort((a, b) => a.sort_order - b.sort_order)]).filter(([, rows]) => rows.length);
  return groups.map(([title, rows]) => el("optgroup", { label: title }, ...rows.map((p) => el("option", { value: p.label, text: p.label }))));
}

function fillMgSelects() {
  const keepT = $("mgType").value, keepP = $("mgPlace").value;
  $("mgType").replaceChildren(el("option", { value: "", text: "ให้ระบบเลือกประเภท (AI)" }), ...mg.data.types.map((t) => el("option", { value: t.key, text: t.label })));
  $("mgPlace").replaceChildren(el("option", { value: "", text: "สถานที่ (ไม่ระบุ)" }), ...mgPlaceOptions());
  $("mgType").value = keepT;
  $("mgPlace").value = keepP;
}

function mgSortKey(a, b) {
  const t = mg.data.today;
  const ahead = (x) => (x.status === "waiting" && x.remind_on && x.remind_on <= t ? 0 : 1);       // a follow-up that has come up goes first
  return ahead(a) - ahead(b) || Number(b.urgent) - Number(a.urgent)
    || (a.due_date || "9999").localeCompare(b.due_date || "9999") || (a.due_time || "").localeCompare(b.due_time || "") || a.seq - b.seq;
}

function renderMgBoard() {
  const d = mg.data;
  const q = mg.q.trim().toLowerCase();
  const keep = (t) => (!mg.filterType || t.type_key === mg.filterType)
    && (!mg.filterPlace || (mg.filterPlace === "__none" ? !t.place : t.place === mg.filterPlace))
    && (!q || [t.title, t.notes, t.waiting_on, t.place, t.type_label].filter(Boolean).join(" ").toLowerCase().includes(q));
  const closed = d.recent.filter(keep);
  const older = closed.filter((t) => mgClosedDay(t) !== d.today);          // the closed column starts empty each day: yesterday's are one click away
  const by = { todo: [], doing: [], waiting: [], done: mg.showOlder ? closed : closed.filter((t) => mgClosedDay(t) === d.today) };
  for (const t of d.tickets.filter(keep)) by[t.status].push(t);
  for (const k of ["todo", "doing", "waiting"]) by[k].sort(mgSortKey);
  for (const [key] of MG_COLS) {
    $(`mgCount_${key}`).textContent = key === "done" ? String(by.done.length) : String(by[key].length);
    fill($(`mgList_${key}`), ...(by[key].length ? by[key].map(mgCard) : [el("p", { class: "muted empty small", text: key === "done" ? "ยังไม่มีงานที่เสร็จวันนี้" : "ว่าง" })]),
      key === "done" && older.length ? el("button", { class: "btn small", type: "button", text: mg.showOlder ? "ซ่อนของวันก่อนๆ" : `ดูที่ปิดไปก่อนหน้านี้ (${older.length})`, onclick: () => { mg.showOlder = !mg.showOlder; renderMgBoard(); } }) : null);
  }
  renderMgTypes();
  renderMgPlaces();
  const overdue = d.tickets.filter((t) => t.due_date && t.due_date < d.today).length;
  const followUps = d.tickets.filter((t) => t.status === "waiting" && t.remind_on && t.remind_on <= d.today).length;
  fill($("mgSummary"),
    el("strong", { text: `${d.tickets.length} เรื่องที่ยังเปิดอยู่` }),
    overdue ? el("span", { class: "badge urgent", text: `เลยกำหนด ${overdue}` }) : null,
    followUps ? el("span", { class: "badge warn", text: `ถึงวันติดตาม ${followUps}` }) : null);
}

// A row of chips to filter by the kind of ticket (instead of a drop-down, which opens upwards at the bottom of the screen).
function renderMgTypes() {
  const counts = new Map();
  for (const t of mg.data.tickets) counts.set(t.type_key, (counts.get(t.type_key) || 0) + 1);
  const chip = (value, label, n, color) => el("button", { class: `chipbtn mgchip${mg.filterType === value ? " on" : ""}`, type: "button", style: color ? `--c:${color}` : null,
    onclick: () => { mg.filterType = mg.filterType === value ? "" : value; renderMgBoard(); } }, color ? el("span", { class: "mgdot" }) : null, n === null ? label : `${label} (${n})`);
  fill($("mgTypes"), chip("", "ทุกประเภท", mg.data.tickets.length, null),
    ...mg.data.types.filter((t) => counts.has(t.key) || mg.filterType === t.key).map((t) => chip(t.key, t.label, counts.get(t.key) || 0, t.color)));
}

// A row of chips to filter by where the work is: all, the two branches, the shops / places that have something open, "no place".
function renderMgPlaces() {
  const counts = new Map();
  for (const t of mg.data.tickets) counts.set(t.place || "", (counts.get(t.place || "") || 0) + 1);
  const chip = (value, label, n) => el("button", { class: `chipbtn mgchip${mg.filterPlace === value ? " on" : ""}`, type: "button", text: n === null ? label : `${label} (${n})`,
    onclick: () => { mg.filterPlace = mg.filterPlace === value ? "" : value; renderMgBoard(); } });
  const shown = mgPlacesSorted().filter((p) => p.kind === "branch" || counts.has(p.label) || mg.filterPlace === p.label);
  fill($("mgPlaces"), chip("", "ทุกที่", mg.data.tickets.length), ...shown.map((p) => chip(p.label, p.label, counts.get(p.label) || 0)),
    counts.get("") ? chip("__none", "ไม่ระบุที่", counts.get("")) : null);
}

function mgDue(t) {
  if (!t.due_date) return null;
  const n = mgDaysTo(t.due_date), time = mgTrim(t.due_time);
  const open = !["done", "cancelled"].includes(t.status);
  if (!open) return { text: `กำหนด ${beDate(t.due_date)}`, cls: "" };
  if (n < 0) return { text: `เลยกำหนด ${-n} วัน${time ? ` (${time})` : ""}`, cls: "bad" };
  if (n === 0) return { text: `วันนี้${time ? ` ${time}` : ""}`, cls: "warn" };
  if (n === 1) return { text: `พรุ่งนี้${time ? ` ${time}` : ""}`, cls: "soon" };
  return { text: `${beDate(t.due_date)}${time ? ` ${time}` : ""}`, cls: "" };
}

function mgTypeChip(t) {
  if (!t.type_label) return null;
  return el("span", { class: "mgtype", style: `--c:${t.type_color || "#6b7280"}`, title: t.type_source === "ai" ? "ประเภทที่ AI เลือกให้ — กดแก้ไขถ้าไม่ใช่" : t.type_source === "rule" ? "ประเภทที่ระบบเดาจากคำ" : "" },
    el("span", { class: "mgdot" }), t.type_label, t.type_source === "ai" ? el("span", { class: "mgai", text: "AI" }) : null);
}

function mgCard(t) {
  const open = !["done", "cancelled"].includes(t.status);
  const due = mgDue(t);
  const followUp = t.status === "waiting" && t.remind_on && t.remind_on <= mg.data.today;
  const meta = [
    t.place ? el("span", { text: t.place }) : null,
    due ? el("span", { class: `mgdue ${due.cls}`.trim(), text: due.text }) : null,
    t.remind_on && open ? el("span", { class: `mgdue ${followUp ? "warn" : ""}`.trim(), text: `ติดตาม ${beDate(t.remind_on)}` }) : null,
    t.recurring_id ? el("span", { title: "งานประจำ", text: "↻ ประจำ" }) : null,
    t.checklist_total ? el("span", { text: `✓ ${t.checklist_done}/${t.checklist_total}` }) : null,
    t.status === "cancelled" ? el("span", { class: "badge", text: "ยกเลิก" }) : null,
  ].filter(Boolean);
  const b = (text, fn, cls = "") => el("button", { class: `btn small ${cls}`.trim(), type: "button", text, onclick: fn });
  const primary = (text, fn) => el("button", { class: "btn primary", type: "button", text, onclick: fn });
  const actions = {
    todo: [primary("กำลังทำ →", () => mgAct(t, "start")), b("รอผล…", () => openMgWaiting(t)), b("เสร็จ ✓", () => mgAct(t, "done")), b("แก้ไข", () => openMgEdit(t)), b("ยกเลิก", () => mgCancel(t), "danger")],
    doing: [primary("รอผล →", () => openMgWaiting(t)), b("← ต้องทำ", () => mgAct(t, "todo")), b("เสร็จ ✓", () => mgAct(t, "done")), b("แก้ไข", () => openMgEdit(t)), b("ยกเลิก", () => mgCancel(t), "danger")],
    waiting: [primary("เสร็จ ✓", () => mgAct(t, "done")), b("← กำลังทำ", () => mgAct(t, "resume")), b("เลื่อนวันติดตาม", () => openMgWaiting(t)), b("แก้ไข", () => openMgEdit(t)), b("ยกเลิก", () => mgCancel(t), "danger")],
    done: [b("← กำลังทำ", () => mgAct(t, "resume")), b("ดู / แก้ไข", () => openMgEdit(t))],
    cancelled: [b("← กำลังทำ", () => mgAct(t, "resume")), b("เปิดใหม่ (ต้องทำ)", () => mgAct(t, "reopen")), b("ดู / แก้ไข", () => openMgEdit(t))],
  }[t.status];
  const card = el("div", { class: `req mgcard${t.urgent ? " urgent" : ""}${due && due.cls === "bad" ? " late" : ""}${followUp ? " followup" : ""}${open ? "" : " done"}`, "data-id": t.id },
    el("div", { class: "req-head" }, mgTypeChip(t), t.urgent ? el("span", { class: "badge urgent", text: "ด่วน" }) : null, followUp ? el("span", { class: "badge warn", text: "ถึงวันติดตามแล้ว" }) : null),
    el("p", { class: "req-problem", text: t.title }),
    t.notes ? el("p", { class: "muted small mgnotes", text: t.notes }) : null,
    meta.length ? el("div", { class: "mgmeta small" }, ...meta) : null,
    t.status === "waiting" && (t.waiting_on || t.waiting_phone) ? el("div", { class: "mgwait small" }, el("span", { text: `รอ ${t.waiting_on || ""}` }), t.waiting_phone ? callLink(t.waiting_phone, "โทร", true) : null) : null,
    el("div", { class: "req-actions" }, ...actions));
  card.draggable = true;
  card.addEventListener("dragstart", (e) => { mg.dragId = t.id; e.dataTransfer.setData("text/plain", t.id); e.dataTransfer.effectAllowed = "move"; card.classList.add("dragging"); $("mgKanban").classList.add("dragging"); });
  card.addEventListener("dragend", () => { mg.dragId = null; card.classList.remove("dragging"); $("mgKanban").classList.remove("dragging"); document.querySelectorAll(".mgcol.dragover").forEach((c) => c.classList.remove("dragover")); });
  return card;
}

const mgFind = (id) => [...mg.data.tickets, ...mg.data.recent].find((t) => t.id === id) || null;

async function mgAct(t, action, extra = {}) {
  showErr("mgError", "");
  try {
    await api(`/api/mgr/tickets/${t.id}/action`, jsonOpts("POST", { action, ...extra }));
  } catch (err) {
    showErr("mgError", err.message);
    return false;
  }
  await loadMgrBoard();
  return true;
}

function mgCancel(t) {
  if (confirm(`ยกเลิกเรื่อง “${t.title}” ใช่ไหม?`)) mgAct(t, "cancel");
}

function mgMove(t, to) {
  if (t.status === to) return;
  if (to === "waiting") return openMgWaiting(t);
  if (to === "done" && t.status === "cancelled") return mgAct(t, "reopen");
  if (to === "doing" && (t.status === "done" || t.status === "cancelled")) return mgAct(t, "resume");
  return mgAct(t, "move", { to });
}

// ---- adding
async function addMgTicket(e) {
  e.preventDefault();
  const btn = $("mgAddBtn");
  const title = $("mgTitle").value.trim();
  if (!title) { showErr("mgAddError", "ใส่เรื่องที่ต้องทำด้วย"); return; }
  showErr("mgAddError", "");
  btn.disabled = true;
  try {
    await api("/api/mgr/tickets", jsonOpts("POST", {
      title, type_key: $("mgType").value, place: $("mgPlace").value, urgent: $("mgUrgent").checked,
      due_date: $("mgDue").value, due_time: $("mgTime").value, remind_on: $("mgRemind").value,
    }));
  } catch (err) {
    showErr("mgAddError", err.message);
    btn.disabled = false;
    return;
  }
  $("addMgForm").reset();
  for (const id of ["mgDueCap", "mgRemindCap"]) $(id).textContent = "";
  btn.disabled = false;
  await loadMgrBoard();
  $("mgTitle").focus();
}

// ---- edit dialog
function openMgDialog(title, saveText, kind) {
  mg.dlgKind = kind;
  $("mgDlgTitle").textContent = title;
  $("mgDlgSave").textContent = saveText;
  $("mgDlgSave").disabled = false;
  showErr("mgDlgError", "");
  $("mgDialog").showModal();
}

function openMgEdit(t) {
  mg.edit = t;
  mg.waiting = null;
  mg.draft = { title: t.title, notes: t.notes || "", type_key: t.type_key || "", place: t.place || "", urgent: !!t.urgent, due_date: t.due_date || "", due_time: mgTrim(t.due_time),
    remind_on: t.remind_on || "", waiting_on: t.waiting_on || "", waiting_phone: t.waiting_phone || "", checklist: (t.checklist || []).map((c) => ({ ...c })) };
  const d = mg.draft;
  const urgent = el("label", { class: "inline mgcheck" }, el("input", { type: "checkbox", id: "mgdUrgent" }), "ด่วน");
  urgent.querySelector("input").checked = d.urgent;
  urgent.querySelector("input").addEventListener("change", (e) => { d.urgent = e.target.checked; });
  fill($("mgDlgBody"),
    el("div", { class: "mfields" },
      mfield(d, "title", "เรื่อง", { wide: true, maxlength: 200 }),
      mfield(d, "notes", "รายละเอียด", { area: true, wide: true, maxlength: 2000 }),
      mselect(d, "type_key", "ประเภท", mg.data.types.map((x) => [x.key, x.label]), { blank: "ให้ระบบเลือกใหม่ (AI)" }),
      mselect(d, "place", "สถานที่", mg.data.places.map((x) => [x.label, x.label]), { blank: "— ไม่ระบุ —" }),
      mfield(d, "due_date", "กำหนดวันที่", { type: "date", caption: true }),
      mfield(d, "due_time", "เวลา", { type: "time" }),
      mfield(d, "remind_on", "ติดตามวันที่", { type: "date", caption: true }),
      urgent),
    t.status === "waiting" || t.waiting_on ? el("div", { class: "mfields" }, mfield(d, "waiting_on", "รอใคร / รออะไร", { maxlength: 120 }), mfield(d, "waiting_phone", "เบอร์โทร", { maxlength: 30 })) : null,
    el("h4", { class: "mgh", text: "รายการย่อย" }), el("div", { id: "mgChecklist" }));
  drawMgChecklist();
  openMgDialog(`เรื่องที่ ${t.seq}`, "บันทึก", "ticket");
}

function drawMgChecklist() {
  const d = mg.draft;
  const row = (c, i) => {
    const box = el("input", { type: "checkbox" });
    box.checked = !!c.done;
    box.addEventListener("change", () => { c.done = box.checked; });
    const text = el("input", { type: "text", maxlength: "200", value: c.text, "aria-label": "รายการย่อย" });
    text.addEventListener("input", () => { c.text = text.value; });
    return el("div", { class: "mgcheckrow" }, box, text, el("button", { class: "btn small", type: "button", text: "✕", title: "ลบ", onclick: () => { d.checklist.splice(i, 1); drawMgChecklist(); } }));
  };
  fill($("mgChecklist"), ...d.checklist.map(row),
    el("button", { class: "btn small", type: "button", text: "+ เพิ่มรายการย่อย", onclick: () => { d.checklist.push({ text: "", done: false }); drawMgChecklist(); const ins = $("mgChecklist").querySelectorAll("input[type=text]"); ins[ins.length - 1].focus(); } }));
}

// ---- "waiting" dialog: who or what, and when to follow up
function openMgWaiting(t) {
  mg.waiting = t;
  mg.edit = null;
  mg.draft = { waiting_on: t.waiting_on || "", waiting_phone: t.waiting_phone || "", remind_on: t.remind_on && t.remind_on >= mg.data.today ? t.remind_on : addDaysISO(mg.data.today, 3) };
  const d = mg.draft;
  const sync = () => { $("mf_waiting_on").value = d.waiting_on; $("mf_waiting_phone").value = d.waiting_phone; };
  const contacts = Object.entries(board.shopContacts || {}).flatMap(([shop, info]) => info.contacts.map((c) => ({ shop, ...c })));
  const chips = contacts.map((c) => el("button", { class: "chipbtn", type: "button", text: `${c.shop} · ${c.label}`, onclick: () => { d.waiting_on = `${c.shop} (${c.label})`; d.waiting_phone = c.phone; sync(); } }));
  const quick = [[1, "พรุ่งนี้"], [3, "อีก 3 วัน"], [7, "อีก 1 สัปดาห์"]].map(([n, label]) => el("button", { class: "chipbtn", type: "button", text: label, onclick: () => {
    d.remind_on = addDaysISO(mg.data.today, n);
    $("mf_remind_on").value = d.remind_on;
    $("mf_remind_on").dispatchEvent(new Event("input"));
  } }));
  fill($("mgDlgBody"),
    el("p", { class: "muted small", text: `${t.title} — วางไว้ในช่อง “รอคนอื่น / รอผล” แล้วพอถึงวันติดตาม จะกลับขึ้นมาให้เอง` }),
    el("div", { class: "mfields" },
      mfield(d, "waiting_on", "รอใคร / รอผลอะไร", { wide: true, maxlength: 120, placeholder: "เช่น ชมรมย์การช่าง ตอบราคา" }),
      mfield(d, "waiting_phone", "เบอร์โทร (ถ้ามี จะมีปุ่มโทรบนการ์ด)", { maxlength: 30 }),
      mfield(d, "remind_on", "ติดตามวันที่", { type: "date", caption: true })),
    el("div", { class: "chips" }, ...quick), contacts.length ? el("div", { class: "chips" }, ...chips) : null);
  openMgDialog(t.status === "waiting" ? "เลื่อนวันติดตาม" : "รอคนอื่น / รอผล", "บันทึก", "waiting");
}

async function saveMgDialog() {
  const btn = $("mgDlgSave");
  if (btn.disabled) return;
  const d = mg.draft;
  btn.disabled = true;
  try {
    if (mg.waiting) {
      await api(`/api/mgr/tickets/${mg.waiting.id}/action`, jsonOpts("POST", { action: "wait", waiting_on: d.waiting_on, waiting_phone: d.waiting_phone, remind_on: d.remind_on }));
    } else if (mg.edit) {
      const body = { title: d.title, notes: d.notes, place: d.place, urgent: d.urgent, due_date: d.due_date, due_time: d.due_time, remind_on: d.remind_on, checklist: d.checklist };
      if (d.type_key !== (mg.edit.type_key || "")) body.type_key = d.type_key;               // only a type that was really changed becomes "chosen by hand"
      if (!d.type_key && mg.edit.type_key && body.type_key === "") body.type_key = "auto";
      if (mg.edit.status === "waiting" || mg.edit.waiting_on) Object.assign(body, { waiting_on: d.waiting_on, waiting_phone: d.waiting_phone });
      await api(`/api/mgr/tickets/${mg.edit.id}`, jsonOpts("PATCH", body));
    }
  } catch (err) {
    showErr("mgDlgError", err.message);
    btn.disabled = false;
    return;
  }
  $("mgDialog").close();
  await loadMgrBoard();
}

// ============================================================ recurring (#mgr-recurring)
async function openMgrRecurring() {
  showView("mgrRecurring");
  showErr("mgrRecError", "");
  await loadMgrRecurring();
}

async function loadMgrRecurring() {
  try {
    [mg.rec, mg.data] = await Promise.all([api("/api/mgr/recurring"), api("/api/mgr/board")]);
  } catch (err) {
    showErr("mgrRecError", `โหลดงานประจำไม่สำเร็จ: ${err.message}`);
    return;
  }
  $("mgrRecNeedsV12").hidden = mg.data.ready !== false;
  fill($("mgrRecList"), ...(mg.rec.length ? mg.rec.map(recurringCard) : [el("p", { class: "muted empty", text: "ยังไม่มีงานประจำ — กด “+ เพิ่มงานประจำ” เช่น ตรวจเดินโรงงานทุกวันจันทร์ จ่ายเงินเดือนวันที่ 1 และ 15" })]));
}

function recurringCard(r) {
  return el("div", { class: `card mgrec${r.active ? "" : " off"}` },
    el("div", { class: "mgrec-top" },
      mgTypeChip({ type_label: r.type_label, type_color: r.type_color }),
      el("strong", { text: r.title }), r.urgent ? el("span", { class: "badge urgent", text: "ด่วน" }) : null, r.active ? null : el("span", { class: "badge", text: "พักไว้" })),
    el("div", { class: "muted" }, `${r.described}${r.time_of_day ? ` · ${mgTrim(r.time_of_day)} น.` : ""}${r.place ? ` · ${r.place}` : ""}`),
    el("div", { class: "muted small", text: [r.next_due ? `ครั้งถัดไป ${beDate(r.next_due)}` : (r.mode === "after_done" ? "ครั้งถัดไป: นับจากวันที่ทำเรื่องล่าสุดเสร็จ" : "ไม่มีครั้งถัดไป"),
      r.lead_days ? `สร้างล่วงหน้า ${r.lead_days} วัน` : null, r.mode === "after_done" ? "นับต่อจากวันที่ทำเสร็จ" : null].filter(Boolean).join(" · ") }),
    el("div", { class: "req-actions" },
      el("button", { class: "btn small", type: "button", text: "แก้ไข", onclick: () => openMgRecurringDialog(r) }),
      el("button", { class: "btn small", type: "button", text: r.active ? "พักไว้" : "เปิดใช้", onclick: async () => { await mgRecSave(r.id, { active: !r.active }); } }),
      el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: async () => {
        if (!confirm(`ลบงานประจำ “${r.title}” ใช่ไหม? (เรื่องที่สร้างไปแล้วยังอยู่)`)) return;
        try { await api(`/api/mgr/recurring/${r.id}`, { method: "DELETE" }); } catch (err) { showErr("mgrRecError", err.message); return; }
        await loadMgrRecurring();
      } })));
}

async function mgRecSave(id, body) {
  try { await api(`/api/mgr/recurring/${id}`, jsonOpts("PATCH", body)); } catch (err) { showErr("mgrRecError", err.message); return; }
  await loadMgrRecurring();
}

const mgToggle = (list, v) => { const i = list.indexOf(v); if (i >= 0) list.splice(i, 1); else list.push(v); list.sort((a, b) => (a === "last" ? 99 : a) - (b === "last" ? 99 : b)); };

function mgDraftFromRecurring(r) {
  const rule = r ? r.rule : { unit: "week", every: 1, weekdays: [0] };
  return {
    title: r ? r.title : "", notes: r ? r.notes || "" : "", type_key: r ? r.type_key || "" : "", place: r ? r.place || "" : "", urgent: r ? !!r.urgent : false,
    time_of_day: r ? mgTrim(r.time_of_day) : "", lead_days: r ? r.lead_days : 0, mode: r ? r.mode : "calendar", start_date: r ? r.start_date || "" : "", end_date: r ? r.end_date || "" : "",
    unit: rule.unit, every: rule.every, weekdays: [...(rule.weekdays || [0])], month_mode: rule.nth ? "nth" : "days", month_days: [...(rule.month_days || [1])],
    nth_n: rule.nth ? rule.nth.n : 1, nth_wd: rule.nth ? rule.nth.weekday : 0, year_month: rule.month || 1, year_day: rule.day || 1,
  };
}

function mgRuleFrom(d) {
  const rule = { unit: d.unit, every: Number(d.every) || 1 };
  if (d.unit === "week") rule.weekdays = [...d.weekdays];
  if (d.unit === "month") { if (d.month_mode === "nth") rule.nth = { n: Number(d.nth_n), weekday: Number(d.nth_wd) }; else rule.month_days = [...d.month_days]; }
  if (d.unit === "year") { rule.month = Number(d.year_month); rule.day = Number(d.year_day); }
  return rule;
}

function openMgRecurringDialog(r) {
  mg.recEdit = r || null;
  mg.edit = null;
  mg.waiting = null;
  mg.recDraft = mgDraftFromRecurring(r);
  const d = mg.recDraft;
  const urgent = el("label", { class: "inline mgcheck" }, el("input", { type: "checkbox" }), "ด่วน");
  urgent.querySelector("input").checked = d.urgent;
  urgent.querySelector("input").addEventListener("change", (e) => { d.urgent = e.target.checked; });
  const unitSel = mselect(d, "unit", "ความถี่", [["day", "ทุกวัน / ทุก N วัน"], ["week", "ทุกสัปดาห์ (เลือกวัน)"], ["month", "ทุกเดือน"], ["year", "ทุกปี"]], { blank: "—", onChange: drawMgRule });
  fill($("mgDlgBody"),
    el("div", { class: "mfields" },
      mfield(d, "title", "เรื่อง", { wide: true, maxlength: 200 }),
      mselect(d, "type_key", "ประเภท", mg.data.types.map((x) => [x.key, x.label]), { blank: "ให้ระบบเลือก (AI)" }),
      mselect(d, "place", "สถานที่", mg.data.places.map((x) => [x.label, x.label]), { blank: "— ไม่ระบุ —" }),
      mfield(d, "notes", "รายละเอียด", { area: true, wide: true, maxlength: 2000 }), urgent),
    el("h4", { class: "mgh", text: "ทำเมื่อไหร่" }),
    el("div", { class: "mfields" }, unitSel, mfield(d, "every", "ทุกกี่ครั้ง (1 = ทุกครั้ง, 2 = เว้นหนึ่งครั้ง)", { type: "number" })),
    el("div", { id: "mgRule", class: "mgrule" }),
    el("div", { class: "mfields" },
      mfield(d, "time_of_day", "เวลา (ไม่บังคับ)", { type: "time" }),
      mfield(d, "lead_days", "ให้ขึ้นบอร์ดก่อนถึงวันกี่วัน", { type: "number" })),
    el("p", { class: "muted small mghint", text: "0 = ขึ้นบอร์ดในวันนั้นเลย · 3 = ขึ้นบอร์ดล่วงหน้า 3 วัน จะได้เห็นก่อน" }),
    el("h4", { class: "mgh", text: "รอบถัดไป" }),
    el("div", { class: "mfields" }, mselect(d, "mode", "นับจาก", [["calendar", "ตามปฏิทิน — เช่น ทุกวันจันทร์ ก็ขึ้นทุกวันจันทร์ ไม่ว่าครั้งก่อนจะทำหรือยัง"],
      ["after_done", "หลังทำครั้งก่อนเสร็จ — ทำเสร็จวันไหน ค่อยนับรอบใหม่จากวันนั้น (ขึ้นทีละเรื่อง)"]], { blank: "—", wide: true })),
    el("h4", { class: "mgh", text: "ใช้ช่วงไหน" }),
    el("div", { class: "mfields" },
      mfield(d, "start_date", "เริ่มตั้งแต่วันที่", { type: "date", caption: true }),
      mfield(d, "end_date", "ถึงวันที่", { type: "date", caption: true })),
    el("p", { class: "muted small mghint", text: "ไม่ใส่ = เริ่มตั้งแต่วันนี้ และไม่มีวันหยุด" }));
  drawMgRule();
  openMgDialog(r ? "แก้ไขงานประจำ" : "เพิ่มงานประจำ", "บันทึก", "recurring");
}

function drawMgRule() {
  const d = mg.recDraft, box = $("mgRule");
  const chip = (label, on, fn) => el("button", { class: `chipbtn mgchip${on ? " on" : ""}`, type: "button", text: label, onclick: () => { fn(); drawMgRule(); } });
  const opt = (arr, key) => arr.map((t, i) => el("option", { value: String(i), text: t }));
  if (d.unit === "week") {
    fill(box, el("div", { class: "muted small", text: "วันไหนบ้าง" }), el("div", { class: "chips" }, ...MG_WEEKDAYS.map((w, i) => chip(w, d.weekdays.includes(i), () => mgToggle(d.weekdays, i)))));
  } else if (d.unit === "month") {
    const kind = el("div", { class: "chips" },
      chip("วันที่ของเดือน", d.month_mode === "days", () => { d.month_mode = "days"; }), chip("วันในสัปดาห์ เช่น จันทร์แรกของเดือน", d.month_mode === "nth", () => { d.month_mode = "nth"; }));
    if (d.month_mode === "nth") {
      const n = el("select", {}, ...[[1, "ที่ 1"], [2, "ที่ 2"], [3, "ที่ 3"], [4, "ที่ 4"], [-1, "สุดท้าย"]].map(([v, t]) => el("option", { value: String(v), text: t })));
      n.value = String(d.nth_n);
      n.addEventListener("change", () => { d.nth_n = Number(n.value); });
      const wd = el("select", {}, ...opt(MG_WEEKDAYS));
      wd.value = String(d.nth_wd);
      wd.addEventListener("change", () => { d.nth_wd = Number(wd.value); });
      fill(box, kind, el("div", { class: "mgnth" }, "วัน", wd, n, "ของเดือน"));
    } else {
      fill(box, kind, el("div", { class: "chips mgdays" }, ...Array.from({ length: 31 }, (_, i) => i + 1).map((n) => chip(String(n), d.month_days.includes(n), () => mgToggle(d.month_days, n))),
        chip("สิ้นเดือน", d.month_days.includes("last"), () => mgToggle(d.month_days, "last"))));
    }
  } else if (d.unit === "year") {
    const m = el("select", {}, ...opt(MG_MONTHS));
    m.value = String(d.year_month - 1);
    m.addEventListener("change", () => { d.year_month = Number(m.value) + 1; });
    const day = el("input", { type: "number", min: "1", max: "31", value: String(d.year_day) });
    day.addEventListener("input", () => { d.year_day = Number(day.value) || 1; });
    fill(box, el("div", { class: "mgnth" }, "วันที่", day, "เดือน", m));
  } else {
    fill(box);
  }
}

async function saveMgRecurring() {
  const btn = $("mgDlgSave");
  if (btn.disabled) return;
  const d = mg.recDraft;
  const body = { title: d.title, notes: d.notes, type_key: d.type_key, place: d.place, urgent: d.urgent, time_of_day: d.time_of_day, lead_days: d.lead_days || 0,
    mode: d.mode || "calendar", start_date: d.start_date, end_date: d.end_date, rule: mgRuleFrom(d) };
  btn.disabled = true;
  try {
    if (mg.recEdit) await api(`/api/mgr/recurring/${mg.recEdit.id}`, jsonOpts("PATCH", body));
    else await api("/api/mgr/recurring", jsonOpts("POST", body));
  } catch (err) {
    showErr("mgDlgError", err.message);
    btn.disabled = false;
    return;
  }
  $("mgDialog").close();
  await loadMgrRecurring();
}

// ============================================================ settings (#mgr-settings)
async function openMgrSettings() {
  showView("mgrSettings");
  showErr("mgrSetError", "");
  try {
    mg.set = await api("/api/mgr/settings");
  } catch (err) {
    showErr("mgrSetError", `โหลดตั้งค่าไม่สำเร็จ: ${err.message}`);
    return;
  }
  $("mgrSetNeedsV12").hidden = mg.set.ready !== false;
  renderMgrSettings();
}

function mgSettingRow(inputs, onSave, extra = null) {
  const msg = el("span", { class: "muted small" });
  const save = async () => {
    try { await onSave(); msg.textContent = "บันทึกแล้ว"; msg.className = "muted small"; } catch (err) { msg.textContent = err.message; msg.className = "error small"; }
  };
  return el("div", { class: "mgset-row" }, ...inputs, el("div", { class: "pl-set-actions" }, el("button", { class: "btn small", type: "button", text: "บันทึก", onclick: save }), extra, msg));
}

function renderMgrSettings() {
  const s = mg.set;
  // reminders: on screen, never LINE (the phone is on do-not-disturb)
  const timedOn = el("input", { type: "checkbox", id: "mgsTimedOn" }); timedOn.checked = !!s.settings.timed_enabled;
  const browserOn = el("input", { type: "checkbox", id: "mgsBrowserOn" }); browserOn.checked = todayNotifyOn();
  const note = el("span", { class: "muted small", text: todayNotifyStatus() });
  browserOn.addEventListener("change", async () => { note.textContent = (await todaySetNotify(browserOn.checked)) || todayNotifyStatus(); browserOn.checked = todayNotifyOn(); });
  const lineMsg = el("span", { class: "muted small" });
  const card1 = el("section", { class: "card mgset" },
    el("h3", { text: "การแจ้งเตือน" }),
    el("p", { class: "muted small", text: "ไม่ส่ง LINE — แจ้งเตือนอยู่ในเว็บนี้เอง: ตัวเลขข้างเมนู แถบสรุปด้านบนทุกหน้า (เรื่องเลยกำหนด / วันนี้ / ถึงวันติดตาม / รถใกล้ถึงรอบน้ำมัน) และถ้าเปิดไว้ ก็มีแจ้งเตือนของเบราว์เซอร์เด้งขึ้นมาเมื่อถึงเวลาของแต่ละเรื่อง (ต้องเปิดหน้าเว็บนี้ค้างไว้)" }),
    el("label", { class: "inline" }, timedOn, "เตือนตามเวลาของแต่ละเรื่อง (เรื่องที่ตั้งเวลาไว้ จะเตือนตอนถึงเวลานั้น)"),
    el("label", { class: "inline" }, browserOn, "ให้เบราว์เซอร์เครื่องนี้เด้งแจ้งเตือนด้วย"), note,
    el("div", { class: "pl-set-actions" },
      el("button", { class: "btn small primary", type: "button", text: "บันทึก", onclick: async () => {
        try {
          mg.set = { ...mg.set, settings: await api("/api/mgr/settings", jsonOpts("PUT", { timed_enabled: timedOn.checked })) };
          lineMsg.textContent = "บันทึกแล้ว"; lineMsg.className = "muted small";
          loadToday(true);
        } catch (err) { lineMsg.textContent = err.message; lineMsg.className = "error small"; }
      } }),
      el("button", { class: "btn small", type: "button", text: "ลองแจ้งเตือน", onclick: () => todayTest() }), lineMsg));

  // types
  const typeRow = (t) => {
    const label = el("input", { type: "text", maxlength: "60", value: t.label });
    const color = el("input", { type: "color", value: t.color || "#6b7280", "aria-label": "สี" });
    const active = el("input", { type: "checkbox" }); active.checked = t.active !== false;
    return mgSettingRow([el("div", { class: "mgset-id" }, color, label), el("label", { class: "inline small" }, active, "ใช้งาน")],
      async () => { mg.set.types = await api(`/api/mgr/types/${t.key}`, jsonOpts("PATCH", { label: label.value, color: color.value, active: active.checked })); });
  };
  const newLabel = el("input", { type: "text", maxlength: "60", placeholder: "ชื่อประเภทใหม่" });
  const newColor = el("input", { type: "color", value: "#0ea5e9", "aria-label": "สี" });
  const card2 = el("section", { class: "card mgset" }, el("h3", { text: "ประเภทของเรื่อง" }),
    el("p", { class: "muted small", text: "ระบบ (AI) เลือกประเภทให้ตอนเพิ่มเรื่อง — เปลี่ยนชื่อ สี หรือปิดประเภทที่ไม่ใช้ได้ที่นี่" }),
    ...s.types.map(typeRow),
    mgSettingRow([el("div", { class: "mgset-id" }, newColor, newLabel)], async () => {
      mg.set.types = await api("/api/mgr/types", jsonOpts("POST", { label: newLabel.value, color: newColor.value }));
      renderMgrSettings();
    }));

  // places
  const kindSelect = (value) => {
    const sel = el("select", { "aria-label": "ชนิด" }, ...MG_KINDS.map(([k, t]) => el("option", { value: k, text: t })));
    sel.value = value || "other";
    return sel;
  };
  const placeRow = (p) => {
    const label = el("input", { type: "text", maxlength: "40", value: p.label });
    const kind = kindSelect(p.kind);
    const active = el("input", { type: "checkbox" }); active.checked = p.active !== false;
    return mgSettingRow([el("div", { class: "mgset-id" }, label, kind), el("label", { class: "inline small" }, active, "ใช้งาน")],
      async () => { mg.set.places = await api(`/api/mgr/places/${p.id}`, jsonOpts("PATCH", { label: label.value, kind: kind.value, active: active.checked })); });
  };
  const newPlace = el("input", { type: "text", maxlength: "40", placeholder: "สถานที่ใหม่ เช่น ชื่อร้าน / อู่ / ลูกค้า" });
  const newKind = kindSelect("shop");
  const ordered = [...s.places].sort((a, b) => MG_KINDS.findIndex(([k]) => k === a.kind) - MG_KINDS.findIndex(([k]) => k === b.kind) || a.sort_order - b.sort_order);
  const card3 = el("section", { class: "card mgset" }, el("h3", { text: "สถานที่" }),
    el("p", { class: "muted small", text: "ใช้กรองเรื่องตามที่ที่ต้องไป — สาขา (สระบุรี / แก่งคอย) ร้าน / อู่ / ศูนย์ และที่อื่นๆ เพิ่มชื่อร้านใหม่ได้ตลอด" }),
    ...ordered.map(placeRow),
    mgSettingRow([el("div", { class: "mgset-id" }, newPlace, newKind)], async () => { mg.set.places = await api("/api/mgr/places", jsonOpts("POST", { label: newPlace.value, kind: newKind.value })); renderMgrSettings(); }));
  fill($("mgrSetList"), card1, card2, card3);
}

// ============================================================ init
function initMgr() {
  $("addMgForm").addEventListener("submit", addMgTicket);
  bindDateField($("mgDue"), $("mgDueCap"));
  bindDateField($("mgRemind"), $("mgRemindCap"));
  $("mgQ").addEventListener("input", () => { mg.q = $("mgQ").value; if (mg.data) renderMgBoard(); });
  $("mgDlgCancel").addEventListener("click", () => $("mgDialog").close());
  $("mgDlgSave").addEventListener("click", () => (mg.dlgKind === "recurring" ? saveMgRecurring() : saveMgDialog()));
  $("mgRecAdd").addEventListener("click", () => openMgRecurringDialog(null));
  document.addEventListener("dragover", (e) => { if (mg.dragId) { if (e.clientY < 90) window.scrollBy(0, -20); else if (e.clientY > window.innerHeight - 90) window.scrollBy(0, 20); } });
  document.querySelectorAll(".mgcol").forEach((col) => {
    col.addEventListener("dragover", (e) => { if (mg.dragId) { e.preventDefault(); col.classList.add("dragover"); } });
    col.addEventListener("dragleave", (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove("dragover"); });
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("dragover");
      const t = mg.dragId && mgFind(mg.dragId);
      mg.dragId = null;
      if (t) mgMove(t, col.dataset.status);
    });
  });
}
