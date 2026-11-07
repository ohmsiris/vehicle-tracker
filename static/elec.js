"use strict";

// บัญชี › ค่าไฟฟ้า (#electricity): every PEA bill read from its PDF (app/pea_bill.py, app/electricity.py), drawn so trends show.
//   * headline tiles for the latest month (both meters together), each with the change from the month before
//   * one panel per meter (small multiples -- the meters are told apart by panel, never by color):
//       kWh by tariff period (stacked) · peak-period demand kW (what the demand charge is set by) · kVAR against PEA's limit ·
//       cost per month by part
//   * the full table underneath
// Color has one job on this page -- the tariff period: peak = orange (the expensive hours), off-peak = blue, holiday = aqua
// (reference palette slots 2 / 1 / 3, validated all-pairs in light and dark). Over-limit kVAR uses the warning status color + a label.
// Charts are plain SVG with a hover tooltip; numbers in the tooltip and the table, never painted on every bar.

const el_ = { data: null, wired: false };
const ELEC_TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
const elMon = (p) => { const [y, m] = p.split("-").map(Number); return `${ELEC_TH[m - 1]} ${String(y + 543).slice(2)}`; };
const elN = (x, d = 0) => (x == null || Number.isNaN(x) ? "–" : Number(x).toLocaleString("th-TH", { minimumFractionDigits: d, maximumFractionDigits: d }));
const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}, ...kids) => { const n = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); kids.forEach((c) => c && n.append(c)); return n; };

async function openElectricity() {
  showView("electricity");
  showErr("elError", "");
  if (!el_.wired) {
    el_.wired = true;
    $("elBranch").onchange = loadElectricity;
  }
  await loadElectricity();
}

