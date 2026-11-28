"use strict";

// บัญชี › ลงข้อมูล › กล่องรับไฟล์ (the drop box; owner, 24 Nov 2026): "drop ANY monthly document" -- the app works out what it is, its
// branch and its month from what is INSIDE the file (never one label: HQ forgets to update titles, payroll PDF names may be stale) and
// shows a preview. Nothing is booked until ลงบัญชี is pressed; booking replaces (dropping the same thing again never doubles it).
//   * the drop card (drag and drop, or tap to pick; many files; sent a few at a time so a phone never waits on one huge request)
//   * the dropped files: what each is, its month and branch(es), its status, totals, and what can be done with it
//   * the preview dialog: where the month came from, any disagreement to choose, each batch with its total vs. the rows' total and the
//     ROLE of every row (a cheque that tops up petty cash is NOT a cost -- the papers are; one source books a cost, the others only pay it)
//   * อ่านด้วย AI (phase 2): only on a click, the page count asked first, then polled every 3 s until done
//   * ตรวจความถูกต้อง: the proofs that nothing is counted twice (app/acc_checks.py), green / amber / red / grey
// Server: app/inbox.py (/api/accounts/inbox*, /api/accounts/checks). The checklist rows below it (accounts.js) link to these files.

// busy: file ids with a request running (every action button of that file is disabled until it answers -- a double click on
// ลงบัญชี once booked a file twice); running: id -> file name of the AI reads being polled (reported one by one when they end).
const ibx = { files: [], checks: null, aiReady: false, msgs: [], poll: null, seq: 0, open: new Set(), busy: new Set(), running: new Map() };
const IBX_STATUS = {
  read: ["อ่านแล้ว รอลงบัญชี", "warn"], booked: ["ลงบัญชีแล้ว", "ok"], needs_choice: ["ต้องเลือก", "urgent"],
  stored: ["เก็บไว้ ไม่ลงบัญชี", ""], needs_ai: ["อ่านด้วย AI ได้", "shop"], ai_running: ["AI กำลังอ่าน...", "shop"], failed: ["อ่านไม่สำเร็จ", "urgent"],
};
// what a conflict asks (app/inbox_common.py conflicts[].key); the badge and the button say the same words
const IBX_CONFLICT = {
  month: "เลือกเดือน", branch: "เลือกสาขา", station: "เลือกสถานี", group: "เลือกกลุ่มเงินเดือน", half: "เลือกครึ่งเดือน",
  duplicate: "ยอดซ้ำ — เลือก", replace_old: "แทนของเดิมไหม",
};
const IBX_NONCOST = new Set(["funding", "pays_bill", "advance", "transfer", "owner", "info"]);
const IBX_CHUNK = 4;                         // files per request
const IBX_MAX_BYTES = 50 * 1024 * 1024;      // one file (the server's limit for the drop box)
const IBX_CHUNK_BYTES = 50 * 1024 * 1024;    // one request: a big scan goes on its own
const IBX_ROW_CAP = 200;                     // rows drawn per batch before "แสดงทั้งหมด"
const IBX_PICK_MONTHS = 18;                  // company months offered when the file names no month
const IBX_STATE = { ok: "ok", pass: "ok", green: "ok", done: "ok", warn: "warn", amber: "warn", check: "warn", bad: "bad", red: "bad", fail: "bad", error: "bad" };
// the old checks shape ({key: number}): Thai names of the number keys; any other key (and any non-number value) is skipped
const IBX_NUM_OLD = {
  in: ["เข้า", "money"], out: ["ออก", "money"], diff: ["ต่าง", "money"], pct: ["ต่าง", "pct"], total: ["ยอดรวม", "money"],
  pairs: ["คู่ที่ดูซ้ำ", "count"], by_document: ["จากเลขเอกสาร", "count"], months: ["จำนวนเดือน", "count"], warn: ["จุดที่ควรตรวจ", "count"],
};

const ibxMonth = (m) => (m && /^\d{4}-\d{2}$/.test(m) ? beMonth(m) : "");
const ibxMoney = (n) => (n == null ? "–" : fmtMoney(n));
const ibxSame = (a, b) => a != null && b != null && Math.abs(a - b) <= 1;
const ibxInt = (n, d = 0) => Number(n || 0).toLocaleString("th-TH", { minimumFractionDigits: 0, maximumFractionDigits: d });
const ibxMB = (bytes) => `${(Number(bytes || 0) / 1024 / 1024).toLocaleString("th-TH", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`;

// a timestamp from the server (UTC) as the viewer's own date: 25/11 18:30 UTC is 26 พ.ย. in Bangkok, not 25
function ibxWhen(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? beDate(ts) : beDateOf(d);
}

// a file a newer file took over completely (contract: row.superseded / superseded_by; older servers only left the error text)
function ibxSuperseded(f) {
  if (f.superseded) return { by: f.superseded_by || "" };
  const m = /^ถูกแทนที่ด้วย\s*(.*)$/.exec(f.error || "");
  return m && f.status !== "booked" ? { by: m[1] || "" } : null;
}

// a question asked per part of the file ("month:NM": the NM sheets of a Bangchak workbook of several stations) reads as its kind
const ibxConflictWord = (k) => IBX_CONFLICT[k] || IBX_CONFLICT[String(k || "").split(":")[0]] || "ต้องเลือก";

function ibxChoiceLabel(f) {
  const keys = [...new Set((f.conflicts || []).map((c) => c && c.key).filter(Boolean))];
  if (!keys.length) return "ต้องเลือก";
  return [...new Set(keys.map(ibxConflictWord))].join(" · ");
}

