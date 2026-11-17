"use strict";

// เงินเดือนรายเดือน on the employee page (owner, 13 Nov 2026): HQ's payroll sheet, person by person (server: app/payroll_people.py).
// Tiles, then a stacked bar per month -- the pay in its parts above the line (ค่าแรง, ค่าคอม, ค่าซอง, เบี้ยขยัน, OT, อื่นๆ), savings paid
// back / back pay hatched on top (not pay), the deductions below the line by type, take-home as a dot -- like the รถสาย chart; then a table
// by month (newest first, one column per deduction type) that opens each month as HQ printed it: HQ's own column labels, sales and
// commission %, the loan (ค้างเงิน -> จ่ายค้าง -> ค้างยกไป), the savings balance, HQ's notes (owner, 17 Nov 2026: "which รายการหัก were made").
// Then the savings / loan balances by month and every note HQ wrote on the row.

const EP_PARTS = [["wage", "ค่าแรง", "var(--el-off)"], ["commission", "ค่าคอม", "var(--el-hol)"], ["bag", "ค่าซอง", "#2bb5c4"],
  ["bonus", "เบี้ยขยัน", "#e8b33a"], ["ot", "OT", "var(--el-dem)"], ["other", "อื่นๆ", "var(--el-ft)"]];
// deductions: warm / wine / earthy tones, apart from the blues-greens-yellow of the pay above the line and from the status colours
const EP_DEDS = [["savings", "สะสม", "#eb6834"], ["advance", "เบิก", "#e8739f"], ["loan", "เงินยืม", "#a24bb0"], ["common", "ส่วนกลาง", "#a8743f"],
  ["card", "ทำบัตร", "#9aa63a"], ["shortage", "เงินขาด", "#8e3553"], ["other", "อื่นๆ", "#6f8296"], ["sso", "ปกส.", "#8a7f73"]];
// HQ heads both the wage and the commission plain "เงิน" (KK: "วันทำงาน/เงิน", "OT./เงิน"): those show the app's name instead
const EP_GENERIC = /^(|เงิน|วันทำงาน\/เงิน|OT\.?\/เงิน)$/;
const EP_REFUND = "#b9b6ad";
const EP_SAVE = "#eb6834", EP_LOAN = "#a24bb0";            // the balance lines: the same colours as their deductions
const epMon = (p) => elMon(p);
const epB = (v) => elN(v, Math.abs(v % 1) > 0.004 ? 2 : 0);
const epHas = (v) => v != null && !Number.isNaN(v);
const epDet = (m) => m.details || {};

async function renderEmployeePay(person) {
  const box = document.getElementById("empPayBox");
  if (!box) return;
  let d;
  try { d = await api(`/api/employees/${person.id}/pay`); }
  catch (err) { fill(box, el("p", { class: "muted small", text: `โหลดเงินเดือนไม่สำเร็จ: ${err.message}` })); return; }
  if (!d.months.length) { fill(box, el("p", { class: "muted small", text: "ยังไม่มีข้อมูลเงินเดือนของคนนี้ในไฟล์ของ HQ (โหลดไว้ ม.ค.–ต.ค. 2569)" })); return; }
  const t = d.tiles;
  const tile = (label, value, ...sub) => el("div", { class: "lc-tile" }, el("div", { class: "small muted", text: label }), el("b", { text: value }),
    ...sub.filter(Boolean).map((s) => (typeof s === "string" ? el("div", { class: "small muted", text: s }) : s)));
  const usedDed = EP_DEDS.filter(([k]) => d.months.some((m) => m.deductions[k]));
  const yearDed = usedDed.filter(([k]) => (t.deductions || {})[k]);
  const tiles = el("div", { class: "lc-tiles" },
    tile("รับสุทธิเฉลี่ย / เดือน", `฿${epB(t.avg_net)}`, `${t.months} เดือนในปี ${t.year}`),
    tile("วันทำงานปีนี้", elN(t.days, t.days % 1 ? 1 : 0), "ตามที่ HQ จ่าย"),
    tile("รวมรับปีนี้", `฿${epB(t.pay)}`, "ไม่รวมคืนสะสม / จ่ายค้าง"),
    tile("รวมหักปีนี้", `฿${epB(t.deduct_total || 0)}`, yearDed.length ? el("div", { class: "ep-tilelist" }, ...yearDed.map(([k, s, c]) =>
      el("div", { class: "ep-kv small" }, el("span", {}, el("i", { class: "ep-sw", style: `background:${c}` }), s), el("span", { text: epB(t.deductions[k]) })))) : "ไม่มี"),
    epHas(t.savings_balance) ? tile("เงินสะสมยกไป", `฿${epB(t.savings_balance)}`, `ณ ${epMon(t.savings_month)}`) : null,
    epHas(t.loan_after) ? tile("ยอดยืมคงค้าง", `฿${epB(t.loan_after)}`, t.loan_after ? `ณ ${epMon(t.loan_month)}` : `ไม่มีค้าง ณ ${epMon(t.loan_month)}`) : null);
  const used = EP_PARTS.filter(([k]) => d.months.some((m) => m.parts[k]));
  const legend = elLegend([...used.map(([, t2, c]) => [t2, c]), ...(d.months.some((m) => m.refund) ? [["คืนสะสม / จ่ายค้าง (ไม่ใช่รายได้)", EP_REFUND]] : []),
    ...usedDed.map(([k, , c]) => [`หัก${d.deduction_labels[k]}`, c]), ["รับสุทธิ", "var(--text)"]]);
  const chrono = d.months.slice().reverse();
  fill(box, tiles, el("div", { class: "ep-chart" }, legend, epChart(chrono, used, usedDed, d)), epTable(d, usedDed), epBalances(chrono), epNotes(d.months));
}

