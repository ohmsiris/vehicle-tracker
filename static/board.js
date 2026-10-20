"use strict";

// Repair board: follow-up on repair requests from the paper ใบแจ้งซ่อม log.
// The paper stays the master; only what you add here is tracked. The state
// rules live on the server (app/repair_requests.py) -- this file only shows
// the right buttons for each status and sends the chosen action.
//
// Loaded BEFORE app.js, so nothing here may run at load time: it relies on
// helpers from app.js (state, $, el, api, showView, ...) at call time only.

const board = {
  active: [],      // open + awaiting_bill, from /api/requests?scope=active
  recent: [],      // completed / cancelled, newest first
  fixers: [],      // names/shops seen before (suggestions)
  attached: [],    // request ids the bill being entered will complete
  attachedInfo: null,
};

// Days before a request starts to look neglected (amber, then red).
const AGE = { open: { warn: 7, bad: 14 }, awaiting_bill: { warn: 3, bad: 7 } };

// --------------------------------------------------------------- helpers
const todayISO = () => new Date().toLocaleDateString("sv-SE");   // YYYY-MM-DD in the viewer's timezone

function daysSince(iso) {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
}

// ---- Dates. The database and every <input type=date> value use the standard
// (ค.ศ.) ISO form, e.g. 2026-10-20. Everything shown to a person uses the
// Buddhist year in full -- "20 ต.ค. 2569" -- like the bills themselves, never
// a 2-digit year. Done by hand, not with Intl, so it reads the same on every
// phone and browser regardless of its language settings.
const TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];

function beDate(iso) {                       // "2026-10-20" (or an ISO timestamp's date part) -> "20 ต.ค. 2569"
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${+m[3]} ${TH_MONTHS[+m[2] - 1]} ${+m[1] + 543}` : "";
}

function beDateOf(dateObj) {                 // a JS Date, in the viewer's own timezone
  return `${dateObj.getDate()} ${TH_MONTHS[dateObj.getMonth()]} ${dateObj.getFullYear() + 543}`;
}

const beMonth = (ym) => `${TH_MONTHS[+ym.slice(5, 7) - 1]} ${+ym.slice(0, 4) + 543}`;   // "2026-10" -> "ต.ค. 2569"

// Typed a พ.ศ. year into a ค.ศ. field (2569 instead of 2026)? Fix it. Returns the ISO date to use.
function fixBuddhistYear(iso) {
  const m = /^(\d{4})-(\d{2}-\d{2})$/.exec(iso || "");
  return m && +m[1] >= 2400 ? `${+m[1] - 543}-${m[2]}` : iso;
}

// The line under a date field that spells out both calendars, so there is never any doubt which year it is.
function dateCaption(iso) {
  const m = /^(\d{4})-/.exec(iso || "");
  return m && +m[1] >= 1900 ? `${beDate(iso)} (พ.ศ.) · ค.ศ. ${+m[1]}` : "";
}

// Wires a date input + its caption: auto-corrects a พ.ศ. year, keeps the caption live. Returns true if it corrected.
function bindDateField(input, captionEl) {
  const refresh = () => {
    const fixed = fixBuddhistYear(input.value);
    const corrected = fixed !== input.value;
    if (corrected) input.value = fixed;
    captionEl.textContent = dateCaption(input.value) + (corrected ? " — แปลงจาก พ.ศ. เป็น ค.ศ. ให้แล้ว" : "");
  };
  input.addEventListener("input", refresh);
  input.addEventListener("change", refresh);
  captionEl.textContent = dateCaption(input.value);
}

const thDate = beDate;

function truckLabel(r) {
  return [r.truck_number ? `#${r.truck_number}` : null, r.plate].filter(Boolean).join(" ") || "ไม่ระบุรถ";
}

