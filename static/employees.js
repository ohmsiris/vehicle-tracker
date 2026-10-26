"use strict";

// พนักงาน: the staff of both branches. A list (#employees) with the same kind of filter card as the other pages, and one page
// per person (#employee=<id>) with the details and, later, their loans, warning letters / memos and payslip checks.
// Data: /api/employees (app/employees.py). The whole section can have a second PIN (HR_PIN on the server): when it is set and
// not given yet, the server answers 423 and the PIN box (hrLockBox) shows instead of the page.
// Loaded BEFORE app.js: nothing here runs at load time; helpers from app.js / board.js / fleet.js are used at call time.

const emp = { all: [], branches: [], positions: [], summary: null, ready: true, loaded: false, current: null, drivers: null };

const EMP_SORTS = {
  code: (a, b) => a.code.localeCompare(b.code),
  name: (a, b) => a.full_name.localeCompare(b.full_name, "th") || a.code.localeCompare(b.code),
  start: (a, b) => (a.start_date || "9999").localeCompare(b.start_date || "9999") || a.code.localeCompare(b.code),
};
const empNorm = (s) => String(s || "").toLowerCase().replace(/[\s\-_.()]/g, "");
const empLabel = (e) => (e.nickname ? `${e.display_name} (${e.nickname})` : e.display_name);
const EMP_GENDER_OF = { "นาย": "ชาย", "นาง": "หญิง", "นางสาว": "หญิง" };
// the same reading as app/employees.py split_prefix: a title typed at the start of the name -> [title, the rest]
const EMP_TITLES = [[/^\s*นางสาว\s*/, "นางสาว"], [/^\s*น\.ส\.\s*/, "นางสาว"], [/^\s*นาง\s*/, "นาง"], [/^\s*นาย\s*/, "นาย"], [/^\s*(?:miss|ms)\.?\s+/i, "นางสาว"], [/^\s*mrs\.?\s*/i, "นาง"], [/^\s*mr\.?\s*/i, "นาย"]];
function empSplitTitle(name) {
  for (const [re, title] of EMP_TITLES) {
    const m = re.exec(name);
    if (m && name.slice(m[0].length).trim()) return [title, name.slice(m[0].length).trim()];
  }
  return [null, name];
}

// ------------------------------------------------------------------ loading + the PIN
async function empLoad() {
  try {
    const d = await api("/api/employees");
    Object.assign(emp, { all: d.employees, branches: d.branches, positions: d.positions, summary: d.summary, ready: d.ready, loaded: true });
    $("hrLockBox").hidden = true;
    return true;
  } catch (err) {
    if (err.status === 423) { showHrLock(); return false; }
    showErr("empError", err.message);
    return false;
  }
}

function showHrLock(wrong = false) {
  $("hrLockBox").hidden = false;
  $("hrPinError").hidden = !wrong;
  $("hrPin").value = "";
  $("hrPin").focus();
}

function submitHrPin() {
  saveHrPin($("hrPin").value.trim());
  $("hrLockBox").hidden = true;
  const wasDetail = !$("employeeView").hidden;
  (wasDetail ? openEmployee(emp.current && emp.current.id) : openEmployees()).then(() => { if (!$("hrLockBox").hidden) showHrLock(true); });
}

// ------------------------------------------------------------------ the list
async function openEmployees() {
  showView("employees");
  showErr("empError", "");
  if (!(await empLoad())) return;
  fillEmpFilters();
  $("empNeedsV21").hidden = emp.ready;
  renderEmployees();
}

function fillEmpFilters() {
  const keepB = $("emBranch").value, keepP = $("emPosition").value;
  $("emBranch").replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...emp.branches.map((b) => el("option", { value: b, text: b })));
  $("emPosition").replaceChildren(el("option", { value: "", text: "ทุกตำแหน่ง" }), ...emp.positions.map((p) => el("option", { value: p, text: p })),
    el("option", { value: "__none", text: "ยังไม่ระบุตำแหน่ง" }));
  $("emBranch").value = keepB;
  $("emPosition").value = keepP;
}