function epChart(ms, used, usedDed, d) {
  const W = 760, H = 280, P = { l: 58, r: 12, t: 12, b: 26 };
  const dedSum = (m) => Math.max(m.deduct_total, usedDed.reduce((s, [k]) => s + (m.deductions[k] > 0 ? m.deductions[k] : 0), 0));
  const top = Math.max(1, ...ms.map((m) => m.pay + m.refund)), bottom = Math.max(0, ...ms.map(dedSum));
  const step = elNiceStep((top + bottom) / 5);
  const hi = Math.ceil(top / step) * step, lo = Math.ceil(bottom / step) * step;
  const y = (v) => P.t + (H - P.t - P.b) * (hi - v) / (hi + lo);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "el-svg", role: "img", "aria-label": "เงินเดือนรายเดือน แยกส่วน และรายการหักแยกประเภท" });
  for (let v = -lo; v <= hi + step / 2; v += step) {
    svg.append(sv("line", { x1: P.l, x2: W - P.r, y1: y(v), y2: y(v), class: Math.abs(v) < 1e-6 ? "el-base" : "el-gl" }),
      sv("text", { x: P.l - 6, y: y(v) + 4, "text-anchor": "end", class: "el-tick" }, document.createTextNode(elShort(Math.abs(v)))));
  }
  svg.append(sv("defs", {}, sv("pattern", { id: "epHatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    sv("rect", { width: 6, height: 6, fill: EP_REFUND }), sv("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--surface)", "stroke-width": 2.5 }))));
  const n = ms.length, w = (W - P.l - P.r) / n, bar = Math.min(46, w * 0.58);
  ms.forEach((m, i) => {
    const bx = P.l + w * i + (w - bar) / 2;
    const g = sv("g", { class: "el-hit" });
    let acc = 0;
    const segs = [...used.filter(([k]) => m.parts[k] > 0).map(([k, , c]) => [m.parts[k], c]), ...(m.refund > 0 ? [[m.refund, "url(#epHatch)"]] : [])];
    segs.forEach(([v, c], j) => {
      const y0 = y(acc), y1 = y(acc + v), last = j === segs.length - 1;
      acc += v;
      g.append(sv("rect", { x: bx, y: y1 + (last ? 0 : 1.5), width: bar, height: Math.max(0, y0 - y1 - (last ? 0 : 1.5)), rx: last ? 4 : 0, fill: c }));
    });
    // below the line: each deduction type its own block, the 2px gap between them like the pay above; the bottom one rounded
    const dsegs = usedDed.filter(([k]) => m.deductions[k] > 0).map(([k, , c]) => [m.deductions[k], c]);
    let down = 0;
    dsegs.forEach(([v, c], j) => {
      const y0 = y(-down), y1 = y(-(down + v)), last = j === dsegs.length - 1;
      down += v;
      g.append(sv("rect", { x: bx, y: y0 + 1.5, width: bar, height: Math.max(0, y1 - y0 - 1.5), rx: last ? 3 : 0, fill: c }));
    });
    const net = m.net - m.refund;
    g.append(sv("circle", { cx: bx + bar / 2, cy: y(net), r: 4.5, fill: "var(--text)", stroke: "var(--surface)", "stroke-width": 2 }));
    g.append(sv("rect", { x: P.l + w * i, y: P.t, width: w, height: H - P.t - P.b, fill: "transparent" }));
    elTip(g, () => [`${epMon(m.month)} · ${elN(m.days, m.days % 1 ? 1 : 0)} วัน`,
      ...used.filter(([k]) => m.parts[k]).map(([k, t2, c]) => [t2, `${epB(m.parts[k])} ฿`, c]),
      ["รวมรับ", `${epB(m.pay)} ฿`],
      ...(m.refund ? [["คืนสะสม / จ่ายค้าง", `${epB(m.refund)} ฿`, EP_REFUND]] : []),
      ...usedDed.map(([k, , c]) => [`หัก${d.deduction_labels[k]}`, m.deductions[k] ? `−${epB(m.deductions[k])} ฿` : "–", c]),
      ["รวมหัก", m.deduct_total ? `−${epB(m.deduct_total)} ฿` : "–"], ["รับสุทธิ", `${epB(net)} ฿`]]);
    svg.append(g);
    svg.append(sv("text", { x: P.l + w * i + w / 2, y: H - 8, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(w < 48 ? ELEC_TH[Number(m.month.slice(5, 7)) - 1] : epMon(m.month))));
  });
  return svg;
}

// one month as HQ printed it: its own column labels (fallback: our parts / deductions), sales and %, the loan, the savings, its notes
function epDetail(d, m) {
  const x = epDet(m), lines = x.lines || [];
  const partC = Object.fromEntries(EP_PARTS.map(([k, , c]) => [k, c])), dedC = Object.fromEntries(EP_DEDS.map(([k, , c]) => [k, c]));
  const kv = (label, value, opts = {}) => el("div", { class: `ep-kv${opts.muted ? " muted" : ""}${opts.total ? " ep-total" : ""}` },
    el("span", {}, opts.c ? el("i", { class: "ep-sw", style: `background:${opts.c}` }) : null, label), el("b", { text: value }));
  const head = (t) => el("div", { class: "small muted ep-h", text: t });
  const inc = lines.filter((l) => l.side === "income"), ded = lines.filter((l) => l.side === "deduction");
  const income = inc.length
    ? inc.map((l) => kv(EP_GENERIC.test(l.label || "") ? (d.part_labels[l.key] || l.key) : l.label, epB(l.amount), { c: partC[l.key] }))
    : [...Object.entries(d.part_labels).filter(([k]) => m.parts[k]).map(([k, t]) => kv(t, epB(m.parts[k]), { c: partC[k] })),
      m.refund ? kv("คืนสะสม / จ่ายค้าง (ไม่ใช่รายได้)", epB(m.refund), { muted: true, c: EP_REFUND }) : null];
  const incTotal = inc.length ? inc.reduce((s, l) => s + l.amount, 0) : m.pay + m.refund;
  const extra = [
    epHas(x.sales) ? kv("ยอดขาย", epB(x.sales), { muted: true }) : null,
    epHas(x.commission_pct) ? kv("ค่าคอม % ของยอดขาย", `${elN(x.commission_pct, 2)}%`, { muted: true }) : null,
    epHas(x.rate) ? kv("ค่าแรงต่อวัน", epB(x.rate), { muted: true }) : null];
  const deductions = ded.length
    ? ded.map((l) => kv(l.label || d.deduction_labels[l.key] || l.key, epB(l.amount), { c: dedC[l.key] }))
    : Object.entries(d.deduction_labels).filter(([k]) => m.deductions[k]).map(([k, t]) => kv(t, epB(m.deductions[k]), { c: dedC[k] }));
  const step = (label, v) => el("div", {}, el("div", { class: "small muted", text: label }), el("b", { text: epHas(v) ? epB(v) : "–" }));
  const loan = [x.loan_before, x.loan_paid, x.loan_after].some(epHas)
    ? el("div", { class: "ep-box" }, head("เงินยืม"), el("div", { class: "ep-flow" },
      step("ค้างเงิน", x.loan_before), el("span", { class: "muted", text: "→" }), step("จ่ายค้าง", x.loan_paid),
      el("span", { class: "muted", text: "→" }), step("ค้างยกไป", x.loan_after)))
    : null;
  const savings = epHas(x.savings_balance) ? el("div", { class: "ep-box" }, head("เงินสะสม"), kv("สะสมยกไป", epB(x.savings_balance))) : null;
  const notes = (x.notes || []).length ? el("div", { class: "ep-notes" }, head("บันทึกของ HQ"),
    ...x.notes.map((nt) => el("div", { class: "small" }, nt.label ? el("span", { class: "muted", text: `${nt.label}: ` }) : null, nt.text))) : null;
  const pos = x.position || m.dept;
  const meta = [pos ? `ตำแหน่ง: ${pos}` : null, m.source === "pdf" ? "จาก PDF ของ HQ" : m.source ? "จากไฟล์ Excel ของ HQ" : null].filter(Boolean);
  return el("div", { class: "ep-panel" },
    meta.length ? el("div", { class: "small muted ep-meta", text: meta.join(" · ") }) : null,
    el("div", { class: "ep-split" },
      el("div", {}, head("รายรับ"), ...income, kv("รวมรับ", epB(incTotal), { total: true }), ...extra),
      el("div", {}, head("รายการหัก"), ...(deductions.length ? deductions : [el("div", { class: "muted small", text: "ไม่มี" })]),
        m.deduct_total ? kv("รวมหัก", epB(m.deduct_total), { total: true }) : null),
      loan || savings ? el("div", {}, loan, savings) : null),
    notes);
}

function epTable(d, usedDed) {
  const hasSave = d.months.some((m) => epHas(epDet(m).savings_balance)), hasLoan = d.months.some((m) => epHas(epDet(m).loan_after));
  const cols = 6 + usedDed.length + (hasSave ? 1 : 0) + (hasLoan ? 1 : 0);
  const body = el("tbody", {});
  d.months.forEach((m, i) => {
    const x = epDet(m);
    const cell = el("td", { colspan: String(cols) });
    const detail = el("tr", { class: "ep-detail", hidden: true }, cell);
    const row = el("tr", { class: `ep-row${i % 2 ? "" : " ep-alt"}`, onclick: () => {
      if (!cell.firstChild) cell.append(epDetail(d, m));
      detail.hidden = !detail.hidden; row.classList.toggle("open", !detail.hidden);
    } },
      el("td", {}, el("b", { text: epMon(m.month) }), m.source === "pdf" ? el("span", { class: "small muted", text: " (PDF)" }) : null),
      el("td", { class: "r", text: elN(m.days, m.days % 1 ? 1 : 0) }),
      el("td", { class: "r", text: epB(m.pay) }),
      ...usedDed.map(([k]) => el("td", { class: `r${m.deductions[k] ? "" : " muted"}`, text: m.deductions[k] ? epB(m.deductions[k]) : "–" })),
      el("td", { class: "r", text: m.deduct_total ? `−${epB(m.deduct_total)}` : "–" }),
      el("td", { class: "r" }, el("b", { text: epB(m.net - m.refund) }), m.refund ? el("div", { class: "small muted", text: `+ คืนสะสม ${epB(m.refund)}` }) : null),
      hasSave ? el("td", { class: "r", text: epHas(x.savings_balance) ? epB(x.savings_balance) : "–" }) : null,
      hasLoan ? el("td", { class: "r", text: epHas(x.loan_after) ? epB(x.loan_after) : "–" }) : null,
      el("td", { class: "r ep-caret muted", text: "›" }));
    body.append(row, detail);
  });
  const th = (t, title) => el("th", { class: "r", text: t, title: title || null });
  return el("div", { class: "ep-tablebox" }, el("div", { class: "lc-wrap" }, el("table", { class: "lc-table ep-table" },
    el("thead", {}, el("tr", {}, el("th", { text: "เดือน" }), th("วัน"), th("รวมรับ"),
      ...usedDed.map(([k, s]) => th(s, d.deduction_labels[k])), th("รวมหัก"), th("รับสุทธิ"),
      hasSave ? th("สะสมยกไป") : null, hasLoan ? th("ยืมคงค้าง") : null, el("th", {}))), body)),
    el("p", { class: "small muted", text: "กดที่เดือนเพื่อดูรายการตามใบของ HQ · ข้อมูลจากไฟล์เงินเดือนของ HQ (เดือนที่มีแต่ PDF อาจต่างจาก HQ ไม่เกินไม่กี่บาทจากการปัดเศษ)" }));
}

// สะสมยกไป and ยืมคงค้าง by month: two lines on one baht axis, a gap where a month does not carry the figure
function epBalances(ms) {
  const series = [["savings_balance", "สะสมยกไป", EP_SAVE], ["loan_after", "ยืมคงค้าง", EP_LOAN]]
    .filter(([k]) => ms.some((m) => epHas(epDet(m)[k])));
  if (!series.length) return null;
  const W = 760, H = 190, P = { l: 58, r: 12, t: 12, b: 26 };
  const max = Math.max(1, ...ms.flatMap((m) => series.map(([k]) => epDet(m)[k] || 0)));
  const step = elNiceStep(max / 4), hi = Math.ceil(max / step) * step;
  const y = (v) => P.t + (H - P.t - P.b) * (1 - v / hi);
  const n = ms.length, w = (W - P.l - P.r) / n, cx = (i) => P.l + w * i + w / 2;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "el-svg", role: "img", "aria-label": "เงินสะสมยกไปและยอดยืมคงค้าง รายเดือน" });
  for (let v = 0; v <= hi + step / 2; v += step) {
    svg.append(sv("line", { x1: P.l, x2: W - P.r, y1: y(v), y2: y(v), class: v ? "el-gl" : "el-base" }),
      sv("text", { x: P.l - 6, y: y(v) + 4, "text-anchor": "end", class: "el-tick" }, document.createTextNode(elShort(v))));
  }
  series.forEach(([k, , c]) => {
    let path = "", pen = false;
    ms.forEach((m, i) => {
      const v = epDet(m)[k];
      if (!epHas(v)) { pen = false; return; }
      path += `${pen ? "L" : "M"}${cx(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
    });
    svg.append(sv("path", { d: path, fill: "none", stroke: c, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    ms.forEach((m, i) => { const v = epDet(m)[k]; if (epHas(v)) svg.append(sv("circle", { cx: cx(i), cy: y(v), r: 3.5, fill: c, stroke: "var(--surface)", "stroke-width": 2 })); });
  });
  ms.forEach((m, i) => {
    const g = sv("g", { class: "el-hit" }, sv("rect", { x: P.l + w * i, y: P.t, width: w, height: H - P.t - P.b, fill: "transparent" }));
    elTip(g, () => [epMon(m.month), ...series.map(([k, t, c]) => [t, epHas(epDet(m)[k]) ? `${epB(epDet(m)[k])} ฿` : "–", c])]);
    svg.append(g, sv("text", { x: cx(i), y: H - 8, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(w < 48 ? ELEC_TH[Number(m.month.slice(5, 7)) - 1] : epMon(m.month))));
  });
  return el("div", { class: "ep-chart ep-bal" }, el("div", { class: "ep-sub", text: "เงินสะสม และเงินยืมคงค้าง" }), elLegend(series.map(([, t, c]) => [t, c])), svg);
}

// "ม.ค.–ต.ค. 69", "ม.ค. 69, มี.ค.–พ.ค. 69": runs of months in a row joined with a dash
function epRanges(list) {
  const idx = (p) => Number(p.slice(0, 4)) * 12 + Number(p.slice(5, 7)) - 1;
  const runs = [];
  [...new Set(list)].sort().forEach((p) => { const r = runs[runs.length - 1]; if (r && idx(p) === idx(r[1]) + 1) r[1] = p; else runs.push([p, p]); });
  return runs.map(([a, b]) => (a === b ? epMon(a) : a.slice(0, 4) === b.slice(0, 4)
    ? `${ELEC_TH[Number(a.slice(5, 7)) - 1]}–${epMon(b)}` : `${epMon(a)}–${epMon(b)}`)).join(", ");
}

// every note HQ wrote on this person's row, once each (by column + text), with the months it was there; the newest first
function epNotes(months) {
  const seen = new Map();
  months.forEach((m) => (epDet(m).notes || []).forEach((nt) => {
    const key = `${nt.label}\u0000${nt.text}`;
    if (!seen.has(key)) seen.set(key, { ...nt, months: [] });
    seen.get(key).months.push(m.month);
  }));
  if (!seen.size) return null;
  const last = (nt) => nt.months.slice().sort().pop();
  const list = [...seen.values()].sort((a, b) => last(b).localeCompare(last(a)));
  return el("div", { class: "ep-hqnotes" }, el("div", { class: "ep-sub", text: "บันทึกของ HQ" }),
    ...list.map((nt) => el("div", { class: "ep-note" },
      el("div", {}, nt.label ? el("span", { class: "muted", text: `${nt.label}: ` }) : null, nt.text),
      el("div", { class: "small muted ep-when", text: epRanges(nt.months) }))));
}
