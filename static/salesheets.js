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
  document.querySelector("#salesSheetsView .page-head").hidden = !!rid;     // inside a driver: no big title
  if (!rid) { ss_.routeId = null; return ssOverview(); }
  const sameRoute = ss_.routeId === Number(rid);
  ss_.routeId = Number(rid);
  ss_.tab = !part || part === "customers" ? "customers" : part === "driver" ? "driver" : "day";
  // a date in the link wins; switching tabs keeps the day; opening a driver starts on today
  ss_.day = /^\d{4}-\d{2}-\d{2}$/.test(part || "") ? part : sameRoute ? ss_.day : null;
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
  const all = el("div", { class: "ss-custbar" },
    el("span", { class: "small muted", text: "ใบงานเปล่า ทุกสาย:" }),
    el("button", { class: "btn", type: "button", text: "PDF (พิมพ์)", onclick: () => ssPrintAll("", "pdf") }),
    el("button", { class: "btn", type: "button", text: "Word", onclick: () => ssPrintAll("", "docx") }));
  fill($("ssBody"), link, all, el("div", { class: "ss-grid" }, ...blocks));
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
    onclick: () => goHash(`#sales-sheets/${r.id}/${key === "day" ? ss_.day : key}`) });
  const head = el("div", { class: "ss-dhead" },
    el("button", { class: "btn small ss-back", type: "button", text: "‹ ทุกสาย", onclick: () => goHash("#sales-sheets") }),
    el("div", { class: "ss-dtitle" }, el("b", { class: "ss-block-no", text: r.truck_no }),
      el("span", {}, el("strong", { text: r.driver_name || "—" }), el("span", { class: "muted small", text: ` · ${r.name || ""}${r.driver_phone ? ` · ${r.driver_phone}` : ""}` }))),
    el("div", { class: "seg ss-tabs" }, tab("customers", `รายชื่อลูกค้า (${d.customers.length})`), tab("day", "ใบขายรายวัน"), tab("driver", "คนขับ / รหัส")));
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
  const go = (n) => { if (n > 0 && ss_.day >= d.today) return; if (ss_.dirty && !confirm("ยังไม่ได้บันทึก — ไปวันอื่นเลยไหม?")) return; ss_.dirty = false; goHash(`#sales-sheets/${d.route.id}/${ssAddDay(ss_.day, n)}`); };
  const pick = el("input", { type: "date", value: ss_.day, max: d.today, "aria-label": "เลือกวัน",
    onchange: (e) => { if (e.target.value > d.today) { alert("ยังไม่ถึงวันนั้น"); e.target.value = ss_.day; return; } if (e.target.value && e.target.value !== ss_.day) { if (ss_.dirty && !confirm("ยังไม่ได้บันทึก — ไปวันอื่นเลยไหม?")) return; ss_.dirty = false; goHash(`#sales-sheets/${d.route.id}/${e.target.value}`); } } });
  const has = new Set(d.days_with_sheet);
  const prevDay = [...has].filter((x) => x < ss_.day).pop(), nextDay = [...has].filter((x) => x > ss_.day && x <= d.today)[0];
  const st = !sheet ? ["ไม่มีใบขาย — กรอกจากใบกระดาษแล้วกดบันทึกได้", ""] : sheet.unsent ? ["คนขับไม่ได้กดส่ง", "bad"] : SS_ST[sheet.status] || [sheet.status, ""];
  const nav = el("div", { class: "ss-daynav card" },
    el("button", { class: "btn ss-arrow", type: "button", text: "‹", "aria-label": "วันก่อน", onclick: () => go(-1) }),
    el("div", { class: "ss-daypick" }, el("b", { class: "ss-daybig", text: ssDmy(ss_.day) }),
      el("span", { class: "muted small", text: `${ssWeekday(ss_.day)}${ss_.day === d.today ? " · วันนี้" : ""}` }), pick),
    el("button", { class: "btn ss-arrow", type: "button", text: "›", "aria-label": "วันถัดไป", disabled: ss_.day >= d.today, onclick: () => go(1) }),
    el("div", { class: "ss-dayjump" },
      prevDay ? el("button", { class: "btn small", type: "button", text: `‹ วันที่มีใบขาย ${ssDmy(prevDay)}`, onclick: () => goHash(`#sales-sheets/${d.route.id}/${prevDay}`) }) : null,
      nextDay ? el("button", { class: "btn small", type: "button", text: `${ssDmy(nextDay)} ›`, onclick: () => goHash(`#sales-sheets/${d.route.id}/${nextDay}`) }) : null,
      ss_.day !== d.today ? el("button", { class: "btn small", type: "button", text: "วันนี้", onclick: () => goHash(`#sales-sheets/${d.route.id}/${d.today}`) }) : null,
      el("span", { class: `badge ${st[1]}`, text: st[0] })));
  const tiles = el("div", { class: "ss-tiles", id: "ssTiles" });
  const warn = [];
  if (d.driver_selling) warn.push(el("p", { class: "warn-text small", text: "คนขับกำลังขายวันนี้อยู่ — ถ้าบันทึกตอนนี้ ตัวเลขของออฟฟิศจะทับในโทรศัพท์ของคนขับ" }));
  if (sheet && (sheet.totals.flags || []).length) warn.push(el("p", { class: "warn-text small", text: `⚠ ${sheet.totals.flags.join(" · ")}` }));
  if (sheet && sheet.reopen_requested_at) warn.push(el("p", { class: "warn-text small", text: `คนขับแจ้งว่าใบนี้ต้องแก้ (${new Date(sheet.reopen_requested_at).toLocaleString("th-TH")}) — แก้ในตารางแล้วกดบันทึก` }));

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
  const table = el("div", { class: "ss-tablewrap soft" }, el("table", { class: "ss-table" }, el("thead", {}, head), el("tbody", {}, ...rows)));

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
    el("span", { class: "small muted", text: "ใบงาน:" }),
    el("button", { class: "btn", type: "button", text: "PDF (พิมพ์)", onclick: () => ssPrintSheet(d.route.id, sheet ? ss_.day : "") }),
    el("button", { class: "btn", type: "button", text: "Word", onclick: () => ssSheetDocx(d.route.id, sheet ? ss_.day : "") }),
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