function visibleEmployees() {
  const tokens = $("emSearch").value.trim().split(/\s+/).map(empNorm).filter(Boolean);
  const branch = $("emBranch").value, pos = $("emPosition").value, status = $("emStatus").value, sort = EMP_SORTS[$("emSort").value] || EMP_SORTS.code;
  const gender = $("emGender").value;
  $("emClear").hidden = !(tokens.length || branch || pos || gender || status !== "active" || $("emSort").value !== "code");
  return emp.all
    .filter((e) => (status === "all" ? true : status === "resigned" ? e.status === "resigned" : e.status !== "resigned"))
    .filter((e) => !branch || e.branch === branch)
    .filter((e) => !pos || (pos === "__none" ? !e.position : e.position === pos))
    .filter((e) => !gender || (gender === "__none" ? !e.gender : e.gender === gender))
    .filter((e) => {
      if (!tokens.length) return true;
      const hay = [e.code, e.display_name, e.nickname, e.position, e.phone, e.branch].map(empNorm).join("|");
      return tokens.every((t) => hay.includes(t));
    })
    .sort(sort);
}

function renderEmployees() {
  const rows = visibleEmployees();
  const s = emp.summary || { active: 0, resigned: 0, by_position: [] };
  fill($("emCount"), `พบ ${rows.length} คน จากที่ทำงานอยู่ ${s.active} คน${s.resigned ? ` (ลาออกแล้ว ${s.resigned})` : ""}`,
    s.by_position.length ? ` · ${s.by_position.map(([p, n]) => `${p} ${n}`).join(" · ")}` : null,
    s.by_gender ? ` · ชาย ${s.by_gender["ชาย"]} · หญิง ${s.by_gender["หญิง"]}${s.by_gender["ยังไม่ระบุ"] ? ` · ยังไม่ระบุเพศ ${s.by_gender["ยังไม่ระบุ"]}` : ""}` : null);
  const head = el("tr", {}, ...["รหัส", "ชื่อ-สกุล", "เพศ", "สาขา", "ตำแหน่ง", "เริ่มงาน", ""].map((t) => el("th", { text: t })));
  const body = rows.map((e) => {
    const tr = el("tr", { class: `emprow${e.status === "resigned" ? " off" : ""}`, tabindex: "0", onclick: () => goHash(`#employee=${e.id}`) },
      el("td", { class: "emcode", text: e.code }), el("td", { class: "emname", text: empLabel(e) }), el("td", { text: e.gender }), el("td", { text: e.branch }),
      el("td", { text: e.position }), el("td", { text: e.start_date ? beDate(e.start_date) : "" }),
      el("td", {}, e.status === "resigned" ? el("span", { class: "badge", text: "ลาออกแล้ว" }) : null));
    tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter") goHash(`#employee=${e.id}`); });
    return tr;
  });
  $("empList").replaceChildren(rows.length
    ? el("div", { class: "tablewrap" }, el("table", { class: "emptable" }, el("thead", {}, head), el("tbody", {}, ...body)))
    : el("p", { class: "muted empty", text: emp.loaded && !emp.all.length ? "ยังไม่มีพนักงาน — กด “+ เพิ่มพนักงาน”" : "ไม่พบพนักงานที่ตรงกับตัวกรอง" }));
}

function clearEmpFilters() {
  $("emSearch").value = "";
  for (const id of ["emBranch", "emPosition", "emGender"]) $(id).value = "";
  $("emStatus").value = "active";
  $("emSort").value = "code";
  renderEmployees();
}

