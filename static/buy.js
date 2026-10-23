"use strict";

// Buying for the factory (เครื่องจักร group):
//   #purchases     the purchase bills from suppliers -- list, one bill's page, delete
//   #purchase-new  read a bill with the AI (or type it in), check it, save
//   #prices        what every thing cost where: the last price, the cheapest supplier, and the lines not linked yet
//   #stock         the mechanics' stock room, a draft: what is on the shelf, in / out / counted
// The suppliers are kept on the settings page (ตั้งค่า), with the words that find them on a bill.
// Data: /api/purchases*, /api/prices, /api/stock* (app/purchasing.py, app/stock.py). Loaded BEFORE app.js.

const bu = {
  opts: null,              // { ready, suppliers, items, machines, branches }
  files: [],               // the photos / PDF pages of the bill being read: { blob, name, type }
  draft: null,             // the bill being checked
  extraction: null,        // what the reader returned (saved with the bill)
  bills: [], prices: null, stock: null, vat: "inc",
  dlgSave: null,
};

const BU_VAT = [["none", "ไม่มี VAT"], ["excluded", "VAT 7% บวกเพิ่มท้ายบิล"], ["included", "ราคารวม VAT แล้ว"]];
const BU_NEW_ITEM = "__new";
const buMoney = (n) => (n === null || n === undefined || n === "" ? "–" : Number(n).toLocaleString("th-TH", { maximumFractionDigits: 2 }));
const buQty = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString("th-TH", { maximumFractionDigits: 3 }));

async function buLoadOptions() {
  bu.opts = await api("/api/purchases/options");
  return bu.opts;
}

function buNeedsV16(id, ready) { $(id).hidden = ready !== false; }

// ================================================================== the bills (#purchases)
async function openPurchases() {
  showView("purchases");
  showErr("buError", "");
  let r;
  try { r = await api("/api/purchases"); } catch (err) { showErr("buError", `โหลดบิลไม่สำเร็จ: ${err.message}`); return; }
  buNeedsV16("buNeedsV16", r.ready);
  bu.bills = r.bills;
  renderPurchases();
}

function renderPurchases() {
  const q = $("buQ").value.trim().toLowerCase();
  const rows = bu.bills.filter((b) => !q || [b.vendor, b.vendor_name, b.bill_no].filter(Boolean).join(" ").toLowerCase().includes(q));
  const total = rows.reduce((s, b) => s + Number(b.total || 0), 0);
  fill($("buCount"), `${rows.length} บิล · รวม ${buMoney(total)} บาท`);
  fill($("buList"), ...(rows.length ? rows.map((b) => el("button", { class: "card bubill", type: "button", onclick: () => openPurchaseBill(b.id) },
    el("span", { class: "bubill-date", text: beDate(b.bill_date) }),
    el("strong", { text: b.vendor || "ไม่ระบุร้าน" }),
    el("span", { class: "muted small", text: [b.bill_no ? `เลขที่ ${b.bill_no}` : null, `${b.lines} รายการ`, b.branch, b.source === "manual" ? "กรอกเอง" : null].filter(Boolean).join(" · ") }),
    el("span", { class: "bubill-total", text: `${buMoney(b.total)} ฿` })))
    : [el("p", { class: "muted empty", text: bu.bills.length ? "ไม่พบบิลที่ตรงกับคำค้น" : "ยังไม่มีบิลซื้อของ — กด “+ บิลซื้อของใหม่”" })]));
}

