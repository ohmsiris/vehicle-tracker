"use strict";

// รถสาย · ใบขาย (#sales-sheets) -- the office side of the drivers' phone sheet (app/route_sales.py; the drivers use /d/).
// Redesigned with the owner, 11 Nov 2026:
//   #sales-sheets              one block per driver: today's sales and what needs a look
//   #sales-sheets/<route>      that driver: ใบขายรายวัน (‹ date ›, the numbers on top, every customer's line -- edit and save
//                              straight away, no "ask the driver to fix it"), รายชื่อลูกค้า (add / edit / take off the route),
//                              คนขับ / รหัส (PIN, phones). Print the paper sheet (ใบงานคนขับ) from the day.
//   #sales-sheets/<route>/<YYYY-MM-DD> | /customers | /driver

const ss_ = { routes: null, today: null, routeId: null, day: null, tab: "day", data: null, doc: null, returns: null, dirty: false };
const SS_ST = { open: ["กำลังขาย", ""], closed: ["ส่งแล้ว รอตรวจ", "warn"], checked: ["ตรวจแล้ว", "ok"] };
const ssN = (v) => { const x = parseFloat(String(v == null ? "" : v).replace(/,/g, "")); return Number.isFinite(x) ? x : 0; };
const ssDmy = (iso) => (iso ? iso.split("-").reverse().join("/") : "");
const ssAddDay = (iso, n) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const ssWeekday = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("th-TH", { weekday: "long", timeZone: "UTC" });

function ssLink() { return `${location.origin}/d/`; }

async function openSalesSheets(sub = "") {
  if (ss_.dirty && !confirm("ยังไม่ได้บันทึกการแก้ไข — ออกจากหน้านี้เลยไหม?")) { history.back(); return; }
  ss_.dirty = false;
  showView("salesSheets");
  showErr("ssError", "");
  const [rid, part] = sub.split("/");
  if (!rid) { ss_.routeId = null; return ssOverview(); }
  const sameRoute = ss_.routeId === Number(rid);
  ss_.routeId = Number(rid);
  ss_.tab = part === "customers" ? "customers" : part === "driver" ? "driver" : "day";
  // a date in the link wins; switching tabs keeps the day; opening a driver starts on today
  ss_.day = /^\d{4}-\d{2}-\d{2}$/.test(part || "") ? part : sameRoute && part ? ss_.day : null;
  await ssLoadDay();
}