async function loadElectricity() {
  fill($("elBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { el_.data = await api(`/api/electricity?branch=${encodeURIComponent($("elBranch").value)}`); }
  catch (err) { fill($("elBody"), null); showErr("elError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  renderElectricity();
}

function renderElectricity() {
  EL_W = window.innerWidth < 700 ? 360 : 520;               // a narrow screen draws narrower charts, so the labels stay readable
  const meters = (el_.data.meters || []).map((m) => ({ ...m, months: [...m.months].reverse() }));       // oldest -> newest, left to right
  if (!meters.length) {
    fill($("elBody"), el("div", { class: "card" }, el("p", { class: "muted", text: "ยังไม่มีใบแจ้งค่าไฟ — แนบ PDF ของ PEA ที่ " }, el("a", { href: "#accounts-in", text: "บัญชี › ลงข้อมูล / แนบไฟล์" }), " แถวค่าไฟฟ้า แล้วระบบอ่านให้เอง")));
    return;
  }
  fill($("elBody"), elKpis(meters), el("div", { class: "el-grid" }, ...meters.map(elMeterPanel)), elTable(meters));
}

// ---- headline tiles: the latest month that every meter has, both meters added up
function elKpis(meters) {
  const periods = meters.map((m) => new Set(m.months.map((x) => x.bill_period)));
  const common = [...periods[0]].filter((p) => periods.every((s) => s.has(p))).sort();
  if (!common.length) return null;
  const last = common[common.length - 1], prev = common[common.length - 2];
  const sum = (p, f) => meters.reduce((a, m) => a + (f(m.months.find((x) => x.bill_period === p) || {}) || 0), 0);
  const tile = (label, f, fmt, betterDown = true, unit = "") => {
    const now = f(last), before = prev ? f(prev) : null;
    const ch = before ? (now - before) / before : null;
    const tone = ch == null || Math.abs(ch) < 0.005 ? "" : (ch > 0) === betterDown ? "up-bad" : "up-good";
    return el("div", { class: "kpi el-kpi" }, el("div", { class: "kpi-label", text: label }), el("div", { class: "kpi-value", text: `${fmt(now)}${unit}` }),
      ch == null ? null : el("div", { class: `el-delta ${tone}`, text: `${ch > 0 ? "▲" : "▼"} ${elN(Math.abs(ch) * 100, 1)}% จาก ${elMon(prev)}` }));
  };
  const kwh = (p) => sum(p, (x) => x.kwh_total), total = (p) => sum(p, (x) => x.total);
  return el("div", {},
    el("p", { class: "muted small", text: `เดือนล่าสุดที่มีครบทุกมิเตอร์: ${elMon(last)} · รวม ${meters.length} มิเตอร์` }),
    el("div", { class: "acx-kpis" },
      tile("ค่าไฟรวม (รวม VAT)", total, (v) => `${elN(v)} ฿`),
      tile("หน่วยที่ใช้", kwh, (v) => elN(v), true, " kWh"),
      tile("บาทต่อหน่วย", (p) => total(p) / kwh(p), (v) => elN(v, 2), true, " ฿"),
      tile("สัดส่วนช่วง Peak", (p) => sum(p, (x) => x.kwh_peak) / kwh(p), (v) => elN(v * 100, 1), true, "%"),
      tile("ค่า demand", (p) => sum(p, (x) => x.demand_charge), (v) => `${elN(v)} ฿`),
      tile("ค่า power factor", (p) => sum(p, (x) => x.pf_charge), (v) => `${elN(v)} ฿`)));
}

// ---- one meter: four small charts
function elMeterPanel(m) {
  const ms = m.months;
  const pfYear = ms.reduce((a, x) => a + (x.pf_charge || 0), 0);
  return el("div", { class: "card el-panel" },
    el("div", { class: "el-panel-head" }, el("h3", { text: `มิเตอร์ ${m.meter}` }), el("span", { class: "muted small", text: `${m.branch} · CA ${m.ca_no}` }),
      pfYear ? el("span", { class: "badge warn", text: `⚠ เสียค่า power factor ${elN(pfYear)} ฿ ใน ${ms.filter((x) => x.pf_charge).length} เดือน` }) : null),
    elChart("หน่วยไฟ (kWh) แยกช่วงเวลา", "ส้ม = Peak จ–ศ 09:00–22:00 (แพง) · ฟ้า = Off-peak · เขียวน้ำทะเล = วันหยุด",
      elStack(ms, [["kwh_peak", "Peak", "var(--el-peak)"], ["kwh_offpeak", "Off-peak", "var(--el-off)"], ["kwh_holiday", "วันหยุด", "var(--el-hol)"]], (v) => `${elN(v)} kWh`),
      elLegend([["Peak", "var(--el-peak)"], ["Off-peak", "var(--el-off)"], ["วันหยุด", "var(--el-hol)"]])),
    elChart("Demand สูงสุดช่วง Peak (kW)", "ตัวนี้ตัวเดียวกำหนดค่า demand ทั้งเดือน — 15 นาทีที่สูงที่สุดของช่วง Peak",
      elLine(ms, "demand_peak_kw", (x) => `${elN(x.demand_peak_kw, 1)} kW · ค่า demand ${elN(x.demand_charge)} ฿`)),
    elChart("kVAR สูงสุด เทียบเพดาน PEA", "เกินเส้นประ (61.97% ของ kW สูงสุด) = เสียค่า power factor → เช็คคาปาซิเตอร์",
      elKvar(ms)),
    elChart("ค่าไฟต่อเดือน (บาท)", "แยกส่วน: ค่าพลังงาน Peak / Off-peak · ค่า demand · Ft · ค่า PF · VAT",
      elStack(ms, [["energy_peak", "พลังงาน Peak", "var(--el-peak)"], ["energy_offpeak", "พลังงาน Off-peak", "var(--el-off)"], ["demand_charge", "ค่า demand", "var(--el-dem)"],
        ["ft_amount", "Ft", "var(--el-ft)"], ["pf_charge", "ค่า PF", "var(--warn)"], ["vat", "VAT", "var(--el-vat)"]], (v) => `${elN(v)} ฿`),
      elLegend([["พลังงาน Peak", "var(--el-peak)"], ["พลังงาน Off-peak", "var(--el-off)"], ["Demand", "var(--el-dem)"], ["Ft", "var(--el-ft)"], ["ค่า PF", "var(--warn)"], ["VAT", "var(--el-vat)"]])));
}

function elChart(title, sub, svg, legend = null) {
  return el("div", { class: "el-chart" }, el("div", { class: "el-chart-title", text: title }), el("div", { class: "muted small", text: sub }), legend, svg);
}

function elLegend(items) {
  return el("div", { class: "el-legend" }, ...items.map(([t, c]) => el("span", {}, el("i", { style: `background:${c}` }), t)));
}

// geometry shared by the small charts
let EL_W = 520; const EL_H = 170, EL_P = { l: 58, r: 10, t: 10, b: 24 };
function elFrame(max, fmtTick) {
  const svg = sv("svg", { viewBox: `0 0 ${EL_W} ${EL_H}`, class: "el-svg", role: "img" });
  const step = elNiceStep((max || 1) / 4);                         // round steps (1 / 2 / 5 × 10ⁿ), so ticks read 50k, 100k ...
  const nice = Math.ceil((max || 1) / step) * step;
  const y = (v) => EL_P.t + (EL_H - EL_P.t - EL_P.b) * (1 - v / nice);
  for (let v = 0, i = 0; v <= nice + step / 2; v += step, i++) {
    const yy = y(v);
    svg.append(sv("line", { x1: EL_P.l, x2: EL_W - EL_P.r, y1: yy, y2: yy, class: i ? "el-gl" : "el-base" }),
      sv("text", { x: EL_P.l - 6, y: yy + 4, "text-anchor": "end", class: "el-tick" }, document.createTextNode(fmtTick(v))));
  }
  return { svg, y, nice };
}
function elNiceStep(v) {
  const p = 10 ** Math.floor(Math.log10(v)), f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
const elShort = (v) => (v >= 1e6 ? `${elN(v / 1e6, 1)}M` : v >= 1e3 ? `${elN(v / 1e3, v >= 1e4 ? 0 : 1)}k` : elN(v));
function elSlots(n) {
  const inner = EL_W - EL_P.l - EL_P.r, w = inner / n;
  return { w, x: (i) => EL_P.l + w * i, bar: Math.min(56, w * 0.56) };
}
function elXLabels(svg, ms, s) {
  ms.forEach((x, i) => svg.append(sv("text", { x: s.x(i) + s.w / 2, y: EL_H - 6, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(elMon(x.bill_period)))));
}

// stacked bars, 2px surface gap between segments, rounded top
function elStack(ms, keys, fmt) {
  const totals = ms.map((x) => keys.reduce((a, [k]) => a + (x[k] || 0), 0));
  const { svg, y } = elFrame(Math.max(...totals), elShort);
  const s = elSlots(ms.length);
  ms.forEach((x, i) => {
    let acc = 0;
    const bx = s.x(i) + (s.w - s.bar) / 2;
    const g = sv("g", { class: "el-hit" });
    const parts = keys.filter(([k]) => x[k] > 0);
    parts.forEach(([k], j) => {
      const v = x[k], y0 = y(acc), y1 = y(acc + v);
      acc += v;
      const top = j === parts.length - 1;
      const h = Math.max(0, y0 - y1 - (top ? 0 : 2));
      g.append(sv("rect", { x: bx, y: y1 + (top ? 0 : 2), width: s.bar, height: h, rx: top ? 4 : 0, fill: keys.find((kk) => kk[0] === k)[2] }));
    });
    g.append(sv("rect", { x: s.x(i), y: EL_P.t, width: s.w, height: EL_H - EL_P.t - EL_P.b, fill: "transparent" }));
    elTip(g, () => [elMon(x.bill_period), ...keys.filter(([k]) => x[k]).map(([k, label, c]) => [label, fmt(x[k]), c]), ["รวม", fmt(totals[i])]]);
    svg.append(g);
  });
  elXLabels(svg, ms, s);
  return svg;
}

// one series line with 8px markers
function elLine(ms, key, tipText) {
  const vals = ms.map((x) => x[key] || 0);
  const { svg, y } = elFrame(Math.max(...vals) * 1.15, (v) => elN(v));
  const s = elSlots(ms.length);
  const pts = ms.map((x, i) => [s.x(i) + s.w / 2, y(vals[i])]);
  svg.append(sv("polyline", { points: pts.map((p) => p.join(",")).join(" "), class: "el-line" }));
  ms.forEach((x, i) => {
    const g = sv("g", { class: "el-hit" });
    g.append(sv("circle", { cx: pts[i][0], cy: pts[i][1], r: 4.5, class: "el-dot" }),
      sv("rect", { x: s.x(i), y: EL_P.t, width: s.w, height: EL_H - EL_P.t - EL_P.b, fill: "transparent" }));
    elTip(g, () => [elMon(x.bill_period), [null, tipText(x)]]);
    svg.append(g);
  });
  const last = ms.length - 1;                                    // one direct label: the latest value
  svg.append(sv("text", { x: pts[last][0], y: pts[last][1] - 10, "text-anchor": "middle", class: "el-val" }, document.createTextNode(elN(vals[last], 1))));
  elXLabels(svg, ms, s);
  return svg;
}

// kVAR bars against the month's limit (a dashed tick per bar); over the limit = warning color + "เกิน" label
function elKvar(ms) {
  const { svg, y } = elFrame(Math.max(...ms.map((x) => Math.max(x.kvar_max || 0, x.kvar_limit || 0))) * 1.1, (v) => elN(v));
  const s = elSlots(ms.length);
  ms.forEach((x, i) => {
    const bx = s.x(i) + (s.w - s.bar) / 2, over = x.kvar_over > 0;
    const g = sv("g", { class: "el-hit" });
    g.append(sv("rect", { x: bx, y: y(x.kvar_max), width: s.bar, height: y(0) - y(x.kvar_max), rx: 4, class: over ? "el-kvar over" : "el-kvar" }));
    if (x.kvar_limit) g.append(sv("line", { x1: bx - 8, x2: bx + s.bar + 8, y1: y(x.kvar_limit), y2: y(x.kvar_limit), class: "el-limit" }));
    if (over) g.append(sv("text", { x: bx + s.bar / 2, y: y(x.kvar_max) - 6, "text-anchor": "middle", class: "el-val warn" }, document.createTextNode(`⚠ เกิน ${elN(x.kvar_over, 0)}`)));
    g.append(sv("rect", { x: s.x(i), y: EL_P.t, width: s.w, height: EL_H - EL_P.t - EL_P.b, fill: "transparent" }));
    elTip(g, () => [elMon(x.bill_period), ["kVAR สูงสุด", elN(x.kvar_max, 1)], ["เพดาน (61.97% × kW)", elN(x.kvar_limit, 1)],
      ["ค่า power factor", x.pf_charge ? `${elN(x.pf_charge, 2)} ฿` : "ไม่เสีย"]]);
    svg.append(g);
  });
  elXLabels(svg, ms, s);
  return svg;
}

// hover tooltip (one shared box), the hit target is the whole month column; a tap anywhere else closes it on a phone
document.addEventListener("pointerdown", (e) => { if (!e.target.closest || !e.target.closest(".el-hit")) { const tip = $("elTip"); if (tip) tip.hidden = true; } });
function elTip(g, rows) {
  const show = () => {
    let tip = $("elTip");
    if (!tip) { tip = el("div", { id: "elTip", class: "el-tip" }); document.body.append(tip); }
    const [head, ...lines] = rows();
    fill(tip, el("strong", { text: head }), ...lines.map(([label, val, c]) => el("div", { class: "el-tip-row" },
      c ? el("i", { style: `background:${c}` }) : null, label ? el("span", { class: "muted", text: label }) : null, el("b", { text: val }))));
    tip.hidden = false;
  };
  const place = (e) => {
    const tip = $("elTip"); if (!tip) return;
    const x = Math.max(8, Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8));
    tip.style.left = `${x}px`; tip.style.top = `${e.clientY + 14}px`;
  };
  g.addEventListener("pointerenter", (e) => { show(); place(e); });
  g.addEventListener("pointermove", place);
  g.addEventListener("pointerdown", (e) => { show(); place(e); });          // a tap on a phone (no hover there)
  g.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") { const tip = $("elTip"); if (tip) tip.hidden = true; } });
}

// ---- the numbers, for anyone who wants them
function elTable(meters) {
  const head = ["เดือน", "หน่วย (kWh)", "Peak %", "Demand kW", "ค่า demand", "kVAR / เพดาน", "ค่า PF", "Ft", "รวม (บาท)", "บาท/หน่วย"];
  return el("div", { class: "card acx-feeds" }, el("h3", { text: "ตัวเลขทั้งหมด" }),
    ...meters.map((m) => el("div", { class: "acx-elec" },
      el("strong", { text: `มิเตอร์ ${m.meter}` }),
      el("div", { class: "acx-elec-row el-table-row head small muted" }, ...head.map((t) => el("span", { text: t }))),
      ...[...m.months].reverse().map((x) => el("div", { class: "acx-elec-row el-table-row" },
        el("span", { text: elMon(x.bill_period) }), el("span", { text: elN(x.kwh_total) }),
        el("span", { class: x.peak_share > 0.3 ? "warn-text" : "", text: `${elN(x.peak_share * 100)}%` }),
        el("span", { text: elN(x.demand_peak_kw, 1) }), el("span", { text: elN(x.demand_charge) }),
        el("span", { class: x.kvar_over > 0 ? "warn-text" : "muted", text: `${elN(x.kvar_max, 1)} / ${elN(x.kvar_limit, 1)}` }),
        el("span", { class: x.pf_charge ? "warn-text" : "muted", text: x.pf_charge ? elN(x.pf_charge, 2) : "–" }),
        el("span", { text: elN(x.ft_amount) }), el("strong", { text: elN(x.total, 2) }), el("span", { text: elN(x.baht_per_kwh, 2) }))))));
}