async function openPurchaseBill(id) {
  let b;
  try { b = await api(`/api/purchases/${id}`); } catch (err) { showErr("buError", err.message); return; }
  const files = b.attachments.map((a, i) => el("a", { class: "btn small", href: a.url, target: "_blank", rel: "noopener", text: a.mime_type === "application/pdf" ? `📄 ไฟล์ ${i + 1}` : `🧾 รูป ${i + 1}` }));
  const vat = (BU_VAT.find(([k]) => k === b.vat_mode) || [, ""])[1];
  buOpenDialog(`${b.vendor || "ไม่ระบุร้าน"} · ${beDate(b.bill_date)}`, [
    el("p", { class: "muted small", text: [b.bill_no ? `เลขที่ ${b.bill_no}` : null, b.branch, vat, b.source === "manual" ? "กรอกเอง ไม่มีบิล" : null].filter(Boolean).join(" · ") }),
    files.length ? el("div", { class: "chips" }, ...files) : null,
    el("div", { class: "bu-scroll" }, el("table", { class: "lines bu-lines-view" },
      el("thead", {}, el("tr", {}, ...["รายการในบิล", "เป็นของ", "จำนวน", "รวม", "ต่อหน่วยจริง"].map((t) => el("th", { text: t })))),
      el("tbody", {}, ...b.lines.map((ln) => el("tr", {},
        el("td", {}, ln.description, ln.machine_name || ln.used_for ? el("div", { class: "muted small", text: [ln.machine_name, ln.used_for].filter(Boolean).join(" · ") }) : null),
        el("td", { class: ln.item_name ? "" : "muted", text: ln.item_name || "ยังไม่ผูก" }),
        el("td", { text: `${buQty(ln.quantity)} ${ln.unit || ""}`.trim() }),
        el("td", { text: buMoney(ln.line_total) }),
        el("td", { text: ln.unit_cost === null ? "–" : `${buMoney(ln.unit_cost)} ฿` })))))),
    el("p", { class: "bu-sum", text: `ยอดบิล ${buMoney(b.total)} บาท` }),
    b.notes ? el("p", { class: "muted small", text: b.notes }) : null,
    el("div", { class: "pl-set-actions" }, el("button", { class: "btn small danger", type: "button", text: "ลบบิลนี้", onclick: async () => {
      if (!confirm("ลบบิลนี้ทั้งใบใช่ไหม? (รายการและราคาในบิลนี้จะหายไปด้วย)")) return;
      try { await api(`/api/purchases/${b.id}`, { method: "DELETE" }); } catch (err) { showErr("buDlgError", err.message); return; }
      $("buDialog").close();
      openPurchases();
    } })),
  ], null);
}

// ================================================================== a new bill (#purchase-new)
async function openPurchaseNew() {
  showView("purchaseNew");
  showErr("bnError", "");
  try { await buLoadOptions(); } catch (err) { showErr("bnError", `โหลดข้อมูลไม่สำเร็จ: ${err.message}`); return; }
  buNeedsV16("bnNeedsV16", bu.opts.ready);
  if (!bu.draft) buShowUpload();
}

function buShowUpload() {
  bu.draft = null;
  bu.extraction = null;
  $("bnUpload").hidden = false;
  $("bnReview").hidden = true;
  buRenderFiles();
}

function buRenderFiles() {
  fill($("bnFiles"), ...bu.files.map((f, i) => el("li", {},
    el("span", { class: "name", text: (f.type === "application/pdf" ? "📄 " : "🧾 ") + f.name }),
    el("span", { class: "muted small", text: `${Math.max(1, Math.round(f.blob.size / 1024))} KB` }),
    el("button", { class: "btn small", type: "button", text: "ลบ", onclick: () => { bu.files.splice(i, 1); buRenderFiles(); } }))));
  $("bnRead").disabled = bu.files.length === 0;
}

async function buAddFiles(list) {
  for (const f of list) {
    const isPdf = f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
    if (!f.type.startsWith("image/") && !isPdf) continue;
    const blob = isPdf ? f : await prepareImage(f);
    bu.files.push({ blob, name: f.name, type: isPdf ? "application/pdf" : blob.type || "image/jpeg" });
  }
  buRenderFiles();
}

async function buRead() {
  const btn = $("bnRead");
  if (btn.disabled) return;
  btn.disabled = true;
  showErr("bnError", "");
  $("bnBusy").hidden = false;
  const fd = new FormData();
  for (const f of bu.files) fd.append("files", f.blob, f.name);
  try {
    const r = await api("/api/purchases/extract", { method: "POST", body: fd });
    if (!bu.opts) await buLoadOptions();
    bu.extraction = r.extraction;
    buStartReview(r.draft);
  } catch (err) {
    showErr("bnError", `อ่านบิลไม่สำเร็จ: ${err.message}`);
  } finally {
    $("bnBusy").hidden = true;
    btn.disabled = bu.files.length === 0;
  }
}

// The same rule as the server (app/shops.py explain): lower-case, no spaces / dots / dashes; "a+b" = both parts;
// the most specific word wins. Used to show the match while a line is typed in.
const buNorm = (t) => String(t || "").toLowerCase().replace(/[\s\-_.]/g, "");
function buMatch(description) {
  const n = buNorm(description);
  if (!n || !bu.opts) return null;
  let best = null, bestLen = 0;
  for (const it of bu.opts.items) {
    for (const w of [...(it.aliases || []), it.name]) {
      const parts = String(w).split("+").map(buNorm).filter(Boolean);
      const len = parts.reduce((s, p) => s + p.length, 0);
      if (parts.length && len > bestLen && parts.every((p) => n.includes(p))) { best = it; bestLen = len; }
    }
  }
  return best;
}

