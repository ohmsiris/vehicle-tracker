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

// Two pages on one screen: "in" = what has to come in (checklist, files, documents to review, typing a cost) and "out" = the
// statement (totals, P&L, fuel by truck, electricity). Same month / half / branch for both.
async function openAccounts(mode = "out") {
  acx.mode = mode;
  showView("accounts");
  $("navAccounts").classList.toggle("active", mode === "out");
  $("navAccountsIn").classList.toggle("active", mode === "in");
  $("acxTitle").textContent = mode === "in" ? "Inputs · ลงข้อมูล / แนบไฟล์" : "Profit & loss · งบกำไรขาดทุน";
  $("acxAdd").hidden = mode !== "in";
  $("acxViews").hidden = mode !== "out";                     // the ต้นทุนต่อหน่วย tab belongs to the P&L, not to ลงข้อมูล
  if (mode !== "out") acx.view = "pnl";
  $("acxMonth").querySelector('option[value="year"]')?.toggleAttribute("hidden", mode === "in");
  if (mode === "in" && $("acxMonth").value === "year") $("acxMonth").value = acxMonths()[0];
  showErr("acxError", "");
  if (!acx.wired) {
    acx.wired = true;
    $("acxMonth").replaceChildren(el("option", { value: "year", text: "ทั้งปี · 12 เดือนล่าสุด (ภาพรวม)" }), ...acxMonths().map((k) => el("option", { value: k, text: acxMonthLabel(k) })));
    $("acxMonth").value = acxMonths()[0];
    for (const id of ["acxMonth", "acxHalf", "acxBranch"]) $(id).onchange = loadAccounts;
    $("acxAdd").onclick = () => acxAddDialog({});
    const view = (v) => { acx.view = v; $("acxViewPnl").classList.toggle("active", v === "pnl"); $("acxViewUnit").classList.toggle("active", v === "unit"); showErr("acxError", ""); loadAccounts(); };
    $("acxViewPnl").onclick = () => view("pnl");
    $("acxViewUnit").onclick = () => view("unit");
  }
  await loadAccounts();
}

