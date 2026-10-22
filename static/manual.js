"use strict";

// Record page > "ช่างทำเอง (กรอกเอง)": the internal mechanic's own work, entered by hand.
// No bill and no AI reading. It saves through the same POST /api/events as a bill
// (source = "manual"), so it lands in the same history, dashboard and board:
// a manual entry can close the repair requests of that truck, exactly like a bill.
//
// Loaded BEFORE app.js; nothing here runs at load time. Uses helpers from the other
// scripts (state, board, el, $, lineRow, recategorize, selectedVehicle ...) at call time.

const manual = { mode: "bill", saving: false };

// Quick buttons for the jobs the mechanic does most. Categories must be names from the
// categories table (the server refuses anything else).
const PRESETS = [
  ["ถ่ายน้ำมันเครื่อง", "ไส้กรองและของเหลว"],
  ["เปลี่ยนกรองน้ำมันเครื่อง", "ไส้กรองและของเหลว"],
  ["เปลี่ยนกรองโซล่า", "ไส้กรองและของเหลว"],
  ["ถ่ายน้ำมันเกียร์", "ไส้กรองและของเหลว"],
  ["ถ่ายน้ำมันเฟืองท้าย", "ไส้กรองและของเหลว"],
  ["อัดจาระบี", "ไส้กรองและของเหลว"],
  ["เปลี่ยนผ้าเบรก", "เบรก"],
  ["เช็คช่วงล่าง", "ช่วงล่าง"],
  ["เปลี่ยนหลอดไฟ", "ระบบไฟฟ้า"],
  ["ปะยาง / เปลี่ยนยาง", "ล้อและยาง"],
];

const mRows = () => [...document.querySelectorAll("#manualLines tbody tr")];
const mHooks = { changed: () => manualTotals(), renumber: () => mRows().forEach((tr, i) => { tr.querySelector(".n").textContent = String(i + 1); }) };
const isBlankRow = (tr) => !tr.querySelector("[data-f=description]").value.trim() && !num(tr.querySelector("[data-f=line_total]").value);

// ------------------------------------------------------------------- mode
function setMode(mode) {
  manual.mode = mode;
  const man = mode === "manual";
  $("billMode").hidden = man;
  $("manualMode").hidden = !man;
  $("extractBtn").hidden = man;
  $("manualSaveBtn").hidden = !man;
  $("vehicleNote").hidden = man;
  $("modeBill").classList.toggle("active", !man);
  $("modeManual").classList.toggle("active", man);
  $("modeBill").setAttribute("aria-selected", String(!man));
  $("modeManual").setAttribute("aria-selected", String(man));
  $("captureError").hidden = true;
  showErrorIn("manualError", "");
  setCatStatus("");
  if (man) prepareManual();
}

function showErrorIn(id, text) {
  const n = $(id);
  n.hidden = !text;
  n.textContent = text || "";
}

// First time in manual mode (or after a save): a sensible empty form.
function prepareManual() {
  if (!$("mDate").value) $("mDate").value = todayISO();
  $("mDateCap").textContent = dateCaption($("mDate").value);
  if (!$("mWho").value) $("mWho").value = board.fixers[0] || "ช่างเอ๋";
  $("mWhoList").replaceChildren(...board.fixers.map((n) => el("option", { value: n })));
  $("mWhoChips").replaceChildren(...board.fixers.slice(0, 6).map((n) => el("button", { class: "chipbtn", type: "button", text: n, onclick: () => { $("mWho").value = n; } })));
  if (!mRows().length) addManualRow({});
  renderManualFixes();
  manualTotals();
}

// ------------------------------------------------------------------- rows
function addManualRow(item) {
  const tbody = document.querySelector("#manualLines tbody");
  const tr = lineRow(item, mHooks);
  tbody.append(tr);
  mHooks.renumber();
  manualTotals();
  return tr;
}

function addPreset([text, category]) {
  const blank = mRows().find(isBlankRow);
  if (blank) {                       // fill the empty row instead of leaving a gap
    blank.querySelector("[data-f=description]").value = text;
    blank.querySelector("[data-f=category]").value = category;
    blank.dataset.catManual = "1";
  } else {
    addManualRow({ description: text, category }).dataset.catManual = "1";
  }
}

function manualSum() {
  return mRows().reduce((s, tr) => s + num(tr.querySelector("[data-f=line_total]").value), 0);
}

function manualTotals() {
  const sum = Math.round(manualSum() * 100) / 100;
  $("mTotals").replaceChildren(sum > 0 ? el("span", {}, "รวมค่าใช้จ่าย ", el("span", { class: "big", text: `${fmtMoney(sum)} ฿` })) : "ไม่ใส่ราคาก็ได้ — ระบบเก็บเป็นรายการที่ทำ");
}

// ----------------------------------------------- closing waiting repairs
function renderManualFixes() {
  const veh = selectedVehicle($("vehicleSelect"));
  const mine = veh ? board.active.filter((r) => r.vehicle_id === veh.id) : [];
  $("manualFixesCard").hidden = manual.mode !== "manual" || mine.length === 0;
  $("manualFixesList").replaceChildren(...mine.map((r) => {
    const box = el("input", { type: "checkbox", "data-id": r.request_id });
    box.checked = board.attached.includes(r.request_id);
    return el("label", { class: "fixrow" }, box,
      el("span", {}, el("strong", { text: `#${r.request_no} ` }), `${r.problem} `,
        el("span", { class: "muted small", text: r.status === "awaiting_bill" ? "(ซ่อมแล้ว รอบิล)" : r.status === "at_shop" ? `(อยู่ที่ ${r.shop})` : `(ยังไม่ได้ซ่อม · แจ้ง ${thDate(r.request_date)})` })));
  }));
}

