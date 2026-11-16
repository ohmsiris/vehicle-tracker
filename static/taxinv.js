"use strict";

// ใบกำกับภาษี แก่งคอย (owner, 16 Nov 2026): the bookkeeper types each tax invoice / credit note; the invoice number tells the customer and the
// price (as the Google Sheet's formula did). Top: the fill-in box. Then the month's figures by customer, the checks (numbers used twice, in
// another month, missing), and the list -- filtered by customer, every row editable. Server: app/tax_invoices.py.
// Hash: #tax-invoices/<YYYY-MM>.

const ti_ = { data: null, month: null, to: null, sel: new Set(), pick: new Set(), q: "", kind: "invoice", editing: null, date: null, showSetup: false };
const tiPicked = (code) => !ti_.sel.size || ti_.sel.has(code || "?");
const tiB = (v, d = 2) => elN(v, d);
const tiDMY = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${Number(iso.slice(0, 4)) + 543}` : "–");
const tiMonthTH = (m) => `${ELEC_TH[Number(m.slice(5, 7)) - 1]} ${Number(m.slice(0, 4)) + 543}`;
const tiShift = (m, n) => { let y = Number(m.slice(0, 4)), mo = Number(m.slice(5, 7)) + n; while (mo < 1) { mo += 12; y -= 1; } while (mo > 12) { mo -= 12; y += 1; } return `${y}-${String(mo).padStart(2, "0")}`; };
const tiNum = (v) => { const t = String(v ?? "").replace(/,/g, "").trim(); return t === "" ? null : Number(t); };
const tiRound = (x) => Math.round((x + 1e-9) * 100) / 100;

// the same reading of a number as app/tax_invoices.parse_number
function tiParse(text) {
  const t = String(text || "").replace("เลขที่", " ").trim();
  let m = t.match(/CN\s*(\d{4})\s*\/\s*(\d+)/i);
  if (m) return { doc_no: `CN${m[1]}/${m[2]}`, kind: "credit_note", code: null };
  m = t.match(/(SRI|IV)\s*-\s*(\d{4})\s*-\s*(\d+)(?:\s*-\s*(\d+))?/i);
  if (!m) return null;
  const pre = m[1].toUpperCase();
  if (m[4] != null) return { doc_no: `${pre}-${m[2]}-${m[3]}-${m[4]}`, kind: "invoice", code: m[3].slice(0, 2) };
  return { doc_no: `${pre}-${m[2]}-${m[3]}`, kind: "invoice", code: m[3].length === 5 ? "P" : m[3].slice(0, 2) };
}
function tiNext(docNo) {                              // SRI-2569-0710001 -> SRI-2569-0710002, CN2569/010 -> CN2569/011
  const m = String(docNo || "").match(/^(.*?)(\d+)$/);
  if (!m) return "";
  return `${m[1]}${String(Number(m[2]) + 1).padStart(m[2].length, "0")}`;
}
function tiPriceOn(code, isoDay) {
  let best = null;
  for (const p of ti_.data.prices) if (p.code === code && p.from_date <= isoDay && (!best || p.from_date > best.from_date)) best = p;
  return best ? Number(best.price) : null;
}
const tiCust = (code) => ti_.data.customers.find((c) => c.code === code) || null;

async function openTaxInvoices(sub = "") {
  showView("taxInvoices");
  showErr("tiError", "");
  const m = (sub || "").match(/^(\d{4}-\d{2})(?:\.\.(\d{4}-\d{2}))?$/);
  const month = m ? m[1] : null, to = m && m[2] ? m[2] : null;
  if (!ti_.data) fill($("tiBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { ti_.data = await api(`/api/tax-invoices${month ? `?month=${month}${to ? `&to=${to}` : ""}` : ""}`); }
  catch (err) { fill($("tiBody"), null); showErr("tiError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  if (ti_.month !== ti_.data.month || ti_.to !== ti_.data.to) { ti_.q = ""; ti_.editing = null; }
  const keep = ti_.month === ti_.data.month && !!document.getElementById("tbMaker");
  ti_.month = ti_.data.month; ti_.to = ti_.data.to;
  tiRender(keep);
}

async function tiReload() {
  try { ti_.data = await api(`/api/tax-invoices?month=${ti_.month}${ti_.to && ti_.to !== ti_.month ? `&to=${ti_.to}` : ""}`); } catch (err) { showErr("tiError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  tiRender(true);
}

function tiRender(keepEntry = false) {
  const d = ti_.data, s = d.summary;
  const months = [...new Set([...d.months, d.month, d.to])].sort();
  const range = d.to && d.to !== d.month;
  const go = (m, to) => goHash(`#tax-invoices/${m}${to && to !== m ? `..${to}` : ""}`);
  const bar = el("div", { class: "ti-monthbar" },
    el("button", { class: "btn small", type: "button", text: "‹", title: "เดือนก่อน", onclick: () => go(tiShift(d.month, -1)) }),
    el("select", { class: "ti-month", "aria-label": "เดือน", onchange: (e) => go(e.target.value, range ? d.to : null) },
      ...months.slice().reverse().map((m) => el("option", { value: m, text: tiMonthTH(m), selected: m === d.month }))),
    el("span", { class: "small muted", text: "ถึง" }),
    el("select", { class: "ti-month", "aria-label": "ถึงเดือน", onchange: (e) => go(d.month, e.target.value) },
      ...months.slice().reverse().filter((m) => m >= d.month).map((m) => el("option", { value: m, text: tiMonthTH(m), selected: m === d.to }))),
    el("button", { class: "btn small", type: "button", text: "›", title: "เดือนถัดไป", onclick: () => go(tiShift(d.month, 1)) }),
    range ? el("span", { class: "small muted", text: "ช่องออกบิลใช้เดือนแรก" }) : null);
  const entry = keepEntry && document.getElementById("tbMaker") ? document.getElementById("tbMaker") : tbMaker();
  fill($("tiBody"), bar, entry, tiList(), tiSummary(s), tiChecks(s), tiSetup());
}