function buBlankLine() {
  return { description: "", quantity: "", unit: "", unit_price: "", line_total: "", item_id: "", new_item: null, learn: false, machine_id: "", used_for: "", low_confidence_fields: [] };
}

// draft = the reader's output (or null to type a bill in by hand)
function buStartReview(draft) {
  const d = draft || {};
  bu.draft = {
    source: draft ? "bill" : "manual",
    vendor_id: d.vendor_id || "", vendor_name: d.vendor_name || "", new_vendor: d.vendor_id ? "" : (d.vendor_name || ""),
    bill_date: d.date || todayISO(), bill_no: d.bill_no || "", branch: "สระบุรี", vat_mode: d.vat_mode || "none",
    subtotal: d.subtotal ?? "", vat_amount: d.vat_amount ?? "", discount: d.discount ?? "", total: d.total ?? "", notes: "", receive_stock: false,
    low: d.low_confidence_fields || [],
    lines: (d.line_items || []).map((ln) => ({ ...buBlankLine(), ...ln, item_id: ln.item_id || "", auto_item: ln.item_id || "",
      quantity: ln.quantity ?? "", unit: ln.unit || "", unit_price: ln.unit_price ?? "", line_total: ln.line_total ?? "" })),
  };
  if (!bu.draft.lines.length) bu.draft.lines.push(buBlankLine());
  $("bnUpload").hidden = true;
  $("bnReview").hidden = false;
  buRenderReview();
}

function buRenderReview() {
  const d = bu.draft;
  const flag = (key, node) => { if (d.low.includes(key)) node.classList.add("flagged"); return node; };
  const sups = bu.opts.suppliers.filter((s) => s.active !== false || s.id === d.vendor_id);
  const vendorSel = el("select", { id: "bnVendor" }, el("option", { value: "", text: "— ไม่ระบุร้าน —" }),
    ...sups.map((s) => el("option", { value: s.id, text: s.name })),
    el("option", { value: "__new", text: "+ ร้านใหม่ (เพิ่มเข้ารายชื่อร้าน)" }));
  vendorSel.value = d.vendor_id || (d.new_vendor ? "__new" : "");
  const newVendor = el("input", { type: "text", maxlength: "60", value: d.new_vendor, placeholder: "ชื่อร้านใหม่", "aria-label": "ชื่อร้านใหม่" });
  newVendor.hidden = vendorSel.value !== "__new";
  vendorSel.addEventListener("change", () => {
    d.vendor_id = vendorSel.value === "__new" ? "" : vendorSel.value;
    newVendor.hidden = vendorSel.value !== "__new";
    if (!newVendor.hidden && !newVendor.value) newVendor.value = d.vendor_name;
    d.new_vendor = newVendor.hidden ? "" : newVendor.value;
  });
  newVendor.addEventListener("input", () => { d.new_vendor = newVendor.value; });

  fill($("bnHead"),
    el("div", { class: "mfields" },
      flag("vendor_name", el("div", { class: "mfield" }, el("label", { for: "bnVendor", text: "ร้าน" }), vendorSel, newVendor,
        d.vendor_name ? el("div", { class: "muted small", text: `ในบิลเขียนว่า: ${d.vendor_name}` }) : null)),
      flag("date", mfield(d, "bill_date", "วันที่ในบิล", { type: "date", caption: true })),
      flag("bill_no", mfield(d, "bill_no", "เลขที่บิล", { maxlength: 60 })),
      mselect(d, "branch", "สาขา", bu.opts.branches.map((b) => [b, b]), { blank: "— ไม่ระบุ —" }),
      flag("vat_mode", mselect(d, "vat_mode", "VAT", BU_VAT, { blank: "— เลือก —", onChange: buRenderSum })),
      flag("total", mfield(d, "total", "ยอดรวมที่จ่ายจริง (บาท)", { type: "number" }))));
  $("mf_total").addEventListener("input", buRenderSum);
  buRenderLines();
  fill($("bnFoot"),
    el("div", { class: "mfields" }, mfield(d, "notes", "หมายเหตุ", { wide: true, maxlength: 500 })),
    el("label", { class: "mgcheck" }, (() => { const c = el("input", { type: "checkbox" }); c.checked = d.receive_stock; c.addEventListener("change", () => { d.receive_stock = c.checked; }); return c; })(),
      "รับของเข้าสต็อกด้วย", el("span", { class: "muted small", text: " (ห้องอะไหล่ยังปรับปรุงอยู่ — ปกติไม่ต้องติ๊ก)" })));
}

