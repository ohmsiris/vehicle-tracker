"use strict";

// แผนซ่อมบำรุงเครื่องจักร: the front page of เครื่องจักร, the chart on each machine's page, and the programs' settings page.
//   #machinery      one card per machine (overdue / coming up / no record, the most urgent job, the hour meter) + a machine × job table
//   #machine=<id>   (machinery.js) the machine's program, its truck-style chart, "+ บันทึกว่าทำแล้ว", its hour-meter readings
//   #machine-plan   the programs (which jobs, every how many months / hours, level) and which machine follows which program
// Data: /api/machinery/overview, /api/machines/<id>/plan, /api/machine-programs (app/machine_plan.py). A job done is an
// ordinary maintenance report carrying the job's key. Loaded after machinery.js, before app.js.

const mpn = { data: null, names: {}, plan: null, readDraft: {}, progs: null, jd: {} };
const PLAN_JOB_STATE = { never: ["ไม่เคยทำ", "urgent"], late: ["เลยกำหนด", "urgent"], soon: ["ใกล้ถึง", "warn"], none: ["ไม่มีบันทึก", ""], ok: ["ปกติ", "ok"] };
// table cells: state -> [symbol, css class, meaning]
const MP_CELL = {
  done: ["✓", "pl-done", "ทำแล้วในรอบล่าสุด"], ok: ["✓", "pl-done", "ปกติ"], due: ["●", "pl-due", "ถึงกำหนดในรอบนี้"], soon: ["●", "pl-soon", "ใกล้ถึงกำหนด"],
  overdue: ["●", "pl-over", "เลยกำหนดแล้ว"], late: ["●", "pl-over", "เลยกำหนดแล้ว"], never: ["✕", "pl-over", "ไม่เคยทำ"], unknown: ["?", "pl-unk", "ไม่มีบันทึก"], none: ["?", "pl-unk", "ไม่มีบันทึก"],
};
const planJobName = (key) => mpn.names[key] || "";

function jobIntervalText(j) {
  const parts = [];
  if (j.months) parts.push(`${Number(j.months)} เดือน`);
  if (j.hours) parts.push(`${Number(j.hours).toLocaleString()} ชม.${j.hours_months ? ` ≈ ${j.hours_months} เดือน` : ""}`);
  return parts.length ? `ทุก ${parts.join(" / ")}` : "";
}

function jobDueText(j) {
  if (j.state === "never") return `ไม่เคยทำ${j.hours_since ? ` · มิเตอร์รวม ~${Math.round(j.hours_since).toLocaleString()} ชม.` : ""}`;
  if (j.state === "none") return "ยังไม่มีบันทึก";
  const d = j.days_left;
  return `ครบ ${beDate(j.due_date)} · ${d < 0 ? `เลยมา ${-d} วัน` : d === 0 ? "ครบวันนี้" : d >= 45 ? `อีก ${d} วัน ≈ ${(d / 30.4375).toFixed(1)} เดือน` : `อีก ${d} วัน`}`;
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
  fill($("mcMatrix"), data.ready ? machineMatrix(data) : null);
}

const jobCountChip = (n, label, cls) => (n ? el("span", { class: `badge ${cls}`.trim(), text: `${label} ${n}` }) : null);

function machineOverviewCard(m) {
  const p = m.plan;
  if (!p) return el("a", { class: "mccard", href: `#machine=${m.id}` }, el("div", { class: "mc-name", text: m.name }), el("div", { class: "muted small", text: machineSub(m) || m.branch }));
  const c = p.counts, next = p.next, h = p.hours_now;
  const meter = h ? `มิเตอร์ ~${Math.round(h.hours).toLocaleString()} ชม. (${p.rate.source === "มิเตอร์" ? "จากมิเตอร์" : "ประมาณ"})` : `ยังไม่ได้กรอกมิเตอร์ · สมมติ ${p.rate.assumed}`;
  return el("a", { class: "mccard", href: `#machine=${m.id}` },
    el("div", { class: "mc-name", text: m.name }),
    el("div", { class: "muted small", text: [machineSub(m) || m.branch, `โปรแกรม ${p.program.code}`].join(" · ") }),
    el("div", { class: "mc-chips" },
      jobCountChip(c.late, "เลยกำหนด", "urgent"), jobCountChip(c.soon, "ใกล้ถึง", "warn"), jobCountChip(c.none, "ไม่มีบันทึก", ""),
      p.jobs.length && !c.late && !c.soon && !c.none ? el("span", { class: "badge ok", text: "ครบทุกงาน" }) : null),
    next ? el("div", { class: "mc-next small" }, el("strong", { text: "เร่งสุด: " }), `${next.name} · ${jobDueText(next)}`) : null,
    el("div", { class: "muted small", text: meter }));
}

