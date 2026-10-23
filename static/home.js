"use strict";

// The home page (หน้าแรก): what needs the owner today, across the whole app. Data: ONE request, /api/home (app/home.py).
// A first draft built to grow: each card is an entry in HOME_CARDS (title, where "ดูทั้งหมด" goes, how to draw its data),
// so adding one is a function here plus a loader in app/home.py. Ideas not built yet come from the server as `soon`
// and are drawn as dashed cards.
// Loaded BEFORE app.js: nothing here runs at load time; helpers from app.js / board.js / dashboard.js are used at call time.

const home = { data: null, loading: false, error: "" };

const hStat = (value, label, tone = "") => el("div", { class: `hstat ${tone}`.trim() }, el("div", { class: "hstat-v", text: String(value) }), el("div", { class: "hstat-l", text: label }));
const hStats = (...stats) => el("div", { class: "hstats" }, ...stats);
const hEmpty = (text) => el("p", { class: "muted small hempty", text });
const hRow = (main, side, tone = "") => el("li", { class: `hrow ${tone}`.trim() }, el("span", { class: "hrow-main", text: main }), side ? el("span", { class: "hrow-side muted small", text: side }) : null);
const hList = (rows) => el("ul", { class: "hlist" }, ...rows);

// "+12 %" against last month, or a plain sentence when there is nothing to compare with
function vsLast(now, last) {
  if (!last) return now ? "เดือนก่อนไม่มี" : "";
  const pct = Math.round(((now - last) / last) * 100);
  return `เดือนก่อน ${baht(last)} (${pct >= 0 ? "+" : ""}${pct}%)`;
}

function planLeft(x) {
  const bits = [];
  if (x.km_left != null) bits.push(x.km_left < 0 ? `เลย ${baht(-x.km_left)} กม.` : `เหลือ ${baht(x.km_left)} กม.`);
  if (x.days_left != null) bits.push(x.days_left < 0 ? `เลย ${-x.days_left} วัน` : x.days_left === 0 ? "ครบวันนี้" : `อีก ${x.days_left} วัน`);
  if (x.together) bits.push("ทำพร้อมน้ำมันเครื่อง");
  return bits.join(" · ");
}

// ------------------------------------------------------------------ the cards
function drawToday(d) {
  const m = d.mgr || {}, p = d.plan || {};
  const out = [];
  if (m.ready) {
    out.push(el("h3", { class: "hsub", text: "บอร์ดของฉัน" }),
      hStats(hStat(m.overdue, "เลยกำหนด", m.overdue ? "bad" : ""), hStat(m.today, "วันนี้", m.today ? "warn" : ""), hStat(m.follow_ups, "ถึงวันติดตาม", m.follow_ups ? "warn" : ""), hStat(m.doing, "กำลังทำ")));
    // the same words as the daily digest: overdue / today / follow-ups / urgent, with the titles (first line is its own heading)
    const digest = (m.text || "").split(/\r?\n/).slice(1).join("\n").trim();
    if (digest) out.push(el("p", { class: "hdigest", text: digest }));
    else if (!m.total) out.push(hEmpty("ไม่มีงานค้างบนบอร์ด"));
  } else {
    out.push(hEmpty("บอร์ดของฉันยังไม่ได้เปิดใช้งาน"));
  }
  out.push(el("h3", { class: "hsub", text: "แผนซ่อมบำรุงรถ" }));
  if (!p.enabled) out.push(hEmpty("การแจ้งเตือนแผนซ่อมปิดอยู่ (ตั้งค่าที่แผนซ่อมบำรุง)"));
  else if (!p.total) out.push(hEmpty("ยังไม่มีรถที่ใกล้ถึงรอบ"));
  else {
    out.push(hStats(hStat(p.counts.overdue, "เลยกำหนด", p.counts.overdue ? "bad" : ""), hStat(p.counts.due, "ครบแล้ว", p.counts.due ? "warn" : ""), hStat(p.counts.soon, `ใกล้ถึง (${p.soon_days} วัน)`)),
      hList(p.items.slice(0, 6).map((x) => hRow(`${x.truck} · ${x.label}`, planLeft(x), x.stage === "overdue" ? "bad" : x.stage === "due" ? "warn" : ""))));
    if (p.total > 6) out.push(el("p", { class: "muted small hempty", text: `และอีก ${p.total - 6} รายการ` }));
  }
  return out;
}

function drawRepairs(d) {
  const out = [hStats(hStat(d.open, "รอซ่อม", d.open ? "warn" : ""), hStat(d.at_shop, "อยู่ที่อู่", d.longest_at_shop_days >= 14 ? "bad" : d.at_shop ? "warn" : ""),
    hStat(d.awaiting_bill, "รอบิล", d.oldest_awaiting_days >= 7 ? "bad" : d.awaiting_bill ? "warn" : ""))];
  if (d.at_shop_list.length) {
    out.push(hList(d.at_shop_list.map((r) => hRow(`${r.truck ? "#" + r.truck : r.plate || "รถ"}${r.shop ? " · " + r.shop : ""}`, `${r.days} วัน`, r.days >= 14 ? "bad" : ""))));
  } else if (!d.open && !d.awaiting_bill) out.push(hEmpty("ไม่มีงานค้าง รถทุกคันอยู่กับที่"));
  return out;
}