function buItemSelect(ln, onChange) {
  const sel = el("select", { "aria-label": "เป็นของรายการไหน" }, el("option", { value: "", text: "— ยังไม่ผูก —" }),
    el("option", { value: BU_NEW_ITEM, text: "+ รายการใหม่" }),
    ...bu.opts.items.filter((i) => i.active !== false || i.id === ln.item_id).map((i) => el("option", { value: i.id, text: i.name })));
  sel.value = ln.new_item ? BU_NEW_ITEM : (ln.item_id || "");
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}

function buRenderLines() {
  const d = bu.draft;
  const machines = bu.opts.machines.filter((m) => m.active !== false);
  const rows = d.lines.map((ln, i) => {
    const inp = (key, attrs = {}) => {
      const x = el("input", { type: attrs.type || "text", ...attrs });
      x.value = ln[key] ?? "";
      if (attrs.type === "number") { x.step = "any"; x.inputMode = "decimal"; }
      if ((ln.low_confidence_fields || []).includes(key)) x.classList.add("flagged");
      x.addEventListener("input", () => { ln[key] = x.value; if (["quantity", "unit_price", "line_total"].includes(key)) buRenderSum(); });
      if (key === "description") x.addEventListener("change", () => {
        if (ln.new_item || (ln.item_id && ln.item_id !== ln.auto_item) || ln.unlinked_by_hand) return;    // chosen by hand: leave it
        const hit = buMatch(x.value);
        ln.item_id = hit ? hit.id : "";
        ln.auto_item = ln.item_id;
        buRenderLines();
      });
      return x;
    };
    const newName = el("input", { type: "text", maxlength: "120", placeholder: "ชื่อรายการใหม่", "aria-label": "ชื่อรายการใหม่" });
    newName.hidden = !ln.new_item;
    if (ln.new_item) newName.value = ln.new_item.name;
    newName.addEventListener("input", () => { if (ln.new_item) ln.new_item.name = newName.value; });
    const itemSel = buItemSelect(ln, (v) => {
      ln.new_item = v === BU_NEW_ITEM ? { name: ln.description } : null;
      ln.item_id = v === BU_NEW_ITEM ? "" : v;
      ln.learn = !!ln.item_id && ln.item_id !== ln.auto_item;          // picked by hand: remember this text for next time
      ln.unlinked_by_hand = !v;
      newName.hidden = !ln.new_item;
      if (ln.new_item) { newName.value = ln.new_item.name; newName.focus(); }
    });
    const machine = el("select", { "aria-label": "ใช้กับเครื่อง" }, el("option", { value: "", text: "—" }), ...machines.map((m) => el("option", { value: m.id, text: m.name })));
    machine.value = ln.machine_id || "";
    machine.addEventListener("change", () => { ln.machine_id = machine.value; });
    return el("tr", {},
      el("td", { class: "n", text: String(i + 1) }),
      el("td", {}, inp("description", { maxlength: "300", "aria-label": "รายการ" })),
      el("td", {}, inp("quantity", { type: "number", "aria-label": "จำนวน" })),
      el("td", {}, inp("unit", { maxlength: "20", "aria-label": "หน่วย" })),
      el("td", {}, inp("unit_price", { type: "number", "aria-label": "ราคาต่อหน่วย" })),
      el("td", {}, inp("line_total", { type: "number", "aria-label": "รวม" })),
      el("td", { class: "bu-item" }, itemSel, newName, ln.auto_item && ln.item_id === ln.auto_item ? el("div", { class: "muted small", text: "จับคู่ให้แล้ว" }) : null),
      el("td", {}, machine),
      el("td", {}, el("button", { class: "btn small", type: "button", title: "ลบบรรทัดนี้", text: "✕", onclick: () => { d.lines.splice(i, 1); if (!d.lines.length) d.lines.push(buBlankLine()); buRenderLines(); } })));
  });
  fill($("bnLines"), el("table", { class: "lines bu-lines" },
    el("thead", {}, el("tr", {}, ...["#", "รายการในบิล", "จำนวน", "หน่วย", "ราคา/หน่วย", "รวม", "เป็นของ (สำหรับเทียบราคา)", "ใช้กับเครื่อง", ""].map((t) => el("th", { text: t })))),
    el("tbody", {}, ...rows)));
  buRenderSum();
}

