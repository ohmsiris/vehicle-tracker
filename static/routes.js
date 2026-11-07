"use strict";

// บัญชี › สายรถ กำไรต่อคัน (#routes): Saraburi's delivery trucks, month by month (app/routes_report.py).
//   * a month range (default: the months whose fuel is known truck by truck), the months' data status as chips
//   * headline tiles for the range
//   * where each truck's sales go: one horizontal bar per truck -- profit / fuel / driver pay / repairs -- sorted by profit
//   * sales vs sales-per-baht-of-fuel scatter, split by the medians into four groups
//   * route profit by month, all trucks together
//   * the full table (sortable); a row opens the truck's months
// Color jobs: the money split (profit = aqua, fuel = orange, pay = blue, repairs = purple -- the electricity page's validated slots);
// the four groups reuse the same four hues, always with their name beside them. Plain SVG, hover tooltip (elec.js's elTip).

const rt_ = { data: null, wired: false, sort: "profit", desc: true, open: null };
const RT_PARTS = [["profit", "กำไรสายรถ", "var(--el-hol)"], ["fuel", "น้ำมัน", "var(--el-peak)"], ["pay", "ค่าแรงคนขับ", "var(--el-off)"], ["repairs", "ซ่อม / ยาง / อะไหล่", "var(--el-dem)"]];
const RT_GROUPS = {
  star: ["ดาวเด่น", "ขายมาก + คุ้มน้ำมัน", "var(--el-hol)"],
  leak: ["ขายมากแต่กินน้ำมัน", "ยอดสูง แต่น้ำมันกินกำไร", "var(--el-peak)"],
  lean: ["ประหยัดแต่ยอดน้อย", "คุ้มน้ำมัน ยังขยายยอดได้", "var(--el-off)"],
  watch: ["ต้องดู", "ยอดต่ำ + น้ำมันสูง", "var(--el-dem)"],
};
const RT_STATUS = { ok: ["ครบ", "ok"], no_cash: ["ไม่มีน้ำมันเงินสด", "warn"], short: ["น้ำมันไม่ครบ · ไม่จัดอันดับ", "bad"] };

