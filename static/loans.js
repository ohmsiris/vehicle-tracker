"use strict";

// เงินกู้: what employees owe the company, and how it is paid back (repaid by salary deduction, no interest).
//   #loans        every loan with its balance, this month's deductions for payroll (and "หักแล้ว" in one click), a new-loan dialog
//   #loan=<id>    one loan: its plan, the month-by-month schedule with what has been paid, the ledger, repay / borrow more / adjust
// Six ways to pay back (MODES) and a start that can be delayed (first_month); the schedule shown while filling the form comes from the server
// (/api/loans/preview), the same code that saves it. Data: /api/loans (app/loans.py). Same PIN lock as พนักงาน.
// Loaded BEFORE app.js: nothing here runs at load time; helpers from app.js / board.js / dashboard.js / employees.js are used at call time.

const ln = { all: [], totals: null, opts: null, ready: true, month: "", cur: null, editing: null, timer: null };

const lnMoney = (n) => Number(n || 0).toLocaleString("th-TH", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const lnMonthLabel = (ym) => (ym ? beMonth(ym) : "");
const LN_STATUS = { active: ["กำลังผ่อน", "warn"], closed: ["จ่ายครบแล้ว", "ok"], cancelled: ["ยกเลิก", ""] };
const LN_STATE = { paid: ["จ่ายแล้ว", "ok"], partial: ["จ่ายบางส่วน", "warn"], due: ["ถึงกำหนด", "warn"], overdue: ["เลยกำหนด", "urgent"], future: ["ยังไม่ถึง", ""] };
const lnBadge = (map, key) => el("span", { class: `badge ${map[key] ? map[key][1] : ""}`.trim(), text: map[key] ? map[key][0] : key });

// ------------------------------------------------------------------ loading
async function lnLoad() {
  try {
    const [opts, d] = await Promise.all([ln.opts ? Promise.resolve(ln.opts) : api("/api/loans/options"), api("/api/loans")]);
    ln.opts = opts;
    Object.assign(ln, { all: d.loans, totals: d.totals, ready: d.ready, month: d.month || ln.month });
    $("hrLockBox").hidden = true;
    return true;
  } catch (err) {
    if (err.status === 423) { showHrLock(); return false; }
    showErr("loanError", err.message);
    return false;
  }
}

// ------------------------------------------------------------------ the list
async function openLoans() {
  showView("loans");
  showErr("loanError", "");
  if (!(await lnLoad())) return;
  $("loanNeedsV23").hidden = ln.ready;
  if (!$("lnMonth").value) $("lnMonth").value = ln.month;
  fillLoanFilters();
  renderLoans();
  loadMonth();
}

function fillLoanFilters() {
  const keep = $("lnKind").value, branches = [...new Set(ln.all.map((l) => l.branch).filter(Boolean))], keepB = $("lnBranch").value;
  $("lnKind").replaceChildren(el("option", { value: "", text: "ทุกประเภท" }), ...Object.entries(ln.opts.kinds).map(([k, v]) => el("option", { value: k, text: v })));
  $("lnBranch").replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...branches.map((b) => el("option", { value: b, text: b })));
  $("lnKind").value = keep;
  $("lnBranch").value = keepB;
}

function visibleLoans() {
  const tokens = $("lnSearch").value.trim().split(/\s+/).map(empNorm).filter(Boolean);
  const status = $("lnStatus").value, kind = $("lnKind").value, branch = $("lnBranch").value;
  $("lnClear").hidden = !(tokens.length || kind || branch || status !== "active");
  return ln.all
    .filter((l) => (status === "all" ? true : l.status === status))
    .filter((l) => !kind || l.kind === kind)
    .filter((l) => !branch || l.branch === branch)
    .filter((l) => !tokens.length || tokens.every((t) => empNorm([l.employee, l.code, l.reason, l.memo_ref].join("|")).includes(t)));
}

