"use strict";

// แผนซ่อมบำรุงเครื่องจักร: the front page of เครื่องจักร and the plan on each machine's page.
//   #machinery      one card per machine: how many jobs are overdue / coming up / without a record, the most urgent one, the hour meter
//   #machine=<id>   (machinery.js) the machine's jobs with "ทำแล้ว", its hour-meter readings
// The jobs, their intervals and the state of each come from /api/machinery/overview and /api/machines/<id>/plan (app/machine_plan.py);
// a job done is an ordinary maintenance report that carries the job's key. Loaded after machinery.js, before app.js.

const mpn = { data: null, names: {}, plan: null, readDraft: {} };
const PLAN_JOB_STATE = { never: ["ไม่เคยทำ", "urgent"], late: ["เลยกำหนด", "urgent"], soon: ["ใกล้ถึง", "warn"], none: ["ไม่มีบันทึก", ""], ok: ["ปกติ", "ok"] };
const planJobName = (key) => mpn.names[key] || "";

function jobIntervalText(j) {
  const parts = [];
  if (j.months) parts.push(`ทุก ${j.months} เดือน`);
  if (j.hours) parts.push(`ทุก ${Number(j.hours).toLocaleString()} ชม.`);
  return parts.join(" หรือ ");
}

function jobDueText(j) {
  if (j.state === "never") return `ไม่เคยทำ${j.hours_since ? ` · มิเตอร์รวม ~${Math.round(j.hours_since).toLocaleString()} ชม.` : ""}`;
  if (j.state === "none") return "ยังไม่มีบันทึก";
  const d = j.days_left;
  return `ครบ ${beDate(j.due_date)} · ${d < 0 ? `เลยมา ${-d} วัน` : d === 0 ? "ครบวันนี้" : `อีก ${d} วัน`}`;
}

// ================================================================== the overview (#machinery)
async function openMachinery() {
  showView("machinery");
  showErr("mcError", "");
  const ok = await loadMachineryBase();
  if (ok !== true) { showErr("mcError", `โหลดข้อมูลเครื่องจักรไม่สำเร็จ: ${ok}`); return; }
  showReady("mcNeedsV9");
  await openMachineryData();
}

async function openMachineryData() {
  let data;
  try {
    data = await api("/api/machinery/overview");
  } catch (err) {
    showErr("mcError", `โหลดภาพรวมเครื่องจักรไม่สำเร็จ: ${err.message}`);
    return;
  }
  mpn.data = data;
  for (const j of data.jobs || []) mpn.names[j.key] = j.name;
  $("mcNeedsV33").hidden = !!data.ready || needsV9();
  const empty = el("p", { class: "muted empty", text: needsV9() ? "" : "ยังไม่มีเครื่อง — กด “+ เพิ่มเครื่อง”" });
  const tube = data.machines.filter((m) => m.kind !== "บ่อซอง");
  const song = data.machines.filter((m) => m.kind === "บ่อซอง");
  fill($("mcCardsTube"), ...(tube.length ? tube.map(machineOverviewCard) : [empty]));
  fill($("mcCardsSong"), ...(song.length ? song.map(machineOverviewCard) : [el("p", { class: "muted empty", text: "ไม่มีเครื่องบ่อซอง" })]));
  fill($("mcJobList"), ...(data.jobs || []).map(jobSettingRow));
}

const jobCountChip = (n, label, cls) => (n ? el("span", { class: `badge ${cls}`.trim(), text: `${label} ${n}` }) : null);

function machineOverviewCard(m) {
  const p = m.plan, c = p.counts, next = p.next, h = p.hours_now;
  const meter = !p.rate ? "" : h ? `มิเตอร์ ~${Math.round(h.hours).toLocaleString()} ชม. (${p.rate.source === "มิเตอร์" ? "จากมิเตอร์" : "ประมาณ"})`
    : `ยังไม่ได้กรอกมิเตอร์ · สมมติ ${p.rate.assumed}`;
  return el("a", { class: "mccard", href: `#machine=${m.id}` },
    el("div", { class: "mc-name", text: m.name }),
    el("div", { class: "muted small", text: machineSub(m) || m.branch }),
    el("div", { class: "mc-chips" },
      jobCountChip(c.late, "เลยกำหนด", "urgent"), jobCountChip(c.soon, "ใกล้ถึง", "warn"), jobCountChip(c.none, "ไม่มีบันทึก", ""),
      p.jobs.length && !c.late && !c.soon && !c.none ? el("span", { class: "badge ok", text: "ครบทุกงาน" }) : null),
    next ? el("div", { class: "mc-next small" }, el("strong", { text: "เร่งสุด: " }), `${next.name} · ${jobDueText(next)}`) : null,
    meter ? el("div", { class: "muted small", text: meter }) : null);
}