async function openRoutes() {
  showView("routes");
  showErr("rtError", "");
  if (!rt_.wired) {
    rt_.wired = true;
    $("rtFrom").onchange = renderRoutes;
    $("rtTo").onchange = renderRoutes;
    $("rtBest").onclick = () => { rtBestRange(); renderRoutes(); };
  }
  fill($("rtBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { rt_.data = await api("/api/routes"); }
  catch (err) { fill($("rtBody"), null); showErr("rtError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  const ms = rt_.data.months;
  if (!ms.length) { fill($("rtBody"), el("div", { class: "card" }, el("p", { class: "muted", text: "ยังไม่มีข้อมูลยอดขายสายรถ" }))); return; }
  for (const id of ["rtFrom", "rtTo"]) fill($(id), ...ms.map((m) => el("option", { value: m.month, text: elMon(m.month) })));
  rtBestRange();
  renderRoutes();
}

function rtBestRange() {
  const ok = rt_.data.months.filter((m) => m.status !== "short");
  const ms = ok.length ? ok : rt_.data.months;
  $("rtFrom").value = ms[0].month;
  $("rtTo").value = ms[ms.length - 1].month;
}

// one row per truck over the chosen months
function rtRows(from, to) {
  return rt_.data.trucks.map((t) => {
    const ms = Object.entries(t.months).filter(([m]) => m >= from && m <= to);
    if (!ms.length) return null;
    const s = { sales: 0, written: 0, fuel: 0, pay: 0, repairs: 0, km: 0, kmMonths: 0, fuelKm: 0 };
    const drivers = new Set();
    for (const [, x] of ms) {
      for (const k of ["sales", "written", "fuel", "pay", "repairs"]) s[k] += x[k] || 0;
      if (x.km) { s.km += x.km; s.fuelKm += x.fuel; s.kmMonths++; }
      x.drivers.forEach((d) => d && drivers.add(d.split(" ")[0]));
    }
    s.profit = s.sales - s.fuel - s.pay - s.repairs;
    return { ...t, ...s, n: ms.length, drivers: [...drivers], margin: s.sales ? s.profit / s.sales : null,
      perFuel: s.fuel ? s.sales / s.fuel : null, fuelPerKm: s.km ? s.fuelKm / s.km : null, perMonth: s.profit / ms.length };
  }).filter((r) => r && (r.sales || r.fuel));
}

const rtMedian = (xs) => { const v = xs.filter((x) => x != null).sort((a, b) => a - b); return v.length ? (v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2) : null; };
const rtName = (r) => `#${r.number}`;
const rtK = (v) => elN((v || 0) / 1000);                      // the table shows thousands of baht, so the columns stay narrow
const rtDriver = (r) => (r.driver_now ? r.driver_now.split(" ")[0] : r.drivers.join(" / ") || "–");

function renderRoutes() {
  let from = $("rtFrom").value, to = $("rtTo").value;
  if (from > to) [from, to] = [to, from];
  const months = rt_.data.months.filter((m) => m.month >= from && m.month <= to);
  const rows = rtRows(from, to);
  const ranked = rows.filter((r) => r.sales > 0 && r.fuel > 0);
  const medS = rtMedian(ranked.map((r) => r.sales)), medR = rtMedian(ranked.map((r) => r.perFuel));
  for (const r of rows) {
    r.group = !(r.sales > 0 && r.fuel > 0) ? null : r.sales >= medS ? (r.perFuel >= medR ? "star" : "leak") : (r.perFuel >= medR ? "lean" : "watch");
  }
  const short = months.some((m) => m.status === "short");
  fill($("rtBody"),
    el("div", { class: "rt-status" }, ...months.map((m) => el("span", { class: `badge ${RT_STATUS[m.status][1]}`, title: RT_STATUS[m.status][0], text: `${elMon(m.month)} · ${RT_STATUS[m.status][0]}` }))),
    short ? el("p", { class: "muted small", text: "เดือนที่ 'น้ำมันไม่ครบ' (ม.ค.–เม.ย.): ไม่มีน้ำมันแยกคัน — รถใหญ่ยอดขายเต็มต่ำไป (บวกน้ำมันคืนไม่ได้) และน้ำมันทุกคันเป็น 0 กำไรจึงสูงเกินจริง ใช้ดูแนวโน้มยอดขายได้ ไม่ควรใช้จัดอันดับ" }) : null,
    rtKpis(rows, ranked, medR, months.length),
    el("div", { class: "card el-panel" }, el("div", { class: "el-panel-head" }, el("h3", { text: "ยอดขายแต่ละคันไปไหน" }),
      el("span", { class: "muted small", text: `${elMon(from)}–${elMon(to)} · เรียงตามกำไร · ความยาวแท่ง = ยอดขาย` })),
      elLegend(RT_PARTS.map(([, t, c]) => [t, c])), rtSplit(rows)),
    el("div", { class: "el-grid" },
      el("div", { class: "card el-panel" }, elChart("ยอดขาย เทียบ ยอดขายต่อน้ำมัน 1 บาท", `เส้นประ = ค่ากลาง (ยอด ${elShort(medS || 0)} · ${elN(medR, 1)} บาท/บาท) แบ่ง 4 กลุ่ม`,
        rtScatter(ranked, medS, medR), elLegend(Object.values(RT_GROUPS).map(([t, , c]) => [t, c])))),
      el("div", { class: "card el-panel" }, elChart("กำไรสายรถรวมทุกคัน รายเดือน", "ยอดขายรวมแยกส่วน · เดือนที่ขีดลาย = น้ำมันไม่ครบ",
        rtMonthly(), elLegend(RT_PARTS.map(([, t, c]) => [t, c]))))),
    rtGroupsCard(rows),
    rtTable(rows));
}

function rtKpis(rows, ranked, medR, n) {
  const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
  const sales = sum("sales"), profit = sum("profit");
  const tile = (label, value, sub) => el("div", { class: "kpi el-kpi" }, el("div", { class: "kpi-label", text: label }), el("div", { class: "kpi-value", text: value }),
    sub ? el("div", { class: "el-delta", text: sub }) : null);
  const watch = rows.filter((r) => r.group === "watch").length;
  return el("div", { class: "acx-kpis" },
    tile("ยอดขายสายรถ", `${elShort(sales)} ฿`, `${n} เดือน · ${rows.length} คัน · เฉลี่ย ${elShort(sales / n)}/เดือน`),
    tile("น้ำมัน", `${elShort(sum("fuel"))} ฿`, sales ? `${elN((sum("fuel") / sales) * 100, 1)}% ของยอดขาย` : null),
    tile("ค่าแรงคนขับ", `${elShort(sum("pay"))} ฿`, sales ? `${elN((sum("pay") / sales) * 100, 1)}% ของยอดขาย` : null),
    tile("กำไรสายรถ (ก่อนค่าน้ำแข็ง)", `${elShort(profit)} ฿`, sales ? `${elN((profit / sales) * 100, 1)}% ของยอดขาย` : null),
    tile("ยอดขายต่อน้ำมัน 1 บาท", `${elN(medR, 1)} ฿`, "ค่ากลางของทุกคัน"),
    tile("คันที่ต้องดู", `${watch} คัน`, "ยอดต่ำกว่ากลาง + น้ำมันสูงกว่ากลาง"));
}

// ---- horizontal split bars: one per truck, the sales cut into profit / fuel / pay / repairs
function rtSplit(rows) {
  const rs = [...rows].sort((a, b) => b.profit - a.profit);
  const W = Math.min(980, Math.max(340, window.innerWidth - 80)), lab = W < 600 ? 92 : 150, H = 22, gap = 6, P = 40;
  const max = Math.max(...rs.map((r) => Math.max(r.sales, r.fuel + r.pay + r.repairs)), 1);
  const x = (v) => lab + (W - lab - P) * (v / max);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${rs.length * (H + gap) + 8}`, class: "el-svg rt-split", role: "img" });
  rs.forEach((r, i) => {
    const y = i * (H + gap) + 4;
    const g = sv("g", { class: "el-hit" });
    g.append(sv("text", { x: 0, y: y + H / 2 + 4, class: "el-tick rt-lab" }, document.createTextNode(`${rtName(r)} ${rtDriver(r)}`.slice(0, W < 600 ? 13 : 22))));
    const parts = r.profit >= 0 ? RT_PARTS : RT_PARTS.slice(1);
    let acc = 0;
    const segs = parts.map(([k, , c]) => [Math.max(0, r[k]), c]).filter(([v]) => v > 0);
    segs.forEach(([v, c], j) => {
      const last = j === segs.length - 1;
      g.append(sv("rect", { x: x(acc) + (j ? 2 : 0), y, width: Math.max(0, x(acc + v) - x(acc) - (j ? 2 : 0)), height: H, rx: last ? 4 : 0, fill: c }));
      acc += v;
    });
    if (r.profit < 0) g.append(sv("text", { x: x(acc) + 6, y: y + H / 2 + 4, class: "el-val warn" }, document.createTextNode(`ขาดทุน ${elShort(-r.profit)}`)));
    else g.append(sv("text", { x: x(acc) + 6, y: y + H / 2 + 4, class: "el-tick" }, document.createTextNode(elShort(r.profit))));
    g.append(sv("rect", { x: 0, y: y - gap / 2, width: W, height: H + gap, fill: "transparent" }));
    elTip(g, () => [`${rtName(r)} · ${rtDriver(r)} (${r.plate || ""})`, ["ยอดขาย", `${elN(r.sales)} ฿`],
      ...RT_PARTS.map(([k, t, c]) => [t, `${elN(r[k])} ฿${r.sales && k !== "profit" ? ` · ${elN((r[k] / r.sales) * 100, 1)}%` : ""}`, c]),
      ["ยอดขายต่อน้ำมัน 1 บาท", r.perFuel ? elN(r.perFuel, 1) : "–"], r.drivers.length ? ["คนขับในช่วงนี้", r.drivers.join(", ")] : null].filter(Boolean));
    svg.append(g);
  });
  return svg;
}

// ---- scatter: x = sales, y = sales per baht of fuel; dashed medians; dot color = group (named in the legend and the tooltip)
function rtScatter(rows, medS, medR) {
  const W = EL_W, H = 260, P = { l: 46, r: 14, t: 12, b: 28 };
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "el-svg", role: "img" });
  const mx = elNiceStep(Math.max(...rows.map((r) => r.sales), 1) / 4) * 4, my = Math.ceil(Math.max(...rows.map((r) => r.perFuel || 0), 1) / 5) * 5;
  const x = (v) => P.l + (W - P.l - P.r) * (v / mx), y = (v) => P.t + (H - P.t - P.b) * (1 - v / my);
  for (let i = 0; i <= 4; i++) {
    const vx = (mx / 4) * i, vy = (my / 4) * i;
    svg.append(sv("line", { x1: P.l, x2: W - P.r, y1: y(vy), y2: y(vy), class: i ? "el-gl" : "el-base" }),
      sv("text", { x: P.l - 6, y: y(vy) + 4, "text-anchor": "end", class: "el-tick" }, document.createTextNode(elN(vy, vy < 10 ? 1 : 0))),
      sv("text", { x: x(vx), y: H - 8, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(elShort(vx))));
  }
  if (medS) svg.append(sv("line", { x1: x(medS), x2: x(medS), y1: P.t, y2: H - P.b, class: "el-limit" }));
  if (medR) svg.append(sv("line", { x1: P.l, x2: W - P.r, y1: y(medR), y2: y(medR), class: "el-limit" }));
  const notable = new Set([...rows].sort((a, b) => b.perFuel - a.perFuel).slice(0, 2).concat([...rows].sort((a, b) => a.profit - b.profit).slice(0, 2)));
  rows.forEach((r) => {
    const g = sv("g", { class: "el-hit" });
    const cx = x(r.sales), cy = y(r.perFuel);
    g.append(sv("circle", { cx, cy, r: 6, fill: RT_GROUPS[r.group][2], class: "rt-dot" }), sv("circle", { cx, cy, r: 14, fill: "transparent" }));
    if (notable.has(r)) g.append(sv("text", { x: cx + 9, y: cy + 4, class: "el-tick" }, document.createTextNode(rtName(r))));
    elTip(g, () => [`${rtName(r)} · ${rtDriver(r)}`, [RT_GROUPS[r.group][0], "", RT_GROUPS[r.group][2]], ["ยอดขาย", `${elN(r.sales)} ฿`],
      ["น้ำมัน", `${elN(r.fuel)} ฿`], ["ยอดขายต่อน้ำมัน 1 บาท", elN(r.perFuel, 1)], ["กำไรสายรถ", `${elN(r.profit)} ฿`]]);
    svg.append(g);
  });
  return svg;
}

// ---- every truck together, month by month (all months, the short ones hatched)
function rtMonthly() {
  const ms = rt_.data.months.map((m) => {
    const s = { month: m.month, status: m.status, profit: 0, fuel: 0, pay: 0, repairs: 0 };
    for (const t of rt_.data.trucks) {
      const x = t.months[m.month];
      if (x) { s.fuel += x.fuel; s.pay += x.pay; s.repairs += x.repairs; s.profit += x.profit; }
    }
    s.sales = s.profit + s.fuel + s.pay + s.repairs;
    return s;
  });
  const { svg, y } = elFrame(Math.max(...ms.map((m) => m.sales)), elShort);
  const s = elSlots(ms.length);
  svg.append(sv("defs", {}, sv("pattern", { id: "rtHatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    sv("rect", { width: 6, height: 6, fill: "transparent" }), sv("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--surface)", "stroke-width": 2.5 }))));
  ms.forEach((m, i) => {
    const bx = s.x(i) + (s.w - s.bar) / 2;
    const g = sv("g", { class: "el-hit" });
    let acc = 0;
    const parts = RT_PARTS.filter(([k]) => m[k] > 0).reverse();       // profit on top
    parts.forEach(([k, , c], j) => {
      const v = m[k], y0 = y(acc), y1 = y(acc + v), top = j === parts.length - 1;
      acc += v;
      g.append(sv("rect", { x: bx, y: y1 + (top ? 0 : 2), width: s.bar, height: Math.max(0, y0 - y1 - (top ? 0 : 2)), rx: top ? 4 : 0, fill: c }));
    });
    if (m.status === "short") g.append(sv("rect", { x: bx, y: y(acc), width: s.bar, height: y(0) - y(acc), fill: "url(#rtHatch)" }));
    g.append(sv("rect", { x: s.x(i), y: EL_P.t, width: s.w, height: EL_H - EL_P.t - EL_P.b, fill: "transparent" }));
    elTip(g, () => [`${elMon(m.month)} · ${RT_STATUS[m.status][0]}`, ["ยอดขาย", `${elN(m.sales)} ฿`], ...RT_PARTS.map(([k, t, c]) => [t, `${elN(m[k])} ฿`, c])]);
    svg.append(g);
  });
  elXLabels(svg, ms.map((m) => ({ bill_period: m.month })), s);
  return svg;
}

function rtGroupsCard(rows) {
  const by = (g) => rows.filter((r) => r.group === g).sort((a, b) => b.sales - a.sales);
  return el("div", { class: "card el-panel" }, el("div", { class: "el-panel-head" }, el("h3", { text: "4 กลุ่ม" }),
    el("span", { class: "muted small", text: "แบ่งด้วยค่ากลางของยอดขาย และยอดขายต่อน้ำมัน 1 บาท — เทียบกันเองในช่วงเดือนที่เลือก" })),
  el("div", { class: "rt-groups" }, ...["watch", "leak", "lean", "star"].map((g) => el("div", { class: "rt-group" },
    el("div", { class: "rt-group-head" }, el("i", { style: `background:${RT_GROUPS[g][2]}` }), el("strong", { text: `${RT_GROUPS[g][0]} (${by(g).length})` })),
    el("div", { class: "muted small", text: RT_GROUPS[g][1] }),
    el("div", { class: "rt-chips" }, ...by(g).map((r) => el("button", { class: "rt-chip", type: "button", text: `${rtName(r)} ${rtDriver(r)}`,
      onclick: () => { rt_.open = r.vehicle_id; renderRoutes(); const row = document.querySelector(`[data-rt="${r.vehicle_id}"]`); if (row) row.scrollIntoView({ block: "center" }); } })))))));
}

// ---- the table: sortable; a row opens that truck's months
const RT_COLS = [["number", "คัน"], ["driver", "คนขับ"], ["sales", "ยอดขาย"], ["fuel", "น้ำมัน"], ["pay", "ค่าแรง"], ["repairs", "ซ่อม"], ["profit", "กำไร"],
  ["margin", "กำไร %"], ["perFuel", "ขาย/น้ำมัน"], ["fuelPerKm", "น้ำมัน/กม."], ["perMonth", "กำไร/เดือน"]];
function rtTable(rows) {
  const val = (r, k) => (k === "driver" ? rtDriver(r) : k === "number" ? (Number(r.number) || 999) : r[k] ?? -Infinity);
  const rs = [...rows].sort((a, b) => { const A = val(a, rt_.sort), B = val(b, rt_.sort); return (A > B ? 1 : A < B ? -1 : 0) * (rt_.desc ? -1 : 1); });
  const head = el("div", { class: "acx-elec-row rt-row head small muted" }, ...RT_COLS.map(([k, t]) => el("button", { type: "button", class: "rt-sort",
    text: t + (rt_.sort === k ? (rt_.desc ? " ▼" : " ▲") : ""), onclick: () => { rt_.desc = rt_.sort === k ? !rt_.desc : k !== "driver" && k !== "number"; rt_.sort = k; renderRoutes(); } })));
  const out = [head];
  for (const r of rs) {
    out.push(el("div", { class: `acx-elec-row rt-row${rt_.open === r.vehicle_id ? " open" : ""}`, "data-rt": r.vehicle_id, role: "button", tabindex: "0",
      onclick: () => { rt_.open = rt_.open === r.vehicle_id ? null : r.vehicle_id; renderRoutes(); } },
    el("span", {}, r.group ? el("i", { class: "rt-gdot", style: `background:${RT_GROUPS[r.group][2]}`, title: RT_GROUPS[r.group][0] }) : null, rtName(r)),
    el("span", { text: rtDriver(r), title: r.drivers.join(", ") }),
    el("span", { text: rtK(r.sales) }), el("span", { text: rtK(r.fuel) }), el("span", { text: rtK(r.pay) }), el("span", { text: rtK(r.repairs) }),
    el("strong", { class: r.profit < 0 ? "warn-text" : "", text: rtK(r.profit) }),
    el("span", { text: r.margin == null ? "–" : `${elN(r.margin * 100)}%` }),
    el("span", { text: r.perFuel ? elN(r.perFuel, 1) : "–" }),
    el("span", { class: "muted", text: r.fuelPerKm ? elN(r.fuelPerKm, 2) : "–" }),
    el("span", { text: rtK(r.perMonth) })));
    if (rt_.open === r.vehicle_id) out.push(rtTruckMonths(r));
  }
  return el("div", { class: "card acx-feeds" }, el("h3", { text: "ทุกคัน" }),
    el("p", { class: "muted small", text: "เงินเป็นพันบาท · กดหัวคอลัมน์เพื่อเรียง · กดแถวเพื่อดูรายเดือน (ตัวเลขเต็ม) · น้ำมัน/กม. มีเฉพาะคันที่มีเลขไมล์หรือ GPS" }), el("div", { class: "acx-elec" }, ...out));
}

function rtTruckMonths(r) {
  const t = rt_.data.trucks.find((x) => x.vehicle_id === r.vehicle_id);
  const ms = rt_.data.months.filter((m) => t.months[m.month]).map((m) => ({ bill_period: m.month, status: m.status, ...t.months[m.month] }));
  const st = Object.fromEntries(rt_.data.months.map((m) => [m.month, m.status]));
  return el("div", { class: "rt-detail" },
    el("div", { class: "el-chart-title", text: `${rtName(r)} · ${r.plate || ""} · คนขับตอนนี้: ${r.driver_now || "–"}` }),
    elStack(ms.map((x) => ({ ...x, profit: Math.max(0, x.profit) })), RT_PARTS.slice().reverse().map(([k, t2, c]) => [k, t2, c]), (v) => `${elN(v)} ฿`),
    el("div", { class: "acx-elec-row rt-mrow head small muted" }, ...["เดือน", "คนขับ", "ยอดเขียน", "ยอดเต็ม", "น้ำมัน", "ค่าแรง", "ซ่อม", "กำไร", "กม."].map((h) => el("span", { text: h }))),
    ...[...ms].reverse().map((x) => el("div", { class: "acx-elec-row rt-mrow small" },
      el("span", { class: st[x.bill_period] === "short" ? "muted" : "", text: elMon(x.bill_period) + (st[x.bill_period] === "short" ? " *" : "") }),
      el("span", { text: x.drivers.map((d) => d.split(" ")[0]).join(" / ") || "–" }),
      el("span", { text: elN(x.written) }), el("span", { text: elN(x.sales) }), el("span", { text: elN(x.fuel) }), el("span", { text: elN(x.pay) }),
      el("span", { text: elN(x.repairs) }), el("strong", { class: x.profit < 0 ? "warn-text" : "", text: elN(x.profit) }), el("span", { class: "muted", text: x.km ? elN(x.km) : "–" }))),
    el("p", { class: "muted small", text: "* น้ำมันเดือนนั้นยังไม่มีแยกคัน" }));
}