function ibxStatusText(f) {
  const sup = ibxSuperseded(f);
  if (sup) return sup.by ? `ถูกแทนที่ด้วย ${sup.by}` : "ถูกแทนที่ด้วยไฟล์ใหม่";
  if (f.status === "needs_choice") return ibxChoiceLabel(f);
  if (f.status === "needs_ai") return f.pages ? `อ่านด้วย AI ได้ (${f.pages} หน้า)` : "อ่านด้วย AI ได้";
  if (f.status === "ai_running" && f.progress && f.progress.total) return `AI กำลังอ่าน... (${f.progress.done}/${f.progress.total} หน้า)`;
  return (IBX_STATUS[f.status] || [f.status_label || f.status])[0];
}

function ibxBadge(f) {
  const tone = ibxSuperseded(f) ? "" : (IBX_STATUS[f.status] || ["", ""])[1];
  return el("span", { class: `badge ${tone}`.trim(), text: ibxStatusText(f) });
}

// the last company months (the 26th starts the next one, as acxMonths), newest first -- for a month the file did not name
function ibxMonthChoices(now = new Date(), n = IBX_PICK_MONTHS) {
  const out = [];
  let y = now.getFullYear(), m = now.getMonth() + 1;
  if (now.getDate() >= 26) { m += 1; if (m > 12) { m = 1; y += 1; } }
  for (let i = 0; i < n; i++) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m -= 1; if (m < 1) { m = 12; y -= 1; }
  }
  return out;
}

// files to send, a few per request and never more than IBX_CHUNK_BYTES in one (unless one file is that big on its own);
// a file over IBX_MAX_BYTES is not sent at all, so it cannot make the others fail with it
function ibxPlanUpload(list, maxBytes = IBX_MAX_BYTES, perChunk = IBX_CHUNK, chunkBytes = IBX_CHUNK_BYTES) {
  const skipped = [], chunks = [];
  let cur = [], size = 0;
  for (const file of list) {
    const b = Number(file.size) || 0;
    if (b > maxBytes) { skipped.push(file); continue; }
    if (cur.length && (cur.length >= perChunk || size + b > chunkBytes)) { chunks.push(cur); cur = []; size = 0; }
    cur.push(file); size += b;
  }
  if (cur.length) chunks.push(cur);
  return { chunks, skipped };
}

const ibxTooBig = (file) => `${file.name}: ไฟล์ใหญ่ ${ibxMB(file.size)} เกิน ${ibxMB(IBX_MAX_BYTES)} — ไม่ได้ส่งไฟล์นี้ (ไฟล์อื่นส่งตามปกติ) · ย่อขนาดหรือแยกเป็นหลายไฟล์แล้วส่งใหม่`;

// when the AI reads end: each file by what it became (a failed read in red with its reason)
function ibxPollReport(running, files) {
  const out = [];
  for (const [id, name] of running) {
    const f = (files || []).find((x) => x.id === id);
    const n = (f && f.file_name) || name || "ไฟล์";
    if (!f) out.push({ text: `${n}: AI อ่านจบแล้ว แต่ไม่อยู่ในรายการของเดือนนี้ — เปลี่ยนเดือนเพื่อดู` });
    else if (f.status === "failed") out.push({ text: `${n}: AI อ่านไม่สำเร็จ — ${f.error || "ไม่ทราบสาเหตุ"}`, bad: true });
    else if (f.status === "needs_choice") out.push({ text: `${n}: AI อ่านเสร็จแล้ว — ${ibxChoiceLabel(f)} ก่อนลงบัญชี` });
    else if (f.status === "read") out.push({ text: `${n}: AI อ่านเสร็จแล้ว — เปิดดูก่อนลงบัญชี` });
    else if (f.status === "stored") out.push({ text: `${n}: AI อ่านเสร็จแล้ว — เก็บไว้ ไม่ลงบัญชี` });
    else if (f.status === "needs_ai") out.push({ text: `${n}: AI ยังอ่านไม่เสร็จ — กดอ่านด้วย AI อีกครั้ง`, bad: true });
    else out.push({ text: `${n}: ${ibxStatusText(f)}` });
  }
  return out.length ? out : [{ text: "AI อ่านจบแล้ว — ดูสถานะของแต่ละไฟล์ในรายการ" }];
}

// what deleting a file does beyond the file itself (asked before ลบ)
function ibxDeleteWarning(f) {
  const out = [];
  const booked = f.status === "booked";
  if (booked) out.push("รายการที่ลงบัญชีจากไฟล์นี้จะถูกลบออกจากบัญชีด้วย");
  if (booked && f.kind === "pea_bill") {
    const next = f.month && /^\d{4}-\d{2}$/.test(f.month) ? ibxMonth(ibxShiftMonth(f.month, 1)) : "";
    out.push(`บิลค่าไฟนี้ถูกจ่ายด้วยชุดเช็ค HQ${next ? ` งวด ${next}` : " เดือนถัดไป"} — ระบบจะอ่านชุดเช็คนั้นใหม่ให้ เพื่อให้ค่าไฟกลับไปลงเป็นค่าใช้จ่ายในชุดเช็คแทน (ค่าไฟไม่หายจากเดือน)`);
  }
  if (booked && f.remove_note) out.push(f.remove_note);          // the reader says what removing does (e.g. Bangchak: the old entries come back)
  return out;
}

function ibxRereadWarning(f) {
  if (f.status !== "booked") return "";
  return "ไฟล์นี้ลงบัญชีแล้ว — ถ้าอ่านใหม่แล้วลงได้ ระบบจะลงแทนของเดิมทันที\n"
    + "ถ้าอ่านใหม่แล้วต้องเลือก ของเดิมยังอยู่ในบัญชี จนกว่าจะเลือกแล้วกดลงบัญชี";
}

