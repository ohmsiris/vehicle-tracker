"use strict";

// บัญชี › งบกำไรขาดทุน (#accounts): the company month (26th - 25th, named after the 25th) or one half (26-10 / 11-25), per branch or both.
//   * the checklist: what should come in this half and whether it has (bills already in the app = automatic ✓)
//   * the P&L in standard lines (Revenue, Cost of goods sold, Selling & delivery, Administrative) that open level by level:
//     line -> account -> shop -> each bill (a truck bill opens its page)
//   * "+ บันทึกรายการ" for a cost that is not in the app yet (electricity, water, a document ...)
// English first (the owner reads it best), Thai beside it. app/accounting.py.

const acx = { data: null, open: new Set() };
const ACX_FROM = { program: "จากโปรแกรม", you: "จากคุณ", hq: "จาก HQ" };

function acxMonths() {
  const out = [];
  const t = new Date();
  let y = t.getFullYear(), m = t.getMonth() + 1;
  if (t.getDate() >= 26) { m += 1; if (m > 12) { m = 1; y += 1; } }
  for (let i = 0; i < 12; i++) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m -= 1; if (m < 1) { m = 12; y -= 1; }
  }
  return out;
}

function acxMonthLabel(key) {
  const [y, m] = key.split("-").map(Number);
  const py = m === 1 ? y - 1 : y, pm = m === 1 ? 12 : m - 1;
  const TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
  return `${TH[m - 1]} ${y + 543} (26 ${TH[pm - 1]} – 25 ${TH[m - 1]})`;
  void py;
}

async function openAccounts() {
  showView("accounts");
  showErr("acxError", "");
  if (!acx.wired) {
    acx.wired = true;
    $("acxMonth").replaceChildren(...acxMonths().map((k) => el("option", { value: k, text: acxMonthLabel(k) })));
    for (const id of ["acxMonth", "acxHalf", "acxBranch"]) $(id).onchange = loadAccounts;
    $("acxAdd").onclick = () => acxAddDialog({});
  }
  await loadAccounts();
}

