"use strict";

// Buying for the factory (เครื่องจักร group):
//   #purchases     the purchase bills from suppliers -- list with filters, one bill's page, delete
//   #purchase-new  pick bill files (one bill per file, or pages of one bill) -> they are READ IN THE BACKGROUND (a queue:
//                  go to another page and come back) -> check each one against the original -> save
//   #prices        what every thing cost where: the last price, the cheapest supplier, and the lines not identified yet
//   #stock         the mechanics' stock room, a draft: what is on the shelf, in / out / counted
// The suppliers are kept on the settings page (ตั้งค่า), with the words that find them on a bill.
// Data: /api/purchases*, /api/prices, /api/stock* (app/purchasing.py, app/stock.py). Loaded BEFORE app.js.
// The queue lives in this page's memory only: refreshing the browser loses bills that were read but not saved.

const bu = {
  opts: null,              // { ready, suppliers, items, machines, branches }
  pick: [],                // files chosen but not started: { blob, name, type }
  mode: "each",            // "each": one bill per file · "one": all the files are pages of one bill
  jobs: [],                // the queue: { id, files, status: queued|reading|ready|error, error, t0, extraction, server, review, manual }
  cur: null,               // the job being checked
  nextId: 1, running: 0, timer: null, notice: "",
  bills: [], prices: null, stock: null, vat: "inc",
  dlgSave: null,
};

const BU_VAT = [["none", "ไม่มี VAT"], ["excluded", "VAT 7% บวกเพิ่มท้ายบิล"], ["included", "ราคารวม VAT แล้ว"]];
const BU_NEW_ITEM = "__new";
const BU_PARALLEL = 3;
const buMoney = (n) => (n === null || n === undefined || n === "" ? "–" : Number(n).toLocaleString("th-TH", { maximumFractionDigits: 2 }));
const buQty = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString("th-TH", { maximumFractionDigits: 3 }));
const buClock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

async function buLoadOptions() {
  bu.opts = await api("/api/purchases/options");
  return bu.opts;
}

const buNeedsV16 = (id, ready) => { $(id).hidden = ready !== false; };

// keep the chosen value of a filter while its options are rebuilt
function buFillSelect(sel, blank, values) {
  const keep = sel.value;
  sel.replaceChildren(el("option", { value: "", text: blank }), ...values.map((v) => el("option", { value: v, text: v })));
  sel.value = values.includes(keep) ? keep : "";
}

// ================================================================== the bills (#purchases)
async function openPurchases() {
  showView("purchases");
  showErr("buError", "");
  let r;
  try { r = await api("/api/purchases?limit=500"); } catch (err) { showErr("buError", `โหลดบิลไม่สำเร็จ: ${err.message}`); return; }
  buNeedsV16("buNeedsV16", r.ready);
  bu.bills = r.bills;
  buFillSelect($("buVendor"), "ทุกร้าน", [...new Set(bu.bills.map((b) => b.vendor).filter(Boolean))].sort());
  buFillSelect($("buBranch"), "ทุกสาขา", [...new Set(bu.bills.map((b) => b.branch).filter(Boolean))].sort());
  renderPurchases();
}

function buPeriodStart(kind) {
  const t = todayISO();
  if (kind === "month") return `${t.slice(0, 7)}-01`;
  if (kind === "year") return `${t.slice(0, 4)}-01-01`;
  return kind ? addDaysISO(t, -Number(kind)) : "";
}