function renderLoans() {
  const t = ln.totals || { active: 0, balance: 0, overdue: 0, due_now: 0, people: 0 };
  $("lnKpis").replaceChildren(
    kpi("ยอดค้างรวม", `${lnMoney(t.balance)} ฿`, `${t.active} สัญญา · ${t.people} คน`, t.balance ? "warn" : ""),
    kpi("ต้องหักเดือนนี้", `${lnMoney(t.due_now)} ฿`, lnMonthLabel(ln.month), ""),
    kpi("ค้างหักจากเดือนก่อน", `${lnMoney(t.overdue)} ฿`, t.overdue ? "ถึงกำหนดแล้วแต่ยังไม่ได้บันทึกว่าหัก" : "ไม่มี", t.overdue ? "bad" : ""));
  const rows = visibleLoans();
  const head = el("tr", {}, ...["พนักงาน", "ประเภท", "ยอดกู้", "คงเหลือ", "แผนการคืน", "หักครั้งต่อไป", ""].map((x) => el("th", { text: x })));
  const body = rows.map((l) => el("tr", { class: `emprow${l.status !== "active" ? " off" : ""}`, tabindex: "0", onclick: () => goHash(`#loan=${l.id}`) },
    el("td", {}, el("div", { class: "emname", text: l.employee }), el("div", { class: "muted small", text: `${l.code} · ${l.loan_date ? beDate(l.loan_date) : ""}` })),
    el("td", { text: l.kind_label }), el("td", { class: "r", text: lnMoney(l.amount) }),
    el("td", { class: "r" }, el("strong", { text: lnMoney(l.balance) }), l.overdue ? el("div", { class: "small", style: "color:var(--bad)", text: `ค้างหัก ${lnMoney(l.overdue)}` }) : null),
    el("td", { class: "small", text: l.plan }),
    el("td", { class: "small", text: l.next ? `${lnMonthLabel(l.next.month)} · ${lnMoney(l.next.amount)}` : l.status === "active" ? "—" : "" }),
    el("td", {}, l.status === "active" ? null : lnBadge(LN_STATUS, l.status))));
  fill($("lnCount"), `พบ ${rows.length} สัญญา`);
  $("lnList").replaceChildren(rows.length ? el("div", { class: "tablewrap" }, el("table", { class: "emptable lntable" }, el("thead", {}, head), el("tbody", {}, ...body)))
    : el("p", { class: "muted empty", text: ln.all.length ? "ไม่พบเงินกู้ที่ตรงกับตัวกรอง" : "ยังไม่มีเงินกู้ — กด “+ บันทึกเงินกู้ใหม่”" }));
}

function clearLoanFilters() {
  $("lnSearch").value = "";
  $("lnStatus").value = "active";
  for (const id of ["lnKind", "lnBranch"]) $(id).value = "";
  renderLoans();
}

// ---- what payroll deducts in a month
async function loadMonth() {
  const month = $("lnMonth").value;
  try {
    const d = await api(`/api/loans/month${month ? `?month=${month}` : ""}`);
    ln.monthData = d;
    renderMonth();
  } catch (err) {
    if (err.status !== 423) showErr("loanError", err.message);
  }
}

function renderMonth() {
  const d = ln.monthData;
  if (!d) return;
  $("lnMonthTotal").textContent = d.rows.length ? `${d.rows.length} คน · ต้องหัก ${lnMoney(d.total_due)} บาท${d.total_overdue ? ` · ค้างจากเดือนก่อน ${lnMoney(d.total_overdue)}` : ""}` : "เดือนนี้ไม่มียอดต้องหัก";
  $("lnApply").disabled = !d.rows.some((r) => r.due > 0);
  const head = el("tr", {}, ...["รหัส", "พนักงาน", "ต้องหักเดือนนี้", "ค้างก่อนหน้า", "บันทึกแล้ว", "คงเหลือ"].map((x) => el("th", { text: x })));
  const body = d.rows.map((r) => el("tr", { class: "emprow", onclick: () => goHash(`#loan=${r.loan_id}`) },
    el("td", { class: "emcode", text: r.code }), el("td", { text: `${r.employee}${r.kind_label ? ` · ${r.kind_label}` : ""}` }), el("td", { class: "r", text: lnMoney(r.due) }),
    el("td", { class: "r", text: r.overdue_before ? lnMoney(r.overdue_before) : "" }), el("td", { class: "r", text: r.recorded ? lnMoney(r.recorded) : "" }), el("td", { class: "r", text: lnMoney(r.balance) })));
  $("lnMonthList").replaceChildren(d.rows.length ? el("div", { class: "tablewrap" }, el("table", { class: "emptable lntable" }, el("thead", {}, head), el("tbody", {}, ...body))) : el("span"));
}

