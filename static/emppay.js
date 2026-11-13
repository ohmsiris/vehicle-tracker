"use strict";

// เงินเดือนรายเดือน on the employee page (owner, 13 Nov 2026): HQ's payroll sheet, person by person (server: app/payroll_people.py).
// Tiles, then a stacked bar per month -- the pay in its parts above the line (ค่าแรง, ค่าคอม, ค่าซอง, เบี้ยขยัน, OT, อื่นๆ), savings paid
// back / back pay hatched on top (not pay), the deductions below the line, take-home as a dot -- like the รถสาย chart; then a table by month
// (newest first) that opens each month's parts and deductions.

const EP_PARTS = [["wage", "ค่าแรง", "var(--el-off)"], ["commission", "ค่าคอม", "var(--el-hol)"], ["bag", "ค่าซอง", "#2bb5c4"],
  ["bonus", "เบี้ยขยัน", "#e8b33a"], ["ot", "OT", "var(--el-dem)"], ["other", "อื่นๆ", "var(--el-ft)"]];
const EP_DED = "var(--el-peak)";
const EP_REFUND = "#b9b6ad";
const epMon = (p) => elMon(p);
const epB = (v) => elN(v, Math.abs(v % 1) > 0.004 ? 2 : 0);

async function renderEmployeePay(person) {
  const box = document.getElementById("empPayBox");
  if (!box) return;
  let d;
  try { d = await api(`/api/employees/${person.id}/pay`); }
  catch (err) { fill(box, el("p", { class: "muted small", text: `โหลดเงินเดือนไม่สำเร็จ: ${err.message}` })); return; }
  if (!d.months.length) { fill(box, el("p", { class: "muted small", text: "ยังไม่มีข้อมูลเงินเดือนของคนนี้ในไฟล์ของ HQ (โหลดไว้ ม.ค.–ต.ค. 2569)" })); return; }
  const t = d.tiles;
  const tile = (label, value, sub) => el("div", { class: "lc-tile" }, el("div", { class: "small muted", text: label }), el("b", { text: value }), sub ? el("div", { class: "small muted", text: sub }) : null);
  const tiles = el("div", { class: "lc-tiles" },
    tile("รับสุทธิเฉลี่ย / เดือน", `฿${epB(t.avg_net)}`, `${t.months} เดือนในปี ${t.year}`),
    tile("วันทำงานปีนี้", elN(t.days, t.days % 1 ? 1 : 0), "ตามที่ HQ จ่าย"),
    tile("รวมรับปีนี้", `฿${epB(t.pay)}`, "ไม่รวมคืนสะสม / จ่ายค้าง"));
  const used = EP_PARTS.filter(([k]) => d.months.some((m) => m.parts[k]));
  const legend = elLegend([...used.map(([, t2, c]) => [t2, c]), ...(d.months.some((m) => m.refund) ? [["คืนสะสม / จ่ายค้าง (ไม่ใช่รายได้)", EP_REFUND]] : []),
    ["รายการหัก", EP_DED], ["รับสุทธิ", "var(--text)"]]);
  fill(box, tiles, el("div", { class: "ep-chart" }, legend, epChart(d.months.slice().reverse(), used)), epTable(d));
}

