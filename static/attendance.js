"use strict";

// เวลาทำงาน: the fingerprint machine's report, checked day by day before any pay is worked out.
//   #attendance   one pay month (the 26th to the 25th, named by the month of the 25th): upload the machine's .xls, then go through the days that
//                 need the owner -- a single scan, or (ผลิต only) a shift shorter than its length (12 h Saraburi, 9 h Kaeng Khoi) minus 10 minutes -- and decide each one.
// The server pairs the scans into shifts and says what is flagged (app/attendance.py); nothing here decides anything by itself. The one shortcut is
// "ใช้ตามข้อเสนอทั้งหมด": the first 3 single scans of a month come with a suggestion (ค่าแรงครึ่งวัน) that is applied only when this button is pressed.
// Data: /api/attendance (same PIN lock as พนักงาน). Loaded BEFORE app.js: nothing runs at load time; helpers from app.js / board.js / dashboard.js /
// employees.js are used at call time.

const at = { data: null, open: new Set(), allDays: new Set(), pending: {} };

const AT_DECISIONS = [["approve", "อนุมัติ"], ["half", "ครึ่งวัน"], ["full_penalty", "เต็ม + ใบเตือน + หัก"], ["unpaid", "ไม่จ่าย"]];
const AT_DOW = ["อา.", "จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส."];
const atMoney = (n) => Number(n || 0).toLocaleString("th-TH", { maximumFractionDigits: 2 });
const atHM = (m) => `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
const atKey = (eid, date) => `${eid}|${date}`;
const atIsFlag = (d) => d.flag === "single" || d.flag === "short";

// ------------------------------------------------------------------ loading
async function atLoad(month) {
  try {
    const d = await api(`/api/attendance${month ? `?month=${encodeURIComponent(month)}` : ""}`);
    $("hrLockBox").hidden = true;
    at.data = d;
    $("atNeedsV26").hidden = d.ready !== false;
    if (d.ready === false) { at.data = null; return false; }
    $("atMonth").value = d.month;
    fill($("atRange"), `${beDate(d.start)} – ${beDate(d.end)}`);
    return true;
  } catch (err) {
    if (err.status === 423) { showHrLock(); return false; }
    showErr("atError", err.message);
    return false;
  }
}

async function openAttendance() {
  showView("attendance");
  showErr("atError", "");
  if (!(await atLoad($("atMonth").value || ""))) { $("atKpis").replaceChildren(); $("atList").replaceChildren(); $("atSuggest").hidden = true; $("atHistory").hidden = true; return; }
  atFillFilters();
  renderAttendance();
}

function atFillFilters() {
  const keepB = $("atBranch").value, keepP = $("atPosition").value;
  const uniq = (f) => [...new Set(at.data.people.map(f).filter(Boolean))].sort();
  $("atBranch").replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...uniq((p) => p.branch).map((b) => el("option", { value: b, text: b })));
  $("atPosition").replaceChildren(el("option", { value: "", text: "ทุกตำแหน่ง" }), ...uniq((p) => p.position).map((x) => el("option", { value: x, text: x })));
  $("atBranch").value = keepB;
  $("atPosition").value = keepP;
}

// the counts shown at the top and on every card follow the decisions made in this page, without asking the server again
function atTally() {
  const t = { people: at.data.people.length, days: 0, single: 0, short: 0, open: 0, decided: 0, suggested: 0 };
  for (const p of at.data.people) {
    const flagged = p.days.filter(atIsFlag);
    Object.assign(p.counts, { days: p.days.length, single: p.days.filter((d) => d.flag === "single").length, short: p.days.filter((d) => d.flag === "short").length,
      open: flagged.filter((d) => !d.review).length, decided: flagged.filter((d) => d.review).length, minutes: p.days.reduce((s, d) => s + d.minutes, 0) });
    p.deduct = p.days.reduce((s, d) => s + (d.review ? d.review.deduct_amount : 0), 0);
    t.days += p.counts.days; t.single += p.counts.single; t.short += p.counts.short; t.open += p.counts.open; t.decided += p.counts.decided;
    t.suggested += p.days.filter((d) => d.suggest && !d.review).length;
  }
  at.data.totals = t;
}

// ------------------------------------------------------------------ the page
function atVisiblePeople() {
  const show = $("atShow").value, kind = $("atKind").value, branch = $("atBranch").value, position = $("atPosition").value;
  const tokens = $("atSearch").value.trim().split(/\s+/).map(empNorm).filter(Boolean);
  return at.data.people
    .filter((p) => !branch || p.branch === branch)
    .filter((p) => !position || p.position === position)
    .filter((p) => !tokens.length || tokens.every((t) => empNorm([p.name, p.code, p.position, p.branch].join("|")).includes(t)))
    .filter((p) => {
      const flagged = p.days.filter((d) => atIsFlag(d) && (!kind || d.flag === kind));
      if (show === "open") return flagged.some((d) => !d.review);
      if (show === "flagged") return flagged.length > 0 || p.days.some((d) => d.flag === "manual");
      return !kind || flagged.length > 0;
    });
}

function renderAttendance() {
  const d = at.data, t = d.totals;
  $("atKpis").replaceChildren(
    kpi("คนที่มีเวลาทำงาน", String(t.people), [`${t.days} วันทำงาน`]),
    kpi("สแกนเดียว", String(t.single), "วันที่มีสแกนครั้งเดียว", t.single ? "warn" : ""),
    kpi("ทำงานไม่ครบเวลา", String(t.short), "ฝ่ายผลิต: สระบุรี < 11:50 ชม. · แก่งคอย < 8:50 ชม.", t.short ? "warn" : ""),
    kpi("ค้างตัดสิน", String(t.open), t.open ? "ต้องให้คุณตัดสิน" : "ครบแล้ว", t.open ? "bad" : "ok"),
    kpi("ตัดสินแล้ว", String(t.decided), "วันที่ติดธง"));
  const box = $("atSuggest");
  box.hidden = !t.suggested;
  if (t.suggested) {
    box.replaceChildren(el("p", { text: `มี ${t.suggested} วันที่เป็นสแกนเดียวครั้งที่ 1–${d.free_singles} ของเดือน — ข้อเสนอคือ “ค่าแรงครึ่งวัน” (ครั้งที่ ${d.free_singles + 1} เป็นต้นไปไม่มีข้อเสนอ ต้องเลือกเอง)` }),
      el("button", { class: "btn small primary", type: "button", onclick: atApplySuggestions, text: `ใช้ตามข้อเสนอทั้งหมด (${t.suggested})` }));
  }
  const rows = atVisiblePeople();
  fill($("atCount"), `พบ ${rows.length} คน`);
  const expandAll = el("button", { class: "btn small", type: "button", onclick: () => { const all = rows.every((p) => at.open.has(p.employee_id)); for (const p of rows) all ? at.open.delete(p.employee_id) : at.open.add(p.employee_id); renderAttendance(); },
    text: rows.every((p) => at.open.has(p.employee_id)) && rows.length ? "ปิดทั้งหมด" : "เปิดทั้งหมด" });
  $("atCount").append(" ", rows.length ? expandAll : "");
  $("atList").replaceChildren(...(rows.length ? rows.map(atCard) : [el("p", { class: "muted empty", text: d.people.length ? "ไม่มีรายการที่ตรงกับตัวกรอง" : "ยังไม่มีข้อมูลของงวดนี้ — กด “+ อัปโหลดไฟล์จากเครื่องสแกน”" })]));
  renderAtHistory();
}

function atCard(p) {
  const open = at.open.has(p.employee_id), c = p.counts;
  const head = el("button", { class: "athead", type: "button", "aria-expanded": String(open), onclick: () => { open ? at.open.delete(p.employee_id) : at.open.add(p.employee_id); renderAttendance(); } },
    el("span", { class: "atwho" }, el("strong", { text: p.name }), el("span", { class: "muted small", text: ` ${p.code} · ${p.position || "—"} · ${p.branch || "—"}` })),
    el("span", { class: "atchips" },
      c.single ? el("span", { class: "badge warn", text: `สแกนเดียว ${c.single}` }) : null,
      c.short ? el("span", { class: "badge warn", text: `ไม่ครบเวลา ${c.short}` }) : null,
      c.open ? el("span", { class: "badge urgent", text: `ค้าง ${c.open}` }) : (c.decided ? el("span", { class: "badge ok", text: "ตัดสินครบ" }) : null),
      p.deduct ? el("span", { class: "badge", text: `หัก ${atMoney(p.deduct)} ฿` }) : null,
      el("span", { class: "muted small", text: `${c.days} วัน · ${atHM(c.minutes)} ชม.` }),
      el("span", { class: "atcaret", text: open ? "▾" : "▸" })));
  const card = el("div", { class: `card atcard${c.open ? " hasopen" : ""}` }, head);
  if (open) card.append(atBody(p));
  return card;
}

function atBody(p) {
  const kind = $("atKind").value, all = at.allDays.has(p.employee_id);
  const days = p.days.filter((d) => all || atIsFlag(d) || d.flag === "manual").filter((d) => all || !kind || d.flag === kind || d.flag === "manual");
  const table = days.length
    ? el("div", { class: "tablewrap" }, el("table", { class: "emptable attable" },
        el("thead", {}, el("tr", {}, ...["วัน", "เวลาสแกน", "ชั่วโมง", "สถานะ", "ตัดสิน"].map((x) => el("th", { text: x })))),
        el("tbody", {}, ...days.map((d) => atDayRow(p, d)))))
    : el("p", { class: "muted small", text: "ไม่มีวันที่ติดธง" });
  const hidden = p.days.length - days.length;
  const toggle = el("button", { class: "btn small", type: "button", onclick: () => { all ? at.allDays.delete(p.employee_id) : at.allDays.add(p.employee_id); renderAttendance(); },
    text: all ? "แสดงเฉพาะวันที่ติดธง" : `ดูเวลาทุกวัน (${p.days.length} วัน${hidden ? `, ซ่อนอยู่ ${hidden}` : ""})` });
  const openDays = p.days.filter((d) => atIsFlag(d) && !d.review);
  const approveRest = openDays.length ? el("button", { class: "btn small", type: "button", onclick: () => atApproveRest(p, openDays), text: `อนุมัติที่ค้างทั้งหมดของคนนี้ (${openDays.length})` }) : null;
  return el("div", { class: "atbody" }, table, el("div", { class: "atfoot" }, toggle, approveRest, atManualForm(p)));
}

function atDayRow(p, d) {
  const dt = new Date(`${d.date}T00:00:00`);
  const times = d.shifts.length
    ? d.shifts.map((s) => (s.out ? `${s.in} → ${s.out}${s.next_day ? " (+1 วัน)" : ""}` : `${s.in} (สแกนเดียว)`)).join("  ·  ")
    : "ไม่มีสแกน (เพิ่มโดยเจ้าของ)";
  const status = d.flag === "single" ? el("span", { class: "badge warn", text: d.single_no ? `สแกนเดียว ครั้งที่ ${d.single_no}` : "สแกนเดียว" })
    : d.flag === "short" ? el("span", { class: "badge warn", text: `ขาด ${atHM(p.expected - d.minutes)} ชม.` })
    : d.flag === "manual" ? el("span", { class: "badge", text: "เพิ่มเอง" }) : el("span", { class: "badge ok", text: "ปกติ" });
  return el("tr", { class: d.review ? "atdone" : d.flag === "ok" ? "" : "atneed" },
    el("td", { class: "nowrap", text: `${AT_DOW[dt.getDay()]} ${beDate(d.date).replace(/ \d{4}$/, "")}` }),
    el("td", { text: times }),
    el("td", { class: "r nowrap", text: d.shifts.some((s) => s.out) ? atHM(d.minutes) : "—" }),
    el("td", {}, status, d.shifts.length > 1 ? el("span", { class: "badge", text: `${d.shifts.length} กะ` }) : null),
    el("td", {}, d.flag === "ok" ? null : atControls(p, d)));
}

function atControls(p, d) {
  const key = atKey(p.employee_id, d.date), cur = d.review ? d.review.decision : "";
  const buttons = AT_DECISIONS.filter(([k]) => d.flag !== "manual" || k === "approve" || k === "half").map(([k, label]) =>
    el("button", { class: `btn small${cur === k ? " primary" : ""}${!d.review && d.suggest === k ? " suggested" : ""}`, type: "button",
      title: !d.review && d.suggest === k ? "ข้อเสนอของระบบ" : null,
      onclick: () => (k === "full_penalty" ? atAskPenalty(p, d) : atDecide(p, d, k, 0)), text: label }));
  const wrap = el("div", { class: "atbtns" }, ...buttons);
  if (cur === "full_penalty") wrap.append(el("span", { class: "small", text: `หัก ${atMoney(d.review.deduct_amount)} ฿` }));
  if (key in at.pending) {
    const input = el("input", { type: "number", min: "0", step: "1", inputmode: "decimal", placeholder: "หักกี่บาท", value: at.pending[key], class: "atamount", "aria-label": "จำนวนเงินที่หัก",
      oninput: (e) => { at.pending[key] = e.target.value; } });
    wrap.append(input, el("button", { class: "btn small primary", type: "button", onclick: () => atDecide(p, d, "full_penalty", at.pending[key]), text: "บันทึก" }),
      el("button", { class: "btn small", type: "button", onclick: () => { delete at.pending[key]; renderAttendance(); }, text: "ยกเลิก" }));
    setTimeout(() => input.focus(), 0);
  }
  if (d.review) wrap.append(el("button", { class: "btn small", type: "button", onclick: () => atDecide(p, d, "", 0), title: "เอาการตัดสินออก", text: "✕" }));
  return wrap;
}

function atAskPenalty(p, d) {
  at.pending[atKey(p.employee_id, d.date)] = d.review && d.review.decision === "full_penalty" ? String(d.review.deduct_amount) : "";
  renderAttendance();
}

async function atDecide(p, d, decision, amount) {
  showErr("atError", "");
  try {
    const r = await api("/api/attendance/review", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ employee_id: p.employee_id, work_date: d.date, decision, deduct_amount: amount || 0, manual: d.flag === "manual" }) });
    d.review = r.review;
    delete at.pending[atKey(p.employee_id, d.date)];
    if (!r.review && d.flag === "manual") p.days = p.days.filter((x) => x !== d);        // a day added by hand disappears with its decision
    if (!p.days.length) at.data.people = at.data.people.filter((x) => x !== p);
    atTally();
    renderAttendance();
  } catch (err) { showErr("atError", err.message); }
}

async function atApproveRest(p, days) {
  if (!confirm(`อนุมัติ ${days.length} วันที่ยังค้างของ ${p.name} (จ่ายเต็มวันทั้งหมด)?`)) return;
  showErr("atError", "");
  try {
    for (const d of days) {
      const r = await api("/api/attendance/review", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ employee_id: p.employee_id, work_date: d.date, decision: "approve" }) });
      d.review = r.review;
    }
  } catch (err) { showErr("atError", err.message); }
  atTally();
  renderAttendance();
}

function atManualForm(p) {
  const date = el("input", { type: "date", min: at.data.start, max: at.data.end, "aria-label": "วันที่ทำงานแต่ไม่มีสแกน" });
  const add = (decision) => async () => {
    if (!date.value) return showErr("atError", "เลือกวันที่ก่อน");
    showErr("atError", "");
    try {
      await api("/api/attendance/review", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ employee_id: p.employee_id, work_date: date.value, decision, manual: true }) });
      await atLoad(at.data.month);
      renderAttendance();
    } catch (err) { showErr("atError", err.message); }
  };
  return el("span", { class: "atmanual" }, el("span", { class: "muted small", text: "ทำงานแต่ไม่มีสแกน:" }), date,
    el("button", { class: "btn small", type: "button", onclick: add("approve"), text: "เพิ่ม เต็มวัน" }), el("button", { class: "btn small", type: "button", onclick: add("half"), text: "เพิ่ม ครึ่งวัน" }));
}

async function atApplySuggestions() {
  if (!confirm(`ใช้ข้อเสนอ “ค่าแรงครึ่งวัน” กับ ${at.data.totals.suggested} วัน?\nแก้เป็นอย่างอื่นรายวันได้ภายหลัง`)) return;
  showErr("atError", "");
  try {
    await api("/api/attendance/apply-suggestions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ month: at.data.month }) });
    await atLoad(at.data.month);
    renderAttendance();
  } catch (err) { showErr("atError", err.message); }
}

// ------------------------------------------------------------------ upload
async function atUploadFiles(files) {
  const note = $("atUploadNote");
  note.hidden = false;
  note.replaceChildren(el("span", { class: "muted", text: "กำลังอ่านไฟล์…" }));
  const lines = [];
  let month = "";
  for (const f of files) {
    try {
      const body = new FormData();
      body.append("file", f);
      const r = await api("/api/attendance/upload", { method: "POST", body });
      month = r.month;
      lines.push(el("div", {}, el("strong", { text: f.name }), ` — งวด ${beDate(r.period_start)} – ${beDate(r.period_end)} · ${r.people} คน · อ่านสแกน ${r.punches_read} รายการ (ใหม่ ${r.punches_added})`,
        r.unknown.length ? el("details", {}, el("summary", { class: "small", text: `ข้าม ${r.unknown.length} รหัสที่ไม่มีในรายชื่อพนักงาน (เช่น คนที่ออกไปนานแล้ว)` }), el("div", { class: "muted small", text: r.unknown.join(", ") })) : null));
    } catch (err) {
      lines.push(el("div", { class: "error", text: `${f.name}: ${err.message}` }));
    }
  }
  note.replaceChildren(...lines);
  if (month) {
    if (await atLoad(month)) { atFillFilters(); renderAttendance(); }
  }
}

function renderAtHistory() {
  const box = $("atHistory"), ups = at.data.uploads || [];
  box.hidden = !ups.length;
  if (!ups.length) return;
  box.replaceChildren(el("h3", { text: "ไฟล์ที่อัปโหลดล่าสุด" }), el("div", { class: "tablewrap" }, el("table", { class: "emptable" },
    el("thead", {}, el("tr", {}, ...["ไฟล์", "งวด", "คน", "สแกนใหม่", "อัปโหลดเมื่อ"].map((x) => el("th", { text: x })))),
    el("tbody", {}, ...ups.map((u) => el("tr", {}, el("td", { text: u.filename }), el("td", { text: `${beDate(u.period_start)} – ${beDate(u.period_end)}` }), el("td", { class: "r", text: String(u.people) }),
      el("td", { class: "r", text: String(u.punches_added) }), el("td", { text: beDate(u.at) })))))));
}

// ------------------------------------------------------------------ wiring
function atShiftMonth(n) {
  const v = $("atMonth").value;
  if (!/^\d{4}-\d{2}$/.test(v)) return;
  const idx = +v.slice(0, 4) * 12 + (+v.slice(5, 7) - 1) + n;
  $("atMonth").value = `${Math.floor(idx / 12)}-${String(idx % 12 + 1).padStart(2, "0")}`;
  atChangeMonth();
}

async function atChangeMonth() {
  at.pending = {};
  if (await atLoad($("atMonth").value)) { atFillFilters(); renderAttendance(); }
}

function initAttendance() {
  $("atMonth").addEventListener("change", atChangeMonth);
  $("atPrev").addEventListener("click", () => atShiftMonth(-1));
  $("atNext").addEventListener("click", () => atShiftMonth(1));
  $("atUpload").addEventListener("click", () => $("atFile").click());
  $("atFile").addEventListener("change", async (e) => { const files = [...e.target.files]; e.target.value = ""; if (files.length) await atUploadFiles(files); });
  $("atSearch").addEventListener("input", () => at.data && renderAttendance());
  for (const id of ["atShow", "atBranch", "atPosition", "atKind"]) $(id).addEventListener("change", () => at.data && renderAttendance());
}