async function applyMonth() {
  const d = ln.monthData;
  if (!d || !confirm(`บันทึกว่าหักเงินเดือน ${lnMonthLabel(d.month)} แล้ว ${d.rows.filter((r) => r.due > 0).length} รายการ รวม ${lnMoney(d.total_due)} บาท?\n(บันทึกเป็นรายการ “คืน” ของแต่ละสัญญา — ลบทีละรายการได้ในหน้าเงินกู้)`)) return;
  try {
    await api("/api/loans/month/apply", jsonOpts("POST", { month: d.month }));
    await lnLoad();
    renderLoans();
    await loadMonth();
  } catch (err) { showErr("loanError", err.message); }
}

function exportMonth() {
  const d = ln.monthData;
  if (!d) return;
  const cols = [["รหัส", (r) => r.code], ["ชื่อ", (r) => r.employee], ["สาขา", (r) => r.branch], ["ประเภท", (r) => r.kind_label], ["ยอดที่ต้องหัก", (r) => r.due], ["ค้างก่อนหน้า", (r) => r.overdue_before],
    ["บันทึกแล้ว", (r) => r.recorded], ["ยอดคงเหลือ", (r) => r.balance]];
  lnCsv(`หักเงินกู้-${d.month}.csv`, cols, d.rows);
}

function exportLoans() {
  const cols = [["รหัส", (l) => l.code], ["ชื่อ", (l) => l.employee], ["สาขา", (l) => l.branch], ["ประเภท", (l) => l.kind_label], ["วันที่กู้ (ค.ศ.)", (l) => l.loan_date], ["ยอดกู้", (l) => l.amount],
    ["คืนแล้ว", (l) => l.repaid], ["คงเหลือ", (l) => l.balance], ["แผนการคืน", (l) => l.plan], ["สถานะ", (l) => (LN_STATUS[l.status] || [l.status])[0]], ["เหตุผล", (l) => l.reason], ["อ้างอิงบันทึก", (l) => l.memo_ref]];
  lnCsv(`เงินกู้-${todayISO()}.csv`, cols, visibleLoans());
}

