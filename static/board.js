"use strict";

// Repair board: follow-up on repair requests from the paper ใบแจ้งซ่อม log.
// The paper stays the master; only what you add here is tracked. The state
// rules live on the server (app/repair_requests.py) -- this file only shows
// the right buttons for each status and sends the chosen action.
//
// Loaded BEFORE app.js, so nothing here may run at load time: it relies on
// helpers from app.js (state, $, el, api, showView, ...) at call time only.

const board = {
  active: [],      // open + at_shop + awaiting_bill, from /api/requests?scope=active
  recent: [],      // completed / cancelled, newest first
  fixers: [],      // names/shops seen before (suggestions)
  shops: [],       // the shops you send trucks to (server list: app/shops.py)
  drag: null,      // the card being dragged between columns
  attached: [],    // request ids the bill being entered will complete
  attachedInfo: null,
};

// Days before a request starts to look neglected (amber, then red).
const AGE = { open: { warn: 7, bad: 14 }, at_shop: { warn: 7, bad: 14 }, awaiting_bill: { warn: 3, bad: 7 } };

// label + badge colour of each step, used wherever a request is shown
const REQ_STATUS = { open: ["รอซ่อม", "urgent"], at_shop: ["อยู่ที่อู่", "shop"], awaiting_bill: ["รอบิล", "warn"] };
const reqStatusBadge = (status) => el("span", { class: `badge ${(REQ_STATUS[status] || [])[1] || ""}`.trim(), text: (REQ_STATUS[status] || [status])[0] });

const addDaysISO = (iso, n) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return d.toLocaleDateString("sv-SE");
};

// --------------------------------------------------------------- helpers
const todayISO = () => new Date().toLocaleDateString("sv-SE");   // YYYY-MM-DD in the viewer's timezone

function daysSince(iso) {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
}

// ---- Dates. The database and every <input type=date> value use the standard
// (ค.ศ.) ISO form, e.g. 2026-10-20. Everything shown to a person uses the
// Buddhist year in full -- "20 ต.ค. 2569" -- like the bills themselves, never
// a 2-digit year. Done by hand, not with Intl, so it reads the same on every
// phone and browser regardless of its language settings.
const TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];

function beDate(iso) {                       // "2026-10-20" (or an ISO timestamp's date part) -> "20 ต.ค. 2569"
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${+m[3]} ${TH_MONTHS[+m[2] - 1]} ${+m[1] + 543}` : "";
}

function beDateOf(dateObj) {                 // a JS Date, in the viewer's own timezone
  return `${dateObj.getDate()} ${TH_MONTHS[dateObj.getMonth()]} ${dateObj.getFullYear() + 543}`;
}

const beMonth = (ym) => `${TH_MONTHS[+ym.slice(5, 7) - 1]} ${+ym.slice(0, 4) + 543}`;   // "2026-10" -> "ต.ค. 2569"

// Typed a พ.ศ. year into a ค.ศ. field (2569 instead of 2026)? Fix it. Returns the ISO date to use.
function fixBuddhistYear(iso) {
  const m = /^(\d{4})-(\d{2}-\d{2})$/.exec(iso || "");
  return m && +m[1] >= 2400 ? `${+m[1] - 543}-${m[2]}` : iso;
}

// The line under a date field that spells out both calendars, so there is never any doubt which year it is.
function dateCaption(iso) {
  const m = /^(\d{4})-/.exec(iso || "");
  return m && +m[1] >= 1900 ? `${beDate(iso)} (พ.ศ.) · ค.ศ. ${+m[1]}` : "";
}

// Wires a date input + its caption: auto-corrects a พ.ศ. year, keeps the caption live. Returns true if it corrected.
function bindDateField(input, captionEl) {
  const refresh = () => {
    const fixed = fixBuddhistYear(input.value);
    const corrected = fixed !== input.value;
    if (corrected) input.value = fixed;
    captionEl.textContent = dateCaption(input.value) + (corrected ? " — แปลงจาก พ.ศ. เป็น ค.ศ. ให้แล้ว" : "");
  };
  input.addEventListener("input", refresh);
  input.addEventListener("change", refresh);
  captionEl.textContent = dateCaption(input.value);
}

const thDate = beDate;

// A few trucks have no fleet number of their own -- the "number" is just the plate. Only then is it left out.
// (Not a substring test: truck #1 must not vanish because "1" appears in its plate.)
const squash = (t) => String(t || "").replace(/[\s\-_.]/g, "");
const numberIsPlate = (r) => !!r.truck_number && !!r.plate && squash(r.truck_number) === squash(r.plate);

// The truck's own nickname (หัวขาว ...): carried on the row itself, or looked up by vehicle id from the fleet list.
function nicknameOf(r) {
  if (r.nickname) return r.nickname;
  const v = r.vehicle_id && (state.vehicles || []).find((x) => x.id === r.vehicle_id);
  return (v && v.nickname) || "";
}

// The truck, as the heading of a card. The blue chip is its fleet number; a truck with no number of its own
// (the "number" is just its plate) shows its nickname there instead, and one with both keeps the nickname beside the plate.
function truckHead(r) {
  const hasNumber = !!r.truck_number && !numberIsPlate(r), nick = nicknameOf(r);
  const chip = hasNumber ? el("span", { class: "truckno", title: "เบอร์รถ", text: `#${r.truck_number}` })
    : nick ? el("span", { class: "truckno", title: "ชื่อเรียกรถ", text: nick }) : null;
  return [
    chip,
    el("strong", { class: "truckplate", text: r.plate || (r.truck_number && !hasNumber ? r.truck_number : "ไม่ระบุรถ") }),
    hasNumber && nick ? el("span", { class: "muted small", text: nick }) : null,
  ].filter(Boolean);
}