async function loadAccounts() {
  const q = new URLSearchParams({ month: $("acxMonth").value, half: $("acxHalf").value, branch: $("acxBranch").value });
  fill($("acxBody"), el("p", { class: "muted", text: "กำลังคำนวณ…" }));
  try {
    acx.data = await api(`/api/accounts/report?${q}`);
  } catch (err) {
    fill($("acxBody"), null);
    showErr("acxError", `โหลดไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderAccounts();
}

const acxMoney = (n) => (n < 0 ? `(${baht(Math.abs(n))})` : baht(n));

function renderAccounts() {
  const d = acx.data, p = d.pnl;
  const half = $("acxHalf").value;
  const periodKey = `${d.month}-${half || 0}`;
  const feeds = el("div", { class: "card acx-feeds" },
    el("h3", { text: `What's needed this period · สิ่งที่ต้องใส่งวดนี้ (${beDate(d.start)} – ${beDate(d.end)})` }),
    ...d.feeds.map((f) => {
      const icon = f.state === "auto" ? "✓" : f.state === "done" ? "✓" : f.state === "none" ? "–" : "○";
      return el("div", { class: `acx-feed ${f.state}` },
        el("span", { class: "acx-feed-icon", text: icon }),
        el("div", {}, el("strong", { text: f.en }), el("span", { class: "muted small", text: ` · ${f.th} · ${ACX_FROM[f.from]}` }),
          el("div", { class: "small muted", text: f.state === "auto" ? `อยู่ในโปรแกรมแล้ว · ${f.entries} รายการ · ${baht(f.total)} ฿${f.note ? ` · ${f.note}` : ""}`
            : f.state === "done" ? `ใส่แล้ว${f.entries ? ` · ${f.entries} รายการ · ${baht(f.total)} ฿` : ""}` : f.state === "none" ? "ไม่มีในงวดนี้" : "ยังไม่ได้ใส่" })),
        f.from !== "program" ? el("div", { class: "acx-feed-actions" },
          el("button", { class: "btn small", type: "button", text: "+ ใส่", onclick: () => acxAddDialog({ feed: f.key, account: f.account }) }),
          el("button", { class: "btn small", type: "button", text: f.state === "none" ? "ยกเลิก" : "ไม่มีงวดนี้",
            onclick: () => acxMark(f.key, periodKey, f.state === "none" ? "" : "none") })) : null);
    }));
  const summary = el("div", { class: "acx-kpis" },
    acxKpi("Revenue · รายได้", p.revenue), acxKpi("Cost of goods sold · ต้นทุนขาย", p.cogs), acxKpi("Gross profit · กำไรขั้นต้น", p.gross_profit),
    acxKpi("Expenses · ค่าใช้จ่าย", p.expenses), acxKpi("Net profit · กำไรสุทธิ", p.net_profit, true));
  const tree = el("div", { class: "card acx-tree" },
    el("h3", { text: "Profit & loss · งบกำไรขาดทุน" }),
    el("p", { class: "muted small", text: "กดที่บรรทัดเพื่อเปิดดูละเอียดขึ้น: หมวด → บัญชี → ร้าน → แต่ละบิล · ไม่รวม VAT (VAT ไปที่บัญชีภาษีซื้อ) · รายได้ยังไม่ได้เชื่อม" }),
    ...p.sections.map((s) => acxNode(`s:${s.key}`, `${s.en} · ${s.th}`, s.total, "acx-l0",
      () => s.accounts.map((a) => acxNode(`a:${a.code}`, `${a.en} · ${a.th}`, a.total, "acx-l1",
        () => a.parties.map((pt) => acxNode(`a:${a.code}:${pt.party}`, pt.party, pt.total, "acx-l2",
          () => pt.entries.map((e) => acxEntry(e)))))))),
    el("div", { class: "acx-line acx-total" }, el("span", { text: "Net profit · กำไรสุทธิ" }), el("strong", { text: acxMoney(p.net_profit) })));
  fill($("acxBody"), summary, feeds, acxDocs(d.docs || []), acxFuel(d.fuel), tree);
}

// เอกสารเบิกกลาง / สิ้นเดือน: each page read, its lines checked against the total written on it; memos with a one-line summary.
const ACX_KIND = { cover: "ปก", expense: "ค่าใช้จ่าย", fuel: "น้ำมัน", fuel_table: "ตารางน้ำมัน (พิมพ์)", advance: "เบิกล่วงหน้า", memo: "บันทึก", other: "อื่นๆ" };
function acxDocs(docs) {
  if (!docs.length) return null;
  const money = docs.filter((x) => !["memo", "other"].includes(x.kind));
  const memos = docs.filter((x) => ["memo", "other"].includes(x.kind));
  const bad = money.filter((x) => x.status === "mismatch").length;
  const row = (x) => {
    const diff = (x.read_total ?? 0) - (x.written_total ?? 0);
    const state = x.status === "ok" ? ["✓", "ok", "ตรงยอด"] : x.status === "checked" ? ["✓", "ok", "ตรวจแล้ว"] : ["!", "warn", `ต่าง ${baht(Math.abs(diff))} ฿`];
    return el("div", { class: `acx-doc ${state[1]}` },
      el("span", { class: "acx-doc-icon", text: state[0] }),
      el("div", {}, el("button", { class: "linkbtn acx-doc-open", type: "button", text: x.title || `หน้า ${x.page}`, onclick: () => acxReview(x.id) }),
        el("div", { class: "small muted", text: [ACX_KIND[x.kind], x.file_name, x.note].filter(Boolean).join(" · ") })),
      el("div", { class: "acx-doc-num" }, el("strong", { text: x.written_total != null ? `${baht(x.written_total)} ฿` : "" }),
        el("div", { class: "small muted", text: x.status === "mismatch" ? `อ่านได้ ${baht(x.read_total)} · ${state[2]}` : state[2] }),
        x.status === "mismatch" ? el("button", { class: "linkbtn small", type: "button", text: "ดูต้นฉบับแล้ว ✓", onclick: () => acxDocChecked(x) }) : null));
  };
  return el("div", { class: "card acx-feeds" },
    el("h3", { text: `Expense documents · เอกสารเบิก${bad ? ` — ${bad} หน้าไม่ตรงยอด` : " — ทุกหน้าตรงยอด"}` }),
    el("p", { class: "muted small", text: "กดชื่อหน้าเพื่อเปิดรูปเทียบกับบรรทัดที่อ่าน (แก้ได้) · อ่านทีละหน้าแล้วบวกเทียบยอดที่เขียนท้ายหน้า · หน้าที่ตรงยอดไม่ต้องตรวจ · ทุกบรรทัดอยู่ในงบด้านล่าง (เงินเบิกกลางเดือนไม่ใช่ค่าใช้จ่าย — ไปหักตอนจ่ายเงินเดือน)" }),
    ...money.map(row),
    memos.length ? el("h3", { class: "acx-sub", text: `Memos · บันทึก / เอกสารบุคคล (${memos.length}) — สำหรับพี่แมว` }) : null,
    ...memos.map((x) => el("div", { class: "acx-doc memo" }, el("span", { class: "acx-doc-icon", text: "•" }),
      el("div", {}, el("button", { class: "linkbtn acx-doc-open", type: "button", text: x.title, onclick: () => acxReview(x.id) }),
        x.person ? el("span", { class: "muted small", text: ` · ${x.person}` }) : null,
        el("div", { class: "small", text: x.summary || "" })))));
}

async function acxDocChecked(x) {
  try { await api(`/api/accounts/docs/${x.id}/checked`, { method: "POST" }); } catch (err) { alert(err.message); return; }
  await loadAccounts();
}

// Cash fuel per truck against the km it drove: a truck that burns much more per km than the others stands out.
function acxFuel(f) {
  if (!f || !f.trucks || !f.trucks.length) return null;
  const sum = f.trucks.reduce((a, t) => a + t.cash, 0);
  const typedSum = f.table ? f.trucks.reduce((a, t) => a + (t.typed || 0), 0) : 0;
  return el("div", { class: "card acx-feeds" },
    el("h3", { text: "Fuel by truck · น้ำมันรายคัน" }),
    el("p", { class: "muted small", text: `เงินสด ${baht(sum)} ฿ ใน ${f.days} วัน · กม. ประมาณจาก GPS หรือเลขไมล์`
      + (f.has_card ? ` · สีแดง = บาท/กม. เกิน 2 เท่าของคันทั่วไป (${f.median_baht_per_km ?? "–"} ฿/กม.)` : " · ยังไม่มีน้ำมันบัตร (บางจาก / PT) — บาท/กม. จะมีความหมายเมื่อใส่บัตรแล้ว")
      + (f.no_truck ? ` · ไม่ระบุรถ ${baht(f.no_truck)} ฿` : "") + (f.table ? ` · ใบพิมพ์ของออฟฟิศ ${baht(f.table.total)} ฿ (ลายมือ ${baht(sum)} ฿ — ต่าง ${baht(sum - typedSum)} ฿ คือคันที่ไม่อยู่ในใบพิมพ์)` : "") }),
    el("div", { class: "acx-fuel" },
      el("div", { class: "acx-fuel-row head small muted" }, ...["รถ", "เงินสด (ครั้ง)", "บัตร", "ใบพิมพ์", "กม.", "฿/กม."].map((t) => el("span", { text: t }))),
      ...f.trucks.map((t) => el("div", { class: `acx-fuel-row${t.high ? " bad" : ""}` },
        el("span", { text: `#${t.number}${t.branch && t.branch !== $("acxBranch").value && $("acxBranch").value ? ` (${t.branch})` : ""}` }),
        el("strong", { text: `${baht(t.cash)} (${t.fills})` }), el("span", { class: "muted", text: t.card ? baht(t.card) : "–" }),
        el("span", { class: t.typed != null && t.typed !== t.cash ? "warn-text" : "muted", text: f.table ? (t.typed != null ? baht(t.typed) : "ไม่มี") : "" }),
        el("span", { class: "muted", text: t.km ? `${baht(t.km)} (${t.km_source})` : "ไม่มีข้อมูล" }),
        el("span", { text: t.baht_per_km ?? "–" })))));
}

function acxKpi(label, value, strong = false) {
  return el("div", { class: `kpi${strong ? (value < 0 ? " bad" : " ok") : ""}` }, el("div", { class: "kpi-label", text: label }), el("div", { class: "kpi-value", text: `${acxMoney(value)} ฿` }));
}

function acxNode(key, label, total, cls, children) {
  const open = acx.open.has(key);
  const head = el("button", { type: "button", class: `acx-line ${cls}`, "aria-expanded": String(open),
    onclick: () => { open ? acx.open.delete(key) : acx.open.add(key); renderAccounts(); } },
    el("span", { class: "acx-caret", text: open ? "▾" : "▸" }), el("span", { class: "acx-label", text: label }), el("strong", { text: acxMoney(total) }));
  return el("div", {}, head, open ? el("div", { class: "acx-kids" }, ...children()) : null);
}

function acxEntry(e) {
  const link = e.source === "truck_bill" ? el("a", { href: `#event=${e.source_id}`, text: "เปิดบิล" }) : null;
  const del = !["truck_bill", "purchase"].includes(e.source) ? el("button", { class: "linkbtn small", type: "button", text: "ลบ", onclick: () => acxDelete(e) }) : null;
  return el("div", { class: "acx-entry small" },
    el("span", { text: beDate(e.date) }), el("span", { class: "acx-label", text: [e.description, e.doc_no ? `เลขที่ ${e.doc_no}` : null, e.doc_type === "tax_invoice" ? "ใบกำกับภาษี" : null].filter(Boolean).join(" · ") || "–" }),
    el("span", { class: "muted", text: e.branch || "" }), link, del, el("strong", { text: acxMoney(e.amount) }));
}

async function acxMark(feed, period, status) {
  try { await api("/api/accounts/feed-mark", jsonOpts("POST", { feed, period, branch: $("acxBranch").value || "all", status })); } catch (err) { alert(err.message); return; }
  await loadAccounts();
}

async function acxDelete(e) {
  if (!confirm("ลบรายการนี้?")) return;
  try { await api(`/api/accounts/entries/${e.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
  await loadAccounts();
}

function acxAddDialog(preset) {
  let dlg = $("acxDialog");
  if (!dlg) { dlg = el("dialog", { id: "acxDialog", class: "wide" }); document.body.append(dlg); }
  const inp = (v, attrs = {}) => { const i = el("input", { type: "text", autocomplete: "off", ...attrs }); i.value = v ?? ""; return i; };
  const field = (label, node) => el("label", { class: "ty-field" }, el("span", { class: "muted small", text: label }), node);
  const date = inp(todayISO(), { type: "date" });
  const cap = el("div", { class: "datecap" });
  bindDateField(date, cap);
  const acc = el("select", {}, ...acx.data.accounts.filter((a) => ["cogs", "expense", "income"].includes(a.kind)).map((a) => el("option", { value: a.code, text: `${a.code} ${a.en} · ${a.th}` })));
  if (preset.account) acc.value = preset.account;
  const branch = el("select", {}, ...acx.data.branches.map((b) => el("option", { value: b, text: b })));
  branch.value = $("acxBranch").value || acx.data.branches[0];
  const amount = inp("", { inputmode: "decimal", placeholder: "ก่อน VAT" });
  const vat = inp("", { inputmode: "decimal", placeholder: "0 ถ้าไม่มีใบกำกับภาษี" });
  const vatAuto = el("button", { class: "linkbtn small", type: "button", text: "= 7%", onclick: () => { vat.value = (Number(amount.value.replace(/,/g, "")) * 0.07).toFixed(2); } });
  const paid = el("select", {}, ...[["ap", "ยังไม่จ่าย (ค้างร้าน)"], ["mum", "จ่ายแล้ว — เช็ค/โอน คุณรัชนิดา"], ["company", "จ่ายแล้ว — บัญชีบริษัท"], ["hq", "จ่ายแล้ว — HQ"], ["cash", "จ่ายแล้ว — เงินสด"]]
    .map(([v, t]) => el("option", { value: v, text: t })));
  const party = inp("", { placeholder: "เช่น PEA / ชลประทาน / ร้าน" });
  const docNo = inp("", { placeholder: "เลขที่เอกสาร (ถ้ามี)" });
  const desc = inp("", { placeholder: "รายละเอียด" });
  const err = el("p", { class: "error", hidden: true });
  const save = el("button", { class: "btn primary", type: "button", text: "บันทึก" });
  save.onclick = async () => {
    save.disabled = true;
    err.hidden = true;
    try {
      await api("/api/accounts/entries", jsonOpts("POST", { entry_date: date.value, account: acc.value, branch: branch.value, amount: amount.value, vat: vat.value || "0",
        paid_from: paid.value, party: party.value, doc_no: docNo.value, description: desc.value, feed: preset.feed || null }));
      dlg.close();
      await loadAccounts();
    } catch (e) { err.textContent = e.message; err.hidden = false; }
    save.disabled = false;
  };
  fill(dlg, el("h3", { text: "บันทึกรายการ" }),
    el("p", { class: "muted small", text: "วันที่ = วันที่ของมาถึง / ใช้บริการ (ไม่ใช่วันจ่ายเงิน) · ใส่ VAT เฉพาะเมื่อมีใบกำกับภาษี" }),
    el("div", { class: "ty-fields" }, field("วันที่", el("div", {}, date, cap)), field("หมวดบัญชี", acc), field("สาขา (ที่ใช้)", branch),
      field("จำนวนเงิน (ก่อน VAT)", amount), field("VAT", el("div", { class: "acx-vat" }, vat, vatAuto)), field("จ่ายจาก", paid),
      field("ร้าน / ผู้รับเงิน", party), field("เลขที่เอกสาร", docNo), field("รายละเอียด", desc)),
    err, el("div", { class: "dialog-actions" }, el("span", { class: "grow" }), el("button", { class: "btn", type: "button", text: "ยกเลิก", onclick: () => dlg.close() }), save));
  dlg.showModal();
}


// ตรวจเอกสาร: the scan on one side, the lines read from it on the other. Fix an amount / account / truck, add or remove a line;
// the page total re-checks against what is written on the paper. app/acc_docs.py doc_detail / save_lines.
async function acxReview(docId) {
  let dlg = $("acxReview");
  if (!dlg) { dlg = el("dialog", { id: "acxReview", class: "acx-review" }); document.body.append(dlg); }
  fill(dlg, el("p", { class: "muted", text: "กำลังโหลด…" }));
  if (!dlg.open) dlg.showModal();
  let d;
  try { d = await api(`/api/accounts/docs/${docId}`); } catch (err) { fill(dlg, el("p", { class: "error", text: err.message }), el("button", { class: "btn", type: "button", text: "ปิด", onclick: () => dlg.close() })); return; }
  const doc = d.doc;
  const money = !["memo", "other", "cover", "fuel_table"].includes(doc.kind);
  const rows = d.lines.map((ln) => ({ ...ln, _orig: JSON.stringify(ln) }));
  const truckAcc = new Set(["6100", "6110", "6120", "6130"]);
  const vehLabel = (v) => `#${v.vehicle_number} ${v.plate_number || ""}${v.branch === "แก่งคอย" ? " (แก่งคอย)" : ""}`;
  const err = el("p", { class: "error", hidden: true });
  const totalBox = el("div", { class: "acx-rv-total" });
  const body = el("div", { class: "acx-rv-lines" });

  const sum = () => rows.filter((r) => !r._delete).reduce((a, r) => a + (Number(String(r.amount).replace(/,/g, "")) || 0), 0);
  const paintTotal = () => {
    if (!money) { fill(totalBox, null); return; }
    const s = Math.round(sum() * 100) / 100, w = doc.written_total;
    const ok = w == null || Math.abs(s - w) < 0.5;
    fill(totalBox, el("span", { class: ok ? "ok-text" : "warn-text", text: ok ? `✓ ตรงยอด ${baht(s)} ฿` : `รวมบรรทัด ${baht(s)} ฿ · บนกระดาษ ${baht(w)} ฿ · ต่าง ${baht(Math.abs(s - w))} ฿` }));
  };
  const rowEl = (r) => {
    const date = el("input", { type: "date", value: r.date || "" });
    const desc = el("input", { type: "text", value: r.description || "" });
    const amt = el("input", { type: "text", inputmode: "decimal", value: r.amount != null ? String(r.amount) : "" });
    const acc = el("select", {}, ...d.accounts.map((a) => el("option", { value: a.code, text: `${a.code} ${a.name_th}` })));
    acc.value = r.account || "7900";
    const veh = el("select", {}, el("option", { value: "", text: "— ไม่ระบุรถ —" }), ...d.vehicles.map((v) => el("option", { value: v.id, text: vehLabel(v) })));
    veh.value = r.vehicle_id || "";
    veh.hidden = !truckAcc.has(acc.value) && !r.vehicle_id;
    const del = el("button", { class: "linkbtn small", type: "button", text: "ลบ" });
    const line = el("div", { class: "acx-rv-line" }, date, desc, amt, acc, veh, del);
    const sync = () => {
      Object.assign(r, { date: date.value, description: desc.value, amount: amt.value, account: acc.value, vehicle_id: veh.value || null });
      veh.hidden = !truckAcc.has(acc.value) && !veh.value;
      paintTotal();
    };
    for (const x of [date, desc, amt, acc, veh]) x.oninput = x.onchange = sync;
    del.onclick = () => { r._delete = !r._delete; line.classList.toggle("deleted", r._delete); del.textContent = r._delete ? "เอาคืน" : "ลบ"; paintTotal(); };
    return line;
  };
  const paintRows = () => fill(body, ...rows.map(rowEl));
  const add = el("button", { class: "btn small", type: "button", text: "+ เพิ่มบรรทัด", onclick: () => {
    const last = rows[rows.length - 1];
    rows.push({ id: null, date: last ? last.date : todayISO(), description: "", amount: "", account: last ? last.account : "7900", vehicle_id: null, _orig: "" });
    paintRows(); paintTotal();
  } });
  const save = el("button", { class: "btn primary", type: "button", text: "บันทึก" });
  save.onclick = async () => {
    const changes = rows.filter((r) => r._delete ? r.id : (!r.id || JSON.stringify({ ...r, _orig: undefined, _delete: undefined }) !== JSON.stringify({ ...JSON.parse(r._orig || "{}"), _orig: undefined })))
      .map((r) => r._delete ? { id: r.id, delete: true } : { id: r.id, date: r.date, description: r.description, amount: r.amount, account: r.account, vehicle_id: r.vehicle_id, branch: r.branch });
    if (!changes.length) { dlg.close(); return; }
    save.disabled = true; err.hidden = true;
    try { await api(`/api/accounts/docs/${docId}/lines`, jsonOpts("POST", { changes })); dlg.close(); await loadAccounts(); }
    catch (e) { err.textContent = e.message; err.hidden = false; }
    save.disabled = false;
  };
  const checked = doc.status === "mismatch" ? el("button", { class: "btn", type: "button", text: "ดูต้นฉบับแล้ว ✓", onclick: async () => { await acxDocChecked(doc); dlg.close(); } }) : null;
  const img = doc.has_image ? el("img", { class: "acx-rv-img", alt: doc.title || "", src: `/api/accounts/docs/${docId}/image?token=${encodeURIComponent(state.token)}` }) : el("p", { class: "muted", text: "ไม่มีรูปของหน้านี้" });
  img.onclick = () => img.classList.toggle("zoom");
  fill(dlg,
    el("div", { class: "acx-rv-head" }, el("h3", { text: doc.title || `หน้า ${doc.page}` }),
      el("span", { class: "muted small", text: [doc.file_name, doc.person, doc.note].filter(Boolean).join(" · ") }),
      el("button", { class: "btn small", type: "button", text: "ปิด", onclick: () => dlg.close() })),
    el("div", { class: "acx-rv" },
      el("div", { class: "acx-rv-scan" }, img, el("p", { class: "muted small", text: "กดที่รูปเพื่อขยาย" })),
      el("div", { class: "acx-rv-side" },
        money ? el("div", { class: "acx-rv-line head small muted" }, ...["วันที่", "รายการ", "บาท", "หมวด", "รถ", ""].map((t) => el("span", { text: t }))) : null,
        money ? body : el("p", { class: "small", text: doc.summary && !doc.summary.startsWith("{") ? doc.summary : "" }),
        money ? el("div", { class: "acx-rv-foot" }, add, totalBox) : null,
        err,
        el("div", { class: "dialog-actions" }, el("span", { class: "grow" }), checked, money ? save : null))));
  if (money) { paintRows(); paintTotal(); }
}
