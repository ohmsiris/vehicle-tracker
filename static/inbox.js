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

const ibx = { files: [], checks: null, aiReady: false, msgs: [], poll: null, seq: 0, open: new Set() };
const IBX_STATUS = {
  read: ["อ่านแล้ว รอลงบัญชี", "warn"], booked: ["ลงบัญชีแล้ว", "ok"], needs_choice: ["ต้องเลือกเดือน", "urgent"],
  stored: ["เก็บไว้ ไม่ลงบัญชี", ""], needs_ai: ["อ่านด้วย AI ได้", "shop"], ai_running: ["AI กำลังอ่าน...", "shop"], failed: ["อ่านไม่สำเร็จ", "urgent"],
};
const IBX_NONCOST = new Set(["funding", "pays_bill", "advance", "transfer", "owner", "info"]);
const IBX_CHUNK = 4;                         // files per request
const IBX_ROW_CAP = 200;                     // rows drawn per batch before "แสดงทั้งหมด"
const IBX_STATE = { ok: "ok", pass: "ok", green: "ok", done: "ok", warn: "warn", amber: "warn", check: "warn", bad: "bad", red: "bad", fail: "bad", error: "bad" };

const ibxMonth = (m) => (m && /^\d{4}-\d{2}$/.test(m) ? beMonth(m) : "");
const ibxMoney = (n) => (n == null ? "–" : fmtMoney(n));
const ibxSame = (a, b) => a != null && b != null && Math.abs(a - b) <= 1;

function ibxStatusText(f) {
  if (f.status === "needs_ai") return f.pages ? `อ่านด้วย AI ได้ (${f.pages} หน้า)` : "อ่านด้วย AI ได้";
  if (f.status === "ai_running" && f.progress && f.progress.total) return `AI กำลังอ่าน... (${f.progress.done}/${f.progress.total} หน้า)`;
  return (IBX_STATUS[f.status] || [f.status_label || f.status])[0];
}

function ibxBadge(f) {
  return el("span", { class: `badge ${(IBX_STATUS[f.status] || ["", ""])[1]}`.trim(), text: ibxStatusText(f) });
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
  const btn = (text, onclick, cls = "btn small") => el("button", { class: cls, type: "button", text, onclick });
  const monthText = f.month ? ibxMonth(f.month) : f.status === "needs_choice" ? "เลือกเดือน" : "ไม่ทราบเดือน";
  const other = f.month && f.month !== month;
  const sums = f.file_total != null || f.rows_total != null
    ? el("div", { class: "small" }, f.file_total != null && f.rows_total != null
      ? el("span", { class: ibxSame(f.file_total, f.rows_total) ? "ok-text" : "warn-text",
        text: ibxSame(f.file_total, f.rows_total) ? `✓ ${baht(f.rows_total)} ฿` : `ในไฟล์ ${baht(f.file_total)} · รวมแถว ${baht(f.rows_total)} ฿` })
      : el("span", { text: `${baht(f.rows_total ?? f.file_total)} ฿` })) : null;
  const acts = [];
  if (f.status === "needs_choice") acts.push(btn("เลือกเดือน", () => ibxPreview(f.id), "btn small primary"));
  if (["read", "booked", "stored", "needs_choice"].includes(f.status) || (f.batches || []).length) {
    acts.push(btn(f.status === "read" ? "ดูก่อนลงบัญชี" : "ดูรายการ", () => ibxPreview(f.id)));
  }
  if (f.status === "read") acts.push(btn("ลงบัญชี", () => ibxBook(f), "btn small primary"));
  if (f.needs_ai && f.status !== "ai_running") {
    const b = btn(f.pages ? `อ่านด้วย AI (${f.pages} หน้า)` : "อ่านด้วย AI", () => ibxAi(f));
    if (!ibx.aiReady) { b.disabled = true; b.title = "AI ยังไม่พร้อมบนเซิร์ฟเวอร์"; }
    acts.push(b);
  }
  if (f.status !== "ai_running") {
    acts.push(btn("อ่านใหม่", () => ibxReread(f)));
    acts.push(btn("ลบ", () => ibxDelete(f), "btn small danger"));
  }
  return el("div", { class: `ibx-file ${f.status}` },
    el("div", { class: "ibx-main" },
      el("button", { class: "linkbtn ibx-name", type: "button", text: f.file_name, title: f.file_name, onclick: () => ibxPreview(f.id) }),
      el("div", { class: "small muted" },
        el("span", { text: f.label || "" }), " · ",
        el("span", { class: f.status === "needs_choice" ? "warn-text" : other ? "ibx-other" : "", text: other ? `งวด ${monthText}` : monthText }),
        (f.branches || []).length ? ` · ${f.branches.join(", ")}` : "",
        f.uploaded_at ? ` · ส่งเมื่อ ${beDate(f.uploaded_at)}${f.uploaded_by ? ` (${f.uploaded_by})` : ""}` : ""),
      f.note ? el("div", { class: "small muted", text: f.note }) : null,
      f.error ? el("div", { class: "small warn-text", text: f.error }) : null,
      f.flags ? el("div", { class: "small warn-text", text: `มีจุดที่ควรตรวจ ${f.flags} จุด — เปิดดูรายการ` }) : null),
    el("div", { class: "ibx-num" }, ibxBadge(f), sums,
      f.status === "booked" && f.booked_total != null ? el("div", { class: "small muted", text: `ลงบัญชี ${baht(f.booked_total)} ฿${f.booked_at ? ` · ${beDate(f.booked_at)}` : ""}` }) : null),
    el("div", { class: "ibx-actions" }, ...acts));
}