// ---- the jobs' intervals and levels
function jobSettingRow(j) {
  const months = el("input", { type: "number", min: "0", step: "1", inputmode: "numeric", placeholder: "—", "aria-label": `${j.name} รอบ (เดือน)` });
  const hours = el("input", { type: "number", min: "0", step: "100", inputmode: "numeric", placeholder: "—", "aria-label": `${j.name} รอบ (ชั่วโมง)` });
  months.value = j.months ?? "";
  hours.value = j.hours ?? "";
  const level = el("select", { "aria-label": `${j.name} ระดับ` }, ...mpn.data.levels.map((l) => el("option", { value: l, text: l })));
  level.value = j.level;
  const msg = el("span", { class: "small muted" });
  const save = el("button", { class: "btn small", type: "button", text: "บันทึก" });
  save.addEventListener("click", async () => {
    msg.textContent = "";
    try {
      await api(`/api/machine-jobs/${j.key}`, jsonOpts("PATCH", { interval_months: months.value, interval_hours: hours.value, level: level.value }));
    } catch (err) {
      msg.className = "small error";
      msg.textContent = err.message;
      return;
    }
    await openMachineryData();
  });
  return el("div", { class: "jobrow" },
    el("div", { class: "jr-name" }, el("strong", { text: j.name }), j.guess ? el("span", { class: "badge", text: "ประมาณ" }) : null,
      el("div", { class: "muted small", text: [j.kinds.includes("บ่อซอง") ? "" : "เฉพาะเครื่องหลอด", j.note].filter(Boolean).join(" · ") })),
    el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "เดือน" }), months),
    el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "ชั่วโมง" }), hours),
    el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "ระดับ" }), level),
    el("div", { class: "jr-save" }, save, msg));
}

// ================================================================== the plan on a machine's page
async function renderMachinePlan(m) {
  let res;
  try {
    res = await api(`/api/machines/${m.id}/plan`);
  } catch (err) {
    showErr("mdError", `โหลดแผนซ่อมบำรุงไม่สำเร็จ: ${err.message}`);
    return;
  }
  mpn.plan = res;
  if (!res.ready) {
    $("mdReadAdd").hidden = true;
    $("mdPlanCount").textContent = "";
    fill($("mdPlanList"), el("p", { class: "muted empty", text: "ยังไม่มีแผนซ่อมบำรุง — ต้องรันไฟล์ schema_v33.sql ใน Supabase ก่อน" }));
    fill($("mdMeter"));
    return;
  }
  $("mdReadAdd").hidden = false;
  const plan = res.plan;
  for (const j of plan.jobs) mpn.names[j.key] = j.name;
  $("mdPlanCount").textContent = `(เลยกำหนด ${plan.counts.late} · ใกล้ถึง ${plan.counts.soon} · ไม่มีบันทึก ${plan.counts.none})`;
  fill($("mdPlanList"), ...(plan.jobs.length ? plan.jobs.map((j) => machinePlanRow(j, m)) : [el("p", { class: "muted empty", text: "ไม่มีงานตามแผนสำหรับเครื่องนี้" })]));
  renderMachineMeter(res, m);
}

