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
    el("span", { class: "muted small", text: `รอบเดือน ${ddMonthTH(ov.month)}` }),
    el("span", { class: "dd-export" },
      el("button", { class: "btn small", type: "button", text: "ส่งออก Excel งวดนี้", onclick: (e) => ddExport(ov.half, e.target) }),
      el("button", { class: "btn small", type: "button", text: "ทั้งเดือน", onclick: (e) => ddExport(0, e.target) })));
  const chips = el("div", { class: "dd-chips" }, ...ov.drivers.map((d) => el("button", {
    type: "button", class: `dd-chip${d.id === dd_.emp ? " on" : ""}`, onclick: () => ddGo(d.id, ov.month, ov.half) },
    d.truck ? el("span", { class: "truckno", text: `#${d.truck}` }) : null,
    el("span", { text: d.nickname || d.name.split(" ")[0] }),
    el("span", { class: `dd-count${d.filled ? (d.filled >= ov.days ? " full" : " part") : ""}`, text: `${d.filled}/${ov.days}` }))));
  if (!ov.drivers.length) chips.append(el("p", { class: "muted small", text: "ไม่พบพนักงานตำแหน่ง “พนักงานขับรถสาย” ที่ยังทำงานอยู่ (สาขาสระบุรี)" }));
  fill($("ddBody"), el("section", { class: "card dd-top" }, period, chips),
    dd_.sheet ? ddSheet() : el("p", { class: "muted dd-pick", text: "เลือกคนขับด้านบนเพื่อกรอกยอดของงวดนี้" }), ddSummary());
  if (dd_.sheet) { const first = document.querySelector("#ddBody .dd-in"); if (first) first.focus(); }
}

