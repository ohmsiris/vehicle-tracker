"use strict";

// The home page (หน้าแรก), design "เช้านี้": what needs the owner today first, then the numbers. The owner's alone.
// Both branches as ONE view (owner, 21 Nov 2026): no branch switch, no per-branch blocks.
//   row 1   left: "ต้องดูวันนี้", one ranked list across the whole app (bad, then warn, then info); every row opens its page
//           right: the tiles (each with the date its data reaches) and the latest closed month's profit
//   row 2   two charts: route sales per day (line) and net profit per month by branch (grouped bars)
//   row 3   "ข้อมูลถึงวันไหน": one chip per feed, because some feeds are days behind
// Data: /api/home for everything but the charts, then /api/home/trends after the first paint (both in app/home.py).
// Everything is built with el(..., { text }) / createTextNode, so words typed by people (ticket titles, repair problems)
// can never become HTML. Charts are plain SVG in the style of elec.js and share its helpers (sv, elTip, elNiceStep, ELEC_TH).
// Years are always Gregorian here (elec.js's elMon writes a Buddhist year, so it is not used).
// Loaded BEFORE app.js: nothing here runs at load time; helpers from app.js / fleet.js / elec.js are used at call time.

const home = { data: null, trends: null, loading: false, error: "", trendsError: "", seq: 0, chartW: 0 };

// A number over its label. The พนักงาน pages use it too (documents.js, employees.js, loans.js), so it stays here.
const hStat = (value, label, tone = "") => el("div", { class: `hstat ${tone}`.trim() }, el("div", { class: "hstat-v", text: String(value) }), el("div", { class: "hstat-l", text: label }));

// ------------------------------------------------------------------ words and numbers
const HM_DAYS = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์"];
const HM_SERIES = ["var(--el-off)", "var(--el-peak)", "var(--el-hol)", "var(--el-dem)"];      // the electricity page's chart colours, so charts agree
const hmIsNum = (v) => (typeof v === "number" || (typeof v === "string" && v.trim() !== "")) && Number.isFinite(Number(v));
// 118907 -> "118,907" ; -10435 -> "−10,435" (a real minus sign) ; nothing -> "–"
function hmNum(v, digits = 0) {
  if (!hmIsNum(v)) return "–";
  const s = Math.abs(Number(v)).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
  return (Number(v) < 0 && /[1-9]/.test(s) ? "−" : "") + s;
}
const hmSigned = (v) => (hmIsNum(v) && Number(v) > 0 ? "+" : "") + hmNum(v);
// axis ticks: 1500000 -> "1.5M", -500000 -> "−500k"
function hmShort(v) {
  const a = Math.abs(v), sign = v < 0 ? "−" : "";
  if (a >= 1e6) return `${sign}${hmNum(a / 1e6, 2)}M`;
  if (a >= 1e3) return `${sign}${hmNum(a / 1e3, a >= 1e4 ? 0 : 1)}k`;
  return `${sign}${hmNum(a, 1)}`;
}
const hmParts = (iso) => /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(String(iso || ""));
const hmDMY = (iso) => { const p = hmParts(iso); return p && p[3] ? `${p[3]}/${p[2]}/${p[1]}` : String(iso || ""); };      // 2026-11-21 -> 21/11/2026
const hmDM = (iso) => { const p = hmParts(iso); return p && p[3] ? `${p[3]}/${p[2]}` : String(iso || ""); };                // 2026-11-21 -> 21/11
const hmUtc = (iso) => { const p = hmParts(iso); return p && p[3] ? Date.UTC(+p[1], +p[2] - 1, +p[3]) : NaN; };
const hmDayName = (iso) => { const t = hmUtc(iso); return Number.isNaN(t) ? "" : HM_DAYS[new Date(t).getUTCDay()]; };
const hmDayFull = (iso) => `${hmDayName(iso)} ${hmDMY(iso)}`.trim();                                                         // เสาร์ 21/11/2026
const hmMonth = (ym) => { const p = hmParts(ym); return p ? `${p[2]}/${p[1]}` : String(ym || ""); };                         // 2026-09 -> 09/2026, as the server writes a month
// how far behind a feed is, as a chip tone -- the server's limits (app/home.py through_chip): a day is normal, up to a week is late.
// Only used when the server did not send the tone itself.
function hmLagTone(asOf, through) {
  const days = (hmUtc(asOf) - hmUtc(through)) / 864e5;
  return Number.isNaN(days) ? "plain" : days <= 1 ? "ok" : days <= 7 ? "warn" : "bad";
}