function ibxShiftMonth(ym, k) {
  const [y, m] = ym.split("-").map(Number);
  const t = y * 12 + (m - 1) + k;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
}

// one request at a time per file: the file's buttons (list and dialog) are disabled until it answers
async function ibxRun(id, work) {
  if (ibx.busy.has(id)) { ibxSay("ไฟล์นี้กำลังทำรายการอยู่ รอสักครู่", true); return false; }
  ibx.busy.add(id);
  const btns = typeof document !== "undefined" ? [...document.querySelectorAll("[data-ibx-file]")].filter((b) => b.dataset.ibxFile === id) : [];
  const was = btns.map((b) => b.disabled);
  btns.forEach((b) => { b.disabled = true; });
  try { await work(); }
  finally {
    ibx.busy.delete(id);
    btns.forEach((b, i) => { b.disabled = was[i]; });
  }
  return true;
}

// a few page-only styles (ibx- prefix), added once
function ibxStyle() {
  if (typeof document === "undefined" || document.getElementById("ibxStyle")) return;
  document.head.append(el("style", { id: "ibxStyle", text:
    ".ibx-file.ibx-superseded{opacity:.75}"
    + ".ibx-file.ibx-busy .ibx-actions{opacity:.6}"
    + ".ibx-monthpick{display:block;margin-top:.4rem;max-width:100%}"
    + ".ibx-nums{display:flex;flex-wrap:wrap;gap:.25rem .75rem;margin-top:.25rem}"
    + ".ibx-nums .ibx-n b{font-variant-numeric:tabular-nums}"
    + ".ibx-table td.ibx-amt{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}" }));
}

// ---------------------------------------------------------------- the cards (accounts.js renderAccounts puts them on the page)
function ibxDropCard() {
  const input = el("input", { type: "file", multiple: true, hidden: true });
  const drop = el("div", { class: "drop ibx-drop", tabindex: "0", role: "button", "aria-label": "เลือกไฟล์" },
    el("strong", { text: "แตะเพื่อเลือกไฟล์ หรือลากไฟล์มาวางที่นี่" }),
    el("div", { class: "small", text: "PDF · รูปถ่าย · Excel · CSV — หลายไฟล์พร้อมกันได้" }), input);
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { const list = [...input.files]; input.value = ""; ibxUpload(list); });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); ibxUpload([...e.dataTransfer.files]); });
  return el("div", { class: "card acx-feeds ibx-card" },
    el("h3", { text: "กล่องรับไฟล์ — โยนไฟล์ทั้งหมดของงวดนี้ที่นี่" }),
    el("p", { class: "muted small", text: "ระบบดูจากเนื้อในไฟล์ว่าเป็นเอกสารอะไร ของเดือนไหน สาขาไหน (ไม่เชื่อชื่อไฟล์อย่างเดียว) ถ้าข้อมูลในไฟล์ไม่ตรงกันจะให้เลือก · ยังไม่ลงบัญชีจนกว่าจะกด ลงบัญชี · ส่งไฟล์เดิมซ้ำหรือลงบัญชีซ้ำ ระบบไม่นับซ้ำ" }),
    drop,
    el("div", { id: "ibxMsg", class: "ibx-msgs small", "aria-live": "polite" }, ...ibx.msgs.map((m) => el("div", { class: m.bad ? "warn-text" : "", text: m.text }))));
}

function ibxListCard() {
  ibxStyle();
  return el("div", { class: "card acx-feeds" },
    el("h3", { text: "ไฟล์ที่โยนเข้ามา" }),
    el("p", { class: "muted small", text: "ไฟล์ของเดือนที่เลือก และทุกไฟล์ที่ยังไม่ได้ลงบัญชี (ทุกเดือน) — ใหม่สุดอยู่บน" }),
    el("div", { id: "ibxList" }, el("p", { class: "muted small", text: "กำลังโหลด…" })));
}

function ibxChecksCard() {
  return el("div", { class: "card acx-feeds" }, el("h3", { text: "ตรวจความถูกต้อง" }),
    el("div", { id: "ibxChecks" }, el("p", { class: "muted small", text: "กำลังตรวจ…" })));
}

async function ibxLoad() {
  const seq = ++ibx.seq;
  const month = $("acxMonth").value, branch = $("acxBranch").value;
  const [list, checks] = await Promise.all([
    api(`/api/accounts/inbox?month=${encodeURIComponent(month)}`).catch((e) => ({ error: e.message })),
    api(`/api/accounts/checks?month=${encodeURIComponent(month)}&branch=${encodeURIComponent(branch)}`).catch((e) => ({ error: e.message }))]);
  if (seq !== ibx.seq) return;
  if (list.error) { ibx.files = null; ibx.listError = list.error; } else { ibx.files = list.files; ibx.aiReady = !!list.ai_ready; ibx.listError = ""; }
  ibx.checks = checks;
  ibxPaintList();
  ibxPaintChecks();
  if ((ibx.files || []).some((f) => f.status === "ai_running")) ibxPollStart();
}

// ---------------------------------------------------------------- the list of dropped files
function ibxPaintList() {
  const box = $("ibxList");
  if (!box) return;
  if (ibx.files == null) { fill(box, el("p", { class: "error", text: `โหลดรายการไฟล์ไม่สำเร็จ: ${ibx.listError}` })); return; }
  if (!ibx.files.length) { fill(box, el("p", { class: "muted small", text: "ยังไม่มีไฟล์ — โยนไฟล์ลงกล่องด้านบน" })); return; }
  const month = $("acxMonth").value;
  fill(box, ...ibx.files.map((f) => ibxRow(f, month)));
}

