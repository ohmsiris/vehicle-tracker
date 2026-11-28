"use strict";

// บัญชี › ลงข้อมูล › ตรวจเอกสารก่อนลงบัญชี (#accounts-in/check=<file id>; owner, 28 Nov 2026): an AI-read mid / end-of-month expense
// paper checked BEFORE ลงบัญชี on a whole page instead of the small dialog -- "checking after ลงบัญชี is a mess":
//   * each page's scan beside the lines read from it (the picture stays in view while the lines scroll; click it to zoom);
//   * fixed straight away: วันที่, รายการ (names too), บาท, บัญชี, รถ; ลบ a misread line (เอาคืน brings it back); + เพิ่มบรรทัด a missed one;
//   * ไปที่, a column of its own: where every line will go (an account and truck, already on the cash-fuel page, a note, deleted).
//     Lines that are not booked are greyed and grouped at the bottom of their page, the reason in ไปที่ -- never glued onto the text;
//   * บันทึกในพนักงาน (app/inbox_hr.py): HR pages and staff loans recorded straight away, each item by its own buttons (bottom of file);
//   * บันทึกการแก้ (Ctrl+S) sends every fix at once (POST /api/accounts/inbox/<id>/edits, app/inbox.save_edits: the preview is rebuilt
//     with them, a booked file is booked again with them); ลงบัญชี books exactly what is left, and waits while a fix is not saved.
// Every other file keeps the dialog (inbox.js ibxPreview). Loaded after inbox.js (ibxBadge, ibxRun, ibxSuperseded ... at call time).

const ick = { id: null, f: null, page: null, saved: null, draft: null, version: null, msg: null, seq: 0, wired: false, editable: false, ro: null };
const ICK_MONEY = new Set(["expense", "fuel", "advance"]);        // pages whose lines are booked (acc_docs.build)
const ICK_ADDABLE = new Set(["expense", "fuel"]);                 // pages a missed line can be added to (as the AI read them)
const ICK_LINE = new Set(["book", "cashfuel", "deleted"]);        // rows that are lines of a page (noted rows are about a page)
const ICK_FIELDS = ["date", "text", "amount", "account", "vehicle_id"];
const ICK_UNSURE = /\s*\(อ่านไม่ชัด\)/g;
const ICK_DEST_NAME = { 1500: "เงินเบิกล่วงหน้า", 1510: "เงินกู้พนักงาน" };
const ICK_PARTS = { expense: "รายการเบิก", fuel: "น้ำมัน", advance: "เบิกล่วงหน้า" };
const ICK_LEAVE = "มีการแก้ที่ยังไม่ได้บันทึก — ออกจากหน้านี้โดยไม่บันทึกไหม?";
const ICK_TRUCK_ACC = ["6100", "6110", "6120", "6130"];

// ---------------------------------------------------------------- pure helpers (tests/test_inbox_check_page.py runs them in Node)
const ickNum = (v) => { const n = parseFloat(String(v ?? "").replace(/,/g, "")); return Number.isFinite(n) ? n : NaN; };
const ickEmpty = () => ({ edits: {}, added: [] });

// a copy of preview.line_edits the page can change freely
function ickCopy(le) {
  const src = le && typeof le === "object" ? le : {};
  const edits = {};
  for (const [k, v] of Object.entries(src.edits || {})) if (v && typeof v === "object") edits[k] = { ...v };
  return { edits, added: (Array.isArray(src.added) ? src.added : []).filter((a) => a && typeof a === "object").map((a) => ({ ...a })) };
}

function ickNormVal(f, v) {
  if (f === "amount") { const n = ickNum(v); return Number.isFinite(n) ? Math.round(n * 100) / 100 : String(v ?? "").trim(); }
  if (f === "vehicle_id") return v || null;
  if (f === "page") return Number(v);
  if (f === "delete" || f === "ok") return !!v;
  return String(v ?? "").trim();
}

// the same fixes always give the same text: what decides "unsaved" (a field typed back to what it was is no fix)
function ickCanon(le) {
  const src = le || {};
  const e = {};
  for (const k of Object.keys(src.edits || {}).sort()) {
    const x = src.edits[k] || {}, o = {};
    for (const f of Object.keys(x).sort()) { if ((f === "delete" || f === "ok") && !x[f]) continue; o[f] = ickNormVal(f, x[f]); }
    if (Object.keys(o).length) e[k] = o;
  }
  const a = (src.added || []).filter((x) => x && !x._gone).map((x) => {
    const o = {};
    for (const f of Object.keys(x).sort()) if (f !== "_gone") o[f] = ickNormVal(f, x[f]);
    return o;
  }).sort((p, q) => (String(p.id) < String(q.id) ? -1 : 1));
  return JSON.stringify({ e, a });
}

// how many lines differ between what is saved and what is on the screen
function ickCount(saved, draft) {
  const s = JSON.parse(ickCanon(saved)), d = JSON.parse(ickCanon(draft));
  let n = 0;
  for (const k of new Set([...Object.keys(s.e), ...Object.keys(d.e)])) if (JSON.stringify(s.e[k]) !== JSON.stringify(d.e[k])) n++;
  const sa = new Map(s.a.map((x) => [x.id, JSON.stringify(x)])), da = new Map(d.a.map((x) => [x.id, JSON.stringify(x)]));
  for (const id of new Set([...sa.keys(), ...da.keys()])) if (sa.get(id) !== da.get(id)) n++;
  return n;
}

// what is sent to /edits: every fix, the empty ones and the lines taken out left away
function ickPayload(draft) {
  const edits = {};
  for (const [k, v] of Object.entries(draft.edits || {})) {
    const o = {};
    for (const [f, x] of Object.entries(v || {})) if (!((f === "delete" || f === "ok") && !x)) o[f] = f === "vehicle_id" ? (x || null) : x;
    if (Object.keys(o).length) edits[k] = o;
  }
  return { edits, added: (draft.added || []).filter((a) => !a._gone).map(({ _gone, ...a }) => ({ ...a, vehicle_id: a.vehicle_id || null })) };
}

function ickEq(f, a, b) {
  if (f === "amount") { const x = ickNum(a), y = ickNum(b); return Number.isFinite(x) && Number.isFinite(y) ? Math.abs(x - y) < 0.005 : String(a ?? "").trim() === String(b ?? "").trim(); }
  if (f === "vehicle_id") return (a || "") === (b || "");
  return String(a ?? "").trim() === String(b ?? "").trim();
}

function ickOverlay(base, e) {
  const v = { ...base };
  for (const f of ICK_FIELDS) if (e && e[f] !== undefined) v[f] = f === "vehicle_id" ? (e[f] || "") : e[f];
  return v;
}

// every page of the file: what the AI read it as, what the books made of it, its rows, whether it has a picture
function ickPages(f) {
  const p = (f && f.preview) || {};
  const checks = new Map((p.page_checks || []).map((c) => [c.page, c]));
  const kinds = new Map((p.page_kinds || []).map((k) => [k.page, k]));
  const rows = ((p.batches || [])[0] || {}).rows || [];
  const imgs = p.ai_images || {};
  const ns = [...new Set([...kinds.keys(), ...checks.keys(), ...rows.map((r) => r.page)])].filter((n) => n != null).sort((a, b) => a - b);
  return ns.map((n) => {
    const c = checks.get(n) || {}, k = kinds.get(n) || {};
    const kind = c.kind || k.kind || "";
    const rs = rows.filter((r) => r.page === n);
    return {
      n, kind, read_kind: k.kind || kind, kind_th: k.kind_th || c.kind_th || "", title: c.title || k.title || "",
      written: c.written_total ?? null, read_total: c.read_total ?? null, status: c.status || null, summary: c.summary || null,
      person: c.person || null, rows: rs, image: !!(imgs[String(n)] || imgs[n]),
      money: ICK_MONEY.has(kind) || rs.some((r) => ICK_LINE.has(r.dest_kind)), canAdd: ICK_ADDABLE.has(k.kind),
      warn: c.status === "mismatch" || rs.some((r) => r.flag),
    };
  });
}