function jsonOpts(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function ageChip(days, kind) {
  const t = AGE[kind];
  const level = days >= t.bad ? "bad" : days >= t.warn ? "warn" : "";
  return el("span", { class: `age ${level}`.trim(), text: days === 0 ? "วันนี้" : `${days} วัน` });
}

function showBoardError(text) {
  const n = $("boardError");
  n.hidden = !text;
  n.textContent = text || "";
}

// --------------------------------------------------------------- loading
async function loadBoard() {
  try {
    const [active, recent] = await Promise.all([api("/api/requests?scope=active"), api("/api/requests?scope=recent")]);
    board.active = active;
    board.recent = recent;
    showBoardError("");
  } catch (err) {
    showBoardError(`โหลดงานซ่อมไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderBoard();
  renderFixes();
}

// --------------------------------------------------------------- rendering
function renderBoard() {
  const open = board.active.filter((r) => r.status === "open");
  const awaiting = board.active
    .filter((r) => r.status === "awaiting_bill")
    .sort((a, b) => b.days_in_status - a.days_in_status);   // waiting longest first

  $("countOpen").textContent = open.length ? `(${open.length})` : "";
  $("countAwaiting").textContent = awaiting.length ? `(${awaiting.length})` : "";
  $("openList").replaceChildren(...(open.length ? open.map((r) => requestCard(r, "open")) : [emptyNote("ไม่มีงานรอซ่อม")]));
  $("awaitingList").replaceChildren(...(awaiting.length ? awaiting.map((r) => requestCard(r, "awaiting_bill")) : [emptyNote("ไม่มีงานที่รอบิล")]));
  $("recentList").replaceChildren(...(board.recent.length ? board.recent.map(recentCard) : [emptyNote("ยังไม่มีงานที่ปิดแล้ว")]));
  $("recentBox").querySelector("summary").textContent = `ปิดแล้วล่าสุด${board.recent.length ? ` (${board.recent.length})` : ""}`;
}

const emptyNote = (text) => el("p", { class: "muted small empty", text });

function requestCard(r, kind) {
  const days = kind === "open" ? daysSince(r.request_date) : r.days_in_status;
  const meta = kind === "open"
    ? [r.reported_by ? `ผู้แจ้ง ${r.reported_by}` : null, `แจ้งเมื่อ ${thDate(r.request_date)}`]
    : [`ซ่อมโดย ${r.fixed_by || "-"}`, `เมื่อ ${thDate(r.fixed_date)}`, r.reported_by ? `ผู้แจ้ง ${r.reported_by}` : null];

  const small = (text, title, fn, cls = "") => el("button", { class: `btn small ${cls}`.trim(), type: "button", title, text, onclick: fn });
  const actions = kind === "open"
    ? [
        el("button", { class: "btn primary", type: "button", text: "ซ่อมแล้ว ✓", onclick: () => openFixDialog(r) }),
        small("✎", "แก้ข้อความ", () => editProblem(r)),
        small(r.urgent ? "เลิกด่วน" : "ด่วน", "สลับสถานะด่วน", () => patchRequest(r.request_id, { urgent: !r.urgent })),
        small("ยกเลิก", "ยกเลิกงานนี้", () => cancelRequest(r), "danger"),
      ]
    : [
        el("button", { class: "btn primary", type: "button", text: "เพิ่มบิล →", onclick: () => startBillFor(r) }),
        el("button", { class: "btn", type: "button", text: "ไม่มีบิล", onclick: () => openNoBillDialog(r) }),
        small("← ย้อนกลับ", "กลับไปเป็นรอซ่อม", () => runAction(r.request_id, { action: "reopen" })),
        small("ยกเลิก", "ยกเลิกงานนี้", () => cancelRequest(r), "danger"),
      ];

  return el("div", { class: `req${r.urgent ? " urgent" : ""}` },
    el("div", { class: "req-head" },
      el("span", { class: "req-no", text: `#${r.request_no}` }),
      el("strong", { text: truckLabel(r) }),
      r.branch ? el("span", { class: "muted small", text: r.branch }) : null,
      r.urgent ? el("span", { class: "badge urgent", text: "ด่วน" }) : null,
      ageChip(days, kind)),
    el("p", { class: "req-problem", text: r.problem }),
    el("p", { class: "muted small", text: meta.filter(Boolean).join(" · ") }),
    el("div", { class: "req-actions" }, ...actions));
}

function recentCard(r) {
  const badges = [];
  if (r.status === "cancelled") badges.push(el("span", { class: "badge", text: "ยกเลิก" }));
  else if (r.no_bill) badges.push(el("span", { class: "badge nobill", text: "ปิดโดยไม่มีบิล" }));
  else badges.push(el("span", { class: "badge ok", text: r.has_record ? "ปิดแล้ว · มีบิล/บันทึก" : "ปิดแล้ว" }));
  const meta = [
    r.fixed_by ? `ซ่อมโดย ${r.fixed_by}` : null,
    r.fixed_date ? `เมื่อ ${thDate(r.fixed_date)}` : null,
    r.no_bill && r.no_bill_reason ? `เหตุผล: ${r.no_bill_reason}` : null,
  ].filter(Boolean).join(" · ");
  return el("div", { class: "req done" },
    el("div", { class: "req-head" },
      el("span", { class: "req-no", text: `#${r.request_no}` }),
      el("strong", { text: truckLabel(r) }),
      ...badges),
    el("p", { class: "req-problem", text: r.problem }),
    meta ? el("p", { class: "muted small", text: meta }) : null,
    r.has_record ? null : el("div", { class: "req-actions" },
      el("button", { class: "btn small", type: "button", text: "← ย้อนกลับเป็นรอซ่อม", onclick: () => reopenRecent(r) })));
}

// --------------------------------------------------------------- actions
// Any server answer of 409 means the request changed under us (another tab,
// a double click): refresh instead of showing a stale board.
async function runAction(id, body) {
  try {
    await api(`/api/requests/${id}/action`, jsonOpts("POST", body));
    await loadBoard();
    return null;
  } catch (err) {
    if (err.status === 409) { await loadBoard(); showBoardError(err.message); return null; }
    return err.message;
  }
}

async function patchRequest(id, body) {
  try {
    await api(`/api/requests/${id}`, jsonOpts("PATCH", body));
    await loadBoard();
  } catch (err) {
    showBoardError(err.message);
  }
}

async function editProblem(r) {
  const text = prompt("แก้ไขปัญหา", r.problem);
  if (text === null || !text.trim() || text.trim() === r.problem) return;
  await patchRequest(r.request_id, { problem: text.trim() });
}

async function cancelRequest(r) {
  if (!confirm(`ยกเลิกงาน #${r.request_no} (${truckLabel(r)}) ใช่ไหม?\n${r.problem}`)) return;
  const err = await runAction(r.request_id, { action: "cancel" });
  if (err) showBoardError(err);
}

async function reopenRecent(r) {
  if (!confirm(`ย้อนงาน #${r.request_no} กลับไปเป็นรอซ่อมใช่ไหม?`)) return;
  const err = await runAction(r.request_id, { action: "reopen" });
  if (err) showBoardError(err);
}

// ---- "ซ่อมแล้ว": who fixed it, then bill / no bill
let fixTarget = null;

function openFixDialog(r) {
  fixTarget = r;
  $("fixSummary").textContent = `#${r.request_no} · ${truckLabel(r)} · ${r.problem}`;
  $("fixBy").value = "";
  $("fixDate").value = todayISO();
  $("fixDate").min = r.request_date;
  $("fixDate").max = todayISO();
  $("fixDateCap").textContent = dateCaption($("fixDate").value);
  $("fixReason").value = "";
  $("fixError").hidden = true;
  $("fixerList").replaceChildren(...board.fixers.map((n) => el("option", { value: n })));
  $("fixChips").replaceChildren(...board.fixers.slice(0, 8).map((n) =>
    el("button", { class: "chipbtn", type: "button", text: n, onclick: () => { $("fixBy").value = n; } })));
  $("fixDialog").showModal();
}

async function submitFix(action) {
  const err = await runAction(fixTarget.request_id, {
    action,
    fixed_by: $("fixBy").value.trim(),
    fixed_date: $("fixDate").value || null,
    reason: $("fixReason").value.trim(),
  });
  if (err) {
    $("fixError").textContent = err;
    $("fixError").hidden = false;
    return;
  }
  $("fixDialog").close();
}

// ---- "ไม่มีบิล" for something already waiting on a bill
let noBillTarget = null;

function openNoBillDialog(r) {
  noBillTarget = r;
  $("noBillSummary").textContent = `#${r.request_no} · ${truckLabel(r)} · ${r.problem}`;
  $("noBillReason").value = "";
  $("noBillError").hidden = true;
  $("noBillDialog").showModal();
}

async function submitNoBill() {
  const err = await runAction(noBillTarget.request_id, { action: "no_bill", reason: $("noBillReason").value.trim() });
  if (err) {
    $("noBillError").textContent = err;
    $("noBillError").hidden = false;
    return;
  }
  $("noBillDialog").close();
}

// --------------------------------------------------------------- adding
async function submitNewRequest(e) {
  e.preventDefault();
  const veh = selectedVehicle($("addVehicle"));
  const problem = $("addProblem").value.trim();
  const err = $("addError");
  if (!veh) { err.textContent = "ยังไม่ได้เลือกรถ"; err.hidden = false; return; }
  if (!problem) { err.textContent = "ใส่ปัญหาที่ต้องซ่อมด้วย"; err.hidden = false; return; }
  err.hidden = true;
  try {
    await api("/api/requests", jsonOpts("POST", {
      vehicle_id: veh.id, problem, reported_by: $("addReporter").value.trim() || null, urgent: $("addUrgent").checked,
    }));
    $("addProblem").value = "";
    $("addReporter").value = "";
    $("addUrgent").checked = false;
    $("addProblem").focus();
    await loadBoard();
  } catch (e2) {
    err.textContent = e2.message;
    err.hidden = false;
  }
}

// --------------------------------------------------------------- bill hand-off
// "เพิ่มบิล →": jump to the bill page with this truck chosen and this request
// attached, so saving the bill completes it.
function startBillFor(r) {
  board.attached = [r.request_id];
  board.attachedInfo = r;
  const idx = state.vehicles.findIndex((v) => v.id === r.vehicle_id);
  $("vehicleSelect").value = idx >= 0 ? String(idx) : "";
  renderAttachNote();
  showView("capture");
}

function renderAttachNote() {
  const r = board.attachedInfo;
  $("attachNote").hidden = !r || board.attached.length === 0;
  if (r) $("attachText").textContent = `กำลังเพิ่มบิลของงานซ่อม #${r.request_no} · ${truckLabel(r)} · ${r.problem} — บันทึกบิลแล้วงานนี้จะถูกปิดให้เอง`;
}

function clearAttachment() {
  board.attached = [];
  board.attachedInfo = null;
  renderAttachNote();
}

// On the review page: which waiting repairs of THIS truck does the bill close?
function renderFixes() {
  const sel = $("evVehicle");
  const veh = sel ? selectedVehicle(sel) : null;
  const card = $("fixesCard");
  const mine = veh ? board.active.filter((r) => r.vehicle_id === veh.id) : [];
  card.hidden = mine.length === 0;
  $("fixesList").replaceChildren(...mine.map((r) => {
    const box = el("input", { type: "checkbox", "data-id": r.request_id });
    box.checked = board.attached.includes(r.request_id);
    return el("label", { class: "fixrow" }, box,
      el("span", {}, el("strong", { text: `#${r.request_no} ` }), `${r.problem} `,
        el("span", { class: "muted small", text: r.status === "awaiting_bill" ? "(ซ่อมแล้ว รอบิล)" : `(ยังไม่ได้ซ่อม · แจ้ง ${thDate(r.request_date)})` })));
  }));
}

const selectedFixIds = () => [...document.querySelectorAll("#fixesList input:checked")].map((b) => b.dataset.id);

// --------------------------------------------------------------- init
function initBoard() {
  fillVehicleSelect($("addVehicle"));
  $("addForm").addEventListener("submit", submitNewRequest);
  bindDateField($("fixDate"), $("fixDateCap"));
  $("fixCancel").addEventListener("click", () => $("fixDialog").close());
  $("fixHasBill").addEventListener("click", () => submitFix("fixed_awaiting_bill"));
  $("fixNoBill").addEventListener("click", () => submitFix("fixed_no_bill"));
  $("noBillCancel").addEventListener("click", () => $("noBillDialog").close());
  $("noBillOk").addEventListener("click", submitNoBill);
  $("attachClear").addEventListener("click", clearAttachment);
  return loadBoard();
}