// ------------------------------------------------------------------ the fill-in box
function tiEntry() {
  const d = ti_.data;
  const today = new Date().toISOString().slice(0, 10);
  if (!ti_.date || ti_.date.slice(0, 7) !== d.month) ti_.date = today.slice(0, 7) === d.month ? today : `${d.month}-01`;
  const kindSeg = el("div", { class: "seg ti-kind" },
    ...[["invoice", "ใบกำกับภาษี"], ["credit_note", "ใบลดหนี้"]].map(([k, text]) => el("button", { type: "button", class: `segbtn${ti_.kind === k ? " active" : ""}`, text,
      onclick: () => { ti_.kind = k; const box = tiEntry(); $("tiEntry").replaceWith(box); } })));
  const f = (label, node, cls = "") => el("label", { class: `ti-f ${cls}` }, el("span", { class: "small muted", text: label }), node);
  const date = el("input", { type: "date", id: "tiDate", value: ti_.date });
  const no = el("input", { type: "text", id: "tiNo", placeholder: ti_.kind === "credit_note" ? "CN2569/011" : "SRI-2569-10001", autocomplete: "off",
    value: ti_.kind === "invoice" && d.last && d.last.next && !d.last.next.startsWith("CN") ? d.last.next : "" });
  const cust = el("select", { id: "tiCust" }, el("option", { value: "", text: "— จากเลขที่ —" }),
    ...d.customers.filter((c) => c.active !== false).map((c) => el("option", { value: c.code, text: `${c.code === "P" ? "" : `${c.code} · `}${c.name}` })));
  const kg = el("input", { type: "text", id: "tiKg", inputmode: "decimal", autocomplete: "off" });
  const price = el("input", { type: "text", id: "tiPrice", inputmode: "decimal", autocomplete: "off" });
  const amount = el("input", { type: "text", id: "tiAmount", inputmode: "decimal", autocomplete: "off" });
  const note = el("input", { type: "text", id: "tiNote", autocomplete: "off", placeholder: "ถ้ามี" });
  const info = el("div", { class: "ti-info small" });
  const kgBox = f("น้ำหนัก (กก.)", kg), priceBox = f("ราคาต่อ กก.", price), amountBox = f("ยอดก่อน VAT (บาท)", amount);
  let priceAuto = true, custAuto = true;
  const refresh = () => {
    const p = tiParse(no.value);
    if (custAuto) cust.value = p && p.code && tiCust(p.code) ? p.code : "";
    const c = tiCust(cust.value);
    const byTotal = c && c.mode === "total";
    const cn = ti_.kind === "credit_note";
    kgBox.hidden = byTotal; priceBox.hidden = byTotal;
    amountBox.hidden = !(byTotal || cn);
    amountBox.querySelector("span").textContent = cn && !byTotal ? "หรือ ยอดก่อน VAT (ถ้าไม่มีน้ำหนัก)" : "ยอดก่อน VAT (บาท)";
    if (priceAuto) { const pr = c && !byTotal ? tiPriceOn(c.code, date.value || `${d.month}-01`) : null; price.value = pr == null ? "" : String(pr); }
    const k = tiNum(kg.value), pr = tiNum(price.value), a = tiNum(amount.value);
    let base = null;
    if (byTotal || (cn && a != null && k == null)) base = a; else if (k != null && pr != null) base = k * pr;
    const parts = [];
    if (no.value.trim() && !p) parts.push(el("span", { class: "ti-bad", text: "เลขที่ไม่ถูกต้อง" }));
    else if (p && p.kind !== ti_.kind) parts.push(el("span", { class: "ti-bad", text: p.kind === "credit_note" ? "เลขนี้เป็นใบลดหนี้ — เลือกประเภท ใบลดหนี้" : "เลขนี้เป็นใบกำกับภาษี — เลือกประเภท ใบกำกับภาษี" }));
    else if (p && p.code && !tiCust(p.code)) parts.push(el("span", { class: "ti-bad", text: `ไม่รู้จักรหัสลูกค้า ${p.code} — เพิ่มที่ “ลูกค้าและราคา” ด้านล่าง` }));
    else if (c) parts.push(el("b", { text: c.name }), byTotal ? el("span", { class: "muted", text: " · ใส่ยอดรวมของเดือน" }) : null);
    if (base != null && Number.isFinite(base)) {
      const a2 = tiRound(base), v = tiRound(a2 * 0.07);
      parts.push(el("span", { text: `  ก่อน VAT ${tiB(a2)} + VAT ${tiB(v)} = ` }), el("b", { text: `${tiB(tiRound(a2 + v))} บาท` }));
    }
    fill(info, ...parts);
  };
  no.addEventListener("input", () => { custAuto = true; priceAuto = true; refresh(); });
  cust.addEventListener("change", () => { custAuto = !cust.value; priceAuto = true; refresh(); });
  price.addEventListener("input", () => { priceAuto = price.value === ""; refresh(); });
  [kg, amount].forEach((n) => n.addEventListener("input", refresh));
  date.addEventListener("change", () => { ti_.date = date.value; priceAuto = true; refresh(); });
  const submit = async () => {
    showErr("tiError", "");
    const body = { doc_no: no.value, doc_date: date.value, customer_code: cust.value || null, kg: kg.value, price: price.value,
                   amount: amount.value, note: note.value };
    btn.disabled = true;
    try {
      const row = await api("/api/tax-invoices", jsonOpts("POST", body));
      ti_.date = date.value;
      const nxt = tiNext(row.doc_no);
      if (row.month !== ti_.month) { ti_.data.last = { doc_no: row.doc_no, next: nxt }; goHash(`#tax-invoices/${row.month}`); return; }
      await tiReload();
      $("tiNo").value = nxt; $("tiKg").value = ""; $("tiAmount").value = ""; $("tiNote").value = "";
      $("tiNo").dispatchEvent(new Event("input"));
      const ok = $("tiOk"); if (ok) ok.textContent = `บันทึก ${row.doc_no} แล้ว`;
      ($("tiKg").closest("label").hidden ? $("tiAmount") : $("tiKg")).focus();
    } catch (err) { showErr("tiError", err.message); }
    finally { btn.disabled = false; }
  };
  const btn = el("button", { class: "btn primary", type: "button", text: "เพิ่ม", onclick: submit });
  const box = el("section", { class: "card ti-entry", id: "tiEntry" },
    el("div", { class: "ti-entry-head" }, el("h3", { text: "กรอกใบใหม่" }), kindSeg),
    el("div", { class: "ti-fields", onkeydown: (e) => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); submit(); } } },
      f("วันที่", date), f("เลขที่", no, "ti-wide"), f("ลูกค้า", cust, "ti-wide"), kgBox, priceBox, amountBox, f("หมายเหตุ", note, "ti-wide"),
      el("div", { class: "ti-go" }, btn)),
    info, el("div", { id: "tiOk", class: "small ti-ok" }));
  queueMicrotask(refresh);
  return box;
}