// ------------------------------------------------------------------ every driver, one block each
async function ssOverview() {
  fill($("ssBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { const r = await api("/api/sales-routes"); ss_.routes = r.routes; ss_.today = r.today; }
  catch (err) { fill($("ssBody"), null); showErr("ssError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  const link = el("div", { class: "card ss-link" },
    el("strong", { text: "ลิงก์สำหรับคนขับ (ลิงก์เดียวทุกคน)" }),
    el("div", { class: "ss-linkrow" }, el("code", { text: ssLink() }),
      el("button", { class: "btn small", type: "button", text: "คัดลอก", onclick: async (e) => {
        try { await navigator.clipboard.writeText(ssLink()); e.target.textContent = "คัดลอกแล้ว ✓"; } catch (x) { window.prompt("คัดลอกลิงก์", ssLink()); }
      } })));
  const blocks = ss_.routes.map((r) => {
    const last = r.last_day, t = (last && last.totals) || {};
    const isToday = last && last.day === ss_.today;
    return el("button", { class: "card ss-block", type: "button", onclick: () => goHash(`#sales-sheets/${r.id}`) },
      el("div", { class: "ss-block-top" },
        el("b", { class: "ss-block-no", text: r.truck_no.replace("KK-", "แก่งคอย ") }),
        el("span", { class: "ss-block-who" }, el("strong", { text: r.driver_name || "—" }), el("span", { class: "muted small", text: r.name || "" }))),
      el("div", { class: "ss-block-num" },
        el("b", { text: isToday ? `฿${elN(t.amount || 0)}` : "—" }),
        el("span", { class: "muted small", text: isToday ? `วันนี้ · ${t.customers || 0}/${r.customers} ร้าน` : last ? `ล่าสุด ${ssDmy(last.day)} ฿${elN(t.amount || 0)}` : `ลูกค้า ${r.customers} ร้าน` })),
      el("div", { class: "ss-block-badges" },
        r.walkins ? el("span", { class: "badge bad", text: `เพิ่มลูกค้าเอง ${r.walkins}` }) : null,
        r.unsent ? el("span", { class: "badge bad", text: `ไม่ได้ส่ง ${r.unsent} วัน` }) : null,
        r.reopen_requests ? el("span", { class: "badge warn", text: `ขอแก้ ${r.reopen_requests}` }) : null,
        (t.flags || []).length && isToday ? el("span", { class: "badge warn", text: `⚠ ${t.flags.length}` }) : null,
        !r.pin_set ? el("span", { class: "badge", text: "ยังไม่ได้ตั้งรหัส" }) : null));
  });
  fill($("ssBody"), link, el("div", { class: "ss-grid" }, ...blocks));
}

// ------------------------------------------------------------------ one driver
async function ssLoadDay() {
  fill($("ssBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  let d;
  try { d = await api(`/api/sales-routes/${ss_.routeId}/office-day${ss_.day ? `?day=${ss_.day}` : ""}`); }
  catch (err) { fill($("ssBody"), null); showErr("ssError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  ss_.data = d; ss_.day = d.day; ss_.today = d.today;
  const sheet = d.sheet;
  ss_.doc = JSON.parse(JSON.stringify((sheet && sheet.doc) || { sales: {}, loads: [], new_customers: [], cash: null, km_end: null }));
  if (!ss_.doc.sales) ss_.doc.sales = {};
  if (!ss_.doc.loads || !ss_.doc.loads.length) ss_.doc.loads = [{ q: {} }];
  ss_.returns = sheet && sheet.returns_foh ? { ...sheet.returns_foh } : null;
  ss_.dirty = false;
  if (ss_.tab === "driver" && !ss_.routes) { try { const r = await api("/api/sales-routes"); ss_.routes = r.routes; } catch (e) { /* the tab says so */ } }
  ssRenderDriver();
}

function ssRenderDriver() {
  const d = ss_.data, r = d.route;
  const tab = (key, label) => el("button", { type: "button", class: `segbtn${ss_.tab === key ? " active" : ""}`, text: label,
    onclick: () => goHash(`#sales-sheets/${r.id}${key === "day" ? `/${ss_.day}` : `/${key}`}`) });
  const head = el("div", { class: "ss-dhead" },
    el("a", { href: "#sales-sheets", class: "ss-back", text: "← ทุกสาย" }),
    el("div", { class: "ss-dtitle" }, el("b", { class: "ss-block-no", text: r.truck_no }),
      el("span", {}, el("strong", { text: r.driver_name || "—" }), el("span", { class: "muted small", text: ` · ${r.name || ""}${r.driver_phone ? ` · ${r.driver_phone}` : ""}` }))),
    el("div", { class: "seg ss-tabs" }, tab("day", "ใบขายรายวัน"), tab("customers", `รายชื่อลูกค้า (${d.customers.length})`), tab("driver", "คนขับ / รหัส")));
  const body = ss_.tab === "customers" ? ssCustomersTab() : ss_.tab === "driver" ? ssDriverTab() : ssDayTab();
  fill($("ssBody"), head, body);
}

// ---- ใบขายรายวัน
function ssProducts() {
  const d = ss_.data, used = new Set();
  d.customers.forEach((c) => Object.keys(c.prices || {}).forEach((k) => used.add(k)));
  Object.values(ss_.doc.sales).forEach((s) => Object.keys(s.q || {}).forEach((k) => used.add(k)));
  (ss_.doc.loads || []).forEach((ld) => Object.keys(ld.q || {}).forEach((k) => used.add(k)));
  return d.products.filter((p) => used.has(p.key));
}

function ssCustomers() {        // the route list + the shops the driver added on this day
  const walk = (ss_.doc.new_customers || []).map((w, i) => ({ id: w.key, seq: null, name: w.name, prices: w.prices || {}, billing: "cash", walkin: w, i }));
  return ss_.data.customers.concat(walk);
}

function ssLine(c) {             // one customer's line, worked out like the server does (app/route_sales.compute)
  const s = ss_.doc.sales[String(c.id)] || {};
  const q = s.q || {};
  const free = !!(c.free || s.free);
  const full = c.free ? 0 : Object.entries(q).reduce((a, [k, v]) => a + ssN(v) * ssN((c.prices || {})[k]), 0);
  const gross = free ? 0 : full;
  const amount = Math.max(gross - Math.min(Math.max(ssN(s.discount), 0), gross), 0);
  const invoice = c.billing === "invoice";
  const paid = invoice || free ? 0 : c.walkin ? amount : Math.min(Math.max(s.paid == null || s.paid === "" ? amount : ssN(s.paid), 0), amount);
  const old = Math.max(ssN(s.old_paid), 0);
  const before = ssN(ss_.data.owed_before[String(c.id)]);
  return { s, q, free, amount, invoice, paid, old, before, after: invoice ? before : before + amount - paid - old,
    sold: Object.values(q).some((v) => ssN(v)) || old > 0 };
}

function ssTotals() {
  const P = ss_.data.products.map((p) => p.key);
  const t = { amount: 0, cash: 0, credit: 0, invoiced: 0, old: 0, shops: 0, sold: {}, loaded: {} };
  P.forEach((k) => { t.sold[k] = 0; t.loaded[k] = 0; });
  ssCustomers().forEach((c) => {
    const ln = ssLine(c);
    if (!ln.sold) return;
    t.shops += 1; t.amount += ln.amount; t.old += ln.old;
    if (ln.invoice) t.invoiced += ln.amount; else { t.cash += ln.paid; t.credit += ln.amount - ln.paid; }
    P.forEach((k) => { t.sold[k] += ssN(ln.q[k]); });
  });
  (ss_.doc.loads || []).forEach((ld) => P.forEach((k) => { t.loaded[k] += ssN((ld.q || {})[k]); }));
  const cash = ss_.doc.cash || {};
  t.expected = t.cash + t.old;
  t.counted = ss_.data.notes.reduce((a, n) => a + ssN(cash[String(n)]) * n, 0) + ssN(cash.coins);
  t.hasCash = Object.values(cash).some((v) => ssN(v));
  t.km = ssN(ss_.doc.km_end) && ssN(ss_.data.km_start) ? ssN(ss_.doc.km_end) - ssN(ss_.data.km_start) : null;
  return t;
}

function ssChanged() {
  ss_.dirty = true;
  const b = $("ssSave"); if (b) { b.disabled = false; b.textContent = "บันทึก"; }
  ssPaintNumbers();
}

function ssNumIn(value, onChange, attrs = {}) {
  return el("input", { class: "ss-in", type: "text", inputmode: "decimal", value: value == null || value === "" || value === 0 ? "" : String(value),
    oninput: (e) => { onChange(e.target.value.trim()); ssChanged(); }, ...attrs });
}

function ssSetQ(c, k, v) {
  const key = String(c.id);
  const s = ss_.doc.sales[key] || (ss_.doc.sales[key] = { q: {} });
  if (!s.q) s.q = {};
  if (v === "" || !ssN(v)) delete s.q[k]; else s.q[k] = ssN(v);
  if (!s.t) s.t = "";
}

function ssDayTab() {
  const d = ss_.data, sheet = d.sheet, P = ssProducts();
  const go = (n) => { if (ss_.dirty && !confirm("ยังไม่ได้บันทึก — ไปวันอื่นเลยไหม?")) return; ss_.dirty = false; goHash(`#sales-sheets/${d.route.id}/${ssAddDay(ss_.day, n)}`); };
  const pick = el("input", { type: "date", value: ss_.day, "aria-label": "เลือกวัน",
    onchange: (e) => { if (e.target.value && e.target.value !== ss_.day) { if (ss_.dirty && !confirm("ยังไม่ได้บันทึก — ไปวันอื่นเลยไหม?")) return; ss_.dirty = false; goHash(`#sales-sheets/${d.route.id}/${e.target.value}`); } } });
  const has = new Set(d.days_with_sheet);
  const prevDay = [...has].filter((x) => x < ss_.day).pop(), nextDay = [...has].filter((x) => x > ss_.day)[0];
  const st = !sheet ? ["ไม่มีใบขาย — กรอกจากใบกระดาษแล้วกดบันทึกได้", ""] : sheet.unsent ? ["คนขับไม่ได้กดส่ง", "bad"] : SS_ST[sheet.status] || [sheet.status, ""];
  const nav = el("div", { class: "ss-daynav card" },
    el("button", { class: "btn ss-arrow", type: "button", text: "‹", "aria-label": "วันก่อน", onclick: () => go(-1) }),
    el("div", { class: "ss-daypick" }, el("b", { class: "ss-daybig", text: ssDmy(ss_.day) }),
      el("span", { class: "muted small", text: `${ssWeekday(ss_.day)}${ss_.day === d.today ? " · วันนี้" : ""}` }), pick),
    el("button", { class: "btn ss-arrow", type: "button", text: "›", "aria-label": "วันถัดไป", onclick: () => go(1) }),
    el("div", { class: "ss-dayjump" },
      prevDay ? el("button", { class: "btn small", type: "button", text: `‹ วันที่มีใบขาย ${ssDmy(prevDay)}`, onclick: () => goHash(`#sales-sheets/${d.route.id}/${prevDay}`) }) : null,
      nextDay ? el("button", { class: "btn small", type: "button", text: `${ssDmy(nextDay)} ›`, onclick: () => goHash(`#sales-sheets/${d.route.id}/${nextDay}`) }) : null,
      ss_.day !== d.today ? el("button", { class: "btn small", type: "button", text: "วันนี้", onclick: () => goHash(`#sales-sheets/${d.route.id}/${d.today}`) }) : null,
      el("span", { class: `badge ${st[1]}`, text: st[0] })));
  const tiles = el("div", { class: "ss-tiles", id: "ssTiles" });
  const warn = [];
  if (d.driver_selling) warn.push(el("p", { class: "warn-text small", text: "คนขับกำลังขายวันนี้อยู่ — ถ้าบันทึกตอนนี้ ตัวเลขของออฟฟิศจะทับในโทรศัพท์ของคนขับ" }));
  if (sheet && (sheet.totals.flags || []).length) warn.push(el("p", { class: "warn-text small", text: `⚠ ${sheet.totals.flags.join(" · ")}` }));
  if (sheet && sheet.reopen_requested_at) warn.push(el("p", { class: "warn-text small" }, `คนขับขอแก้ใบขายนี้ (${new Date(sheet.reopen_requested_at).toLocaleString("th-TH")}) — แก้ที่นี่ได้เลย หรือ `,
    el("button", { class: "btn small", type: "button", text: "เปิดให้คนขับแก้เอง", onclick: () => ssReopen(sheet.id) })));

  // every customer's line
  const head = el("tr", {}, el("th", { text: "#" }), el("th", { text: "ร้าน" }), el("th", { text: "ราคา" }), ...P.map((p) => el("th", { class: "c", text: p.label })),
    el("th", { class: "r", text: "รวมเงิน" }), el("th", { class: "r", text: "เก่ายกมา" }), el("th", { class: "r", text: "จ่ายเก่า" }),
    el("th", { class: "r", text: "รับเงิน" }), el("th", { class: "r", text: "ค้าง" }));
  const rows = ssCustomers().map((c) => {
    const ln = ssLine(c);
    const cells = { amount: el("td", { class: "r b" }), after: el("td", { class: "r" }), paid: el("td", { class: "r" }) };
    const tr = el("tr", { class: `${ln.sold ? "sold" : ""}${c.walkin ? " walk" : ""}` },
      el("td", { class: "muted", text: c.seq ? String(c.seq) : "+" }),
      el("td", {}, c.walkin ? el("span", { class: "badge bad", text: "เพิ่มเอง" }) : null, el("span", { text: ` ${c.name}` }),
        c.bins ? el("div", { class: "ss-bins", text: c.bins }) : null,
        c.walkin && sheet ? el("button", { class: "btn small", type: "button", text: "เพิ่มเข้ารายชื่อสาย", onclick: () => ssWalkin(sheet.id, c) }) : null),
      el("td", { class: "small", text: c.free ? "ฟรี" : c.billing === "invoice" ? `${ssPrice(c)} วางบิล` : ssPrice(c) }),
      ...P.map((p) => el("td", { class: "c" }, ssNumIn(ln.q[p.key], (v) => { ssSetQ(c, p.key, v); ssPaintRow(c, cells); }, { "aria-label": `${c.name} ${p.label}` }))),
      cells.amount,
      el("td", { class: "r muted", text: ln.before ? elN(ln.before) : "" }),
      el("td", { class: "r" }, c.billing === "invoice" || c.walkin ? null : ssNumIn(ln.s.old_paid, (v) => {
        const s = ss_.doc.sales[String(c.id)] || (ss_.doc.sales[String(c.id)] = { q: {} });
        if (v === "") delete s.old_paid; else s.old_paid = ssN(v);
        ssPaintRow(c, cells);
      }, { "aria-label": `${c.name} จ่ายเก่า` })),
      cells.paid, cells.after);
    if (c.billing !== "invoice" && !c.free && !c.walkin) {
      cells.paid.append(ssNumIn(ln.s.paid, (v) => {
        const s = ss_.doc.sales[String(c.id)] || (ss_.doc.sales[String(c.id)] = { q: {} });
        if (v === "") delete s.paid; else s.paid = ssN(v);
        ssPaintRow(c, cells);
      }, { "aria-label": `${c.name} รับเงิน`, title: "ว่าง = จ่ายเต็ม · 0 = ค้างทั้งหมด" }));
    }
    ssPaintRow(c, cells);
    return tr;
  });
  const table = el("div", { class: "ss-tablewrap" }, el("table", { class: "ss-table" }, el("thead", {}, head), el("tbody", {}, ...rows)));

  // รับ / คืน, money counted, mileage
  const loads = el("table", { class: "ss-mini" },
    el("thead", {}, el("tr", {}, el("th", { text: "รับน้ำแข็ง" }), ...P.map((p) => el("th", { class: "c", text: p.label })), el("th", {}))),
    el("tbody", {},
      ...ss_.doc.loads.map((ld, i) => el("tr", {}, el("td", { text: `เที่ยว ${i + 1}${ld.t ? ` · ${ld.t}` : ""}` }),
        ...P.map((p) => el("td", { class: "c" }, ssNumIn((ld.q || {})[p.key], (v) => { ld.q = ld.q || {}; if (v === "" || !ssN(v)) delete ld.q[p.key]; else ld.q[p.key] = ssN(v); }))),
        el("td", {}, ss_.doc.loads.length > 1 ? el("button", { class: "btn small", type: "button", text: "ลบ", onclick: () => { ss_.doc.loads.splice(i, 1); ss_.dirty = true; ssRenderDriver(); } }) : null))),
      el("tr", {}, el("td", { text: "คืน (หน้าโรงนับ)" }), ...P.map((p) => el("td", { class: "c" }, ssNumIn((ss_.returns || {})[p.key], (v) => {
        ss_.returns = ss_.returns || {}; if (v === "") delete ss_.returns[p.key]; else ss_.returns[p.key] = ssN(v); }))), el("td", {})),
      el("tr", { class: "muted", id: "ssLeftRow" })));
  const cash = ss_.doc.cash || (ss_.doc.cash = {});
  const money = el("table", { class: "ss-mini" },
    el("thead", {}, el("tr", {}, el("th", { text: "เงินที่นับได้" }), el("th", { class: "c", text: "จำนวน" }))),
    el("tbody", {}, ...d.notes.map((n) => el("tr", {}, el("td", { text: `แบงก์ ${elN(n)}` }), el("td", { class: "c" }, ssNumIn(cash[String(n)], (v) => { if (v === "") delete cash[String(n)]; else cash[String(n)] = ssN(v); })))),
      el("tr", {}, el("td", { text: "เหรียญ (บาท)" }), el("td", { class: "c" }, ssNumIn(cash.coins, (v) => { if (v === "") delete cash.coins; else cash.coins = ssN(v); })))));
  const km = el("div", { class: "ss-km" },
    el("label", {}, "ไมล์ใหม่ ", ssNumIn(ss_.doc.km_end, (v) => { ss_.doc.km_end = v === "" ? null : ssN(v); }, { "aria-label": "ไมล์ใหม่" })),
    el("span", { class: "muted small", text: `ไมล์เก่า ${d.km_start ? elN(d.km_start) : "—"}` }));
  const under = el("div", { class: "ss-under" }, el("div", {}, loads, el("button", { class: "btn small", type: "button", text: "+ เพิ่มเที่ยว",
    onclick: () => { ss_.doc.loads.push({ q: {} }); ss_.dirty = true; ssRenderDriver(); } })), el("div", {}, money, km));

  const bar = el("div", { class: "ss-savebar" },
    el("button", { class: "btn primary", id: "ssSave", type: "button", text: sheet ? "บันทึกแล้ว" : "บันทึก", disabled: true, onclick: ssSaveDay }),
    sheet && sheet.status === "checked" ? el("button", { class: "btn", type: "button", text: "ยกเลิกการตรวจ", onclick: () => ssCheck(sheet.id, false) })
      : sheet ? el("button", { class: "btn", type: "button", text: "ตรวจแล้ว ✓", onclick: () => ssCheck(sheet.id, true) }) : null,
    el("span", { class: "grow" }),
    el("button", { class: "btn", type: "button", text: "พิมพ์ใบงาน / PDF", onclick: () => ssPrintSheet(d.route.id, sheet ? ss_.day : "") }),
    el("button", { class: "btn", type: "button", text: "Excel", onclick: () => ssSheetXlsx(d.route.id, sheet ? ss_.day : "") }));
  setTimeout(ssPaintNumbers, 0);
  return el("div", {}, nav, tiles, ...warn, table, under, bar);
}

function ssPrice(c) {
  const p = c.prices || {}, keys = Object.keys(p);
  if (!keys.length) return "";
  const vals = [...new Set(keys.map((k) => p[k]))];
  if (vals.length === 1) return elN(vals[0]);
  const short = Object.fromEntries(ss_.data.products.map((x) => [x.key, x.short]));
  return keys.map((k) => `${short[k] || k}${elN(p[k])}`).join(" ");
}

function ssPaintRow(c, cells) {
  const ln = ssLine(c);
  cells.amount.textContent = ln.amount ? elN(ln.amount) : "";
  cells.after.textContent = ln.invoice ? (ln.amount ? "วางบิล" : "") : ln.after >= 1 ? elN(ln.after) : "";
  cells.after.className = `r${ln.after >= 1 && !ln.invoice ? " warn-text" : ""}`;
  const inp = cells.paid.querySelector("input");
  if (inp) inp.placeholder = ln.amount ? elN(ln.amount) : "";
  else cells.paid.textContent = ln.invoice ? "" : c.walkin && ln.amount ? elN(ln.amount) : c.free && ln.sold ? "ฟรี" : "";
  cells.amount.parentElement && cells.amount.parentElement.classList.toggle("sold", ln.sold);
}

function ssPaintNumbers() {
  const box = $("ssTiles");
  if (!box) return;
  const t = ssTotals(), P = ssProducts();
  const tile = (label, value, sub, cls = "") => el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: label }), el("b", { class: cls, text: value }), sub ? el("span", { class: "muted small", text: sub }) : null);
  const diff = t.hasCash ? t.counted - t.expected : null;
  fill(box,
    tile("ขายรวม", `฿${elN(t.amount)}`, `${t.shops}/${ss_.data.customers.length} ร้าน`),
    tile("เงินสดต้องมี", `฿${elN(t.expected)}`, t.old ? `รวมจ่ายเก่า ฿${elN(t.old)}` : ""),
    tile("นับได้", t.hasCash ? `฿${elN(t.counted)}` : "—", diff != null && Math.abs(diff) >= 1 ? `${diff > 0 ? "เกิน" : "ขาด"} ฿${elN(Math.abs(diff))}` : t.hasCash ? "ตรง" : "", diff != null && Math.abs(diff) >= 1 ? "warn-text" : ""),
    tile("ค้าง (ขายเชื่อ)", `฿${elN(t.credit)}`),
    tile("วางบิล", `฿${elN(t.invoiced)}`),
    tile("วิ่ง", t.km != null ? `${elN(t.km)} กม.` : "—"));
  const left = $("ssLeftRow");
  if (left) fill(left, el("td", { text: "รับ − ขาย = เหลือ" }), ...P.map((p) => {
    const v = t.loaded[p.key] - t.sold[p.key];
    return el("td", { class: `c${v < 0 ? " warn-text" : ""}`, text: t.loaded[p.key] || t.sold[p.key] ? elN(v) : "" });
  }), el("td", {}));
}

async function ssSaveDay() {
  const b = $("ssSave");
  b.disabled = true; b.textContent = "กำลังบันทึก…";
  // the phone shows how each shop paid from s.pay: keep it in step with what the office typed
  ssCustomers().forEach((c) => {
    const s = ss_.doc.sales[String(c.id)];
    if (!s) return;
    const ln = ssLine(c);
    s.pay = s.paid == null || s.paid === "" || ln.paid >= ln.amount ? "cash" : ln.paid <= 0 ? "credit" : "part";
  });
  const doc = { ...ss_.doc, loads: (ss_.doc.loads || []).filter((ld) => Object.values(ld.q || {}).some((v) => ssN(v))) };
  try { await api(`/api/sales-routes/${ss_.routeId}/office-day`, jsonOpts("PUT", { day: ss_.day, doc, returns: ss_.returns })); }
  catch (err) { alert(`บันทึกไม่สำเร็จ: ${err.message}`); b.disabled = false; b.textContent = "บันทึก"; return; }
  ss_.dirty = false;
  await ssLoadDay();
  const s = $("ssSave"); if (s) s.textContent = "บันทึกแล้ว ✓";
  if (typeof loadSalesAttention === "function") loadSalesAttention();
}

async function ssCheck(dayId, checked) {
  if (ss_.dirty && !confirm("ยังไม่ได้บันทึกการแก้ไข — ทำต่อโดยไม่บันทึก?")) return;
  try { await api(`/api/sales-routes/day/${dayId}/check`, jsonOpts("POST", { checked })); } catch (err) { alert(err.message); return; }
  await ssLoadDay();
  if (typeof loadSalesAttention === "function") loadSalesAttention();
}

async function ssReopen(dayId) {
  try { await api(`/api/sales-routes/day/${dayId}/reopen`, jsonOpts("POST", {})); } catch (err) { alert(err.message); return; }
  await ssLoadDay();
}

async function ssWalkin(dayId, c) {
  if (ss_.dirty) { alert("บันทึกการแก้ไขก่อน แล้วค่อยเพิ่มร้านนี้เข้ารายชื่อ"); return; }
  if (!confirm(`เพิ่ม "${c.name}" เข้ารายชื่อสายนี้ (ต่อท้ายสาย) ราคาตามที่คนขับใส่ — แก้ราคา / ลำดับได้ที่ "รายชื่อลูกค้า"`)) return;
  try { await api(`/api/sales-routes/day/${dayId}/walkin`, jsonOpts("POST", { key: c.id })); } catch (err) { alert(err.message); return; }
  await ssLoadDay();
  if (typeof loadSalesAttention === "function") loadSalesAttention();
}

// ---- รายชื่อลูกค้า
function ssCustomersTab() {
  const d = ss_.data, P = d.products;
  const owed = Object.values(d.owed_before).reduce((a, v) => a + (v > 0 ? v : 0), 0);
  const tiles = el("div", { class: "ss-tiles" },
    el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: "ลูกค้าในสาย" }), el("b", { text: String(d.customers.length) })),
    el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: "วางบิล" }), el("b", { text: String(d.customers.filter((c) => c.billing === "invoice").length) })),
    el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: "ฟรี" }), el("b", { text: String(d.customers.filter((c) => c.free).length) })),
    el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: `ค้างรวม (ก่อน ${ssDmy(d.day)})` }), el("b", { class: owed ? "warn-text" : "", text: `฿${elN(owed)}` })));
  const rows = d.customers.map((c) => el("tr", { onclick: () => ssEditCustomer(c) },
    el("td", { class: "muted", text: String(c.seq) }),
    el("td", {}, el("b", { text: c.name }), c.added_by_driver ? el("span", { class: "muted small", text: " · คนขับเพิ่ม" }) : null),
    el("td", { class: "ss-bins", text: c.bins || "" }),
    ...P.map((p) => el("td", { class: "c", text: c.prices && c.prices[p.key] ? elN(c.prices[p.key]) : "" })),
    el("td", { class: "small", text: [c.billing === "invoice" ? "วางบิล" : "", c.free ? "ฟรี" : ""].filter(Boolean).join(" · ") }),
    el("td", { class: `r${ssN(d.owed_before[String(c.id)]) >= 1 ? " warn-text" : ""}`, text: ssN(d.owed_before[String(c.id)]) >= 1 ? elN(d.owed_before[String(c.id)]) : "" })));
  return el("div", {},
    tiles,
    el("div", { class: "ss-custbar" },
      el("button", { class: "btn primary", type: "button", text: "+ เพิ่มลูกค้า", onclick: () => ssEditCustomer(null) }),
      el("button", { class: "btn", type: "button", text: "พิมพ์ใบงานเปล่า / PDF", onclick: () => ssPrintSheet(d.route.id, "") }),
      el("button", { class: "btn", type: "button", text: "ใบงานเปล่า Excel", onclick: () => ssSheetXlsx(d.route.id, "") }),
      el("span", { class: "muted small", text: "กดที่ร้านเพื่อแก้ชื่อ ถัง ราคา ลำดับ หรือเลิกส่ง" })),
    el("div", { class: "ss-tablewrap" }, el("table", { class: "ss-table ss-custtable" },
      el("thead", {}, el("tr", {}, el("th", { text: "#" }), el("th", { text: "ร้าน" }), el("th", { text: "ถัง" }), ...P.map((p) => el("th", { class: "c", text: p.label })),
        el("th", { text: "" }), el("th", { class: "r", text: "ค้าง" }))),
      el("tbody", {}, ...rows))));
}