// every machine against every job: the state today, "–" where the machine's program has no such job
function machineMatrix(data) {
  const jobs = data.jobs.filter((j) => data.machines.some((m) => m.plan && m.plan.jobs.some((x) => x.key === j.key)));
  const head = el("tr", {}, el("th", { class: "pl-rowh", text: "เครื่อง" }), ...jobs.map((j) => el("th", { class: "mx-h" }, el("span", { text: j.name }))));
  const body = data.machines.filter((m) => m.plan).map((m) => {
    const by = Object.fromEntries(m.plan.jobs.map((j) => [j.key, j]));
    return el("tr", {},
      el("th", { class: "pl-rowh", scope: "row" }, el("a", { href: `#machine=${m.id}`, text: m.name }), el("div", { class: "muted small", text: `โปรแกรม ${m.plan.program.code}` })),
      ...jobs.map((j) => {
        const s = by[j.key];
        if (!s) return el("td", { class: "pl-cell mx-na", title: `${m.name}: ไม่อยู่ในโปรแกรม`, text: "–" });
        const spec = MP_CELL[s.state];
        return el("td", { class: `pl-cell ${spec[1]}`, title: `${m.name} · ${s.name}: ${jobDueText(s)}`, text: spec[0] });
      }));
  });
  return el("div", { class: "card pl-chartbox" },
    el("div", { class: "pl-chartscroll" }, el("table", { class: "pl-chart mx-chart" }, el("thead", {}, head), el("tbody", {}, ...body))),
    mpLegend([["pl-done", "✓", "ปกติ"], ["pl-soon", "●", `ใกล้ถึง (ภายใน 30 วัน)`], ["pl-over", "●", "เลยกำหนด"], ["pl-over", "✕", "ไม่เคยทำ"], ["pl-unk", "?", "ไม่มีบันทึก"]],
      "กดชื่อเครื่องเพื่อดูตารางรอบของเครื่องนั้น · ชี้ที่ช่องเพื่อดูวันครบกำหนด"));
}

const mpLegend = (items, note) => el("div", { class: "pl-legend small muted" },
  ...items.map(([cls, sym, text]) => el("span", {}, el("b", { class: cls, text: sym }), ` ${text}`)), note ? el("span", { text: note }) : null);

// ================================================================== a machine's page
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
    $("mdReadAdd").hidden = $("mdJobsDone").hidden = true;
    $("mdPlanCount").textContent = "";
    fill($("mdProgram"), el("span", { class: "muted", text: "ยังไม่มีแผนซ่อมบำรุง — ต้องรันไฟล์ schema_v33.sql ใน Supabase ก่อน" }));
    fill($("mdChart"));
    fill($("mdPlanList"));
    fill($("mdMeter"));
    return;
  }
  $("mdReadAdd").hidden = $("mdJobsDone").hidden = false;
  const plan = res.plan;
  for (const j of plan.jobs) mpn.names[j.key] = j.name;
  $("mdPlanCount").textContent = `(เลยกำหนด ${plan.counts.late} · ใกล้ถึง ${plan.counts.soon} · ไม่มีบันทึก ${plan.counts.none})`;
  fill($("mdProgram"),
    el("div", { class: "pl-groupinfo" },
      el("div", { class: "muted small", text: "โปรแกรมของเครื่องนี้" }),
      el("strong", { text: `${plan.program.code} · ${plan.program.label}` }),
      el("div", { class: "muted small", text: `${plan.jobs.length} งาน · ตารางแบ่งรอบละ ${plan.program.step_months} เดือน` })),
    el("a", { class: "muted small", href: "#machine-plan", text: "เปลี่ยนโปรแกรม / แก้รอบงาน ได้ที่ แผนซ่อมบำรุงเครื่องจักร" }));
  fill($("mdChart"), machineChart(plan.chart));
  fill($("mdPlanList"), ...plan.jobs.map((j) => machinePlanRow(j, m)));
  renderMachineMeter(res, m);
}