// the lines of one page as the screen shows them: the server's row with the fixes not saved yet laid over it.
// state: book (goes to the books) / new (added here, not saved) / unapplied (an added line saved but the server could not use it --
// e.g. its date fell outside the half after another month was picked) / cashfuel (already on the cash-fuel page) / deleted.
// l.chg: the fields changed on the screen and not saved yet (draft against saved); l.unapplied: fields saved but not what the server
// used (its row still shows the read value) -- both per field, so a fix the server left out is never mistaken for one waiting to save.
function ickLinesOf(pg, draft, saved) {
  const d = draft || ickEmpty(), s = saved || ick.saved || ickEmpty();
  const out = [], seen = new Set();
  const savedAdded = (id) => (s.added || []).find((x) => x.id === id);
  const fieldsOf = (base, dE, sE) => {
    const val = (e, f) => (e && e[f] !== undefined ? e[f] : base[f]);
    const chg = new Set(ICK_FIELDS.filter((f) => !ickEq(f, val(dE, f), val(sE, f))));
    const un = new Set(ICK_FIELDS.filter((f) => !chg.has(f) && sE && sE[f] !== undefined
      && !ickEq(f, f === "text" ? String(sE[f] ?? "").replace(ICK_UNSURE, "") : sE[f], base[f])));
    return [chg, un];
  };
  for (const r of pg.rows) {
    if (!ICK_LINE.has(r.dest_kind) || !r.key) continue;
    const base = {
      date: r.date || "", text: String(r.text || "").replace(ICK_UNSURE, "").trim(),
      amount: r.dest_kind === "book" ? r.amount : (r.read_amount ?? r.amount), account: r.account || "7900", vehicle_id: r.vehicle_id || "",
    };
    const m = /^p\d+:(x[0-9a-z]+)$/.exec(r.key);
    if (m) {                                        // a line the owner added earlier (saved)
      seen.add(m[1]);
      const a = (d.added || []).find((x) => x.id === m[1]);
      const [chg, un] = fieldsOf(base, a, savedAdded(m[1]));
      out.push({ key: r.key, id: m[1], row: r, base, v: a ? ickOverlay(base, a) : base, added: true, inSaved: true, flag: false, chg, unapplied: un,
        state: !a || a._gone ? "deleted" : r.dest_kind === "deleted" ? "book" : r.dest_kind });
      continue;
    }
    const e = (d.edits || {})[r.key] || {};
    const [chg, un] = fieldsOf(base, e, (s.edits || {})[r.key]);
    out.push({ key: r.key, row: r, base, v: ickOverlay(base, e), added: false, flag: !!r.flag, chg, unapplied: un,
      state: e.delete ? "deleted" : r.dest_kind === "deleted" ? "book" : r.dest_kind });
  }
  const blank = { date: "", text: "", amount: "", account: "7900", vehicle_id: "" };
  for (const a of d.added || []) {
    if (Number(a.page) !== pg.n || seen.has(a.id)) continue;
    const sa = savedAdded(a.id);
    if (a._gone && !sa) continue;
    const v = ickOverlay(blank, a);
    // saved but no row came back: the server left it out (its reason is in หมายเหตุจากการอ่าน); changed since, it waits to be saved again
    const same = !!sa && ICK_FIELDS.every((f) => ickEq(f, v[f], ickOverlay(blank, sa)[f]));
    out.push({ key: `p${pg.n}:${a.id}`, id: a.id, row: null, base: null, v, added: true, inSaved: !!sa, flag: false,
      chg: same ? new Set() : new Set(ICK_FIELDS), unapplied: same ? new Set(ICK_FIELDS) : new Set(),
      state: a._gone ? "deleted" : same ? "unapplied" : "new" });
  }
  const order = { book: 0, new: 0, unapplied: 0, cashfuel: 1, deleted: 2 };
  return out.map((l, i) => [l, i]).sort((x, y) => order[x[0].state] - order[y[0].state] || x[1] - y[1]).map((x) => x[0]);
}

// the fields typed on the screen and not saved yet
function ickChanged(l) {
  return l.chg || new Set(l.base ? [] : ICK_FIELDS);
}

// รวมบรรทัด of a page: what is left on it (cash-fuel lines count, as acc_docs.build counts them against the paper)
function ickPageSum(lines) {
  return Math.round(lines.filter((l) => l.state !== "deleted" && l.state !== "unapplied").reduce((s, l) => s + (Number.isFinite(ickNum(l.v.amount)) ? ickNum(l.v.amount) : 0), 0) * 100) / 100;
}

function ickAccName(code, accounts) {
  if (ICK_DEST_NAME[code]) return ICK_DEST_NAME[code];
  const a = (accounts || []).find((x) => x[0] === code);
  return a ? String(a[1]).replace(/\s*\(ไม่ใช่ค่าใช้จ่าย\)\s*$/, "") : "";
}

function ickVehLabel(v) {
  if (!v) return "";
  const n = v.vehicle_number, p = v.plate_number;
  return n && p && String(n) !== String(p) ? `#${n} (${p})` : String(n || p || "");
}

// ไปที่ of a line that goes to the books, worked out on the page as the owner types (the server says the same: inbox_ai.dest_text)
function ickDestText(account, vid, accounts, vehicles) {
  const name = ickAccName(account, accounts);
  const v = vid ? (vehicles || []).find((x) => x.id === vid) : null;
  return `บัญชี ${account}${name ? ` ${name}` : ""}${v ? ` · รถ ${ickVehLabel(v)}` : ""}`;
}

// the server's own text while nothing on the line waits to be saved (it also says when the paper's truck is not in the register);
// worked out here only for a line changed on the screen, or added and not saved yet
function ickDest(l, accounts, vehicles) {
  if (l.state === "deleted") return ["deleted", "ลบแล้ว · ไม่ลง"];
  if (l.state === "unapplied") return ["unapplied", "ใส่ไม่ได้ · ไม่ลงบัญชี — ดูหมายเหตุจากการอ่าน แล้วแก้ให้ถูก"];
  const changed = ickChanged(l).size > 0;
  if (l.state === "cashfuel") return ["cashfuel", `${l.row.dest}${changed ? " · ตรวจใหม่เมื่อบันทึก" : ""}`];
  if (l.row && l.row.dest && !changed) return ["book", l.row.dest];
  return ["book", ickDestText(l.v.account, l.v.vehicle_id, accounts, vehicles)];
}

// the first day of the file's half (26th of the month before / 11th): where a line added on a page with no dates starts
function ickHalfStart(month, half) {
  if (!/^\d{4}-\d{2}$/.test(month || "")) return "";
  const [y, m] = month.split("-").map(Number);
  if (half === "end") return `${month}-11`;
  return m === 1 ? `${y - 1}-12-26` : `${y}-${String(m - 1).padStart(2, "0")}-26`;
}

const ickNewId = () => `x${Date.now().toString(36).slice(-6)}${Math.floor(Math.random() * 1296).toString(36).padStart(2, "0")}`;

// ---------------------------------------------------------------- the page
const ickDirty = () => !!(ick.saved && ick.draft && ickCount(ick.saved, ick.draft) > 0);