// ------------------------------------------------------------------- save
function readManual() {
  const veh = selectedVehicle($("vehicleSelect"));
  const v = (id) => $(id).value.trim();
  const rows = mRows().filter((tr) => !isBlankRow(tr));
  const sum = Math.round(manualSum() * 100) / 100;
  return {
    vehicle_id: veh ? veh.id : null,
    source: "manual",
    event: {
      event_date: v("mDate") || null, performed_by: v("mWho") || null, mileage: num(v("mMileage")) || null,
      total_cost: sum > 0 ? sum : null, notes: v("mNotes") || null, summary: v("mSummary") || null,
    },
    line_items: rows.map((tr) => {
      const g = (n) => tr.querySelector(`[data-f=${n}]`).value.trim();
      return {
        file_index: null, description: g("description"), category: g("category") || null, quantity: num(g("quantity")) || null,
        unit: g("unit") || null, unit_cost: num(g("unit_cost")) || null, line_total: num(g("line_total")) || null,
      };
    }),
    attachments: state.refFiles.map((f) => ({ type: "reference_photo", filename: f.name })),
    bill_extractions: {},
    fixes_request_ids: [...document.querySelectorAll("#manualFixesList input:checked")].map((b) => b.dataset.id),
  };
}

async function saveManual() {
  if (manual.saving) return;
  const p = readManual();
  const problems = [];
  if (!p.vehicle_id) problems.push("ยังไม่ได้เลือกรถ");
  if (!p.event.performed_by) problems.push("ยังไม่ได้ใส่ว่าใครทำ");
  if (!p.event.event_date) problems.push("ยังไม่ได้ใส่วันที่");
  if (!p.line_items.length) problems.push("ยังไม่ได้ใส่ว่าทำอะไร");
  showErrorIn("manualError", problems.join(" · "));
  if (problems.length) return;

  manual.saving = true;
  const btn = $("manualSaveBtn");
  btn.disabled = true;
  btn.textContent = "กำลังบันทึก…";
  try {
    const res = await postEvent(p, false, state.refFiles);
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch (_) { /* not json */ }
      showErrorIn("manualError", detail || `บันทึกไม่สำเร็จ (HTTP ${res.status}) — ข้อมูลที่กรอกยังอยู่ ลองอีกครั้ง`);
      return;
    }
    const saved = await res.json();
    const veh = selectedVehicle($("vehicleSelect"));
    $("doneText").textContent = `${veh ? vehicleLabel(veh) : ""} · ${beDate(p.event.event_date)} · ${p.line_items.length} รายการ · ${p.event.performed_by} (รหัส ${saved.event_id.slice(0, 8)})`;
    const closed = p.fixes_request_ids.length;
    $("doneClosed").hidden = closed === 0;
    $("doneClosed").textContent = closed ? `ปิดงานซ่อมให้แล้ว ${closed} งาน` : "";
    resetAfterSave();
    resetManualForm();
    showView("done");
  } catch (err) {
    showErrorIn("manualError", "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ — ข้อมูลที่กรอกยังอยู่ ลองอีกครั้ง");
  } finally {
    manual.saving = false;
    btn.disabled = false;
    btn.textContent = "บันทึก";
  }
}

function resetManualForm() {
  document.querySelector("#manualLines tbody").replaceChildren();
  for (const id of ["mMileage", "mSummary", "mNotes"]) $(id).value = "";
  $("mDate").value = todayISO();
  showErrorIn("manualError", "");
  setCatStatus("");
}

// ------------------------------------- from the board's "ช่างทำเอง" button
// Opens this form for that repair request: truck, date and mechanic filled in, and the
// request ticked, so saving the entry completes it.
function startManualFor(request, who, dateISO) {
  board.attached = [request.request_id];
  board.attachedInfo = request;
  const idx = state.vehicles.findIndex((v) => v.id === request.vehicle_id);
  $("vehicleSelect").value = idx >= 0 ? String(idx) : "";
  showView("capture");
  setMode("manual");
  $("mWho").value = who || board.fixers[0] || "ช่างเอ๋";
  $("mDate").value = dateISO || todayISO();
  $("mDateCap").textContent = dateCaption($("mDate").value);
  const blank = mRows().find(isBlankRow);
  if (blank) blank.querySelector("[data-f=description]").value = request.problem || "";     // the reported problem is a good start
  renderManualFixes();
  renderAttachNote();
}

// --------------------------------------------------------------------- init
function initManual() {
  $("modeBill").addEventListener("click", () => setMode("bill"));
  $("modeManual").addEventListener("click", () => setMode("manual"));
  $("mAddRow").addEventListener("click", () => addManualRow({}));
  // Typed names get a category automatically when you leave the box; this does every row at once (or retries).
  // Rows whose category came from a shortcut button, or that you picked yourself, are left alone.
  $("mRecat").addEventListener("click", () => {
    const rows = mRows().filter((tr) => tr.querySelector("[data-f=description]").value.trim());
    if (!rows.length) { setCatStatus("ยังไม่มีรายการให้จัดหมวด — พิมพ์ว่าทำอะไรก่อน"); return; }
    recategorize(rows);
  });
  $("manualSaveBtn").addEventListener("click", saveManual);
  $("mPresets").replaceChildren(...PRESETS.map((p) => el("button", { class: "chipbtn", type: "button", text: p[0], onclick: () => addPreset(p) })));
  bindDateField($("mDate"), $("mDateCap"));
  $("vehicleSelect").addEventListener("change", renderManualFixes);
  $("fixSelf").addEventListener("click", () => {
    if (!fixTarget) return;
    const request = fixTarget, who = $("fixBy").value.trim(), when = $("fixDate").value;
    $("fixDialog").close();
    startManualFor(request, who, when);
  });
}
