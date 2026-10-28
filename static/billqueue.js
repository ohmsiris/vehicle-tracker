"use strict";

// บันทึกบิล (repair bills) as a queue, the way บิลซื้อของ works: pick many photos ("one bill per file" or "all files are pages of one bill"), they are read in
// the background (RQ_PARALLEL at a time, with a clock), and wait in a list grouped by truck until checked. "ตรวจและบันทึก" opens one on the usual check page
// (photo beside the form, mileage history, shop list); saving it takes it off the list -- nothing is saved before it has been checked, so an unchecked bill is
// in no history and no plan. The list lives in this page (a refresh loses unsaved bills; the browser warns first), exactly like บิลซื้อของ.
// Loaded BEFORE app.js: nothing runs at load time; helpers from app.js / board.js / today.js are used at call time.

const RQ_PARALLEL = 3;
const rq = { jobs: [], nextId: 1, running: 0, timer: null, mode: "each", cur: null };

const rqClock = (ms) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

// the truck a bill is for: the one picked before reading, else the one its plate matched
function rqVehicle(job) {
  if (job.vehicle) return job.vehicle;
  const m = job.draft && job.draft.bill_vehicle && job.draft.bill_vehicle.roster_match;
  return m ? state.vehicles.find((v) => v.plate === m.plate && v.branch === m.branch) || null : null;
}

function rqRenderModes() {
  const n = state.pickBills.length;
  $("rqModes").hidden = n < 2;
  const bills = rq.mode === "one" || n < 2 ? 1 : n;
  $("extractBtn").textContent = n > 1 ? `อ่านบิล (${bills} ใบ)` : "อ่านบิล";
  $("rqRefNote").hidden = !(bills > 1 && state.pickRefs.length);
}

// "อ่านบิล": the picked files become bills in the queue and reading starts; the page stays free for the next ones
function rqEnqueue() {
  if (!state.pickBills.length) return;
  $("captureError").hidden = true;
  const vehicle = selectedVehicle($("vehicleSelect"));
  const groups = rq.mode === "one" || state.pickBills.length < 2 ? [[...state.pickBills]] : state.pickBills.map((f) => [f]);
  groups.forEach((files) => {
    rq.jobs.push({ id: rq.nextId++, files, refs: groups.length === 1 ? [...state.pickRefs] : [], vehicle, attached: [...board.attached], attachedInfo: board.attachedInfo,
      status: "queued", error: "", t0: null, draft: null });
  });
  state.pickBills.length = 0;
  if (groups.length === 1) state.pickRefs.length = 0;               // the other photos went with that bill
  renderPicked();
  clearAttachment();
  rqRender();
  rqPump();
}

function rqPump() {
  while (rq.running < RQ_PARALLEL) {
    const job = rq.jobs.find((j) => j.status === "queued");
    if (!job) break;
    rqRun(job);
  }
}

async function rqRun(job) {
  job.status = "reading";
  job.t0 = Date.now();
  job.error = "";
  rq.running++;
  rqTick();
  rqRender();
  const fd = new FormData();
  job.files.forEach((f) => fd.append("files", f.blob, f.name));
  fd.append("plate", job.vehicle ? job.vehicle.plate : "");
  try {
    job.draft = await api("/api/extract", { method: "POST", body: fd });
    job.status = "ready";
  } catch (err) {
    job.status = "error";
    job.error = err.status === 403 ? "รหัสเข้าใช้งานไม่ถูกต้อง" : err.message;
  } finally {
    rq.running--;
    rqRender();
    rqPump();
  }
}

function rqTick() {
  if (rq.timer) return;
  rq.timer = setInterval(() => {
    if (!rq.jobs.some((j) => j.status === "reading")) { clearInterval(rq.timer); rq.timer = null; return; }
    for (const s of document.querySelectorAll(".rqjob-clock")) {
      const job = rq.jobs.find((j) => String(j.id) === s.dataset.id);
      if (job && job.t0) s.textContent = rqClock(Date.now() - job.t0);
    }
  }, 1000);
}

// the list, one block per truck (bills whose truck is not known yet come last)
function rqRender() {
  if (typeof navBadge === "function") navBadge("navBills", rq.jobs.length, false);
  $("rqQueueBox").hidden = rq.jobs.length === 0;
  const ready = rq.jobs.filter((j) => j.status === "ready").length;
  fill($("rqQueueHead"), `บิลที่รอตรวจ ${rq.jobs.length} ใบ`, ready ? el("span", { class: "muted small", text: ` · อ่านเสร็จแล้ว ${ready} ใบ` }) : null);
  const groups = new Map();
  for (const job of rq.jobs) {
    const v = rqVehicle(job), key = v ? v.id : "", label = v ? vehicleLabel(v) : (job.status === "ready" ? "ยังไม่รู้ว่ารถคันไหน — เลือกตอนตรวจ" : "รอผลการอ่าน");
    if (!groups.has(key)) groups.set(key, { label, jobs: [] });
    groups.get(key).jobs.push(job);
  }
  const blocks = [...groups.entries()].sort(([a, x], [b, y]) => (a ? 0 : 1) - (b ? 0 : 1) || x.label.localeCompare(y.label, "th", { numeric: true }));
  fill($("rqQueue"), ...blocks.map(([, g]) => el("div", { class: "rqgroup" },
    el("div", { class: "rqgroup-head" }, el("strong", { text: g.label }), el("span", { class: "muted small", text: ` · ${g.jobs.length} ใบ` })),
    ...g.jobs.sort((a, b) => String((a.draft && a.draft.event.event_date) || "").localeCompare(String((b.draft && b.draft.event.event_date) || ""))).map(rqRow))));
}

