"use strict";

// Buying for the factory (เครื่องจักร group):
//   #purchases     the purchase bills from suppliers -- list with filters, one bill's page, delete
//   #purchase-new  pick bill files (one bill per file, or pages of one bill) -> they are READ IN THE BACKGROUND (a queue:
//                  go to another page and come back) -> check each one against the original -> save
//   #prices        what every thing cost where: the last price, the cheapest supplier, and the lines not identified yet
//   #stock         the mechanics' stock room: what is on the shelf; IN is recorded by the owner (bills go in first), OUT is typed once a week from the mechanic's paper
// The suppliers are kept on the settings page (ตั้งค่า), with the words that find them on a bill.
// Data: /api/purchases*, /api/prices, /api/stock* (app/purchasing.py, app/stock.py). Loaded BEFORE app.js.
// The queue lives in this page's memory only: refreshing the browser loses bills that were read but not saved.

const bu = {
  opts: null,              // { ready, suppliers, items, machines, branches }
  pick: [],                // files chosen but not started: { blob, name, type }
  mode: "each",            // "each": one bill per file · "one": all the files are pages of one bill
  purpose: "stock",        // what the bills picked next are: stock / repair (one machine's repair) / reference (a model's parts list: no money)
  jobs: [],                // the queue: { id, files, status: queued|reading|ready|error, error, t0, extraction, server, review, manual }
  cur: null,               // the job being checked
  nextId: 1, running: 0, timer: null, notice: "",
  bills: [], prices: null, stock: null, vat: "inc", skSort: { key: "category", dir: 1 }, prSort: { key: "name", dir: 1 },
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
  const refs = rows.filter((b) => b.purpose === "reference").length;
  const total = rows.reduce((s, b) => s + (b.purpose === "reference" ? 0 : Number(b.total || 0)), 0);       // a reference parts list is not money spent
  fill($("buCount"), `${rows.length} บิล · รวม ${buMoney(total)} บาท${refs ? ` (ไม่นับบิลอ้างอิง ${refs} ใบ)` : ""}`);
  fill($("buList"), ...(rows.length ? rows.map((b) => el("button", { class: "card bubill", type: "button", onclick: () => openPurchaseBill(b.id) },
    el("span", { class: "bubill-date", text: beDate(b.bill_date) }),
    el("strong", { text: b.vendor || "ไม่ระบุร้าน" }),
    el("span", { class: "muted small", text: [buPurposeText(b), b.bill_no ? `เลขที่ ${b.bill_no}` : null, `${b.lines} รายการ`, b.branch, b.source === "manual" ? "กรอกเอง" : null].filter(Boolean).join(" · ") }),
    el("span", { class: `bubill-total${b.purpose === "reference" ? " muted" : ""}`, text: b.purpose === "reference" ? `(${buMoney(b.total)} ฿ ไม่นับ)` : `${buMoney(b.total)} ฿` })))
    : [el("p", { class: "muted empty", text: bu.bills.length ? "ไม่พบบิลที่ตรงกับตัวกรอง" : "ยังไม่มีบิลซื้อของ — กด “+ บิลซื้อของใหม่”" })]));
}