async function ssWalkin(dayId, c) {
  if (ss_.dirty) { alert("บันทึกการแก้ไขก่อน แล้วค่อยเพิ่มร้านนี้เข้ารายชื่อ"); return; }
  if (!confirm(`เพิ่ม "${c.name}" เข้ารายชื่อสายนี้ (ต่อท้ายสาย) ราคาตามที่คนขับใส่ — แก้ราคา / ลำดับได้ที่ "รายชื่อลูกค้า"`)) return;
  try { await api(`/api/sales-routes/day/${dayId}/walkin`, jsonOpts("POST", { key: c.id })); } catch (err) { alert(err.message); return; }
  await ssLoadDay();
  if (typeof loadSalesAttention === "function") loadSalesAttention();
}

// ---- รายชื่อลูกค้า (the driver page opens here: owner, 11 Nov 2026)
const ssCanSeeContact = () => !!state.me && (state.me.role === "owner" || (state.me.menus || []).includes("customers"));

function ssCustomersTab() {
  const d = ss_.data, P = d.products;
  const owed = Object.values(d.owed_before).reduce((a, v) => a + (v > 0 ? v : 0), 0);
  const nBins = d.customers.reduce((a, c) => a + (c.bins_list || []).length, 0);
  const clashes = d.customers.filter((c) => (c.bins_clash || []).length).length;
  const tile = (label, value, cls = "") => el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: label }), el("b", { class: cls, text: value }));
  const tiles = el("div", { class: "ss-tiles" },
    tile("ลูกค้าในสาย", String(d.customers.length)),
    tile("ถังที่ให้ยืม", String(nBins)),
    tile("วางบิล · ฟรี", `${d.customers.filter((c) => c.billing === "invoice").length} · ${d.customers.filter((c) => c.free).length}`),
    tile("ค้างรวม", `฿${elN(owed)}`, owed ? "warn-text" : ""),
    clashes ? tile("ถังซ้ำกับร้านอื่น — ตรวจ", String(clashes), "warn-text") : null);
  const q = el("input", { type: "text", class: "ss-find", placeholder: "หาร้าน หรือ เลขถัง", value: ss_.q || "",
    oninput: (e) => { ss_.q = e.target.value; ssPaintCustomerRows(body); } });
  const binQ = el("input", { type: "text", class: "ss-find", placeholder: "ถังนี้อยู่ไหน? พิมพ์เลขถังแล้วกด Enter",
    onkeydown: (e) => { if (e.key === "Enter") ssFindBin(e.target.value, binOut); } });
  const binOut = el("div", { class: "ss-binout" });
  const body = el("tbody", {});
  const table = el("div", { class: "ss-tablewrap soft" }, el("table", { class: "ss-table ss-custtable" },
    el("thead", {}, el("tr", {}, el("th", { text: "#" }), el("th", { text: "ร้าน" }), el("th", { text: "ถัง" }), ...P.map((p) => el("th", { class: "c", text: p.label })),
      el("th", { text: "" }), el("th", { class: "r", text: "ค้าง" }))), body));
  ssPaintCustomerRows(body);
  return el("div", {},
    tiles,
    el("div", { class: "ss-custbar" },
      el("button", { class: "btn primary", type: "button", text: "+ เพิ่มลูกค้า", onclick: () => ssEditCustomer(null) }),
      q, binQ,
      el("span", { class: "grow" }),
      el("span", { class: "small muted", text: "ใบงานเปล่า:" }),
      el("button", { class: "btn", type: "button", text: "PDF (พิมพ์)", onclick: () => ssPrintSheet(d.route.id, "") }),
      el("button", { class: "btn", type: "button", text: "Word", onclick: () => ssSheetDocx(d.route.id, "") }),
      el("button", { class: "btn", type: "button", text: "Excel", onclick: () => ssSheetXlsx(d.route.id, "") })),
    binOut, table);
}

