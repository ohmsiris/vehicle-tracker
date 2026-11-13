"use strict";

// เจ้าหนี้ร้านค้า (#payables, #payables/<shop>), owner, 13 Nov 2026. The bills stay where they were recorded (truck repairs / tyres under
// the truck, factory and stock bills under purchases); this page gathers them by shop: what each shop is owed, which bills are paid.
// A payment (mum's cheque, a transfer, cash) is entered on the shop's page with the bills it pays ticked -- no cutting by dates. The
// ticked bills must match the amount within ±1 baht, or the payment carries a note. Server: app/payables.py.

const py_ = { data: null, shop: null, picked: new Set(), form: null, showAll: false };
const pyDate = (iso) => { if (!iso) return ""; const [y, m, d] = String(iso).slice(0, 10).split("-"); return `${d}/${m}/${y}`; };
const pyBaht = (x) => Number(x || 0).toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pyKey = (b) => `${b.source}:${b.id}`;

async function openPayables(sub = "") {
  showView("payables");
  showErr("pyError", "");
  const shop = sub || null;                                  // route() has decoded the hash already
  if (shop !== py_.shop) { py_.picked = new Set(); py_.form = null; }
  py_.shop = shop;
  if (!py_.data) fill($("pyBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { py_.data = await api("/api/payables"); }
  catch (err) { fill($("pyBody"), null); showErr("pyError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  if (py_.shop) pyShop(); else pyHome();
}

function pyTile(label, value, sub, tone) {
  return el("div", { class: `lc-tile ${tone || ""}` }, el("div", { class: "small muted", text: label }), el("b", { text: value }), sub ? el("div", { class: "small muted", text: sub }) : null);
}

// ------------------------------------------------------------------ every shop
function pyHome() {
  const d = py_.data, t = d.tiles;
  document.querySelector("#payablesView .page-head").hidden = false;
  const owing = d.shops.filter((s) => s.owed > 0);
  const tiles = el("div", { class: "lc-tiles" },
    pyTile("ค้างจ่ายรวม", `฿${pyBaht(t.owed)}`, `${t.shops} ร้าน · ${t.bills} บิล`, t.owed ? "warn" : ""),
    pyTile("ค้างเกิน 60 วัน", `฿${pyBaht(t.over60)}`, "", t.over60 ? "bad" : ""),
    pyTile("จ่ายแล้วเดือนนี้", `฿${pyBaht(t.paid_month)}`));
  const top = owing.slice(0, 10), max = Math.max(1, ...top.map((s) => s.owed));
  const chart = top.length ? el("section", { class: "card py-chart" }, el("h3", { text: "ค้างจ่ายมากที่สุด" }),
    ...top.map((s) => el("button", { class: "py-bar", type: "button", onclick: () => goHash(`#payables/${encodeURIComponent(s.shop)}`) },
      el("span", { class: "py-bar-name", text: s.shop }),
      el("span", { class: "py-bar-track" }, el("span", { class: "py-bar-fill", style: `width:${Math.max(2, (s.owed / max) * 100)}%` })),
      el("span", { class: "py-bar-val", text: `฿${pyBaht(s.owed)}` })))) : null;
  const list = py_.showAll ? d.shops : owing;
  const rows = list.map((s) => el("tr", { class: "py-row", onclick: () => goHash(`#payables/${encodeURIComponent(s.shop)}`) },
    el("td", {}, el("div", { class: "lc-name", text: s.shop }), s.pays_now ? el("div", { class: "small muted", text: "จ่ายสด / โอนทันที" }) : null),
    el("td", { class: "r" }, el("b", { text: s.owed ? `฿${pyBaht(s.owed)}` : "—" }), s.due_count ? el("div", { class: "small muted", text: `${s.due_count} บิล` }) : null),
    el("td", { class: "small", text: s.oldest ? `ค้างตั้งแต่ ${pyDate(s.oldest)} (${s.oldest_age} วัน)` : "" }),
    el("td", { class: "small", text: s.last_payment ? `จ่ายล่าสุด ${pyDate(s.last_payment.paid_on)} · ฿${pyBaht(s.last_payment.amount)}` : "ยังไม่มีการจ่ายในระบบ" }),
    el("td", { class: "r" }, s.flagged ? el("span", { class: "lc-badge warn", text: `ต้องดู ${s.flagged}` }) : null)));
  fill($("pyBody"), tiles, chart,
    el("div", { class: "lc-bar" }, el("h3", { class: "lc-h", style: "margin:0", text: py_.showAll ? `ทุกร้าน (${d.shops.length})` : `ร้านที่ค้างจ่าย (${owing.length})` }),
      el("span", { class: "grow" }),
      el("button", { class: "btn small", type: "button", text: py_.showAll ? "เฉพาะร้านที่ค้าง" : "แสดงทุกร้าน", onclick: () => { py_.showAll = !py_.showAll; pyHome(); } })),
    rows.length ? el("div", { class: "lc-wrap" }, el("table", { class: "lc-table py-table" }, el("tbody", {}, ...rows)))
      : el("p", { class: "muted", text: "ไม่มีร้านที่ค้างจ่าย" }));
}

// ------------------------------------------------------------------ one shop
function pyShop() {
  const d = py_.data;
  document.querySelector("#payablesView .page-head").hidden = true;
  const s = d.shops.find((x) => x.shop === py_.shop);
  if (!s) { fill($("pyBody"), el("p", { class: "muted", text: "ไม่พบร้านนี้" }), el("a", { href: "#payables", text: "← ทุกร้าน" })); return; }
  const open = s.bills.filter((b) => b.state === "due" || b.state === "now");
  const picked = open.filter((b) => py_.picked.has(pyKey(b)));
  const pickedSum = picked.reduce((a, b) => a + b.amount, 0);
  const head = el("div", { class: "py-head" },
    el("a", { class: "btn small", href: "#payables", text: "‹ ทุกร้าน" }),
    el("h2", { class: "page-title", text: s.shop }),
    el("span", { class: "grow" }),
    el("label", { class: "small py-now" }, el("input", { type: "checkbox", checked: s.pays_now, onchange: (e) => pyPaysNow(s.shop, e.target.checked) }),
      el("span", { text: " ร้านนี้จ่ายสด / โอนทันที (ไม่นับเป็นค้างจ่าย)" })));
  const tiles = el("div", { class: "lc-tiles" },
    pyTile("ค้างจ่าย", `฿${pyBaht(s.owed)}`, `${s.due_count} บิล`, s.owed ? "warn" : ""),
    pyTile("ค้างตั้งแต่", s.oldest ? pyDate(s.oldest) : "—", s.oldest ? `${s.oldest_age} วัน` : ""),
    pyTile("จ่ายแล้วในระบบ", `฿${pyBaht(s.payments.filter((p) => p.method !== "before").reduce((a, p) => a + p.amount, 0))}`, `${s.payments.length} ครั้ง`));

  const billRow = (b, tick) => el("tr", { class: tick && py_.picked.has(pyKey(b)) ? "py-picked" : "" },
    tick ? el("td", { class: "py-tick" }, el("input", { type: "checkbox", checked: py_.picked.has(pyKey(b)), "aria-label": "เลือกบิลนี้",
      onchange: (e) => { if (e.target.checked) py_.picked.add(pyKey(b)); else py_.picked.delete(pyKey(b)); pyShop(); } })) : el("td", {}),
    el("td", { class: "lc-date", text: pyDate(b.date) }),
    el("td", {}, el("div", { text: b.what || "" }), el("div", { class: "small muted", text: [b.bill_no ? `เลขที่ ${b.bill_no}` : "", b.branch || "", b.source === "truck" ? "บิลซ่อมรถ" : "บิลซื้อของ"].filter(Boolean).join(" · ") })),
    el("td", { class: "r" }, el("b", { text: `฿${pyBaht(b.amount)}` }),
      b.changed ? el("div", { class: "small warn-text", text: `บิลแก้หลังจ่าย ${b.changed > 0 ? "+" : "−"}฿${pyBaht(Math.abs(b.changed))}` }) : null),
    el("td", { class: "r" }, b.source === "truck" ? el("a", { class: "linkish small", href: b.link, text: "เปิดบิล" })
      : el("button", { class: "linkish small", type: "button", text: "เปิดบิล", onclick: () => openPurchaseBill(b.id, "pyError") })));

  const parts = [head, tiles];
  // the bills still owed, to tick
  parts.push(el("div", { class: "lc-bar" }, el("h3", { class: "lc-h", style: "margin:0", text: `ยังไม่จ่าย (${open.length})` }),
    picked.length ? el("span", { class: "lc-badge info", text: `ติ๊กแล้ว ${picked.length} บิล · ฿${pyBaht(pickedSum)}` }) : null, el("span", { class: "grow" }),
    open.length ? el("button", { class: "btn small", type: "button", text: picked.length === open.length ? "ไม่เลือกเลย" : "เลือกทั้งหมด",
      onclick: () => { if (picked.length === open.length) py_.picked = new Set(); else py_.picked = new Set(open.map(pyKey)); pyShop(); } }) : null));
  parts.push(open.length ? el("div", { class: "lc-wrap" }, el("table", { class: "lc-table py-table" }, el("tbody", {}, ...open.map((b) => billRow(b, true)))))
    : el("p", { class: "muted small", text: "ไม่มีบิลค้างจ่าย" }));
  if (open.length) parts.push(pyPayForm(s, picked, pickedSum));

  // the payments made, each with the bills it paid
  parts.push(el("h3", { class: "lc-h", text: `จ่ายแล้ว (${s.payments.length})` }));
  if (!s.payments.length) parts.push(el("p", { class: "muted small", text: "ยังไม่มีการจ่ายที่บันทึกในระบบ" }));
  s.payments.forEach((p) => {
    const bills = s.bills.filter((b) => b.payment_id === p.id);
    const off = Math.abs(p.diff) > 1;
    parts.push(el("section", { class: "card py-pay" },
      el("div", { class: "py-pay-head" },
        el("b", { text: `${pyDate(p.paid_on)} · ${p.method_label}` }),
        el("span", { class: "small muted", text: [p.book, p.cheque_no ? `เลขที่เช็ค ${p.cheque_no}` : "", p.created_by ? `บันทึกโดย ${p.created_by}` : ""].filter(Boolean).join(" · ") }),
        el("span", { class: "grow" }),
        el("b", { text: `฿${pyBaht(p.amount)}` }),
        el("span", { class: `lc-badge ${off ? "warn" : "ok"}`, text: off ? `ต่างจากบิล ฿${pyBaht(Math.abs(p.diff))}` : "ตรงกับบิล" }),
        el("button", { class: "linkish small", type: "button", text: "ยกเลิก", onclick: () => pyUnpay(p) })),
      p.note ? el("p", { class: "small", text: `หมายเหตุ: ${p.note}` }) : null,
      p.missing ? el("p", { class: "small warn-text", text: `มี ${p.missing} บิลของการจ่ายนี้ที่ไม่พบแล้ว (ถูกลบ หรือยอดเป็น 0)` }) : null,
      bills.length ? el("table", { class: "lc-table py-table" }, el("tbody", {}, ...bills.map((b) => billRow(b, false)))) : null));
  });
  fill($("pyBody"), ...parts);
}

function pyPayForm(s, picked, pickedSum) {
  const f = py_.form || (py_.form = { method: "cheque", book: "", cheque_no: "", paid_on: py_.data.today, amount: "", note: "", before: "" });
  const statusOf = () => {
    const amount = f.method === "before" ? pickedSum : Number(f.amount || 0);
    const diff = Math.round((amount - pickedSum) * 100) / 100;
    return !picked.length ? el("span", { class: "small muted", text: "ติ๊กบิลที่จ่ายด้านบน" })
      : f.method !== "before" && !f.amount ? el("span", { class: "small muted", text: `บิลที่ติ๊ก ${picked.length} ใบ รวม ฿${pyBaht(pickedSum)} — ใส่ยอดที่จ่าย` })
        : el("span", { class: `lc-badge ${Math.abs(diff) <= 1 ? "ok" : "warn"}`,
          text: Math.abs(diff) <= 1 ? `ตรงกัน · ${picked.length} บิล ฿${pyBaht(pickedSum)}` : `ไม่ตรง: จ่าย ฿${pyBaht(amount)} · บิล ฿${pyBaht(pickedSum)} · ต่าง ฿${pyBaht(Math.abs(diff))} (ใส่หมายเหตุ)` });
  };
  const status = el("span", { class: "py-status" }, statusOf());
  const field = (label, ctl) => el("label", { class: "lc-field" }, el("span", { class: "small muted", text: label }), ctl);
  const inp = (key, attrs = {}) => el("input", { value: f[key], oninput: (e) => { f[key] = e.target.value; if (key === "amount") fill(status, statusOf()); }, onchange: (e) => { f[key] = e.target.value; }, ...attrs });
  const before = el("input", { type: "date", value: f.before, onchange: (e) => { f.before = e.target.value; } });
  return el("section", { class: "card lc-form py-form" }, el("h3", { text: "บันทึกการจ่าย" }),
    el("div", { class: "lc-grid" },
      field("วิธีจ่าย", el("select", { onchange: (e) => { f.method = e.target.value; pyShop(); } },
        ...py_.data.methods.map((m) => el("option", { value: m.key, text: m.label, selected: m.key === f.method })))),
      f.method === "before" ? null : field("ยอดที่จ่าย (บาท)", inp("amount", { type: "number", step: "0.01", min: "0", inputmode: "decimal", placeholder: pyBaht(pickedSum) })),
      field("วันที่จ่าย", inp("paid_on", { type: "date" })),
      f.method === "cheque" || f.method === "transfer" ? field("สมุดเช็ค / บัญชี", el("select", { onchange: (e) => { f.book = e.target.value; } },
        el("option", { value: "", text: "— ไม่ระบุ —" }), ...py_.data.books.map((b) => el("option", { value: b, text: b, selected: b === f.book })))) : null,
      f.method === "cheque" ? field("เลขที่เช็ค", inp("cheque_no")) : null,
      field("หมายเหตุ", inp("note", { placeholder: "เช่น บิลที่แม่เขียนไว้" }))),
    f.method === "before" ? el("p", { class: "small muted", text: "ใช้กับบิลเก่าที่จ่ายไปแล้วก่อนเริ่มบันทึกในระบบ — ยอดเท่ากับบิลที่ติ๊ก" }) : null,
    el("div", { class: "lc-formbar" },
      el("span", { class: "small muted", text: "ติ๊กบิลทั้งหมดก่อนวันที่" }), before,
      el("button", { class: "btn small", type: "button", text: "ติ๊ก", onclick: () => {
        if (!f.before) return;
        s.bills.filter((b) => (b.state === "due" || b.state === "now") && b.date < f.before).forEach((b) => py_.picked.add(pyKey(b)));
        pyShop();
      } })),
    el("div", { class: "lc-formbar" }, status, el("span", { class: "grow" }),
      el("button", { class: "btn primary", type: "button", text: "บันทึกการจ่าย", onclick: () => pySave(s, picked) })));
}

async function pySave(s, picked) {
  const f = py_.form;
  if (!picked.length) { alert("ติ๊กบิลที่จ่ายก่อน"); return; }
  try {
    await api("/api/payables/payments", jsonOpts("POST", { shop: s.shop, method: f.method, amount: f.amount, paid_on: f.paid_on, book: f.book,
      cheque_no: f.cheque_no, note: f.note, bills: picked.map((b) => ({ source: b.source, id: b.id })) }));
    py_.picked = new Set(); py_.form = null;
    await openPayables(s.shop);
  } catch (err) { alert(err.message); }
}

async function pyUnpay(p) {
  if (!window.confirm(`ยกเลิกการจ่าย ${pyDate(p.paid_on)} ฿${pyBaht(p.amount)}? บิลของการจ่ายนี้จะกลับเป็นค้างจ่าย`)) return;
  try { await api(`/api/payables/payments/${p.id}`, { method: "DELETE" }); await openPayables(py_.shop); }
  catch (err) { alert(err.message); }
}

async function pyPaysNow(shop, on) {
  try { await api("/api/payables/pays-now", jsonOpts("POST", { shop, on })); await openPayables(shop); }
  catch (err) { alert(err.message); }
}