function ibxRow(f, month) {
  const busy = ibx.busy.has(f.id);
  const btn = (text, onclick, cls = "btn small") => {
    const b = el("button", { class: cls, type: "button", text, onclick, "data-ibx-file": f.id });
    if (busy) b.disabled = true;
    return b;
  };
  const sup = ibxSuperseded(f);
  // an AI-read expense paper opens on its own page (inboxcheck.js: each page beside its lines, fixed before ลงบัญชี); others the dialog
  const look = () => (f.check ? goHash(`#accounts-in/check=${f.id}`) : ibxPreview(f.id));
  const askMonth = f.status === "needs_choice" && (f.conflicts || []).some((c) => c && String(c.key || "").split(":")[0] === "month");
  const monthText = f.month ? ibxMonth(f.month) : askMonth ? "เลือกเดือน" : "ไม่ทราบเดือน";
  const other = f.month && f.month !== month;
  const sums = f.file_total != null || f.rows_total != null
    ? el("div", { class: "small" }, f.file_total != null && f.rows_total != null
      ? el("span", { class: ibxSame(f.file_total, f.rows_total) ? "ok-text" : "warn-text",
        text: ibxSame(f.file_total, f.rows_total) ? `✓ ${baht(f.rows_total)} ฿` : `ในไฟล์ ${baht(f.file_total)} · รวมแถว ${baht(f.rows_total)} ฿` })
      : el("span", { text: `${baht(f.rows_total ?? f.file_total)} ฿` })) : null;
  const acts = [];
  if (f.status === "needs_choice" || (f.pending && f.pending.status === "needs_choice")) acts.push(btn(ibxChoiceLabel(f), () => ibxPreview(f.id), "btn small primary"));
  if (["read", "booked", "stored", "needs_choice"].includes(f.status) || (f.batches || []).length) {
    acts.push(btn(f.status === "read" && !sup ? "ดูก่อนลงบัญชี" : "ดูรายการ", look));
  }
  if (f.status === "read" && !sup) acts.push(btn("ลงบัญชี", () => ibxBook(f), "btn small primary"));
  if (f.needs_ai && f.status !== "ai_running") {
    const b = btn(f.pages ? `อ่านด้วย AI (${f.pages} หน้า)` : "อ่านด้วย AI", () => ibxAi(f));
    if (!ibx.aiReady) { b.disabled = true; b.title = "AI ยังไม่พร้อมบนเซิร์ฟเวอร์"; }
    acts.push(b);
  }
  if (f.status !== "ai_running") {
    acts.push(btn("อ่านใหม่", () => ibxReread(f)));
    acts.push(btn("ลบ", () => ibxDelete(f), "btn small danger"));
  }
  const supNote = sup ? `ไฟล์${sup.by ? ` ${sup.by}` : "ใหม่"} ลงบัญชีแทนไฟล์นี้แล้ว — ไฟล์นี้เก็บไว้ ไม่ได้อยู่ในบัญชี` : "";
  return el("div", { class: `ibx-file ${f.status}${sup ? " ibx-superseded" : ""}${busy ? " ibx-busy" : ""}` },
    el("div", { class: "ibx-main" },
      el("button", { class: "linkbtn ibx-name", type: "button", text: f.file_name, title: f.file_name, onclick: look }),
      el("div", { class: "small muted" },
        el("span", { text: f.label || "" }), " · ",
        el("span", { class: f.status === "needs_choice" ? "warn-text" : other ? "ibx-other" : "", text: other ? `งวด ${monthText}` : monthText }),
        (f.branches || []).length ? ` · ${f.branches.join(", ")}` : "",
        f.uploaded_at ? ` · ส่งเมื่อ ${ibxWhen(f.uploaded_at)}${f.uploaded_by ? ` (${f.uploaded_by})` : ""}` : ""),
      f.note ? el("div", { class: "small muted", text: f.note }) : null,
      f.pending ? el("div", { class: "small warn-text", text: `อ่านใหม่แล้ว ${f.pending.message || "ต้องเลือกก่อน"} — รายการเดิมยังอยู่ในบัญชี` }) : null,
      sup ? el("div", { class: "small muted", text: supNote })
        : f.error ? el("div", { class: "small warn-text", text: f.error }) : null,
      f.flags ? el("div", { class: "small warn-text", text: `มีจุดที่ควรตรวจ ${f.flags} จุด — เปิดดูรายการ` }) : null,
      busy ? el("div", { class: "small muted", text: "กำลังทำรายการ… รอสักครู่" }) : null),
    el("div", { class: "ibx-num" }, ibxBadge(f), sums,
      f.status === "booked" && f.booked_total != null ? el("div", { class: "small muted", text: `ลงบัญชี ${baht(f.booked_total)} ฿${f.booked_at ? ` · ${ibxWhen(f.booked_at)}` : ""}` }) : null),
    el("div", { class: "ibx-actions" }, ...acts));
}

function ibxSay(text, bad = false) {
  ibx.msgs = [{ text, bad }];
  ibxPaintMsgs();
}

function ibxPaintMsgs() {
  const box = typeof $ === "function" ? $("ibxMsg") : null;
  if (box) fill(box, ...ibx.msgs.map((m) => el("div", { class: m.bad ? "warn-text" : "", text: m.text })));
}