// ------------------------------------------------------------------ checks and the summary by customer
function tiChecks(s) {
  const items = [];
  if (s.duplicates.length) items.push(el("li", {}, el("b", { text: "เลขที่ซ้ำในเดือนนี้: " }), s.duplicates.join(", ")));
  if (s.other_months && s.other_months.length) {
    const shown = s.other_months.slice(0, 8).map((x) => `${x.doc_no} (${x.months.map(tiMonthTH).join(", ")})`);
    items.push(el("li", {}, el("b", { text: `เลขที่ที่มีในเดือนอื่นด้วย ${s.other_months.length} ใบ: ` }), shown.join(", ") + (s.other_months.length > 8 ? " …" : "")));
  }
  s.gaps.forEach((g) => items.push(el("li", {}, el("b", { text: `เลขที่ขาด (${g.series}…): ` }), g.missing.join(", "))));
  const bad = ti_.data.rows.filter((r) => r.kind === "invoice" && !r.customer_code);
  if (bad.length) items.push(el("li", {}, el("b", { text: "ไม่มีลูกค้า: " }), bad.map((r) => r.doc_no).join(", ")));
  if (!items.length) return null;
  return el("section", { class: "card ti-checks" }, el("h3", { text: "ควรตรวจสอบ" }), el("ul", {}, ...items));
}

