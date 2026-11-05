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
  fill($("acxBody"), summary, feeds, tree);
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