async function ibxUpload(list) {
  if (!list.length) return;
  const { chunks, skipped } = ibxPlanUpload(list);
  const msgs = skipped.map((file) => ({ text: ibxTooBig(file), bad: true }));
  const total = chunks.reduce((s, c) => s + c.length, 0);
  let sent = 0;
  for (const chunk of chunks) {
    sent += chunk.length;
    ibx.msgs = [{ text: `กำลังส่งและอ่านไฟล์ ${ibxInt(sent)} / ${ibxInt(total)} …` }, ...msgs];
    ibxPaintMsgs();
    const fd = new FormData();
    for (const file of chunk) fd.append("files", file);
    try {
      const r = await api("/api/accounts/inbox", { method: "POST", body: fd });
      for (const f of r.files || []) msgs.push({ text: f.message || `${f.file_name}: ${ibxStatusText(f)}`, bad: f.status === "failed" || f.status === "rejected" || (!f.id && !!f.error) });
    } catch (err) {
      const big = err.status === 413 ? "ไฟล์ใหญ่เกินที่เซิร์ฟเวอร์รับได้" : err.message;
      msgs.push({ text: `ส่งไฟล์ไม่สำเร็จ (${chunk.map((x) => x.name).join(", ")}): ${big}`, bad: true });
    }
  }
  ibx.msgs = msgs;
  if (!total) { ibxPaintMsgs(); return; }
  await loadAccounts();                       // the checklist below changes too ("received")
}

// re-books that could not be done (another file of the chain was busy, needs a choice or failed): the owner must know, or a cost may be counted twice
function ibxRebookFailed(r) {
  return ((r && r.rebook_failed) || []).map((x) => ({ text: `ลงใหม่ไม่สำเร็จ: ${x.file_name || "ไฟล์ที่เกี่ยวข้อง"} — ${x.error || ""} (เปิดไฟล์นั้นแล้วกด อ่านใหม่ / ลงบัญชี)`, bad: true }));
}

async function ibxBook(f) {
  const sup = ibxSuperseded(f);
  if (sup && !confirm(`${f.file_name} ถูกแทนที่ด้วย ${sup.by || "ไฟล์ที่ส่งทีหลัง"} แล้ว\n`
    + `ถ้าลงบัญชีไฟล์นี้อีกครั้ง รายการจากไฟล์นี้จะกลับไปแทนของ${sup.by ? ` ${sup.by}` : "ไฟล์นั้น"} ในบัญชี\nลงบัญชีไฟล์นี้อีกครั้งไหม?`)) return;
  const ran = await ibxRun(f.id, async () => {
    try {
      const r = await api(`/api/accounts/inbox/${f.id}/book`, { method: "POST" });
      ibx.msgs = [{ text: `${f.file_name}: ลงบัญชีแล้ว ${baht(r.booked_total || 0)} ฿${r.rebooked ? ` · ลงใหม่ ${ibxInt(r.rebooked)} ไฟล์ที่เกี่ยวข้องให้ด้วย` : ""}` }, ...ibxRebookFailed(r)];
    } catch (err) { ibx.msgs = [{ text: `${f.file_name}: ${err.message}`, bad: true }]; }
  });
  if (!ran) return;
  const dlg = $("ibxDialog");
  if (dlg && dlg.open) dlg.close();
  await loadAccounts();
}

async function ibxReread(f) {
  const warn = ibxRereadWarning(f);
  if (warn && !confirm(`${f.file_name}
${warn}

อ่านใหม่ไหม?`)) return;
  const ran = await ibxRun(f.id, async () => {
    try {
      const r = await api(`/api/accounts/inbox/${f.id}/reread`, { method: "POST" }) || {};
      const waiting = r.pending && r.pending.status;            // the booking stays; the new read waits for an answer
      ibx.msgs = [{ text: `${f.file_name}: อ่านใหม่แล้ว — ${waiting ? (r.pending.message || "ต้องเลือกก่อน ของเดิมยังอยู่ในบัญชี") : (r.message || ibxStatusText(r))}`, bad: !!waiting }, ...ibxRebookFailed(r)];
    } catch (err) { ibx.msgs = [{ text: `${f.file_name}: ${err.message}`, bad: true }]; }
  });
  if (ran) await loadAccounts();
}

async function ibxDelete(f) {
  const warn = ibxDeleteWarning(f);
  if (!confirm(`ลบไฟล์ ${f.file_name}?${warn.length ? `\n\n${warn.join("\n\n")}` : ""}`)) return;
  const ran = await ibxRun(f.id, async () => {
    try {
      const r = await api(`/api/accounts/inbox/${f.id}`, { method: "DELETE" }) || {};
      ibx.msgs = [{ text: `ลบ ${f.file_name} แล้ว${r.rebooked ? ` · ลงใหม่ ${ibxInt(r.rebooked)} ไฟล์ที่เกี่ยวข้องให้ด้วย` : ""}` }, ...ibxRebookFailed(r)];
    }
    catch (err) { ibx.msgs = [{ text: `${f.file_name}: ${err.message}`, bad: true }]; }
  });
  if (!ran) return;
  const dlg = $("ibxDialog");
  if (dlg && dlg.open) dlg.close();
  await loadAccounts();
}

// ---------------------------------------------------------------- อ่านด้วย AI (phase 2): ask first, then poll every 3 s
async function ibxAi(f) {
  const pages = f.pages ? `${f.pages} หน้า` : "ไฟล์นี้";
  if (!confirm(`ให้ AI อ่าน ${f.file_name} (${pages})?\nอ่านทีละหน้า ใช้เวลาสักครู่ ระบบบันทึกจำนวน token ที่ใช้ไว้ · อ่านเสร็จแล้วยังไม่ลงบัญชีจนกว่าจะกด ลงบัญชี`)) return;
  let ok = false;
  const ran = await ibxRun(f.id, async () => {
    try { await api(`/api/accounts/inbox/${f.id}/ai`, { method: "POST" }); ok = true; ibxSay(`${f.file_name}: AI เริ่มอ่านแล้ว — หน้านี้จะอัปเดตเองเมื่ออ่านเสร็จ`); }
    catch (err) { ibxSay(`${f.file_name}: ${err.message}`, true); }
  });
  if (!ran || !ok) return;
  ibx.running.set(f.id, f.file_name);
  await ibxLoad();
  ibxPollStart();
}