function renderPurchases() {
  const q = $("buQ").value.trim().toLowerCase(), vendor = $("buVendor").value, branch = $("buBranch").value, from = buPeriodStart($("buPeriod").value);
  const rows = bu.bills.filter((b) => (!q || [b.vendor, b.vendor_name, b.bill_no].filter(Boolean).join(" ").toLowerCase().includes(q))
    && (!vendor || b.vendor === vendor) && (!branch || b.branch === branch) && (!from || b.bill_date >= from));
  const total = rows.reduce((s, b) => s + Number(b.total || 0), 0);
  fill($("buCount"), `${rows.length} บิล · รวม ${buMoney(total)} บาท`);
  fill($("buList"), ...(rows.length ? rows.map((b) => el("button", { class: "card bubill", type: "button", onclick: () => openPurchaseBill(b.id) },
    el("span", { class: "bubill-date", text: beDate(b.bill_date) }),
    el("strong", { text: b.vendor || "ไม่ระบุร้าน" }),
    el("span", { class: "muted small", text: [b.bill_no ? `เลขที่ ${b.bill_no}` : null, `${b.lines} รายการ`, b.branch, b.source === "manual" ? "กรอกเอง" : null].filter(Boolean).join(" · ") }),
    el("span", { class: "bubill-total", text: `${buMoney(b.total)} ฿` })))
    : [el("p", { class: "muted empty", text: bu.bills.length ? "ไม่พบบิลที่ตรงกับตัวกรอง" : "ยังไม่มีบิลซื้อของ — กด “+ บิลซื้อของใหม่”" })]));
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
        el("td", { class: ln.item_name ? "" : "muted", text: ln.item_name || "ยังไม่ระบุ" }),
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

// ================================================================== a new bill (#purchase-new): pick -> queue -> check
async function openPurchaseNew() {
  showView("purchaseNew");
  showErr("bnError", "");
  try { await buLoadOptions(); } catch (err) { showErr("bnError", `โหลดข้อมูลไม่สำเร็จ: ${err.message}`); return; }
  buNeedsV16("bnNeedsV16", bu.opts.ready);
  if (bu.cur) buShowReview(); else buShowHome();
}

function buShowHome() {
  bu.cur = null;
  $("bnUpload").hidden = false;
  $("bnReview").hidden = true;
  $("bnNotice").hidden = !bu.notice;
  $("bnNotice").textContent = bu.notice;                 // stays until the next bill is started or opened (the page can be routed twice)
  buRenderPick();
  buRenderQueue();
}

function buShowReview() {
  $("bnUpload").hidden = true;
  $("bnReview").hidden = false;
  buRenderOrigin();
  buRenderReview();
  window.scrollTo(0, 0);
}

function buRenderPick() {
  fill($("bnFiles"), ...bu.pick.map((f, i) => el("li", {},
    el("span", { class: "name", text: (f.type === "application/pdf" ? "📄 " : "🧾 ") + f.name }),
    el("span", { class: "muted small", text: `${Math.max(1, Math.round(f.blob.size / 1024))} KB` }),
    el("button", { class: "btn small", type: "button", text: "ลบ", onclick: () => { bu.pick.splice(i, 1); buRenderPick(); } }))));
  $("bnModes").hidden = bu.pick.length < 2;
  const bills = bu.mode === "one" || bu.pick.length < 2 ? 1 : bu.pick.length;
  $("bnRead").disabled = bu.pick.length === 0;
  $("bnRead").textContent = bu.pick.length > 1 ? `อ่านบิล (${bills} ใบ)` : "อ่านบิล";
}

async function buAddFiles(list) {
  for (const f of list) {
    const isPdf = f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
    if (!f.type.startsWith("image/") && !isPdf) continue;
    const blob = isPdf ? f : await prepareImage(f);
    bu.pick.push({ blob, name: f.name, type: isPdf ? "application/pdf" : blob.type || "image/jpeg" });
  }
  buRenderPick();
}

// ---- the background queue: up to BU_PARALLEL bills are read at once; it keeps running while another page is open
function buStartJobs() {
  if (!bu.pick.length) return;
  bu.notice = "";
  $("bnNotice").hidden = true;
  const groups = bu.mode === "one" ? [bu.pick] : bu.pick.map((f) => [f]);
  for (const files of groups) {
    for (const f of files) f.url = f.url || URL.createObjectURL(f.blob);
    bu.jobs.push({ id: bu.nextId++, files, status: "queued", error: "", t0: null, extraction: null, server: null, review: null, manual: false });
  }
  bu.pick = [];
  buRenderPick();
  buRenderQueue();
  buPump();
}

function buPump() {
  while (bu.running < BU_PARALLEL) {
    const job = bu.jobs.find((j) => j.status === "queued");
    if (!job) break;
    buRunJob(job);
  }
}

async function buRunJob(job) {
  job.status = "reading";
  job.t0 = Date.now();
  job.error = "";
  bu.running++;
  buTick();
  buRenderQueue();
  buBadge();
  const fd = new FormData();
  for (const f of job.files) fd.append("files", f.blob, f.name);
  try {
    if (!bu.opts) await buLoadOptions();
    const r = await api("/api/purchases/extract", { method: "POST", body: fd });
    job.extraction = r.extraction;
    job.server = r.draft;
    job.status = "ready";
  } catch (err) {
    job.status = "error";
    job.error = err.message;
  } finally {
    bu.running--;
    buRenderQueue();
    buBadge();
    buPump();
  }
}

// a ticking clock on the jobs being read, only while there are some
function buTick() {
  if (bu.timer) return;
  bu.timer = setInterval(() => {
    if (!bu.jobs.some((j) => j.status === "reading")) { clearInterval(bu.timer); bu.timer = null; return; }
    for (const s of document.querySelectorAll(".bujob-clock")) {
      const job = bu.jobs.find((j) => String(j.id) === s.dataset.id);
      if (job && job.t0) s.textContent = buClock(Date.now() - job.t0);
    }
  }, 1000);
}

// the number beside "บิลซื้อของ" in the menu: bills read or being read that are not saved yet
function buBadge() {
  if (typeof navBadge === "function") navBadge("navPurchases", bu.jobs.length, false);
}

function buJobTitle(job) {
  return job.manual ? "กรอกเอง (ไม่มีบิล)" : job.files.map((f) => f.name).join(", ");
}

function buRenderQueue() {
  $("bnQueueBox").hidden = bu.jobs.length === 0;
  fill($("bnQueue"), ...bu.jobs.map((job) => {
    const d = job.server || {};
    const status = job.status === "reading" ? el("span", { class: "muted small" }, el("span", { class: "spin" }), "กำลังอ่านบิล… ", el("span", { class: "bujob-clock", "data-id": String(job.id), text: buClock(Date.now() - job.t0) }))
      : job.status === "queued" ? el("span", { class: "muted small", text: "รอคิว…" })
      : job.status === "error" ? el("span", { class: "error small", text: `อ่านไม่สำเร็จ: ${job.error}` })
      : el("span", { class: "small", text: job.manual ? "กรอกค้างไว้ ยังไม่ได้บันทึก"
        : `${d.vendor_name || "ไม่ทราบร้าน"} · ${d.date ? beDate(d.date) : "ไม่ทราบวันที่"} · ${(d.line_items || []).length} รายการ · ${buMoney(d.total)} ฿` });
    const actions = [];
    if (job.status === "ready") actions.push(el("button", { class: "btn small primary", type: "button", text: "ตรวจและบันทึก", onclick: () => buOpenJob(job) }));
    if (job.status === "error") actions.push(el("button", { class: "btn small", type: "button", text: "ลองใหม่", onclick: () => { job.status = "queued"; buRenderQueue(); buPump(); } }));
    if (job.status !== "reading") actions.push(el("button", { class: "btn small danger", type: "button", text: "เอาออก", onclick: () => buRemoveJob(job, true) }));
    return el("div", { class: `card bujob ${job.status}` }, el("div", {}, el("div", { class: "bujob-name", text: buJobTitle(job) }), status), el("div", { class: "bujob-actions" }, ...actions));
  }));
}

function buRemoveJob(job, ask) {
  if (ask && !confirm(`เอาบิลนี้ออกจากรายการใช่ไหม? (ยังไม่ได้บันทึก)\n${buJobTitle(job)}`)) return;
  for (const f of job.files) if (f.url) URL.revokeObjectURL(f.url);
  bu.jobs = bu.jobs.filter((j) => j !== job);
  if (bu.cur === job) bu.cur = null;
  buRenderQueue();
  buBadge();
}

function buOpenJob(job) {
  bu.cur = job;
  bu.notice = "";
  if (!job.review) job.review = buMakeReview(job.server);
  buShowReview();
}

// draft = what the server read (null to type a bill in by hand)
function buMakeReview(draft) {
  const d = draft || {};
  const review = {
    source: draft ? "bill" : "manual",
    vendor_id: d.vendor_id || "", vendor_name: d.vendor_name || "", new_vendor: d.vendor_id ? "" : (d.vendor_name || ""),
    bill_date: d.date || todayISO(), bill_no: d.bill_no || "", branch: "สระบุรี", vat_mode: d.vat_mode || "none",
    subtotal: d.subtotal ?? "", vat_amount: d.vat_amount ?? "", discount: d.discount ?? "", total: d.total ?? "", notes: "", receive_stock: false,
    low: d.low_confidence_fields || [],
    lines: (d.line_items || []).map((ln) => {
      const found = ln.item_id ? { id: ln.item_id } : buMatch(ln.description);          // the item list may have changed while the bill waited
      return { ...buBlankLine(), ...ln, item_id: found ? found.id : "", auto_item: found ? found.id : "",
        quantity: ln.quantity ?? "", unit: ln.unit || "", unit_price: ln.unit_price ?? "", line_total: ln.line_total ?? "" };
    }),
  };
  if (!review.lines.length) review.lines.push(buBlankLine());
  return review;
}

// ---- the original bill, next to what was read (images can be turned for reading; PDFs open in a tab)
function buRenderOrigin() {
  const job = bu.cur;
  const files = job ? job.files : [];
  $("bnOrigin").hidden = files.length === 0;
  fill($("bnThumbs"), ...files.map((f) => {
    if (f.type === "application/pdf") return el("div", { class: "bu-thumb" }, el("a", { class: "btn small", href: f.url, target: "_blank", rel: "noopener", text: `📄 ${f.name}` }));
    let turns = f.turns || 0;
    const img = el("img", { src: f.url, alt: f.name, title: "กดเพื่อดูภาพใหญ่" });
    const apply = () => {
      img.style.transform = `rotate(${turns * 90}deg)`;
      const odd = turns % 2 === 1;
      img.style.maxWidth = odd ? "260px" : "100%";
      img.style.maxHeight = odd ? "220px" : "100%";
    };
    apply();
    img.addEventListener("click", () => window.open(f.url, "_blank", "noopener"));
    return el("div", { class: "bu-thumb" }, img,
      el("button", { class: "btn small rot", type: "button", title: "หมุนรูป (เฉพาะที่ดูตรงนี้)", text: "↻", onclick: () => { turns = (turns + 1) % 4; f.turns = turns; apply(); } }));
  }));
}

function buBlankLine() {
  return { description: "", quantity: "", unit: "", unit_price: "", line_total: "", item_id: "", new_item: null, learn: false, unlinked_by_hand: false, used_for: "", low_confidence_fields: [] };
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

function buRenderReview() {
  const d = bu.cur.review;
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
  const sel = el("select", { "aria-label": "เป็นของรายการไหน" }, el("option", { value: "", text: "— ยังไม่ระบุ —" }),
    el("option", { value: BU_NEW_ITEM, text: "+ รายการใหม่" }),
    ...bu.opts.items.filter((i) => i.active !== false || i.id === ln.item_id).map((i) => el("option", { value: i.id, text: i.name })));
  sel.value = ln.new_item ? BU_NEW_ITEM : (ln.item_id || "");
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}

function buRenderLines() {
  const d = bu.cur.review;
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
    return el("tr", {},
      el("td", { class: "n", text: String(i + 1) }),
      el("td", {}, inp("description", { maxlength: "300", "aria-label": "รายการ" })),
      el("td", {}, inp("quantity", { type: "number", "aria-label": "จำนวน" })),
      el("td", {}, inp("unit", { maxlength: "20", "aria-label": "หน่วย" })),
      el("td", {}, inp("unit_price", { type: "number", "aria-label": "ราคาต่อหน่วย" })),
      el("td", {}, inp("line_total", { type: "number", "aria-label": "รวม" })),
      el("td", { class: "bu-item" }, itemSel, newName, ln.auto_item && ln.item_id === ln.auto_item ? el("div", { class: "muted small", text: "รู้แล้วว่าเป็นของอะไร" }) : null),
      el("td", {}, el("button", { class: "btn small", type: "button", title: "ลบบรรทัดนี้", text: "✕", onclick: () => { d.lines.splice(i, 1); if (!d.lines.length) d.lines.push(buBlankLine()); buRenderLines(); } })));
  });
  fill($("bnLines"), el("table", { class: "lines bu-lines" },
    el("thead", {}, el("tr", {}, ...["#", "รายการในบิล", "จำนวน", "หน่วย", "ราคา/หน่วย", "รวม", "เป็นของ (สำหรับเทียบราคา)", ""].map((t) => el("th", { text: t })))),
    el("tbody", {}, ...rows)));
  buRenderSum();
}