function ssPaintCustomerRows(body) {
  const d = ss_.data, P = d.products, q = (ss_.q || "").trim().toLowerCase();
  const rows = d.customers.filter((c) => !q || `${c.seq} ${c.name} ${c.bins || ""}`.toLowerCase().includes(q));
  fill(body, ...rows.map((c) => el("tr", { onclick: () => ssEditCustomer(c) },
    el("td", { class: "muted", text: String(c.seq) }),
    el("td", {}, el("b", { text: c.name }), c.added_by_driver ? el("span", { class: "muted small", text: " · คนขับเพิ่ม" }) : null),
    el("td", { class: "ss-binscell" }, ...(c.bins_list || []).map((b) => el("span", { class: "ss-binchip", text: b.code })),
      c.bins_other ? el("span", { class: "ss-binchip other", text: c.bins_other }) : null,
      (c.bins_clash || []).length ? el("span", { class: "badge warn", title: "ใบงานเดิมเขียนถังนี้ แต่ถังอยู่ที่ร้านอื่น", text: `ซ้ำ ${c.bins_clash.join(", ")}` }) : null),
    ...P.map((p) => el("td", { class: "c", text: c.prices && c.prices[p.key] ? elN(c.prices[p.key]) : "" })),
    el("td", { class: "small muted", text: [c.billing === "invoice" ? "วางบิล" : "", c.free ? "ฟรี" : ""].filter(Boolean).join(" · ") }),
    el("td", { class: `r${ssN(d.owed_before[String(c.id)]) >= 1 ? " warn-text" : ""}`, text: ssN(d.owed_before[String(c.id)]) >= 1 ? elN(d.owed_before[String(c.id)]) : "" }))));
}

async function ssFindBin(text, out) {
  if (!text.trim()) { fill(out, null); return; }
  let r;
  try { r = await api(`/api/sales-routes/bins/find?q=${encodeURIComponent(text.trim())}&branch=${encodeURIComponent(ss_.data.route.branch || "สระบุรี")}`); }
  catch (err) { fill(out, el("p", { class: "error small", text: err.message })); return; }
  const names = Object.fromEntries(ss_.data.customers.map((c) => [c.id, `${c.seq}. ${c.name}`]));
  const who = (id) => (id == null ? "ไม่อยู่ที่ร้าน" : names[id] ? `${names[id]} (สายนี้)` : `ลูกค้ารหัส ${id} (สายอื่น)`);
  fill(out, r.bins.length ? el("div", { class: "card ss-binfound" }, ...r.bins.map((b) => el("div", {},
    el("b", { text: b.code }), el("span", { text: ` · อยู่ที่ ${who(b.customer_id)}${b.since ? ` ตั้งแต่ ${ssDmy(b.since)}` : ""}` }),
    el("div", { class: "muted small", text: (b.history || []).map((h) => `${new Date(h.at).toLocaleDateString("th-TH")} ${h.note || ""} → ${who(h.to_customer)}${h.who ? ` (${h.who})` : ""}`).join(" · ") }))))
    : el("p", { class: "muted small", text: "ไม่พบถังนี้ในระบบ" }));
}