function ibxPollStart() {
  for (const f of ibx.files || []) if (f.status === "ai_running") ibx.running.set(f.id, f.file_name);
  if (ibx.poll) return;
  ibx.poll = setInterval(async () => {
    if (acx.mode !== "in" || !$("ibxList") || document.hidden) {
      if (!$("ibxList") || acx.mode !== "in") { clearInterval(ibx.poll); ibx.poll = null; }
      return;
    }
    let r;
    try { r = await api(`/api/accounts/inbox?month=${encodeURIComponent($("acxMonth").value)}`); } catch (_) { return; }
    ibx.files = r.files || [];
    ibx.aiReady = !!r.ai_ready;
    for (const f of ibx.files) if (f.status === "ai_running") ibx.running.set(f.id, f.file_name);
    if (!ibx.files.some((f) => f.status === "ai_running")) {
      clearInterval(ibx.poll); ibx.poll = null;
      ibx.msgs = ibxPollReport(ibx.running, ibx.files);
      ibx.running.clear();
      await loadAccounts();
      return;
    }
    ibxPaintList();
  }, 3000);
}

// ---------------------------------------------------------------- the preview dialog (ดูก่อนลงบัญชี)
async function ibxPreview(id) {
  let dlg = $("ibxDialog");
  if (!dlg) { dlg = el("dialog", { id: "ibxDialog", class: "ibx-dialog" }); document.body.append(dlg); }
  fill(dlg, el("p", { class: "muted", text: "กำลังโหลด…" }));
  if (!dlg.open) dlg.showModal();
  let f;
  try { f = await api(`/api/accounts/inbox/${id}`); }
  catch (err) { fill(dlg, el("p", { class: "error", text: err.message }), el("button", { class: "btn", type: "button", text: "ปิด", onclick: () => dlg.close() })); return; }
  const p = f.preview || {};
  const roles = f.roles || {};
  const err = el("p", { class: "error", hidden: true });
  const fileLink = el("a", { class: "btn small", href: `/api/accounts/inbox/${f.id}/file?token=${encodeURIComponent(state.token)}`, target: "_blank", rel: "noopener", text: "เปิดไฟล์" });
  const toCheck = f.check ? el("button", { class: "btn small primary", type: "button", text: "ตรวจ / แก้รายการ (ทั้งหน้า)",
    onclick: () => { dlg.close(); goHash(`#accounts-in/check=${f.id}`); } }) : null;
  const head = el("div", { class: "acx-rv-head" }, el("h3", { text: f.file_name }), ibxBadge(f), toCheck, fileLink,
    el("button", { class: "btn small", type: "button", text: "ปิด", onclick: () => dlg.close() }));
  const sub = el("p", { class: "muted small", text: [f.label, f.month ? `งวด ${ibxMonth(f.month)}` : null, (f.branches || []).join(", ") || null].filter(Boolean).join(" · ") });

  // where the month / branch came from: every independent signal the reader saw
  const sig = (what, title) => {
    const xs = (p.signals || []).filter((s) => s.what === what);
    return xs.length ? el("div", { class: "small ibx-signals" }, el("strong", { text: `${title}: ` }),
      xs.map((s) => `${s.source} ${what === "month" ? ibxMonth(s.value) || s.value : s.value}`).join(" · ")) : null;
  };
  const busy = ibx.busy.has(f.id);
  const asking = (f.pending_preview && (f.pending_preview.conflicts || []).length) ? f.pending_preview : p;
  const conflicts = (asking.conflicts || []).map((c) => {
    const name = `ibxc_${c.key}`;
    const chosen = (f.choices || {})[c.key] || c.default;
    const opts = c.options || [];
    // a month the file never named (e.g. a cheque sheet with no date): pick any of the last company months
    const pick = !opts.length && c.key === "month" ? el("select", { class: "ibx-monthpick", "aria-label": "เลือกเดือน" },
      el("option", { value: "", text: "— เลือกเดือน —" }),
      ...ibxMonthChoices().map((m) => {
        const o = el("option", { value: m, text: typeof acxMonthLabel === "function" ? acxMonthLabel(m) : ibxMonth(m) });
        o.selected = m === chosen;
        return o;
      })) : null;
    return el("fieldset", { class: "ibx-conflict", "data-key": c.key },
      el("legend", { class: "warn-text", text: c.message || `ข้อมูลในไฟล์ไม่ตรงกัน — ${ibxConflictWord(c.key)}` }),
      ...opts.map((o) => {
        const r = el("input", { type: "radio", name, value: o.value });
        r.checked = String(o.value) === String(chosen);
        return el("label", { class: "ibx-radio" }, r, el("span", { text: o.label || o.value }));
      }), pick,
      !opts.length && !pick ? el("p", { class: "small muted", text: "ไม่มีตัวเลือกให้เลือก — ลองกด อ่านใหม่ หรือเปิดไฟล์ดู" }) : null);
  });
  const chooseBtn = conflicts.length ? el("button", { class: "btn primary", type: "button", "data-ibx-file": f.id, text: `ใช้ตัวเลือกนี้ แล้วอ่านใหม่ (${ibxChoiceLabel(p)})`, onclick: async () => {
    const choices = {};
    for (const fs of conflicts) {
      const r = fs.querySelector("input:checked"), s = fs.querySelector("select");
      if (r) choices[fs.dataset.key] = r.value;
      else if (s && s.value) choices[fs.dataset.key] = s.value;
    }
    if (!Object.keys(choices).length) { err.textContent = "ยังไม่ได้เลือก — เลือกก่อนแล้วกดอีกครั้ง"; err.hidden = false; return; }
    err.hidden = true;
    let ok = false;
    const ran = await ibxRun(f.id, async () => {
      try { const r = await api(`/api/accounts/inbox/${f.id}/choose`, jsonOpts("POST", { choices })) || {}; ok = true; ibx.msgs = ibxRebookFailed(r); ibxPaintMsgs(); }
      catch (e) { err.textContent = e.message; err.hidden = false; }
    });
    if (ran && ok) { await ibxPreview(f.id); loadAccounts(); }
  } }) : null;
  const flags = (p.flags || []).length ? el("ul", { class: "ibx-flags small" }, ...p.flags.map((x) => el("li", { text: x }))) : null;
  const usage = p.ai_usage || f.ai_usage;
  const ai = usage ? el("p", { class: "muted small", text: `อ่านด้วย AI${usage.model ? ` (${usage.model})` : ""} · ${ibxInt(usage.calls || 0)} ครั้ง · token เข้า ${ibxInt(usage.input_tokens || 0)} · ออก ${ibxInt(usage.output_tokens || 0)}` }) : null;
  const sup = ibxSuperseded(f);
  const bookBtn = f.status === "read" && !sup ? el("button", { class: "btn primary", type: "button", "data-ibx-file": f.id, text: "ลงบัญชี", onclick: () => ibxBook(f) })
    : sup && (p.batches || []).length ? el("button", { class: "btn", type: "button", "data-ibx-file": f.id, text: "ลงบัญชีไฟล์นี้อีกครั้ง", onclick: () => ibxBook(f) }) : null;
  if (busy) for (const b of [chooseBtn, bookBtn]) if (b) b.disabled = true;
  fill(dlg, head, sub,
    sig("month", "เดือนมาจาก"), sig("branch", "สาขามาจาก"),
    p.note ? el("p", { class: "small", text: p.note }) : null,
    f.pending ? el("p", { class: "small warn-text", text: `อ่านใหม่แล้ว ${f.pending.message || "ต้องเลือกก่อน"} — รายการเดิมของไฟล์นี้ยังอยู่ในบัญชี` }) : null,
    sup ? el("p", { class: "small warn-text", text: `ไฟล์นี้ถูกแทนที่ด้วย ${sup.by || "ไฟล์ที่ส่งทีหลัง"} — รายการของไฟล์นั้นอยู่ในบัญชีแทน ไฟล์นี้เก็บไว้เฉย ๆ` })
      : f.error ? el("p", { class: "small warn-text", text: f.error }) : null,
    ...conflicts, chooseBtn, flags, ai,
    ...(p.batches || []).map((b) => ibxBatch(b, roles, (f.batches || []).find((x) => x.key === b.key), f.status === "booked")),
    !(p.batches || []).length && !conflicts.length ? el("p", { class: "muted small", text: p.store_only ? "ไฟล์นี้เก็บไว้กับเดือน ไม่ลงบัญชี" : "ไม่มีรายการที่จะลงบัญชี" }) : null,
    err, el("div", { class: "dialog-actions" }, el("span", { class: "grow" }), el("button", { class: "btn", type: "button", text: "ปิด", onclick: () => dlg.close() }), bookBtn));
}