function tiToggle(code) {
  if (code === "all") ti_.sel = new Set(); else if (ti_.sel.has(code)) ti_.sel.delete(code); else ti_.sel.add(code);
  tiRender(true);
}

// the summary: pick any customers (all, just CP, X + Y + Z) -- the tiles, the table and the list below follow
function tiSummary(s) {
  const period = ti_.to && ti_.to !== ti_.month ? `${tiMonthTH(ti_.month)} – ${tiMonthTH(ti_.to)}` : tiMonthTH(ti_.month);
  const chips = el("div", { class: "dd-chips" },
    el("button", { type: "button", class: `dd-chip${!ti_.sel.size ? " on" : ""}`, onclick: () => tiToggle("all") }, el("span", { text: "ทุกลูกค้า" })),
    ...s.customers.map((c) => el("button", { type: "button", class: `dd-chip${ti_.sel.has(c.code) ? " on" : ""}`, onclick: () => tiToggle(c.code) },
      el("span", { text: c.name }))));
  const sel = s.customers.filter((c) => tiPicked(c.code));
  const t = { total: 0, amount: 0, vat: 0, kg: 0, invoices: 0, credit_notes: 0 };
  sel.forEach((c) => Object.keys(t).forEach((k) => { t[k] += Number(c[k]) || 0; }));
  const tile = (label, value, sub) => el("div", { class: "lc-tile" }, el("div", { class: "small muted", text: label }), el("b", { text: value }), sub ? el("div", { class: "small muted", text: sub }) : null);
  const tiles = el("div", { class: "lc-tiles" },
    tile("ยอดเงินสุทธิ (รวม VAT)", `฿${tiB(t.total)}`, `ก่อน VAT ${tiB(t.amount)} · VAT ${tiB(t.vat)}`),
    tile("น้ำหนักรวม", `${tiB(t.kg, 0)} กก.`, "ไม่รวมซันฟู้ด (ยอดรวม)"),
    tile("จำนวนใบ", `${t.invoices} ใบ`, t.credit_notes ? `ใบลดหนี้ ${t.credit_notes} ใบ` : "ไม่มีใบลดหนี้"));
  return el("section", { class: "card ti-sumcard" }, el("h3", { text: `สรุป · ${period}` }),
    el("p", { class: "small muted", text: "เลือกได้หลายลูกค้า — กดอีกครั้งเพื่อเอาออก" }), chips, tiles, tiByCustomer(s));
}