// ------------------------------------------------------------------ small pieces
const hmTone = (tone, allowed) => (allowed.includes(tone) ? tone : "");
const hmChip = (text, tone = "plain", strong = "") => el("span", { class: `hm-chip ${hmTone(tone, ["ok", "warn", "bad"]) || "plain"}` }, text, strong ? " " : null, strong ? el("b", { text: strong }) : null);
const hmNone = (text = "ยังไม่มีข้อมูล") => el("div", { class: "hm-none", text });
// Only the app's own "#page" addresses become links; anything else from the server is drawn as plain text.
const hmIsHash = (s) => typeof s === "string" && /^#\S+$/.test(s);
function hmLinked(link, cls, ...kids) {
  if (!hmIsHash(link)) return el("div", { class: cls }, ...kids);
  return el("a", { class: cls, href: link, onclick: (e) => {
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;       // a new tab opens the app on that page by itself
    e.preventDefault();
    goHash(link);
  } }, ...kids);
}

// ------------------------------------------------------------------ row 1, left: ต้องดูวันนี้
const HM_LEVEL_WORD = { bad: "ด่วน", warn: "ตามงาน", info: "แจ้งให้ทราบ" };

function hmTodoRow(it) {
  const level = hmTone(it.level, ["bad", "warn"]) || "info";
  return el("li", {}, hmLinked(it.link, `hm-it ${level}`,
    el("span", { class: "hm-bar", "aria-hidden": "true" }),
    el("span", { class: "hm-it-main" },
      el("span", { class: "hm-sr", text: `${HM_LEVEL_WORD[level]}: ` }),
      el("b", { text: it.title || "" }),
      it.who ? el("span", { class: "hm-who", text: ` · ${it.who}` }) : null,
      it.detail ? el("span", { class: "hm-detail", text: it.detail }) : null),
    it.link_label ? el("span", { class: "hm-go", text: it.link_label }) : null));
}

function hmTodoCard(todo, failed) {
  const items = (todo && Array.isArray(todo.items) ? todo.items : []).filter(Boolean);
  const count = (level) => (todo && hmIsNum(todo[level]) ? Number(todo[level]) : items.filter((x) => x.level === level).length);
  const bad = count("bad"), warn = count("warn");
  const broken = failed.filter((name) => /^todo/.test(name));                       // part of THIS list did not load: never say "nothing pending" then
  let body;
  if (items.length) body = el("ul", { class: "hm-todo" }, ...items.map(hmTodoRow));
  else if (!todo || broken.length) body = hmNone(todo ? "โหลดรายการไม่ได้ กดรีเฟรชเพื่อลองอีกครั้ง" : "ยังไม่มีข้อมูล");
  else body = el("p", { class: "hm-calm", text: "วันนี้ไม่มีเรื่องค้าง" });
  return el("section", { class: "card hm-card", "aria-label": "ต้องดูวันนี้" },
    el("div", { class: "hm-row" }, el("h3", { text: "ต้องดูวันนี้" }),
      hmChip(`ด่วน ${hmNum(bad)}`, bad ? "bad" : "plain"), hmChip(`ตามงาน ${hmNum(warn)}`, warn ? "warn" : "plain"),
      el("span", { class: "grow" }), el("span", { class: "hm-sub", text: "เรียงตามความเร่งด่วน ไม่ใช่ตามเมนู" })),
    body,
    items.length && broken.length ? el("p", { class: "hm-part", text: "รายการนี้ยังไม่ครบ บางส่วนโหลดไม่ได้ กดรีเฟรชเพื่อลองอีกครั้ง" }) : null);
}

