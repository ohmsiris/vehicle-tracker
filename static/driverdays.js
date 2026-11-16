"use strict";

// ยอดขายรายวัน (คิดเงินเดือน) -- owner, 16 Nov 2026. Driver by driver, half-month by half-month (26–10, 11–25): one row per day, a column per
// product (the totals at the bottom of the paper sales sheet) and the money brought in that day. Tab goes to the next box, Enter (or the
// arrows) up / down a column, Ctrl+S saves. Server: app/driver_days.py. Hash: #driver-days/<employee id>/<company month>/<half>.

const dd_ = { ov: null, sheet: null, emp: null, month: null, half: null, dirty: false, saving: false };
const DD_DOW = ["อา.", "จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส."];
const ddNum = (v) => { const t = String(v ?? "").replace(/,/g, "").trim(); return t === "" ? null : Number(t); };
const ddBad = (v, whole) => { const n = ddNum(v); return n !== null && (!Number.isFinite(n) || n < 0 || (whole && !Number.isInteger(n))); };
const ddDMY = (iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${Number(iso.slice(0, 4)) + 543}`;
const ddMonthTH = (month) => `${ELEC_TH[Number(month.slice(5, 7)) - 1]} ${Number(month.slice(0, 4)) + 543}`;

window.addEventListener("beforeunload", (e) => { if (dd_.dirty) { e.preventDefault(); e.returnValue = ""; } });

function ddHash(emp, month, half) { return `#driver-days/${emp || ""}/${month}/${half}`; }
function ddGo(emp, month, half) {
  if (dd_.dirty && !confirm("ยังไม่ได้บันทึกตัวเลขของหน้านี้ — ออกโดยไม่บันทึก?")) return;
  dd_.dirty = false;
  goHash(ddHash(emp, month, half));
}
function ddShift(month, half, step) {               // the half-month before / after
  let y = Number(month.slice(0, 4)), m = Number(month.slice(5, 7)), h = half + step;
  if (h === 0) { h = 2; m -= 1; } else if (h === 3) { h = 1; m += 1; }
  if (m === 0) { m = 12; y -= 1; } else if (m === 13) { m = 1; y += 1; }
  return [`${y}-${String(m).padStart(2, "0")}`, h];
}

async function openDriverDays(sub = "") {
  showView("driverDays");
  showErr("ddError", "");
  const [emp, month, half] = (sub || "").split("/");
  if (dd_.dirty && (emp || null) === dd_.emp && month === dd_.month && Number(half) === dd_.half) return;     // same page, keep what is typed
  dd_.dirty = false;
  if (!dd_.ov) fill($("ddBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try {
    dd_.ov = await api(month && half ? `/api/driver-days?month=${encodeURIComponent(month)}&half=${encodeURIComponent(half)}` : "/api/driver-days");
  } catch (err) { fill($("ddBody"), null); showErr("ddError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  dd_.month = dd_.ov.month; dd_.half = dd_.ov.half; dd_.emp = emp || null; dd_.sheet = null;
  if (dd_.emp) {
    try { dd_.sheet = await api(`/api/driver-days/${encodeURIComponent(dd_.emp)}?month=${dd_.month}&half=${dd_.half}`); }
    catch (err) { showErr("ddError", `โหลดไม่สำเร็จ: ${err.message}`); dd_.emp = null; }
  }
  ddRender();
}

function ddRender() {
  const ov = dd_.ov;
  const [pm, ph] = ddShift(ov.month, ov.half, -1), [nm, nh] = ddShift(ov.month, ov.half, 1);
  const halfBtn = (h, text) => el("button", { type: "button", class: `segbtn${ov.half === h ? " active" : ""}`, text, onclick: () => ddGo(dd_.emp, ov.month, h) });
  const prevEnd = ddShift(ov.month, 1, -1)[0];
  const period = el("div", { class: "dd-period" },
    el("button", { class: "btn small", type: "button", title: "งวดก่อน", text: "‹", onclick: () => ddGo(dd_.emp, pm, ph) }),
    el("b", { text: `งวด ${ddDMY(ov.start)} – ${ddDMY(ov.end)}` }),
    el("button", { class: "btn small", type: "button", title: "งวดถัดไป", text: "›", onclick: () => ddGo(dd_.emp, nm, nh) }),
    el("div", { class: "seg dd-seg" }, halfBtn(1, `26 ${ELEC_TH[Number(prevEnd.slice(5, 7)) - 1]} – 10 ${ELEC_TH[Number(ov.month.slice(5, 7)) - 1]}`),
      halfBtn(2, `11 – 25 ${ELEC_TH[Number(ov.month.slice(5, 7)) - 1]}`)),
    el("span", { class: "muted small", text: `รอบเดือน ${ddMonthTH(ov.month)}` }));
  const chips = el("div", { class: "dd-chips" }, ...ov.drivers.map((d) => el("button", {
    type: "button", class: `dd-chip${d.id === dd_.emp ? " on" : ""}`, onclick: () => ddGo(d.id, ov.month, ov.half) },
    d.truck ? el("span", { class: "truckno", text: `#${d.truck}` }) : null,
    el("span", { text: d.nickname || d.name.split(" ")[0] }),
    el("span", { class: `dd-count${d.filled ? (d.filled >= ov.days ? " full" : " part") : ""}`, text: `${d.filled}/${ov.days}` }))));
  if (!ov.drivers.length) chips.append(el("p", { class: "muted small", text: "ไม่พบพนักงานตำแหน่ง “พนักงานขับรถสาย” ที่ยังทำงานอยู่ (สาขาสระบุรี)" }));
  fill($("ddBody"), el("section", { class: "card dd-top" }, period, chips),
    dd_.sheet ? ddSheet() : el("p", { class: "muted dd-pick", text: "เลือกคนขับด้านบนเพื่อกรอกยอดของงวดนี้" }));
  if (dd_.sheet) { const first = document.querySelector("#ddBody .dd-in"); if (first) first.focus(); }
}

function ddSheet() {
  const s = dd_.sheet, prods = s.products;
  const cols = [...prods.map((p) => ({ key: p.key, label: p.label, whole: true })), { key: "money", label: "เงินที่ได้ (บาท)", whole: false }];
  const body = el("tbody", {});
  s.rows.forEach((r, ri) => {
    const d = new Date(`${r.day}T00:00:00`);
    const tr = el("tr", { class: d.getDay() === 0 ? "dd-sun" : "" }, el("td", { class: "dd-date" }, el("span", { class: "muted small", text: DD_DOW[d.getDay()] }), ` ${r.day.slice(8, 10)}/${r.day.slice(5, 7)}`));
    cols.forEach((c, ci) => {
      const v = c.key === "money" ? r.money : (r.qty || {})[c.key];
      const inp = el("input", { type: "text", inputmode: c.whole ? "numeric" : "decimal", class: `dd-in${c.key === "money" ? " dd-money" : ""}`, value: v == null ? "" : String(v),
        "aria-label": `${ddDMY(r.day)} ${c.label}`, autocomplete: "off" });
      inp.dataset.r = String(ri); inp.dataset.c = String(ci);
      inp.addEventListener("input", () => {
        if (c.key === "money") r.money = inp.value; else { r.qty = { ...(r.qty || {}) }; r.qty[c.key] = inp.value; }
        inp.classList.toggle("flagged", ddBad(inp.value, c.whole));
        dd_.dirty = true; ddTotals(cols); ddStatus();
      });
      inp.addEventListener("keydown", ddKey);
      tr.append(el("td", { class: "r" }, inp));
    });
    body.append(tr);
  });
  const foot = el("tfoot", {}, el("tr", {}, el("td", { text: "รวม" }), ...cols.map((c) => el("td", { class: "r", id: `ddT_${c.key}` }))));
  const table = el("table", { class: "dd-grid" },
    el("thead", {}, el("tr", {}, el("th", { text: "วันที่" }), ...cols.map((c) => el("th", { class: "r", text: c.label })))), body, foot);
  const card = el("section", { class: "card dd-card" },
    el("div", { class: "dd-head" }, el("h3", { text: `${s.name}${s.nickname ? ` (${s.nickname})` : ""}` }), el("span", { id: "ddStatus", class: "small" })),
    el("div", { class: "lc-wrap" }, table),
    el("div", { class: "dd-actions" },
      el("span", { id: "ddDays", class: "muted small" }),
      el("button", { class: "btn primary", id: "ddSave", type: "button", text: "บันทึก", onclick: ddSave })));
  queueMicrotask(() => { ddTotals(cols); ddStatus(); });
  return card;
}

function ddTotals(cols) {
  const rows = dd_.sheet.rows;
  cols.forEach((c) => {
    const sum = rows.reduce((a, r) => a + (Number(ddNum(c.key === "money" ? r.money : (r.qty || {})[c.key])) || 0), 0);
    const cell = $(`ddT_${c.key}`);
    if (cell) cell.textContent = elN(sum, c.key === "money" && sum % 1 ? 2 : 0);
  });
  const days = rows.filter((r) => ddNum(r.money) !== null || Object.values(r.qty || {}).some((v) => ddNum(v) !== null)).length;
  if ($("ddDays")) $("ddDays").textContent = `กรอกแล้ว ${days} จาก ${rows.length} วัน`;
}

function ddStatus() {
  const st = $("ddStatus");
  if (!st) return;
  if (dd_.saving) { st.className = "small muted"; st.textContent = "กำลังบันทึก…"; return; }
  if (dd_.dirty) { st.className = "small dd-unsaved"; st.textContent = "ยังไม่ได้บันทึก"; return; }
  const at = dd_.sheet.saved_at;
  st.className = "small muted";
  st.textContent = at ? `บันทึกล่าสุด ${ddDMY(at.slice(0, 10))} ${new Date(at).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" })}` : "ยังไม่มีข้อมูลของงวดนี้";
}

function ddKey(e) {
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) { e.preventDefault(); ddSave(); return; }
  const r = Number(e.target.dataset.r), c = Number(e.target.dataset.c);
  const step = e.key === "Enter" || e.key === "ArrowDown" ? (e.shiftKey && e.key === "Enter" ? -1 : 1) : e.key === "ArrowUp" ? -1 : 0;
  if (!step) return;
  const next = document.querySelector(`#ddBody .dd-in[data-r="${r + step}"][data-c="${c}"]`);
  e.preventDefault();
  if (next) { next.focus(); next.select(); }
}

async function ddSave() {
  if (!dd_.sheet || dd_.saving) return;
  const bad = document.querySelector("#ddBody .dd-in.flagged");
  if (bad) { showErr("ddError", "มีช่องที่ไม่ใช่ตัวเลข (สีแดง) — แก้ก่อนบันทึก"); bad.focus(); return; }
  showErr("ddError", "");
  const s = dd_.sheet;
  const rows = s.rows.map((r) => ({ day: r.day, qty: r.qty || {}, money: r.money }));
  dd_.saving = true; ddStatus();
  const btn = $("ddSave"); if (btn) btn.disabled = true;
  try {
    const saved = await api(`/api/driver-days/${encodeURIComponent(s.employee_id)}`, jsonOpts("PUT", { month: s.month, half: s.half, rows }));
    dd_.saving = false; dd_.dirty = false;
    const focus = document.activeElement && document.activeElement.dataset ? [document.activeElement.dataset.r, document.activeElement.dataset.c] : null;
    dd_.sheet = saved;
    const d = dd_.ov.drivers.find((x) => x.id === s.employee_id);
    if (d) d.filled = saved.totals.days;
    ddRender();
    if (focus && focus[0] != null) { const n = document.querySelector(`#ddBody .dd-in[data-r="${focus[0]}"][data-c="${focus[1]}"]`); if (n) n.focus(); }
  } catch (err) {
    dd_.saving = false; ddStatus();
    if (btn) btn.disabled = false;
    showErr("ddError", `บันทึกไม่สำเร็จ: ${err.message}`);
  }
}