// The customer form: like the repair-bill form -- sections, one row per bin, photos (owner, 11 Nov 2026)
async function ssEditCustomer(c) {
  const d = ss_.data, P = d.products, contactOk = ssCanSeeContact();
  let reg = null, photos = [];
  if (c && contactOk) {
    try { const x = await api(`/api/customers/for/${c.id}`); reg = x.register; photos = x.photos; } catch (e) { /* no register menu: the form still works */ }
  }
  const close = () => { dlg.close(); dlg.remove(); urls.forEach((u) => URL.revokeObjectURL(u)); };
  const urls = [];
  const inp = (attrs) => el("input", { type: "text", ...attrs });
  const f = (label, input, wide) => el("label", { class: `ss-f${wide ? " wide" : ""}` }, el("span", { class: "small muted", text: label }), input);
  const name = inp({ maxlength: "120", value: c ? c.name : "" });
  const seq = el("input", { type: "number", min: "1", value: c ? c.seq : (Math.max(0, ...d.customers.map((x) => x.seq)) + 1) });
  const invoice = el("input", { type: "checkbox", checked: !!(c && c.billing === "invoice") });
  const free = el("input", { type: "checkbox", checked: !!(c && c.free) });
  const prices = P.map((p) => el("input", { type: "text", inputmode: "decimal", "data-k": p.key, value: c && c.prices && c.prices[p.key] ? c.prices[p.key] : "" }));
  // contact (the register)
  const R = reg || {};
  const contact = { contact_name: inp({ value: R.contact_name || "" }), phone: inp({ value: R.phone || "", inputmode: "tel" }),
    id_card: inp({ value: R.id_card || "", inputmode: "numeric" }), gps: inp({ value: R.gps || "", placeholder: "พิกัด หรือ ลิงก์ Google Maps" }),
    loan_date: el("input", { type: "date", value: R.loan_date || "" }), note: inp({ value: R.note || "" }) };
  // bins: one row each
  const binRows = el("div", { class: "ss-binrows" });
  const binRow = (b = {}) => {
    const model = el("select", {}, ...["V", "S", "C", "T", "L", ""].map((m) => el("option", { value: m, text: m || "ไม่มี", selected: (b.model || "") === m })));
    const size = el("select", {}, ...[100, 120, 130, 150, 160, 200, 300, 400, 450, 500, 800, 850].map((s) => el("option", { value: s, text: `${s} ลิตร`, selected: Number(b.size || 200) === s })));
    const number = el("input", { type: "text", inputmode: "numeric", value: b.number || "", placeholder: "เลขถัง", class: "ss-binno" });
    const since = el("input", { type: "date", value: b.since || "" });
    const row = el("div", { class: "ss-binrow" }, model, size, number, el("span", { class: "small muted", text: "ยืมตั้งแต่" }), since,
      el("button", { class: "btn small", type: "button", text: "ลบ", onclick: () => row.remove() }));
    row.read = () => ({ model: model.value, size: size.value, number: number.value.trim(), since: since.value || null });
    binRows.append(row);
  };
  ((c && c.bins_list) || []).forEach(binRow);
  if (!c) binRow();
  const binsOther = inp({ value: c ? c.bins_other || "" : "", placeholder: "ถังที่ไม่มีเลข เช่น 500L" });
  // photos
  const photoBox = el("div", { class: "cu-photos" });
  const newFiles = [];
  const paintPhotos = () => {
    fill(photoBox, ...photos.map((p) => {
      const box = el("div", { class: "cu-photo" }, el("span", { class: "muted small", text: "กำลังโหลด…" }));
      if (p.mime_type !== "application/pdf") cuPhoto(p.id).then((u) => box.replaceChildren(el("img", { src: u, alt: p.name || "รูป" }))).catch(() => box.replaceChildren(el("span", { class: "small", text: "โหลดรูปไม่ได้" })));
      else box.replaceChildren(el("span", { text: "PDF" }));
      box.append(el("button", { class: "ss-photo-x", type: "button", title: "ลบรูป", text: "×", onclick: async () => {
        if (!confirm("ลบรูปนี้?")) return;
        try { await api(`/api/customers/photo/${p.id}`, { method: "DELETE" }); photos = photos.filter((x) => x.id !== p.id); paintPhotos(); } catch (err) { alert(err.message); }
      } }));
      return box;
    }), ...newFiles.map((f, i) => {
      const u = URL.createObjectURL(f); urls.push(u);
      return el("div", { class: "cu-photo new" }, f.type === "application/pdf" ? el("span", { text: "PDF" }) : el("img", { src: u, alt: f.name }),
        el("button", { class: "ss-photo-x", type: "button", text: "×", onclick: () => { newFiles.splice(i, 1); paintPhotos(); } }));
    }));
  };
  const pick = el("input", { type: "file", accept: "image/*,application/pdf", multiple: true, hidden: true,
    onchange: () => { newFiles.push(...pick.files); pick.value = ""; paintPhotos(); } });
  paintPhotos();
  const msg = el("p", { class: "error small", hidden: true });
  const save = async (btn) => {
    const body = { name: name.value.trim(), seq: seq.value, billing: invoice.checked ? "invoice" : "cash", free: free.checked,
      prices: Object.fromEntries(prices.filter((x) => ssN(x.value) > 0).map((x) => [x.dataset.k, ssN(x.value)])),
      bins_list: [...binRows.children].map((r) => r.read()).filter((b) => b.number), bins_other: binsOther.value.trim() };
    if (!body.name) { msg.textContent = "ใส่ชื่อร้าน"; msg.hidden = false; return; }
    btn.disabled = true; btn.textContent = "กำลังบันทึก…"; msg.hidden = true;
    try {
      let id = c && c.id, r;
      if (c) r = await api(`/api/sales-routes/customers/${c.id}`, jsonOpts("PATCH", body));
      else { r = await api(`/api/sales-routes/${d.route.id}/customers`, jsonOpts("POST", body)); id = r.id; }
      if (contactOk) {
        const vals = Object.fromEntries(Object.entries(contact).map(([k, x]) => [k, x.value.trim()]));
        if (reg || Object.values(vals).some(Boolean) || newFiles.length) await api(`/api/customers/for/${id}`, jsonOpts("PUT", { shop: body.name, ...vals }));
        for (let i = 0; i < newFiles.length; i += 10) {
          const fd = new FormData();
          newFiles.slice(i, i + 10).forEach((f) => fd.append("files", f));
          const res = await fetch(`/api/customers/for/${id}/photos`, { method: "POST", body: fd, headers: { "X-App-Token": state.token } });
          if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `อัปโหลดรูปไม่สำเร็จ (HTTP ${res.status})`);
        }
      }
      if (r && r.moved && r.moved.length) alert(`ย้ายถังมาจากร้านอื่น: ${r.moved.map((m) => m.code).join(", ")} — บันทึกประวัติไว้แล้ว`);
    } catch (err) { msg.textContent = err.message; msg.hidden = false; btn.disabled = false; btn.textContent = c ? "บันทึก" : "เพิ่มลูกค้า"; return; }
    close(); await ssLoadDay();
  };
  const drop = async () => {
    if (!confirm(`เลิกส่ง "${c.name}"? (เอาออกจากรายชื่อสาย ประวัติการขายยังอยู่ — ถังที่ยืมยังผูกกับร้านนี้จนกว่าจะเก็บคืน)`)) return;
    try { await api(`/api/sales-routes/customers/${c.id}`, jsonOpts("PATCH", { active: false })); } catch (err) { alert(err.message); return; }
    close(); await ssLoadDay();
  };
  const saveBtn = el("button", { class: "btn primary", type: "button", text: c ? "บันทึก" : "เพิ่มลูกค้า", onclick: (e) => save(e.target) });
  const dlg = el("dialog", { class: "ss-dialog ss-form-dialog" },
    el("div", { class: "ss-dlg-head" }, el("h3", { text: c ? `${c.seq}. ${c.name}` : `เพิ่มลูกค้า · รถ ${d.route.truck_no}` }),
      el("button", { class: "btn small", type: "button", text: "ปิด", onclick: close })),
    el("section", { class: "ss-sec" }, el("h4", { text: "ร้าน" }),
      el("div", { class: "ss-grid2" }, f("ชื่อร้าน *", name, true), f("ลำดับในสาย", seq),
        el("div", { class: "ss-f" }, el("span", { class: "small muted", text: "การจ่าย" }),
          el("label", { class: "ss-check" }, invoice, " วางบิล (ห้าง / สาขา)"), el("label", { class: "ss-check" }, free, " ฟรี")))),
    contactOk ? el("section", { class: "ss-sec" }, el("h4", { text: "ผู้ติดต่อ" }),
      el("div", { class: "ss-grid2" }, f("ชื่อลูกค้า", contact.contact_name), f("เบอร์โทร", contact.phone), f("เลขบัตรประชาชน", contact.id_card),
        f("ยืมถังวันที่", contact.loan_date), f("พิกัด / ที่อยู่", contact.gps, true), f("หมายเหตุ", contact.note, true)),
      reg ? el("p", { class: "muted small", text: `ทะเบียนลูกค้าเลขที่ ${reg.cust_no}` }) : null) : null,
    el("section", { class: "ss-sec" }, el("h4", { text: "ราคาต่อหน่วย (บาท) — ใส่เฉพาะที่ขาย" }),
      el("div", { class: "ss-prices" }, ...P.map((p, i) => el("label", {}, el("span", { class: "small", text: p.label }), prices[i])))),
    el("section", { class: "ss-sec" }, el("h4", { text: "ถังที่ให้ยืม — หนึ่งแถวต่อหนึ่งถัง" }), binRows,
      el("button", { class: "btn small", type: "button", text: "+ เพิ่มถัง", onclick: () => binRow() }),
      f("ถังอื่น / ไม่มีเลข", binsOther, true),
      c && (c.bins_clash || []).length ? el("p", { class: "warn-text small", text: `ใบงานเดิมเขียนถัง ${c.bins_clash.join(", ")} ไว้ที่ร้านนี้ด้วย แต่ถังนั้นอยู่ที่ร้านอื่น — ถ้าอยู่ร้านนี้จริง ให้เพิ่มถังนั้นในรายการด้านบน (ระบบจะย้ายมาและเก็บประวัติ)` }) : null),
    contactOk ? el("section", { class: "ss-sec" }, el("h4", { text: "รูป — บัตรประชาชน ใบเปิดลูกค้า ร้าน" }), photoBox,
      el("button", { class: "btn small", type: "button", text: "+ เพิ่มรูป", onclick: () => pick.click() }), pick)
      : el("p", { class: "muted small", text: "ข้อมูลผู้ติดต่อ เลขบัตร และรูป ดูได้เฉพาะคนที่มีเมนู ทะเบียนลูกค้า" }),
    msg,
    el("div", { class: "ss-dlg-foot" },
      c ? el("button", { class: "btn", type: "button", text: "เลิกส่งร้านนี้", onclick: drop }) : null,
      el("span", { class: "grow" }), saveBtn));
  document.body.append(dlg);
  dlg.showModal();
  if (!c) name.focus();
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