function ibxSay(text, bad = false) {
  ibx.msgs = [{ text, bad }];
  const box = $("ibxMsg");
  if (box) fill(box, el("div", { class: bad ? "warn-text" : "", text }));
}

async function ibxUpload(list) {
  if (!list.length) return;
  const msgs = [];
  for (let i = 0; i < list.length; i += IBX_CHUNK) {
    ibxSay(`กำลังส่งและอ่านไฟล์ ${Math.min(i + IBX_CHUNK, list.length)} / ${list.length} …`);
    const fd = new FormData();
    for (const file of list.slice(i, i + IBX_CHUNK)) fd.append("files", file);
    try {
      const r = await api("/api/accounts/inbox", { method: "POST", body: fd });
      for (const f of r.files) msgs.push({ text: f.message || `${f.file_name}: ${ibxStatusText(f)}`, bad: f.status === "failed" });
    } catch (err) {
      msgs.push({ text: `ส่งไฟล์ไม่สำเร็จ (${list.slice(i, i + IBX_CHUNK).map((x) => x.name).join(", ")}): ${err.message}`, bad: true });
    }
  }
  ibx.msgs = msgs;
  await loadAccounts();                       // the checklist below changes too ("received")
}

async function ibxBook(f) {
  try {
    const r = await api(`/api/accounts/inbox/${f.id}/book`, { method: "POST" });
    ibx.msgs = [{ text: `${f.file_name}: ลงบัญชีแล้ว ${baht(r.booked_total || 0)} ฿${r.rebooked ? ` · ลงใหม่ ${r.rebooked} ไฟล์ที่เกี่ยวข้องให้ด้วย` : ""}` }];
  } catch (err) { ibx.msgs = [{ text: `${f.file_name}: ${err.message}`, bad: true }]; }
  const dlg = $("ibxDialog");
  if (dlg && dlg.open) dlg.close();
  await loadAccounts();
}

async function ibxReread(f) {
  try {
    const r = await api(`/api/accounts/inbox/${f.id}/reread`, { method: "POST" });
    ibx.msgs = [{ text: `${f.file_name}: อ่านใหม่แล้ว — ${ibxStatusText(r)}` }];
  } catch (err) { ibx.msgs = [{ text: `${f.file_name}: ${err.message}`, bad: true }]; }
  await loadAccounts();
}

async function ibxDelete(f) {
  const booked = f.status === "booked" ? "\nรายการที่ลงบัญชีจากไฟล์นี้จะถูกลบออกจากบัญชีด้วย" : "";
  if (!confirm(`ลบไฟล์ ${f.file_name}?${booked}`)) return;
  try { await api(`/api/accounts/inbox/${f.id}`, { method: "DELETE" }); ibx.msgs = [{ text: `ลบ ${f.file_name} แล้ว` }]; }
  catch (err) { ibx.msgs = [{ text: `${f.file_name}: ${err.message}`, bad: true }]; }
  await loadAccounts();
}