function epChart(ms, used) {
  const W = 760, H = 260, P = { l: 58, r: 12, t: 12, b: 26 };
  const top = Math.max(1, ...ms.map((m) => m.pay + m.refund)), bottom = Math.max(0, ...ms.map((m) => m.deduct_total));
  const step = elNiceStep((top + bottom) / 5);
  const hi = Math.ceil(top / step) * step, lo = Math.ceil(bottom / step) * step;
  const y = (v) => P.t + (H - P.t - P.b) * (hi - v) / (hi + lo);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "el-svg", role: "img", "aria-label": "เงินเดือนรายเดือน แยกส่วน" });
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
    if (m.deduct_total > 0) g.append(sv("rect", { x: bx, y: y(0) + 1.5, width: bar, height: Math.max(0, y(-m.deduct_total) - y(0) - 1.5), rx: 3, fill: EP_DED, opacity: 0.85 }));
    const net = m.net - m.refund;
    g.append(sv("circle", { cx: bx + bar / 2, cy: y(net), r: 4.5, fill: "var(--text)", stroke: "var(--surface)", "stroke-width": 2 }));
    g.append(sv("rect", { x: P.l + w * i, y: P.t, width: w, height: H - P.t - P.b, fill: "transparent" }));
    elTip(g, () => [`${epMon(m.month)} · ${elN(m.days, m.days % 1 ? 1 : 0)} วัน`,
      ...used.filter(([k]) => m.parts[k]).map(([k, t2, c]) => [t2, `${epB(m.parts[k])} ฿`, c]),
      ["รวมรับ", `${epB(m.pay)} ฿`],
      ...(m.refund ? [["คืนสะสม / จ่ายค้าง", `${epB(m.refund)} ฿`, EP_REFUND]] : []),
      ["รายการหัก", `−${epB(m.deduct_total)} ฿`, EP_DED], ["รับสุทธิ", `${epB(net)} ฿`]]);
    svg.append(g);
    svg.append(sv("text", { x: P.l + w * i + w / 2, y: H - 8, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(w < 48 ? ELEC_TH[Number(m.month.slice(5, 7)) - 1] : epMon(m.month))));
  });
  return svg;
}

function epTable(d) {
  const body = el("tbody", {});
  d.months.forEach((m) => {
    const detail = el("tr", { class: "ep-detail", hidden: true }, el("td", { colspan: "6" },
      el("div", { class: "ep-split" },
        el("div", {}, el("div", { class: "small muted", text: "รายรับ" }), ...Object.entries(d.part_labels).filter(([k]) => m.parts[k])
          .map(([k, t]) => el("div", { class: "ep-kv" }, el("span", { text: t }), el("b", { text: epB(m.parts[k]) }))),
          m.refund ? el("div", { class: "ep-kv muted" }, el("span", { text: "คืนสะสม / จ่ายค้าง (ไม่ใช่รายได้)" }), el("b", { text: epB(m.refund) })) : null),
        el("div", {}, el("div", { class: "small muted", text: "รายการหัก" }), ...Object.entries(d.deduction_labels).filter(([k]) => m.deductions[k])
          .map(([k, t]) => el("div", { class: "ep-kv" }, el("span", { text: t }), el("b", { text: epB(m.deductions[k]) }))),
          !m.deduct_total ? el("div", { class: "muted small", text: "ไม่มี" }) : null)),
      null));                                         // the readers' notes stay in the data (gone through with the owner), not on the page
    const row = el("tr", { class: "ep-row", onclick: () => { detail.hidden = !detail.hidden; } },
      el("td", {}, el("b", { text: epMon(m.month) }), m.source === "pdf" ? el("span", { class: "small muted", text: " (PDF)" }) : null),
      el("td", { class: "r", text: elN(m.days, m.days % 1 ? 1 : 0) }),
      el("td", { class: "r", text: epB(m.pay) }),
      el("td", { class: "r", text: m.deduct_total ? `−${epB(m.deduct_total)}` : "–" }),
      el("td", { class: "r" }, el("b", { text: epB(m.net - m.refund) })),
      el("td", { class: "r small muted", text: m.refund ? `+ คืนสะสม ${epB(m.refund)}` : "" }));
    body.append(row, detail);
  });
  return el("div", { class: "lc-wrap" }, el("table", { class: "lc-table ep-table" },
    el("thead", {}, el("tr", {}, ...["เดือน", "วัน", "รวมรับ", "รวมหัก", "รับสุทธิ", ""].map((h, i) => el("th", { class: i ? "r" : "", text: h })))), body),
    el("p", { class: "small muted", text: "กดที่เดือนเพื่อดูแต่ละส่วน · ข้อมูลจากไฟล์เงินเดือนของ HQ (เดือนที่มีแต่ PDF อาจต่างจาก HQ ไม่เกินไม่กี่บาทจากการปัดเศษ)" }));
}