function machineChart(c) {
  if (!c || !c.rows.length) return el("p", { class: "muted", text: "โปรแกรมนี้ยังไม่มีงาน — เปิดงานได้ที่ แผนซ่อมบำรุงเครื่องจักร" });
  const heads = c.columns.map((col) => el("th", { class: `pl-ch${col.n === 0 ? " latest" : ""}` },
    el("div", { class: "pl-ch-label", text: col.label }), el("div", { class: "pl-ch-date small", text: col.n === 0 ? beDate(col.date) : `ถึง ${beDate(col.date)}` })));
  const body = c.rows.map((r) => el("tr", { class: r.state === "late" || r.state === "never" ? "overdue" : r.state },
    el("th", { class: "pl-rowh", scope: "row" }, el("div", { class: "pl-rowname", text: r.name }),
      el("div", { class: "muted small", text: [jobIntervalText(r), r.level !== "ทำเอง" ? r.level : null].filter(Boolean).join(" · ") })),
    el("td", { class: "pl-last" }, r.last_date ? beDate(r.last_date) : "–",
      r.last_job && r.last_job !== r.key ? el("div", { class: "muted small", text: `ตอน${planJobName(r.last_job) || r.last_job}` }) : null),
    ...r.cells.map((cell) => {
      const spec = cell ? MP_CELL[cell] : null;
      return el("td", { class: `pl-cell${spec ? ` ${spec[1]}` : ""}`, title: spec ? `${r.name}: ${spec[2]}` : "", text: spec ? spec[0] : "" });
    })));
  return el("div", { class: "card pl-chartbox" },
    el("div", { class: "pl-chartscroll" },
      el("table", { class: "pl-chart" },
        el("thead", {}, el("tr", {}, el("th", { class: "pl-rowh", text: "งาน" }), el("th", { class: "pl-ch", text: "ทำล่าสุด" }), ...heads)),
        el("tbody", {}, ...body))),
    mpLegend([["pl-done", "✓", "ทำแล้วในรอบล่าสุด"], ["pl-due", "●", "ถึงกำหนดในรอบนั้น"], ["pl-over", "●", "เลยกำหนด"], ["pl-over", "✕", "ไม่เคยทำ"], ["pl-unk", "?", "ไม่มีบันทึก"]],
      `แต่ละรอบห่างกัน ${c.step_months} เดือน · งานที่เลยกำหนดหรือไม่มีบันทึก นับรอบถัดไปจากวันนี้ (เหมือนทำวันนี้)`));
}

function machinePlanRow(j, m) {
  const [label, cls] = PLAN_JOB_STATE[j.state];
  const last = j.last_date ? `ทำล่าสุด ${beDate(j.last_date)}${j.last_job && j.last_job !== j.key ? ` (ตอน${planJobName(j.last_job) || j.last_job})` : ""}` : "ทำล่าสุด: ไม่มีบันทึก";
  return el("div", { class: `planrow ${j.state}` },
    el("div", { class: "pl-main" },
      el("div", { class: "pl-name" }, el("strong", { text: j.name }), j.level !== "ทำเอง" ? el("span", { class: "badge shop", text: j.level }) : null, j.guess ? el("span", { class: "badge", text: "ประมาณ" }) : null),
      j.note ? el("div", { class: "muted small", text: j.note }) : null),
    el("div", { class: "pl-when small" }, el("div", { text: last }), el("div", { class: "muted", text: jobIntervalText(j) }),
      j.hours_since != null && j.hours ? el("div", { class: "muted", text: `ใช้ไป ${Math.round(j.hours_since).toLocaleString()} / ${Number(j.hours).toLocaleString()} ชม.${j.hours_left != null ? ` · เหลือ ~${Math.max(0, j.hours_left).toLocaleString()} ชม.` : ""}${j.hours_estimated ? " (ประมาณ)" : ""}` }) : null),
    el("div", { class: "pl-state" }, el("span", { class: `badge ${cls}`.trim(), text: label }), el("div", { class: "small", text: jobDueText(j) })),
    el("button", { class: "btn small", type: "button", text: "ทำแล้ว", onclick: () => openJobsDone(m, [j.key]) }));
}