function ibxRoleBadge(role, roles) {
  const r = role || "unknown";
  return el("span", { class: `ibx-role ${IBX_NONCOST.has(r) ? "noncost" : r}`, title: roles[r] || r },
    (roles[r] || r).replace(/\s*\(.*\)\s*$/, ""), IBX_NONCOST.has(r) ? el("em", { text: "ไม่ใช่ค่าใช้จ่าย" }) : null);
}

function ibxBatch(b, roles, view, fileBooked = false) {
  const rows = b.rows || [];
  const key = b.key;
  const all = ibx.open.has(key);
  const shown = all ? rows : rows.slice(0, IBX_ROW_CAP);
  const total = el("div", { class: "small" }, b.file_total != null
    ? el("span", { class: ibxSame(b.file_total, b.rows_total) ? "ok-text" : "warn-text",
      text: ibxSame(b.file_total, b.rows_total) ? `✓ ยอดในไฟล์ ${ibxMoney(b.file_total)} = รวมแถว ${ibxMoney(b.rows_total)} ฿`
        : `ยอดในไฟล์ ${ibxMoney(b.file_total)} · รวมแถว ${ibxMoney(b.rows_total)} ฿ · ต่าง ${ibxMoney(Math.abs((b.file_total || 0) - (b.rows_total || 0)))} ฿` })
    : el("span", { class: "muted", text: `ไฟล์ไม่มียอดรวม · รวมแถว ${ibxMoney(b.rows_total)} ฿` }));
  // per role: so it is plain which part is a cost and which only pays / moves money
  const byRole = {};
  for (const r of rows) byRole[r.role || "unknown"] = (byRole[r.role || "unknown"] || 0) + (Number(r.amount) || 0);
  const roleSum = el("div", { class: "ibx-rolesum" }, ...Object.entries(byRole).map(([r, v]) => el("span", {}, ibxRoleBadge(r, roles), ` ${fmtMoney(v)} ฿`)));
  const table = el("div", { class: "ibx-rows" },
    el("div", { class: "ibx-r head small muted" }, ...["วันที่", "รายการ", "หน้าที่", "บัญชี", "บาท"].map((t) => el("span", { text: t }))),
    ...shown.map((r) => el("div", { class: `ibx-r${r.flag ? " flagged" : ""}` },
      el("span", { text: r.date ? beDate(r.date) : "" }),
      el("span", { class: "ibx-text" }, r.text || "", r.party ? el("span", { class: "muted", text: ` · ${r.party}` }) : null,
        r.dest && r.dest_kind && r.dest_kind !== "book" ? el("div", { class: "small muted", text: `→ ${r.dest}` }) : null,
        r.flag ? el("div", { class: "small warn-text", text: r.flag }) : null),
      ibxRoleBadge(r.role, roles),
      el("span", { class: "muted", text: r.account || "" }),
      el("strong", { text: ibxMoney(r.amount) }))));
  const more = rows.length > IBX_ROW_CAP ? el("button", { class: "linkbtn small", type: "button", text: all ? "แสดงน้อยลง" : `แสดงทั้งหมด (${rows.length} แถว)`,
    onclick: () => { all ? ibx.open.delete(key) : ibx.open.add(key); const fresh = ibxBatch(b, roles, view, fileBooked); box.replaceWith(fresh); } }) : null;
  const box = el("div", { class: "ibx-batch" },
    el("div", { class: "ibx-batch-head" }, el("strong", { text: b.title || b.key }),
      view && view.booked ? el("span", { class: "badge ok", text: "ลงบัญชีแล้ว" })
        : view && fileBooked ? el("span", { class: "badge", text: "ไฟล์ใหม่ลงแทนแล้ว" }) : null),
    el("div", { class: "small muted", text: [b.branch, b.month ? `งวด ${ibxMonth(b.month)}` : null, `${rows.length} แถว`].filter(Boolean).join(" · ") }),
    total, roleSum, table, more);
  return box;
}