function tiByCustomer(s) {
  if (!s.customers.length) return el("p", { class: "muted", text: "ยังไม่มีใบในช่วงนี้" });
  const shown = s.customers.filter((c) => tiPicked(c.code));
  const max = Math.max(1, ...shown.map((c) => Math.abs(c.total)));
  const rows = shown.map((c) => el("tr", { class: "ti-crow" },
    el("td", {}, el("b", { text: c.name })),
    el("td", { class: "r", text: `${c.invoices}${c.credit_notes ? ` (−${c.credit_notes})` : ""}` }),
    el("td", { class: "r", text: c.kg ? tiB(c.kg, 0) : "–" }),
    el("td", { class: "r", text: tiB(c.amount) }), el("td", { class: "r", text: tiB(c.vat) }),
    el("td", { class: "r" }, el("b", { text: tiB(c.total) })),
    el("td", { class: "ti-barcell" }, el("div", { class: "ti-bar", style: `width:${Math.round(Math.abs(c.total) / max * 100)}%` }))));
  const t = { invoices: 0, kg: 0, amount: 0, vat: 0, total: 0 };
  shown.forEach((c) => Object.keys(t).forEach((k) => { t[k] += Number(c[k]) || 0; }));
  return el("div", {},
    el("div", { class: "lc-wrap" }, el("table", { class: "lc-table ti-sum" },
      el("thead", {}, el("tr", {}, ...["ลูกค้า", "ใบ", "น้ำหนัก (กก.)", "ก่อน VAT", "VAT 7%", "ยอดเงินสุทธิ", ""].map((h, i) => el("th", { class: i && i < 6 ? "r" : "", text: h })))),
      el("tbody", {}, ...rows),
      el("tfoot", {}, el("tr", {}, el("td", { text: "รวม" }), el("td", { class: "r", text: String(t.invoices) }), el("td", { class: "r", text: tiB(t.kg, 0) }),
        el("td", { class: "r", text: tiB(t.amount) }), el("td", { class: "r", text: tiB(t.vat) }), el("td", { class: "r" }, el("b", { text: tiB(t.total) })), el("td", {}))))),
    el("p", { class: "small muted", text: "ใบลดหนี้หักออกจากยอดของลูกค้าแล้ว" }));
}

// ------------------------------------------------------------------ the list
function tiList() {
  const d = ti_.data;
  const counts = {};
  d.rows.forEach((r) => { const k = r.customer_code || "?"; counts[k] = (counts[k] || 0) + 1; });
  const chip = (key, text) => el("button", { type: "button", class: `dd-chip${key === "all" ? (!ti_.sel.size ? " on" : "") : (ti_.sel.has(key) ? " on" : "")}`, onclick: () => tiToggle(key) },
    el("span", { text }), el("span", { class: "dd-count", text: String(key === "all" ? d.rows.length : counts[key] || 0) }));
  const chips = el("div", { class: "dd-chips" }, chip("all", "ทั้งหมด"),
    ...Object.keys(counts).sort((a, b) => (counts[b] - counts[a])).map((k) => chip(k, (tiCust(k) || {}).name || "ไม่ระบุลูกค้า")));
  const q = el("input", { type: "text", class: "ti-search", placeholder: "ค้นหาเลขที่ / หมายเหตุ", value: ti_.q,
    oninput: (e) => { ti_.q = e.target.value; const tb = $("tiRows"); if (tb) tb.replaceWith(tiRows()); } });
  return el("section", { class: "card" }, el("div", { class: "ti-listhead" }, el("h3", { text: "รายการใบกำกับภาษี / ใบลดหนี้" }), q), chips,
    el("div", { id: "tiPickBar" }, tiPickBar()),
    el("div", { class: "lc-wrap" }, el("table", { class: "lc-table ti-list" },
      el("thead", {}, el("tr", {}, ...["", "เลขที่", "วันที่", "ลูกค้า", "น้ำหนัก", "ราคา", "ก่อน VAT", "VAT", "สุทธิ", "หมายเหตุ", "พิมพ์แล้ว", ""].map((h, i) => el("th", { class: i >= 4 && i <= 8 ? "r" : "", text: h })))),
      tiRows())));
}