// ------------------------------------------------------------------ ใบงานคนขับ: the owner's own Word sheet, filled from IceBox
// Word = app/route_docx.py (their template, rows added); PDF = that Word file turned to PDF on the server; Excel = app/route_sheet.py.
async function ssSheetFile(path, kind) {
  const w = kind === "pdf" ? window.open("", "_blank") : null;
  if (kind === "pdf" && !w) { alert("เบราว์เซอร์บล็อกหน้าต่างใหม่ — อนุญาตป๊อปอัปของเว็บนี้แล้วลองใหม่"); return; }
  if (w) w.document.write("<p style='font-family:sans-serif;padding:16px'>กำลังทำ PDF… (ประมาณ 10–30 วินาที)</p>");
  let res;
  try {
    res = await fetch(path, { headers: { "X-App-Token": state.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  } catch (err) { if (w) w.close(); alert(err.message); return; }
  const url = URL.createObjectURL(await res.blob());
  if (w) { w.location.href = url; return; }
  const m = /filename\*=UTF-8''([^;]+)/.exec(res.headers.get("Content-Disposition") || "");
  const a = el("a", { href: url, download: m ? decodeURIComponent(m[1]) : `sheet.${kind}` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
const ssQ = (day) => (day ? `?day=${day}` : "");
const ssPrintSheet = (routeId, day) => ssSheetFile(`/api/sales-routes/${routeId}/sheet.pdf${ssQ(day)}`, "pdf");
const ssSheetDocx = (routeId, day) => ssSheetFile(`/api/sales-routes/${routeId}/sheet.docx${ssQ(day)}`, "docx");
const ssSheetXlsx = (routeId, day) => ssSheetFile(`/api/sales-routes/${routeId}/sheet.xlsx${ssQ(day)}`, "xlsx");
const ssPrintAll = (day = "", kind = "pdf") => ssSheetFile(`/api/sales-routes/all/sheet.${kind}${ssQ(day)}`, kind);