function ssEditCustomer(c) {
  const d = ss_.data, P = d.products;
  const close = () => { dlg.close(); dlg.remove(); };
  const f = (id, label, input) => el("label", { class: "ss-f", for: id }, el("span", { class: "small muted", text: label }), input);
  const name = el("input", { id: "sceName", type: "text", maxlength: "120", value: c ? c.name : "" });
  const seq = el("input", { id: "sceSeq", type: "number", min: "1", value: c ? c.seq : (Math.max(0, ...d.customers.map((x) => x.seq)) + 1) });
  const bins = el("input", { id: "sceBins", type: "text", maxlength: "300", value: c ? c.bins || "" : "", placeholder: "เช่น V200-1805, S200-0412" });
  const prices = P.map((p) => el("input", { type: "text", inputmode: "decimal", "data-k": p.key, value: c && c.prices && c.prices[p.key] ? c.prices[p.key] : "", "aria-label": `ราคา${p.label}` }));
  const invoice = el("input", { type: "checkbox", checked: !!(c && c.billing === "invoice") });
  const free = el("input", { type: "checkbox", checked: !!(c && c.free) });
  const save = async () => {
    const body = { name: name.value.trim(), seq: seq.value, bins: bins.value.trim(), billing: invoice.checked ? "invoice" : "cash", free: free.checked,
      prices: Object.fromEntries(prices.filter((x) => ssN(x.value) > 0).map((x) => [x.dataset.k, ssN(x.value)])) };
    if (!body.name) { alert("ใส่ชื่อร้าน"); return; }
    try {
      if (c) await api(`/api/sales-routes/customers/${c.id}`, jsonOpts("PATCH", body));
      else await api(`/api/sales-routes/${d.route.id}/customers`, jsonOpts("POST", body));
    } catch (err) { alert(err.message); return; }
    close(); await ssLoadDay();
  };
  const drop = async () => {
    if (!confirm(`เลิกส่ง "${c.name}"? (เอาออกจากรายชื่อสาย ประวัติการขายยังอยู่)`)) return;
    try { await api(`/api/sales-routes/customers/${c.id}`, jsonOpts("PATCH", { active: false })); } catch (err) { alert(err.message); return; }
    close(); await ssLoadDay();
  };
  const dlg = el("dialog", { class: "ss-dialog" },
    el("div", { class: "ss-dlg-head" }, el("h3", { text: c ? `แก้ไขลูกค้า · ${c.name}` : `เพิ่มลูกค้า · รถ ${d.route.truck_no}` }),
      el("button", { class: "btn small", type: "button", text: "ปิด", onclick: close })),
    el("div", { class: "ss-form" },
      f("sceName", "ชื่อร้าน", name), f("sceSeq", "ลำดับในสาย (ร้านที่ลำดับนี้และหลังจากนี้เลื่อนลงหนึ่ง)", seq), f("sceBins", "ถัง", bins),
      el("div", { class: "ss-f" }, el("span", { class: "small muted", text: "ราคาต่อหน่วย (บาท) — ใส่เฉพาะที่ขาย" }),
        el("div", { class: "ss-prices" }, ...P.map((p, i) => el("label", {}, el("span", { class: "small", text: p.label }), prices[i])))),
      el("label", { class: "ss-check" }, invoice, " วางบิล (ห้าง / ร้านสาขา ไม่เก็บเงินสด)"),
      el("label", { class: "ss-check" }, free, " ฟรี (ไม่คิดเงิน)")),
    el("div", { class: "ss-dlg-foot" },
      c ? el("button", { class: "btn", type: "button", text: "เลิกส่งร้านนี้", onclick: drop }) : null,
      el("span", { class: "grow" }),
      el("button", { class: "btn primary", type: "button", text: c ? "บันทึก" : "เพิ่ม", onclick: save })));
  document.body.append(dlg);
  dlg.showModal();
  name.focus();
}