function renderMachineMeter(res, m) {
  const r = res.plan.rate, h = res.plan.hours_now;
  fill($("mdMeter"),
    el("div", {}, el("strong", { text: h ? `ประมาณ ${Math.round(h.hours).toLocaleString()} ชม. วันนี้` : "ยังไม่ได้บันทึกมิเตอร์" }),
      h ? el("span", { class: "muted small", text: ` · อ่านล่าสุด ${Number(h.reading).toLocaleString()} ชม. เมื่อ ${beDate(h.read_on)}` }) : null),
    el("div", { class: "muted small", text: r.source === "มิเตอร์" ? `เดินเฉลี่ย ${r.per_day.toFixed(1)} ชม./วัน ≈ ${Math.round(r.per_day * 30.4375).toLocaleString()} ชม./เดือน (จากมิเตอร์ ${r.readings} ครั้ง ย้อนหลัง ${r.span_days} วัน) — ระบบเอาอัตรานี้แปลงชั่วโมงเป็นเดือนให้ทุกงาน`
      : `สมมติเดิน ${r.assumed} ≈ ${Math.round(r.per_day * 30.4375).toLocaleString()} ชม./เดือน — ใส่มิเตอร์ 2 ครั้งห่างกันตั้งแต่ 1 สัปดาห์ ระบบจะคำนวณจากมิเตอร์เอง แล้วแปลงชั่วโมงเป็นเดือนให้ (แก้ที่สมมติได้ที่ “แก้ไข”)` }),
    ...res.readings.map((rd) => el("div", { class: "rdrow small" },
      el("span", { text: `${beDate(rd.read_on)} · ${Number(rd.hours).toLocaleString()} ชม.${rd.notes ? ` · ${rd.notes}` : ""}` }),
      el("button", { class: "linkbtn", type: "button", text: "ลบ", onclick: async () => {
        if (!confirm("ลบการอ่านมิเตอร์นี้ใช่ไหม?")) return;
        try { await api(`/api/machine-readings/${rd.id}`, { method: "DELETE" }); } catch (err) { showErr("mdError", err.message); return; }
        await renderMachinePlan(m);
      } }))));
}

// ---- "+ บันทึกว่าทำแล้ว": tick the jobs done on one day (one report each)
function openJobsDone(m, preset = []) {
  if (!mpn.plan || !mpn.plan.plan) return;
  const jobs = mpn.plan.plan.chart ? mpn.plan.plan.chart.rows : mpn.plan.plan.jobs;
  const d = mpn.jd = { done_date: todayISO(), job_keys: new Set(preset), performed_by: "", cost: "", hours_at: "", notes: "" };
  $("jdTitle").textContent = `บันทึกว่าทำแล้ว — ${m.name}`;
  const picks = jobs.map((j) => el("label", { class: "pl-pick" },
    el("input", { type: "checkbox", checked: d.job_keys.has(j.key), onchange: (e) => { if (e.target.checked) d.job_keys.add(j.key); else d.job_keys.delete(j.key); } }),
    el("span", { text: j.name })));
  fill($("jdBody"),
    el("p", { class: "muted small", text: "ติ๊กงานที่ทำในวันเดียวกัน — ระบบนับรอบใหม่ของแต่ละงานจากวันที่นี้ และบันทึกลง “บันทึกซ่อมบำรุง” ด้วย" }),
    el("div", { class: "pl-picks" }, ...picks),
    el("div", { class: "mfields" },
      mfield(d, "done_date", "วันที่ทำ", { type: "date", caption: true }),
      typeof plShopPick === "function" ? plShopPick(d, "performed_by", "ผู้ทำ / ร้าน") : mfield(d, "performed_by", "ผู้ทำ / ร้าน"),
      mfield(d, "cost", "ค่าใช้จ่ายรวม (฿) — ไม่บังคับ", { type: "number" }),
      mfield(d, "hours_at", "มิเตอร์ตอนทำ (ชม.) — ไม่บังคับ", { type: "number" }),
      mfield(d, "notes", "หมายเหตุ", { wide: true, maxlength: 500 })));
  showErr("jdError", "");
  $("jdDialog").showModal();
}