function ddSheet() {
  const s = dd_.sheet, prods = s.products;
  // ทวาย สำเริง สะอาด (owner, 17 Nov 2026): ยอดขายซอง + ยอดขายหลอด in baht; เงินที่ได้ is their sum -- worked out, not typed, Tab skips it
  const split = !!s.split;
  const cols = [...prods.map((p) => ({ key: p.key, label: p.label, whole: true })),
    ...(split ? (s.parts || []).map((p) => ({ key: `p_${p.key}`, part: p.key, label: p.label, whole: false })) : []),
    { key: "money", label: split ? "เงินที่ได้ (บาท) — รวมให้" : "เงินที่ได้ (บาท)", whole: false, auto: split }];
  const valOf = (r, c) => (c.key === "money" ? r.money : c.part ? (r.parts || {})[c.part] : (r.qty || {})[c.key]);
  const body = el("tbody", {});
  s.rows.forEach((r, ri) => {
    const d = new Date(`${r.day}T00:00:00`);
    const tr = el("tr", { class: `${d.getDay() === 0 ? "dd-sun" : ""}${r.spare ? " dd-spare" : ""}` }, el("td", { class: "dd-date" }, el("span", { class: "muted small", text: DD_DOW[d.getDay()] }), ` ${r.day.slice(8, 10)}/${r.day.slice(5, 7)}`));
    cols.forEach((c, ci) => {
      const v = valOf(r, c);
      const inp = el("input", { type: "text", inputmode: c.whole ? "numeric" : "decimal", class: `dd-in${c.key === "money" || c.part ? " dd-money" : ""}${c.auto ? " dd-auto" : ""}`,
        value: v == null ? "" : String(v), "aria-label": `${ddDMY(r.day)} ${c.label}`, autocomplete: "off",
        ...(c.auto ? { readOnly: true, tabindex: "-1", title: "รวมยอดขายซอง + หลอด ให้เอง — แก้ไม่ได้" } : {}) });
      inp.dataset.r = String(ri); inp.dataset.c = String(ci);
      inp.addEventListener("input", () => {
        if (c.auto) return;
        if (c.key === "money") r.money = inp.value;
        else if (c.part) {
          r.parts = { ...(r.parts || {}) }; r.parts[c.part] = inp.value;
          const got = Object.values(r.parts).map(ddNum).filter((x) => x !== null && Number.isFinite(x));
          r.money = got.length ? String(Math.round(got.reduce((a, x) => a + x, 0) * 100) / 100) : "";
          const m = inp.closest("tr").querySelector("input.dd-auto"); if (m) m.value = r.money;
        } else { r.qty = { ...(r.qty || {}) }; r.qty[c.key] = inp.value; }
        inp.classList.toggle("flagged", ddBad(inp.value, c.whole));
        dd_.dirty = true; ddTotals(cols); ddStatus();
      });
      inp.addEventListener("keydown", ddKey);
      tr.append(el("td", { class: "r" }, inp));
    });
    // สำรองขับ (owner, 17 Nov 2026): one dropdown -- "—" = his own day; a name (or ไม่ระบุชื่อ) = the spare drove: the day counts for nobody,
    // the spare's days are counted. Mouse only: Tab never stops here (tabindex -1).
    const by = el("select", { class: `dd-by${r.spare ? " on" : ""}`, tabindex: "-1", "aria-label": `${ddDMY(r.day)} คนขับสำรอง`, title: "คนขับสำรองขับวันนี้ — ไม่นับยอด",
      onchange: () => {
        r.spare = by.value !== ""; r.spare_by = by.value && by.value !== "?" ? by.value : null;
        by.classList.toggle("on", r.spare); tr.classList.toggle("dd-spare", r.spare); dd_.dirty = true; ddTotals(cols); ddStatus();
      } },
      el("option", { value: "", text: "—" }),
      el("option", { value: "?", text: "สำรอง (ไม่ระบุชื่อ)", selected: r.spare && !r.spare_by }),
      ...(dd_.ov.spares || []).filter((p) => p.id !== s.employee_id).map((p) => el("option", { value: p.id, selected: r.spare && p.id === r.spare_by,
        text: `สำรอง: ${p.nickname || p.name.split(" ")[0]}` })));
    const who = by;
    tr.firstChild.append(who);                                  // beside the date: always in sight, never in the Tab path
    body.append(tr);
  });
  const foot = el("tfoot", {}, el("tr", {}, el("td", { text: "รวม (ขับเอง)" }), ...cols.map((c) => el("td", { class: "r", id: `ddT_${c.key}` }))),
    el("tr", { class: "dd-sparerow", id: "ddSpareRow" }, el("td", { text: "สำรองขับ (ไม่นับ)" }), ...cols.map((c) => el("td", { class: "r", id: `ddS_${c.key}` }))));
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
  const own = rows.filter((r) => !r.spare), spare = rows.filter((r) => r.spare);
  cols.forEach((c) => {
    const sumOf = (list) => list.reduce((a, r) => a + (Number(ddNum(c.key === "money" ? r.money : c.part ? (r.parts || {})[c.part] : (r.qty || {})[c.key])) || 0), 0);
    const a = sumOf(own), b = sumOf(spare);
    const cell = $(`ddT_${c.key}`), cell2 = $(`ddS_${c.key}`);
    const dec = (x) => ((c.key === "money" || c.part) && x % 1 ? 2 : 0);
    if (cell) cell.textContent = elN(a, dec(a));
    if (cell2) cell2.textContent = spare.length ? elN(b, dec(b)) : "";
  });
  const sr = $("ddSpareRow"); if (sr) sr.hidden = !spare.length;
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
  const rows = s.rows.map((r) => ({ day: r.day, qty: r.qty || {}, money: r.money, spare: !!r.spare, spare_by: r.spare ? r.spare_by || null : null,
    ...(s.split ? { parts: r.parts || {} } : {}) }));
  dd_.saving = true; ddStatus();
  const btn = $("ddSave"); if (btn) btn.disabled = true;
  try {
    const saved = await api(`/api/driver-days/${encodeURIComponent(s.employee_id)}`, jsonOpts("PUT", { month: s.month, half: s.half, rows }));
    dd_.saving = false; dd_.dirty = false;
    const focus = document.activeElement && document.activeElement.dataset ? [document.activeElement.dataset.r, document.activeElement.dataset.c] : null;
    dd_.sheet = saved;
    try { dd_.ov = await api(`/api/driver-days?month=${dd_.month}&half=${dd_.half}`); } catch (e) { /* the summary refreshes next time */ }
    ddRender();
    if (focus && focus[0] != null) { const n = document.querySelector(`#ddBody .dd-in[data-r="${focus[0]}"][data-c="${focus[1]}"]`); if (n) n.focus(); }
  } catch (err) {
    dd_.saving = false; ddStatus();
    if (btn) btn.disabled = false;
    showErr("ddError", `บันทึกไม่สำเร็จ: ${err.message}`);
  }
}