// Some trucks have no separate fleet number (it is just their plate): don't print it twice, and use the nickname if it has one.
function truckLabel(r) {
  const hasNumber = !!r.truck_number && !numberIsPlate(r);
  return [hasNumber ? `#${r.truck_number}` : nicknameOf(r), r.plate].filter(Boolean).join(" ") || "ไม่ระบุรถ";
}

// A phone number as a tap-to-call button (tel: link). `label` is the short text; the number is shown after it unless compact.
const telHref = (phone) => `tel:${String(phone || "").replace(/[^\d+]/g, "")}`;

function callLink(phone, label = "โทร", compact = false) {
  return el("a", { class: "btn small call", href: telHref(phone), title: `โทร ${phone}` },
    el("span", { text: `📞 ${label}` }), compact ? null : el("span", { class: "callnum", text: phone }));
}

// The main driver of a truck (with a number to call), from the fleet list -- null when there is none or it has no number.
function driverFor(vehicleId) {
  const v = (fleet.vehicles || []).find((x) => x.vehicle_id === vehicleId);
  return v && v.driver && v.driver_phone ? { name: v.driver, nickname: v.driver_nickname, phone: v.driver_phone } : null;
}

function jsonOpts(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function ageChip(days, kind) {
  const t = AGE[kind];
  const level = days >= t.bad ? "bad" : days >= t.warn ? "warn" : "";
  return el("span", { class: `age ${level}`.trim(), text: days === 0 ? "วันนี้" : `${days} วัน` });
}

function showBoardError(text) {
  const n = $("boardError");
  n.hidden = !text;
  n.textContent = text || "";
}

// --------------------------------------------------------------- loading
async function loadBoard() {
  try {
    const [active, recent] = await Promise.all([api("/api/requests?scope=active"), api("/api/requests?scope=recent"), ensureVehicles()]);
    board.active = active;
    board.recent = recent;
    showBoardError("");
  } catch (err) {
    showBoardError(`โหลดงานซ่อมไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderBoard();
  renderFixes();
  if (typeof renderManualFixes === "function") renderManualFixes();
  if (typeof renderWhere === "function") renderWhere();
}

// --------------------------------------------------------------- rendering
function renderBoard() {
  const open = board.active.filter((r) => r.status === "open");
  const atShop = board.active
    .filter((r) => r.status === "at_shop")
    .sort((a, b) => atShopDays(b) - atShopDays(a));          // away longest first
  const awaiting = board.active
    .filter((r) => r.status === "awaiting_bill")
    .sort((a, b) => b.days_in_status - a.days_in_status);   // waiting longest first

  $("countOpen").textContent = open.length ? `(${open.length})` : "";
  $("countAtShop").textContent = atShop.length ? `(${atShop.length})` : "";
  $("countAwaiting").textContent = awaiting.length ? `(${awaiting.length})` : "";
  $("openList").replaceChildren(...(open.length ? open.map((r) => requestCard(r, "open")) : [emptyNote("ไม่มีงานรอซ่อม")]));
  $("atShopList").replaceChildren(...(atShop.length ? atShop.map((r) => requestCard(r, "at_shop")) : [emptyNote("ไม่มีรถที่อยู่ที่อู่")]));
  $("awaitingList").replaceChildren(...(awaiting.length ? awaiting.map((r) => requestCard(r, "awaiting_bill")) : [emptyNote("ไม่มีงานที่รอบิล")]));
  $("recentList").replaceChildren(...(board.recent.length ? board.recent.map(recentCard) : [emptyNote("ยังไม่มีงานที่ปิดแล้ว")]));
  $("recentBox").querySelector("summary").textContent = `ปิดแล้วล่าสุด${board.recent.length ? ` (${board.recent.length})` : ""}`;
}

const emptyNote = (text) => el("p", { class: "muted small empty", text });

const atShopDays = (r) => (r.days_at_shop ?? (r.sent_date ? daysSince(r.sent_date) : 0));

// แจ้ง -> ส่งอู่ -> กลับมา -> บิล: how far this request has got
function stageRail(status) {
  const reached = { open: 0, at_shop: 1, awaiting_bill: 2 }[status] ?? 0;
  return el("div", { class: "rail", "aria-hidden": "true" },
    ...["แจ้ง", "ส่งอู่", "กลับมา", "บิล"].map((t, i) => el("span", { class: `${i <= reached ? "on" : ""}${i === reached ? " cur" : ""}`.trim(), text: t })));
}

function requestCard(r, kind) {
  const days = kind === "open" ? daysSince(r.request_date) : kind === "at_shop" ? atShopDays(r) : r.days_in_status;
  const overdue = kind === "at_shop" && r.expected_return && r.expected_return < todayISO() ? daysSince(r.expected_return) : 0;
  const meta = kind === "open"
    ? [r.reported_by ? `ผู้แจ้ง ${r.reported_by}` : null, `แจ้งเมื่อ ${thDate(r.request_date)}`]
    : kind === "at_shop"
      ? [`ส่ง ${r.shop} เมื่อ ${thDate(r.sent_date)}`, r.expected_return ? `คาดว่ากลับ ${thDate(r.expected_return)}` : null, `แจ้งเมื่อ ${thDate(r.request_date)}`]
      : [`ซ่อมโดย ${r.fixed_by || "-"}`, `เมื่อ ${thDate(r.fixed_date)}`, r.reported_by ? `ผู้แจ้ง ${r.reported_by}` : null];

  const small = (text, title, fn, cls = "") => el("button", { class: `btn small ${cls}`.trim(), type: "button", title, text, onclick: fn });
  const story = small("ขั้นตอน", "ดูเส้นทางของงานนี้", () => showBoardRequest(r));
  const actions = kind === "open"
    ? [
        el("button", { class: "btn primary", type: "button", text: "ส่งซ่อม →", onclick: () => openSendDialog(r) }),
        el("button", { class: "btn", type: "button", title: "ซ่อมเอง หรือซ่อมเสร็จแล้วโดยไม่ได้บันทึกว่าส่งที่ไหน", text: "ซ่อมแล้ว ✓", onclick: () => openFixDialog(r) }),
        small("แก้ไข", "แก้ไขงาน เช่น เปลี่ยนรถ", () => openEditRequest(r)),
        small(r.urgent ? "เลิกด่วน" : "ด่วน", "สลับสถานะด่วน", () => patchRequest(r.request_id, { urgent: !r.urgent })),
        small("ยกเลิก", "ยกเลิกงานนี้", () => cancelRequest(r), "danger"),
      ]
    : kind === "at_shop"
      ? [
          el("button", { class: "btn primary", type: "button", text: "รถกลับมาแล้ว ✓", onclick: () => openReturnDialog(r) }),
          small("เปลี่ยนอู่ / วันที่", "แก้ไขข้อมูลการส่งซ่อม", () => openSendDialog(r)),
          small("แก้ไข", "แก้ไขงาน เช่น เปลี่ยนรถ", () => openEditRequest(r)),
          small("← เรียกรถคืน", "ยังไม่ได้ส่งจริง กลับไปเป็นรอซ่อม", () => recallFromShop(r)),
          small("ยกเลิก", "ยกเลิกงานนี้", () => cancelRequest(r), "danger"),
        ]
      : [
          el("button", { class: "btn primary", type: "button", text: "เพิ่มบิล →", onclick: () => startBillFor(r) }),
          el("button", { class: "btn", type: "button", text: "ไม่มีบิล", onclick: () => openNoBillDialog(r) }),
          small("← ย้อนกลับ", r.sent_date ? "กลับไปเป็นอยู่ที่อู่" : "กลับไปเป็นรอซ่อม", () => stepBack(r)),
          small("แก้ไข", "แก้ไขงาน เช่น เปลี่ยนรถ", () => openEditRequest(r)),
          small("ยกเลิก", "ยกเลิกงานนี้", () => cancelRequest(r), "danger"),
        ];

  const card = el("div", { class: `req${r.urgent ? " urgent" : ""}${overdue ? " late" : ""}`, "data-id": r.request_id },
    el("div", { class: "req-head" },
      ...truckHead(r),
      r.branch ? el("span", { class: "muted small", text: r.branch }) : null,
      r.urgent ? el("span", { class: "badge urgent", text: "ด่วน" }) : null,
      overdue ? el("span", { class: "badge urgent", text: `เลยกำหนด ${overdue} วัน` }) : null,
      ageChip(days, kind)),
    el("p", { class: "req-problem", text: r.problem }),
    el("p", { class: "muted small", text: meta.filter(Boolean).join(" · ") }),
    stageRail(kind),
    el("div", { class: "req-actions" }, ...actions, story, callDriver(r)));
  wireDrag(card, r);
  return card;
}

function callDriver(r) {
  const d = driverFor(r.vehicle_id);
  return d ? callLink(d.phone, `โทร ${d.nickname || d.name}`, true) : null;
}

// The timeline dialog lives with the maintenance log (logbook.js); a board row has the same fields.
function showBoardRequest(r) {
  showRequestDialog({ ...r, shop_raw: r.shop || r.fixed_by, status_label: (REQ_STATUS[r.status] || [r.status])[0] });
}

// ---- moving cards: drag to the next column (desktop) or use the buttons (phone) -- both end in the same dialogs
function wireDrag(card, r) {
  card.draggable = true;
  card.addEventListener("dragstart", (e) => {
    board.drag = r;
    e.dataTransfer.setData("text/plain", r.request_id);
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
    document.querySelector(".kanban").classList.add("dragging");
  });
  card.addEventListener("dragend", () => {
    board.drag = null;
    card.classList.remove("dragging");
    document.querySelector(".kanban").classList.remove("dragging");
    document.querySelectorAll(".kcol.dragover").forEach((c) => c.classList.remove("dragover"));
  });
}

function wireColumns() {
  // a long board: scroll the page when the card is held near the top or bottom edge
  document.addEventListener("dragover", (e) => {
    if (!board.drag) return;
    if (e.clientY < 90) window.scrollBy(0, -20);
    else if (e.clientY > window.innerHeight - 90) window.scrollBy(0, 20);
  });
  document.querySelectorAll(".kcol").forEach((col) => {
    col.addEventListener("dragover", (e) => { if (board.drag) { e.preventDefault(); col.classList.add("dragover"); } });
    col.addEventListener("dragleave", (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove("dragover"); });
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("dragover");
      const r = board.drag;
      board.drag = null;
      if (r) moveTo(r, col.dataset.status);
    });
  });
}

function moveTo(r, target) {
  if (r.status === target) return;
  if (r.status === "open" && target === "at_shop") return openSendDialog(r);
  if (r.status === "open" && target === "awaiting_bill") return openFixDialog(r);
  if (r.status === "at_shop" && target === "awaiting_bill") return openReturnDialog(r);
  if (r.status === "at_shop" && target === "open") return recallFromShop(r);
  if (r.status === "awaiting_bill" && target === "at_shop" && r.sent_date) return stepBack(r);
  showBoardError("ย้ายข้ามขั้นแบบนี้ไม่ได้ — ย้ายไปทีละขั้น (รอซ่อม → อยู่ที่อู่ → รอบิล) หรือใช้ปุ่มในการ์ด");
}

function recentCard(r) {
  const badges = [];
  if (r.status === "cancelled") badges.push(el("span", { class: "badge", text: "ยกเลิก" }));
  else if (r.no_bill) badges.push(el("span", { class: "badge nobill", text: "ปิดโดยไม่มีบิล" }));
  else badges.push(el("span", { class: "badge ok", text: r.has_record ? "ปิดแล้ว · มีบิล/บันทึก" : "ปิดแล้ว" }));
  const meta = [
    r.sent_date ? `ส่ง ${r.shop} เมื่อ ${thDate(r.sent_date)}` : null,
    r.fixed_by && r.fixed_by !== r.shop ? `ซ่อมโดย ${r.fixed_by}` : null,
    r.fixed_date ? `เมื่อ ${thDate(r.fixed_date)}` : null,
    r.no_bill && r.no_bill_reason ? `เหตุผล: ${r.no_bill_reason}` : null,
  ].filter(Boolean).join(" · ");
  return el("div", { class: "req done" },
    el("div", { class: "req-head" },
      ...truckHead(r),
      ...badges),
    el("p", { class: "req-problem", text: r.problem }),
    meta ? el("p", { class: "muted small", text: meta }) : null,
    r.has_record ? null : el("div", { class: "req-actions" },
      el("button", { class: "btn small", type: "button", text: "← ย้อนกลับเป็นรอซ่อม", onclick: () => reopenRecent(r) })));
}

// --------------------------------------------------------------- actions
// Any server answer of 409 means the request changed under us (another tab,
// a double click): refresh instead of showing a stale board.
async function runAction(id, body) {
  try {
    await api(`/api/requests/${id}/action`, jsonOpts("POST", body));
    await loadBoard();
    return null;
  } catch (err) {
    if (err.status === 409) { await loadBoard(); showBoardError(err.message); return null; }
    return err.message;
  }
}

async function patchRequest(id, body) {
  try {
    await api(`/api/requests/${id}`, jsonOpts("PATCH", body));
    await loadBoard();
  } catch (err) {
    showBoardError(err.message);
  }
}

// ---- "แก้ไข": fix a mistake on a request (wrong truck, typo, wrong date ...)
let editTarget = null;

function openEditRequest(r) {
  editTarget = r;
  $("reSummary").textContent = `${truckLabel(r)} · ${r.problem}`;
  const idx = state.vehicles.findIndex((v) => v.id === r.vehicle_id);
  fillVehicleSelect($("reTruck"), idx >= 0 ? idx : "");
  $("reProblem").value = r.problem;
  $("reReporter").value = r.reported_by || "";
  $("reDate").max = todayISO();
  $("reDate").value = r.request_date;
  $("reDateCap").textContent = dateCaption($("reDate").value);
  $("reUrgent").checked = !!r.urgent;
  $("reError").hidden = true;
  $("reqEditDialog").showModal();
}

async function saveEditRequest() {
  const r = editTarget, veh = selectedVehicle($("reTruck"));
  if (!veh) { $("reError").textContent = "เลือกรถด้วย"; $("reError").hidden = false; return; }
  const body = {};
  if (veh.id !== r.vehicle_id) body.vehicle_id = veh.id;
  if ($("reProblem").value.trim() !== r.problem) body.problem = $("reProblem").value.trim();
  if ($("reReporter").value.trim() !== (r.reported_by || "")) body.reported_by = $("reReporter").value.trim();
  if ($("reDate").value !== r.request_date) body.request_date = $("reDate").value;
  if ($("reUrgent").checked !== !!r.urgent) body.urgent = $("reUrgent").checked;
  if (!Object.keys(body).length) { $("reqEditDialog").close(); return; }
  if (body.vehicle_id && !confirm(`ย้ายงานนี้จาก ${truckLabel(r)} ไปที่ ${truckLabel({ truck_number: veh.truck_number, plate: veh.plate })} ใช่ไหม?`)) return;
  try {
    await api(`/api/requests/${r.request_id}`, jsonOpts("PATCH", body));
  } catch (err) {
    $("reError").textContent = err.message;
    $("reError").hidden = false;
    return;
  }
  $("reqEditDialog").close();
  await loadBoard();
}

async function cancelRequest(r) {
  if (!confirm(`ยกเลิกงาน (${truckLabel(r)}) ใช่ไหม?\n${r.problem}`)) return;
  const err = await runAction(r.request_id, { action: "cancel" });
  if (err) showBoardError(err);
}

async function reopenRecent(r) {
  if (!confirm(`ย้อนงานนี้กลับไป${r.sent_date ? "เป็นอยู่ที่อู่" : "เป็นรอซ่อม"}ใช่ไหม?`)) return;
  const err = await runAction(r.request_id, { action: "reopen" });
  if (err) showBoardError(err);
}

// undo one step: waiting-for-bill -> back at the shop (or -> waiting, if it never went to one)
async function stepBack(r) {
  const err = await runAction(r.request_id, { action: "reopen" });
  if (err) showBoardError(err);
}

// "we haven't actually sent it" / the shop sent it back unfixed
async function recallFromShop(r) {
  if (!confirm(`เรียกรถ (${truckLabel(r)}) คืนจาก ${r.shop} กลับไปเป็นรอซ่อมใช่ไหม?`)) return;
  const err = await runAction(r.request_id, { action: "reopen" });
  if (err) showBoardError(err);
}

// ---- "ส่งซ่อม": which shop, since when, expected back
let shopTarget = null;

function openSendDialog(r) {
  shopTarget = r;
  $("shopTitle").textContent = r.status === "at_shop" ? "แก้ไขการส่งซ่อม" : "ส่งซ่อมที่…";
  $("shopOk").textContent = r.status === "at_shop" ? "บันทึก" : "ส่งซ่อม";
  $("shopSummary").textContent = `${truckLabel(r)} · ${r.problem}`;
  $("shopName").value = r.shop || "";
  $("shopDate").min = r.request_date;
  $("shopDate").max = todayISO();
  $("shopDate").value = r.sent_date || todayISO();
  $("shopDateCap").textContent = dateCaption($("shopDate").value);
  $("shopBack").value = r.expected_return || "";
  $("shopBack").min = $("shopDate").value;
  $("shopBackCap").textContent = dateCaption($("shopBack").value);
  $("shopError").hidden = true;
  const names = [...new Set([...board.shops, ...board.fixers])];
  $("shopList").replaceChildren(...names.map((n) => el("option", { value: n })));
  $("shopChips").replaceChildren(...board.shops.map((n) => el("button", { class: "chipbtn", type: "button", text: n, onclick: () => { $("shopName").value = n; renderShopCalls(); } })));
  renderShopCalls();
  $("shopBackChips").replaceChildren(...[3, 7, 14].map((n) => el("button", { class: "chipbtn", type: "button", text: `อีก ${n} วัน`, onclick: () => {
    $("shopBack").value = addDaysISO($("shopDate").value || todayISO(), n);
    $("shopBackCap").textContent = dateCaption($("shopBack").value);
  } })));
  $("shopDialog").showModal();
}

// Under the shop name: who to call there (the numbers are kept in app/shops.py).
function renderShopCalls() {
  const typed = $("shopName").value.trim().toLowerCase().replace(/\s+/g, "");
  const dir = board.shopContacts || {};
  const key = Object.keys(dir).find((k) => k.toLowerCase().replace(/\s+/g, "") === typed)
    || (typed.length >= 3 ? Object.keys(dir).find((k) => k.toLowerCase().replace(/\s+/g, "").includes(typed)) : null);
  const info = key ? dir[key] : null;
  fill($("shopCalls"), ...(info ? [el("span", { class: "muted small", text: info.contacts.length ? `โทรหา ${info.full_name}:` : `${info.full_name} — ยังไม่มีเบอร์` }),
    ...info.contacts.map((c) => callLink(c.phone, c.label))] : []));
}

async function submitShop() {
  const err = await runAction(shopTarget.request_id, {
    action: "send_to_shop", shop: $("shopName").value.trim(), sent_date: $("shopDate").value || null, expected_return: $("shopBack").value || null,
  });
  if (err) {
    $("shopError").textContent = err;
    $("shopError").hidden = false;
    return;
  }
  $("shopDialog").close();
}

// ---- "รถกลับมาแล้ว": when it came back (the days away are shown as you pick the date)
let returnTarget = null;

function updateReturnDays() {
  const r = returnTarget, back = $("returnDate").value;
  if (!r || !back) { $("returnDays").textContent = ""; return; }
  const days = Math.round((new Date(`${back}T00:00:00`) - new Date(`${r.sent_date}T00:00:00`)) / 86400000);
  $("returnDays").textContent = days >= 0 ? `อยู่ที่ ${r.shop} ${days} วัน (ส่ง ${thDate(r.sent_date)})` : "วันที่รถกลับมาต้องไม่ก่อนวันที่ส่งซ่อม";
}

function openReturnDialog(r) {
  returnTarget = r;
  $("returnSummary").textContent = `${truckLabel(r)} · ${r.problem} — ที่ ${r.shop}`;
  $("returnDate").min = r.sent_date;
  $("returnDate").max = todayISO();
  $("returnDate").value = todayISO();
  $("returnDateCap").textContent = dateCaption($("returnDate").value);
  $("returnReason").value = "";
  $("returnError").hidden = true;
  updateReturnDays();
  $("returnDialog").showModal();
}

async function submitReturn(action) {
  const err = await runAction(returnTarget.request_id, { action, fixed_date: $("returnDate").value || null, reason: $("returnReason").value.trim() });
  if (err) {
    $("returnError").textContent = err;
    $("returnError").hidden = false;
    return;
  }
  $("returnDialog").close();
}

// ---- "ซ่อมแล้ว": who fixed it, then bill / no bill
let fixTarget = null;

function openFixDialog(r) {
  fixTarget = r;
  $("fixSummary").textContent = `${truckLabel(r)} · ${r.problem}`;
  $("fixBy").value = "";
  $("fixDate").value = todayISO();
  $("fixDate").min = r.request_date;
  $("fixDate").max = todayISO();
  $("fixDateCap").textContent = dateCaption($("fixDate").value);
  $("fixReason").value = "";
  $("fixError").hidden = true;
  $("fixerList").replaceChildren(...board.fixers.map((n) => el("option", { value: n })));
  $("fixChips").replaceChildren(...board.fixers.slice(0, 8).map((n) =>
    el("button", { class: "chipbtn", type: "button", text: n, onclick: () => { $("fixBy").value = n; } })));
  $("fixDialog").showModal();
}

async function submitFix(action) {
  const err = await runAction(fixTarget.request_id, {
    action,
    fixed_by: $("fixBy").value.trim(),
    fixed_date: $("fixDate").value || null,
    reason: $("fixReason").value.trim(),
  });
  if (err) {
    $("fixError").textContent = err;
    $("fixError").hidden = false;
    return;
  }
  $("fixDialog").close();
}

// ---- "ไม่มีบิล" for something already waiting on a bill
let noBillTarget = null;

function openNoBillDialog(r) {
  noBillTarget = r;
  $("noBillSummary").textContent = `${truckLabel(r)} · ${r.problem}`;
  $("noBillReason").value = "";
  $("noBillError").hidden = true;
  $("noBillDialog").showModal();
}

async function submitNoBill() {
  const err = await runAction(noBillTarget.request_id, { action: "no_bill", reason: $("noBillReason").value.trim() });
  if (err) {
    $("noBillError").textContent = err;
    $("noBillError").hidden = false;
    return;
  }
  $("noBillDialog").close();
}

// --------------------------------------------------------------- adding
// A clean slate after every add: a second report for the same truck straight away is rare, so the truck is
// cleared too and the cursor goes back to the truck picker.
function resetAddForm() {
  $("addVehicle").value = "";
  delete $("addReporter").dataset.typed;
  $("addProblem").value = "";
  $("addReporter").value = "";
  $("addUrgent").checked = false;
  $("addShop").value = "";
  $("addDate").value = todayISO();
  $("addDateCap").textContent = dateCaption($("addDate").value);
  $("addVehicle").focus();
}

async function submitNewRequest(e) {
  e.preventDefault();
  const veh = selectedVehicle($("addVehicle"));
  const problem = $("addProblem").value.trim();
  const err = $("addError");
  if (!veh) { err.textContent = "ยังไม่ได้เลือกรถ"; err.hidden = false; return; }
  if (!problem) { err.textContent = "ใส่ปัญหาที่ต้องซ่อมด้วย"; err.hidden = false; return; }
  err.hidden = true;
  // Another unfinished job on this truck is allowed (a truck can have two problems) but worth a second look.
  const already = board.active.filter((r) => r.vehicle_id === veh.id);
  if (already.length && !confirm(`รถคันนี้มีงานที่ยังไม่ปิดอยู่แล้ว ${already.length} งาน:\n${already.map((r) => `• ${r.problem}`).join("\n")}\n\nเพิ่มงานนี้อีกงานใช่ไหม?`)) return;
  const submit = $("addForm").querySelector("button[type=submit]");
  if (submit.disabled) return;                               // a double click or a second Enter while the first is still saving
  submit.disabled = true;
  try {
    await api("/api/requests", jsonOpts("POST", {
      vehicle_id: veh.id, problem, reported_by: $("addReporter").value.trim() || null, urgent: $("addUrgent").checked,
      shop: $("addShop").value || null, request_date: $("addDate").value || null,
    }));
    resetAddForm();
    await loadBoard();
  } catch (e2) {
    err.textContent = e2.message;
    err.hidden = false;
  } finally {
    submit.disabled = false;
  }
}

// --------------------------------------------------------------- bill hand-off
// "เพิ่มบิล →": jump to the bill page with this truck chosen and this request
// attached, so saving the bill completes it.
function startBillFor(r) {
  board.attached = [r.request_id];
  board.attachedInfo = r;
  const idx = state.vehicles.findIndex((v) => v.id === r.vehicle_id);
  $("vehicleSelect").value = idx >= 0 ? String(idx) : "";
  renderAttachNote();
  showView("capture");
}

function renderAttachNote() {
  const r = board.attachedInfo;
  $("attachNote").hidden = !r || board.attached.length === 0;
  if (r) $("attachText").textContent = `กำลังบันทึกงานซ่อม · ${truckLabel(r)} · ${r.problem} — บันทึกแล้วงานนี้จะถูกปิดให้เอง`;
}

function clearAttachment() {
  board.attached = [];
  board.attachedInfo = null;
  renderAttachNote();
  if (typeof renderManualFixes === "function") renderManualFixes();
}

// On the review page: which waiting repairs of THIS truck does the bill close?
function renderFixes() {
  const sel = $("evVehicle");
  const veh = sel ? selectedVehicle(sel) : null;
  const card = $("fixesCard");
  const mine = veh ? board.active.filter((r) => r.vehicle_id === veh.id) : [];
  card.hidden = mine.length === 0;
  $("fixesList").replaceChildren(...mine.map((r) => {
    const box = el("input", { type: "checkbox", "data-id": r.request_id });
    box.checked = board.attached.includes(r.request_id);
    return el("label", { class: "fixrow" }, box,
      el("span", {}, `${r.problem} `,
        el("span", { class: "muted small", text: r.status === "awaiting_bill" ? "(ซ่อมแล้ว รอบิล)" : r.status === "at_shop" ? `(อยู่ที่ ${r.shop})` : `(ยังไม่ได้ซ่อม · แจ้ง ${thDate(r.request_date)})` })));
  }));
}

const selectedFixIds = () => [...document.querySelectorAll("#fixesList input:checked")].map((b) => b.dataset.id);

// --------------------------------------------------------------- init
function initBoard() {
  fillVehicleSelect($("addVehicle"));
  $("addForm").addEventListener("submit", submitNewRequest);
  // Picking a truck suggests its main driver as the reporter -- until you type a different name yourself.
  $("addReporter").addEventListener("input", () => { $("addReporter").dataset.typed = "1"; });
  $("addVehicle").addEventListener("change", () => {
    const v = selectedVehicle($("addVehicle"));
    if (!$("addReporter").dataset.typed) $("addReporter").value = v && v.driver ? v.driver : "";
  });
  $("addDate").max = todayISO();
  bindDateField($("addDate"), $("addDateCap"));
  $("addDate").value = todayISO();
  $("addDateCap").textContent = dateCaption($("addDate").value);
  bindDateField($("fixDate"), $("fixDateCap"));
  $("fixCancel").addEventListener("click", () => $("fixDialog").close());
  $("fixHasBill").addEventListener("click", () => submitFix("fixed_awaiting_bill"));
  $("fixNoBill").addEventListener("click", () => submitFix("fixed_no_bill"));
  $("reCancel").addEventListener("click", () => $("reqEditDialog").close());
  $("reSave").addEventListener("click", saveEditRequest);
  bindDateField($("reDate"), $("reDateCap"));
  $("shopCancel").addEventListener("click", () => $("shopDialog").close());
  $("shopOk").addEventListener("click", submitShop);
  bindDateField($("shopDate"), $("shopDateCap"));
  bindDateField($("shopBack"), $("shopBackCap"));
  $("shopDate").addEventListener("change", () => { $("shopBack").min = $("shopDate").value; });
  $("returnCancel").addEventListener("click", () => $("returnDialog").close());
  $("returnOk").addEventListener("click", () => submitReturn("returned"));
  $("returnNoBill").addEventListener("click", () => submitReturn("returned_no_bill"));
  bindDateField($("returnDate"), $("returnDateCap"));
  $("returnDate").addEventListener("change", updateReturnDays);
  $("returnDate").addEventListener("input", updateReturnDays);
  wireColumns();
  $("noBillCancel").addEventListener("click", () => $("noBillDialog").close());
  $("noBillOk").addEventListener("click", submitNoBill);
  $("attachClear").addEventListener("click", clearAttachment);
  return loadBoard();
}
