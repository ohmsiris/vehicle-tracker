"use strict";

// บัญชี › ค่าไฟฟ้า, under the bill charts: "มิเตอร์ราย 15 นาที" -- PEA's AMR readings (app/amr.py), one branch at a time.
//   * a month picker; tiles for that month: daytime load on working days, peak share, what the peak-rate units cost above the
//     off-peak rate, early starts / late stops on working days, the demand spike
//   * an average working day, 15 minutes at a time, the month against a month to compare (the peak-price hours shaded)
//   * daytime load per month, stacked by meter · what the peak-rate units cost above off-peak, per month
//   * one day, every meter -- to put next to the crew's LINE start / stop messages
// Color keeps its job from the bill charts: orange = the peak-price hours. The meters are told apart by a slate pair, not by a tariff
// color. New months come in by attaching PEA's AMR .xls at บัญชี › ลงข้อมูล / แนบไฟล์ (row ค่าไฟฟ้า).

const am_ = { data: null, branch: "", month: "", cmp: "", day: "" };
const AM_W = 760, AM_H = 210, AM_P = { l: 52, r: 12, t: 12, b: 26 };
const amHH = (i) => `${String(Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}`;
const AM_METER = ["var(--el-m1)", "var(--el-m2)", "var(--el-m3)"];

async function loadAmr() {
  const box = $("elAmr");
  if (!box) return;
  const branch = $("elBranch").value;
  if (!branch) { fill(box, el("div", { class: "card" }, el("p", { class: "muted", text: "มิเตอร์ราย 15 นาที: เลือกสาขาเพื่อดู" }))); return; }
  fill(box, el("div", { class: "card" }, el("p", { class: "muted", text: "กำลังโหลดมิเตอร์ราย 15 นาที…" })));
  try { am_.data = await api(`/api/electricity/amr?branch=${encodeURIComponent(branch)}`); }
  catch (err) { fill(box, el("div", { class: "card" }, el("p", { class: "error", text: `โหลดข้อมูลมิเตอร์ไม่สำเร็จ: ${err.message}` }))); return; }
  const ms = am_.data.months || [];
  if (am_.branch !== branch) {                       // a new branch: start from its latest full month, compared with its first
    am_.branch = branch;
    const full = ms.filter((m) => (am_.data.by_month[m].work_days || 0) >= 15);
    am_.month = full[full.length - 1] || ms[ms.length - 1] || "";
    am_.cmp = "";                                  // no comparison unless the owner picks a month
    am_.day = "";
  }
  renderAmr();
}

function renderAmr() {
  const d = am_.data, box = $("elAmr");
  if (!d.months.length) {
    fill(box, el("div", { class: "card" }, el("h3", { text: "มิเตอร์ราย 15 นาที (AMR)" }),
      el("p", { class: "muted", text: "ยังไม่มีข้อมูล — โหลดรายงาน kWh รายเดือน (.xls) จากระบบ AMR ของ PEA แล้วแนบที่ " }, el("a", { href: "#accounts-in", text: "บัญชี › ลงข้อมูล / แนบไฟล์" }), " แถวค่าไฟฟ้า")));
    return;
  }
  const opt = (m) => el("option", { value: m, text: elMon(m) + (d.by_month[m].work_days < 15 ? " (ยังไม่ครบเดือน)" : "") });
  const pick = el("select", { id: "amMonth", "aria-label": "เดือน", onchange: (e) => { am_.month = e.target.value; renderAmr(); } }, ...d.months.map(opt));
  const cmp = el("select", { id: "amCmp", "aria-label": "เทียบกับเดือน", onchange: (e) => { am_.cmp = e.target.value; renderAmr(); } },
    el("option", { value: "", text: "— ไม่เทียบ —" }), ...d.months.map(opt));
  pick.value = am_.month; cmp.value = am_.cmp;
  const meters = d.meters.map((m, i) => el("span", {}, el("i", { style: `background:${AM_METER[i % 3]}` }),
    `มิเตอร์ ${m.name}${m.machines ? ` · ${m.machines}` : ""}`));
  fill(box, el("div", { class: "card el-panel" },
    el("div", { class: "el-panel-head" }, el("h3", { text: "มิเตอร์ราย 15 นาที (AMR)" }),
      el("span", { class: "muted small", text: `${am_.branch} · ${elMon(d.months[0])}–${elMon(d.months[d.months.length - 1])} · มิเตอร์บอกได้แค่ยอดรวมของทุกเครื่องที่ต่ออยู่ เครื่องไหนเดินเมื่อไรดูจากไลน์ดับ/เดินเครื่อง` })),
    el("div", { class: "el-legend" }, ...meters),
    el("div", { class: "am-bar" }, el("label", { class: "small muted", for: "amMonth", text: "เดือน" }), pick,
      el("label", { class: "small muted", for: "amCmp", text: "เทียบกับเดือน (ถ้าต้องการ)" }), cmp),
    amKpis(), amShape(), el("div", { class: "el-grid" }, amDayLevel(), amPremium()), amList(), amDayBox()));
  amLoadDay();
}