function exportEmployees() {
  const cols = [["รหัส", (e) => e.code], ["คำนำหน้า", (e) => e.prefix], ["ชื่อ-สกุล", (e) => e.full_name], ["เพศ", (e) => e.gender], ["ชื่อเล่น", (e) => e.nickname], ["สาขา", (e) => e.branch], ["ตำแหน่ง/แผนก", (e) => e.position],
    ["สถานะ", (e) => (e.status === "resigned" ? "ลาออกแล้ว" : "ทำงานอยู่")], ["วันเริ่มงาน (ค.ศ.)", (e) => e.start_date], ["วันที่ลาออก (ค.ศ.)", (e) => e.end_date],
    ["เบอร์โทร", (e) => e.phone], ["หมายเหตุ", (e) => e.notes]];
  const q = (x) => `"${String(x ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.map(([h]) => q(h)).join(","), ...visibleEmployees().map((e) => cols.map(([, f]) => q(f(e))).join(","))];
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: `พนักงาน-${todayISO()}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ------------------------------------------------------------------ one person
function tenure(startIso, endIso) {
  if (!startIso) return "";
  const s = new Date(`${startIso}T00:00:00`), e = endIso ? new Date(`${endIso}T00:00:00`) : new Date();
  let months = (e.getFullYear() - s.getFullYear()) * 12 + (e.getMonth() - s.getMonth()) - (e.getDate() < s.getDate() ? 1 : 0);
  if (months < 0) months = 0;
  const y = Math.floor(months / 12), m = months % 12;
  return y || m ? [y ? `${y} ปี` : "", m ? `${m} เดือน` : ""].filter(Boolean).join(" ") : "ไม่ถึงเดือน";
}

async function openEmployee(id) {
  showView("employee");
  showErr("empDetailError", "");
  if (!id) { goHash("#employees"); return; }
  if (!emp.loaded && !(await empLoad())) return;
  try {
    emp.current = await api(`/api/employees/${id}`);
  } catch (err) {
    if (err.status === 423) { showHrLock(); return; }
    showErr("empDetailError", err.message);
    $("empHead").replaceChildren();
    $("empBody").replaceChildren();
    return;
  }
  renderEmployee();
}

function renderEmployee() {
  const e = emp.current;
  const off = e.status === "resigned";
  fill($("empHead"),
    el("div", { class: "tk-title" }, el("span", { class: "tk-no", text: e.code }), el("span", { class: "tk-plate", text: e.display_name }), e.nickname ? el("span", { class: "muted", text: `(${e.nickname})` }) : null),
    el("div", { class: "tk-sub muted", text: [e.branch ? `สาขา${e.branch}` : "", e.position].filter(Boolean).join(" · ") }),
    el("div", { class: "tk-badges" }, off ? el("span", { class: "badge", text: "ลาออกแล้ว" }) : el("span", { class: "badge ok", text: "ทำงานอยู่" }),
      e.position ? el("span", { class: "badge shop", text: e.position }) : null));
  const btn = (text, onclick, cls = "") => el("button", { class: `btn${cls ? ` ${cls}` : ""}`, type: "button", text, onclick });
  fill($("empActions"), off ? btn("กลับเข้าทำงาน", reinstateEmployee) : btn("ลาออก", openResignDialog), btn("ลบ (เพิ่มผิด)", deleteEmployee, "danger"), btn("แก้ไข", () => openEmpDialog(e), "primary"));
  const card = (title, ...rows) => el("section", { class: "card tkcard" }, el("h3", { text: title }), ...rows.filter(Boolean));
  const soon = (title, text) => el("section", { class: "card tkcard hsoon" }, el("div", { class: "hsoon-tag", text: "เร็วๆ นี้" }), el("h3", { text: title }), el("p", { class: "muted small", text }));
  fill($("empBody"),
    card("ข้อมูลพนักงาน", kvAll("รหัส", e.code), kvAll("คำนำหน้า", e.prefix), kvAll("ชื่อ-สกุล", e.full_name), kvAll("เพศ", e.gender), kvAll("ชื่อเล่น", e.nickname), kvAll("สาขา", e.branch), kvAll("ตำแหน่ง/แผนก", e.position),
      kvAll("เบอร์โทร", "", e.phone ? el("span", { class: "kv-v" }, el("span", { text: `${e.phone} ` }), callLink(e.phone, "", true)) : null),
      kvAll("คนขับในระบบรถ", e.driver_name)),
    card("การทำงาน", kvAll("สถานะ", off ? "ลาออกแล้ว" : "ทำงานอยู่"), kvAll("วันเริ่มงาน", e.start_date ? beDate(e.start_date) : ""),
      kvAll("อายุงาน", tenure(e.start_date, e.end_date) + (off ? "" : e.start_date ? " (ถึงวันนี้)" : "")),
      off ? kvAll("วันที่ลาออก", e.end_date ? beDate(e.end_date) : "") : null, off ? kvAll("เหตุผล", e.end_reason) : null, kvAll("หมายเหตุ", e.notes)),
    el("section", { class: "card tkcard" }, el("h3", { text: "เงินกู้ / หักเงินเดือน" }), el("div", { id: "empLoansBox" }, el("p", { class: "muted small", text: "กำลังโหลด…" }))),
    soon("ใบเตือน และบันทึกข้อความ", "เอกสารที่ออกให้คนนี้ พร้อมสถานะการเซ็นรับ และระดับการเตือน"),
    soon("ตรวจสลิปเงินเดือน", "อ่านสลิปแล้วตรวจกับค่าแรง เงินกู้ และเงินสะสมของคนนี้"));
  renderEmployeeLoans(e);
}

// ------------------------------------------------------------------ add / edit
async function openEmpDialog(e) {
  const isNew = !e;
  $("empDlgTitle").textContent = isNew ? "เพิ่มพนักงาน" : "แก้ไขข้อมูลพนักงาน";
  $("emdBranch").replaceChildren(...emp.branches.map((b) => el("option", { value: b, text: b })));
  $("emdPosList").replaceChildren(...emp.positions.map((p) => el("option", { value: p })));
  $("emdCode").value = e ? e.code : "";
  $("emdName").value = e ? e.full_name : "";
  $("emdPrefix").value = e ? e.prefix : "";
  $("emdGender").value = e ? e.gender : "";
  $("emdNameHint").textContent = "";
  $("emdNick").value = e ? e.nickname : "";
  $("emdBranch").value = e ? e.branch : (emp.branches[0] || "");
  $("emdPosition").value = e ? e.position : "";
  $("emdStart").value = e && e.start_date ? e.start_date : "";
  $("emdStart").max = todayISO();
  $("emdStartCap").textContent = dateCaption($("emdStart").value);
  $("emdPhone").value = e ? e.phone : "";
  $("emdNotes").value = e ? e.notes : "";
  showErr("emdError", "");
  $("emdSave").dataset.id = e ? e.id : "";
  $("empDialog").showModal();
}

async function saveEmployee() {
  const id = $("emdSave").dataset.id;
  const body = {
    code: $("emdCode").value, full_name: $("emdName").value, prefix: $("emdPrefix").value, gender: $("emdGender").value, nickname: $("emdNick").value, branch: $("emdBranch").value, position: $("emdPosition").value,
    start_date: $("emdStart").value || null, phone: $("emdPhone").value, notes: $("emdNotes").value,
  };
  $("emdSave").disabled = true;
  try {
    const saved = await api(id ? `/api/employees/${id}` : "/api/employees", jsonOpts(id ? "PATCH" : "POST", body));
    $("empDialog").close();
    emp.loaded = false;
    await empLoad();
    if (id) { emp.current = saved; renderEmployee(); } else goHash(`#employee=${saved.id}`);
  } catch (err) {
    showErr("emdError", err.message);
  } finally {
    $("emdSave").disabled = false;
  }
}

// ------------------------------------------------------------------ leaving / back / mistake
function openResignDialog() {
  $("emrDate").value = todayISO();
  $("emrDate").max = "";
  $("emrDateCap").textContent = dateCaption($("emrDate").value);
  $("emrReason").value = "";
  showErr("emrError", "");
  $("emrTitle").textContent = `ลาออก — ${empLabel(emp.current)}`;
  $("empResignDialog").showModal();
}

async function submitResign() {
  $("emrSave").disabled = true;
  try {
    emp.current = await api(`/api/employees/${emp.current.id}/resign`, jsonOpts("POST", { end_date: $("emrDate").value || null, reason: $("emrReason").value }));
    $("empResignDialog").close();
    emp.loaded = false;
    await empLoad();
    renderEmployee();
  } catch (err) {
    showErr("emrError", err.message);
  } finally {
    $("emrSave").disabled = false;
  }
}

async function reinstateEmployee() {
  if (!confirm(`ให้ ${empLabel(emp.current)} กลับเข้าทำงาน? (ล้างวันที่และเหตุผลที่ลาออก — ประวัติอื่นยังอยู่)`)) return;
  try {
    emp.current = await api(`/api/employees/${emp.current.id}/reinstate`, jsonOpts("POST", {}));
    emp.loaded = false;
    await empLoad();
    renderEmployee();
  } catch (err) { showErr("empDetailError", err.message); }
}

async function deleteEmployee() {
  if (!confirm(`ลบ ${empLabel(emp.current)} ออกจากระบบ?\n\nใช้เฉพาะกรณีเพิ่มผิด — ถ้าเป็นคนที่ลาออกจริง ให้กด “ลาออก” แทน เพื่อเก็บประวัติไว้`)) return;
  try {
    await api(`/api/employees/${emp.current.id}`, { method: "DELETE" });
    emp.loaded = false;
    goHash("#employees");
  } catch (err) { showErr("empDetailError", err.message); }
}

// ------------------------------------------------------------------ init
function initEmployees() {
  $("emSearch").addEventListener("input", renderEmployees);
  for (const id of ["emBranch", "emPosition", "emGender", "emStatus", "emSort"]) $(id).addEventListener("change", renderEmployees);
  $("emClear").addEventListener("click", clearEmpFilters);
  $("emAdd").addEventListener("click", () => { emp.loaded ? openEmpDialog(null) : empLoad().then((ok) => ok && openEmpDialog(null)); });
  $("emExport").addEventListener("click", exportEmployees);
  $("emBack").addEventListener("click", () => goHash("#employees"));
  $("emdCancel").addEventListener("click", () => $("empDialog").close());
  $("emdSave").addEventListener("click", saveEmployee);
  // a title typed in front of the name moves to the คำนำหน้า box at once (and the gender follows it)
  $("emdName").addEventListener("input", () => {
    const [title, rest] = empSplitTitle($("emdName").value);
    if (!title) return;
    $("emdPrefix").value = title;
    $("emdGender").value = EMP_GENDER_OF[title];
    $("emdName").value = rest;
    $("emdNameHint").textContent = `แยกคำนำหน้า “${title}” ให้แล้ว — เพศ: ${EMP_GENDER_OF[title]}`;
  });
  $("emdPrefix").addEventListener("change", () => {
    const g = EMP_GENDER_OF[$("emdPrefix").value];
    if (g) $("emdGender").value = g;
    $("emdNameHint").textContent = "";
  });
  bindDateField($("emdStart"), $("emdStartCap"));
  $("emrCancel").addEventListener("click", () => $("empResignDialog").close());
  $("emrSave").addEventListener("click", submitResign);
  bindDateField($("emrDate"), $("emrDateCap"));
  $("hrPinSave").addEventListener("click", submitHrPin);
  $("hrPin").addEventListener("keydown", (ev) => { if (ev.key === "Enter") submitHrPin(); });
}