function buLineAmount(ln) {
  if (ln.line_total !== "" && ln.line_total !== null) return num(ln.line_total);
  return num(ln.quantity) * num(ln.unit_price);
}

function buRenderSum() {
  const d = bu.draft;
  if (!d) return;
  const sum = d.lines.reduce((s, ln) => s + buLineAmount(ln), 0);
  const expected = d.vat_mode === "excluded" ? sum * 1.07 : sum;
  const total = d.total === "" || d.total === null ? null : num(d.total);
  const off = total !== null && Math.abs(total - expected) > 1;
  fill($("bnSum"), el("span", { text: `รวมรายการ ${buMoney(sum)} บาท${d.vat_mode === "excluded" ? ` + VAT = ${buMoney(expected)}` : ""}` }),
    el("span", { class: off ? "error" : "muted", text: total === null ? " · ยังไม่ได้ใส่ยอดบิล (จะใช้ยอดรวมรายการ)" : off ? ` · ยอดบิล ${buMoney(total)} ไม่ตรง — ตรวจรายการหรือส่วนลดอีกที` : ` · ตรงกับยอดบิล ${buMoney(total)}` }));
}

async function buSave(allowDuplicates = false) {
  const d = bu.draft;
  const btn = $("bnSave");
  if (btn.disabled) return;
  showErr("bnSaveError", "");
  const lines = d.lines.filter((ln) => String(ln.description || "").trim() || ln.line_total !== "" || ln.quantity !== "");
  const payload = {
    source: d.source, vendor_id: d.vendor_id || null, new_vendor: d.vendor_id ? null : (d.new_vendor || null), vendor_name: d.vendor_name || d.new_vendor || null,
    bill_date: d.bill_date, bill_no: d.bill_no, branch: d.branch, vat_mode: d.vat_mode || "none",
    subtotal: d.subtotal, vat_amount: d.vat_amount, discount: d.discount, total: d.total, notes: d.notes, receive_stock: d.receive_stock,
    extraction: bu.extraction,
    lines: lines.map((ln) => ({ description: ln.description, quantity: ln.quantity, unit: ln.unit, unit_price: ln.unit_price, line_total: ln.line_total,
      item_id: ln.item_id || null, new_item: ln.new_item, learn: ln.learn, unlinked_by_hand: !!ln.unlinked_by_hand, machine_id: ln.machine_id || null, used_for: ln.used_for || null,
      low_confidence_fields: ln.low_confidence_fields || [] })),
  };
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  if (allowDuplicates) fd.append("allow_duplicates", "true");
  if (d.source === "bill") for (const f of bu.files) fd.append("files", f.blob, f.name);
  btn.disabled = true;
  try {
    await api("/api/purchases", { method: "POST", body: fd });
  } catch (err) {
    btn.disabled = false;
    if (err.status === 409 && confirm("ไฟล์บิลนี้เคยบันทึกไว้แล้ว — บันทึกซ้ำอีกครั้งใช่ไหม?")) return buSave(true);
    showErr("bnSaveError", err.message);
    return;
  }
  btn.disabled = false;
  bu.files = [];
  bu.draft = null;
  goHash("#purchases");
}

// ================================================================== prices (#prices)
async function openPrices() {
  showView("prices");
  showErr("prError", "");
  await loadPrices();
}

async function loadPrices() {
  try {
    [bu.prices] = await Promise.all([api(`/api/prices?q=${encodeURIComponent($("prQ").value.trim())}`), bu.opts ? null : buLoadOptions()]);
  } catch (err) { showErr("prError", `โหลดราคาไม่สำเร็จ: ${err.message}`); return; }
  buNeedsV16("prNeedsV16", bu.prices.ready);
  renderPrices();
}

function buCost(x) { return bu.vat === "ex" ? x.unit_cost_ex_vat : x.unit_cost; }