// ------------------------------------------------------------------ row 1, right: tiles + profit
function hmSpark(values) {
  const v = (Array.isArray(values) ? values : []).filter(hmIsNum).map(Number);
  if (v.length < 2) return null;
  const W = 150, H = 30, max = Math.max(...v), min = Math.min(...v);
  const y = (n) => 3 + ((max - n) / ((max - min) || 1)) * (H - 6), x = (i) => 2 + (i * (W - 4)) / (v.length - 1);
  return sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "hm-spark", preserveAspectRatio: "none", "aria-hidden": "true" },
    sv("polyline", { points: v.map((n, i) => `${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(" "), "vector-effect": "non-scaling-stroke" }));
}

// One tile, whatever its key: label + the date chip, the big number with its unit, the change + a note, a sparkline.
function hmTile(t) {
  const label = el("span", { text: t.label || t.key || "" });
  if (t.ready === false) return el("div", { class: "hm-tile off" }, el("div", { class: "hm-k" }, label), hmNone());
  const tone = hmTone(t.tone, ["warn", "bad"]);
  const value = typeof t.value === "string" ? t.value : hmNum(t.value, t.unit === "฿" ? 0 : 2);            // baht never shows decimals
  const after = [t.unit, t.suffix].filter(Boolean).join(" ");
  const delta = t.delta && t.delta.text ? el("span", { class: `hm-delta ${hmTone(t.delta.tone, ["good", "bad"]) || "flat"}`, text: t.delta.text }) : null;
  return hmLinked(t.link, `hm-tile ${tone}`.trim(),
    el("div", { class: "hm-k" }, label, t.chip && t.chip.text ? hmChip(t.chip.text, t.chip.tone) : null),
    el("div", { class: "hm-v" }, value, after ? " " : null, after ? el("small", { text: after }) : null),
    delta || t.note ? el("div", { class: "hm-d" }, delta, delta && t.note ? " " : null, t.note || null) : null,
    hmSpark(t.spark));
}

function hmProfitCard(p) {
  const head = (chip) => el("div", { class: "hm-row" }, el("h3", { text: "กำไรเดือนล่าสุดที่ปิดแล้ว" }), chip, el("span", { class: "grow" }),
    p && hmIsHash(p.link) ? hmLinked(p.link, "hm-link", "งบกำไรขาดทุน") : null);
  if (!p || p.ready === false) return el("section", { class: "card hm-card" }, head(null), hmNone());
  const num = (label, text, cls = "") => el("div", {}, el("div", { class: "hm-sub", text: label }), el("div", { class: `hm-pn ${cls}`.trim(), text }));
  return el("section", { class: "card hm-card" },
    head(p.month ? hmChip(hmMonth(p.month)) : null),
    el("div", { class: "hm-profit" },
      num("รวม", `${hmNum(p.total)} ฿`),
      ...(Array.isArray(p.branches) ? p.branches : []).map((b) => num(b.name || "", hmSigned(b.value), !hmIsNum(b.value) || !Number(b.value) ? "" : Number(b.value) > 0 ? "up" : "down"))),
    p.note ? el("div", { class: "hm-sub hm-note", text: p.note }) : null);
}

// ------------------------------------------------------------------ row 2: the two charts
// The width a chart is drawn at = the width of the box it sits in (not of the window: the menu takes 252 px of it), so the SVG is
// never scaled down and its 11 px labels stay 11 px. Before the page is on screen there is no box to measure: a middle size.
function hmChartW() {
  const box = $("homeCharts"), w = box ? box.clientWidth : 0;
  if (!w) return window.innerWidth < 700 ? 360 : 520;
  const cols = Math.max(1, getComputedStyle(box).gridTemplateColumns.split(" ").filter(Boolean).length);
  const each = (w - 12 * (cols - 1)) / cols - (window.innerWidth <= 560 ? 24 : 32);    // less the gap between the cards and a card's padding
  return Math.round(Math.min(900, Math.max(300, each)) / 20) * 20;
}

// the frame both charts share: horizontal grid lines with their ticks; the zero line (when the axis has one) is the strong one
function hmFrame(W, H, P, lo, hi, step, label) {
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "el-svg", role: "img", "aria-label": label });
  const y = (v) => P.t + (H - P.t - P.b) * (1 - (v - lo) / (hi - lo));
  const n = Math.round((hi - lo) / step);
  for (let i = 0; i <= n; i++) {
    const v = lo + i * step, yy = y(v);
    svg.append(sv("line", { x1: P.l, x2: W - P.r, y1: yy, y2: yy, class: Math.abs(v) < step / 1e6 ? "el-base" : "el-gl" }),
      sv("text", { x: P.l - 6, y: yy + 4, "text-anchor": "end", class: "el-tick" }, document.createTextNode(hmShort(v))));
  }
  return { svg, y };
}

// Route sales per day: one 2px line. The axis starts above zero when every day sits well above it (the card's subtitle says where),
// so day-to-day movement is readable; the last point carries its value, every day has a tooltip.
function hmLineChart(sd) {
  const days = sd.days || [], vals = days.map((_, i) => (hmIsNum((sd.values || [])[i]) ? Number(sd.values[i]) : null));
  const nums = vals.filter((v) => v != null);
  const max = Math.max(...nums), min = Math.min(...nums);
  let lo = 0, step = elNiceStep(Math.max(max - min, Math.abs(max) * 0.05, 1) / 3);
  if (min > 0 && min >= max * 0.5) { lo = Math.floor(min / step) * step; if (lo === min) lo -= step; }
  if (lo <= 0) { lo = Math.min(0, Math.floor(min / step) * step); if (min >= 0) step = elNiceStep((max || 1) / 4); }
  let hi = Math.ceil(max / step) * step;
  if (hi <= lo) hi = lo + step;
  const W = hmChartW(), H = W < 500 ? 180 : 200, P = { l: 46, r: 18, t: 18, b: 24 };
  const { svg, y } = hmFrame(W, H, P, lo, hi, step, `ยอดขายรถสายรายวัน ${hmDMY(days[0])} ถึง ${hmDMY(days[days.length - 1])} ล่าสุด ${hmNum(nums[nums.length - 1])} บาท`);
  const inner = W - P.l - P.r, n = days.length, slot = n > 1 ? inner / (n - 1) : inner;
  const x = (i) => (n > 1 ? P.l + slot * i : P.l + inner / 2);
  let run = [];                                                                        // a day without a number breaks the line instead of drawing a false zero
  const flush = () => { if (run.length > 1) svg.append(sv("polyline", { points: run.join(" "), class: "hm-line" })); run = []; };
  vals.forEach((v, i) => { if (v == null) flush(); else run.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`); });
  flush();
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(inner / 46))));
  let last = -1;
  vals.forEach((v, i) => { if (v != null) last = i; });
  days.forEach((day, i) => {
    if ((n - 1 - i) % every === 0) svg.append(sv("text", { x: x(i), y: H - 6, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(hmDM(day))));
    if (vals[i] == null) return;
    const g = sv("g", { class: "el-hit hm-hit" },
      sv("line", { x1: x(i), x2: x(i), y1: P.t, y2: H - P.b, class: "hm-guide" }),
      sv("circle", { cx: x(i), cy: y(vals[i]), r: 4, class: i === last ? "hm-dot on" : "hm-dot" }),
      sv("rect", { x: x(i) - slot / 2, y: 0, width: slot, height: H - P.b, fill: "transparent" }));
    elTip(g, () => [hmDayFull(day), ["ยอดขายรถสาย", `${hmNum(vals[i])} ฿`, HM_SERIES[0]]]);
    svg.append(g);
  });
  if (last >= 0) svg.append(sv("text", { x: x(last) - 7, y: y(vals[last]) - 9, "text-anchor": "end", class: "el-val" }, document.createTextNode(hmNum(vals[last]))));
  return { svg, lo };
}

