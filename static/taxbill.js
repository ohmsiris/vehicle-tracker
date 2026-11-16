"use strict";

// ออกบิล (owner, 16 Nov 2026): the bookkeeper makes each day's bills here instead of copying Excel tabs. Pick the customer:
//  * one bill per delivery (CP ซิลเลอร์, ศูนย์จำหน่าย, โกลบอล): the date and the kg off CPF's ใบชั่งน้ำหนัก (or the number of ก้อน / กระสอบ),
//    Enter -- the next number is given, price, VAT, total worked out; then "ดาวน์โหลดไฟล์บิล" = her own Excel bill, one tab per bill, to print
//    on the 9 x 5.5" paper exactly as before.
//  * monthly (โรงหมู, หนองโดน): each delivery (date, เลขที่บิลส่งของ, กระสอบ, kg) through the month, then "ออกใบวางบิล" = her ใบสรุปวางบิล.
//  * the rest (ซันฟู้ด -- the owner's father does it --, credit notes, the occasional customers): the typed box, no bill file.
// Server: app/tax_invoices.py.

const tb_ = { code: null, next: null, monthly: null, busy: false };
const TB_KEY = "ti_bill_customer";
const tbCustomers = () => ti_.data.customers.filter((c) => c.billing === "per_bill" || c.billing === "monthly");
const tbToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

function tbMaker() {
  if (!tb_.code) { try { tb_.code = localStorage.getItem(TB_KEY); } catch (e) { /* storage blocked */ } }
  const list = tbCustomers();
  if (!tb_.code || (tb_.code !== "other" && !list.some((c) => c.code === tb_.code))) tb_.code = list.length ? list[0].code : "other";
  const pick = (code) => { tb_.code = code; tb_.monthly = null; try { localStorage.setItem(TB_KEY, code); } catch (e) { /* ignore */ } $("tbMaker").replaceWith(tbMaker()); };
  const chips = el("div", { class: "dd-chips tb-cust" },
    ...list.map((c) => el("button", { type: "button", class: `dd-chip${tb_.code === c.code ? " on" : ""}`, onclick: () => pick(c.code) },
      el("span", { text: c.name }), el("span", { class: "dd-count", text: c.billing === "monthly" ? "รายเดือน" : "รายบิล" }))),
    el("button", { type: "button", class: `dd-chip${tb_.code === "other" ? " on" : ""}`, onclick: () => pick("other") }, el("span", { text: "อื่นๆ / ใบลดหนี้ / ซันฟู้ด" })));
  const c = tiCust(tb_.code);
  let body;
  if (tb_.code === "other") body = tiEntry();
  else if (c.billing === "monthly") body = tbMonthly(c);
  else body = tbBill(c);
  return el("section", { class: "card ti-entry", id: "tbMaker" }, el("div", { class: "ti-entry-head" }, el("h3", { text: "ออกบิล" })), chips, body);
}