function renderPrices() {
  const p = bu.prices;
  if (!p) return;
  const per = (x, unit) => `${buMoney(buCost(x))} ฿${unit ? `/${unit}` : ""}`;
  const cards = p.items.filter((it) => it.count > 0).map((it) => el("section", { class: "card prcard" },
    el("div", { class: "prhead" }, el("strong", { text: it.name }), it.category ? el("span", { class: "badge", text: it.category }) : null,
      el("span", { class: "muted small", text: `ซื้อ ${it.count} ครั้ง` })),
    it.last ? el("div", { class: "prline" },
      el("span", {}, "ล่าสุด ", el("strong", { text: per(it.last, it.unit || it.last.unit) }), ` · ${it.last.vendor || "ไม่ระบุร้าน"} · ${beDate(it.last.bill_date)}`),
      it.lowest && (it.lowest.unit_cost !== it.last.unit_cost || it.lowest.vendor !== it.last.vendor) ? el("span", { class: "prlow" }, "ถูกสุด ", el("strong", { text: per(it.lowest, it.unit || it.lowest.unit) }), ` · ${it.lowest.vendor || "ไม่ระบุร้าน"} · ${beDate(it.lowest.bill_date)}`) : null)
      : el("div", { class: "muted small", text: "ยังไม่มีราคา (บรรทัดในบิลไม่มีจำนวนหรือยอด)" }),
    it.by_vendor.length > 1 ? el("div", { class: "chips" }, ...it.by_vendor.map((v, i) => el("span", { class: `badge${i === 0 ? " ok" : ""}`,
      text: `${v.vendor} ${buMoney(bu.vat === "ex" ? v.last_cost_ex_vat : v.last_cost)} ฿ (${beDate(v.last_date)}${v.times > 1 ? ` · ${v.times} ครั้ง` : ""})` }))) : null,
    el("details", {}, el("summary", { class: "small", text: "ประวัติการซื้อ" }),
      ...it.history.map((h) => el("div", { class: "prhist small" }, el("span", { text: beDate(h.bill_date) }), el("span", { text: h.vendor || "–" }),
        el("span", { class: "muted", text: h.description }), el("span", { text: `${buQty(h.quantity)} ${h.unit || ""}` }),
        el("strong", { text: buCost(h) === null ? "–" : `${buMoney(buCost(h))} ฿` }))))));
  const unlinked = p.unlinked.map((ln) => {
    const sel = buItemSelect({ item_id: "" }, async (v) => {
      const body = v === BU_NEW_ITEM ? { new_item: { name: prompt("ชื่อรายการใหม่", ln.description) || "" } } : { item_id: v || null };
      if (body.new_item && !body.new_item.name) { sel.value = ""; return; }
      try { bu.prices = await api(`/api/purchases/lines/${ln.id}`, jsonOpts("PATCH", body)); } catch (err) { showErr("prError", err.message); return; }
      await buLoadOptions();
      await loadPrices();
    });
    return el("div", { class: "prunlinked" }, el("span", { class: "small", text: beDate(ln.bill_date) }), el("span", { text: ln.description }),
      el("span", { class: "muted small", text: `${ln.vendor || "–"} · ${buCost(ln) === null ? "–" : `${buMoney(buCost(ln))} ฿/${ln.unit || "หน่วย"}`}` }), sel);
  });
  fill($("prList"), ...(cards.length ? cards : [el("p", { class: "muted empty", text: "ยังไม่มีราคาของ — บันทึกบิลซื้อของแล้วผูกบรรทัดกับรายการ ราคาจะขึ้นที่นี่" })]));
  fill($("prUnlinked"), ...(unlinked.length ? [el("h3", { class: "section-title", text: `บรรทัดที่ยังไม่ได้ผูกกับรายการ (${unlinked.length})` }),
    el("p", { class: "muted small", text: "เลือกว่าเป็นของรายการไหน — ระบบจะจำคำนี้ไว้ ครั้งหน้าบิลที่เขียนแบบเดียวกันจะผูกให้เอง" }), ...unlinked] : []));
}

// ================================================================== stock (#stock), a draft
async function openStock() {
  showView("stock");
  showErr("skError", "");
  await loadStock();
}

async function loadStock() {
  try { bu.stock = await api("/api/stock"); } catch (err) { showErr("skError", `โหลดสต็อกไม่สำเร็จ: ${err.message}`); return; }
  buNeedsV16("skNeedsV16", bu.stock.ready);
  renderStock();
}