const tiPrintable = (r) => r.kind === "invoice" && r.doc_date && r.kg != null && r.price != null && ((ti_.data.templates || {})[r.customer_code] || {}).bill;

// the bar over the list: what is ticked to print, and the buttons (one Excel tab for all of them; then tick พิมพ์แล้ว)
function tiPickBar() {
  const rows = ti_.data.rows.filter((r) => ti_.pick.has(r.id));
  const codes = new Set(rows.map((r) => r.customer_code));
  const shown = ti_.data.rows.filter((r) => tiPicked(r.customer_code) && tiPrintable(r) && !r.printed_at);
  const pickUnprinted = el("button", { class: "btn small", type: "button", text: `เลือกบิลที่ยังไม่พิมพ์ (${shown.length})`, disabled: !shown.length,
    onclick: () => { shown.forEach((r) => ti_.pick.add(r.id)); tiRedrawList(); } });
  if (!rows.length) return el("div", { class: "ti-pickbar" }, el("span", { class: "small muted", text: "ติ๊กช่องหน้าบิลเพื่อพิมพ์หลายใบในครั้งเดียว" }), pickUnprinted);
  const dl = el("button", { class: "btn primary", type: "button", text: `ดาวน์โหลดเพื่อพิมพ์ (${rows.length} ใบ)`, disabled: codes.size > 1,
    onclick: async () => {
      const name = (tiCust([...codes][0]) || {}).name || "";
      await tbDownload(`/api/tax-invoices/bills.xlsx?ids=${rows.map((r) => r.id).join(",")}`, `บิล ${name} ${rows.length} ใบ.xlsx`, dl);
    } });
  const mark = el("button", { class: "btn small", type: "button", text: "ติ๊กพิมพ์แล้ว", onclick: () => tiSetPrinted(rows.map((r) => r.id), true) });
  const clear = el("button", { class: "btn small", type: "button", text: "ไม่เลือก", onclick: () => { ti_.pick.clear(); tiRedrawList(); } });
  return el("div", { class: "ti-pickbar" }, el("b", { text: `เลือก ${rows.length} ใบ` }),
    codes.size > 1 ? el("span", { class: "ti-bad small", text: "เลือกได้ทีละลูกค้า" }) : null, dl, mark, clear, pickUnprinted);
}

function tiRedrawList() {
  const tb = $("tiRows"); if (tb) tb.replaceWith(tiRows());
  const bar = $("tiPickBar"); if (bar) fill(bar, tiPickBar());
}

async function tiSetPrinted(ids, printed) {
  try {
    const res = await api("/api/tax-invoices/printed", jsonOpts("POST", { ids, printed }));
    ti_.data.rows.forEach((r) => { if (ids.includes(r.id)) r.printed_at = res.printed_at; });
    if (printed) ids.forEach((i) => ti_.pick.delete(i));
    tiRedrawList();
  } catch (err) { showErr("tiError", err.message); }
}

function tiRows() {
  const q = ti_.q.trim().toLowerCase();
  const all = ti_.data.rows.filter((r) => tiPicked(r.customer_code)
    && (!q || r.doc_no.toLowerCase().includes(q) || (r.note || "").toLowerCase().includes(q) || (r.ticket_no || "").toLowerCase().includes(q)));
  const LIMIT = 600, rows = all.slice(0, LIMIT);
  const body = el("tbody", { id: "tiRows" });
  rows.forEach((r) => body.append(ti_.editing === r.id ? tiEditRow(r) : tiRow(r)));
  if (all.length > LIMIT) body.append(el("tr", {}, el("td", { colspan: "12", class: "muted small", text: `แสดง ${LIMIT} จาก ${all.length} ใบ — เลือกลูกค้าหรือค้นหาเลขที่เพื่อให้แคบลง` })));
  if (!rows.length) body.append(el("tr", {}, el("td", { colspan: "12", class: "muted", text: "ไม่มีรายการ" })));
  return body;
}