function rqRow(job) {
  const d = job.draft, ev = d ? d.event : {};
  const title = d ? [ev.event_date ? beDate(ev.event_date) : "ไม่มีวันที่", ev.vendor || "ไม่ระบุร้าน", `${d.line_items.length} รายการ`, ev.total_cost ? `${fmtMoney(ev.total_cost)} ฿` : null].filter(Boolean).join(" · ")
    : job.files.map((f) => f.name).join(", ");
  const status = job.status === "reading" ? el("span", { class: "muted small" }, el("span", { class: "spin" }), "กำลังอ่านบิล… ", el("span", { class: "rqjob-clock", "data-id": String(job.id), text: rqClock(Date.now() - job.t0) }))
    : job.status === "queued" ? el("span", { class: "muted small", text: "รอคิว…" })
    : job.status === "error" ? el("span", { class: "error small", text: `อ่านไม่สำเร็จ: ${job.error}` })
    : el("span", { class: "badge warn", text: d && d.warnings && d.warnings.length ? `ยังไม่ตรวจ · ควรดู ${d.warnings.length} จุด` : "ยังไม่ตรวจ" });
  return el("div", { class: "rqjob" },
    el("div", { class: "rqjob-main" }, el("div", { class: "rqjob-title", text: title }), el("div", { class: "muted small", text: `${job.files.length} ไฟล์${job.refs.length ? ` · รูปอื่นๆ ${job.refs.length}` : ""}` }), status),
    el("div", { class: "rqjob-actions" },
      job.status === "ready" ? el("button", { class: "btn small primary", type: "button", text: "ตรวจและบันทึก", onclick: () => rqOpen(job) }) : null,
      job.status === "error" ? el("button", { class: "btn small", type: "button", text: "อ่านใหม่", onclick: () => { job.status = "queued"; rqRender(); rqPump(); } }) : null,
      job.status !== "reading" ? el("button", { class: "btn small", type: "button", text: "ลบ", onclick: () => rqDrop(job) }) : null));
}

function rqDrop(job, ask = true) {
  if (ask && !confirm("เอาบิลนี้ออกจากคิว (ยังไม่ได้บันทึก) ใช่ไหม?")) return;
  [...job.files, ...job.refs].forEach((f) => f.url && URL.revokeObjectURL(f.url));
  rq.jobs = rq.jobs.filter((j) => j !== job);
  if (rq.cur === job) rq.cur = null;
  rqRender();
}

// open one bill on the usual check page: its files become "the bill being checked"
function rqOpen(job) {
  rq.cur = job;
  state.billFiles.splice(0, state.billFiles.length, ...job.files);
  state.refFiles.splice(0, state.refFiles.length, ...job.refs);
  state.draft = job.draft;
  board.attached = [...job.attached];
  board.attachedInfo = job.attachedInfo;
  showReviewError("");
  render(job.draft, job.vehicle);
  showView("review");
  window.scrollTo(0, 0);
}

// after a save: the bill leaves the list; "next" opens the next one read (the same truck first)
function rqSaved(vehicleId) {
  const job = rq.cur;
  rq.cur = null;
  if (job) rq.jobs = rq.jobs.filter((j) => j !== job);             // its files were released with the saved bill
  rq.lastVehicle = vehicleId || null;
  rqRender();
  const n = rq.jobs.filter((j) => j.status === "ready").length;
  $("newBtn").textContent = n ? `ตรวจบิลถัดไป (${n})` : "บันทึกซ่อมถัดไป";
}

function rqNext() {
  const ready = rq.jobs.filter((j) => j.status === "ready");
  const next = ready.find((j) => { const v = rqVehicle(j); return v && v.id === rq.lastVehicle; }) || ready[0];
  if (next) rqOpen(next);
  else showView("capture");
}

function initBillQueue() {
  for (const r of document.querySelectorAll("input[name=rqMode]")) r.addEventListener("change", () => { rq.mode = document.querySelector("input[name=rqMode]:checked").value; rqRenderModes(); });
  window.addEventListener("beforeunload", (e) => { if (rq.jobs.some((j) => j.status !== "error")) { e.preventDefault(); e.returnValue = ""; } });
}