function drawSpend(d) {
  const r = d.repairs, p = d.purchases;
  const out = [hStats(hStat(`${baht(r.this)} ฿`, "ค่าซ่อมเดือนนี้"), hStat(`${baht(r.last)} ฿`, "เดือนก่อน"))];
  out.push(el("p", { class: "muted small", text: `${r.count_this} บิล/บันทึกเดือนนี้ · ${vsLast(r.this, r.last)}`.replace(/ · $/, "") }));
  if (p.ready) {
    out.push(el("h3", { class: "hsub", text: "ซื้อของโรงงาน" }), hStats(hStat(`${baht(p.this)} ฿`, "เดือนนี้"), hStat(`${baht(p.last)} ฿`, "เดือนก่อน")),
      el("p", { class: "muted small", text: `${p.count_this} บิลเดือนนี้ · ${vsLast(p.this, p.last)}`.replace(/ · $/, "") }));
  }
  return out;
}

function drawStock(d) {
  if (!d.tracked) return [hEmpty("ยังไม่ได้ตั้งจำนวนขั้นต่ำให้ของชิ้นไหน — ตั้งที่หน้าสต็อกแล้วที่นี่จะเตือนเมื่อของใกล้หมด")];
  if (!d.low_count) return [hEmpty(`ของที่ติดตามไว้ ${d.tracked} รายการ ยังพอทุกอย่าง`)];
  const out = [hList(d.low.map((x) => hRow(x.name, `เหลือ ${x.on_hand} / ขั้นต่ำ ${x.min_qty}${x.unit ? " " + x.unit : ""}`, x.on_hand <= 0 ? "bad" : "warn")))];
  if (d.low_count > d.low.length) out.push(hEmpty(`และอีก ${d.low_count - d.low.length} รายการ`));
  return out;
}

function drawMachinery(d) {
  const out = [hStats(hStat(d.machines, "เครื่องที่ใช้งาน"), hStat(d.events_recent, `งานซ่อมบำรุง ${d.recent_days} วัน`))];
  if (d.latest.length) out.push(hList(d.latest.map((e) => hRow(`${e.where ? e.where + " · " : ""}${e.summary}`, `${e.kind || ""} ${beDate(e.date)}`.trim()))));
  else out.push(hEmpty("ยังไม่มีบันทึกซ่อมบำรุง"));
  return out;
}

// key -> title, where the link goes, how to draw it. The page shows them in this order.
const HOME_CARDS = [
  { key: "today", title: "วันนี้ต้องดู", wide: true, go: ["บอร์ดของฉัน", () => goHash("#mgr")], draw: drawToday },
  { key: "repairs", title: "รถ — งานซ่อม", go: ["สถานะงานซ่อม", () => { showView("board"); loadDueStrip(); }], draw: drawRepairs },
  { key: "spend", title: "ค่าใช้จ่าย", go: ["ภาพรวม", () => { showView("dash"); loadDashboard(); }], draw: drawSpend },
  { key: "stock", title: "สต็อก — ของใกล้หมด", go: ["สต็อก", () => goHash("#stock")], draw: drawStock },
  { key: "machinery", title: "เครื่องจักร", go: ["บันทึกซ่อมบำรุง", () => goHash("#machine-log")], draw: drawMachinery },
];

function homeCard(spec, data) {
  const head = el("div", { class: "hcard-head" }, el("h2", { text: spec.title }),
    el("button", { class: "hlink", type: "button", text: `${spec.go[0]} →`, onclick: spec.go[1] }));
  let body;
  if (!data) body = [hEmpty("ไม่มีข้อมูล")];
  else if (data.error) body = [el("p", { class: "error small", text: "โหลดส่วนนี้ไม่สำเร็จ — กดรีเฟรชเพื่อลองอีกครั้ง" })];
  else if (data.ready === false && spec.key !== "today") body = [hEmpty("ส่วนนี้ยังไม่ได้เปิดใช้ (ยังไม่ได้รันสคีมาในฐานข้อมูล)")];
  else body = spec.draw(data);
  return el("section", { class: `card hcard${spec.wide ? " wide" : ""}` }, head, ...body);
}

function soonCard(s) {
  return el("section", { class: "hcard hsoon", title: `ต้องมีก่อน: ${s.needs}` },
    el("div", { class: "hsoon-tag", text: "เร็วๆ นี้" }), el("h2", { text: s.title }), el("p", { class: "muted small", text: s.blurb }),
    el("p", { class: "hsoon-needs small", text: `ต้องมีก่อน: ${s.needs}` }));
}

function renderHome() {
  const d = home.data;
  $("homeAsOf").textContent = d ? `ข้อมูล ณ ${beDate(d.as_of)}` : "";
  showErr("homeError", home.error);
  if (!d) return;
  $("homeCards").replaceChildren(...HOME_CARDS.map((c) => homeCard(c, d[c.key])));
  $("homeSoonHead").hidden = !d.soon.length;
  $("homeSoon").replaceChildren(...d.soon.map(soonCard));
}

async function loadHome(fresh = false) {
  if (home.loading) return;
  home.loading = true;
  $("homeRefresh").disabled = true;
  try {
    home.data = await api(`/api/home${fresh ? "?fresh=true" : ""}`);
    home.error = "";
  } catch (err) {
    home.error = err.message;                  // keep showing the last numbers, if any, under the message
  } finally {
    home.loading = false;
    $("homeRefresh").disabled = false;
  }
  renderHome();
}

function initHome() {
  $("homeRefresh").addEventListener("click", () => loadHome(true));
  $("navHome").addEventListener("click", () => { showView("home"); loadHome(); });
}