async function openPurchaseBill(id) {
  let b;
  try { b = await api(`/api/purchases/${id}`); } catch (err) { showErr("buError", err.message); return; }
  const files = b.attachments.map((a, i) => el("button", { class: "btn small", type: "button", onclick: () => buView(a.url, a.mime_type, `${b.vendor || "บิล"} · ${a.mime_type === "application/pdf" ? "ไฟล์" : "รูป"} ${i + 1}`), text: a.mime_type === "application/pdf" ? `📄 ไฟล์ ${i + 1}` : `🧾 รูป ${i + 1}` }));
  const vat = (BU_VAT.find(([k]) => k === b.vat_mode) || [, ""])[1];
  buOpenDialog(`${b.vendor || "ไม่ระบุร้าน"} · ${beDate(b.bill_date)}`, [
    el("p", { class: "muted small", text: [buPurposeText(b), b.bill_no ? `เลขที่ ${b.bill_no}` : null, b.branch, vat, b.source === "manual" ? "กรอกเอง ไม่มีบิล" : null].filter(Boolean).join(" · ") }),
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
    el("div", { class: "pl-set-actions" }, el("button", { class: "btn small primary", type: "button", text: "แก้ไขบิลนี้", onclick: () => { $("buDialog").close(); buEditBill(b); } }),
      el("button", { class: "btn small danger", type: "button", text: "ลบบิลนี้", onclick: async () => {
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
  if (bu.presetMachine && !bu.cur) buSetPurpose("repair");              // sent here by a machine's "บันทึกบิลซ่อม": the bills picked next are that machine's repair
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
    bu.jobs.push({ id: bu.nextId++, files, status: "queued", error: "", t0: null, extraction: null, server: null, review: null, manual: false, purpose: bu.purpose });
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
  const tag = job.purpose === "repair" ? "[ซ่อมเครื่อง] " : job.purpose === "reference" ? "[อ้างอิง ไม่นับเงิน] " : "";
  return tag + (job.manual ? "กรอกเอง (ไม่มีบิล)" : job.files.map((f) => f.name).join(", "));
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

// "แก้ไขบิลนี้": the saved bill opens on the check page (its photos beside it); saving writes the changes back to the same bill
async function buEditBill(b) {
  if (!bu.opts) { try { await buLoadOptions(); } catch (err) { showErr("buError", err.message); return; } }
  const lines = b.lines.map((ln) => ({ ...buBlankLine(), description: ln.description, quantity: ln.quantity ?? "", unit: ln.unit || "", unit_price: ln.unit_price ?? "", line_total: ln.line_total ?? "",
    item_id: ln.item_id || "", auto_item: ln.item_id || "", used_for: ln.used_for || "", unlinked_by_hand: !ln.item_id, low_confidence_fields: ln.low_confidence_fields || [] }));
  const review = { source: b.source, purpose: b.purpose || "stock", machine_id: b.machine_id || "", machine_model: b.machine_model || "",
    vendor_id: b.vendor_id || "", vendor_name: b.vendor_name || "", new_vendor: b.vendor_id ? "" : (b.vendor_name || ""), bill_date: String(b.bill_date).slice(0, 10), bill_no: b.bill_no || "",
    branch: b.branch || "", vat_mode: b.vat_mode || "none", subtotal: b.subtotal ?? "", vat_amount: b.vat_amount ?? "", discount: b.discount ?? "", total: b.total ?? "", notes: b.notes || "",
    receive_stock: !!b.received_stock, low: [], lines: lines.length ? lines : [buBlankLine()] };
  const files = b.attachments.map((a, i) => ({ url: a.url, type: a.mime_type, name: `${a.mime_type === "application/pdf" ? "ไฟล์" : "รูป"} ${i + 1}`, remote: true }));
  const job = { id: -1, files, status: "ready", error: "", t0: null, extraction: null, server: null, review, manual: false, purpose: review.purpose, editId: b.id };
  bu.cur = job;
  bu.notice = "";
  goHash("#purchase-new");                                // that page shows the bill being checked when there is one
}

function buOpenJob(job) {
  bu.cur = job;
  bu.notice = "";
  if (!job.review) job.review = buMakeReview(job.server, job.purpose);
  buShowReview();
}

// draft = what the server read (null to type a bill in by hand)
function buMakeReview(draft, purpose = "stock") {
  const d = draft || {};
  const models = (bu.opts && bu.opts.machine_models) || [];
  const review = {
    source: draft ? "bill" : "manual",
    purpose, machine_id: purpose === "repair" ? (bu.presetMachine || "") : "", machine_model: models.length === 1 ? models[0] : "",
    vendor_id: d.vendor_id || "", vendor_name: d.vendor_name || "", new_vendor: d.vendor_id ? "" : (d.vendor_name || ""),
    bill_date: d.date || todayISO(), bill_no: d.bill_no || "", branch: "สระบุรี", vat_mode: d.vat_mode || "none",
    subtotal: d.subtotal ?? "", vat_amount: d.vat_amount ?? "", discount: d.discount ?? "", total: d.total ?? "", notes: "", receive_stock: true,
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
  fill($("bnThumbs"), ...files.map((f) => buThumb(f)));
}

// One bill file as a small preview that can be turned (↻) and opened large in the pop-up below. Used by the purchase bills, the repair-bill
// review and the repair record: `f` is {url, type, name} (a turn made here is kept in f.turns).
function buThumb(f) {
  if (f.type === "application/pdf") return el("div", { class: "bu-thumb" }, el("button", { class: "btn small", type: "button", text: `📄 ${f.name || "ไฟล์"}`, onclick: () => buView(f.url, f.type, f.name) }));
  let turns = f.turns || 0;
  const img = el("img", { src: f.url, alt: f.name || "บิล", title: "กดเพื่อดูภาพใหญ่", loading: "lazy" });
  const apply = () => {
    img.style.transform = `rotate(${turns * 90}deg)`;
    const odd = turns % 2 === 1;
    img.style.maxWidth = odd ? "var(--thumb-h)" : "100%";
    img.style.maxHeight = odd ? "var(--thumb-w)" : "100%";
  };
  apply();
  img.addEventListener("click", () => buView(f.url, f.type, f.name, () => f.turns || 0, (t) => { f.turns = t; turns = t; apply(); }));
  return el("div", { class: "bu-thumb" }, img,
    el("button", { class: "btn small rot", type: "button", title: "หมุนรูป (เฉพาะที่ดูตรงนี้)", text: "↻", onclick: () => { turns = (turns + 1) % 4; f.turns = turns; apply(); } }));
}

// ---- the original bill in a pop-up: click the picture / press Esc to close, so checking is one click in and one out
function buView(url, type, name, getTurns, setTurns) {
  let turns = getTurns ? getTurns() : 0;
  const body = $("buViewerBody");
  body.classList.remove("zoomed");
  $("buViewerName").textContent = name || "";
  const isPdf = type === "application/pdf";
  $("buViewerRot").hidden = isPdf;
  $("buViewerZoom").hidden = isPdf;
  if (isPdf) {
    fill(body, el("iframe", { src: url, title: name || "บิล" }));
  } else {
    const img = el("img", { src: url, alt: name || "บิล" });
    const apply = () => { img.style.transform = `rotate(${turns * 90}deg)`; };
    apply();
    img.addEventListener("click", () => { body.classList.toggle("zoomed"); });
    $("buViewerRot").onclick = () => { turns = (turns + 1) % 4; apply(); if (setTurns) setTurns(turns); };
    $("buViewerZoom").onclick = () => { body.classList.toggle("zoomed"); };
    fill(body, img);
  }
  $("buViewer").showModal();
}

function buBlankLine() {
  return { description: "", quantity: "", unit: "", unit_price: "", line_total: "", item_id: "", new_item: null, learn: false, unlinked_by_hand: false, used_for: "", low_confidence_fields: [] };
}

// The same rule as the server (app/shops.py explain): lower-case, no spaces / dots / dashes; "a+b" = both parts;
// the most specific word wins. Used to show the match while a line is typed in.
const buNorm = (t) => String(t || "").toLowerCase().replace(/[\s\-_.]/g, "");
// one word of an item found in a bill line: spaces, dashes, dots and quotes between its characters do not matter (SC-N2 = SCN2), but a code is a WHOLE code ("B-1" is not in
// "B-105", "UCF207" is not in "UCF207-20"). The same rule as the server's word_regex.
function buWordRegex(word) {
  const chars = [...String(word || "")].filter((c) => !/[\s\-_."']/.test(c));
  if (!chars.length) return null;
  const ascii = (c) => /^[A-Za-z0-9]$/.test(c);
  const esc = (c) => c.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&");
  const pre = ascii(chars[0]) ? "(?<![A-Za-z0-9])" : "";
  const post = ascii(chars[chars.length - 1]) ? "(?![A-Za-z0-9]|[-/]\\d+(?![A-Za-z]))" : "";
  return new RegExp(pre + chars.map(esc).join("[\\s\\-_.\"']*") + post, "i");
}

function buMatch(description) {
  const text = String(description || "");
  if (!text.trim() || !bu.opts) return null;
  let best = null, bestLen = 0;
  for (const it of bu.opts.items) {
    for (const w of [...(it.aliases || []), it.name]) {
      const parts = String(w).split("+").map((p) => p.trim()).filter(Boolean);
      const regexes = parts.map(buWordRegex);
      if (!parts.length || regexes.some((r) => !r)) continue;
      const len = parts.reduce((s, p) => s + buNorm(p).length, 0);
      if (len > bestLen && regexes.every((r) => r.test(text))) { best = it; bestLen = len; }
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

  fill($("bnHead"), buPurposeBlock(d),
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
    d.purpose && d.purpose !== "stock" ? el("p", { class: "muted small", text: d.purpose === "repair" ? "บิลซ่อมเครื่อง: อะไหล่ใช้กับเครื่องนี้เลย ไม่รับเข้าสต็อก" : "บิลอ้างอิง: ไม่รับเข้าสต็อก และไม่นับเงินที่ไหนเลย" })
      : el("label", { class: "mgcheck" }, (() => { const c = el("input", { type: "checkbox" }); c.checked = d.receive_stock; c.addEventListener("change", () => { d.receive_stock = c.checked; }); return c; })(),
      "รับของเข้าสต็อกด้วย", el("span", { class: "muted small", text: " (ของที่ซื้อเข้าสต็อกก่อนเสมอ — เอาติ๊กออกเฉพาะบิลที่ไม่ใช่ของเข้าห้องอะไหล่)" })));
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
  if (d.purpose === "repair" && !d.machine_id) return showErr("bnSaveError", "บิลซ่อมเครื่อง: เลือกเครื่องที่ซ่อมก่อน");
  if (d.purpose === "reference" && !String(d.machine_model || "").trim()) return showErr("bnSaveError", "บิลอ้างอิง: เลือกรุ่นเครื่องก่อน");
  const lines = d.lines.filter((ln) => String(ln.description || "").trim() || ln.line_total !== "" || ln.quantity !== "");
  const editing = !!job.editId;
  const payload = {
    source: d.source, vendor_id: d.vendor_id || null, new_vendor: d.vendor_id ? null : (d.new_vendor || null), vendor_name: d.vendor_name || d.new_vendor || null,
    bill_date: d.bill_date, bill_no: d.bill_no, branch: d.branch, vat_mode: d.vat_mode || "none",
    subtotal: d.subtotal, vat_amount: d.vat_amount, discount: d.discount, total: d.total, notes: d.notes, receive_stock: d.receive_stock,
    purpose: d.purpose || "stock", machine_id: d.machine_id || null, machine_model: d.machine_model || null,
    extraction: job.extraction,
    lines: lines.map((ln) => ({ description: ln.description, quantity: ln.quantity, unit: ln.unit, unit_price: ln.unit_price, line_total: ln.line_total,
      item_id: ln.item_id || null, new_item: ln.new_item, learn: ln.learn, unlinked_by_hand: !!ln.unlinked_by_hand, used_for: ln.used_for || null,
      low_confidence_fields: ln.low_confidence_fields || [] })),
  };
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  if (allowDuplicates) fd.append("allow_duplicates", "true");
  if (d.source === "bill" && !editing) for (const f of job.files) fd.append("files", f.blob, f.name);
  btn.disabled = true;
  try {
    if (editing) await api(`/api/purchases/${job.editId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    else await api("/api/purchases", { method: "POST", body: fd });
  } catch (err) {
    btn.disabled = false;
    if (err.status === 409 && confirm(`${err.message} — บันทึกซ้ำอีกครั้งใช่ไหม?`)) return buSave(true);
    showErr("bnSaveError", err.message);
    return;
  }
  btn.disabled = false;
  bu.opts = null;                                        // a new supplier / item may have been made
  if (editing) { bu.cur = null; goHash("#purchases"); return; }
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

const PR_COLS = [["name", "ของ"], ["category", "หมวด"], ["last", "ราคาล่าสุด"], ["vendor", "ร้านล่าสุด"], ["date", "วันที่"], ["lowest", "ถูกสุดที่เคยซื้อ"], ["count", "ซื้อ (ครั้ง)"]];
const prSortValue = (it, key) => (key === "last" ? (it.last ? Number(buCost(it.last)) : null) : key === "lowest" ? (it.lowest ? Number(buCost(it.lowest)) : null)
  : key === "vendor" ? (it.last ? it.last.vendor || "" : "") : key === "date" ? (it.last ? it.last.bill_date || "" : "") : key === "count" ? it.count : (it[key] || ""));

// one row per thing, the name opens its own page (shops, brands, every purchase); the cheaper-than-last price is the only thing marked
function renderPrices() {
  const p = bu.prices;
  if (!p) return;
  const cat = $("prCat").value, vendor = $("prVendor").value;
  const shown = p.items.filter((it) => it.count > 0 && (!cat || it.category === cat) && (!vendor || it.by_vendor.some((v) => v.vendor === vendor)));
  const { key, dir } = bu.prSort;
  shown.sort((a, b) => {
    const x = prSortValue(a, key), y = prSortValue(b, key);
    if ((x === null || x === "") !== (y === null || y === "")) return x === null || x === "" ? 1 : -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), "th", { numeric: true })) * dir || String(a.name).localeCompare(String(b.name), "th", { numeric: true });
  });
  const money = (x, unit) => `${buMoney(buCost(x))} ฿${unit ? `/${unit}` : ""}`;
  const head = el("tr", {}, ...PR_COLS.map(([k, label]) => el("th", { class: ["last", "lowest", "count"].includes(k) ? "r" : "", "aria-sort": key === k ? (dir > 0 ? "ascending" : "descending") : "none" },
    el("button", { class: "skth", type: "button", title: "เรียงตามคอลัมน์นี้", text: label + (key === k ? (dir > 0 ? " ▲" : " ▼") : ""),
      onclick: () => { bu.prSort = { key: k, dir: bu.prSort.key === k ? -bu.prSort.dir : 1 }; renderPrices(); } }))));
  const rows = shown.map((it) => {
    const cheaper = it.last && it.lowest && Number(buCost(it.lowest)) < Number(buCost(it.last));
    return el("tr", {},
      el("td", {}, el("button", { class: "linkbtn", type: "button", text: it.name, onclick: () => goHash(`#price=${it.id}`) }),
        it.by_brand && it.by_brand.length > 1 ? el("span", { class: "badge shop", text: `${it.by_brand.length} ยี่ห้อ` }) : null),
      el("td", { class: "small", text: it.category || "" }),
      el("td", { class: "r nowrap" }, it.last ? el("strong", { text: money(it.last, it.unit || it.last.unit) }) : "–"),
      el("td", { text: it.last ? it.last.vendor || "ไม่ระบุร้าน" : "" }),
      el("td", { class: "small nowrap", text: it.last ? beDate(it.last.bill_date) : "" }),
      el("td", { class: "r nowrap" }, cheaper ? el("span", { class: "prlow" }, el("strong", { text: money(it.lowest, it.unit || it.lowest.unit) }), el("div", { class: "small", text: `${it.lowest.vendor || "ไม่ระบุร้าน"} · ${beDate(it.lowest.bill_date)}` }))
        : el("span", { class: "muted small", text: it.last ? "ล่าสุดคือถูกสุด" : "" })),
      el("td", { class: "r", text: String(it.count) }));
  });
  fill($("prList"), ...(rows.length ? [el("div", { class: "bu-scroll" }, el("table", { class: "lines prtable" }, el("thead", {}, head), el("tbody", {}, ...rows)))]
    : [el("p", { class: "muted empty", text: p.items.some((it) => it.count > 0) ? "ไม่พบของที่ตรงกับตัวกรอง" : "ยังไม่มีราคาของ — บันทึกบิลซื้อของแล้วเลือกว่าแต่ละบรรทัดเป็นของอะไร ราคาจะขึ้นที่นี่" })]));
  renderUnlinked();
}

// ---- one item's page: the price in each shop, brand by brand (only when more than one brand was bought), and every purchase
async function openPriceItem(id) {
  showView("priceItem");
  showErr("piError", "");
  if (!bu.prices) {
    try { [bu.prices] = await Promise.all([api("/api/prices"), bu.opts ? null : buLoadOptions()]); } catch (err) { showErr("piError", `โหลดราคาไม่สำเร็จ: ${err.message}`); return; }
  }
  renderPriceItem(id);
}

function renderPriceItem(id) {
  const it = (bu.prices.items || []).find((x) => x.id === id);
  if (!it) { fill($("piTitle"), "ไม่พบรายการนี้"); fill($("piSub"), ""); fill($("piBody"), ""); return; }
  const unit = it.unit ? `/${it.unit}` : "";
  const money = (v) => (v === null || v === undefined ? "–" : `${buMoney(v)} ฿${unit}`);
  const vat = (inc, ex) => (bu.vat === "ex" ? ex : inc);
  fill($("piTitle"), it.name);
  fill($("piSub"), [it.category, it.remark, `ซื้อ ${it.count} ครั้ง`].filter(Boolean).join(" · "));
  const tbl = (cols, rows) => el("div", { class: "bu-scroll" }, el("table", { class: "lines prtable" }, el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", { text: c })))), el("tbody", {}, ...rows)));
  const sections = [];
  sections.push(el("div", { class: "card pisum" },
    it.last ? el("div", {}, el("div", { class: "muted small", text: "ล่าสุด" }), el("strong", { class: "pibig", text: money(buCost(it.last)) }), el("div", { class: "small", text: `${it.last.vendor || "ไม่ระบุร้าน"} · ${beDate(it.last.bill_date)}` })) : null,
    it.lowest ? el("div", {}, el("div", { class: "muted small", text: "ถูกสุดที่เคยซื้อ" }), el("strong", { class: "pibig prlow", text: money(buCost(it.lowest)) }), el("div", { class: "small", text: `${it.lowest.vendor || "ไม่ระบุร้าน"} · ${beDate(it.lowest.bill_date)}` })) : null));
  sections.push(el("section", { class: "card" }, el("h3", { class: "section-title", text: "ราคาแต่ละร้าน" }),
    tbl(["ร้าน", "ราคาล่าสุด", "วันที่", "ซื้อ (ครั้ง)"], it.by_vendor.map((v, i) => el("tr", { class: i === 0 && it.by_vendor.length > 1 ? "cheapest" : "" },
      el("td", {}, el("strong", { text: v.vendor || "ไม่ระบุร้าน" }), i === 0 && it.by_vendor.length > 1 ? el("span", { class: "badge ok", text: "ถูกสุดตอนนี้" }) : null),
      el("td", { class: "nowrap", text: money(vat(v.last_cost, v.last_cost_ex_vat)) }), el("td", { class: "small nowrap", text: beDate(v.last_date) }), el("td", { text: String(v.times) }))))));
  if (it.by_brand && it.by_brand.length > 1) {
    sections.push(el("section", { class: "card" }, el("h3", { class: "section-title", text: "ราคาแต่ละยี่ห้อ" }),
      tbl(["ยี่ห้อ", "ราคาล่าสุด", "ร้าน", "วันที่", "ซื้อ (ครั้ง)"], it.by_brand.map((b, i) => el("tr", { class: i === 0 ? "cheapest" : "" },
        el("td", {}, el("strong", { text: b.brand }), i === 0 ? el("span", { class: "badge ok", text: "ถูกสุดตอนนี้" }) : null),
        el("td", { class: "nowrap", text: money(vat(b.last_cost, b.last_cost_ex_vat)) }), el("td", { text: b.last_vendor || "ไม่ระบุร้าน" }),
        el("td", { class: "small nowrap", text: beDate(b.last_date) }), el("td", { text: String(b.times) }))))));
  }
  sections.push(el("section", { class: "card" }, el("h3", { class: "section-title", text: `ประวัติการซื้อ (${it.history.length}${it.count > it.history.length ? ` จาก ${it.count}` : ""})` }),
    tbl(["วันที่", "ร้าน", "ยี่ห้อ", "ชื่อในบิล", "จำนวน", "ราคาต่อหน่วย"], it.history.map((h) => el("tr", {},
      el("td", { class: "small nowrap", text: beDate(h.bill_date) }), el("td", { text: h.vendor || "–" }), el("td", { text: h.brand || "" }),
      el("td", { class: "muted small", text: h.description }), el("td", { class: "nowrap", text: `${buQty(h.quantity)} ${h.unit || ""}` }),
      el("td", { class: "r nowrap" }, el("strong", { text: buCost(h) === null ? "–" : `${buMoney(buCost(h))} ฿` })))))));
  fill($("piBody"), ...sections);
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

// ================================================================== stock (#stock)
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

// The filters work one level at a time, each narrowing the next: หมวด › ชนิด › แรงดัน (V, read from the spec) › ยี่ห้อ. Every list shows only what is left
// after the choices before it, with how many items each has -- e.g. ไฟฟ้า › แมกเนติก › 220V › Fuji.
const SK_NONE = "__none__";
const SK_VOLT_RE = /(\d+(?:\.\d+)?)\s*V\s*(DC|AC)?/i;
const skVolt = (i) => { const m = SK_VOLT_RE.exec(i.spec || ""); return m ? `${m[1]}V${m[2] ? m[2].toUpperCase() : ""}` : ""; };
const SK_LEVELS = [["skCat", (i) => i.category || "", "ทุกหมวด"], ["skKind", (i) => i.kind || "", "ทุกชนิด"], ["skVolt", skVolt, "ทุกแรงดัน"], ["skBrand", (i) => i.brand || "", "ทุกยี่ห้อ"]];
const SK_COLS = [["code", "รหัส"], ["category", "หมวด"], ["kind", "ชนิด"], ["brand", "ยี่ห้อ"], ["model", "รุ่น"], ["spec", "สเปค"], ["on_hand", "คงเหลือ"], ["min_qty", "ขั้นต่ำ"], ["location", "ที่เก็บ"], ["last", "ราคาล่าสุด"]];
const skSortValue = (i, key) => (key === "last" ? (i.last ? Number(i.last.unit_cost) : null) : key === "on_hand" || key === "min_qty" ? (i[key] === null || i[key] === undefined ? null : Number(i[key])) : (i[key] || "").toString());
const skOptionText = (v, n) => `${v === SK_NONE ? "(ไม่ระบุ)" : v} (${n})`;

function skSorted(items) {
  const { key, dir } = bu.skSort;
  return [...items].sort((a, b) => {
    const x = skSortValue(a, key), y = skSortValue(b, key);
    if ((x === null || x === "") !== (y === null || y === "")) return x === null || x === "" ? 1 : -1;          // blanks last, whichever way it is sorted
    const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "th", { numeric: true });
    return (c || String(a.name).localeCompare(String(b.name), "th", { numeric: true })) * dir;
  });
}

function renderStock() {
  const s = bu.stock;
  const all = s.items.filter((i) => i.active !== false);
  let pool = all;
  const picked = [];
  for (const [id, get, blank] of SK_LEVELS) {
    const sel = $(id), cur = sel.value, counts = new Map();
    for (const i of pool) { const v = get(i) || SK_NONE; counts.set(v, (counts.get(v) || 0) + 1); }
    const values = [...counts.keys()].sort((a, b) => (a === SK_NONE ? 1 : b === SK_NONE ? -1 : a.localeCompare(b, "th", { numeric: true })));
    sel.replaceChildren(el("option", { value: "", text: blank }), ...values.map((v) => el("option", { value: v, text: skOptionText(v, counts.get(v)) })));
    sel.value = counts.has(cur) ? cur : "";
    if (sel.value) { pool = pool.filter((i) => (get(i) || SK_NONE) === sel.value); picked.push(sel.value === SK_NONE ? "(ไม่ระบุ)" : sel.value); }
  }
  const locSel = $("skLoc"), curLoc = locSel.value, locs = [...new Set(pool.map((i) => i.location).filter(Boolean))].sort();
  locSel.replaceChildren(el("option", { value: "", text: "ทุกที่เก็บ" }), ...locs.map((l) => el("option", { value: l, text: l })));
  locSel.value = locs.includes(curLoc) ? curLoc : "";
  const q = $("skQ").value.trim().toLowerCase(), loc = locSel.value, onlyLow = $("skLow").checked;
  const items = skSorted(pool.filter((i) => (!q || [i.name, i.category, i.kind, i.brand, i.model, i.spec, i.location, ...(i.aliases || [])].filter(Boolean).join(" ").toLowerCase().includes(q))
    && (!loc || i.location === loc) && (!onlyLow || i.low)));
  const low = items.filter((i) => i.low).length;
  $("skClear").hidden = !(picked.length || q || loc || onlyLow);
  fill($("skCount"), `${picked.length ? `${picked.join(" › ")} — ` : ""}${items.length} รายการ${low ? ` · ควรสั่งเพิ่ม ${low}` : ""}`);
  const arrow = (k) => (bu.skSort.key === k ? (bu.skSort.dir > 0 ? " ▲" : " ▼") : "");
  const head = el("tr", {}, ...SK_COLS.map(([k, label]) => el("th", { class: ["on_hand", "min_qty", "last"].includes(k) ? "r" : "", "aria-sort": bu.skSort.key === k ? (bu.skSort.dir > 0 ? "ascending" : "descending") : "none" },
    el("button", { class: "skth", type: "button", title: "เรียงตามคอลัมน์นี้", text: label + arrow(k), onclick: () => { bu.skSort = { key: k, dir: bu.skSort.key === k ? -bu.skSort.dir : 1 }; renderStock(); } })))
    , el("th", {}));
  fill($("skList"), ...(items.length ? [el("div", { class: "bu-scroll" }, el("table", { class: "lines sktable" }, el("thead", {}, head),
    el("tbody", {}, ...items.map((i) => el("tr", { class: i.low ? "low" : "", title: i.name },
      el("td", { class: "small nowrap", text: i.code || "" }),
      el("td", { text: i.category || "" }),
      el("td", {}, el("button", { class: "linkbtn", type: "button", text: i.kind || i.name, onclick: () => openStockItem(i) }), i.kind ? null : el("div", { class: "muted small", text: "ยังไม่ได้จัดชนิด" })),
      el("td", { text: i.brand || "" }),
      el("td", {}, el("button", { class: "linkbtn", type: "button", text: i.model || "", onclick: () => openStockItem(i) }), i.remark ? el("div", { class: "muted small sk-remark", text: i.remark }) : null),
      el("td", { text: i.spec || "" }),
      el("td", { class: "r nowrap" }, el("strong", { text: `${buQty(i.on_hand)} ${i.unit || ""}`.trim() }), i.low ? el("div", { class: "badge urgent", text: "ควรสั่งเพิ่ม" }) : null),
      el("td", { class: "r", text: i.min_qty === null || i.min_qty === undefined ? "" : buQty(i.min_qty) }),
      el("td", { text: i.location || "" }),
      el("td", { class: "small r nowrap", text: i.last ? `${buMoney(i.last.unit_cost)} ฿` : "–", title: i.last ? i.last.vendor || "" : "" }),
      el("td", { class: "sk-actions" },
        el("button", { class: "btn small", type: "button", text: "+ รับเข้า", onclick: () => openStockMove(i, "in") }),
        el("button", { class: "btn small", type: "button", text: "− เบิก", onclick: () => openStockMove(i, "out") }),
        el("button", { class: "btn small", type: "button", text: "นับ", onclick: () => openStockMove(i, "count") })))))))]
    : [el("p", { class: "muted empty", text: s.items.length ? "ไม่พบรายการที่ตรงกับตัวกรอง" : "ยังไม่มีรายการ — กด “+ เพิ่มรายการ” หรือบันทึกบิลซื้อของแล้วสร้างรายการจากบรรทัดในบิล" })]));
}

function skClearFilters() {
  for (const id of ["skCat", "skKind", "skBrand", "skVolt", "skLoc"]) $(id).value = "";
  $("skQ").value = "";
  $("skLow").checked = false;
  renderStock();
}

// one standard name per item: ชนิด + ยี่ห้อ + รุ่น + สเปค (the server builds it too; an item without a ชนิด keeps the name it was typed with)
const SK_FIELDS = [["code", "รหัส (เว้นว่าง = ตั้งให้เอง)", { maxlength: 12 }], ["kind", "ชนิด (เช่น แมกเนติก โอเวอร์โหลด เบรกเกอร์)", { maxlength: 60 }], ["brand", "ยี่ห้อ", { maxlength: 40 }], ["model", "รุ่น / เบอร์", { maxlength: 60 }],
  ["spec", "สเปค (เช่น 220V 40A)", { maxlength: 200 }], ["name", "ชื่อรายการ (ตั้งให้เองจาก ชนิด + ยี่ห้อ + รุ่น + สเปค · สายพานและลูกปืน: ชนิด + รุ่น ไม่ใส่ยี่ห้อ)", { wide: true, maxlength: 120 }],
  ["remark", "คำอธิบายสั้น (โชว์ใต้ชื่อ: รูมิล/รูหุน ชื่ออื่นของของชิ้นนี้)", { wide: true, maxlength: 200 }],
  ["category", "หมวด (เช่น ไฟฟ้า โซ่ เชื่อม-ตัด)", { maxlength: 40 }], ["unit", "หน่วย", { maxlength: 20 }],
  ["min_qty", "ขั้นต่ำ (ต่ำกว่านี้ขึ้นว่าควรสั่งเพิ่ม)", { type: "number" }], ["location", "ที่เก็บ / ชั้น", { maxlength: 60 }]];
const SK_BRAND_FREE = ["สายพาน", "ลูกปืน", "ตลับลูกปืน"];            // parts that interchange by size: named by kind + size only; the brand is a field (and a price column)
const skStandardName = (d) => (SK_BRAND_FREE.some((k) => String(d.kind || "").trim().startsWith(k)) ? [d.kind, d.model] : [d.kind, d.brand, d.model, d.spec])
  .map((x) => String(x || "").trim()).filter(Boolean).join(" ");
const SK_SHORTHAND = "รูมิล = รูวัดเป็นมิลลิเมตร · รูหุน = รูวัดเป็นหุน (1 หุน = 1/8 นิ้ว = 3.2 มม. · 1 นิ้ว = 8 หุน)";

// หมวด and หน่วย are picked from a list (the ones already used + the usual ones; "+ เพิ่มใหม่…" types a new one). The หมวด is guessed as the parts are typed:
// first from the items of the same ชนิด already in the list (แมกเนติก -> ไฟฟ้า), then from words in the name (SK_CATEGORY_WORDS). No AI: instant and predictable.
const SK_UNITS = ["ตัว", "ชิ้น", "อัน", "ชุด", "คู่", "ลูก", "ม้วน", "เส้น", "เมตร", "ขวด", "หลอด", "กระป๋อง", "กล่อง", "แพ็ก", "ถุง", "ลิตร", "กก."];
const SK_CATEGORY_WORDS = [
  ["ท่อ-ประปา", /ท่อ|ข้อต่อ|วาล์ว|ก๊อก|เทปพันเกลียว|กาวประสานท่อ|พีวีซี|pvc/i],
  ["ไฟฟ้า", /แมกเนติก|โอเวอร์โหลด|เบรกเกอร์|เบรคเกอร์|รีเลย์|ไทเมอร์|สายไฟ|ปลั๊ก|สวิตช์|สวิทช์|หลอดไฟ|ไพลอต|ฟิวส์|เทปพันสายไฟ|เคเบิล|มอเตอร์|คอนแทค|ลูกลอย|ซ็อกเก็ต|อินเวอร์เตอร์|ตู้ไฟ|มิเตอร์|breaker|contactor|relay/i],
  ["เชื่อม-ตัด", /ลวดเชื่อม|ใบตัด|ใบเจียร|ใบเลื่อย/],
  ["โซ่", /โซ่|สเตอร์/],
  ["ลูกปืน", /ลูกปืน|bearing/i],
  ["สายพาน", /สายพาน|belt/i],
  ["น้ำมัน-จาระบี", /น้ำมัน|จาระบี|จารบี|grease/i],
  ["น้ำยา-กาว", /น้ำยา|กาว|ซิลิโคน|สเปรย์|silicone/i],
  ["นอต-สกรู", /น็อต|นอต|สกรู|แหวนรอง|โบลท์|bolt/i],
];

function skMostCommon(values) {
  const c = new Map();
  for (const v of values) if (v) c.set(v, (c.get(v) || 0) + 1);
  return [...c.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || "";
}

// {category, unit, why}: what an item with these parts most likely is
function skGuess(d) {
  const kind = String(d.kind || "").trim().toLowerCase();
  const same = kind ? (bu.stock?.items || []).filter((i) => String(i.kind || "").trim().toLowerCase() === kind) : [];
  let category = skMostCommon(same.map((i) => i.category)), why = category ? `ชนิด “${d.kind}” อยู่หมวดนี้` : "";
  if (!category) {
    const text = [d.kind, d.name, d.brand, d.model, d.spec].filter(Boolean).join(" ");
    const hit = SK_CATEGORY_WORDS.find(([, re]) => re.test(text));
    if (hit) { category = hit[0]; why = "จากคำในชื่อ"; }
  }
  return { category, unit: skMostCommon(same.map((i) => i.unit)), why };
}

const skCategories = () => [...new Set([...(bu.stock?.items || []).map((i) => i.category).filter(Boolean), ...SK_CATEGORY_WORDS.map(([c]) => c)])].sort((a, b) => a.localeCompare(b, "th"));
const skUnits = () => [...new Set([...SK_UNITS, ...(bu.stock?.items || []).map((i) => i.unit).filter(Boolean)])];

// a pick-list with "+ เพิ่มใหม่…" (a text box for a new value); box._set(value) chooses one from code, box._touched says the person chose
function skChoice(d, key, label, values) {
  const id = `mf_${key}`, opts = [...values];
  const sel = el("select", { id }, el("option", { value: "", text: "— เลือก —" }), ...opts.map((v) => el("option", { value: v, text: v })), el("option", { value: "__new", text: "+ เพิ่มใหม่…" }));
  const other = el("input", { type: "text", maxlength: key === "unit" ? "20" : "40", placeholder: "พิมพ์ชื่อใหม่", "aria-label": `${label} (ใหม่)`, hidden: true });
  const hint = el("div", { class: "datecap" });
  const box = el("div", { class: "mfield" }, el("label", { for: id, text: label }), sel, other, hint);
  box._set = (v) => {
    if (v && !opts.includes(v)) { opts.push(v); sel.insertBefore(el("option", { value: v, text: v }), sel.lastChild); }
    sel.value = v || "";
    other.hidden = true;
    d[key] = v || "";
  };
  box._hint = (text) => fill(hint, text || "");
  box._touched = false;
  sel.addEventListener("change", () => {
    box._touched = true;
    box._hint("");
    if (sel.value === "__new") { other.hidden = false; other.value = ""; d[key] = ""; other.focus(); }
    else { other.hidden = true; d[key] = sel.value; }
  });
  other.addEventListener("input", () => { box._touched = true; d[key] = other.value.trim(); });
  box._set(d[key]);
  return box;
}

// − / + beside every number box of the stock forms (steps of 1, never below 0)
function skSteppers(root) {
  for (const input of root.querySelectorAll("input[type=number]")) {
    if (input.closest(".stepper")) continue;
    const bump = (n) => {
      const v = Math.max(0, (parseFloat(String(input.value).replace(/,/g, "")) || 0) + n);
      input.value = String(Math.round(v * 1000) / 1000);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    };
    const wrap = el("div", { class: "stepper" });
    input.replaceWith(wrap);
    wrap.append(el("button", { type: "button", class: "btn small stepbtn", "aria-label": "ลดลง 1", text: "−", onclick: () => bump(-1) }), input,
      el("button", { type: "button", class: "btn small stepbtn", "aria-label": "เพิ่มขึ้น 1", text: "+", onclick: () => bump(1) }));
  }
}

async function openStockItem(item) {
  const d = item ? { ...item, code: item.code || "", remark: item.remark || "", track_out: item.track_out !== false, kind: item.kind || "", brand: item.brand || "", model: item.model || "", min_qty: item.min_qty ?? "", aliases: (item.aliases || []).join(", ") }
    : { code: "", remark: "", track_out: true, kind: "", brand: "", model: "", name: "", category: "", unit: "", spec: "", min_qty: "", location: "", aliases: "" };
  let history = null;
  if (item) {
    try { history = await api(`/api/stock/items/${item.id}/moves`); } catch (_) { history = []; }
  }
  const KIND = { in: "รับเข้า", out: "เบิก", count: "นับสต็อก" };
  const catBox = skChoice(d, "category", "หมวด", skCategories()), unitBox = skChoice(d, "unit", "หน่วย", skUnits());
  buOpenDialog(item ? item.name : "เพิ่มรายการ", [
    el("div", { class: "mfields" }, ...SK_FIELDS.map(([k, l, o]) => (k === "category" ? catBox : k === "unit" ? unitBox : mfield(d, k, l, o))),
      el("p", { class: "muted small wide", text: SK_SHORTHAND }),
      mfield(d, "aliases", "คำที่เจอในบิลแล้วนับเป็นรายการนี้ (คั่นด้วย , — ใส่รุ่นไว้ได้เลย เช่น SC-N8; รุ่นเดียวกันหลายสเปคใช้คู่ เช่น TR-N2/3+12-18A)", { wide: true, maxlength: 2000 })),
    el("label", { class: "mgcheck" }, (() => { const c = el("input", { type: "checkbox" }); c.checked = !!d.track_out; c.addEventListener("change", () => { d.track_out = c.checked; }); return c; })(),
      " เบิกด้วยใบติ๊กประจำสัปดาห์ ", el("span", { class: "muted small", text: "(ไม่ติ๊ก = ของสิ้นเปลือง นับที่ชั้นแทน ไม่ต้องติ๊กทุกชิ้น)" })),
    item ? el("div", { id: "skPrices", class: "skprices" }) : null,
    history && history.length ? el("details", { open: true }, el("summary", { text: `ความเคลื่อนไหว (${history.length})` }),
      ...history.map((m) => el("div", { class: "prhist small" }, el("span", { text: beDate(m.move_date) }), el("span", { text: KIND[m.kind] || m.kind }),
        el("strong", { text: `${m.qty > 0 ? "+" : ""}${buQty(m.qty)}` }), el("span", { class: "muted", text: [m.machine_name, m.notes].filter(Boolean).join(" · ") }),
        m.purchase_line_id ? el("span", { class: "muted", text: "จากบิล" }) : el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: async () => {
          if (!confirm("ลบรายการเคลื่อนไหวนี้ใช่ไหม?")) return;
          try { await api(`/api/stock/moves/${m.id}`, { method: "DELETE" }); } catch (err) { showErr("buDlgError", err.message); return; }
          $("buDialog").close(); await loadStock();
        } })))) : null,
  ], async () => {
    const body = { ...Object.fromEntries(SK_FIELDS.map(([k]) => [k, d[k]])), track_out: !!d.track_out };
    if (d.kind) delete body.name;                                  // the server builds the standard name from the parts
    if (item || d.aliases) body.aliases = String(d.aliases || "").split(",").map((w) => w.trim()).filter(Boolean);
    bu.stock = await api(item ? `/api/stock/items/${item.id}` : "/api/stock/items", jsonOpts(item ? "PATCH" : "POST", body));
    bu.opts = null;                                    // the bill page's item list is stale now
    await loadStock();
  });
  const guess = () => {                               // fill หมวด / หน่วย from what is typed, until the person picks one themselves
    const g = skGuess(d);
    if (!catBox._touched && !(item && item.category) && g.category) { catBox._set(g.category); catBox._hint(`เดาให้: ${g.category} (${g.why}) — เปลี่ยนได้`); }
    if (!unitBox._touched && !(item && item.unit) && g.unit) { unitBox._set(g.unit); unitBox._hint(`เดาให้จากชนิดเดียวกัน — เปลี่ยนได้`); }
  };
  for (const k of ["kind", "brand", "model", "spec", "name"]) $(`mf_${k}`).addEventListener("input", () => { skSyncName(d); guess(); });
  skSyncName(d);
  skSteppers($("buDlgBody"));
  if (item) skLoadPrices(item);
}

// the prices this item was bought at, brand by brand and shop by shop (a B-29 belt is one item whoever made it: which make is cheapest, and where)
async function skLoadPrices(item) {
  const box = $("skPrices");
  if (!box) return;
  let r;
  try { r = await api(`/api/stock/items/${item.id}/prices`); } catch (_) { return; }
  if (!$("skPrices")) return;                                    // the dialog was closed meanwhile
  const unit = item.unit ? `/${item.unit}` : "";
  const money = (v) => (v === null || v === undefined ? "–" : `${buMoney(v)} ฿${unit}`);
  if (!r.count) { fill(box, el("p", { class: "muted small", text: "ยังไม่เคยซื้อ — ราคาจะขึ้นที่นี่เมื่อบันทึกบิลที่มีของชิ้นนี้" })); return; }
  fill(box, el("h4", { class: "section-title", text: "เทียบราคาที่เคยซื้อ" }),
    el("table", { class: "lines skprice-table" },
      el("thead", {}, el("tr", {}, ...["ยี่ห้อ", "ล่าสุด", "ถูกสุด (ร้าน)", "ซื้อ"].map((t) => el("th", { text: t })))),
      el("tbody", {}, ...r.by_brand.map((b, i) => el("tr", { class: i === 0 && r.by_brand.length > 1 ? "cheapest" : "" },
        el("td", {}, el("strong", { text: b.brand })),
        el("td", { text: `${money(b.last_cost)} · ${b.last_vendor || "ไม่ระบุร้าน"} · ${beDate(b.last_date)}` }),
        el("td", { text: `${money(b.lowest_cost)} · ${b.lowest_vendor || "ไม่ระบุร้าน"}` }),
        el("td", { text: `${b.times} ครั้ง` }))))),
    r.by_vendor.length ? el("div", { class: "chips" }, ...r.by_vendor.map((v, i) => el("span", { class: `badge${i === 0 ? " ok" : ""}`, text: `${v.vendor} ${buMoney(v.last_cost)} ฿ (${beDate(v.last_date)})` }))) : null);
}

function skSyncName(d) {                       // while typing the parts, the name field shows the standard name (and cannot be typed over)
  const name = $("mf_name");
  if (!name) return;
  name.readOnly = !!String(d.kind || "").trim();
  if (name.readOnly) { d.name = skStandardName(d); name.value = d.name; }
}

// several things at once: the mechanic's paper typed once a week (kind "out"), or several things received (kind "in"): one list, one save
function openStockBatch(kind) {
  const isOut = kind === "out";
  const items = bu.stock.items.filter((i) => i.active !== false), byName = new Map(items.map((i) => [i.name, i]));
  const machines = bu.stock.machines.filter((m) => m.active !== false);
  const d = { move_date: todayISO(), notes: "" }, rows = [], list = el("div", { class: "skw" });
  const addRow = () => {
    const name = el("input", { type: "text", list: "skwItems", placeholder: "พิมพ์ชื่อ/รุ่น แล้วเลือกจากรายการที่ขึ้น", autocomplete: "off", "aria-label": "รายการที่เบิก" });
    const qty = el("input", { type: "number", min: "0", step: "any", inputmode: "decimal", placeholder: "จำนวน", "aria-label": "จำนวน" });
    const machine = el("select", { "aria-label": "ใช้กับเครื่อง" }, el("option", { value: "", text: "— ไม่ระบุเครื่อง —" }), ...machines.map((m) => el("option", { value: m.id, text: m.name })));
    const have = el("span", { class: "muted small skw-have" });
    name.addEventListener("input", () => { const it = byName.get(name.value.trim()); fill(have, it ? `มี ${buQty(it.on_hand)} ${it.unit || ""}` : ""); });
    rows.push({ name, qty, machine });
    list.append(el("div", { class: `skw-row${isOut ? "" : " in"}` }, name, qty, isOut ? machine : null, have));
    skSteppers(list);
  };
  for (let i = 0; i < 8; i++) addRow();
  buOpenDialog(isOut ? "เบิกของประจำสัปดาห์ (จากกระดาษของช่าง)" : "รับของเข้าสต็อกหลายรายการ", [
    el("datalist", { id: "skwItems" }, ...items.map((i) => el("option", { value: i.name }))),
    el("p", { class: "muted small", text: (isOut ? "พิมพ์ของที่ช่างเขียนเบิกไว้ในสัปดาห์นี้ทีเดียว" : "พิมพ์ของที่รับเข้ามาทีเดียว (ไม่มีบิลก็ได้)") + " — ช่องไหนเว้นว่างไว้ข้ามให้ ถ้ามีแถวไหนผิด ระบบจะบอกเลขแถวและยังไม่บันทึกอะไรเลย กด “+ เพิ่มแถว” ได้เรื่อยๆ" }),
    el("div", { class: "mfields" }, mfield(d, "move_date", isOut ? "วันที่เบิก (ใส่วันสิ้นสัปดาห์ก็ได้)" : "วันที่รับเข้า", { type: "date", caption: true }), mfield(d, "notes", "หมายเหตุ (เช่น สัปดาห์ 20–26 ต.ค.)", { wide: true, maxlength: 300 })),
    list,
    el("button", { class: "btn small", type: "button", text: "+ เพิ่มแถว", onclick: () => { addRow(); rows[rows.length - 1].name.focus(); } }),
  ], async () => {
    const out = [];
    rows.forEach((r, i) => {
      const n = r.name.value.trim();
      if (!n && !r.qty.value.trim()) return;
      const it = byName.get(n);
      if (!it) throw new Error(`แถวที่ ${i + 1}: เลือกรายการจากรายการที่ขึ้นให้ (พิมพ์ชื่อหรือรุ่นแล้วกดเลือก)`);
      out.push({ item_id: it.id, qty: r.qty.value, machine_id: r.machine.value || "" });
    });
    if (!out.length) throw new Error("ยังไม่ได้ใส่รายการ");
    const r = await api("/api/stock/moves/batch", jsonOpts("POST", { kind, move_date: d.move_date, notes: d.notes, rows: out }));
    bu.stock = r;
    renderStock();
    if (isOut && r.negative && r.negative.length) alert(`บันทึก ${r.written} รายการแล้ว\nแต่ของเหล่านี้ในระบบติดลบ (เช็คว่านับยอดเริ่มต้นหรือจำนวนที่ช่างเขียนถูกไหม):\n• ${r.negative.join("\n• ")}`);
  });
}

// several NEW items at once (the standard name is built per row from ชนิด + ยี่ห้อ + รุ่น + สเปค)
function openStockMulti() {
  const d = { category: "" }, rows = [], list = el("div", { class: "skm" });
  const addRow = () => {
    const f = (ph, cls, type = "text") => el("input", { type, placeholder: ph, autocomplete: "off", class: cls, "aria-label": ph, ...(type === "number" ? { min: "0", step: "any", inputmode: "decimal" } : {}) });
    const r = { kind: f("ชนิด เช่น แมกเนติก", "skm-kind"), brand: f("ยี่ห้อ", "skm-brand"), model: f("รุ่น / เบอร์", "skm-model"), spec: f("สเปค เช่น 220V", "skm-spec"),
      unit: f("หน่วย", "skm-unit"), qty: f("จำนวน", "skm-qty", "number") }, name = el("div", { class: "skm-name muted small" });
    r.unit.setAttribute("list", "skmUnitList");
    const sync = () => fill(name, skStandardName({ kind: r.kind.value, brand: r.brand.value, model: r.model.value, spec: r.spec.value }));
    for (const k of ["kind", "brand", "model", "spec"]) r[k].addEventListener("input", sync);
    rows.push(r);
    list.append(el("div", { class: "skm-row" }, r.kind, r.brand, r.model, r.spec, r.unit, r.qty, name));
    skSteppers(list);
  };
  for (let i = 0; i < 6; i++) addRow();
  buOpenDialog("เพิ่มหลายรายการ", [
    el("p", { class: "muted small", text: "ใส่ชนิดอย่างน้อยในแต่ละแถว — ชื่อมาตรฐานสร้างให้เอง (โชว์ใต้แต่ละแถว) รุ่นจะถูกใช้เป็นคำที่เจอในบิลให้ด้วย จำนวนที่ใส่คือยอดตั้งต้นในสต็อก (เว้นว่างได้) ถ้ามีแถวไหนผิด ระบบจะบอกเลขแถวและยังไม่บันทึกอะไรเลย" }),
    el("div", { class: "mfields" }, skChoice(d, "category", "หมวดของชุดนี้", skCategories())),
    el("datalist", { id: "skmUnitList" }, ...skUnits().map((u) => el("option", { value: u }))),
    list,
    el("button", { class: "btn small", type: "button", text: "+ เพิ่มแถว", onclick: () => { addRow(); rows[rows.length - 1].kind.focus(); } }),
  ], async () => {
    const body = rows.map((r) => ({ kind: r.kind.value, brand: r.brand.value, model: r.model.value, spec: r.spec.value, unit: r.unit.value, qty: r.qty.value }));
    bu.stock = await api("/api/stock/items/batch", jsonOpts("POST", { category: d.category, rows: body }));
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
  skSteppers($("buDlgBody"));
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
  for (const b of document.querySelectorAll("#bnPurposes [data-purpose]")) b.addEventListener("click", () => buSetPurpose(b.dataset.purpose));
  buSetPurpose("stock");
  $("bnManual").addEventListener("click", async () => {
    if (!bu.opts) { try { await buLoadOptions(); } catch (err) { showErr("bnError", err.message); return; } }     // pressed before the page finished loading
    const job = { id: bu.nextId++, files: [], status: "ready", error: "", t0: null, extraction: null, server: null, review: buMakeReview(null, bu.purpose), manual: true, purpose: bu.purpose };
    bu.jobs.push(job);
    buBadge();
    buOpenJob(job);
  });
  $("bnAddLine").addEventListener("click", () => { bu.cur.review.lines.push(buBlankLine()); buRenderLines(); });
  $("bnBack").addEventListener("click", () => { const editing = bu.cur && bu.cur.editId; bu.cur = null; if (editing) goHash("#purchases"); else buShowHome(); });
  $("bnDiscard").addEventListener("click", () => {
    if (!bu.cur) return;
    const j = bu.cur;
    if (j.editId) { if (confirm("ยกเลิกการแก้ไข (ไม่เปลี่ยนอะไรในบิลนี้) ใช่ไหม?")) { bu.cur = null; goHash("#purchases"); } return; }
    if (confirm("ทิ้งบิลนี้ทั้งใบ (ยังไม่ได้บันทึก) ใช่ไหม?")) { buRemoveJob(j, false); buShowHome(); }
  });
  $("bnSave").addEventListener("click", () => buSave(false));
  let prTimer = null;
  $("prQ").addEventListener("input", () => { clearTimeout(prTimer); prTimer = setTimeout(loadPrices, 300); });
  for (const id of ["prCat", "prVendor"]) $(id).addEventListener("change", renderPrices);
  $("prVat").addEventListener("change", () => { bu.vat = $("prVat").value; renderPrices(); });
  $("piBack").addEventListener("click", () => goHash("#prices"));
  $("prUq").addEventListener("input", renderUnlinked);
  $("prUv").addEventListener("change", renderUnlinked);
  for (const id of ["skQ", "skCat", "skKind", "skBrand", "skVolt", "skLoc", "skLow"]) $(id).addEventListener(id === "skQ" ? "input" : "change", renderStock);
  $("skClear").addEventListener("click", skClearFilters);
  $("skAdd").addEventListener("click", () => openStockItem(null));
  $("skWeekly").addEventListener("click", () => bu.stock && bu.stock.ready && openStockBatch("out"));
  $("skIn").addEventListener("click", () => bu.stock && bu.stock.ready && openStockBatch("in"));
  $("skMulti").addEventListener("click", () => bu.stock && bu.stock.ready && openStockMulti());
  $("skSheet").addEventListener("click", () => goHash("#stock-sheet"));
  $("shKind").addEventListener("change", renderStockSheet);
  $("shPrint").addEventListener("click", skPrintSheet);
  $("shBack").addEventListener("click", () => goHash("#stock"));
  $("buDlgCancel").addEventListener("click", () => $("buDialog").close());
  $("buViewerClose").addEventListener("click", () => $("buViewer").close());
  $("buViewer").addEventListener("click", (e) => { if (e.target === $("buViewer")) $("buViewer").close(); });          // a click on the dim area closes it
  $("buViewer").addEventListener("close", () => fill($("buViewerBody")));
  $("buDlgSave").addEventListener("click", buDialogSave);
  // bills read but not saved live in this page only: warn before a refresh or closing the tab throws them away
  window.addEventListener("beforeunload", (e) => { if (bu.jobs.some((j) => j.status !== "error")) { e.preventDefault(); e.returnValue = ""; } });
}


// ================================================================== the printed sheets (#stock-sheet)
// "out": the mechanic's weekly sheet -- every tracked item pre-printed in the stock table's order with 6 boxes: one tick per piece taken (no names to write,
// no day of the week); the owner adds the ticks up when typing the week in. "count": the list for counting the cheap consumables on the shelf.
const SHEET_BOXES = 6;

function skSheetItems(kind) {
  const keys = ["category", "kind", "brand", "model", "spec", "name"];
  return bu.stock.items.filter((i) => i.active !== false && (kind === "out" ? i.track_out !== false : i.track_out === false))
    .sort((a, b) => { for (const k of keys) { const c = String(a[k] || "").localeCompare(String(b[k] || ""), "th", { numeric: true }); if (c) return c; } return 0; });
}

async function openStockSheet() {
  showView("sheet");
  showErr("shError", "");
  try { bu.stock = await api("/api/stock"); } catch (err) { showErr("shError", `โหลดสต็อกไม่สำเร็จ: ${err.message}`); return; }
  renderStockSheet();
}

function renderStockSheet() {
  if (!bu.stock) return;
  const kind = $("shKind").value, items = skSheetItems(kind), today = beDateOf(new Date());
  const blank = (w) => el("span", { class: "ss-blank", style: `min-width:${w}` });
  const boxes = () => el("span", { class: "ss-boxes" }, ...Array.from({ length: SHEET_BOXES }, () => el("span", { class: "ss-box" })));
  const rows = [];
  let group = "";
  for (const i of items) {
    const g = [i.category, i.kind].filter(Boolean).join(" · ");
    if (g !== group) { group = g; rows.push(el("tr", { class: "ss-group" }, el("td", { colspan: kind === "out" ? "5" : "5", text: g || "อื่นๆ" }))); }
    rows.push(kind === "out"
      ? el("tr", {}, el("td", { class: "ss-code", text: i.code || "" }), el("td", { class: "ss-name" }, i.name, i.remark ? el("div", { class: "ss-remark", text: i.remark }) : null),
          el("td", { class: "ss-have", text: buQty(i.on_hand) }), el("td", {}, boxes()), el("td", {}, el("span", { class: "ss-box wide" })))
      : el("tr", {}, el("td", { class: "ss-code", text: i.code || "" }), el("td", { class: "ss-name" }, i.name, i.remark ? el("div", { class: "ss-remark", text: i.remark }) : null),
          el("td", { class: "ss-have", text: `${buQty(i.on_hand)} ${i.unit || ""}`.trim() }), el("td", {}, el("span", { class: "ss-box count" })), el("td", { class: "ss-note" })));
  }
  const table = el("table", { class: "ss-table" },
    el("thead", {}, el("tr", {}, ...(kind === "out"
      ? ["รหัส", "รายการ", "มี", "ติ๊ก 1 ช่อง = 1 ชิ้นที่หยิบ", "เกิน 6 ชิ้น เขียนจำนวน"]
      : ["รหัส", "รายการ", "ระบบคิดว่ามี", "นับได้จริง", "หมายเหตุ"]).map((t) => el("th", { text: t })))),
    el("tbody", {}, ...rows));
  const extra = kind === "out" ? el("table", { class: "ss-table ss-extra" },
    el("thead", {}, el("tr", {}, el("th", { text: "ของที่ไม่มีในรายการข้างบน (เขียนชื่อ/รุ่น)" }), el("th", { text: "จำนวน" }), el("th", { text: "ใช้กับ" }))),
    el("tbody", {}, ...Array.from({ length: 6 }, () => el("tr", { class: "ss-line" }, el("td"), el("td"), el("td"))))) : null;
  const head = kind === "out"
    ? [el("h1", { text: "ใบเบิกอะไหล่ประจำสัปดาห์" }), el("div", { class: "ss-meta" }, "สัปดาห์ที่ ", blank("90px"), " ถึง ", blank("90px"), "   ช่าง ", blank("150px"), "   สาขา ", blank("90px")),
       el("p", { class: "ss-how", text: "ติ๊ก 1 ช่องต่อ 1 ชิ้นที่หยิบ · เกิน 6 ชิ้นเขียนจำนวนในช่อง “เกิน 6” · ของที่ไม่มีในรายการเขียนในตารางท้ายใบ · ไม่ต้องเขียนวัน" })]
    : [el("h1", { text: "ใบนับของสิ้นเปลือง" }), el("div", { class: "ss-meta" }, "วันที่นับ ", blank("110px"), "   ผู้นับ ", blank("150px")),
       el("p", { class: "ss-how", text: "นับของจริงบนชั้นแล้วเขียนจำนวนในช่อง “นับได้จริง” — ระบบคิดของที่ใช้ไปจากผลต่าง" })];
  const foot = kind === "out"
    ? el("div", { class: "ss-foot" }, el("span", {}, "ลายเซ็นช่าง ", blank("170px")), el("span", {}, "ลงระบบแล้ว ", el("span", { class: "ss-box" }), "  วันที่ ", blank("90px"), "  ผู้บันทึก ", blank("110px")))
    : el("div", { class: "ss-foot" }, el("span", {}, "ลงระบบแล้ว ", el("span", { class: "ss-box" }), "  วันที่ ", blank("90px"), "  ผู้บันทึก ", blank("110px")));
  fill($("shPaper"), el("div", { class: "stocksheet" }, ...head, table, extra, foot, el("p", { class: "ss-stamp", text: `พิมพ์ ${today} · คอลัมน์ “มี” = จำนวนในระบบตอนพิมพ์` })));
  fill($("shCount"), `${items.length} รายการ${kind === "out" ? " ที่เบิกด้วยใบติ๊ก" : " ที่นับที่ชั้น (ของสิ้นเปลือง)"}`);
}

function skPrintSheet() {
  document.body.classList.add("printing-sheet");
  const done = () => { document.body.classList.remove("printing-sheet"); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
}


// ================================================================== what a bill is: stock / a machine's repair / a reference parts list
const BU_PURPOSE_NOTE = {
  stock: "ของที่ซื้อเข้าห้องอะไหล่ — นับเงิน และรับเข้าสต็อก",
  repair: "อะไหล่ / งานซ่อมของเครื่องจักร 1 เครื่อง — นับเป็นค่าซ่อมของเครื่องนั้น และขึ้นในประวัติงานซ่อมของเครื่อง (บิลหนึ่งใบ = เครื่องเดียว)",
  reference: "รายการอะไหล่ของรุ่นเครื่อง (เช่น ใบรายการ MYCOM 8WB-HE) — เก็บไว้รู้ว่ารุ่นนี้มีอะไหล่อะไร ราคาเท่าไหร่ ไม่นับเงินที่ไหนเลย",
};

function buSetPurpose(purpose) {
  bu.purpose = purpose;
  if (purpose !== "repair") bu.presetMachine = "";
  for (const b of document.querySelectorAll("#bnPurposes [data-purpose]")) {
    b.classList.toggle("active", b.dataset.purpose === purpose);
    b.setAttribute("aria-selected", String(b.dataset.purpose === purpose));
  }
  fill($("bnPurposeNote"), BU_PURPOSE_NOTE[purpose] || "");
}

function buPurposeText(b) {
  if (b.purpose === "repair") return `ซ่อม ${b.machine_name || "เครื่องจักร"}`;
  if (b.purpose === "reference") return `อ้างอิง ${b.machine_model || ""} (ไม่นับเงิน)`.replace("  ", " ");
  return null;
}

// on the check page: what this bill is, and its machine (repair) or model (reference)
function buPurposeBlock(d) {
  d.purpose = d.purpose || "stock";
  const purposes = (bu.opts && bu.opts.purposes) || { stock: "ซื้อเข้าสต็อก", repair: "ซ่อมเครื่องจักร", reference: "รายการอะไหล่อ้างอิง (ไม่นับเงิน)" };
  const sel = el("select", { id: "bnPurpose", "aria-label": "บิลนี้คือ" }, ...Object.entries(purposes).map(([k, v]) => el("option", { value: k, text: v })));
  sel.value = d.purpose;
  sel.addEventListener("change", () => { d.purpose = sel.value; buRenderReview(); });
  const parts = [el("div", { class: "mfield" }, el("label", { for: "bnPurpose", text: "บิลนี้คือ" }), sel)];
  if (d.purpose === "repair") {
    parts.push(mselect(d, "machine_id", "เครื่องที่ซ่อม", (bu.opts.machines || []).filter((m) => m.active !== false || m.id === d.machine_id).map((m) => [m.id, m.name]), { blank: "— เลือกเครื่อง —" }));
  } else if (d.purpose === "reference") {
    parts.push(mfield(d, "machine_model", "รุ่นเครื่อง (ใช้กับทุกเครื่องรุ่นนี้)", { list: "bnModelList", placeholder: "MYCOM 8WB-HE", maxlength: 80 }),
      el("datalist", { id: "bnModelList" }, ...(bu.opts.machine_models || []).map((m) => el("option", { value: m }))));
  }
  return el("div", { class: `bn-purpose ${d.purpose}` }, el("div", { class: "mfields" }, ...parts), el("p", { class: "muted small", text: BU_PURPOSE_NOTE[d.purpose] }));
}

// the machine page's button: every price and part known for this machine
async function openMachinePrices(machine) {
  let r;
  try { r = await api(`/api/machines/${machine.id}/prices`); } catch (err) { alert(err.message); return; }
  const q = el("input", { type: "search", placeholder: "ค้นหาอะไหล่ ร้าน", "aria-label": "ค้นหา", class: "mp-q" });
  const body = el("tbody");
  const paint = () => {
    const t = q.value.trim().toLowerCase();
    const rows = r.rows.filter((x) => !t || [x.description, x.vendor].filter(Boolean).join(" ").toLowerCase().includes(t));
    fill(body, ...(rows.length ? rows.map((x) => el("tr", { class: x.counts ? "" : "mp-ref" },
      el("td", { class: "nowrap", text: beDate(x.bill_date) }), el("td", { text: x.vendor || "" }),
      el("td", {}, el("span", { class: `badge${x.counts ? "" : " shop"}`, text: x.purpose === "reference" ? "อ้างอิง" : x.purpose === "repair" ? "ซ่อม" : "ซื้อ" })),
      el("td", { text: x.description }), el("td", { class: "r nowrap", text: `${buQty(x.quantity)} ${x.unit || ""}`.trim() }),
      el("td", { class: "r nowrap", text: x.unit_cost === null || x.unit_cost === undefined ? "–" : `${buMoney(x.unit_cost)} ฿` }),
      el("td", { class: "r nowrap", text: x.line_total === null || x.line_total === undefined ? "–" : buMoney(x.line_total) })))
      : [el("tr", {}, el("td", { colspan: "7", class: "muted", text: r.rows.length ? "ไม่พบรายการที่ตรงกับคำค้น" : "ยังไม่มีบิลของเครื่องนี้ — บันทึกบิลซื้อของแบบ “ซ่อมเครื่องจักร” หรือ “รายการอะไหล่อ้างอิง”" }))]));
  };
  q.addEventListener("input", paint);
  paint();
  const model = [r.machine.make, r.machine.model].filter(Boolean).join(" ");
  buOpenDialog(`ราคาและอะไหล่ · ${r.machine.name}`, [
    el("p", { class: "muted small", text: `${model ? `รุ่น ${model} · ` : ""}ค่าใช้จ่ายของเครื่องนี้ ${buMoney(r.spent)} บาท (ไม่นับบิลอ้างอิง) · ${r.bills} บิล${r.reference_lines ? ` · รายการอ้างอิงของรุ่นนี้ ${r.reference_lines} รายการ` : ""}` }),
    q,
    el("div", { class: "bu-scroll" }, el("table", { class: "lines mp-table" },
      el("thead", {}, el("tr", {}, ...["วันที่", "ร้าน", "ประเภท", "รายการ", "จำนวน", "ต่อหน่วยจริง", "รวม"].map((t) => el("th", { text: t })))), body)),
  ], null);
}