// ---- คนขับ / รหัส
function ssDriverTab() {
  const r = (ss_.routes || []).find((x) => x.id === ss_.routeId);
  if (!r) return el("p", { class: "muted", text: "โหลดข้อมูลคนขับไม่สำเร็จ" });
  return el("div", { class: "card ss-driver" },
    el("p", {}, el("b", { text: r.driver_name || "—" }), el("span", { class: "muted small", text: r.driver_phone ? ` · ${r.driver_phone}` : "" })),
    el("p", {}, el("span", { class: `badge ${r.pin_set ? "ok" : ""}`, text: r.pin_set ? "ตั้งรหัสแล้ว" : "ยังไม่ได้ตั้งรหัส — คนขับจะยังไม่เห็นชื่อในโทรศัพท์" })),
    r.pin_set ? ssPhones(r) : null,
    el("div", { class: "ss-actions" },
      el("button", { class: "btn primary", type: "button", text: r.pin_set ? "ตั้งรหัสใหม่" : "ตั้งรหัส", onclick: () => ssSetPin(r) }),
      r.pin_set ? el("button", { class: "btn", type: "button", text: "ออกจากระบบทุกเครื่อง", onclick: () => ssSignOut(r) }) : null),
    el("p", { class: "muted small", text: `ลิงก์สำหรับคนขับ: ${ssLink()} · เลือกชื่อ ใส่รหัส 4 หลักครั้งเดียว แล้วโทรศัพท์จะจำไว้` }));
}