function machinePlanRow(j, m) {
  const [label, cls] = PLAN_JOB_STATE[j.state];
  const last = j.last_date ? `ทำล่าสุด ${beDate(j.last_date)}${j.last_job && j.last_job !== j.key ? ` (ตอน${planJobName(j.last_job) || j.last_job})` : ""}` : "ทำล่าสุด: ไม่มีบันทึก";
  return el("div", { class: `planrow ${j.state}` },
    el("div", { class: "pl-main" },
      el("div", { class: "pl-name" }, el("strong", { text: j.name }), j.level !== "ทำเอง" ? el("span", { class: "badge shop", text: j.level }) : null, j.guess ? el("span", { class: "badge", text: "ประมาณ" }) : null),
      j.note ? el("div", { class: "muted small", text: j.note }) : null),
    el("div", { class: "pl-when small" }, el("div", { text: last }), el("div", { class: "muted", text: jobIntervalText(j) }),
      j.hours_since != null && j.hours ? el("div", { class: "muted", text: `${Math.round(j.hours_since).toLocaleString()} / ${Number(j.hours).toLocaleString()} ชม.${j.hours_estimated ? " (ประมาณ)" : ""}` }) : null),
    el("div", { class: "pl-state" }, el("span", { class: `badge ${cls}`.trim(), text: label }), el("div", { class: "small", text: jobDueText(j) })),
    el("button", { class: "btn small", type: "button", text: "บันทึกว่าทำแล้ว",
      onclick: () => openEventDialog(null, { machine_id: m.id, kind: j.event_kind, summary: j.name, job_key: j.key }) }));
}

function renderMachineMeter(res, m) {
  const r = res.plan.rate, h = res.plan.hours_now;
  fill($("mdMeter"),
    el("div", {}, el("strong", { text: h ? `ประมาณ ${Math.round(h.hours).toLocaleString()} ชม. วันนี้` : "ยังไม่ได้บันทึกมิเตอร์" }),
      h ? el("span", { class: "muted small", text: ` · อ่านล่าสุด ${Number(h.reading).toLocaleString()} ชม. เมื่อ ${beDate(h.read_on)}` }) : null),
    el("div", { class: "muted small", text: r.source === "มิเตอร์" ? `เดินเฉลี่ย ${r.per_day.toFixed(1)} ชม./วัน (คำนวณจากมิเตอร์)`
      : `สมมติเดิน ${r.assumed} — ใส่มิเตอร์ 2 ครั้งห่างกันตั้งแต่ 1 สัปดาห์ ระบบจะคำนวณจากมิเตอร์เอง (แก้ที่สมมติได้ที่ “แก้ไข”)` }),
    ...res.readings.map((rd) => el("div", { class: "rdrow small" },
      el("span", { text: `${beDate(rd.read_on)} · ${Number(rd.hours).toLocaleString()} ชม.${rd.notes ? ` · ${rd.notes}` : ""}` }),
      el("button", { class: "linkbtn", type: "button", text: "ลบ", onclick: async () => {
        if (!confirm("ลบการอ่านมิเตอร์นี้ใช่ไหม?")) return;
        try { await api(`/api/machine-readings/${rd.id}`, { method: "DELETE" }); } catch (err) { showErr("mdError", err.message); return; }
        await renderMachinePlan(m);
      } }))));
}

// ---- an hour-meter reading (dialog)
function openReadDialog() {
  const m = mc.current;
  if (!m) return;
  mpn.readDraft = { read_on: todayISO(), hours: "", notes: "" };
  $("rdTitle").textContent = `บันทึกมิเตอร์ชั่วโมง — ${m.name}`;
  fill($("rdBody"), el("div", { class: "mfields" },
    mfield(mpn.readDraft, "read_on", "วันที่อ่าน", { type: "date", caption: true }),
    mfield(mpn.readDraft, "hours", "ชั่วโมงที่มิเตอร์", { type: "number" }),
    mfield(mpn.readDraft, "notes", "หมายเหตุ (ไม่บังคับ)", { wide: true, maxlength: 200 })));
  showErr("rdError", "");
  $("readDialog").showModal();
}

async function saveReading() {
  try {
    await api(`/api/machines/${mc.current.id}/readings`, jsonOpts("POST", mpn.readDraft));
  } catch (err) {
    showErr("rdError", err.message);
    return;
  }
  $("readDialog").close();
  await refreshMachineryViews();
}

function initMachPlan() {
  $("mdReadAdd").addEventListener("click", openReadDialog);
  $("rdCancel").addEventListener("click", () => $("readDialog").close());
  $("rdSave").addEventListener("click", saveReading);
}