// ------------------------------------------------------------------ one bill per delivery
function tbBill(c) {
  const byBags = c.entry === "bags" && c.kg_per_bag;
  const bagWord = (c.item_format || "").includes("กระสอบ") ? "กระสอบ" : "ก้อน";
  if (!ti_.date) ti_.date = tbToday();
  const f = (label, node, cls = "") => el("label", { class: `ti-f ${cls}` }, el("span", { class: "small muted", text: label }), node);
  const date = el("input", { type: "date", id: "tbDate", value: ti_.date });
  const qty = el("input", { type: "text", id: "tbQty", inputmode: "decimal", autocomplete: "off" });
  const items = c.items || [];
  const item = items.length > 1 ? el("select", { id: "tbItem" }, ...items.map((t) => el("option", { value: t, text: t.replace(/\s*\(.*\)$/, "") }))) : null;
  const ticket = el("input", { type: "text", id: "tbTicket", autocomplete: "off", placeholder: "ถ้ามี" });
  const no = el("input", { type: "text", id: "tbNo", autocomplete: "off" });
  const info = el("div", { class: "ti-info small" });
  const btn = el("button", { class: "btn primary", type: "button", text: "บันทึกบิล" });
  const dayList = el("div", { id: "tbDay" });
  let noAuto = true;
  const price = () => tiPriceOn(c.code, date.value || tbToday());
  const refresh = () => {
    const q = tiNum(qty.value), p = price();
    const kg = q == null ? null : (byBags ? q * Number(c.kg_per_bag) : q);
    const parts = [];
    if (p == null) parts.push(el("span", { class: "ti-bad", text: `ยังไม่มีราคาของ ${c.name} — ใส่ที่ “ลูกค้าและราคา” ด้านล่าง` }));
    if (kg != null && p != null && Number.isFinite(kg)) {
      const a = tiRound(kg * p), v = a * 0.07;
      const text = byBags ? (c.item_format || "").replace("{bags}", String(q)) : (item ? item.value : items[0] || "");
      parts.push(el("span", { class: "muted", text: `${text} · ${tiB(kg, 0)} กก. x ${p} = ` }), el("span", { text: `${tiB(a)} + VAT ${tiB(v)} = ` }),
        el("b", { text: `${tiB(tiRound(a + v))} บาท` }));
    }
    fill(info, ...parts);
  };
  const loadNext = async () => {
    if (!noAuto || !date.value) return;
    try { const r = await api(`/api/tax-invoices/next?code=${encodeURIComponent(c.code)}&day=${date.value}`); if (noAuto) no.value = r.doc_no || ""; }
    catch (err) { showErr("tiError", err.message); }
  };
  no.addEventListener("input", () => { noAuto = no.value === ""; });
  qty.addEventListener("input", refresh);
  if (item) item.addEventListener("change", refresh);
  date.addEventListener("change", () => { ti_.date = date.value; noAuto = true; loadNext(); refresh(); tbDayList(c, dayList, date.value); });
  const save = async () => {
    if (tb_.busy) return;
    showErr("tiError", "");
    const q = tiNum(qty.value);
    if (q == null || !(q > 0)) { showErr("tiError", byBags ? `กรุณาใส่จำนวน${bagWord}` : "กรุณาใส่น้ำหนักสุทธิ (กก.)"); qty.focus(); return; }
    const body = { doc_no: no.value, doc_date: date.value, customer_code: c.code, item: item ? item.value : "", ticket_no: ticket.value,
                   ...(byBags ? { bags: q } : { kg: q }) };
    tb_.busy = true; btn.disabled = true;
    try {
      const row = await api("/api/tax-invoices", jsonOpts("POST", body));
      $("tiOk2").textContent = `บันทึก ${row.doc_no} แล้ว — ${tiB(row.total)} บาท`;
      qty.value = ""; ticket.value = ""; noAuto = true; refresh();
      await loadNext();
      tbDayList(c, dayList, date.value);
      tiReloadQuiet();
      qty.focus();
    } catch (err) { showErr("tiError", err.message); }
    finally { tb_.busy = false; btn.disabled = false; }
  };
  btn.onclick = save;
  const box = el("div", {},
    el("div", { class: "ti-fields", onkeydown: (e) => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(); } } },
      f("วันที่", date),
      f(byBags ? `จำนวน${bagWord}` : "น้ำหนักสุทธิ (กก.)", qty),
      item ? f("รายการ", item, "ti-wide") : null,
      c.code === "P" ? f("เลขที่บัตรชั่ง", ticket) : null,
      f("เลขที่ (เลขถัดไป)", no, "ti-wide"),
      el("div", { class: "ti-go" }, btn)),
    info, el("div", { id: "tiOk2", class: "small ti-ok" }), dayList);
  queueMicrotask(() => { loadNext(); refresh(); tbDayList(c, dayList, date.value); qty.focus(); });
  return box;
}

async function tiReloadQuiet() {                       // the summary below follows, the box keeps its place
  try { ti_.data = await api(`/api/tax-invoices?month=${ti_.month}${ti_.to && ti_.to !== ti_.month ? `&to=${ti_.to}` : ""}`); } catch (err) { return; }
  tiRender(true);
}