function tiRow(r) {
  const cn = r.kind === "credit_note", sign = cn ? -1 : 1;
  return el("tr", { class: cn ? "ti-cn" : "" },
    el("td", {}, tiPrintable(r) ? el("input", { type: "checkbox", checked: ti_.pick.has(r.id), title: "เลือกเพื่อพิมพ์",
      onchange: (e) => { if (e.target.checked) ti_.pick.add(r.id); else ti_.pick.delete(r.id); const bar = $("tiPickBar"); if (bar) fill(bar, tiPickBar()); } }) : null),
    el("td", { class: "nowrap" }, cn ? el("span", { class: "badge", text: "ใบลดหนี้" }) : null, ` ${r.doc_no}`),
    el("td", { class: "nowrap", text: tiDMY(r.doc_date) }),
    el("td", { text: (tiCust(r.customer_code) || {}).name || r.customer_name || "–" }),
    el("td", { class: "r", text: r.kg != null ? tiB(r.kg, 0) : "–" }),
    el("td", { class: "r", text: r.price != null ? String(Number(r.price)) : "–" }),
    el("td", { class: "r", text: tiB(sign * r.amount) }), el("td", { class: "r", text: tiB(sign * r.vat) }),
    el("td", { class: "r" }, el("b", { text: tiB(sign * r.total) })),
    el("td", { class: "small muted", text: r.note || "" }),
    el("td", { class: "ti-printed" }, r.kind === "invoice" ? el("input", { type: "checkbox", checked: !!r.printed_at, title: r.printed_at ? `พิมพ์แล้ว ${tiDMY(r.printed_at.slice(0, 10))}` : "ยังไม่พิมพ์",
      onchange: (e) => tiSetPrinted([r.id], e.target.checked) }) : null),
    el("td", {}, el("button", { class: "btn small", type: "button", text: "แก้ไข", onclick: () => { ti_.editing = r.id; $("tiRows").replaceWith(tiRows()); } })));
}

function tiEditRow(r) {
  const inp = (v, attrs = {}) => el("input", { type: "text", value: v == null ? "" : String(v), autocomplete: "off", ...attrs });
  const no = inp(r.doc_no), date = el("input", { type: "date", value: r.doc_date || "" });
  const cust = el("select", {}, el("option", { value: "", text: "— ไม่ระบุ —" }),
    ...ti_.data.customers.map((c) => el("option", { value: c.code, text: c.name, selected: c.code === r.customer_code })));
  const kg = inp(r.kg, { inputmode: "decimal" }), price = inp(r.price == null ? "" : Number(r.price), { inputmode: "decimal" });
  const amount = inp(r.amount, { inputmode: "decimal", title: "ยอดก่อน VAT — ใช้เมื่อไม่มีน้ำหนัก (ซันฟู้ด / ใบลดหนี้)" });
  const note = inp(r.note);
  const save = async () => {
    showErr("tiError", "");
    const c = ti_.data.customers.find((x) => x.code === cust.value);
    const byAmount = (c && c.mode === "total") || (r.kind === "credit_note" && tiNum(kg.value) == null);
    const body = { doc_no: no.value, doc_date: date.value || null, month: r.month, customer_code: cust.value || null, kg: kg.value, price: price.value,
                   amount: byAmount ? amount.value : null, note: note.value };
    try { await api(`/api/tax-invoices/${r.id}`, jsonOpts("PUT", body)); ti_.editing = null; await tiReload(); }
    catch (err) { showErr("tiError", `บันทึกไม่สำเร็จ: ${err.message}`); }
  };
  const del = async () => {
    if (!confirm(`ลบ ${r.doc_no}?`)) return;
    try { await api(`/api/tax-invoices/${r.id}`, { method: "DELETE" }); ti_.editing = null; await tiReload(); }
    catch (err) { showErr("tiError", `ลบไม่สำเร็จ: ${err.message}`); }
  };
  return el("tr", { class: "ti-editrow", onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); save(); } if (e.key === "Escape") { ti_.editing = null; $("tiRows").replaceWith(tiRows()); } } },
    el("td", {}), el("td", {}, no), el("td", {}, date), el("td", {}, cust), el("td", { class: "r" }, kg), el("td", { class: "r" }, price),
    el("td", { class: "r", colspan: "3" }, amount), el("td", {}, note), el("td", {}),
    el("td", { class: "nowrap" },
      el("button", { class: "btn small primary", type: "button", text: "บันทึก", onclick: save }), " ",
      el("button", { class: "btn small", type: "button", text: "ยกเลิก", onclick: () => { ti_.editing = null; $("tiRows").replaceWith(tiRows()); } }), " ",
      el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: del })),
  );
}