// a bar from the zero line y0 to its value y1, rounded only at the data end
function hmBarPath(x, y0, y1, w) {
  const h = Math.abs(y1 - y0), r = Math.min(4, w / 2, h), s = y1 < y0 ? 1 : -1;       // s = 1: the bar goes up
  if (h < 0.5) return "";
  return `M${x},${y0}V${y1 + s * r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + s * r}V${y0}Z`;
}

// Net profit per month, one bar per branch side by side, anchored to the zero line (a loss hangs below it).
// A month whose books are not complete is drawn pale and says so in its tooltip.
function hmBarChart(pb) {
  const months = pb.months || [], series = (pb.series || []).filter((s) => s && Array.isArray(s.values));
  const pale = new Set(pb.incomplete || []);
  const all = series.flatMap((s) => months.map((_, i) => s.values[i]).filter(hmIsNum).map(Number));
  const max = Math.max(0, ...all), min = Math.min(0, ...all);
  const step = elNiceStep(((max - min) || 1) / 4);
  const hi = Math.ceil(max / step) * step || step, lo = Math.floor(min / step) * step;
  const W = hmChartW(), H = W < 500 ? 180 : 200, P = { l: 46, r: 8, t: 12, b: 24 };
  const { svg, y } = hmFrame(W, H, P, lo, hi, step, `กำไรสุทธิรายเดือน แยกสาขา ${hmMonth(months[0])} ถึง ${hmMonth(months[months.length - 1])}`);
  const slot = (W - P.l - P.r) / months.length, k = Math.max(1, series.length);
  const bw = Math.max(3, Math.min(22, (slot - 6 - (k - 1) * 2) / k));
  const every = slot < 26 ? 2 : 1;
  const years = new Set(months.map((m) => String(m).slice(0, 4))).size;                // the axis shows the short month; the year (Gregorian) only where it changes
  months.forEach((m, i) => {
    const x0 = P.l + slot * i, left = x0 + (slot - (bw * k + (k - 1) * 2)) / 2;
    const g = sv("g", { class: "el-hit hm-hit" });
    series.forEach((s, j) => {
      if (!hmIsNum(s.values[i])) return;
      const d = hmBarPath(left + j * (bw + 2), y(0), y(Number(s.values[i])), bw);
      if (d) g.append(sv("path", { d, fill: HM_SERIES[j % HM_SERIES.length], class: pale.has(i) ? "hm-pale" : "hm-bar-mark" }));
    });
    g.append(sv("rect", { x: x0, y: P.t, width: slot, height: H - P.t - P.b, fill: "transparent" }));
    elTip(g, () => {
      const rows = series.map((s, j) => (hmIsNum(s.values[i]) ? [s.name || "", `${hmSigned(s.values[i])} ฿`, HM_SERIES[j % HM_SERIES.length]] : null)).filter(Boolean);
      if (rows.length > 1) rows.push(["รวม", `${hmSigned(series.reduce((a, s) => a + (hmIsNum(s.values[i]) ? Number(s.values[i]) : 0), 0))} ฿`]);
      return [`${hmMonth(m)}${pale.has(i) ? " · ยังไม่ครบ" : ""}`, ...rows];
    });
    svg.append(g);
    if (i % every === 0) {
      const p = hmParts(m), newYear = years > 1 && slot >= 48 && (i === 0 || (p && p[2] === "01"));
      const text = p && ELEC_TH[+p[2] - 1] ? ELEC_TH[+p[2] - 1] + (newYear ? ` ${p[1]}` : "") : String(m);
      svg.append(sv("text", { x: x0 + slot / 2, y: H - 6, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(text)));
    }
  });
  return svg;
}

function hmChartCard(title, sub, chip, body, legend = null) {
  return el("section", { class: "card hm-card" },
    el("div", { class: "hm-row" }, el("h3", { text: title }), sub ? el("span", { class: "hm-sub", text: sub }) : null, el("span", { class: "grow" }), chip),
    el("div", { class: "hm-chart" }, body), legend);
}

function hmSalesCard(sd, wait) {
  const title = "ยอดขายรถสายรายวัน";
  if (wait) return hmChartCard(title, "", null, wait);
  const ok = sd && sd.ready !== false && Array.isArray(sd.days) && sd.days.length && (sd.values || []).some(hmIsNum);
  if (!ok) return hmChartCard(title, "", null, hmNone());
  const chart = hmLineChart(sd);
  const through = sd.through || sd.days[sd.days.length - 1];
  const sub = ["บาท", sd.note, chart.lo > 0 ? `แกนเริ่มที่ ${hmNum(chart.lo)}` : ""].filter(Boolean).join(" · ");
  const tone = ["ok", "warn", "bad"].includes(sd.through_tone) ? sd.through_tone : hmLagTone(home.data && home.data.as_of, through);
  return hmChartCard(title, sub, hmChip(`ลงถึง ${hmDMY(through)}`, tone), chart.svg);
}

function hmProfitChartCard(pb, wait) {
  const title = "กำไรสุทธิรายเดือน แยกสาขา";
  if (wait) return hmChartCard(title, "", null, wait);
  const ok = pb && pb.ready !== false && Array.isArray(pb.months) && pb.months.length && (pb.series || []).some((s) => s && (s.values || []).some(hmIsNum));
  if (!ok) return hmChartCard(title, "", null, hmNone());
  const years = [...new Set(pb.months.map((m) => String(m).slice(0, 4)))];
  const legend = el("div", { class: "el-legend" },
    ...pb.series.filter((s) => s && Array.isArray(s.values)).map((s, j) => el("span", {}, el("i", { style: `background:${HM_SERIES[j % HM_SERIES.length]}` }), s.name || "")),
    (pb.incomplete || []).length ? el("span", {}, el("i", { class: "hm-pale", style: `background:${HM_SERIES[0]}` }), "ยังไม่ครบ") : null);
  return hmChartCard(title, `บาท · ${years.length > 1 ? `${years[0]}–${years[years.length - 1]}` : years[0]}`, null, hmBarChart(pb), legend);
}

function hmRenderCharts() {
  const box = $("homeCharts");
  if (!box) return;
  home.chartW = hmChartW();
  const t = home.trends;
  const lost = () => el("div", { class: "hm-wait", text: "โหลดกราฟไม่สำเร็จ กดรีเฟรชเพื่อลองอีกครั้ง" });
  const failed = t && Array.isArray(t.failed) ? t.failed.map(String) : [];             // a chart the server could not build: an error, not "no data"
  const instead = (name) => (!t ? (home.trendsError ? lost() : el("div", { class: "hm-wait", text: "กำลังโหลดกราฟ…" })) : failed.includes(name) ? lost() : null);
  fill(box, hmSalesCard(t && t.sales_daily, instead("sales_daily")), hmProfitChartCard(t && t.profit_branch, instead("profit_branch")));
}

// ------------------------------------------------------------------ the page
// the server names a part that did not load by its key ("todo.machines"); the owner reads what it is
const HM_PART = {
  "todo.repair_danger": "งานซ่อมด่วน", "todo.at_shop_long": "รถอยู่อู่นาน", "todo.awaiting_bill": "งานซ่อมรอบิล", "todo.open_long": "แจ้งซ่อมค้างนาน",
  "todo.licence_expired": "เอกสารหมดอายุ", "todo.licence_soon": "เอกสารใกล้หมดอายุ", "todo.oil_due": "รอบถ่ายน้ำมันเครื่อง", "todo.my_overdue": "บอร์ดของฉัน",
  "todo.sales_sheets": "ใบขายรถสาย", "todo.machines": "เครื่องจักร", "todo.stale_data": "ข้อมูลที่ยังไม่มา", "todo.next7": "7 วันข้างหน้า",
  "tiles.route_sales": "ยอดขายรถสาย", "tiles.kk_invoice": "ใบกำกับภาษีแก่งคอย", "tiles.phone_sales": "ขายวันนี้ (มือถือ)", "tiles.at_shop": "รถอยู่ที่อู่",
  "tiles.repair_mtd": "ค่าซ่อมรถ", "tiles.payables": "ค้างจ่ายร้านค้า", profit: "กำไร", fresh: "ข้อมูลถึงวันไหน", "fresh.feeds": "วันที่ของข้อมูล",
  "fresh.meters_sbr": "มิเตอร์ไฟ สระบุรี", "fresh.meters_kk": "มิเตอร์ไฟ แก่งคอย", "fresh.program_sbr": "โปรแกรมขายหน้าโรง สระบุรี",
  "fresh.program_kk": "โปรแกรมขายหน้าโรง แก่งคอย", "fresh.elec_bills": "ใบแจ้งค่าไฟ", "fresh.payroll": "เงินเดือน",
};
const hmPartName = (key) => HM_PART[key] || "รายการอื่น";

function renderHome() {
  if (!$("homeTop")) return;                                                           // the page was replaced (a person with no menus)
  const d = home.data;
  $("homeAsOf").textContent = d ? [hmDayFull(d.as_of), d.time ? `อัปเดต ${d.time}` : ""].filter(Boolean).join(" · ") : "";
  showErr("homeError", home.error);
  if (!d) { fill($("homeTop")); return; }
  const failed = (Array.isArray(d.failed) ? d.failed : []).map(String);
  const tiles = (Array.isArray(d.tiles) ? d.tiles : []).filter(Boolean);
  fill($("homeTop"), hmTodoCard(d.todo, failed),
    el("div", { class: "hm-side" }, tiles.length ? el("div", { class: "hm-tiles" }, ...tiles.map(hmTile)) : null, hmProfitCard(d.profit)));
  hmRenderCharts();
  const fresh = (Array.isArray(d.fresh) ? d.fresh : []).filter(Boolean);
  $("homeFresh").hidden = !fresh.length;
  fill($("homeFresh"), el("b", { text: "ข้อมูลถึงวันไหน" }), ...fresh.map((f) => hmChip(f.label || "", f.tone, f.value == null ? "" : String(f.value))));
  $("homeFailed").hidden = !failed.length;
  $("homeFailed").textContent = failed.length ? `โหลดไม่ได้บางส่วน: ${[...new Set(failed.map(hmPartName))].join(", ")} · กดรีเฟรชเพื่อลองอีกครั้ง` : "";
}

// the two charts: asked for after the page has painted, and only the newest request may draw
async function loadHomeTrends(fresh, seq) {
  await new Promise((done) => { if (document.hidden) setTimeout(done, 0); else requestAnimationFrame(() => setTimeout(done, 0)); });
  try {
    const t = await api(`/api/home/trends${fresh ? "?fresh=true" : ""}`);
    if (seq !== home.seq) return;
    home.trends = t; home.trendsError = "";
  } catch (err) {
    if (seq !== home.seq) return;
    home.trendsError = err.message;                                                    // keep showing the last charts, if any
  }
  hmRenderCharts();
}

async function loadHome(fresh = false) {
  if (home.loading || !$("homeRefresh")) return;
  home.loading = true;
  $("homeRefresh").disabled = true;
  let ok = false;
  try {
    home.data = await api(`/api/home${fresh ? "?fresh=true" : ""}`);
    home.error = ""; home.trendsError = ""; ok = true;                                 // the charts are about to be asked for again: an old failure is not shown meanwhile
  } catch (err) {
    home.error = err.message;                  // keep showing the last numbers, if any, under the message
  } finally {
    home.loading = false;
    $("homeRefresh").disabled = false;
  }
  renderHome();
  // A new number only once the page itself has loaded: when this load failed, the charts the load BEFORE it asked for may still draw
  // (otherwise they are thrown away and the cards wait for good).
  if (ok) loadHomeTrends(fresh, ++home.seq);
}

function initHome() {
  $("homeRefresh").addEventListener("click", () => loadHome(true));
  $("navHome").addEventListener("click", () => { showView("home"); loadHome(); });
  // the chart box changed width (the window was resized, a phone was turned, the menu was folded): draw the charts again at the new size
  const resized = () => { if (home.trends && !$("homeView").hidden && home.chartW !== hmChartW()) hmRenderCharts(); };
  if (window.ResizeObserver) new ResizeObserver(resized).observe($("homeCharts"));
  else window.addEventListener("resize", resized);
}