async function tbDayList(c, box, day) {
  if (!day) { fill(box, null); return; }
  const my = (tb_.daySeq = (tb_.daySeq || 0) + 1);         // only the latest look counts (an earlier, slower one must not overwrite it)
  let rows;
  try { rows = (await api(`/api/tax-invoices?month=${day.slice(0, 7)}`)).rows.filter((r) => r.customer_code === c.code && r.doc_date === day && r.kind === "invoice"); }
  catch (err) { if (my === tb_.daySeq) fill(box, el("p", { class: "ti-bad small", text: err.message })); return; }
  if (my !== tb_.daySeq) return;
  if (!rows.length) { fill(box, el("p", { class: "muted small tb-none", text: `ยังไม่มีบิล ${c.name} วันที่ ${tiDMY(day)}` })); return; }
  const picked = new Set(rows.filter((r) => !r.printed_at).map((r) => r.id));
  const sum = rows.reduce((a, r) => a + Number(r.total), 0);
  const draw = () => tbDayList(c, box, day);
  const all = el("input", { type: "checkbox", title: "เลือกทั้งหมด", checked: rows.every((r) => picked.has(r.id)),
    onchange: (e) => { rows.forEach((r) => (e.target.checked ? picked.add(r.id) : picked.delete(r.id))); box.querySelectorAll("input[data-pick]").forEach((x) => { x.checked = e.target.checked; }); bar(); } });
  const body = el("tbody", {}, ...rows.map((r) => el("tr", {},
    el("td", {}, el("input", { type: "checkbox", "data-pick": "1", checked: picked.has(r.id), onchange: (e) => { if (e.target.checked) picked.add(r.id); else picked.delete(r.id); bar(); } })),
    el("td", { class: "nowrap", text: r.doc_no }), el("td", { text: r.item || "" }), el("td", { class: "r", text: tiB(r.kg, 0) }),
    el("td", { class: "r", text: tiB(r.total) }), el("td", { class: "small muted", text: r.ticket_no ? `บัตรชั่ง ${r.ticket_no}` : "" }),
    el("td", { class: "ti-printed" }, el("label", { class: "small" }, el("input", { type: "checkbox", checked: !!r.printed_at,
      onchange: async (e) => { await api("/api/tax-invoices/printed", jsonOpts("POST", { ids: [r.id], printed: e.target.checked })); draw(); } }), " พิมพ์แล้ว")))));
  const actions = el("span", { class: "ti-pickbar" });
  const bar = () => {
    const ids = [...picked];
    const dl = el("button", { class: "btn primary", type: "button", text: `ดาวน์โหลดเพื่อพิมพ์ (${ids.length} ใบ)`, disabled: !ids.length,
      onclick: async () => {
        await tbDownload(`/api/tax-invoices/bills.xlsx?ids=${ids.join(",")}`, `บิล ${c.name} ${day}.xlsx`, dl);
        fill(actions, dl, el("button", { class: "btn", type: "button", text: `ติ๊กพิมพ์แล้ว ${ids.length} ใบนี้`, onclick: async () => {
          try { await api("/api/tax-invoices/printed", jsonOpts("POST", { ids, printed: true })); draw(); tiReloadQuiet(); } catch (err) { showErr("tiError", err.message); } } }));
      } });
    fill(actions, dl);
  };
  bar();
  fill(box, el("div", { class: "tb-day" },
    el("div", { class: "ti-listhead" }, el("b", { text: `บิล ${c.name} วันที่ ${tiDMY(day)} — ${rows.length} ใบ · ${tiB(sum)} บาท` }), actions),
    el("div", { class: "lc-wrap" }, el("table", { class: "lc-table ti-list" }, el("thead", {}, el("tr", {}, el("th", {}, all), ...["เลขที่", "รายการ", "น้ำหนัก", "สุทธิ", "", ""].map((h) => el("th", { text: h })))), body)),
    el("p", { class: "small muted", text: "ติ๊กไว้ให้แล้วเฉพาะบิลที่ยังไม่พิมพ์ — ทุกบิลที่เลือกอยู่ในแท็บเดียว (บิลละหน้า) เปิดใน Excel แล้วพิมพ์ครั้งเดียว" })));
}

async function tbDownload(url, name, btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(url, { headers: { "X-App-Token": state.token } });
    if (!res.ok) { let msg = `HTTP ${res.status}`; try { msg = (await res.json()).detail || msg; } catch (e) { /* not json */ } throw new Error(msg); }
    const u = URL.createObjectURL(await res.blob());
    const a = el("a", { href: u, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 5000);
  } catch (err) { showErr("tiError", `ดาวน์โหลดไม่สำเร็จ: ${err.message}`); }
  finally { if (btn) btn.disabled = false; }
}