function ickWire() {
  if (ick.wired) return;
  ick.wired = true;
  window.addEventListener("beforeunload", (e) => { if (!$("inboxCheckView").hidden && ickDirty()) { e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("keydown", (e) => {
    if ($("inboxCheckView").hidden || !(e.ctrlKey || e.metaKey) || String(e.key).toLowerCase() !== "s") return;
    e.preventDefault();
    ickSave();
  });
  window.addEventListener("resize", ickMeasure);
}

// fleet.js route() and app.js showView(): leaving with fixes not saved asks first (the sidebar, its hashless items, the browser's Back)
function ickMayLeave(h) {
  if (!ick.id || $("inboxCheckView").hidden || h === `accounts-in/check=${ick.id}` || !ickDirty()) return true;
  if (confirm(ICK_LEAVE)) { ick.draft = ickCopy(ick.saved); return true; }
  history.replaceState(null, "", `#accounts-in/check=${ick.id}`);
  return false;
}

async function openInboxCheck(id) {
  showView("inboxCheck");
  ickWire();
  if (ick.id !== id) { ick.page = null; ick.stripX = 0; ick.msg = null; ick.saved = ick.draft = null; ick.autoAcc = ick.manualAcc = null; ickHr.drafts = {}; ickHr.msgs = {}; }
  ick.id = id;
  const seq = ++ick.seq;
  fill($("ickBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  let f;
  try { f = await api(`/api/accounts/inbox/${id}`); }
  catch (err) {
    if (seq !== ick.seq) return;
    fill($("ickBody"), el("p", { class: "error", text: `เปิดไฟล์ไม่สำเร็จ: ${err.message}` }),
      el("button", { class: "btn", type: "button", text: "กลับไปกล่องรับไฟล์", onclick: () => goHash("#accounts-in") }));
    return;
  }
  if (seq !== ick.seq) return;
  if (!ickCheckable(f.preview)) {                     // not an AI-read expense paper (or one read before this page): the dialog
    ick.id = null;
    history.replaceState(null, "", "#accounts-in");
    await openAccounts("in");
    ibxPreview(id);
    return;
  }
  ickTake(f);
  ickRender();
}

// the same rule as app/inbox.row_view "check": an AI-read expense paper whose rows say where they go (one read before this page has
// none -- it stays on the dialog, whose อ่านใหม่ (no AI cost) gives it them)
function ickCheckable(p) {
  return !!(p && p.acc_docs_batch && (((p.batches || [])[0] || {}).rows || []).some((r) => r.key && r.dest_kind));
}

function ickTake(f) {
  ick.f = f;
  ick.saved = ickCopy((f.preview || {}).line_edits);
  ick.draft = ickCopy(ick.saved);
  ick.version = f.edits_version || "";
  ick.editable = ickCheckable(f.preview) && f.status !== "ai_running" && !f.pending;
}

function ickSay(text, bad = false) {
  ick.msg = text ? { text, bad } : null;
  const box = $("ickMsg");
  if (box) fill(box, ick.msg ? el("span", { class: ick.msg.bad ? "warn-text" : "ok-text", text: ick.msg.text }) : null);
  if (box && !window.ResizeObserver) ickMeasure();
}

function ickRender() {
  const f = ick.f;
  const pages = ickPages(f);
  if (ick.page !== "all" && !pages.some((x) => x.n === ick.page)) ick.page = pages.length ? pages[0].n : "all";
  const shown = ick.page === "all" ? pages : pages.filter((x) => x.n === ick.page);
  const was = document.querySelector("#inboxCheckView .ick-strip");
  if (was) ick.stripX = was.scrollLeft;
  fill($("ickBody"), ickTop(f), ickStrip(pages),
    el("div", { id: "ickMain", class: "ick-main" }, ick.page === "all" ? ickHrAll(f) : null, ...shown.map(ickPageEl),
      !pages.length ? el("p", { class: "muted", text: "ไม่มีหน้าในไฟล์นี้" }) : null));
  ickStripRestore();
  ickRefresh();
  ickMeasure();
  ickWatchTop();
}

// the bar's height, so the scan sticks just under it -- measured again whenever the bar grows or shrinks (หมายเหตุจากการอ่าน opened,
// a message shown, a narrower window), not only on render
function ickMeasure() {
  const top = document.querySelector("#inboxCheckView .ick-top"), body = $("ickBody");
  if (top && body) body.style.setProperty("--ick-top", `${top.offsetHeight}px`);
}

function ickWatchTop() {
  const top = document.querySelector("#inboxCheckView .ick-top");
  if (!top) return;
  if (window.ResizeObserver) {
    if (!ick.ro) ick.ro = new ResizeObserver(() => ickMeasure());
    ick.ro.disconnect();
    ick.ro.observe(top);
  } else {
    const det = top.querySelector("details");
    if (det) det.addEventListener("toggle", ickMeasure);
  }
}

function ickTop(f) {
  const p = f.preview || {};
  const sup = ibxSuperseded(f);
  const half = p.half === "mid" ? "กลางเดือน (26–10)" : p.half === "end" ? "ปลายเดือน (11–25)" : "";
  const back = el("button", { class: "btn small", type: "button", text: "กลับ", onclick: () => { if (!ickDirty() || confirm(ICK_LEAVE)) { ick.draft = ickCopy(ick.saved); goHash("#accounts-in"); } } });
  const fileLink = el("a", { class: "btn small", href: `/api/accounts/inbox/${f.id}/file?token=${encodeURIComponent(state.token)}`, target: "_blank", rel: "noopener", text: "เปิดไฟล์" });
  const save = el("button", { id: "ickSave", class: "btn", type: "button", "data-ibx-file": f.id, onclick: ickSave });
  const book = f.status === "read" && !sup ? el("button", { id: "ickBook", class: "btn primary", type: "button", "data-ibx-file": f.id, text: "ลงบัญชี", onclick: ickBook })
    : sup && (p.batches || []).length ? el("button", { id: "ickBook", class: "btn", type: "button", "data-ibx-file": f.id, text: "ลงบัญชีไฟล์นี้อีกครั้ง", onclick: ickBook }) : null;
  const why = el("span", { id: "ickWhy", class: "ick-why" });
  const hr = f.hr || {};
  const hrRead = hr.can_read && hr.access === "ok" ? el("button", { id: "ickHrRead", class: "btn", type: "button", "data-ibx-file": f.id, onclick: ickHrRead,
    title: "อ่านหน้าเรื่องพนักงานและรายการเงินกู้พนักงานของไฟล์นี้ด้วย AI (ใช้ค่า AI เล็กน้อย)", text: "อ่านเรื่องพนักงาน" }) : null;
  const conflicts = (f.conflicts || []).length ? el("div", { class: "ick-note warn" },
    el("strong", { text: "ยังมีเรื่องที่ต้องเลือกก่อนลงบัญชี: " }), (f.conflicts || []).map((c) => c.message || IBX_CONFLICT[c.key] || "ต้องเลือก").join(" · "), " ",
    el("button", { class: "btn small primary", type: "button", text: ibxChoiceLabel(f), onclick: ickChoose })) : null;
  const flags = (p.flags || []).length ? el("details", { class: "ick-flags" }, el("summary", { text: `หมายเหตุจากการอ่าน (${p.flags.length})` }),
    el("ul", { class: "small" }, ...p.flags.map((x) => el("li", { text: x })))) : null;
  return el("div", { class: "ick-top" },
    el("div", { class: "ick-bar" }, back, el("h2", { text: f.file_name || "ไฟล์" }), ibxBadge(f),
      el("div", { class: "ick-actions" }, fileLink, hrRead, save, book)),
    el("div", { class: "ick-sub small" },
      el("span", { class: "muted", text: [(f.branches || []).join(", ") || p.branch, f.month ? `งวด ${ibxMonth(f.month)}` : null, half].filter(Boolean).join(" · ") }),
      el("span", { id: "ickTotals" }), el("span", { id: "ickHr", class: "ick-hr-count" }), why, el("span", { id: "ickMsg", "aria-live": "polite" })),
    f.status === "booked" ? el("div", { class: "ick-note" }, "ไฟล์นี้ลงบัญชีแล้ว — บันทึกการแก้แล้วระบบลงบัญชีใหม่ตามที่แก้ให้ทันที") : null,
    f.pending ? el("div", { class: "ick-note warn", text: `อ่านใหม่แล้ว ${f.pending.message || "ต้องเลือกก่อน"} — รายการเดิมยังอยู่ในบัญชี · เลือกก่อนแล้วค่อยแก้รายการ` }) : null,
    sup ? el("div", { class: "ick-note warn", text: `ไฟล์นี้ถูกแทนที่ด้วย ${sup.by || "ไฟล์ที่ส่งทีหลัง"} — รายการของไฟล์นั้นอยู่ในบัญชีแทน` })
      : f.error ? el("div", { class: "ick-note warn", text: f.error }) : null,
    conflicts, flags);
}

function ickStrip(pages) {
  const chip = (key, label, sub, warn, hrN) => el("button", { class: "ick-chip", type: "button", "aria-pressed": String(ick.page === key),
    onclick: (e) => { const nav = e.currentTarget.closest(".ick-strip"); if (nav) ick.stripX = nav.scrollLeft; ick.page = key; ickRender(); window.scrollTo(0, 0); } },
    el("span", { text: label }), sub ? el("small", { text: sub }) : null, warn ? el("span", { class: "ick-dot", title: "ยอดไม่ตรง หรือมีบรรทัดอ่านไม่ชัด" }) : null,
    hrN ? el("span", { class: "ick-hrn", title: `เรื่องพนักงานรอบันทึก ${hrN} รายการ`, text: String(hrN) }) : null);
  const allHr = ickHrItems(ick.f).filter((it) => it.status === "pending").length;
  const nav = el("nav", { class: "ick-strip", "aria-label": "หน้าในไฟล์" },
    ...pages.map((pg) => chip(pg.n, `หน้า ${pg.n}`, pg.kind_th, pg.warn, ickHrPendingOn(ick.f, pg.n))), chip("all", "ทุกหน้า", `${pages.length} หน้า`, false, allHr));
  // the strip stays where it was slid to when a page is opened (owner, 28 Nov 2026: it went back to หน้า 1 every time): its position is
  // kept when a chip is pressed and put back by ickStripRestore once the new strip is on the page
  nav.addEventListener("scroll", () => { ick.stripX = nav.scrollLeft; }, { passive: true });
  return nav;
}

// the strip back where it was, and the open page's chip in view if it is off the edge
function ickStripRestore() {
  const nav = document.querySelector("#inboxCheckView .ick-strip");
  if (!nav) return;
  nav.scrollLeft = ick.stripX || 0;
  const on = nav.querySelector('[aria-pressed="true"]');
  if (on) {
    const a = on.getBoundingClientRect(), b = nav.getBoundingClientRect();
    if (a.left < b.left) nav.scrollLeft -= b.left - a.left + 24;
    else if (a.right > b.right) nav.scrollLeft += a.right - b.right + 24;
  }
  ick.stripX = nav.scrollLeft;
}

function ickPageEl(pg) {
  const head = el("div", { class: "ick-page-head" }, el("h3", { text: `หน้า ${pg.n}` }), pg.kind_th ? el("span", { class: "badge", text: pg.kind_th }) : null,
    pg.title ? el("span", { class: "muted small", text: pg.title }) : null);
  const side = pg.money ? ickTable(pg)      // a page the AI found no money on can still take a missed line
    : pg.canAdd && ick.editable ? el("div", { class: "ick-stack" }, ickCard(pg), ickTable(pg)) : ickCard(pg);
  const onPage = ickHrSplit(ick.f, pg).onPage;              // บันทึกในพนักงาน of a memo page (a loan line's sits under its line)
  const adv = pg.read_kind === "advance" ? ickAdvEl(pg) : null;   // the list's ส่วนกลาง / ความผิด / อุปกรณ์ per person (28 Nov 2026)
  return el("section", { class: "ick-page", id: `ickP${pg.n}`, "data-page": String(pg.n) }, head,
    el("div", { class: "ick-grid" }, ickScan(pg), onPage.length || adv ? el("div", { class: "ick-stack" }, side, adv, ...onPage.map(ickHrEl)) : side));
}

// ---------------------------------------------------------------- ใบเบิกกลางเดือน: ส่วนกลาง / ความผิด / อุปกรณ์ per person (owner, 28 Nov 2026)
// The AI page read's advance_rows (app/inbox_ai.py), shown so the owner can compare them with the scan -- READ-ONLY for now: the pay page
// (app/payroll.py deduct_view) takes them from here. A page read before the reader returned them has none: อ่านหน้านี้ใหม่ reads that
// page again from its stored image (one paid AI call, POST /api/accounts/inbox/<id>/pages/reread).
const ICK_ADV_COLS = [["common", "ส่วนกลาง"], ["fault", "ความผิด"], ["other", "อุปกรณ์"]];

// {old: read before advance_rows existed, rows: [{code, name, amount, common, fault, other, unsure}], sums: {common, fault, other}}
function ickAdvRows(f, n) {
  const page = (((f && f.preview) || {}).ai_pages || []).find((p) => p && p.n === n) || {};
  if (!Object.prototype.hasOwnProperty.call(page, "advance_rows")) return { old: true, rows: [], sums: { common: 0, fault: 0, other: 0 } };
  const rows = (page.advance_rows || []).filter((r) => r && typeof r === "object");
  const sums = {};
  for (const [k] of ICK_ADV_COLS) sums[k] = Math.round(rows.reduce((s, r) => s + (Number.isFinite(ickNum(r[k])) ? ickNum(r[k]) : 0), 0) * 100) / 100;
  return { old: false, rows, sums };
}

// The end-of-month file's advance pages are the SIGNED copies: the pay page reads only the mid-month file (app/payroll.py advance_lists),
// so no paid re-read and no "the pay page cannot use these" is offered there (owner's review, 28 Nov 2026) -- the rows are only shown.
function ickAdvEl(pg) {
  const got = ickAdvRows(ick.f, pg.n);
  if ((((ick.f && ick.f.preview) || {}).half) === "end") {
    return el("div", { class: "ick-card ick-adv-card" }, el("strong", { text: "ใบเบิกกลางเดือน (ฉบับเซ็นรับ)" }),
      el("p", { class: "small muted", text: "หน้าคำนวณเงินเดือนใช้ส่วนกลาง / ความผิด / อุปกรณ์ จากไฟล์กลางเดือน ไม่ใช่จากฉบับเซ็นรับในไฟล์ปลายเดือน" }));
  }
  const busy = ick.rereading === pg.n;
  const btn = ick.editable && pg.image ? el("button", { class: "btn small", type: "button", "data-ibx-file": ick.f.id, disabled: !!ick.rereading,
    text: busy ? "กำลังอ่านใหม่…" : "อ่านหน้านี้ใหม่ (เอาส่วนกลาง / ความผิด)", onclick: () => ickReread(pg) }) : null;
  const cellOf = (v) => { const t = String(v ?? "").trim(); return t ? (Number.isFinite(ickNum(t)) ? fmtMoney(ickNum(t)) : t) : "–"; };
  const body = got.old
    ? el("p", { class: "small warn-text", text: "หน้านี้อ่านก่อนมีช่องส่วนกลาง / ความผิด / อุปกรณ์ — หน้าคำนวณเงินเดือนยังเอายอดเหล่านี้ไปใช้ไม่ได้ จนกว่าจะอ่านหน้านี้ใหม่" })
    : !got.rows.length ? el("p", { class: "small muted", text: "AI ไม่ได้อ่านแถวรายคนของหน้านี้ — ถ้ากระดาษมีช่องส่วนกลาง / ความผิด ให้อ่านหน้านี้ใหม่" })
    : el("div", { class: "ick-adv-wrap" }, el("table", { class: "ick-adv" },
      el("thead", {}, el("tr", {}, ...["รหัส", "ชื่อ", "จำนวนเงิน", ...ICK_ADV_COLS.map((c) => c[1])].map((t) => el("th", { text: t })))),
      el("tbody", {}, ...got.rows.map((r) => el("tr", { class: r.unsure ? "unsure" : "" },
        el("td", { text: r.code || "–" }), el("td", { text: `${r.name || ""}${r.unsure ? " (อ่านไม่ชัด)" : ""}` }), el("td", { class: "num", text: cellOf(r.amount) }),
        ...ICK_ADV_COLS.map(([k]) => el("td", { class: "num", text: cellOf(r[k]) }))))),
      el("tfoot", {}, el("tr", {}, el("td", { colspan: "3", text: `รวม ${got.rows.length} คน` }),
        ...ICK_ADV_COLS.map(([k]) => el("td", { class: "num", text: fmtMoney(got.sums[k]) }))))));
  return el("div", { class: "ick-card ick-adv-card" },
    el("strong", { text: "ส่วนกลาง / ความผิด / อุปกรณ์ รายคน (ดูอย่างเดียว เทียบกับรูป)" }), body,
    el("p", { class: "small muted", text: "หน้าคำนวณเงินเดือนใช้ยอดเหล่านี้ · ถ้าอ่านผิด แก้ที่นี่ยังไม่ได้ ให้อ่านหน้านี้ใหม่" }), btn);
}

async function ickReread(pg) {
  const f = ick.f;
  if (!f || ick.rereading) return;
  if (ickDirty()) { ickSay("มีการแก้ที่ยังไม่ได้บันทึก — บันทึกการแก้ก่อน แล้วค่อยอ่านหน้านี้ใหม่", true); return; }
  if (!confirm(`อ่านหน้า ${pg.n} ใหม่ด้วย AI เพื่อเอาช่องส่วนกลาง / ความผิด / อุปกรณ์ รายคน?\nใช้ค่า AI 1 ครั้ง (1 หน้า) · บรรทัดของหน้านี้จะอ่านใหม่ด้วย${f.status === "booked" ? " และไฟล์นี้จะลงบัญชีใหม่ทันที" : ""}`)) return;
  ick.rereading = pg.n;
  ickRender();
  await ibxRun(f.id, async () => {
    try {
      const res = await api(`/api/accounts/inbox/${f.id}/pages/reread`, jsonOpts("POST", { pages: [pg.n] }));
      if (ick.f === f) {
        const failed = ibxRebookFailed(res).map((x) => x.text);
        const bad = ((res.reread || {}).failed || []).length > 0;
        ick.msg = res.message ? { text: res.message, bad: true }
          : { text: `${bad ? `อ่านหน้า ${pg.n} ใหม่ไม่สำเร็จ — ยังใช้ที่อ่านไว้เดิม` : `อ่านหน้า ${pg.n} ใหม่แล้ว`}${res.booked_again ? " · ลงบัญชีใหม่แล้ว" : ""}${failed.length ? ` · ${failed.join(" · ")}` : ""}`, bad: bad || failed.length > 0 };
        ickTake(res);
      }
    } catch (err) { ick.msg = { text: err.message, bad: true }; }
  });
  ick.rereading = null;
  ickRender();
}

function ickScan(pg) {
  const box = el("div", { class: "ick-scan" });
  const none = () => fill(box, el("p", { class: "ick-noimg", text: "ไม่มีรูปหน้านี้" }));
  if (!pg.image) { none(); return box; }
  const img = el("img", { alt: `หน้า ${pg.n}`, loading: "lazy", src: `/api/accounts/inbox/${ick.id}/page/${pg.n}?token=${encodeURIComponent(state.token)}` });
  img.onerror = none;
  img.onclick = () => img.classList.toggle("zoom");
  fill(box, img, el("p", { class: "muted small ick-hint", text: "กดที่รูปเพื่อขยาย" }));
  return box;
}

// a memo, an HR paper, a cover or the typed fuel table: what it says, who it is about, and that it is not booked
function ickCard(pg) {
  const noted = pg.rows.find((r) => r.dest_kind === "noted");
  let body = null;
  if (pg.summary && /^\s*\{/.test(pg.summary)) {
    try {
      const parts = JSON.parse(pg.summary);
      body = el("ul", { class: "small ick-parts" }, ...Object.entries(parts).map(([k, v]) => el("li", {}, `${ICK_PARTS[k] || (pg.kind === "fuel_table" ? `รถ ${k}` : k)} `, el("b", { text: `${fmtMoney(v)} ฿` }))));
    } catch (_) { body = null; }
  }
  if (!body && pg.summary) body = el("p", { class: "small", text: pg.summary });
  return el("div", { class: "ick-card" },
    el("strong", { text: pg.title || pg.kind_th || `หน้า ${pg.n}` }), body,
    pg.person ? el("div", { class: "small" }, el("span", { class: "muted", text: "เกี่ยวกับ: " }), pg.person) : null,
    pg.written != null ? el("div", { class: "small muted", text: `ยอดที่เขียนบนกระดาษ ${fmtMoney(pg.written)} ฿` }) : null,
    el("div", {}, el("span", { class: "ick-dest noted", text: noted ? noted.dest : "ไม่ลงบัญชี · ข้อมูลประกอบ" })));
}

function ickTable(pg) {
  const lines = ickLinesOf(pg, ick.draft);
  const on = lines.filter((l) => l.state === "book" || l.state === "new" || l.state === "unapplied"), off = lines.filter((l) => l.state === "cashfuel" || l.state === "deleted");
  const total = el("div", { class: "ick-total", "data-total": String(pg.n) });
  const add = pg.canAdd && ick.editable ? el("button", { class: "btn small", type: "button", text: "+ เพิ่มบรรทัด", onclick: () => ickAdd(pg, lines) }) : null;
  const unsure = lines.filter((l) => l.flag && !l.added && l.state !== "deleted" && !ickChanged(l).size);
  const allOk = unsure.length && ick.editable ? el("button", { class: "btn small", type: "button", text: `ถูกแล้วทั้งหน้า (${unsure.length} บรรทัดอ่านไม่ชัด)`,
    title: "ดูกับรูปแล้ว ทุกบรรทัดที่อ่านไม่ชัดในหน้านี้ถูกต้อง", onclick: () => { for (const l of unsure) ickSetOk(l.key, true); ickRepaint(pg.n); } }) : null;
  const byLine = ickHrSplit(ick.f, pg).byLine;
  const withHr = (l) => [ickLineEl(pg, l), ...byLine.filter((it) => it.line_key === l.key).map(ickHrEl)];
  const box = el("div", { class: "ick-lines" },
    el("div", { class: "ick-r head", "aria-hidden": "true" }, ...["วันที่", "รายการ", "บาท", "บัญชี", "รถ", "ไปที่", ""].map((t, i) => el("span", { class: `c-${["date", "text", "amt", "acc", "veh", "dest", "act"][i]}`, text: t }))),
    ...on.flatMap(withHr),
    !on.length ? el("p", { class: "muted small ick-empty", text: "ไม่มีบรรทัดที่จะลงบัญชีจากหน้านี้" }) : null,
    off.length ? el("div", { class: "ick-divider", text: "ไม่ลงบัญชีจากหน้านี้" }) : null,
    ...off.flatMap(withHr),
    el("div", { class: "ick-foot" }, total, el("span", { class: "ick-footbtns" }, allOk, add)));
  ickPaintTotal(pg, total);
  return box;
}

function ickPaintTotal(pg, box) {
  const target = box || document.querySelector(`#inboxCheckView [data-total="${pg.n}"]`);
  if (!target) return;
  const a = ickPageSum(ickLinesOf(pg, ick.draft)), b = pg.written;
  if (b == null) { fill(target, el("span", { class: "muted", text: `รวมบรรทัด ${fmtMoney(a)} ฿ · ไม่มียอดบนกระดาษ` })); return; }
  const ok = Math.abs(a - b) < 0.5;
  fill(target, el("span", { class: ok ? "ok-text" : "warn-text",
    text: ok ? `✓ รวมบรรทัด ${fmtMoney(a)} = บนกระดาษ ${fmtMoney(b)} ฿` : `รวมบรรทัด ${fmtMoney(a)} · บนกระดาษ ${fmtMoney(b)} · ต่าง ${fmtMoney(Math.abs(a - b))} ฿` }));
}

function ickAccSelect(code) {
  const list = ick.f.accounts || [];
  const s = el("select", { "aria-label": "บัญชี" }, ...list.map(([c, n]) => el("option", { value: c, text: `${c} ${n}` })));
  if (code && !list.some((x) => x[0] === code)) s.prepend(el("option", { value: code, text: code }));
  s.value = code || "7900";
  return s;
}

function ickVehSelect(vid) {
  const s = el("select", { "aria-label": "รถ" }, el("option", { value: "", text: "— ไม่ระบุรถ —" }));
  const by = new Map();
  for (const v of ick.f.vehicles || []) { const b = v.branch || "อื่นๆ"; if (!by.has(b)) by.set(b, []); by.get(b).push(v); }
  for (const [b, vs] of by) {
    s.append(el("optgroup", { label: `สาขา${b}` }, ...vs.slice().sort((x, y) => num(x.vehicle_number) - num(y.vehicle_number))
      .map((v) => el("option", { value: v.id, text: ickVehLabel(v) }))));
  }
  s.value = vid || "";
  return s;
}

const ickTruckAcc = () => new Set(ick.f.truck_accounts || ICK_TRUCK_ACC);

function ickLineEl(pg, l0) {
  let l = l0;
  const ro = !ick.editable || l.state === "deleted";
  const amtText = (v) => (typeof v === "number" ? fmtMoney(v) : String(v ?? ""));
  const date = el("input", { type: "date", value: l.v.date || "", "aria-label": "วันที่" });
  const text = el("input", { type: "text", value: l.v.text || "", maxlength: "200", "aria-label": "รายการ", placeholder: l.state === "new" ? "รายการ" : null });
  const amt = el("input", { type: "text", inputmode: "decimal", value: amtText(l.v.amount), "aria-label": "บาท", placeholder: l.state === "new" ? "0.00" : null });
  const acc = ickAccSelect(l.v.account);
  const veh = ickVehSelect(l.v.vehicle_id);
  const dest = el("span", { class: "ick-dest" });
  const del = el("button", { class: "linkbtn small", type: "button", text: l.state === "deleted" ? "เอาคืน" : "ลบ",
    "aria-label": l.state === "deleted" ? "เอาบรรทัดนี้คืน" : "ลบบรรทัดนี้" });
  for (const x of [date, text, amt, acc, veh]) x.disabled = ro;
  del.disabled = !ick.editable;
  const cell = (cls, ...kids) => el("span", { class: `c-${cls}` }, ...kids);
  // อ่านไม่ชัด + ถูกแล้ว: the owner checked it against the scan and it is right -- nothing to retype (owner, 28 Nov 2026)
  const okNow = () => !!((ick.draft.edits || {})[l.key] || {}).ok;
  const okBtn = l.flag && !ro && !l.added ? el("button", { class: "linkbtn small ick-okbtn", type: "button" }) : null;
  const flagText = l.flag ? el("span", {}) : null;
  const flag = l.flag ? el("span", { class: "ick-flag" }, flagText, okBtn) : null;
  const paintFlag = () => {
    if (!flag) return;
    const ok = okNow();
    flagText.className = ok ? "ok-text" : "warn-text";
    flagText.textContent = ok ? "ถูกแล้ว (รอบันทึก) " : "อ่านไม่ชัด ";
    if (okBtn) okBtn.textContent = ok ? "ยกเลิก" : "ถูกแล้ว";
  };
  if (okBtn) okBtn.onclick = () => { ickSetOk(l.key, !okNow()); paintFlag(); ickRefresh(); };
  const accNote = el("span", { class: "ick-accnote muted small", hidden: true });
  const cells = { date: cell("date", date), text: cell("text", text, flag), amount: cell("amt", amt), account: cell("acc", acc, accNote), vehicle_id: cell("veh", veh) };
  const row = el("div", { class: `ick-r ${l.state}${l.row && l.row.edited ? " edited" : ""}`, "data-key": l.key },
    cells.date, cells.text, cells.amount, cells.account, cells.vehicle_id, cell("dest", dest), cell("act", del));
  const paint = () => {
    const ch = ickChanged(l);
    row.className = `ick-r ${l.state}${l.row && l.row.edited ? " edited" : ""}`;
    for (const f of ICK_FIELDS) {
      const un = l.state !== "unapplied" && l.unapplied.has(f) && !ch.has(f);       // saved, but not what the server used
      cells[f].classList.toggle("chg", l.state !== "new" && ch.has(f));
      cells[f].classList.toggle("unapplied", un);
      cells[f].title = un ? "ที่แก้ไว้ใช้ไม่ได้ — ดูหมายเหตุจากการอ่าน" : "";
    }
    veh.hidden = !ickTruckAcc().has(acc.value) && !veh.value;
    const [k, t] = ickDest(l, ick.f.accounts, ick.f.vehicles);
    dest.className = `ick-dest ${k}`;
    dest.textContent = t;
    if (flag) { flag.hidden = ch.size > 0; paintFlag(); }
  };
  const on = (f, input, read) => {
    const go = () => {
      ickSet(l, f, read());
      l = ickLinesOf(pg, ick.draft).find((x) => x.key === l.key) || l;
      paint();
      ickPaintTotal(pg);
      ickRefresh();
    };
    input.addEventListener("input", go);
    input.addEventListener("change", go);
  };
  on("date", date, () => date.value);
  on("text", text, () => text.value);
  on("amount", amt, () => amt.value);
  on("account", acc, () => acc.value);
  on("vehicle_id", veh, () => veh.value || null);
  // retyped รายการ -> its บัญชี re-picked from the new wording, unless the owner picked the บัญชี himself (owner, 28 Nov 2026)
  const auto = ick.autoAcc || (ick.autoAcc = new Set()), manual = ick.manualAcc || (ick.manualAcc = new Set());
  acc.addEventListener("change", (e) => { if (e.isTrusted) { auto.delete(l.key); manual.add(l.key); accNote.hidden = true; } });
  text.addEventListener("change", async () => {
    const t = text.value.trim();
    const picked = () => manual.has(l.key) || (l.state !== "new" && ickChanged(l).has("account") && !auto.has(l.key));
    if (ro || !t || picked() || (l.state !== "new" && !ickChanged(l).has("text"))) return;
    accNote.hidden = false;
    accNote.textContent = "กำลังเลือกหมวดจากชื่อ…";
    let r = null;
    try { r = await api("/api/accounts/inbox/suggest-account", jsonOpts("POST", { text: t })); } catch (err) { r = null; }
    if (text.value.trim() !== t || picked() || !r || !r.account) { accNote.hidden = !auto.has(l.key); if (!r || !r.account) accNote.textContent = ""; return; }
    auto.add(l.key);
    if (acc.value !== r.account) { acc.value = r.account; acc.dispatchEvent(new Event("change")); }
    accNote.textContent = r.source === "history" && r.like ? `หมวดตามชื่อใหม่ (เหมือน “${r.like}” ที่เคยลง)` : "หมวดตามชื่อใหม่";
  });
  del.onclick = () => ickToggle(pg, l);
  paint();
  return row;
}

function ickSetOk(key, on) {
  const d = ick.draft, e = { ...(d.edits[key] || {}) };
  if (on) e.ok = true; else delete e.ok;
  if (Object.keys(e).length) d.edits[key] = e; else delete d.edits[key];
}

// one field typed: kept as a fix of that line (typed back to what it was, the fix goes away)
function ickSet(l, field, value) {
  const d = ick.draft;
  if (l.added) {
    const a = d.added.find((x) => x.id === l.id);
    if (a) a[field] = value;
    else if (l.row) d.added.push({ id: l.id, page: l.row.page, ...l.base, [field]: value });
    return;
  }
  const e = { ...(d.edits[l.key] || {}) };
  const saved = ((ick.saved.edits || {})[l.key] || {})[field];
  if (saved !== undefined && ickEq(field, value, saved)) e[field] = saved;              // typed back to what is saved
  else if (saved === undefined && ickEq(field, value, l.base[field])) delete e[field];  // typed back to what was read: no fix
  else e[field] = value;
  if (Object.keys(e).length) d.edits[l.key] = e; else delete d.edits[l.key];
}

function ickToggle(pg, l) {
  const d = ick.draft;
  if (l.added && !l.inSaved) d.added = d.added.filter((a) => a.id !== l.id);
  else if (l.added) {
    const a = d.added.find((x) => x.id === l.id);
    if (a) { if (a._gone) delete a._gone; else a._gone = true; }
    else { const s = (ick.saved.added || []).find((x) => x.id === l.id); if (s) d.added.push({ ...s }); }
  } else {
    const e = { ...(d.edits[l.key] || {}) };
    if (e.delete) delete e.delete; else e.delete = true;
    if (Object.keys(e).length) d.edits[l.key] = e; else delete d.edits[l.key];
  }
  ickRepaint(pg.n);
}

function ickAdd(pg, lines) {
  const last = [...lines].reverse().find((l) => l.state !== "deleted");
  const p = ick.f.preview || {};
  const id = ickNewId();
  ick.draft.added.push({ id, page: pg.n, date: (last && last.v.date) || ickHalfStart(p.month, p.half), text: "", amount: "",
    account: (last && last.v.account) || (pg.read_kind === "fuel" ? "6130" : "7900") });
  ickRepaint(pg.n);
  const row = document.querySelector(`#inboxCheckView [data-key="p${pg.n}:${id}"] .c-text input`);
  if (row) row.focus();
}

function ickRepaint(n) {
  const pg = ickPages(ick.f).find((x) => x.n === n);
  const old = $(`ickP${n}`);
  if (pg && old) old.replaceWith(ickPageEl(pg));
  ickRefresh();
}

// the bar: how many fixes wait, whether ลงบัญชี may go, the file's totals as they stand on the screen
function ickRefresh() {
  const f = ick.f;
  if (!f) return;
  const n = ickCount(ick.saved, ick.draft);
  const save = $("ickSave"), book = $("ickBook"), why = $("ickWhy"), totals = $("ickTotals");
  if (save) {
    save.textContent = ick.saving ? "กำลังบันทึก…" : n ? `บันทึกการแก้ (${n})` : "บันทึกการแก้";
    save.disabled = !n || !!ick.saving || ibx.busy.has(f.id);
    save.className = n ? "btn primary" : "btn";
    save.title = "Ctrl+S";
  }
  if (book) {
    book.disabled = n > 0 || !!ick.saving || ibx.busy.has(f.id);
    book.className = n ? "btn" : book.textContent === "ลงบัญชี" ? "btn primary" : "btn";
  }
  if (why) why.textContent = n ? "บันทึกการแก้ก่อน แล้วค่อยลงบัญชี" : "";
  const hrBox = $("ickHr"), hrRead = $("ickHrRead");
  if (hrBox) {
    const items = ickHrItems(f), pending = items.filter((it) => it.status === "pending");
    const stuck = (f.hr || {}).access === "ok" ? pending.filter((it) => it.block).length : 0, waiting = pending.length - stuck;
    hrBox.textContent = !items.length ? "" : !pending.length ? `เรื่องพนักงาน: ตัดสินครบแล้ว (${items.length})`
      : `เรื่องพนักงาน: ${waiting} รอบันทึก${stuck ? ` · ${stuck} บันทึกไม่ได้ (มีแล้ว / ต้องเลือก) — กดไม่บันทึกหรือแก้` : ""}`;
    hrBox.className = `ick-hr-count${waiting ? " wait" : ""}`;
  }
  if (hrRead) { hrRead.textContent = ickHr.reading ? "กำลังอ่าน…" : "อ่านเรื่องพนักงาน"; hrRead.disabled = ickHr.reading || ibx.busy.has(f.id); }
  if (totals) {
    const b = ((f.preview || {}).batches || [])[0] || {};
    // รวมแถว = what will be booked; the paper's own total also holds the fuel already on the cash-fuel page, so the difference counts it
    let rows = 0, cf = 0;
    for (const pg of ickPages(f)) {
      if (!pg.money && !pg.canAdd) continue;
      for (const l of ickLinesOf(pg, ick.draft)) {
        const a = Number.isFinite(ickNum(l.v.amount)) ? ickNum(l.v.amount) : 0;
        if (l.state === "book" || l.state === "new") rows += a; else if (l.state === "cashfuel") cf += a;
      }
    }
    rows = Math.round(rows * 100) / 100; cf = Math.round(cf * 100) / 100;
    const ft = b.file_total, cfText = cf ? ` (+ มีแล้วในน้ำมันเงินสด ${ibxMoney(cf)})` : "";
    fill(totals, ft != null
      ? el("span", { class: ibxSame(ft, rows + cf) ? "ok-text" : "warn-text", text: `ยอดในไฟล์ ${ibxMoney(ft)} · รวมแถว ${ibxMoney(rows)}${cfText} · ต่าง ${ibxMoney(Math.abs(ft - rows - cf))} ฿` })
      : el("span", { class: "muted", text: `ไฟล์ไม่มียอดรวม · รวมแถว ${ibxMoney(rows)}${cfText} ฿` }));
  }
  ickSay(ick.msg ? ick.msg.text : "", ick.msg ? ick.msg.bad : false);
}

async function ickSave() {
  if (!ick.f || !ickDirty() || ick.saving) return;
  const f = ick.f;
  ick.saving = true;
  const main = $("ickMain");
  if (main) { main.inert = true; main.setAttribute("aria-busy", "true"); }      // nothing typed while it saves (a re-book takes seconds)
  ickRefresh();
  let res = null;
  await ibxRun(f.id, async () => {
    // version: the fixes this page opened with -- saved from another tab meanwhile, the server refuses instead of wiping them
    try { res = await api(`/api/accounts/inbox/${f.id}/edits`, jsonOpts("POST", { line_edits: ickPayload(ick.draft), version: ick.version })); }
    catch (err) { ick.msg = { text: err.message, bad: true }; }
  });
  ick.saving = false;
  if (main) { main.inert = false; main.removeAttribute("aria-busy"); }
  if (res) {
    const failed = ibxRebookFailed(res).map((x) => x.text);
    ick.msg = res.message ? { text: res.message, bad: true }
      : { text: (res.booked_again ? "บันทึกแล้ว · ลงบัญชีใหม่ตามที่แก้แล้ว" : "บันทึกแล้ว") + (failed.length ? ` · ${failed.join(" · ")}` : ""), bad: failed.length > 0 };
    ickTake(res);
    ickRender();
  } else ickRefresh();
}

async function ickBook() {
  const f = ick.f;
  if (!f || ickDirty()) return;
  const sup = ibxSuperseded(f);
  if (sup && !confirm(`${f.file_name} ถูกแทนที่ด้วย ${sup.by || "ไฟล์ที่ส่งทีหลัง"} แล้ว\nถ้าลงบัญชีไฟล์นี้อีกครั้ง รายการจากไฟล์นี้จะกลับไปแทนของ${sup.by ? ` ${sup.by}` : "ไฟล์นั้น"} ในบัญชี\nลงบัญชีไฟล์นี้อีกครั้งไหม?`)) return;
  let ok = false;
  await ibxRun(f.id, async () => {
    try {
      const r = await api(`/api/accounts/inbox/${f.id}/book`, { method: "POST" });
      ibx.msgs = [{ text: `${f.file_name}: ลงบัญชีแล้ว ${baht(r.booked_total || 0)} ฿${r.rebooked ? ` · ลงใหม่ ${ibxInt(r.rebooked)} ไฟล์ที่เกี่ยวข้องให้ด้วย` : ""}` }, ...ibxRebookFailed(r)];
      ok = true;
    } catch (err) { ick.msg = { text: err.message, bad: true }; }
  });
  if (ok) { ick.saved = ick.draft = null; goHash("#accounts-in"); } else ickRefresh();
}

// a question to answer (month, branch, half, replace): the dialog's choose flow, then this page again
function ickChoose() {
  const id = ick.id;
  if (ickDirty() && !confirm("มีการแก้ที่ยังไม่ได้บันทึก — ไปเลือกโดยทิ้งการแก้นี้ไหม? (กด ยกเลิก แล้วบันทึกการแก้ก่อนได้)")) return;
  ibxPreview(id).then(() => {
    const dlg = $("ibxDialog");
    if (dlg) dlg.addEventListener("close", () => { if (ick.id === id && !$("inboxCheckView").hidden) openInboxCheck(id); }, { once: true });
  });
}

// ---------------------------------------------------------------- บันทึกในพนักงาน (owner, 28 Nov 2026; app/inbox_hr.py)
// HR pages and staff loans of the paper recorded STRAIGHT AWAY, one item at a time, each by its own buttons (never by ลงบัญชี): who
// it goes to (the ไปที่ pill), the fields read from the paper (editable), what พนักงาน has now, then บันทึก / ไม่บันทึก.
const ICK_HR_KIND = { warning: "หนังสือเตือน", resign: "ลาออก", loan: "เงินกู้", loan_terms: "เปลี่ยนการผ่อน", role_change: "เปลี่ยนตำแหน่ง" };
const ICK_HR_FIELDS = {          // [field, label, input]
  warning: [["doc_date", "วันที่หนังสือ", "date"], ["offence_date", "วันที่ทำผิด", "date"], ["level", "ครั้งที่", "level"],
    ["offence", "ความผิด", "text"], ["punishment", "บทลงโทษ", "text"], ["penalty_amount", "หักเงิน (บาท)", "money"]],
  resign: [["end_date", "วันสุดท้ายที่ทำงาน", "date"], ["reason", "เหตุผล", "text"]],
  loan: [["loan_kind", "ประเภท", "loan_kind"], ["amount", "ยอดกู้ (บาท)", "money"], ["loan_date", "วันที่กู้", "date"],
    ["repay_amount", "หักต่อเดือน (บาท)", "money"], ["first_month", "เริ่มหักเดือน", "month"], ["reason", "เหตุผล", "text"]],
  loan_terms: [["repay_amount", "หักต่อเดือน (บาท)", "money"], ["first_month", "เริ่มหักเดือน", "month"]],
  role_change: [["change_date", "มีผลวันที่", "date"], ["to_position", "ตำแหน่งใหม่", "position"], ["to_branch", "สาขา", "branch"],
    ["daily_rate", "ค่าแรงต่อวัน (บาท)", "money"], ["reason", "เหตุผล", "text"]],
};
const ickHr = { drafts: {}, msgs: {}, busy: null, reading: false, people: null, loading: null };

const ickHrItems = (f) => ((f && f.hr && f.hr.items) || []);
const ickHrPendingOn = (f, n) => ickHrItems(f).filter((it) => it.page === n && it.status === "pending").length;

// which items sit under a line of the page (a 1510 loan line) and which on the page itself (a memo; a line no longer shown)
function ickHrSplit(f, pg) {
  const keys = new Set(pg.rows.map((r) => r.key).filter(Boolean));
  const mine = ickHrItems(f).filter((it) => it.page === pg.n);
  return { byLine: mine.filter((it) => it.line_key && keys.has(it.line_key)), onPage: mine.filter((it) => !(it.line_key && keys.has(it.line_key))) };
}

// a field as its input shows it: the owner's draft over what was read (a month as YYYY-MM)
function ickHrValue(it, field) {
  const d = ickHr.drafts[it.id] || {};
  const v = d[field] !== undefined ? d[field] : (it.fields || {})[field];
  if (v == null) return "";
  if (field === "first_month") return String(v).slice(0, 7);
  return String(v);
}

const ickHrEdited = (it) => Object.keys(ickHr.drafts[it.id] || {}).length > 0;

// DD/MM/YYYY (Buddhist year) HH:MM, Bangkok time
function ickHrWhen(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const t = new Date(d.getTime() + 7 * 3600 * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return `${p(t.getUTCDate())}/${p(t.getUTCMonth() + 1)}/${t.getUTCFullYear() + 543} ${p(t.getUTCHours())}:${p(t.getUTCMinutes())}`;
}

function ickHrInput(it, field, label, kind, ro) {
  const opts = (ick.f.hr || {}).options || {};
  const value = ickHrValue(it, field);
  let input;
  if (kind === "level") {
    input = el("select", {}, el("option", { value: "", text: `ตามระบบ${it.level ? ` (ครั้งที่ ${it.level})` : ""}` }),
      ...[1, 2, 3].map((n) => el("option", { value: String(n), text: `ครั้งที่ ${n}` })));
  } else if (kind === "loan_kind") {
    input = el("select", {}, ...Object.entries(opts.loan_kinds || { loan: "เงินกู้ทั่วไป" }).map(([k, t]) => el("option", { value: k, text: t })));
  } else if (kind === "position") {
    const list = opts.positions || [];
    input = el("select", {}, el("option", { value: "", text: "— เลือกตำแหน่ง —" }), ...list.map((p) => el("option", { value: p, text: p })));
    if (value && !list.includes(value)) input.append(el("option", { value, text: value }));
  } else if (kind === "branch") {
    input = el("select", {}, el("option", { value: "", text: "สาขาเดิม" }), ...(opts.branches || []).map((b) => el("option", { value: b, text: b })));
  } else {
    input = el("input", { type: kind === "money" ? "text" : kind, value, inputmode: kind === "money" ? "decimal" : null, maxlength: kind === "text" ? "300" : null });
  }
  if (input.tagName === "SELECT") input.value = value;
  input.disabled = ro;
  const go = () => {
    const d = { ...(ickHr.drafts[it.id] || {}) };
    const read = (it.fields || {})[field];
    const was = read == null ? "" : field === "first_month" ? String(read).slice(0, 7) : String(read);
    if (input.value === was) delete d[field]; else d[field] = input.value;
    if (Object.keys(d).length) ickHr.drafts[it.id] = d; else delete ickHr.drafts[it.id];
    ickHrButtons(it);
  };
  input.addEventListener("input", go);
  input.addEventListener("change", go);
  return el("label", { class: `ick-hr-f f-${kind}` }, el("span", { class: "muted small", text: label }), input);
}

// the people to pick from (/api/employees, the HR section's own list): loaded once, the first time a picker is used
async function ickHrPeople() {
  if (ickHr.people) return ickHr.people;
  if (!ickHr.loading) {
    ickHr.loading = api("/api/employees").then((d) => { ickHr.people = d.employees || []; return ickHr.people; })
      .catch((err) => { ickHr.loading = null; throw err; });
  }
  return ickHr.loading;
}

function ickHrPicker(it) {
  const listId = `ickHrPeople-${it.id.replace(/[^A-Za-z0-9_-]/g, "_")}`;
  const list = el("datalist", { id: listId });
  const input = el("input", { type: "search", list: listId, placeholder: "เปลี่ยนคน: ค้นหารหัส / ชื่อ", "aria-label": "เลือกพนักงาน", class: "ick-hr-pick" });
  const fillList = async () => {
    try {
      const people = await ickHrPeople();
      if (!list.children.length) {
        list.append(...people.map((p) => el("option", { value: p.code,
          label: `${p.display_name}${p.nickname ? ` (${p.nickname})` : ""} · ${p.position || "-"} · ${p.branch}${p.status === "resigned" ? " · ลาออกแล้ว" : ""}` })));
      }
    } catch (err) { ickHr.msgs[it.id] = { text: `โหลดรายชื่อไม่สำเร็จ: ${err.message}`, bad: true }; ickHrRepaint(it.page); }
  };
  input.addEventListener("focus", fillList, { once: true });
  input.addEventListener("change", () => {
    const code = input.value.trim().toUpperCase();
    if (!code) return;
    if (ickHr.people && !ickHr.people.some((p) => String(p.code).toUpperCase() === code)) {
      ickHr.msgs[it.id] = { text: `ไม่พบรหัส ${code} — เลือกจากรายชื่อ`, bad: true };
      ickHrRepaint(it.page);
      return;
    }
    ickHrAct(it, "person", { employee_code: code });
  });
  return el("span", { class: "ick-hr-picker" }, input, list);
}

function ickHrNote(msg) {
  return msg ? el("div", { class: msg.bad ? "warn-text small" : "ok-text small", text: msg.text }) : null;
}

function ickHrEl(it) {
  const hr = ick.f.hr || {};
  const busy = ickHr.busy === it.id;
  const box = el("div", { class: `ick-hr ${it.status}`, "data-hr": it.id });
  const head = el("div", { class: "ick-hr-head" }, el("span", { class: "badge ick-hr-kind", text: ICK_HR_KIND[it.kind] || it.kind }),
    el("strong", { text: "บันทึกในพนักงาน" }), it.summary ? el("span", { class: "muted small", text: it.summary }) : null);
  const dest = el("span", { class: `ick-dest hr${it.status === "dismissed" ? " off" : ""}`, text: it.dest || "พนักงาน" });
  if (it.status === "applied") {
    fill(box, head, dest, el("div", { class: "ick-hr-done" }, el("span", { class: "ok-text", text: `บันทึกแล้ว — ${it.result || ""}` }),
      it.applied_at ? el("span", { class: "muted small", text: ` · ${ickHrWhen(it.applied_at)}${it.applied_by ? ` · ${it.applied_by}` : ""}` }) : null));
    return box;
  }
  if (it.status === "dismissed") {
    const back = el("button", { class: "linkbtn small", type: "button", text: "เอากลับมา", disabled: busy || hr.access !== "ok", onclick: () => ickHrAct(it, "reopen") });
    fill(box, head, dest, el("div", { class: "ick-hr-off muted" }, "ไม่บันทึก · ", back), ickHrNote(ickHr.msgs[it.id]));
    return box;
  }
  if (hr.access === "none") {
    fill(box, head, dest, el("p", { class: "muted small", text: it.block || "บันทึกเรื่องพนักงานได้เฉพาะผู้ที่มีเมนูรายชื่อพนักงาน" }));
    return box;
  }
  if (hr.access === "locked") {
    const pin = el("input", { type: "password", inputmode: "numeric", autocomplete: "off", placeholder: "PIN ส่วนพนักงาน", "aria-label": "PIN ส่วนพนักงาน" });
    const go = el("button", { class: "btn small", type: "button", text: "ปลดล็อก", onclick: () => ickHrUnlock(pin.value.trim()) });
    pin.addEventListener("keydown", (e) => { if (e.key === "Enter") ickHrUnlock(pin.value.trim()); });
    fill(box, head, dest, el("div", { class: "ick-hr-lock" }, el("span", { class: "muted small", text: "ส่วนพนักงานล็อกอยู่ — ใส่ PIN เพื่อดูและบันทึก" }), pin, go));
    return box;
  }
  const e = it.employee;
  const who = el("div", { class: "ick-hr-who" }, el("span", { class: "muted small", text: "คน: " }),
    e ? el("strong", { text: `${e.name} (${e.code})` }) : el("strong", { class: "warn-text", text: `ยังไม่ได้เลือก${it.employee_name_read ? ` — ในกระดาษ: ${it.employee_name_read}` : ""}` }),
    e ? el("span", { class: "muted small", text: ` ${e.position || ""} · ${e.branch || ""}${e.status === "resigned" ? " · ลาออกแล้ว" : ""}` }) : null,
    ickHrPicker(it));
  const fields = el("div", { class: "ick-hr-fields" }, ...(ICK_HR_FIELDS[it.kind] || []).map(([f, label, kind]) => ickHrInput(it, f, label, kind, busy)));
  const now = el("div", { class: "ick-hr-now small" }, el("span", { class: "muted", text: "ในระบบตอนนี้: " }), it.now || "—");
  const save = el("button", { class: "btn small primary", type: "button", "data-hr-save": it.id, text: busy ? "กำลังบันทึก…" : "บันทึก", onclick: () => ickHrApply(it) });
  const skip = el("button", { class: "btn small", type: "button", text: "ไม่บันทึก", disabled: busy, onclick: () => ickHrAct(it, "dismiss") });
  const why = el("span", { class: "ick-hr-why small", "data-hr-why": it.id });
  fill(box, head, dest, who, it.mismatch ? el("div", { class: "warn-text small", text: it.mismatch }) : null, fields, now,
    el("div", { class: "ick-hr-act" }, save, skip, why), ickHrNote(ickHr.msgs[it.id]));
  ickHrButtons(it, box);
  return box;
}

// บันทึก is off, with the reason beside it, while the item would duplicate what is there (or nobody is picked) and nothing was changed
function ickHrButtons(it, root) {
  const scope = root || [...document.querySelectorAll("#inboxCheckView [data-hr]")].find((x) => x.dataset.hr === it.id);
  if (!scope) return;
  const save = scope.querySelector("[data-hr-save]"), why = scope.querySelector("[data-hr-why]");
  const blocked = !!it.block && (!ickHrEdited(it) || !it.employee);
  if (save) { save.disabled = blocked || ickHr.busy === it.id; save.title = blocked ? it.block : ""; }
  if (why) why.textContent = blocked ? it.block : ickHrEdited(it) ? "แก้แล้ว — กดบันทึกเพื่อใช้ค่าที่แก้" : "";
}

function ickHrApply(it) {
  ickHrAct(it, "apply", { fields: { ...(ickHr.drafts[it.id] || {}) }, employee_code: it.employee_code || null });
}

async function ickHrAct(it, what, body) {
  if (ickHr.busy || !ick.f) return;
  const f = ick.f;
  ickHr.busy = it.id;
  delete ickHr.msgs[it.id];
  ickHrRepaint(it.page);
  let res = null;
  await ibxRun(f.id, async () => {
    try { res = await api(`/api/accounts/inbox/${f.id}/hr/${encodeURIComponent(it.id)}/${what}`, jsonOpts("POST", body || {})); }
    catch (err) { ickHr.msgs[it.id] = { text: err.status === 423 ? "ส่วนพนักงานล็อกอยู่ — ใส่ PIN ก่อน" : err.message, bad: true }; }
  });
  ickHr.busy = null;
  if (res && ick.f === f) {
    if (what === "apply") delete ickHr.drafts[it.id];   // picking the person keeps the fields the owner corrected
    ickHrTake(res);
  }
  ickHrRepaint(it.page);
  ickRefresh();
}

// a reply to an HR action: only the HR part of the file changes (line fixes waiting on the screen stay as they are)
function ickHrTake(res) {
  ick.f = { ...ick.f, hr: res.hr, ai_usage: res.ai_usage, preview: { ...(ick.f.preview || {}), hr_items: (res.preview || {}).hr_items } };
}

function ickHrRepaint(n) {
  if (!ick.f) return;
  if (ick.page === "all") { ickRender(); return; }
  ickRepaint(n);
  const strip = document.querySelector("#inboxCheckView .ick-strip");
  if (strip) { ick.stripX = strip.scrollLeft; strip.replaceWith(ickStrip(ickPages(ick.f))); ickStripRestore(); }
}

async function ickHrUnlock(pin) {
  if (!pin) return;
  saveHrPin(pin);
  try { ickHrTake(await api(`/api/accounts/inbox/${ick.id}`)); }
  catch (err) { ickSay(err.message, true); return; }
  ick.msg = ick.f.hr && ick.f.hr.access === "locked" ? { text: "PIN ไม่ถูกต้อง", bad: true } : null;
  ickRender();
}

async function ickHrRead() {
  const f = ick.f;
  if (!f || ickHr.reading) return;
  ickHr.reading = true;
  ickRefresh();
  await ibxRun(f.id, async () => {
    try {
      const res = await api(`/api/accounts/inbox/${f.id}/hr/read`, { method: "POST" });
      if (ick.f === f) { ickHrTake(res); ick.msg = { text: `อ่านเรื่องพนักงานแล้ว: ${ickHrItems(ick.f).length} รายการ`, bad: false }; }
    } catch (err) { ick.msg = { text: err.message, bad: true }; }
  });
  ickHr.reading = false;
  ickRender();
}

// ทุกหน้า: every item of the file in one list, each with the page it is on
function ickHrAll(f) {
  const items = ickHrItems(f);
  if (!items.length) return null;
  const st = { pending: "รอบันทึก", applied: "บันทึกแล้ว", dismissed: "ไม่บันทึก" };
  return el("section", { class: "ick-hr-all", id: "ickHrAll" }, el("h3", { text: `เรื่องพนักงานในไฟล์นี้ (${items.length})` }),
    el("ul", {}, ...items.map((it) => el("li", { class: `s-${it.status}` },
      el("button", { class: "linkbtn small", type: "button", text: `หน้า ${it.page}`, onclick: () => { ick.page = it.page; ickRender(); window.scrollTo(0, 0); } }),
      el("span", { class: "badge ick-hr-kind", text: ICK_HR_KIND[it.kind] || it.kind }),
      el("span", { class: "ick-hr-all-dest", text: (it.dest || "").replace(/^พนักงาน · /, "") }),
      el("span", { class: `ick-hr-st ${it.status}${it.status === "pending" && it.block ? " stuck" : ""}`,
        text: it.status === "pending" && it.block && (f.hr || {}).access === "ok" ? it.block : st[it.status] || it.status })))));
}