async function loadAccounts() {
  const seq = acx.seq = (acx.seq || 0) + 1;                  // a slower, older load must not draw over a newer one
  if (acx.mode === "out" && acx.view === "unit") { await loadUnitCost(seq); return; }    // unitcost.js
  const yearView = $("acxMonth").value === "year";
  $("acxHalf").hidden = yearView;
  const q = new URLSearchParams({ month: $("acxMonth").value, half: $("acxHalf").value, branch: $("acxBranch").value });
  fill($("acxBody"), el("p", { class: "muted", text: "กำลังคำนวณ…" }));
  try {
    const yq = `/api/accounts/year?branch=${encodeURIComponent($("acxBranch").value)}`;
    if (yearView) { const y = await api(yq); if (seq !== acx.seq) return; acx.year = y; acx.yearBranch = $("acxBranch").value; renderYear(); return; }
    const [rep, yr] = await Promise.all([api(`/api/accounts/report?${q}`),
      acx.mode === "out" && acx.yearBranch !== $("acxBranch").value ? api(yq).catch(() => null) : Promise.resolve(acx.year)]);
    if (seq !== acx.seq) return;
    acx.data = rep;
    if (yr) { acx.year = yr; acx.yearBranch = $("acxBranch").value; }
  } catch (err) {
    if (seq !== acx.seq) return;
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
      const icon = f.state === "auto" ? "✓" : f.state === "done" ? "✓" : f.state === "none" ? "–" : f.state === "received" ? "📎" : "○";
      const myFiles = f.from !== "program" ? (d.files || []).filter((x) => x.feed === f.key && (!half || Number(x.period.slice(-1)) === Number(half))) : [];
      return el("div", { class: `acx-feed ${f.state}` },
        el("span", { class: "acx-feed-icon", text: icon }),
        el("div", {}, el("strong", { text: f.en }), el("span", { class: "muted small", text: ` · ${f.th} · ${ACX_FROM[f.from]}` }),
          el("div", { class: "small muted", text: f.state === "auto" ? `อยู่ในโปรแกรมแล้ว · ${f.entries} รายการ · ${baht(f.total)} ฿${f.note ? ` · ${f.note}` : ""}`
            : f.state === "done" ? `ใส่แล้ว${f.entries ? ` · ${f.entries} รายการ · ${baht(f.total)} ฿` : ""}` : f.state === "none" ? "ไม่มีในงวดนี้"
            : f.state === "received" ? "ได้รับไฟล์แล้ว · ยังไม่ได้ลงตัวเลข" : "ยังไม่ได้ใส่" }),
          myFiles.length ? el("div", { class: "acx-files" }, ...myFiles.map(acxFileChip)) : null),
        f.from !== "program" ? el("div", { class: "acx-feed-actions" },
          el("button", { class: "btn small", type: "button", text: "📎 แนบไฟล์", onclick: () => acxUpload(f) }),
          el("button", { class: "btn small", type: "button", text: "+ ใส่", onclick: () => acxAddDialog({ feed: f.key, account: f.account }) }),
          el("button", { class: "btn small", type: "button", text: f.state === "none" ? "ยกเลิก" : "ไม่มีงวดนี้",
            onclick: () => acxMark(f.key, periodKey, f.state === "none" ? "" : "none") })) : null);
    }));
  const prev = !half && acx.year ? acx.year.months.find((x) => x.month === acxPrevMonth(d.month)) : null;
  const summary = el("div", { class: "acx-kpis" },
    acxTile("Revenue · รายได้", p.revenue, p.revenue, prev && prev.revenue, "up", prev),
    acxTile("Cost of goods sold · ต้นทุนขาย", p.cogs, p.revenue, prev && prev.cogs, "down", prev),
    acxTile("Gross profit · กำไรขั้นต้น", p.gross_profit, p.revenue, prev && prev.gross_profit, "up", prev, true),
    acxTile("Expenses · ค่าใช้จ่าย", p.expenses, p.revenue, prev && prev.expenses, "down", prev),
    acxTile("Operating profit · กำไรจากการดำเนินงาน", p.operating_profit, p.revenue, prev && prev.operating_profit, "up", prev, true),
    acxTile("Net profit · กำไรสุทธิ (หลังจ่ายหนี้)", p.net_profit, p.revenue, prev && prev.net_profit, "up", prev, true));
  const tree = el("div", { class: "card acx-tree" },
    el("h3", { text: "Profit & loss · งบกำไรขาดทุน" }),
    el("p", { class: "muted small", text: "กดที่บรรทัดเพื่อเปิดดูละเอียดขึ้น: หมวด → บัญชี → ร้าน → แต่ละบิล · ไม่รวม VAT (VAT ไปที่บัญชีภาษีซื้อ) · % = ส่วนของรายได้ · รายได้อื่นแยกไว้ล่างสุด" }),
    ...p.sections.flatMap((s) => [
      s.key === "finance" ? el("div", { class: `acx-line acx-total ${p.operating_profit < 0 ? "neg" : "pos"}` },
        el("span", { text: "Operating profit · กำไรจากการดำเนินงาน (ก่อนจ่ายหนี้)" }),
        el("span", { class: "acx-pct", text: acxPct(p.operating_profit, p.revenue) }), el("strong", { text: acxMoney(p.operating_profit) })) : null,
      acxNode(`s:${s.key}`, `${s.en} · ${s.th}`, s.total, "acx-l0",
      () => s.accounts.map((a) => acxNode(`a:${a.code}`, `${a.en} · ${a.th}`, a.total, "acx-l1",
        () => a.parties.map((pt) => acxNode(`a:${a.code}:${pt.party}`, pt.party, pt.total, "acx-l2",
          () => pt.entries.map((e) => acxEntry(e)))))))]).filter(Boolean),
    el("div", { class: `acx-line acx-total ${p.net_profit < 0 ? "neg" : "pos"}` }, el("span", { text: "Net profit · กำไรสุทธิ" }),
      el("span", { class: "acx-pct", text: acxPct(p.net_profit, p.revenue) }), el("strong", { text: acxMoney(p.net_profit) })));
  const toIn = el("p", { class: "muted small" }, "ยังขาดอะไรอยู่ ดูและแนบไฟล์ได้ที่ ", el("a", { href: "#accounts-in", text: "ลงข้อมูล / แนบไฟล์" }),
    ` (${d.feeds.filter((f) => f.state === "missing" || f.state === "received").length} รายการยังไม่ครบ)`);
  const toOut = el("p", { class: "muted small" }, "ดูผลที่ ", el("a", { href: "#accounts", text: "งบกำไรขาดทุน" }));
  const toElec = (d.electricity || []).length ? el("p", { class: "muted small" }, "ค่าไฟแยกมิเตอร์ กราฟ demand / Peak / power factor: ", el("a", { href: "#electricity", text: "หน้าค่าไฟฟ้า" })) : null;
  const toRoutes = el("p", { class: "muted small" }, "กำไรต่อคัน / น้ำมันต่อคัน: ", el("a", { href: "#routes", text: "หน้ารถสาย" }), " · ภาพรวมทั้งปี: เลือก \"ทั้งปี\" ที่ช่องเดือน");
  acx.rev = p.revenue;
  if (acx.mode === "in") fill($("acxBody"), toOut, feeds, acxDocs(d.docs || []), acxFuel(d.fuel));
  else fill($("acxBody"), summary, toIn, toElec, toRoutes, tree);
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
    el("span", { class: "acx-caret", text: open ? "▾" : "▸" }), el("span", { class: "acx-label", text: label }),
    cls === "acx-l2" ? el("span") : el("span", { class: "acx-pct", text: acxPct(total, acx.rev) }), el("strong", { text: acxMoney(total) }));
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