// ------------------------------------------------------------------ customers and prices
function tiSetup() {
  const d = ti_.data;
  const head = el("button", { class: "btn small", type: "button", text: ti_.showSetup ? "ซ่อน" : "ดู / แก้ไข", onclick: () => { ti_.showSetup = !ti_.showSetup; tiRender(true); } });
  const card = el("section", { class: "card" }, el("div", { class: "ti-listhead" }, el("h3", { text: "ลูกค้าและราคา" }), head),
    el("p", { class: "small muted", text: "รหัสลูกค้า = เลข 2 หลักหลังปีในเลขที่ (SRI-2569-07… = 07) · เลขที่ที่มี 5 หลักหลังปี = CP ซิลเลอร์ · ราคาใหม่ใช้ตั้งแต่วันที่ที่ใส่" }));
  if (!ti_.showSetup) return card;
  const rows = d.customers.map((c) => {
    const hist = d.prices.filter((p) => p.code === c.code).slice().reverse();
    return el("tr", {},
      el("td", { class: "nowrap", text: c.code === "P" ? "5 หลัก" : c.code }), el("td", {}, el("b", { text: c.name })),
      el("td", { text: c.mode === "total" ? "ยอดรวม" : `ต่อ ${c.unit}` }),
      el("td", {}, ...hist.map((p) => el("div", { class: "small ti-price" }, el("b", { text: String(Number(p.price)) }), ` ตั้งแต่ ${tiDMY(p.from_date)} `,
        el("button", { class: "linkbtn small", type: "button", text: "ลบ", onclick: async () => {
          if (!confirm(`ลบราคา ${Number(p.price)} ของ ${c.name}?`)) return;
          try { await api(`/api/tax-invoices-setup/prices/${p.id}`, { method: "DELETE" }); await tiReload(); } catch (err) { showErr("tiError", err.message); } } }))),
        c.mode === "total" ? el("span", { class: "small muted", text: "ไม่ใช้ราคา" }) : null));
  });
  const pc = el("select", {}, ...d.customers.filter((c) => c.mode !== "total").map((c) => el("option", { value: c.code, text: c.name })));
  const pp = el("input", { type: "text", inputmode: "decimal", placeholder: "0.51", class: "ti-small" });
  const pf = el("input", { type: "date", value: `${d.month}-01` });
  const addPrice = el("div", { class: "ti-addrow" }, el("b", { class: "small", text: "ราคาใหม่: " }), pc, pp, el("span", { class: "small muted", text: "ตั้งแต่" }), pf,
    el("button", { class: "btn small primary", type: "button", text: "เพิ่มราคา", onclick: async () => {
      try { await api("/api/tax-invoices-setup/prices", jsonOpts("POST", { code: pc.value, price: pp.value, from_date: pf.value })); await tiReload(); }
      catch (err) { showErr("tiError", err.message); } } }));
  const cc = el("input", { type: "text", placeholder: "รหัส เช่น 15", class: "ti-small" }), cn = el("input", { type: "text", placeholder: "ชื่อลูกค้า" });
  const cm = el("select", {}, el("option", { value: "kg", text: "คิดตามน้ำหนัก" }), el("option", { value: "total", text: "ใส่ยอดรวม" }));
  const addCust = el("div", { class: "ti-addrow" }, el("b", { class: "small", text: "ลูกค้าใหม่ / แก้ชื่อ: " }), cc, cn, cm,
    el("button", { class: "btn small primary", type: "button", text: "บันทึกลูกค้า", onclick: async () => {
      try { await api("/api/tax-invoices-setup/customers", jsonOpts("POST", { code: cc.value, name: cn.value, mode: cm.value })); await tiReload(); }
      catch (err) { showErr("tiError", err.message); } } }));
  card.append(el("div", { class: "lc-wrap" }, el("table", { class: "lc-table" },
    el("thead", {}, el("tr", {}, ...["รหัส", "ลูกค้า", "คิดเงิน", "ราคา (ล่าสุดก่อน)"].map((h) => el("th", { text: h })))), el("tbody", {}, ...rows))), addPrice, addCust);
  return card;
}