function ssAgo(iso) {
  if (!iso) return "—";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 15) return "เพิ่งใช้";
  if (min < 60) return `${min} นาทีก่อน`;
  if (min < 24 * 60) return `${Math.round(min / 60)} ชม.ก่อน`;
  return new Date(iso).toLocaleDateString("th-TH", { day: "numeric", month: "short" });
}

function ssPhones(r) {
  const ph = r.phones || [];
  if (!ph.length) return el("p", { class: "ss-phones muted small", text: "ยังไม่มีโทรศัพท์เข้าระบบ" });
  return el("div", { class: "ss-phones" },
    el("span", { class: `small${ph.length > 1 ? " warn-text" : ""}`, text: `เข้าระบบอยู่ ${ph.length} เครื่อง` }),
    ...ph.map((x) => el("span", { class: "ss-phone small", text: `${x.agent} · ใช้ล่าสุด ${ssAgo(x.last_seen)} · เข้าระบบ ${new Date(x.since).toLocaleDateString("th-TH", { day: "numeric", month: "short" })}` })));
}

async function ssSetPin(r) {
  const pin = window.prompt(`รหัส 4 หลักสำหรับ ${r.driver_name || "สาย " + r.truck_no}\n(บอกคนขับเอง อย่าส่งในกลุ่มไลน์)`, "");
  if (pin == null) return;
  if (!/^\d{4}$/.test(pin.trim())) { alert("รหัสต้องเป็นตัวเลข 4 หลัก"); return; }
  try { await api(`/api/sales-routes/${r.id}/pin`, jsonOpts("POST", { pin: pin.trim() })); } catch (err) { alert(err.message); return; }
  ss_.routes = null;
  await ssLoadDay();
}