function renderStock() {
  const s = bu.stock;
  const q = $("skQ").value.trim().toLowerCase();
  const items = s.items.filter((i) => (i.active !== false) && (!q || [i.name, i.category, i.spec, i.location, ...(i.aliases || [])].filter(Boolean).join(" ").toLowerCase().includes(q)));
  const low = items.filter((i) => i.low).length;
  fill($("skCount"), `${items.length} รายการ${low ? ` · ควรสั่งเพิ่ม ${low}` : ""}`);
  fill($("skList"), ...(items.length ? [el("div", { class: "bu-scroll" }, el("table", { class: "lines sktable" },
    el("thead", {}, el("tr", {}, ...["รายการ", "หมวด", "คงเหลือ", "ขั้นต่ำ", "ที่เก็บ", "ราคาล่าสุด", ""].map((t) => el("th", { text: t })))),
    el("tbody", {}, ...items.map((i) => el("tr", { class: i.low ? "low" : "" },
      el("td", {}, el("button", { class: "linkbtn", type: "button", text: i.name, onclick: () => openStockItem(i) }), i.spec ? el("div", { class: "muted small", text: i.spec }) : null),
      el("td", { text: i.category || "" }),
      el("td", {}, el("strong", { text: `${buQty(i.on_hand)} ${i.unit || ""}`.trim() }), i.low ? el("span", { class: "badge urgent", text: "ควรสั่งเพิ่ม" }) : null),
      el("td", { text: i.min_qty === null || i.min_qty === undefined ? "" : buQty(i.min_qty) }),
      el("td", { text: i.location || "" }),
      el("td", { class: "small", text: i.last ? `${buMoney(i.last.unit_cost)} ฿ · ${i.last.vendor || ""}` : "–" }),
      el("td", { class: "sk-actions" },
        el("button", { class: "btn small", type: "button", text: "+ รับเข้า", onclick: () => openStockMove(i, "in") }),
        el("button", { class: "btn small", type: "button", text: "− เบิก", onclick: () => openStockMove(i, "out") }),
        el("button", { class: "btn small", type: "button", text: "นับ", onclick: () => openStockMove(i, "count") })))))))]
    : [el("p", { class: "muted empty", text: s.items.length ? "ไม่พบรายการที่ตรงกับคำค้น" : "ยังไม่มีรายการ — กด “+ เพิ่มรายการ” หรือบันทึกบิลซื้อของแล้วสร้างรายการจากบรรทัดในบิล" })]));
}

const SK_FIELDS = [["name", "ชื่อรายการ", { wide: true, maxlength: 120 }], ["category", "หมวด (เช่น ลูกปืน สายพาน ไฟฟ้า)", { maxlength: 40 }], ["unit", "หน่วย", { maxlength: 20 }],
  ["spec", "สเปค", { wide: true, maxlength: 200 }], ["min_qty", "ขั้นต่ำ (ต่ำกว่านี้ขึ้นว่าควรสั่งเพิ่ม)", { type: "number" }], ["location", "ที่เก็บ / ชั้น", { maxlength: 60 }]];

async function openStockItem(item) {
  const d = item ? { ...item, min_qty: item.min_qty ?? "", aliases: (item.aliases || []).join(", ") } : { name: "", category: "", unit: "", spec: "", min_qty: "", location: "", aliases: "" };
  let history = null;
  if (item) {
    try { history = await api(`/api/stock/items/${item.id}/moves`); } catch (_) { history = []; }
  }
  const KIND = { in: "รับเข้า", out: "เบิก", count: "นับสต็อก" };
  buOpenDialog(item ? item.name : "เพิ่มรายการ", [
    el("div", { class: "mfields" }, ...SK_FIELDS.map(([k, l, o]) => mfield(d, k, l, o)),
      mfield(d, "aliases", "คำที่เจอในบิลแล้วนับเป็นรายการนี้ (คั่นด้วย ,)", { wide: true, maxlength: 2000 })),
    history && history.length ? el("details", { open: true }, el("summary", { text: `ความเคลื่อนไหว (${history.length})` }),
      ...history.map((m) => el("div", { class: "prhist small" }, el("span", { text: beDate(m.move_date) }), el("span", { text: KIND[m.kind] || m.kind }),
        el("strong", { text: `${m.qty > 0 ? "+" : ""}${buQty(m.qty)}` }), el("span", { class: "muted", text: [m.machine_name, m.notes].filter(Boolean).join(" · ") }),
        m.purchase_line_id ? el("span", { class: "muted", text: "จากบิล" }) : el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: async () => {
          if (!confirm("ลบรายการเคลื่อนไหวนี้ใช่ไหม?")) return;
          try { await api(`/api/stock/moves/${m.id}`, { method: "DELETE" }); } catch (err) { showErr("buDlgError", err.message); return; }
          $("buDialog").close(); await loadStock();
        } })))) : null,
  ], async () => {
    const body = { ...Object.fromEntries(SK_FIELDS.map(([k]) => [k, d[k]])), aliases: String(d.aliases || "").split(",").map((w) => w.trim()).filter(Boolean) };
    bu.stock = await api(item ? `/api/stock/items/${item.id}` : "/api/stock/items", jsonOpts(item ? "PATCH" : "POST", body));
    bu.opts = null;                                    // the bill page's item list is stale now
    renderStock();
  });
}