// แนบไฟล์: keep the paper behind a checklist row (PDF / photo / Excel) for this period. Monthly rows are filed under their own half.
function acxUpload(f) {
  const month = $("acxMonth").value;
  const half = $("acxHalf").value || String(f.half || 0);
  const pick = el("input", { type: "file", multiple: true, accept: ".pdf,.jpg,.jpeg,.png,.webp,.heic,.xls,.xlsx,.csv" });
  pick.onchange = async () => {
    if (!pick.files.length) return;
    const fd = new FormData();
    for (const file of pick.files) fd.append("files", file);
    fd.append("feed", f.key);
    fd.append("period", `${month}-${half}`);
    fd.append("branch", $("acxBranch").value || "all");
    showErr("acxError", "");
    try { await api("/api/accounts/files", { method: "POST", body: fd }); }
    catch (err) { showErr("acxError", `แนบไฟล์ไม่สำเร็จ: ${err.message}`); return; }
    await loadAccounts();
  };
  pick.click();
}

function acxFileChip(x) {
  const kb = x.byte_size ? (x.byte_size > 1048576 ? `${(x.byte_size / 1048576).toFixed(1)} MB` : `${Math.round(x.byte_size / 1024)} KB`) : "";
  return el("span", { class: "acx-file" },
    el("a", { href: `/api/accounts/files/${x.id}?token=${encodeURIComponent(state.token)}`, target: "_blank", rel: "noopener", text: x.file_name }),
    el("span", { class: "muted", text: ` ${kb}${x.branch === "all" ? " · ทั้งสองสาขา" : ""}` }),
    el("button", { class: "linkbtn small", type: "button", title: "ลบไฟล์", text: "×", onclick: async () => {
      if (!confirm(`ลบไฟล์ ${x.file_name}?`)) return;
      try { await api(`/api/accounts/files/${x.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
      await loadAccounts();
    } }));
}


// ไฟฟ้า: every PEA bill read from its PDF, per meter, newest month first -- what it cost and why.
function acxElectricity(meters) {
  if (!meters.length) return null;
  const n = (x, d = 0) => (x == null ? "–" : Number(x).toLocaleString("th-TH", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
  const mon = (p) => { const [y, m] = p.split("-").map(Number); return `${TH[m - 1]} ${String(y + 543).slice(2)}`; };
  const head = ["เดือน", "หน่วย (kWh)", "Peak %", "Demand peak kW", "ค่า demand", "kVAR / เพดาน", "ค่า PF", "รวม (บาท)", "บาท/หน่วย"];
  return el("div", { class: "card acx-feeds" },
    el("h3", { text: "Electricity · ค่าไฟฟ้า (อ่านจากใบแจ้ง PEA)" }),
    el("p", { class: "muted small", text: "ค่า demand คิดจาก kW สูงสุดช่วง Peak (จ–ศ 09:00–22:00) เท่านั้น · Peak % = สัดส่วนหน่วยที่ใช้ช่วงแพง (ค่าหน่วย Peak แพงกว่า Off-peak ~60%) · ค่า PF = kVAR เกิน 61.97% ของ kW สูงสุด (คาปาซิเตอร์ไม่พอ)" }),
    ...meters.map((m) => el("div", { class: "acx-elec" },
      el("strong", { text: `มิเตอร์ ${m.meter} · ${m.branch}` }), el("span", { class: "muted small", text: ` CA ${m.ca_no}` }),
      el("div", { class: "acx-elec-row head small muted" }, ...head.map((t) => el("span", { text: t }))),
      ...m.months.map((x) => el("div", { class: "acx-elec-row" },
        el("span", { text: mon(x.bill_period) }),
        el("span", { text: n(x.kwh_total) }),
        el("span", { class: x.peak_share > 0.3 ? "warn-text" : "", text: `${n(x.peak_share * 100)}%` }),
        el("span", { text: n(x.demand_peak_kw, 1) }),
        el("span", { text: n(x.demand_charge) }),
        el("span", { class: x.kvar_over > 0 ? "warn-text" : "muted", text: `${n(x.kvar_max, 1)} / ${n(x.kvar_limit, 1)}` }),
        el("span", { class: x.pf_charge ? "warn-text" : "muted", text: x.pf_charge ? n(x.pf_charge, 2) : "–" }),
        el("strong", { text: n(x.total, 2) }),
        el("span", { text: n(x.baht_per_kwh, 2) }))))));
}


// ---- % of revenue, change from last month, coloring (green = better, red = worse)
const acxPct = (v, rev) => (rev ? `${elN((v / rev) * 100, 1)}%` : "");
function acxPrevMonth(m) { const [y, mm] = m.split("-").map(Number); return mm === 1 ? `${y - 1}-12` : `${y}-${String(mm - 1).padStart(2, "0")}`; }
// good = "up" (more is better: revenue, profit) or "down" (less is better: costs). A profit line is red / green by its sign too.
function acxTile(label, value, revenue, before, good, prev, signed = false) {
  const ch = before ? (value - before) / Math.abs(before) : null;
  const better = ch == null || Math.abs(ch) < 0.005 ? null : (ch > 0) === (good === "up");
  const share = label.startsWith("Revenue") ? null : acxPct(value, revenue);
  return el("div", { class: `kpi el-kpi${signed ? (value < 0 ? " bad" : " ok") : ""}` },
    el("div", { class: "kpi-label", text: label }), el("div", { class: "kpi-value", text: `${acxMoney(value)} ฿` }),
    share ? el("div", { class: "el-delta", text: `${share} ของรายได้` }) : null,
    ch == null ? null : el("div", { class: `el-delta ${better == null ? "" : better ? "up-good" : "up-bad"}`, text: `${ch > 0 ? "▲" : "▼"} ${elN(Math.abs(ch) * 100, 1)}% จาก ${elMon(prev.month)}` }));
}

// ---- ทั้งปี: the big lines only, month by month
const ACX_BIG = [["revenue", "Revenue · รายได้", "up"], ["cogs", "Cost of goods sold · ต้นทุนขาย", "down"], ["gross_profit", "Gross profit · กำไรขั้นต้น", "up"],
  ["selling", "Selling & delivery · ขายและขนส่ง", "down"], ["admin", "Administrative · บริหาร", "down"], ["other", "Other income · รายได้อื่น", "up"],
  ["operating_profit", "Operating profit · กำไรจากการดำเนินงาน", "up"], ["finance", "Debt payments · ภาระหนี้", "down"],
  ["net_profit", "Net profit · กำไรสุทธิ", "up"]];
function renderYear() {
  const y = acx.year, ms = y.months;
  if (!ms.length) { fill($("acxBody"), el("div", { class: "card" }, el("p", { class: "muted", text: "ยังไม่มีเดือนที่ลงรายได้" }))); return; }
  const tot = Object.fromEntries(ACX_BIG.map(([k]) => [k, ms.reduce((a, m) => a + (m[k] || 0), 0)]));
  const best = ms.reduce((a, m) => (m.net_profit > a.net_profit ? m : a)), worst = ms.reduce((a, m) => (m.net_profit < a.net_profit ? m : a));
  const tile = (k, label, signed) => el("div", { class: `kpi el-kpi${signed ? (tot[k] < 0 ? " bad" : " ok") : ""}` }, el("div", { class: "kpi-label", text: label }),
    el("div", { class: "kpi-value", text: `${elShort(tot[k])} ฿` }), k === "revenue" ? el("div", { class: "el-delta", text: `${ms.length} เดือน · เฉลี่ย ${elShort(tot.revenue / ms.length)}/เดือน` })
      : el("div", { class: "el-delta", text: `${acxPct(tot[k], tot.revenue)} ของรายได้` }));
  const tiles = el("div", { class: "acx-kpis" }, tile("revenue", "Revenue · รายได้"), tile("cogs", "Cost of goods sold · ต้นทุนขาย"),
    tile("gross_profit", "Gross profit · กำไรขั้นต้น", true), el("div", { class: "kpi el-kpi" }, el("div", { class: "kpi-label", text: "Expenses · ค่าใช้จ่าย" }),
      el("div", { class: "kpi-value", text: `${elShort(tot.selling + tot.admin)} ฿` }), el("div", { class: "el-delta", text: `${acxPct(tot.selling + tot.admin, tot.revenue)} ของรายได้` })),
    tile("net_profit", "Net profit · กำไรสุทธิ", true),
    el("div", { class: "kpi el-kpi" }, el("div", { class: "kpi-label", text: "เดือนดีสุด / แย่สุด" }),
      el("div", { class: "el-delta up-good", text: `▲ ${elMon(best.month)} · ${elShort(best.net_profit)} (${acxPct(best.net_profit, best.revenue)})` }),
      el("div", { class: `el-delta ${worst.net_profit < 0 ? "up-bad" : ""}`, text: `▼ ${elMon(worst.month)} · ${elShort(worst.net_profit)} (${acxPct(worst.net_profit, worst.revenue)})` })));
  const branchNote = $("acxBranch").value ? "" : " · รวมทุกสาขา (ไม่นับน้ำแข็งที่ส่งกันเองระหว่างสาขา)";
  fill($("acxBody"),
    el("p", { class: "muted small", text: `เฉพาะหัวข้อใหญ่ ไม่มีรายละเอียด — ดูรายละเอียดทีละเดือนได้โดยเลือกเดือนที่ช่องด้านบน${branchNote} · ม.ค.–เม.ย. น้ำมันยังไม่ครบบางส่วน` }),
    tiles,
    el("div", { class: "el-grid" },
      el("div", { class: "card el-panel" }, elChart("กำไรสุทธิรายเดือน", "เขียว = กำไร · แดง = ขาดทุน · ตัวเลข = % ของรายได้", acxProfitBars(ms))),
      el("div", { class: "card el-panel" }, elChart("รายได้ เทียบ ต้นทุน + ค่าใช้จ่าย", "ช่องว่างระหว่างสองเส้น = กำไร",
        acxTwoLines(ms), elLegend([["รายได้", "var(--el-off)"], ["ต้นทุน + ค่าใช้จ่าย", "var(--el-peak)"]]))),
      el("div", { class: "card el-panel" }, elChart("ต้นทุนและค่าใช้จ่าย เป็น % ของรายได้", "ยิ่งสูงยิ่งเหลือกำไรน้อย",
        elStack(ms.map((m) => ({ bill_period: m.month, cogs: m.revenue ? (m.cogs / m.revenue) * 100 : 0, selling: m.revenue ? (m.selling / m.revenue) * 100 : 0, admin: m.revenue ? (m.admin / m.revenue) * 100 : 0 })),
          [["cogs", "ต้นทุนขาย", "var(--el-peak)"], ["selling", "ขายและขนส่ง", "var(--el-off)"], ["admin", "บริหาร", "var(--el-dem)"]], (v) => `${elN(v, 1)}%`),
        elLegend([["ต้นทุนขาย", "var(--el-peak)"], ["ขายและขนส่ง", "var(--el-off)"], ["บริหาร", "var(--el-dem)"]]))),
      el("div", { class: "card el-panel" }, elChart("ค่าใช้จ่ายก้อนใหญ่ของปี", "10 อันดับแรก · % ของรายได้ทั้งปี", acxTopCosts(y.accounts, tot.revenue)))),
    acxYearTable(ms, tot));
}

function acxProfitBars(ms) {
  const W = EL_W, H = EL_H, P = EL_P;
  const max = Math.max(...ms.map((m) => m.net_profit), 0), min = Math.min(...ms.map((m) => m.net_profit), 0);
  const step = elNiceStep((max - min || 1) / 4), top = Math.ceil(max / step) * step, bot = Math.floor(min / step) * step;
  const y = (v) => P.t + (H - P.t - P.b) * ((top - v) / (top - bot || 1));
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "el-svg", role: "img" });
  for (let v = bot; v <= top + step / 2; v += step) svg.append(sv("line", { x1: P.l, x2: W - P.r, y1: y(v), y2: y(v), class: Math.abs(v) < 1e-6 ? "el-base" : "el-gl" }),
    sv("text", { x: P.l - 6, y: y(v) + 4, "text-anchor": "end", class: "el-tick" }, document.createTextNode(v < 0 ? `-${elShort(-v)}` : elShort(v))));
  const s = elSlots(ms.length);
  ms.forEach((m, i) => {
    const v = m.net_profit, bx = s.x(i) + (s.w - s.bar) / 2, g = sv("g", { class: "el-hit" });
    g.append(sv("rect", { x: bx, y: Math.min(y(v), y(0)), width: s.bar, height: Math.abs(y(v) - y(0)), rx: 4, fill: v < 0 ? "var(--bad)" : "var(--ok)" }),
      sv("text", { x: bx + s.bar / 2, y: v < 0 ? y(v) + 13 : y(v) - 5, "text-anchor": "middle", class: "el-tick" }, document.createTextNode(acxPct(v, m.revenue))),
      sv("rect", { x: s.x(i), y: P.t, width: s.w, height: H - P.t - P.b, fill: "transparent" }));
    elTip(g, () => [elMon(m.month), ["รายได้", `${elN(m.revenue)} ฿`], ["ต้นทุน + ค่าใช้จ่าย", `${elN(m.cogs + m.selling + m.admin)} ฿`],
      ["กำไรสุทธิ", `${elN(v)} ฿ · ${acxPct(v, m.revenue)}`, v < 0 ? "var(--bad)" : "var(--ok)"]]);
    svg.append(g);
  });
  elXLabels(svg, ms.map((m) => ({ bill_period: m.month })), s);
  return svg;
}

function acxTwoLines(ms) {
  const cost = ms.map((m) => m.cogs + m.selling + m.admin);
  const { svg, y } = elFrame(Math.max(...ms.map((m) => m.revenue), ...cost) * 1.05, elShort);
  const s = elSlots(ms.length);
  const pts = (vals) => vals.map((v, i) => [s.x(i) + s.w / 2, y(v)]);
  const a = pts(ms.map((m) => m.revenue)), b = pts(cost);
  svg.append(sv("polyline", { points: a.map((p) => p.join(",")).join(" "), class: "el-line" }),
    sv("polyline", { points: b.map((p) => p.join(",")).join(" "), class: "el-line", style: "stroke: var(--el-peak)" }));
  ms.forEach((m, i) => {
    const g = sv("g", { class: "el-hit" });
    g.append(sv("circle", { cx: a[i][0], cy: a[i][1], r: 4.5, class: "el-dot" }), sv("circle", { cx: b[i][0], cy: b[i][1], r: 4.5, class: "el-dot", style: "fill: var(--el-peak)" }),
      sv("rect", { x: s.x(i), y: EL_P.t, width: s.w, height: EL_H - EL_P.t - EL_P.b, fill: "transparent" }));
    elTip(g, () => [elMon(m.month), ["รายได้", `${elN(m.revenue)} ฿`, "var(--el-off)"], ["ต้นทุน + ค่าใช้จ่าย", `${elN(cost[i])} ฿`, "var(--el-peak)"],
      ["ส่วนต่าง", `${elN(m.revenue - cost[i])} ฿`]]);
    svg.append(g);
  });
  elXLabels(svg, ms.map((m) => ({ bill_period: m.month })), s);
  return svg;
}

function acxTopCosts(accounts, revenue) {
  const rows = accounts.filter((a) => ["cogs", "selling", "admin"].includes(a.section) && a.total > 0).slice(0, 10);
  const W = EL_W, lab = W < 400 ? 120 : 190, H = 20, gap = 6, max = Math.max(...rows.map((r) => r.total), 1);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${rows.length * (H + gap) + 6}`, class: "el-svg", role: "img" });
  const col = { cogs: "var(--el-peak)", selling: "var(--el-off)", admin: "var(--el-dem)" };
  rows.forEach((r, i) => {
    const yy = i * (H + gap) + 3, w = (W - lab - 60) * (r.total / max), g = sv("g", { class: "el-hit" });
    g.append(sv("text", { x: 0, y: yy + H / 2 + 4, class: "el-tick rt-lab" }, document.createTextNode(r.th.slice(0, W < 400 ? 16 : 26))),
      sv("rect", { x: lab, y: yy, width: Math.max(1, w), height: H, rx: 4, fill: col[r.section] }),
      sv("text", { x: lab + w + 6, y: yy + H / 2 + 4, class: "el-tick" }, document.createTextNode(acxPct(r.total, revenue))),
      sv("rect", { x: 0, y: yy - gap / 2, width: W, height: H + gap, fill: "transparent" }));
    elTip(g, () => [`${r.en} · ${r.th}`, ["ทั้งปี", `${elN(r.total)} ฿`], ["% ของรายได้", acxPct(r.total, revenue)]]);
    svg.append(g);
  });
  return svg;
}

// the table: big lines x months; each cell the amount (thousands) and its % of revenue, colored against the year's own average
function acxYearTable(ms, tot) {
  const cols = `190px repeat(${ms.length + 1}, 76px)`;             // fixed widths: every row lines up
  const head = el("div", { class: "acx-yr-row head small muted", style: `grid-template-columns:${cols}` }, el("span", { text: "พันบาท" }),
    ...ms.map((m) => el("span", { text: elMon(m.month) })), el("strong", { text: "ทั้งปี" }));
  const rows = ACX_BIG.map(([k, label, good]) => {
    const avg = tot.revenue ? tot[k] / tot.revenue : 0;
    const cell = (v, rev, isTotal) => {
      const share = rev ? v / rev : 0;
      let tone = "";
      if (k === "net_profit" || k === "gross_profit" || k === "operating_profit") tone = v < 0 ? "bad" : "ok";
      else if (k !== "revenue" && !isTotal && rev && Math.abs(share - avg) >= 0.03) tone = (share > avg) === (good === "down") ? "bad" : "ok";
      return el("span", { class: `acx-yr-cell ${tone}` }, el("b", { text: elN(v / 1000) }), k === "revenue" ? null : el("small", { text: acxPct(v, rev) }));
    };
    return el("div", { class: `acx-yr-row${k === "net_profit" ? " total" : ""}${k === "gross_profit" || k === "operating_profit" ? " sub" : ""}`, style: `grid-template-columns:${cols}` },
      el("span", { class: "acx-yr-label", text: label }), ...ms.map((m) => cell(m[k], m.revenue, false)), cell(tot[k], tot.revenue, true));
  });
  return el("div", { class: "card acx-feeds" }, el("h3", { text: "รายเดือน · หัวข้อใหญ่" }),
    el("p", { class: "muted small", text: "ตัวเลขเป็นพันบาท · ตัวเล็ก = % ของรายได้เดือนนั้น · ต้นทุน/ค่าใช้จ่าย: แดง = สูงกว่าค่าเฉลี่ยทั้งปีเกิน 3 จุด, เขียว = ต่ำกว่า · กำไร: เขียว = กำไร, แดง = ขาดทุน" }),
    el("div", { class: "acx-yr" }, head, ...rows));
}