async function ssSignOut(r) {
  if (!confirm(`ออกจากระบบทุกโทรศัพท์ของ ${r.driver_name}? (ต้องใส่รหัสใหม่ตอนเปิดครั้งหน้า)`)) return;
  try { const x = await api(`/api/sales-routes/${r.id}/signout`, jsonOpts("POST", {})); alert(`ออกจากระบบแล้ว ${x.signed_out} เครื่อง`); } catch (err) { alert(err.message); }
  ss_.routes = null;
  await ssLoadDay();
}

// ------------------------------------------------------------------ ใบงานคนขับ: the paper sheet, from IceBox (app/route_sheet.py)
async function ssSheetXlsx(routeId, day) {
  let res;
  try {
    res = await fetch(`/api/sales-routes/${routeId}/sheet.xlsx${day ? `?day=${day}` : ""}`, { headers: { "X-App-Token": state.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  } catch (err) { alert(`โหลดไม่สำเร็จ: ${err.message}`); return; }
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const a = el("a", { href: URL.createObjectURL(await res.blob()), download: m ? decodeURIComponent(m[1]) : "sheet.xlsx" });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

// The print view: the same sheet as HTML in a new tab, A4, black and white; the browser's print dialog saves it as PDF.
async function ssPrintSheet(routeId, day) {
  const w = window.open("", "_blank");
  if (!w) { alert("เบราว์เซอร์บล็อกหน้าต่างใหม่ — อนุญาตป๊อปอัปของเว็บนี้แล้วลองใหม่"); return; }
  w.document.write("<p style='font-family:sans-serif'>กำลังโหลด…</p>");
  let s;
  try { s = await api(`/api/sales-routes/${routeId}/sheet${day ? `?day=${day}` : ""}`); }
  catch (err) { w.document.body.textContent = `โหลดไม่สำเร็จ: ${err.message}`; return; }
  w.document.open();
  w.document.write(ssSheetHtml(s));
  w.document.close();
  w.onload = () => setTimeout(() => w.print(), 300);
}

function ssSheetHtml(s) {
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const n = (v) => (v == null || v === "" || v === 0 ? "" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 }));
  const d = s.day ? s.day.split("-").reverse().join("/") : "....................";
  const P = s.products, sm = s.summary || {};
  const cols = ["ลูกค้า", "ถัง", "ราคา", ...P.map((p) => p.label), "รวมเงิน", "เก่ายกมา", "จ่ายเก่า", "จ่ายสด", "รวมจ่าย", "ค้าง"];
  const head = `<tr class="hd"><td colspan="${cols.length}"><div class="hdgrid">
      <span>รถเบอร์ <b class="big">${esc(s.truck_no)}</b></span><span>พนักงานขับรถ <b class="big">${esc(s.driver)}</b></span><span>วันที่ <b class="big">${esc(d)}</b></span>
      <span>สาย <b class="big">${esc(s.route)}</b></span><span>เบอร์โทรศัพท์ <b class="big">${esc(s.phone)}</b></span><span></span></div></td></tr>
    <tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  const body = s.rows.map((r) => `<tr class="${r.sold ? "sold" : ""}"><td class="nm">${esc(r.name)}</td><td class="bins">${esc(r.bins).replace(/, /g, "<br>")}</td>
      <td class="c">${esc(r.price)}</td>${P.map((p) => `<td class="c">${n(r.q[p.key])}</td>`).join("")}
      <td class="r">${n(r.amount)}</td><td class="r">${n(r.owed_before)}</td><td class="r">${n(r.old_paid)}</td><td class="r">${n(r.paid)}</td>
      <td class="r">${n(r.paid_all)}</td><td class="r">${r.invoice && r.amount ? "วางบิล" : n(r.owed_after)}</td></tr>`).join("");
  const t = s.totals;
  const foot = `<tr class="tot"><td colspan="3" class="c">รวมถุง</td>${P.map((p) => `<td class="c">${n(t.q[p.key])}</td>`).join("")}<td colspan="6"></td></tr>
    <tr class="tot"><td colspan="3" class="c">รวมเงิน</td>${P.map(() => "<td></td>").join("")}<td class="r">${n(t.amount)}</td><td class="r">${n(t.owed_before)}</td>
      <td class="r">${n(t.old_paid)}</td><td class="r">${n(t.paid)}</td><td class="r">${n(t.paid_all)}</td><td class="r">${n(t.owed_after)}</td></tr>`;
  const loads = (sm.loads && sm.loads.length ? sm.loads.map((q, i) => [String(i + 1), q]) : [["1", {}], ["2", {}], ["3", {}]])
    .concat([["รวม", sm.loaded || {}], ["คืน", sm.returned || {}], ["ขายได้", sm.sold || {}], ["ขาด(-) / เกิน(+)", sm.diff || {}]]);
  const stock = `<table class="box"><tr><th>รับ</th>${P.map((p) => `<th>${p.label}</th>`).join("")}</tr>${loads.map(([l, q]) =>
    `<tr><td class="c">${l}</td>${P.map((p) => `<td class="c">${n(q[p.key])}</td>`).join("")}</tr>`).join("")}</table>`;
  const money = [["รวมขาย", sm.amount], ["วางบิล", sm.invoiced], ["ค้าง (ขายเชื่อ)", sm.credit], ["ขายสด", sm.cash_sales], ["จ่ายเก่า", sm.old_paid],
    ["รวมรับเงิน", sm.expected_cash], ["ค้างยกมา", sm.owed_before], ["ค้างยกไป", sm.owed_after]];
  const notes = [1000, 500, 100, 50, 20].map((v) => [`${v.toLocaleString()} =`, (sm.notes || {})[String(v)]]).concat([["เหรียญ =", sm.coins], ["รวมเงิน", sm.counted]]);
  const kv = (list, cls) => `<table class="box ${cls}">${list.map(([l, v]) => `<tr><td>${l}</td><td class="r b">${n(v)}</td></tr>`).join("")}</table>`;
  return `<!doctype html><html lang="th"><head><meta charset="utf-8"><title>ใบงานคนขับ รถ ${esc(s.truck_no)} ${esc(d)}</title>
<link rel="stylesheet" href="/static/fonts/fonts.css"><style>
@page { size: A4 portrait; margin: 8mm; }
* { box-sizing: border-box; }
body { font-family: "Noto Sans Thai", Tahoma, sans-serif; color: #000; background: #fff; margin: 0; font-size: 10.5px; }
table { border-collapse: collapse; width: 100%; }
.main { table-layout: fixed; }
.main td { overflow-wrap: anywhere; }
.main th, .main td, .box th, .box td { border: 1px solid #000; padding: 2px 3px; }
.main th { font-size: 10px; font-weight: 700; }
.main thead .hd td { border: none; padding: 0 0 4px; }
.hdgrid { display: grid; grid-template-columns: 1.1fr 1.6fr 1fr; gap: 2px 10px; font-size: 11px; }
.big { font-size: 14px; }
.nm { font-size: 10.5px; } tr.sold .nm { font-weight: 700; }
.bins { font-size: 8px; text-align: center; line-height: 1.15; }
.c { text-align: center; } .r { text-align: right; } .b { font-weight: 700; }
tr { page-break-inside: avoid; }
.tot td { font-weight: 700; }
.under { display: grid; grid-template-columns: 1.5fr 1fr .8fr; gap: 10px; margin-top: 10px; page-break-inside: avoid; }
.km { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0; margin-top: 10px; border: 1px solid #000; page-break-inside: avoid; }
.km span { border-right: 1px solid #000; padding: 6px 4px; } .km span:last-child { border-right: 0; }
.flags { margin-top: 6px; font-size: 9.5px; }
.bar { position: sticky; top: 0; background: #f3f3f3; padding: 8px; display: flex; gap: 8px; align-items: center; font-size: 13px; }
.bar button { font: inherit; padding: 4px 12px; }
@media print { .bar { display: none; } }
</style></head><body>
<div class="bar"><button onclick="print()">พิมพ์ / บันทึกเป็น PDF</button><span>เลือกเครื่องพิมพ์ “Save as PDF” เพื่อบันทึกเป็นไฟล์ · กระดาษ A4 แนวตั้ง</span></div>
<table class="main"><colgroup><col style="width:23%"><col style="width:9%"><col style="width:7%">${P.map(() => '<col style="width:4.6%">').join("")}<col style="width:7%"><col style="width:6.2%"><col style="width:6.2%"><col style="width:6.2%"><col style="width:6.2%"><col style="width:6.2%"></colgroup><thead>${head}</thead><tbody>${body}${foot}</tbody></table>
<div class="under">${stock}${kv(money, "money")}${kv(notes, "notes")}</div>
<div class="km"><span>ไมล์ใหม่ <b>${n(sm.km_end)}</b></span><span>ไมล์เก่า <b>${n(sm.km_start)}</b></span><span>รถวิ่ง <b>${n(sm.km)}</b> กม.</span><span>ตรวจ</span><span>ตรวจ</span></div>
${(sm.flags || []).length ? `<div class="flags">ที่ไม่ตรง: ${sm.flags.map(esc).join(" · ")}</div>` : ""}
</body></html>`;
}