function buLineAmount(ln) {
  if (ln.line_total !== "" && ln.line_total !== null) return num(ln.line_total);
  return num(ln.quantity) * num(ln.unit_price);
}

function buRenderSum() {
  const d = bu.cur && bu.cur.review;
  if (!d) return;
  const sum = d.lines.reduce((s, ln) => s + buLineAmount(ln), 0);
  const expected = d.vat_mode === "excluded" ? sum * 1.07 : sum;
  const total = d.total === "" || d.total === null ? null : num(d.total);
  const off = total !== null && Math.abs(total - expected) > 1;
  fill($("bnSum"), el("span", { text: `รวมรายการ ${buMoney(sum)} บาท${d.vat_mode === "excluded" ? ` + VAT = ${buMoney(expected)}` : ""}` }),
    el("span", { class: off ? "error" : "muted", text: total === null ? " · ยังไม่ได้ใส่ยอดบิล (จะใช้ยอดรวมรายการ)" : off ? ` · ยอดบิล ${buMoney(total)} ไม่ตรง — ตรวจรายการหรือส่วนลดอีกที` : ` · ตรงกับยอดบิล ${buMoney(total)}` }));
}

async function buSave(allowDuplicates = false) {
  const job = bu.cur;
  const d = job.review;
  const btn = $("bnSave");
  if (btn.disabled) return;
  showErr("bnSaveError", "");
  const lines = d.lines.filter((ln) => String(ln.description || "").trim() || ln.line_total !== "" || ln.quantity !== "");
  const payload = {
    source: d.source, vendor_id: d.vendor_id || null, new_vendor: d.vendor_id ? null : (d.new_vendor || null), vendor_name: d.vendor_name || d.new_vendor || null,
    bill_date: d.bill_date, bill_no: d.bill_no, branch: d.branch, vat_mode: d.vat_mode || "none",
    subtotal: d.subtotal, vat_amount: d.vat_amount, discount: d.discount, total: d.total, notes: d.notes, receive_stock: d.receive_stock,
    extraction: job.extraction,
    lines: lines.map((ln) => ({ description: ln.description, quantity: ln.quantity, unit: ln.unit, unit_price: ln.unit_price, line_total: ln.line_total,
      item_id: ln.item_id || null, new_item: ln.new_item, learn: ln.learn, unlinked_by_hand: !!ln.unlinked_by_hand, used_for: ln.used_for || null,
      low_confidence_fields: ln.low_confidence_fields || [] })),
  };
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  if (allowDuplicates) fd.append("allow_duplicates", "true");
  if (d.source === "bill") for (const f of job.files) fd.append("files", f.blob, f.name);
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
  bu.opts = null;                                        // a new supplier / item may have been made
  buRemoveJob(job, false);
  bu.notice = `บันทึกบิลแล้ว: ${d.new_vendor || d.vendor_name || "ไม่ระบุร้าน"} · ${beDate(d.bill_date)}`;
  if (bu.jobs.length) goHash("#purchase-new"); else goHash("#purchases");
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
  const bought = bu.prices.items.filter((it) => it.count > 0);
  buFillSelect($("prCat"), "ทุกหมวด", [...new Set(bought.map((it) => it.category).filter(Boolean))].sort());
  const vendors = [...new Set([...bought.flatMap((it) => it.by_vendor.map((v) => v.vendor)), ...bu.prices.unlinked.map((u) => u.vendor)].filter(Boolean))].sort();
  buFillSelect($("prVendor"), "ทุกร้าน", vendors);
  buFillSelect($("prUv"), "ทุกร้าน", [...new Set(bu.prices.unlinked.map((u) => u.vendor).filter(Boolean))].sort());
  renderPrices();
}