// ---------------------------------------------------------------- ตรวจความถูกต้อง (app/acc_checks.py)
function ibxPaintChecks() {
  const box = $("ibxChecks");
  if (!box) return;
  const c = ibx.checks || {};
  if (c.error) { fill(box, el("p", { class: "error", text: `ตรวจไม่สำเร็จ: ${c.error}` })); return; }
  if (!c.available) { fill(box, el("p", { class: "muted small", text: c.note || "ชุดตรวจความถูกต้องยังไม่พร้อม" })); return; }
  if (!(c.checks || []).length) { fill(box, el("p", { class: "muted small", text: "ยังไม่มีอะไรให้ตรวจในเดือนนี้" })); return; }
  fill(box, c.note ? el("p", { class: "muted small", text: c.note }) : null, ...c.checks.map(ibxCheck));
}

// a check's numbers as [{label, text}]: the list shape [{label, value, kind}] as it is; the old {key: value} shape keeps only the
// number values it has a Thai name for (an object, an array, a string or an unknown English key is left out)
function ibxCheckNumbers(numbers) {
  const fmt = (v, kind) => (kind === "count" ? ibxInt(v) : kind === "pct" ? `${ibxInt(v, 2)}%` : `${fmtMoney(v)} ฿`);
  const ok = (v) => typeof v === "number" && Number.isFinite(v);
  if (Array.isArray(numbers)) {
    return numbers.filter((n) => n && typeof n === "object" && ok(n.value) && n.label)
      .map((n) => ({ label: String(n.label), text: fmt(n.value, n.kind || "money") }));
  }
  if (numbers && typeof numbers === "object") {
    return Object.entries(numbers).filter(([k, v]) => ok(v) && IBX_NUM_OLD[k])
      .map(([k, v]) => ({ label: IBX_NUM_OLD[k][0], text: fmt(v, IBX_NUM_OLD[k][1]) }));
  }
  return [];
}

function ibxCheck(k) {
  const tone = IBX_STATE[k.state] || "none";
  const list = ibxCheckNumbers(k.numbers);
  const nums = list.length ? el("div", { class: "ibx-nums small" },
    ...list.map((n) => el("span", { class: "ibx-n" }, `${n.label} `, el("b", { text: n.text })))) : null;
  const rows = Array.isArray(k.rows) ? k.rows : [];
  const body = rows.length ? el("details", { class: "ibx-check-rows" }, el("summary", { class: "small", text: `ดูรายการ (${ibxInt(rows.length)})` }), ibxTable(rows)) : null;
  return el("div", { class: `ibx-check ${tone}` },
    el("span", { class: "ibx-dot", "aria-hidden": "true" }),
    el("div", {}, el("strong", { text: k.title || "การตรวจ" }), k.summary ? el("div", { class: "small", text: k.summary }) : null, nums, body));
}

// a check's rows: always the same Thai columns (date, item, from, baht), whatever else a row carries
function ibxCheckCells(r) {
  if (r == null || typeof r !== "object") return [{ text: r == null ? "" : String(r) }];
  const amt = typeof r.amount === "number" && Number.isFinite(r.amount) ? fmtMoney(r.amount) : "";
  const text = typeof r.text === "string" ? r.text : r.text == null ? "" : String(r.text);
  const src = typeof r.source === "string" ? r.source : "";
  return [{ text: r.date && /^\d{4}-\d{2}-\d{2}/.test(String(r.date)) ? beDate(String(r.date)) : "" }, { text }, { text: src }, { text: amt, cls: "ibx-amt" }];
}

function ibxTable(rows) {
  if (typeof rows[0] !== "object" || rows[0] == null) return el("ul", { class: "small" }, ...rows.map((r) => el("li", { text: String(r) })));
  return el("div", { class: "ibx-table" }, el("table", { class: "small" },
    el("thead", {}, el("tr", {}, ...["วันที่", "รายการ", "ที่มา", "บาท"].map((c) => el("th", { text: c })))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...ibxCheckCells(r).map((c) => el("td", { class: c.cls || null, text: c.text })))))));
}