// สรุปงวด: every driver -- the days they drove, days off (the spare's days included), the spare's days, what they sold, the money
function ddSummary() {
  const ov = dd_.ov, list = ov.summary || [];
  if (!list.length) return null;
  const prods = ov.products;
  const sum = (f) => list.reduce((a, x) => a + f(x), 0);
  const rows = list.map((x) => el("tr", { class: x.id === dd_.emp ? "on" : "", onclick: () => { if (x.kind !== "หน้าโรง") ddGo(x.id, ov.month, ov.half); } },
    el("td", {}, x.truck ? el("span", { class: "truckno", text: `#${x.truck}` }) : el("span", { class: "muted small", text: x.kind === "หน้าโรง" ? "หน้าโรง" : "" }), ` ${x.nickname || x.name.split(" ")[0]}`),
    el("td", { class: "r", text: String(x.days_worked) }),
    el("td", { class: "r", title: x.off_dates.map(ddDMY).join(", "), text: String(x.days_off) }),
    el("td", { class: "r", title: x.spare_dates.map(ddDMY).join(", "), text: x.spare_days ? String(x.spare_days) : "–" }),
    el("td", { class: "r", title: (x.as_spare_dates || []).map(ddDMY).join(", "), text: x.as_spare_days ? String(x.as_spare_days) : "–" }),
    ...prods.map((p) => el("td", { class: "r", text: x.qty[p.key] ? elN(x.qty[p.key]) : "–" })),
    el("td", { class: "r" }, el("b", { text: elN(x.money, x.money % 1 ? 2 : 0) }))));
  return el("section", { class: "card dd-sum" },
    el("h3", { text: `สรุปงวด ${ddDMY(ov.start)} – ${ddDMY(ov.end)}` }),
    el("div", { class: "lc-wrap" }, el("table", { class: "lc-table dd-sumtable" },
      el("thead", {}, el("tr", {}, ...["คนขับ", "ขับ (วัน)", "หยุด", "สำรองขับรถคันนี้", "ไปเป็นสำรอง", ...prods.map((p) => p.label), "เงินที่ได้"].map((h, i) => el("th", { class: i ? "r" : "", text: h })))),
      el("tbody", {}, ...rows),
      el("tfoot", {}, el("tr", {}, el("td", { text: "รวม" }), el("td", { class: "r", text: String(sum((x) => x.days_worked)) }), el("td", {}), el("td", { class: "r", text: String(sum((x) => x.spare_days)) }), el("td", { class: "r", text: String(sum((x) => x.as_spare_days || 0)) }),
        ...prods.map((p) => el("td", { class: "r", text: elN(sum((x) => x.qty[p.key] || 0)) })), el("td", { class: "r" }, el("b", { text: elN(sum((x) => x.money), 2) })))))),
    el("p", { class: "small muted", text: "วันที่ติ๊ก สำรองขับ ไม่นับยอดให้ใคร (คนขับสำรองไม่ได้ค่าคอม) และนับเป็นวันหยุดของคนขับหลัก · ชี้ที่ตัวเลขวันหยุด / สำรองขับ เพื่อดูวันที่" }));
}

async function ddExport(half, btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`/api/driver-days/export.xlsx?month=${dd_.month}&half=${half}`, { headers: { "X-App-Token": state.token } });
    if (!res.ok) { let msg = `HTTP ${res.status}`; try { msg = (await res.json()).detail || msg; } catch (e) { /* not json */ } throw new Error(msg); }
    const u = URL.createObjectURL(await res.blob());
    const a = el("a", { href: u, download: `ยอดขายรถสาย ${dd_.month} ${half ? `งวด ${half}` : "ทั้งเดือน"}.xlsx` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 5000);
  } catch (err) { showErr("ddError", `ส่งออกไม่สำเร็จ: ${err.message}`); }
  finally { if (btn) btn.disabled = false; }
}