// ------------------------------------------------------------------ monthly: deliveries, then the month's ใบวางบิล
function tbMonthly(c) {
  const month = ti_.month;
  const box = el("div", { id: "tbMonthly" }, el("p", { class: "muted small", text: "กำลังโหลด…" }));
  (async () => {
    try { tb_.monthly = await api(`/api/tax-invoices/deliveries?code=${encodeURIComponent(c.code)}&month=${month}`); }
    catch (err) { fill(box, el("p", { class: "ti-bad", text: err.message })); return; }
    tbMonthlyDraw(c, box);
  })();
  return box;
}

function tbMonthlyDraw(c, box) {
  const d = tb_.monthly, month = ti_.month;
  const byBags = c.entry === "bags" && c.kg_per_bag;
  const f = (label, node, cls = "") => el("label", { class: `ti-f ${cls}` }, el("span", { class: "small muted", text: label }), node);
  const today = tbToday();
  const date = el("input", { type: "date", value: (ti_.date && ti_.date.slice(0, 7) === month) ? ti_.date : (today.slice(0, 7) === month ? today : `${month}-01`) });
  const note = el("input", { type: "text", value: d.next_note || "", autocomplete: "off" });
  const bags = el("input", { type: "text", inputmode: "decimal", autocomplete: "off" });
  const kg = el("input", { type: "text", inputmode: "decimal", autocomplete: "off", readOnly: !!byBags, placeholder: byBags ? `= กระสอบ x ${Number(c.kg_per_bag)}` : "" });
  const info = el("div", { class: "ti-info small" });
  const btn = el("button", { class: "btn primary", type: "button", text: "บันทึกการส่ง" });
  const refresh = () => {
    if (byBags) { const b = tiNum(bags.value); kg.value = b == null ? "" : String(b * Number(c.kg_per_bag)); }
    const k = tiNum(kg.value), p = tiPriceOn(c.code, date.value || today);
    fill(info, k != null && p != null ? el("span", { text: `${tiB(k, 0)} กก. x ${p} = ${tiB(tiRound(k * p))} บาท (ก่อน VAT)` }) :
      p == null ? el("span", { class: "ti-bad", text: `ยังไม่มีราคาของ ${c.name}` }) : null);
  };
  [bags, kg].forEach((n) => n.addEventListener("input", refresh));
  date.addEventListener("change", () => { ti_.date = date.value; refresh(); });
  const save = async () => {
    showErr("tiError", "");
    btn.disabled = true;
    try {
      await api("/api/tax-invoices/deliveries", jsonOpts("POST", { customer_code: c.code, day: date.value, note_no: note.value, bags: bags.value, kg: kg.value }));
      ti_.date = date.value;
      tb_.monthly = await api(`/api/tax-invoices/deliveries?code=${encodeURIComponent(c.code)}&month=${month}`);
      tbMonthlyDraw(c, box);
      const b = box.querySelector("input[data-first]"); if (b) b.focus();
    } catch (err) { showErr("tiError", err.message); btn.disabled = false; }
  };
  btn.onclick = save;
  bags.dataset.first = "1";
  const form = el("div", { class: "ti-fields", onkeydown: (e) => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); save(); } } },
    f("วันที่ส่ง", date), f("เลขที่บิลส่งของ", note, "ti-wide"), f("จำนวนกระสอบ", bags), f(byBags ? "น้ำหนัก (กก.) — คิดให้" : "น้ำหนัก (กก.)", kg),
    el("div", { class: "ti-go" }, btn));
  const open = d.open;
  const picked = new Set(open.map((r) => r.id));
  const sumOf = (rows) => rows.reduce((a, r) => a + Number(r.amount), 0);
  const tableOf = (rows, removable) => el("div", { class: "lc-wrap" }, el("table", { class: "lc-table ti-list" },
    el("thead", {}, el("tr", {}, ...["", "วันที่", "เลขที่บิลส่งของ", "รายการ", "กระสอบ", "น้ำหนัก", "ราคา", "จำนวนเงิน", ""].map((h, i) => el("th", { class: i >= 4 && i <= 7 ? "r" : "", text: h })))),
    el("tbody", {}, ...rows.map((r) => el("tr", {},
      el("td", {}, removable ? el("input", { type: "checkbox", checked: picked.has(r.id), onchange: (e) => { if (e.target.checked) picked.add(r.id); else picked.delete(r.id); } }) : null),
      el("td", { class: "nowrap", text: tiDMY(r.day) }), el("td", { class: "nowrap", text: r.note_no || "" }), el("td", { text: r.item || "" }),
      el("td", { class: "r", text: r.bags != null ? tiB(r.bags, 0) : "" }), el("td", { class: "r", text: tiB(r.kg, 0) }), el("td", { class: "r", text: String(Number(r.price)) }),
      el("td", { class: "r", text: tiB(r.amount) }),
      el("td", {}, removable ? el("button", { class: "linkbtn small", type: "button", text: "ลบ", onclick: async () => {
        if (!confirm(`ลบรายการส่งของ ${tiDMY(r.day)} ${r.note_no || ""}?`)) return;
        try { await api(`/api/tax-invoices/deliveries/${r.id}`, { method: "DELETE" }); tb_.monthly = await api(`/api/tax-invoices/deliveries?code=${encodeURIComponent(c.code)}&month=${month}`); tbMonthlyDraw(c, box); }
        catch (err) { showErr("tiError", err.message); } } }) : null))))));
  const parts = [form, info];
  if (d.bill) {
    const dl = el("button", { class: "btn primary", type: "button", text: "ดาวน์โหลดใบวางบิล (Excel)",
      onclick: () => tbDownload(`/api/tax-invoices/monthly/${d.bill.id}/file.xlsx`, `ใบวางบิล ${c.name} ${month}.xlsx`, dl) });
    const undo = el("button", { class: "btn small danger", type: "button", text: "ยกเลิกใบวางบิลนี้", onclick: async () => {
      if (!confirm(`ยกเลิกใบวางบิล ${d.bill.doc_no}? รายการส่งของจะกลับมาเป็นยังไม่วางบิล`)) return;
      try { await api(`/api/tax-invoices/monthly/${d.bill.id}`, { method: "DELETE" }); tb_.monthly = await api(`/api/tax-invoices/deliveries?code=${encodeURIComponent(c.code)}&month=${month}`); tbMonthlyDraw(c, box); tiReloadQuiet(); }
      catch (err) { showErr("tiError", err.message); } } });
    parts.push(el("div", { class: "tb-day" },
      el("div", { class: "ti-listhead" }, el("b", { text: `ใบวางบิล ${tiMonthTH(month)}: ${d.bill.doc_no} — ${d.billed.length} รายการ · ${tiB(d.bill.total)} บาท (รวม VAT)` }), el("span", {}, dl, " ", undo)),
      tableOf(d.billed, false)));
  }
  if (open.length) {
    const make = el("button", { class: "btn primary", type: "button", text: `ออกใบวางบิล ${tiMonthTH(month)} (รายการที่ติ๊ก)`, onclick: async () => {
      if (!picked.size) { showErr("tiError", "ยังไม่ได้ติ๊กรายการส่งของ"); return; }
      make.disabled = true;
      try {
        const inv = await api("/api/tax-invoices/monthly", jsonOpts("POST", { code: c.code, month, delivery_ids: [...picked] }));
        tb_.monthly = await api(`/api/tax-invoices/deliveries?code=${encodeURIComponent(c.code)}&month=${month}`);
        tbMonthlyDraw(c, box); tiReloadQuiet();
        tbDownload(`/api/tax-invoices/monthly/${inv.id}/file.xlsx`, `ใบวางบิล ${c.name} ${month}.xlsx`);
      } catch (err) { showErr("tiError", err.message); make.disabled = false; }
    } });
    parts.push(el("div", { class: "tb-day" },
      el("div", { class: "ti-listhead" }, el("b", { text: `ส่งแล้ว ยังไม่วางบิล — ${open.length} รายการ · ${tiB(sumOf(open))} บาท (ก่อน VAT)` }), d.bill ? null : make),
      tableOf(open, true),
      el("p", { class: "small muted", text: d.bill ? `เดือนนี้มีใบวางบิลแล้ว — รายการที่เหลือจะไปอยู่ในใบวางบิลเดือนถัดไป` : "ติ๊กเฉพาะรายการที่จะอยู่ในใบวางบิลเดือนนี้ (เช่น ส่งวันที่ 30 เดือนก่อน แต่วางบิลเดือนนี้ ก็ติ๊กได้)" })));
  } else if (!d.bill) parts.push(el("p", { class: "muted small tb-none", text: `ยังไม่มีรายการส่งของ ${c.name} ที่ยังไม่วางบิล` }));
  fill(box, ...parts);
  refresh();
}