// ---------------------------------------------------------------- อ่านด้วย AI (phase 2): ask first, then poll every 3 s
async function ibxAi(f) {
  const pages = f.pages ? `${f.pages} หน้า` : "ไฟล์นี้";
  if (!confirm(`ให้ AI อ่าน ${f.file_name} (${pages})?\nอ่านทีละหน้า ใช้เวลาสักครู่ ระบบบันทึกจำนวน token ที่ใช้ไว้ · อ่านเสร็จแล้วยังไม่ลงบัญชีจนกว่าจะกด ลงบัญชี`)) return;
  try { await api(`/api/accounts/inbox/${f.id}/ai`, { method: "POST" }); ibxSay(`${f.file_name}: AI เริ่มอ่านแล้ว — หน้านี้จะอัปเดตเองเมื่ออ่านเสร็จ`); }
  catch (err) { ibxSay(`${f.file_name}: ${err.message}`, true); return; }
  await ibxLoad();
  ibxPollStart();
}

function ibxPollStart() {
  if (ibx.poll) return;
  ibx.poll = setInterval(async () => {
    if (acx.mode !== "in" || !$("ibxList") || document.hidden) {
      if (!$("ibxList") || acx.mode !== "in") { clearInterval(ibx.poll); ibx.poll = null; }
      return;
    }
    let r;
    try { r = await api(`/api/accounts/inbox?month=${encodeURIComponent($("acxMonth").value)}`); } catch (_) { return; }
    ibx.files = r.files;
    ibx.aiReady = !!r.ai_ready;
    if (!r.files.some((f) => f.status === "ai_running")) {
      clearInterval(ibx.poll); ibx.poll = null;
      ibx.msgs = [{ text: "AI อ่านเสร็จแล้ว — เปิดดูก่อนลงบัญชี" }];
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
  const head = el("div", { class: "acx-rv-head" }, el("h3", { text: f.file_name }), ibxBadge(f), fileLink,
    el("button", { class: "btn small", type: "button", text: "ปิด", onclick: () => dlg.close() }));
  const sub = el("p", { class: "muted small", text: [f.label, f.month ? `งวด ${ibxMonth(f.month)}` : null, (f.branches || []).join(", ") || null].filter(Boolean).join(" · ") });

  // where the month / branch came from: every independent signal the reader saw
  const sig = (what, title) => {
    const xs = (p.signals || []).filter((s) => s.what === what);
    return xs.length ? el("div", { class: "small ibx-signals" }, el("strong", { text: `${title}: ` }),
      xs.map((s) => `${s.source} ${what === "month" ? ibxMonth(s.value) || s.value : s.value}`).join(" · ")) : null;
  };
  const conflicts = (p.conflicts || []).map((c) => {
    const name = `ibxc_${c.key}`;
    const chosen = (f.choices || {})[c.key] || c.default;
    return el("fieldset", { class: "ibx-conflict", "data-key": c.key },
      el("legend", { class: "warn-text", text: c.message || "ข้อมูลในไฟล์ไม่ตรงกัน — เลือก" }),
      ...(c.options || []).map((o) => {
        const r = el("input", { type: "radio", name, value: o.value });
        r.checked = String(o.value) === String(chosen);
        return el("label", { class: "ibx-radio" }, r, el("span", { text: o.label || o.value }));
      }));
  });
  const chooseBtn = conflicts.length ? el("button", { class: "btn primary", type: "button", text: "ใช้ตัวเลือกนี้ แล้วอ่านใหม่", onclick: async () => {
    const choices = {};
    for (const fs of conflicts) { const r = fs.querySelector("input:checked"); if (r) choices[fs.dataset.key] = r.value; }
    chooseBtn.disabled = true; err.hidden = true;
    try { await api(`/api/accounts/inbox/${f.id}/choose`, jsonOpts("POST", { choices })); await ibxPreview(f.id); loadAccounts(); }
    catch (e) { err.textContent = e.message; err.hidden = false; chooseBtn.disabled = false; }
  } }) : null;
  const flags = (p.flags || []).length ? el("ul", { class: "ibx-flags small" }, ...p.flags.map((x) => el("li", { text: x }))) : null;
  const usage = p.ai_usage || f.ai_usage;
  const ai = usage ? el("p", { class: "muted small", text: `อ่านด้วย AI${usage.model ? ` (${usage.model})` : ""} · ${usage.calls || 0} ครั้ง · token เข้า ${elN(usage.input_tokens || 0)} · ออก ${elN(usage.output_tokens || 0)}` }) : null;
  const bookBtn = f.status === "read" ? el("button", { class: "btn primary", type: "button", text: "ลงบัญชี", onclick: () => ibxBook(f) }) : null;
  fill(dlg, head, sub,
    sig("month", "เดือนมาจาก"), sig("branch", "สาขามาจาก"),
    p.note ? el("p", { class: "small", text: p.note }) : null,
    f.error ? el("p", { class: "small warn-text", text: f.error }) : null,
    ...conflicts, chooseBtn, flags, ai,
    ...(p.batches || []).map((b) => ibxBatch(b, roles, (f.batches || []).find((x) => x.key === b.key))),
    !(p.batches || []).length && !conflicts.length ? el("p", { class: "muted small", text: p.store_only ? "ไฟล์นี้เก็บไว้กับเดือน ไม่ลงบัญชี" : "ไม่มีรายการที่จะลงบัญชี" }) : null,
    err, el("div", { class: "dialog-actions" }, el("span", { class: "grow" }), el("button", { class: "btn", type: "button", text: "ปิด", onclick: () => dlg.close() }), bookBtn));
}

function ibxRoleBadge(role, roles) {
  const r = role || "unknown";
  return el("span", { class: `ibx-role ${IBX_NONCOST.has(r) ? "noncost" : r}`, title: roles[r] || r },
    (roles[r] || r).replace(/\s*\(.*\)\s*$/, ""), IBX_NONCOST.has(r) ? el("em", { text: "ไม่ใช่ค่าใช้จ่าย" }) : null);
}

function ibxBatch(b, roles, view) {
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
        r.flag ? el("div", { class: "small warn-text", text: r.flag }) : null),
      ibxRoleBadge(r.role, roles),
      el("span", { class: "muted", text: r.account || "" }),
      el("strong", { text: ibxMoney(r.amount) }))));
  const more = rows.length > IBX_ROW_CAP ? el("button", { class: "linkbtn small", type: "button", text: all ? "แสดงน้อยลง" : `แสดงทั้งหมด (${rows.length} แถว)`,
    onclick: () => { all ? ibx.open.delete(key) : ibx.open.add(key); const fresh = ibxBatch(b, roles, view); box.replaceWith(fresh); } }) : null;
  const box = el("div", { class: "ibx-batch" },
    el("div", { class: "ibx-batch-head" }, el("strong", { text: b.title || b.key }),
      view && view.booked ? el("span", { class: "badge ok", text: "ลงบัญชีแล้ว" }) : null),
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

function ibxCheck(k) {
  const tone = IBX_STATE[k.state] || "none";
  const nums = k.numbers && typeof k.numbers === "object" && !Array.isArray(k.numbers)
    ? el("div", { class: "ibx-nums small" }, ...Object.entries(k.numbers).map(([n, v]) => el("span", { class: "acx-file", text: `${n}: ${typeof v === "number" ? fmtMoney(v) : v}` }))) : null;
  const rows = k.rows || [];
  const body = rows.length ? el("details", { class: "ibx-check-rows" }, el("summary", { class: "small", text: `ดูรายการ (${rows.length})` }), ibxTable(rows)) : null;
  return el("div", { class: `ibx-check ${tone}` },
    el("span", { class: "ibx-dot", "aria-hidden": "true" }),
    el("div", {}, el("strong", { text: k.title || k.key }), k.summary ? el("div", { class: "small", text: k.summary }) : null, nums, body));
}

function ibxTable(rows) {
  if (typeof rows[0] !== "object" || rows[0] == null) return el("ul", { class: "small" }, ...rows.map((r) => el("li", { text: String(r) })));
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const cell = (v) => (typeof v === "number" ? fmtMoney(v) : /^\d{4}-\d{2}-\d{2}/.test(String(v || "")) ? beDate(v) : v == null ? "" : String(v));
  return el("div", { class: "ibx-table" }, el("table", { class: "small" },
    el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", { text: c })))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...cols.map((c) => el("td", { text: cell(r[c]) })))))));
}