async function saveJobsDone() {
  const d = mpn.jd, m = mc.current;
  if (!m) return;
  try {
    await api(`/api/machines/${m.id}/jobs-done`, jsonOpts("POST", { ...d, job_keys: [...d.job_keys] }));
  } catch (err) {
    showErr("jdError", err.message);
    return;
  }
  $("jdDialog").close();
  await refreshMachineryViews();
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

// ================================================================== the programs (#machine-plan)
async function openMachinePrograms() {
  showView("machinePlan");
  showErr("mppError", "");
  await loadMachinePrograms();
}

async function loadMachinePrograms() {
  try {
    mpn.progs = await api("/api/machine-programs");
  } catch (err) {
    showErr("mppError", `โหลดโปรแกรมไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderMachinePrograms();
}

function renderMachinePrograms() {
  const d = mpn.progs;
  if (!d) return;
  $("mppNeedsV34").hidden = d.ready;
  fill($("mppList"), ...d.programs.map(programCard), addProgramCard(), assignCard());
}

async function programCall(path, opts, msg) {
  try {
    mpn.progs = await api(path, opts);
  } catch (err) {
    msg.className = "error small";
    msg.textContent = err.message;
    return false;
  }
  renderMachinePrograms();
  return true;
}

function programCard(p) {
  const levels = mpn.progs.levels;
  const label = el("input", { type: "text", maxlength: "60", value: p.label, "aria-label": "ชื่อโปรแกรม" });
  const step = el("input", { type: "number", min: "1", max: "12", step: "1", value: String(p.step_months), "aria-label": "ตารางแบ่งรอบละกี่เดือน" });
  const rows = p.jobs.map((j) => {
    const on = el("input", { type: "checkbox", checked: !!j.on, "aria-label": `ใช้งาน ${j.name}` });
    const months = el("input", { type: "number", min: "0", step: "1", value: j.months ?? "", placeholder: "—", "aria-label": `${j.name} เดือน` });
    const hours = el("input", { type: "number", min: "0", step: "100", value: j.hours ?? "", placeholder: "—", "aria-label": `${j.name} ชั่วโมง` });
    const level = el("select", { "aria-label": `${j.name} ระดับ` }, ...levels.map((l) => el("option", { value: l, text: l })));
    level.value = j.level;
    const row = el("div", { class: `jobrow${j.on ? "" : " off"}` },
      el("label", { class: "jr-name check" }, on, el("span", {}, el("strong", { text: ` ${j.name}` }), j.guess ? el("span", { class: "badge", text: "ประมาณ" }) : null,
        el("div", { class: "muted small", text: [j.note, j.default ? `ค่าเริ่มต้น: ${j.default.on ? [jobIntervalText(j.default), j.default.level].filter(Boolean).join(" · ") : "ไม่ใช้"}` : null].filter(Boolean).join(" · ") }))),
      el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "เดือน" }), months),
      el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "ชั่วโมง" }), hours),
      el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "ระดับ" }), level));
    on.addEventListener("change", () => row.classList.toggle("off", !on.checked));
    return { key: j.key, row, read: () => ({ on: on.checked, months: months.value, hours: hours.value, level: level.value }) };
  });
  const msg = el("span", { class: "muted small" });
  const save = el("button", { class: "btn small primary", type: "button", text: "บันทึกโปรแกรมนี้", onclick: () => programCall(`/api/machine-programs/${p.code}`,
    jsonOpts("PUT", { label: label.value, step_months: step.value, jobs: Object.fromEntries(rows.map((r) => [r.key, r.read()])) }), msg) });
  const reset = p.builtin && p.customised ? el("button", { class: "btn small", type: "button", text: "คืนค่าเริ่มต้น", onclick: () => {
    if (confirm(`คืนค่าเริ่มต้นของโปรแกรม ${p.code} ใช่ไหม? (งาน รอบ และระดับที่แก้ไว้จะหายไป)`)) programCall(`/api/machine-programs/${p.code}`, { method: "DELETE" }, msg);
  } }) : null;
  const del = !p.builtin ? el("button", { class: "btn small danger", type: "button", text: "ลบโปรแกรม", onclick: () => {
    if (confirm(`ลบโปรแกรม ${p.code} · ${p.label} ใช่ไหม?`)) programCall(`/api/machine-programs/${p.code}`, { method: "DELETE" }, msg);
  } }) : null;
  return el("section", { class: "card plset pl-wide mpprog" },
    el("div", { class: "mpprog-head" },
      el("strong", { class: "mpprog-code", text: p.code }),
      el("label", { class: "jr-f grow" }, el("span", { class: "muted small", text: "ชื่อโปรแกรม" }), label),
      el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "ตารางรอบละ (เดือน)" }), step),
      el("span", { class: "badge", text: `${p.machines} เครื่อง` }),
      p.builtin ? el("span", { class: "badge shop", text: "ตั้งต้น" }) : null),
    el("p", { class: "muted small", text: "ติ๊ก = เครื่องในโปรแกรมนี้ได้งานนั้น · ใส่เดือน และ/หรือ ชั่วโมง (อย่างไหนถึงก่อน) · ‘ไม่ต้องทำ’ = ไม่ขึ้นในแผน" }),
    ...rows.map((r) => r.row),
    el("div", { class: "pl-set-actions" }, save, reset, del, msg));
}

function addProgramCard() {
  const label = el("input", { type: "text", maxlength: "60", placeholder: "เช่น เครื่องแก่งคอย", "aria-label": "ชื่อโปรแกรมใหม่" });
  const from = el("select", { "aria-label": "เริ่มจากโปรแกรม" }, ...mpn.progs.programs.map((p) => el("option", { value: p.code, text: `${p.code} · ${p.label}` })));
  const msg = el("span", { class: "muted small" });
  return el("section", { class: "card plset" },
    el("h3", { text: "+ เพิ่มโปรแกรม" }),
    el("p", { class: "muted small", text: "เริ่มจากโปรแกรมที่มีอยู่ แล้วค่อยแก้งาน/รอบ — เช่น เครื่องที่แก่งคอย หรือเครื่องที่ไม่มีเครื่องบด" }),
    el("div", { class: "mfields" }, el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "ชื่อ" }), label),
      el("label", { class: "jr-f" }, el("span", { class: "muted small", text: "คัดลอกจาก" }), from)),
    el("div", { class: "pl-set-actions" }, el("button", { class: "btn small", type: "button", text: "เพิ่ม", onclick: () => programCall("/api/machine-programs",
      jsonOpts("POST", { label: label.value, copy_from: from.value }), msg) }), msg));
}

// Which machine follows which program: picking only marks the row; nothing changes until "บันทึก" and a confirmation.
function assignCard() {
  const d = mpn.progs;
  const staged = new Map();
  const name = (code) => { const p = d.programs.find((x) => x.code === code); return p ? `${p.code} ${p.label}` : code; };
  const msg = el("span", { class: "muted small" });
  const save = el("button", { class: "btn small primary", type: "button", text: "บันทึก", disabled: true });
  const sync = () => { save.disabled = !staged.size; save.textContent = staged.size ? `บันทึกการเปลี่ยนโปรแกรม (${staged.size} เครื่อง)` : "บันทึก"; };
  const list = el("div", { class: "pl-trucks" }, ...d.machines.map((m) => {
    const sel = el("select", { "aria-label": `โปรแกรมของ ${m.name}` }, ...d.programs.map((p) => el("option", { value: p.code, text: `${p.code} ${p.label}` })));
    sel.value = m.program_code;
    const row = el("label", { class: "pl-truck" }, el("span", { class: "pl-truck-name", text: m.name }), sel);
    sel.addEventListener("change", () => {
      if (sel.value === m.program_code) staged.delete(m.id); else staged.set(m.id, sel.value);
      row.classList.toggle("changed", staged.has(m.id));
      sync();
    });
    return row;
  }));
  save.addEventListener("click", () => {
    const lines = [...staged].map(([id, code]) => { const m = d.machines.find((x) => x.id === id); return `• ${m.name}: ${name(m.program_code)} → ${name(code)}`; });
    if (!confirm(`เปลี่ยนโปรแกรมของ ${staged.size} เครื่อง\n\n${lines.join("\n")}\n\nงานและรอบของเครื่องเหล่านี้จะคำนวณใหม่ ยืนยันไหม?`)) return;
    programCall("/api/machine-program-assignments", jsonOpts("PUT", { changes: [...staged].map(([machine_id, program_code]) => ({ machine_id, program_code })) }), msg);
  });
  return el("section", { class: "card plset pl-trucks-card" },
    el("h3", { text: "เครื่องแต่ละเครื่องใช้โปรแกรมไหน" }),
    el("p", { class: "muted small", text: "แถวที่แก้จะเป็นสีเหลือง ยังไม่เปลี่ยนจริงจนกว่าจะกด “บันทึกการเปลี่ยนโปรแกรม” และยืนยัน" }),
    list, el("div", { class: "pl-set-actions pl-trucks-actions" }, save, msg));
}

// ---- every machine's meter in one go (the regular round: date once, a figure per machine, blanks skipped)
function openMetersDialog() {
  const ms = (mpn.data && mpn.data.machines || []).filter((m) => m.plan);
  const d = mpn.mt = { read_on: todayISO(), hours: {} };
  fill($("mtBody"),
    el("p", { class: "muted small", text: "ใส่ชั่วโมงที่อ่านได้จากมิเตอร์ของแต่ละเครื่อง เว้นว่างเครื่องที่ไม่ได้อ่าน — ระบบเอาไปคำนวณว่าเครื่องเดินวันละกี่ชั่วโมง แล้วแปลงรอบที่เป็นชั่วโมงเป็นเดือนให้" }),
    el("div", { class: "mfields" }, mfield(d, "read_on", "วันที่อ่าน", { type: "date", caption: true })),
    el("div", { class: "mtlist" }, ...ms.map((m) => {
      const h = m.plan.hours_now;
      const input = el("input", { type: "number", inputmode: "decimal", min: "0", step: "any", placeholder: "ชม.", "aria-label": `${m.name} มิเตอร์` });
      input.addEventListener("input", () => { d.hours[m.id] = input.value; });
      return el("label", { class: "mtrow" }, el("span", { class: "mt-name" }, el("strong", { text: m.name }),
        el("span", { class: "muted small", text: h ? ` · อ่านล่าสุด ${Number(h.reading).toLocaleString()} ชม. (${beDate(h.read_on)})` : " · ยังไม่เคยกรอก" })), input);
    })));
  showErr("mtError", "");
  $("metersDialog").showModal();
}

async function saveMeters() {
  const d = mpn.mt, todo = Object.entries(d.hours).filter(([, v]) => String(v).trim() !== "");
  if (!todo.length) { showErr("mtError", "ยังไม่ได้ใส่ชั่วโมงของเครื่องไหนเลย"); return; }
  const failed = [];
  for (const [id, hours] of todo) {
    try {
      await api(`/api/machines/${id}/readings`, jsonOpts("POST", { read_on: d.read_on, hours }));
    } catch (err) {
      const m = mpn.data.machines.find((x) => x.id === id);
      failed.push(`${m ? m.name : "?"}: ${err.message}`);
    }
  }
  if (failed.length) { showErr("mtError", `บันทึกไม่ได้ ${failed.length} เครื่อง — ${failed.join(" · ")}`); await openMachineryData(); return; }
  $("metersDialog").close();
  await openMachineryData();
}

function initMachPlan() {
  $("mcMeters").addEventListener("click", openMetersDialog);
  $("mtCancel").addEventListener("click", () => $("metersDialog").close());
  $("mtSave").addEventListener("click", saveMeters);
  $("mdReadAdd").addEventListener("click", openReadDialog);
  $("rdCancel").addEventListener("click", () => $("readDialog").close());
  $("rdSave").addEventListener("click", saveReading);
  $("mdJobsDone").addEventListener("click", () => mc.current && openJobsDone(mc.current, []));
  $("jdCancel").addEventListener("click", () => $("jdDialog").close());
  $("jdSave").addEventListener("click", saveJobsDone);
}