// ---- tiles for the picked month
function amKpis() {
  const d = am_.data, m = d.by_month[am_.month], c = am_.cmp && am_.cmp !== am_.month ? d.by_month[am_.cmp] : null;
  const spike = (m.spikes || []).reduce((a, s) => a + s.baht, 0);
  const tile = (label, value, sub, tone = "") => el("div", { class: `kpi el-kpi ${tone}` }, el("div", { class: "kpi-label", text: label }),
    el("div", { class: "kpi-value", text: value }), sub ? el("div", { class: "el-delta muted", text: sub }) : null);
  const shift = m.early.baht + m.late.baht;
  const head = el("p", { class: "am-kpi-head" }, el("strong", { text: `ตัวเลขของเดือน ${elMon(am_.month)}` }),
    el("span", { class: "muted small", text: ` · วันทำงาน ${m.work_days} วัน${m.work_days < 15 ? " (ยังไม่ครบเดือน)" : ""}${c ? ` · ตัวเลขเล็กใต้แต่ละช่อง = ${elMon(am_.cmp)}` : ""}` }));
  return el("div", {}, head, el("div", { class: "acx-kpis" },
    tile("เดินช่วงกลางวัน (วันทำงาน 12–17 น.)", `${elN(m.day_kw)} kW`, c ? `เทียบ ${elMon(am_.cmp)}: ${elN(c.day_kw)} kW` : "เครื่องที่ยังเดินช่วงราคาแพง"),
    tile("สัดส่วนช่วง Peak", `${elN(m.peak_share * 100, 1)}%`, c ? `เทียบ ${elMon(am_.cmp)}: ${elN(c.peak_share * 100, 1)}%` : `${elN(m.kwh.A)} kWh ช่วง Peak`),
    tile("จ่ายเพิ่มเพราะใช้ไฟช่วง Peak", `${elN(m.peak_premium)} ฿`, `เทียบราคา Off-peak (+${elN(d.rates.peak - d.rates.off, 2)} ฿/หน่วย) ก่อน VAT`),
    tile("เปิดก่อน 22:00 / ปิดหลัง 09:00", `${m.early.days + m.late.days} วัน`, shift ? `${elN(shift)} ฿` : "ไม่มี — ตรงเวลา", shift > 2000 ? "warn-tile" : ""),
    tile("Demand: 15 นาทีที่สูงผิดปกติ", spike ? `${elN(spike)} ฿` : "ไม่มี", spike ? `${m.spikes.length} มิเตอร์ · ดูรายการด้านล่าง` : "ค่า demand ตามวันปกติ", spike ? "warn-tile" : "")));
}