function lnCsv(name, cols, rows) {
  const q = (x) => `"${String(x ?? "").replace(/"/g, '""')}"`;
  const blob = new Blob(["﻿" + [cols.map(([h]) => q(h)).join(","), ...rows.map((r) => cols.map(([, f]) => q(f(r))).join(","))].join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ------------------------------------------------------------------ the new-loan / edit-plan dialog
const LN_MODE_FIELDS = { monthly_fixed: ["lndAmountEach"], months_equal: ["lndMonths"], percent: ["lndPercent"], custom: ["lndCustom"], lump_sum: [], flexible: [] };

async function openLoanDialog(employeeId, loan) {
  if (!emp.loaded && !(await empLoad())) return;
  if (!ln.opts) await lnLoad();
  ln.editing = loan || null;
  const live = emp.all.filter((e) => e.status !== "resigned");
  $("lndEmpList").replaceChildren(...live.map((e) => el("option", { value: `${e.code} · ${e.display_name}` })));
  $("lndTitle").textContent = loan ? `แก้ไขแผนการคืน — ${loan.employee}` : "บันทึกเงินกู้ใหม่";
  $("lndKind").replaceChildren(...Object.entries(ln.opts.kinds).map(([k, v]) => el("option", { value: k, text: v })));
  $("lndMode").replaceChildren(...Object.entries(ln.opts.modes).map(([k, v]) => el("option", { value: k, text: v })));
  const e = employeeId ? emp.all.find((x) => x.id === employeeId) : null;
  $("lndEmployee").value = loan ? `${loan.code} · ${loan.employee}` : e ? `${e.code} · ${e.display_name}` : "";
  $("lndEmployee").disabled = !!loan;
  $("lndKind").value = loan ? loan.kind : "loan";
  $("lndAmount").value = loan ? loan.amount : "";
  $("lndAmount").disabled = !!loan;
  $("lndDate").value = loan ? loan.loan_date : todayISO();
  $("lndDate").max = todayISO();
  $("lndDate").disabled = !!loan;
  $("lndDateCap").textContent = dateCaption($("lndDate").value);
  $("lndReason").value = loan ? loan.reason : "";
  $("lndMemo").value = loan ? loan.memo_ref : "";
  $("lndApprover").value = loan ? loan.approved_by : "";
  $("lndNotes").value = loan ? loan.notes : "";
  $("lndMode").value = loan ? loan.repay_mode : "monthly_fixed";
  $("lndAmountEachN").value = loan && loan.repay_amount ? loan.repay_amount : "";
  $("lndMonthsN").value = loan && loan.repay_months ? loan.repay_months : "";
  $("lndPercentN").value = loan && loan.repay_percent ? loan.repay_percent : "";
  $("lndCustomN").value = loan && loan.repay_mode === "custom" ? loan.schedule.map((r) => r.amount).join(", ") : "";
  $("lndStart").value = loan ? "pick" : "1";
  $("lndStartMonth").value = loan ? loan.first_month : "";
  showErr("lndError", "");
  syncLoanDialog();
  $("loanDialog").showModal();
}

function lnFirstMonth() {
  const sel = $("lndStart").value;
  if (sel === "pick") return $("lndStartMonth").value || "";
  const base = ($("lndDate").value || todayISO()).slice(0, 7);
  const [y, m] = base.split("-").map(Number);
  const idx = y * 12 + (m - 1) + Number(sel);
  return `${String(Math.floor(idx / 12)).padStart(4, "0")}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

function lnFormData() {
  const code = ($("lndEmployee").value.split("·")[0] || "").trim().toUpperCase();
  const person = emp.all.find((e) => e.code.toUpperCase() === code);
  return {
    employee_id: person ? person.id : "", kind: $("lndKind").value, amount: $("lndAmount").value, loan_date: $("lndDate").value, reason: $("lndReason").value, memo_ref: $("lndMemo").value,
    approved_by: $("lndApprover").value, notes: $("lndNotes").value, repay_mode: $("lndMode").value, first_month: lnFirstMonth(), repay_amount: $("lndAmountEachN").value,
    repay_months: $("lndMonthsN").value, repay_percent: $("lndPercentN").value, amounts: $("lndCustomN").value,
  };
}

function syncLoanDialog() {
  const mode = $("lndMode").value;
  for (const [m, ids] of Object.entries(LN_MODE_FIELDS)) for (const id of ids) $(id).hidden = m !== mode;
  $("lndStartMonthRow").hidden = $("lndStart").value !== "pick";
  clearTimeout(ln.timer);
  ln.timer = setTimeout(previewLoan, 250);
}

async function previewLoan() {
  const box = $("lndPreview");
  const d = lnFormData();
  if (!(Number(String(d.amount).replace(/,/g, "")) > 0)) { box.replaceChildren(el("p", { class: "muted small", text: "ใส่ยอดเงินเพื่อดูตารางการหัก" })); return; }
  try {
    const p = await api("/api/loans/preview", jsonOpts("POST", d));
    if (d.repay_mode === "flexible") { box.replaceChildren(el("p", { class: "muted small", text: "ไม่กำหนดงวด — บันทึกการคืนเองเมื่อมีการหัก/คืน" })); return; }
    let left = p.total;
    const rows = p.schedule.map((r) => { left = Math.round((left - r.amount) * 100) / 100; return el("tr", {}, el("td", { text: lnMonthLabel(r.month) }), el("td", { class: "r", text: lnMoney(r.amount) }), el("td", { class: "r", text: lnMoney(Math.max(left, 0)) })); });
    box.replaceChildren(el("table", { class: "emptable lntable" }, el("thead", {}, el("tr", {}, ...["เดือนที่หัก", "ยอดหัก", "คงเหลือหลังหัก"].map((x) => el("th", { text: x })))), el("tbody", {}, ...rows)),
      p.unscheduled ? el("p", { class: "small", style: "color:var(--warn)", text: `ยอดที่ยังไม่ได้กำหนดงวด ${lnMoney(p.unscheduled)} บาท` }) : null);
  } catch (err) {
    box.replaceChildren(el("p", { class: "small", style: "color:var(--bad)", text: err.message }));
  }
}

async function saveLoan() {
  const data = lnFormData();
  $("lndSave").disabled = true;
  try {
    let saved;
    if (ln.editing) saved = await api(`/api/loans/${ln.editing.id}`, jsonOpts("PATCH", { kind: data.kind, reason: data.reason, memo_ref: data.memo_ref, approved_by: data.approved_by, notes: data.notes, repay_mode: data.repay_mode,
      first_month: data.first_month, repay_amount: data.repay_amount, repay_months: data.repay_months, repay_percent: data.repay_percent, amounts: data.amounts }));
    else {
      if (!data.employee_id) throw new Error("เลือกพนักงานจากรายการ (พิมพ์รหัสหรือชื่อ)");
      saved = await api("/api/loans", jsonOpts("POST", data));
    }
    $("loanDialog").close();
    if (ln.editing) { ln.cur = saved; renderLoan(); } else goHash(`#loan=${saved.id}`);
  } catch (err) {
    showErr("lndError", err.message);
  } finally {
    $("lndSave").disabled = false;
  }
}

// ------------------------------------------------------------------ one loan
async function openLoan(id) {
  showView("loan");
  showErr("loanDetailError", "");
  if (!id) { goHash("#loans"); return; }
  try {
    ln.cur = await api(`/api/loans/${id}`);
  } catch (err) {
    if (err.status === 423) { showHrLock(); return; }
    showErr("loanDetailError", err.message);
    $("loanHead").replaceChildren();
    $("loanBody").replaceChildren();
    return;
  }
  renderLoan();
}

function renderLoan() {
  const l = ln.cur;
  const btn = (text, onclick, cls = "") => el("button", { class: `btn${cls ? ` ${cls}` : ""}`, type: "button", text, onclick });
  fill($("loanHead"),
    el("div", { class: "tk-title" }, el("span", { class: "tk-plate", text: l.employee }), el("span", { class: "muted", text: l.code })),
    el("div", { class: "tk-sub muted", text: `${l.kind_label} · กู้เมื่อ ${beDate(l.loan_date)}${l.reason ? ` · ${l.reason}` : ""}` }),
    el("div", { class: "tk-badges" }, lnBadge(LN_STATUS, l.status), l.overdue ? el("span", { class: "badge urgent", text: `ค้างหัก ${lnMoney(l.overdue)}` }) : null),
    el("div", { class: "hstats" }, hStat(lnMoney(l.total), "ยอดรวม"), hStat(lnMoney(l.repaid), "คืนแล้ว"), hStat(lnMoney(l.balance), "คงเหลือ", l.balance ? "warn" : "")));
  fill($("loanActions"), l.status === "cancelled" ? null : btn("ยกเลิกเงินกู้", cancelLoan, "danger"), l.entries.length ? null : btn("ลบ (เพิ่มผิด)", deleteLoan, "danger"),
    l.status === "active" ? btn("แก้ไขแผนการคืน", () => openLoanDialog(null, l), "primary") : null);
  const card = (title, ...rows) => el("section", { class: "card tkcard" }, el("h3", { text: title }), ...rows.filter(Boolean));
  const sched = l.schedule.length
    ? el("div", { class: "tablewrap" }, el("table", { class: "emptable lntable" }, el("thead", {}, el("tr", {}, ...["เดือนที่หัก", "ยอดที่ต้องหัก", "จ่ายแล้ว", "สถานะ"].map((x) => el("th", { text: x })))),
      el("tbody", {}, ...l.schedule.map((r) => el("tr", {}, el("td", { text: lnMonthLabel(r.month) }), el("td", { class: "r", text: lnMoney(r.amount) }), el("td", { class: "r", text: r.paid ? lnMoney(r.paid) : "" }), el("td", {}, lnBadge(LN_STATE, r.state)))))))
    : el("p", { class: "muted small", text: l.repay_mode === "flexible" ? "ไม่กำหนดงวด — บันทึกการคืนเองเมื่อมีการหัก/คืน" : "ไม่มีงวดที่ต้องหัก" });
  const entries = l.entries.length ? el("ul", { class: "hlist" }, ...l.entries.map((e) => el("li", { class: "hrow" },
    el("span", { class: "hrow-main", text: `${beDate(e.entry_date)} · ${{ repay: "คืน", add: "กู้เพิ่ม", adjust: "ปรับยอด" }[e.kind]} ${lnMoney(e.amount)}${e.method_label ? ` (${e.method_label}${e.pay_month ? ` ${lnMonthLabel(e.pay_month)}` : ""})` : ""}${e.note ? ` — ${e.note}` : ""}` }),
    el("button", { class: "hlink", type: "button", text: "ลบ", onclick: () => deleteEntry(e) })))) : el("p", { class: "muted small", text: "ยังไม่มีรายการคืน" });
  fill($("loanBody"),
    card("แผนการคืน", kvAll("วิธีคืน", l.mode_label), kvAll("รายละเอียด", l.plan), kvAll("เริ่มหัก", l.first_month ? lnMonthLabel(l.first_month) : ""), kvAll("ครั้งต่อไป", l.next ? `${lnMonthLabel(l.next.month)} · ${lnMoney(l.next.amount)} บาท` : ""),
      kvAll("อ้างอิงบันทึกข้อความ", l.memo_ref), kvAll("ผู้อนุมัติ", l.approved_by), kvAll("หมายเหตุ", l.notes),
      el("div", { class: "tk-link" }, el("button", { class: "btn small", type: "button", text: "ข้อมูลพนักงาน", onclick: () => goHash(`#employee=${l.employee_id}`) }))),
    card("ตารางการหัก", sched),
    l.status === "cancelled" ? null : card("บันทึกรายการ", entryForm(l)),
    card("ประวัติรายการ", entries));
}

function entryForm(l) {
  const next = l.next || null;
  const kind = el("select", { id: "leKind" }, el("option", { value: "repay", text: "คืนเงิน (หักเงินเดือน)" }), el("option", { value: "add", text: "กู้เพิ่ม" }), el("option", { value: "adjust", text: "ปรับยอด (ใส่ - เพื่อลด)" }));
  const method = el("select", { id: "leMethod" }, ...Object.entries(ln.opts.methods).map(([k, v]) => el("option", { value: k, text: v })));
  const amount = el("input", { id: "leAmount", type: "text", inputmode: "decimal", value: next ? String(next.amount) : "" });
  const date = el("input", { id: "leDate", type: "date", value: todayISO(), max: todayISO() });
  const month = el("input", { id: "leMonth", type: "month", value: next ? next.month : "" });
  const note = el("input", { id: "leNote", type: "text", maxlength: "300", placeholder: "หมายเหตุ (ไม่บังคับ)" });
  return el("div", { class: "lnentry" },
    el("div", { class: "lnfields" }, el("div", {}, el("label", { text: "รายการ" }), kind), el("div", {}, el("label", { text: "จำนวนเงิน (บาท)" }), amount), el("div", {}, el("label", { text: "วันที่" }), date),
      el("div", {}, el("label", { text: "วิธี" }), method), el("div", {}, el("label", { text: "หักในเดือน (ถ้าหักเงินเดือน)" }), month)),
    note, el("p", { class: "error", id: "leError", hidden: true }),
    el("button", { class: "btn primary", type: "button", text: "บันทึกรายการ", onclick: saveEntry }));
}

async function saveEntry() {
  const body = { kind: $("leKind").value, amount: $("leAmount").value, entry_date: $("leDate").value, method: $("leMethod").value, pay_month: $("leMonth").value || null, note: $("leNote").value };
  try {
    ln.cur = await api(`/api/loans/${ln.cur.id}/entries`, jsonOpts("POST", body));
    renderLoan();
  } catch (err) { showErr("leError", err.message); }
}

async function deleteEntry(e) {
  if (!confirm("ลบรายการนี้?")) return;
  try {
    ln.cur = await api(`/api/loans/${ln.cur.id}/entries/${e.id}`, { method: "DELETE" });
    renderLoan();
  } catch (err) { showErr("loanDetailError", err.message); }
}

async function cancelLoan() {
  const note = prompt(`ยกเลิกเงินกู้ของ ${ln.cur.employee}?\n(เก็บไว้ในประวัติ แต่ไม่นับเป็นยอดค้างอีก) — เหตุผล:`);
  if (note === null) return;
  try {
    ln.cur = await api(`/api/loans/${ln.cur.id}/cancel`, jsonOpts("POST", { note }));
    renderLoan();
  } catch (err) { showErr("loanDetailError", err.message); }
}

async function deleteLoan() {
  if (!confirm(`ลบเงินกู้นี้ออกจากระบบ?\n\nใช้เฉพาะกรณีบันทึกผิด และยังไม่มีรายการคืน`)) return;
  try {
    await api(`/api/loans/${ln.cur.id}`, { method: "DELETE" });
    goHash("#loans");
  } catch (err) { showErr("loanDetailError", err.message); }
}

// ------------------------------------------------------------------ on a person's page
async function renderEmployeeLoans(person) {
  const box = document.getElementById("empLoansBox");
  if (!box) return;
  try {
    const d = await api(`/api/loans?employee_id=${person.id}`);
    if (!d.ready) { box.replaceChildren(el("p", { class: "muted small", text: "ต้องรัน schema_v23.sql ก่อนจึงใช้เงินกู้ได้" })); return; }
    const live = d.loans.filter((l) => l.status === "active");
    box.replaceChildren(
      el("div", { class: "hstats" }, hStat(lnMoney(live.reduce((s, l) => s + l.balance, 0)), "ยอดค้างรวม", live.length ? "warn" : ""), hStat(live.length, "สัญญาที่ผ่อนอยู่")),
      d.loans.length ? el("ul", { class: "hlist" }, ...d.loans.map((l) => el("li", { class: "hrow", style: "cursor:pointer", onclick: () => goHash(`#loan=${l.id}`) },
        el("span", { class: "hrow-main", text: `${l.kind_label} ${lnMoney(l.amount)} · ${beDate(l.loan_date)}` }),
        el("span", { class: "hrow-side muted small", text: l.status === "active" ? `เหลือ ${lnMoney(l.balance)} · ${l.plan}` : (LN_STATUS[l.status] || [""])[0] })))) : el("p", { class: "muted small", text: "ไม่มีเงินกู้" }),
      el("button", { class: "btn small", type: "button", text: "+ บันทึกเงินกู้ใหม่", onclick: () => openLoanDialog(person.id) }));
  } catch (err) {
    box.replaceChildren(el("p", { class: "muted small", text: err.status === 423 ? "ล็อกอยู่ (ใส่ PIN)" : err.message }));
  }
}

// ------------------------------------------------------------------ init
function initLoans() {
  $("lnSearch").addEventListener("input", renderLoans);
  for (const id of ["lnStatus", "lnKind", "lnBranch"]) $(id).addEventListener("change", renderLoans);
  $("lnClear").addEventListener("click", clearLoanFilters);
  $("lnAdd").addEventListener("click", () => openLoanDialog(null));
  $("lnExport").addEventListener("click", exportLoans);
  $("lnMonth").addEventListener("change", loadMonth);
  $("lnApply").addEventListener("click", applyMonth);
  $("lnMonthCsv").addEventListener("click", exportMonth);
  $("lnBack").addEventListener("click", () => goHash("#loans"));
  $("lndCancel").addEventListener("click", () => $("loanDialog").close());
  $("lndSave").addEventListener("click", saveLoan);
  for (const id of ["lndMode", "lndStart", "lndStartMonth", "lndAmount", "lndDate", "lndAmountEachN", "lndMonthsN", "lndPercentN", "lndCustomN"]) {
    $(id).addEventListener("input", syncLoanDialog);
    $(id).addEventListener("change", syncLoanDialog);
  }
  bindDateField($("lndDate"), $("lndDateCap"));
}