const buCost = (x) => (bu.vat === "ex" ? x.unit_cost_ex_vat : x.unit_cost);

function renderPrices() {
  const p = bu.prices;
  if (!p) return;
  const per = (x, unit) => `${buMoney(buCost(x))} ฿${unit ? `/${unit}` : ""}`;
  const cat = $("prCat").value, vendor = $("prVendor").value;
  const shown = p.items.filter((it) => it.count > 0 && (!cat || it.category === cat) && (!vendor || it.by_vendor.some((v) => v.vendor === vendor)));
  const cards = shown.map((it) => el("section", { class: "card prcard" },
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
  fill($("prList"), ...(cards.length ? cards : [el("p", { class: "muted empty", text: p.items.some((it) => it.count > 0) ? "ไม่พบของที่ตรงกับตัวกรอง" : "ยังไม่มีราคาของ — บันทึกบิลซื้อของแล้วเลือกว่าแต่ละบรรทัดเป็นของอะไร ราคาจะขึ้นที่นี่" })]));
  renderUnlinked();
}

function renderUnlinked() {
  const p = bu.prices;
  const q = $("prUq").value.trim().toLowerCase(), v = $("prUv").value;
  const rows = p.unlinked.filter((ln) => (!q || `${ln.description} ${ln.vendor || ""}`.toLowerCase().includes(q)) && (!v || ln.vendor === v));
  $("prUnlinkedBox").hidden = p.unlinked.length === 0;
  fill($("prUnlinkedTitle"), `บรรทัดที่ยังไม่ได้ระบุว่าเป็นของอะไร (${rows.length}${rows.length !== p.unlinked.length ? ` จาก ${p.unlinked.length}` : ""})`);
  fill($("prUnlinked"), ...rows.map((ln) => {
    const sel = buItemSelect({ item_id: "" }, async (val) => {
      const body = val === BU_NEW_ITEM ? { new_item: { name: prompt("ชื่อรายการใหม่", ln.description) || "" } } : { item_id: val || null };
      if (body.new_item && !body.new_item.name) { sel.value = ""; return; }
      try { await api(`/api/purchases/lines/${ln.id}`, jsonOpts("PATCH", body)); } catch (err) { showErr("prError", err.message); return; }
      await buLoadOptions();
      await loadPrices();
    });
    return el("div", { class: "prunlinked" }, el("span", { class: "small", text: beDate(ln.bill_date) }), el("span", { text: ln.description }),
      el("span", { class: "muted small", text: `${ln.vendor || "–"} · ${buCost(ln) === null ? "–" : `${buMoney(buCost(ln))} ฿/${ln.unit || "หน่วย"}`}` }), sel);
  }));
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
  const active = bu.stock.items.filter((i) => i.active !== false);
  buFillSelect($("skCat"), "ทุกหมวด", [...new Set(active.map((i) => i.category).filter(Boolean))].sort());
  buFillSelect($("skLoc"), "ทุกที่เก็บ", [...new Set(active.map((i) => i.location).filter(Boolean))].sort());
  renderStock();
}

function renderStock() {
  const s = bu.stock;
  const q = $("skQ").value.trim().toLowerCase(), cat = $("skCat").value, loc = $("skLoc").value, onlyLow = $("skLow").checked;
  const items = s.items.filter((i) => (i.active !== false) && (!q || [i.name, i.category, i.spec, i.location, ...(i.aliases || [])].filter(Boolean).join(" ").toLowerCase().includes(q))
    && (!cat || i.category === cat) && (!loc || i.location === loc) && (!onlyLow || i.low));
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
    : [el("p", { class: "muted empty", text: s.items.length ? "ไม่พบรายการที่ตรงกับตัวกรอง" : "ยังไม่มีรายการ — กด “+ เพิ่มรายการ” หรือบันทึกบิลซื้อของแล้วสร้างรายการจากบรรทัดในบิล" })]));
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
    await loadStock();
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
  for (const id of ["buQ", "buVendor", "buBranch", "buPeriod"]) $(id).addEventListener(id === "buQ" ? "input" : "change", renderPurchases);
  $("buNew").addEventListener("click", () => goHash("#purchase-new"));
  const drop = $("bnDrop"), input = $("bnInput");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { buAddFiles([...input.files]); input.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); buAddFiles([...e.dataTransfer.files]); });
  for (const r of document.querySelectorAll("input[name=bnMode]")) r.addEventListener("change", () => { bu.mode = document.querySelector("input[name=bnMode]:checked").value; buRenderPick(); });
  $("bnRead").addEventListener("click", buStartJobs);
  $("bnManual").addEventListener("click", async () => {
    if (!bu.opts) { try { await buLoadOptions(); } catch (err) { showErr("bnError", err.message); return; } }     // pressed before the page finished loading
    const job = { id: bu.nextId++, files: [], status: "ready", error: "", t0: null, extraction: null, server: null, review: buMakeReview(null), manual: true };
    bu.jobs.push(job);
    buBadge();
    buOpenJob(job);
  });
  $("bnAddLine").addEventListener("click", () => { bu.cur.review.lines.push(buBlankLine()); buRenderLines(); });
  $("bnBack").addEventListener("click", () => { bu.cur = null; buShowHome(); });
  $("bnDiscard").addEventListener("click", () => { if (bu.cur) { const j = bu.cur; if (confirm("ทิ้งบิลนี้ทั้งใบ (ยังไม่ได้บันทึก) ใช่ไหม?")) { buRemoveJob(j, false); buShowHome(); } } });
  $("bnSave").addEventListener("click", () => buSave(false));
  let prTimer = null;
  $("prQ").addEventListener("input", () => { clearTimeout(prTimer); prTimer = setTimeout(loadPrices, 300); });
  for (const id of ["prCat", "prVendor"]) $(id).addEventListener("change", renderPrices);
  $("prVat").addEventListener("change", () => { bu.vat = $("prVat").value; renderPrices(); });
  $("prUq").addEventListener("input", renderUnlinked);
  $("prUv").addEventListener("change", renderUnlinked);
  for (const id of ["skQ", "skCat", "skLoc", "skLow"]) $(id).addEventListener(id === "skQ" ? "input" : "change", renderStock);
  $("skAdd").addEventListener("click", () => openStockItem(null));
  $("buDlgCancel").addEventListener("click", () => $("buDialog").close());
  $("buDlgSave").addEventListener("click", buDialogSave);
  // bills read but not saved live in this page only: warn before a refresh or closing the tab throws them away
  window.addEventListener("beforeunload", (e) => { if (bu.jobs.some((j) => j.status !== "error")) { e.preventDefault(); e.returnValue = ""; } });
}