function openStockMove(item, kind) {
  const d = { qty: "", move_date: todayISO(), machine_id: "", notes: "" };
  const title = { in: "รับเข้า", out: "เบิกออก", count: "นับสต็อก" }[kind];
  buOpenDialog(`${title} · ${item.name}`, [
    el("p", { class: "muted small", text: `ตอนนี้มี ${buQty(item.on_hand)} ${item.unit || ""}` }),
    el("div", { class: "mfields" },
      mfield(d, "qty", kind === "count" ? "นับได้จริง" : "จำนวน", { type: "number" }),
      mfield(d, "move_date", "วันที่", { type: "date", caption: true }),
      kind === "out" ? mselect(d, "machine_id", "ใช้กับเครื่อง", bu.stock.machines.filter((m) => m.active !== false).map((m) => [m.id, m.name])) : null,
      mfield(d, "notes", "หมายเหตุ", { wide: true, maxlength: 300 })),
  ], async () => {
    bu.stock = await api("/api/stock/moves", jsonOpts("POST", { item_id: item.id, kind, ...d }));
    renderStock();
  });
}

// ================================================================== the dialog
function buOpenDialog(title, nodes, onSave) {
  $("buDlgTitle").textContent = title;
  fill($("buDlgBody"), ...nodes);
  showErr("buDlgError", "");
  bu.dlgSave = onSave;
  $("buDlgSave").hidden = !onSave;
  $("buDlgSave").disabled = false;
  $("buDlgCancel").textContent = onSave ? "ยกเลิก" : "ปิด";
  $("buDialog").showModal();
}

async function buDialogSave() {
  const btn = $("buDlgSave");
  if (!bu.dlgSave || btn.disabled) return;
  btn.disabled = true;
  try {
    await bu.dlgSave();
  } catch (err) {
    showErr("buDlgError", err.message);
    btn.disabled = false;
    return;
  }
  $("buDialog").close();
}

// ================================================================== init
function initBuy() {
  $("buQ").addEventListener("input", renderPurchases);
  $("buNew").addEventListener("click", () => { bu.draft = null; goHash("#purchase-new"); });
  const drop = $("bnDrop"), input = $("bnInput");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { buAddFiles([...input.files]); input.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); buAddFiles([...e.dataTransfer.files]); });
  $("bnRead").addEventListener("click", buRead);
  $("bnManual").addEventListener("click", async () => {
    if (!bu.opts) { try { await buLoadOptions(); } catch (err) { showErr("bnError", err.message); return; } }     // pressed before the page finished loading
    bu.extraction = null;
    buStartReview(null);
  });
  $("bnAddLine").addEventListener("click", () => { bu.draft.lines.push(buBlankLine()); buRenderLines(); });
  $("bnBack").addEventListener("click", () => { if (confirm("ทิ้งที่กรอกไว้ แล้วกลับไปเลือกไฟล์ใหม่ใช่ไหม?")) buShowUpload(); });
  $("bnSave").addEventListener("click", () => buSave(false));
  let prTimer = null;
  $("prQ").addEventListener("input", () => { clearTimeout(prTimer); prTimer = setTimeout(loadPrices, 300); });
  $("prVat").addEventListener("change", () => { bu.vat = $("prVat").value; renderPrices(); });
  $("skQ").addEventListener("input", renderStock);
  $("skAdd").addEventListener("click", () => openStockItem(null));
  $("buDlgCancel").addEventListener("click", () => $("buDialog").close());
  $("buDlgSave").addEventListener("click", buDialogSave);
}