// ---- shared frame for the wide charts: one y scale, round steps, the peak-price hours shaded
function amFrame(max, fmt, band) {
  const svg = sv("svg", { viewBox: `0 0 ${AM_W} ${AM_H}`, class: "el-svg", role: "img" });
  const step = elNiceStep((max || 1) / 4), nice = Math.ceil((max || 1) / step) * step;
  const y = (v) => AM_P.t + (AM_H - AM_P.t - AM_P.b) * (1 - v / nice);
  const x = (i) => AM_P.l + (AM_W - AM_P.l - AM_P.r) * i / 96;
  if (band) band.forEach(([a, b]) => svg.append(sv("rect", { x: x(a), y: AM_P.t, width: x(b) - x(a), height: AM_H - AM_P.t - AM_P.b, class: "am-band" })));
  for (let v = 0, i = 0; v <= nice + step / 2; v += step, i++) {
    svg.append(sv("line", { x1: AM_P.l, x2: AM_W - AM_P.r, y1: y(v), y2: y(v), class: i ? "el-gl" : "el-base" }),
      sv("text", { x: AM_P.l - 6, y: y(v) + 4, "text-anchor": "end", class: "el-tick" }, document.createTextNode(fmt(v))));
  }
  for (let h = 0; h <= 24; h += 3) svg.append(sv("text", { x: x(h * 4), y: AM_H - 7, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(`${String(h).padStart(2, "0")}:00`)));
  return { svg, x, y };
}
function amPeakBand(tou) {                                  // contiguous runs of "A" -> [[from, to]]
  const out = []; let s = -1;
  for (let i = 0; i <= 96; i++) { const a = tou && tou[i] === "A"; if (a && s < 0) s = i; if (!a && s >= 0) { out.push([s, i]); s = -1; } }
  return out;
}
function amPath(vals, x, y) {
  let d = "", pen = false;
  vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i + 0.5).toFixed(1)},${y(v).toFixed(1)}`; pen = true; });
  return d;
}
function amHover(svg, x, rows) {                           // one hit column per 15 minutes, the shared tooltip
  for (let i = 0; i < 96; i++) {
    const g = sv("g", { class: "el-hit" });
    g.append(sv("rect", { x: x(i), y: AM_P.t, width: x(i + 1) - x(i), height: AM_H - AM_P.t - AM_P.b, fill: "transparent" }));
    elTip(g, () => rows(i));
    svg.append(g);
  }
}

// ---- an average working day
function amShape() {
  const d = am_.data, m = d.by_month[am_.month], c = am_.cmp && am_.cmp !== am_.month ? d.by_month[am_.cmp] : null;
  const max = Math.max(...m.work.filter((v) => v != null), ...(c ? c.work.filter((v) => v != null) : [0]));
  const { svg, x, y } = amFrame(max * 1.08, (v) => elN(v), [[36, 88]]);
  if (c) svg.append(sv("path", { d: amPath(c.work, x, y), class: "am-line cmp" }));
  svg.append(sv("path", { d: amPath(m.work, x, y), class: "am-line" }));
  amHover(svg, x, (i) => [`${amHH(i)}–${amHH(i + 1)} · วันทำงานเฉลี่ย`, [elMon(am_.month), `${elN(m.work[i])} kW`, "var(--el-off)"],
    ...(c ? [[elMon(am_.cmp), `${elN(c.work[i])} kW`, "var(--muted)"]] : []), ["วันหยุด " + elMon(am_.month), `${elN(m.off[i])} kW`]]);
  return elChart("วันทำงานเฉลี่ย ทุก 15 นาที (kW)", "แถบส้ม = Peak จ–ศ 09:00–22:00 (แพง) · เครื่องที่เปิดก่อน 22:00 หรือปิดหลัง 09:00 จะเห็นเป็นขั้นในแถบส้ม",
    svg, elLegend([[elMon(am_.month), "var(--el-off)"], ...(c ? [[elMon(am_.cmp), "var(--muted)"]] : []), ["ช่วง Peak", "var(--el-peak)"]]));
}

// ---- daytime load per month, stacked by meter
function amDayLevel() {
  const d = am_.data, ms = d.months.map((m) => ({ bill_period: m, ...d.by_month[m] }));
  const keys = d.meters.map((mt, i) => [mt.ca_no, `มิเตอร์ ${mt.name}`, AM_METER[i % 3]]);
  const rows = ms.map((x) => Object.fromEntries([["bill_period", x.bill_period], ...keys.map(([ca]) => [ca, (x.meters[ca] || {}).day_kw || 0])]));
  return elChart("เดินช่วงกลางวัน วันทำงาน (kW)", "ค่ากลางของ 12:00–17:00 — ส่วนที่ยังเดินช่วงราคาแพง แยกมิเตอร์",
    elStack(rows, keys, (v) => `${elN(v)} kW`), elLegend(keys.map(([, l, c]) => [l, c])));
}

// ---- what the peak-rate units cost above the off-peak rate, per month
function amPremium() {
  const d = am_.data;
  const rows = d.months.map((m) => ({ bill_period: m, prem: d.by_month[m].peak_premium, shift: d.by_month[m].early.baht + d.by_month[m].late.baht }));
  return elChart("จ่ายเพิ่มเพราะใช้ไฟช่วง Peak (บาท)", "หน่วยช่วง Peak × ส่วนต่างราคา Peak–Off-peak · ส่วนใหญ่มาจากเครื่องที่เดินกลางวัน ไม่ใช่เปิดก่อนเวลา",
    elStack(rows, [["prem", "ส่วนต่างค่าไฟ Peak", "var(--el-peak)"]], (v) => `${elN(v)} ฿`));
}

// ---- the month's early starts / late stops and demand spikes
function amList() {
  const d = am_.data, m = d.by_month[am_.month];
  const ev = d.events.filter((e) => e.day.startsWith(am_.month));
  const name = (ca) => { const mt = d.meters.find((x) => x.ca_no === ca); return mt ? `มิเตอร์ ${mt.name}` : ca; };
  const items = [
    ...ev.map((e) => el("li", {}, el("a", { href: "#", onclick: (x) => { x.preventDefault(); am_.day = e.day; amLoadDay(true); }, text: `${e.day.slice(8)}/${e.day.slice(5, 7)}` }),
      ` · ${e.kind === "early" ? `เปิดเครื่องก่อน 22:00 ตั้งแต่ ${e.at}` : `ปิดเครื่องหลัง 09:00 ถึง ${e.at}`} · ${elN(e.kwh)} kWh ≈ ${elN(e.baht)} ฿`)),
    ...m.spikes.map((s) => el("li", {}, el("a", { href: "#", onclick: (x) => { x.preventDefault(); am_.day = s.at.slice(0, 10); amLoadDay(true); }, text: `${s.at.slice(8, 10)}/${s.at.slice(5, 7)} ${s.at.slice(11)}` }),
      ` · ${name(s.ca_no)} ขึ้นไป ${elN(s.kw, 1)} kW (วันปกติสูงสุด ~${elN(s.normal_kw)} kW) → ค่า demand ทั้งเดือนเพิ่ม ≈ ${elN(s.baht)} ฿`)),
  ];
  return el("div", { class: "el-chart" }, el("div", { class: "el-chart-title", text: `รายการ ${elMon(am_.month)}` }),
    items.length ? el("ul", { class: "am-list" }, ...items) : el("p", { class: "muted small", text: "ไม่มีการเปิดก่อนเวลา / ปิดช้า และไม่มี 15 นาทีที่สูงผิดปกติ" }),
    el("p", { class: "muted small", text: "คลิกวันที่เพื่อดูวันนั้นทีละ 15 นาที · วันหยุดราชการ เสาร์ อาทิตย์ คิดราคาถูกทั้งวัน ไม่นับ" }));
}

// ---- one day, every meter
function amDayBox() {
  return el("div", { class: "el-chart", id: "amDay" }, el("div", { class: "el-chart-title", text: "ดูทีละวัน" }), el("p", { class: "muted small", text: "กำลังโหลด…" }));
}
async function amLoadDay(scroll = false) {
  const d = am_.data;
  if (!am_.day || !am_.day.startsWith(am_.month)) {      // default: the month's last working day with an event, else its last day
    const ev = d.events.filter((e) => e.day.startsWith(am_.month));
    am_.day = ev.length ? ev[ev.length - 1].day : `${am_.month}-${String(new Date(+am_.month.slice(0, 4), +am_.month.slice(5), 0).getDate()).padStart(2, "0")}`;
  }
  let r;
  try { r = await api(`/api/electricity/amr/day?branch=${encodeURIComponent(am_.branch)}&day=${am_.day}`); }
  catch (err) { const b = $("amDay"); if (b) fill(b, el("p", { class: "error", text: `โหลดวันนี้ไม่สำเร็จ: ${err.message}` })); return; }
  const box = $("amDay");
  if (!box) return;
  const go = (day) => { am_.day = day; if (!day.startsWith(am_.month) && d.months.includes(day.slice(0, 7))) { am_.month = day.slice(0, 7); renderAmr(); } else amLoadDay(); };
  const input = el("input", { type: "date", id: "amDayPick", value: r.day, "aria-label": "วันที่", onchange: (e) => e.target.value && go(e.target.value) });
  const head = el("div", { class: "am-bar" }, el("button", { class: "btn small", type: "button", text: "‹ วันก่อน", onclick: () => go(r.prev) }), input,
    el("button", { class: "btn small", type: "button", text: "วันถัดไป ›", onclick: () => go(r.next) }));
  if (!r.meters.length) { fill(box, el("div", { class: "el-chart-title", text: "ดูทีละวัน" }), head, el("p", { class: "muted small", text: "ไม่มีข้อมูลวันนี้" })); return; }
  const total = Array.from({ length: 96 }, (_, i) => (r.meters.every((m) => m.kw[i] != null) ? r.meters.reduce((a, m) => a + m.kw[i], 0) : null));
  const tou = r.meters[0].tou, band = amPeakBand(tou);
  const { svg, x, y } = amFrame(Math.max(...total.filter((v) => v != null)) * 1.08, (v) => elN(v), band);
  r.meters.forEach((m, j) => svg.append(sv("path", { d: amPath(m.kw, x, y), class: "am-line thin", style: `stroke:${AM_METER[d.meters.findIndex((t) => t.ca_no === m.ca_no) % 3]}` })));
  if (r.meters.length > 1) svg.append(sv("path", { d: amPath(total, x, y), class: "am-line" }));
  const nameOf = (ca) => { const mt = d.meters.find((t) => t.ca_no === ca); return mt ? `มิเตอร์ ${mt.name}` : ca; };
  amHover(svg, x, (i) => [`${amHH(i)}–${amHH(i + 1)} · ${tou[i] === "A" ? "Peak (แพง)" : tou[i] === "C" ? "วันหยุด (ถูก)" : "Off-peak"}`,
    ...(r.meters.length > 1 ? [["รวม", `${elN(total[i])} kW`, "var(--el-off)"]] : []),
    ...r.meters.map((m) => [nameOf(m.ca_no), `${elN(m.kw[i])} kW`, AM_METER[d.meters.findIndex((t) => t.ca_no === m.ca_no) % 3]])]);
  const wd = new Date(`${r.day}T00:00:00`).toLocaleDateString("th-TH", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  fill(box, el("div", { class: "el-chart-title", text: `ดูทีละวัน · ${wd}` }),
    el("div", { class: "muted small", text: band.length ? "แถบส้ม = ช่วง Peak ของวันนี้ · ขั้นขึ้น = เปิดเครื่อง ขั้นลง = ดับเครื่อง (เทียบเวลากับไลน์หน้าโรง)" : "วันหยุด (เสาร์ อาทิตย์ หรือวันหยุดราชการ) — ราคาถูกทั้งวัน" }),
    head, svg, elLegend([...(r.meters.length > 1 ? [["รวม", "var(--el-off)"]] : []), ...r.meters.map((m) => [nameOf(m.ca_no), AM_METER[d.meters.findIndex((t) => t.ca_no === m.ca_no) % 3]])]));
  if (scroll) box.scrollIntoView({ behavior: "smooth", block: "start" });
}
