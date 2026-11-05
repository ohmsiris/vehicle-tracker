"use strict";

// ผู้จัดการ › ปฏิทิน (#mgr-calendar): a Google-style calendar of what is coming.
//   * one chip per truck / machine on the day its OIL CHANGE is due (/api/mgr/calendar, app/calendar_plan.py); the panel lists what
//     to do in the same visit ("ทำพร้อมกันเลย") and the never-done items ("ถ้ามีโอกาส")
//   * the board's open jobs on their due date (/api/mgr/board)
// Colours are the ticket types' colours from the settings page (รอบซ่อมบำรุงรถ / รอบซ่อมบำรุงเครื่องจักร for the visits); a red edge = overdue.
// Overdue: board jobs pile up on TODAY one by one (on purpose: they stay in sight until done); overdue trucks and machines are ONE chip
// each on today ("รถเลยกำหนด 11 คัน"), opened in the panel. Month (default) / week / year views. Loaded after mgr.js (uses mg, MG_MONTHS).

const mcl = {
  view: "month", cursor: "", pick: null, plan: null, kinds: { truck: true, machine: true, job: true }, branch: "",
};
const MCL_VIEWS = [["month", "เดือน"], ["week", "สัปดาห์"], ["year", "ปี"]];
const MCL_KINDS = [["truck", "รถ"], ["machine", "เครื่องจักร"], ["job", "งานบอร์ด"]];
const MCL_WD = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"];
const MCL_MONTH_CHIPS = 3;                       // chips a day shows in the month view before "+N เพิ่มเติม"

const mclISO = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const mclDate = (iso) => new Date(`${iso}T00:00:00`);
const mclAdd = (iso, n) => { const d = mclDate(iso); d.setDate(d.getDate() + n); return mclISO(d); };
const mclMonday = (iso) => { const d = mclDate(iso); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return mclISO(d); };

async function openMgrCal() {
  showView("mgrCal");
  showErr("mclError", "");
  mclWire();
  try {
    const [plan, board] = await Promise.all([api("/api/mgr/calendar"), api("/api/mgr/board")]);
    mcl.plan = plan;
    mg.data = board;                               // the board's own data: its jobs, the types' colours, and "แก้ไข" works from here
  } catch (err) {
    showErr("mclError", `โหลดปฏิทินไม่สำเร็จ: ${err.message}`);
    return;
  }
  if (!mcl.cursor) mcl.cursor = mcl.plan.today;
  mclFillBranches();
  renderMcl();
}

// Called by the board after a job is saved (mgr.js loadMgrBoard): redraw if the calendar is the page on screen.
function mclRefresh() {
  if (mcl.plan && !$("mgrCalView").hidden) renderMcl();
}

function mclWire() {
  if (mcl.wired) return;
  mcl.wired = true;
  const step = (n) => {
    if (mcl.view === "month") { const d = mclDate(mcl.cursor); d.setDate(1); d.setMonth(d.getMonth() + n); mcl.cursor = mclISO(d); }
    else if (mcl.view === "week") mcl.cursor = mclAdd(mcl.cursor, 7 * n);
    else { const d = mclDate(mcl.cursor); d.setFullYear(d.getFullYear() + n); mcl.cursor = mclISO(d); }
    renderMcl();
  };
  $("mclPrev").onclick = () => step(-1);
  $("mclNext").onclick = () => step(1);
  $("mclToday").onclick = () => { mcl.cursor = mcl.plan.today; mcl.pick = null; renderMcl(); };
  $("mclBranch").onchange = (e) => { mcl.branch = e.target.value; renderMcl(); };
  try { const v = localStorage.getItem("mclView"); if (MCL_VIEWS.some(([k]) => k === v)) mcl.view = v; } catch { /* storage blocked */ }
}

function mclFillBranches() {
  const keep = $("mclBranch").value;
  const names = [...new Set([...mcl.plan.entries, ...mcl.plan.no_date].map((e) => e.branch).filter(Boolean))].sort();
  $("mclBranch").replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...names.map((b) => el("option", { value: b, text: b })));
  $("mclBranch").value = names.includes(keep) ? keep : "";
}

// ------------------------------------------------------------------ the entries
function mclTypeColor(key, fallback) {
  const t = mg.data && mg.data.types.find((x) => x.key === key);
  return (t && t.color) || fallback;
}

function mclDays(iso) { return Math.round((mclDate(iso) - mclDate(mcl.plan.today)) / 86400000); }

// Everything that can go on the calendar, filtered; overdue trucks / machines folded into one chip each on today.
function mclEntries() {
  const today = mcl.plan.today;
  const truckColor = mclTypeColor("truck_plan", "#92400e"), machineColor = mclTypeColor("machine_plan", "#475569");
  const keepBranch = (e) => !mcl.branch || e.branch === mcl.branch;
  const out = [];
  const lateGroups = { truck: [], machine: [] };
  for (const e of mcl.plan.entries) {
    if (!mcl.kinds[e.kind] || !keepBranch(e)) continue;
    const color = e.kind === "truck" ? truckColor : machineColor;
    const x = { ...e, color, late: e.days_left < 0, text: `${e.label} ${e.main}${e.together && e.together.length ? ` +${e.together.length}` : ""}` };
    if (x.late) lateGroups[e.kind].push(x);
    else out.push(x);
  }
  for (const [kind, list] of Object.entries(lateGroups)) {
    if (!list.length) continue;
    list.sort((a, b) => a.days_left - b.days_left);
    out.push({ kind: "late", group: kind, date: today, late: true, color: kind === "truck" ? truckColor : machineColor, items: list,
      text: `${kind === "truck" ? "รถ" : "เครื่องจักร"}เลยกำหนด ${list.length} ${kind === "truck" ? "คัน" : "เครื่อง"}` });
  }
  if (mcl.kinds.job) {
    for (const t of (mg.data ? mg.data.tickets : [])) {
      if (!t.due_date) continue;
      const days = mclDays(t.due_date);
      out.push({ kind: "job", id: t.id, ticket: t, date: days < 0 ? today : t.due_date, due: t.due_date, days_left: days, late: days < 0,
        color: mclTypeColor(t.type_key, "#6b7280"), text: `${t.due_time ? `${String(t.due_time).slice(0, 5)} ` : ""}${t.title}`, main: t.type_label || "" });
    }
  }
  const order = { late: 0, job: 1, truck: 2, machine: 3 };
  return out.sort((a, b) => order[a.kind] - order[b.kind] || (a.days_left ?? 0) - (b.days_left ?? 0) || String(a.text).localeCompare(String(b.text)));
}

function mclByDay(entries) {
  const m = new Map();
  for (const e of entries) m.set(e.date, [...(m.get(e.date) || []), e]);
  return m;
}

// ------------------------------------------------------------------ drawing
function renderMcl() {
  if (!mcl.plan) return;
  fill($("mclViews"), ...MCL_VIEWS.map(([k, label]) => el("button", { type: "button", class: `segbtn${mcl.view === k ? " active" : ""}`, text: label,
    onclick: () => { mcl.view = k; try { localStorage.setItem("mclView", k); } catch { /* fine */ } renderMcl(); } })));
  fill($("mclFilters"), ...MCL_KINDS.map(([k, label]) => {
    const color = k === "truck" ? mclTypeColor("truck_plan", "#92400e") : k === "machine" ? mclTypeColor("machine_plan", "#475569") : "var(--accent)";
    return el("button", { type: "button", class: `chipbtn mcl-kind${mcl.kinds[k] ? " on" : ""}`, style: `--c:${color}`, "aria-pressed": String(mcl.kinds[k]),
      onclick: () => { mcl.kinds[k] = !mcl.kinds[k]; renderMcl(); } }, el("span", { class: "mgdot" }), label);
  }));
  const entries = mclEntries();
  const byDay = mclByDay(entries);
  if (mcl.view === "year") mclYear(byDay);
  else if (mcl.view === "week") mclWeek(byDay);
  else mclMonth(byDay);
  mclSide(byDay);
}

function mclChip(e) {
  const on = mcl.pick && mcl.pick.type === "entry" && mcl.pick.entry === mclKey(e);
  return el("button", { type: "button", class: `mcl-chip${e.late ? " late" : ""}${on ? " on" : ""}${e.kind === "late" ? " group" : ""}`, style: `--c:${e.color}`,
    title: e.text, onclick: (ev) => { ev.stopPropagation(); mcl.pick = { type: "entry", entry: mclKey(e) }; renderMcl(); } }, e.text);
}

const mclKey = (e) => `${e.kind}:${e.group || e.id}:${e.kind === "machine" ? e.main : ""}`;

function mclMonth(byDay) {
  const today = mcl.plan.today;
  const first = mcl.cursor.slice(0, 8) + "01";
  const [y, m] = first.split("-").map(Number);
  $("mclTitle").textContent = `${MG_MONTHS[m - 1]} ${y + 543}`;
  const start = mclMonday(first);
  const cells = MCL_WD.map((w) => el("div", { class: "mcl-wd", text: w }));
  for (let i = 0; i < 42; i++) {
    const iso = mclAdd(start, i);
    if (i === 35 && iso.slice(0, 7) !== first.slice(0, 7)) break;          // a 5-week month needs no 6th row
    const list = byDay.get(iso) || [];
    const cap = iso === today ? Infinity : MCL_MONTH_CHIPS;               // today shows everything: overdue work piles up there on purpose
    const shown = list.slice(0, list.length > cap + 1 ? cap : cap + 1);
    const more = list.length - shown.length;
    cells.push(el("div", { class: `mcl-day${iso.slice(0, 7) !== first.slice(0, 7) ? " out" : ""}${iso === today ? " today" : ""}${mclPickedDay(iso) ? " on" : ""}`,
      onclick: () => { mcl.pick = { type: "day", day: iso }; renderMcl(); } },
      el("div", { class: "mcl-num", text: Number(iso.slice(8)) === 1 ? `1 ${MG_MONTHS[Number(iso.slice(5, 7)) - 1].slice(0, 3)}` : String(Number(iso.slice(8))) }),
      ...shown.map(mclChip),
      more > 0 ? el("button", { type: "button", class: "mcl-more", text: `+${more} เพิ่มเติม`, onclick: (ev) => { ev.stopPropagation(); mcl.pick = { type: "day", day: iso }; renderMcl(); } }) : null));
  }
  fill($("mclCal"), el("div", { class: "mcl-month" }, ...cells));
}

function mclWeek(byDay) {
  const today = mcl.plan.today;
  const start = mclMonday(mcl.cursor), end = mclAdd(start, 6);
  const [sy, sm, sd] = start.split("-").map(Number), [ey, em, ed] = end.split("-").map(Number);
  $("mclTitle").textContent = sm === em ? `${sd}–${ed} ${MG_MONTHS[sm - 1]} ${sy + 543}` : `${sd} ${MG_MONTHS[sm - 1].slice(0, 3)} – ${ed} ${MG_MONTHS[em - 1].slice(0, 3)} ${ey + 543}`;
  const cols = [];
  for (let i = 0; i < 7; i++) {
    const iso = mclAdd(start, i);
    const list = byDay.get(iso) || [];
    cols.push(el("div", { class: `mcl-wcol${iso === today ? " today" : ""}${mclPickedDay(iso) ? " on" : ""}`, onclick: () => { mcl.pick = { type: "day", day: iso }; renderMcl(); } },
      el("div", { class: "mcl-whead" }, el("span", { class: "muted small", text: MCL_WD[i] }), el("strong", { text: String(Number(iso.slice(8))) })),
      ...(list.length ? list.map(mclChip) : [el("p", { class: "muted small mcl-empty", text: "—" })])));
  }
  fill($("mclCal"), el("div", { class: "mcl-week" }, ...cols));
}

function mclYear(byDay) {
  const today = mcl.plan.today;
  const y = Number(mcl.cursor.slice(0, 4));
  $("mclTitle").textContent = `พ.ศ. ${y + 543}`;
  const months = [];
  for (let m = 1; m <= 12; m++) {
    const first = `${y}-${String(m).padStart(2, "0")}-01`;
    const start = mclMonday(first), days = new Date(y, m, 0).getDate();
    const cells = MCL_WD.map((w) => el("div", { class: "mcl-ywd", text: w }));
    const lead = (mclDate(first).getDay() + 6) % 7;
    for (let i = 0; i < lead; i++) cells.push(el("div"));
    for (let d = 1; d <= days; d++) {
      const iso = `${first.slice(0, 8)}${String(d).padStart(2, "0")}`;
      const list = byDay.get(iso) || [];
      const dots = [...new Set(list.map((e) => e.color))].slice(0, 3);
      cells.push(el("button", { type: "button", class: `mcl-yday${iso === today ? " today" : ""}${list.some((e) => e.late) ? " late" : ""}`,
        title: list.length ? `${list.length} รายการ` : "", onclick: () => { mcl.view = "month"; mcl.cursor = iso; mcl.pick = { type: "day", day: iso }; renderMcl(); } },
        el("span", { text: String(d) }), list.length ? el("span", { class: "mcl-dots" }, ...dots.map((c) => el("i", { style: `background:${c}` }))) : null));
    }
    void start;
    months.push(el("div", { class: "mcl-ymonth" }, el("button", { type: "button", class: "mcl-ytitle", text: MG_MONTHS[m - 1],
      onclick: () => { mcl.view = "month"; mcl.cursor = first; renderMcl(); } }), el("div", { class: "mcl-ygrid" }, ...cells)));
  }
  fill($("mclCal"), el("div", { class: "mcl-year" }, ...months));
}

const mclPickedDay = (iso) => mcl.pick && mcl.pick.type === "day" && mcl.pick.day === iso;

// ------------------------------------------------------------------ the side panel
function mclSide(byDay) {
  const all = [...byDay.values()].flat();
  let body;
  if (mcl.pick && mcl.pick.type === "entry") {
    const e = all.find((x) => mclKey(x) === mcl.pick.entry);
    body = e ? mclDetail(e) : null;
  } else if (mcl.pick && mcl.pick.type === "day") {
    const list = byDay.get(mcl.pick.day) || [];
    body = [el("h3", { class: "section-title", text: beDate(mcl.pick.day) }), ...(list.length ? list.map(mclRow) : [el("p", { class: "muted small", text: "ไม่มีรายการ" })])];
  }
  if (!body) body = mclOverview(byDay);
  fill($("mclSide"), mcl.pick ? el("button", { type: "button", class: "btn small mcl-back", text: "← ภาพรวม", onclick: () => { mcl.pick = null; renderMcl(); } }) : null, ...[body].flat());
}

// Nothing picked: today, the next 14 days, and the trucks / machines with no oil change on record.
function mclOverview(byDay) {
  const today = mcl.plan.today;
  const soon = [];
  for (let i = 1; i <= mcl.plan.soon_days; i++) soon.push(...(byDay.get(mclAdd(today, i)) || []));
  const keep = (e) => mcl.kinds[e.kind] && (!mcl.branch || e.branch === mcl.branch);
  const nodate = mcl.plan.no_date.filter(keep);
  const check = nodate.filter((e) => e.green_tick), waiting = nodate.filter((e) => !e.green_tick);
  return [
    el("h3", { class: "section-title", text: `วันนี้ · ${beDate(today)}` }),
    ...((byDay.get(today) || []).map(mclRow)), (byDay.get(today) || []).length ? null : el("p", { class: "muted small", text: "ไม่มีรายการ" }),
    el("h3", { class: "section-title", text: `อีก ${mcl.plan.soon_days} วันข้างหน้า (${soon.length})` }),
    ...(soon.length ? soon.map(mclRow) : [el("p", { class: "muted small", text: "ไม่มีรายการ" })]),
    check.length ? el("h3", { class: "section-title mcl-check", text: `ติ๊กเขียวแล้ว แต่ไม่มีบันทึกถ่ายน้ำมัน (${check.length}) — ควรเช็ค` }) : null,
    ...check.map((e) => mclNoDate(e, true)),
    waiting.length ? el("details", { class: "mcl-wait" }, el("summary", { class: "muted small", text: `ประวัติยังไม่ครบ ยังไม่มีวันถ่ายน้ำมัน (${waiting.length})` }),
      ...waiting.map((e) => mclNoDate(e, false))) : null,
  ];
}

function mclNoDate(e, strong) {
  const href = e.kind === "truck" ? `#truck=${e.id}` : `#machine=${e.id}`;
  return el("div", { class: `mcl-row${strong ? "" : " quiet"}` }, el("a", { href, text: e.label }), el("span", { class: "muted small", text: ` · ${e.main}${e.branch ? ` · ${e.branch}` : ""}` }));
}

function mclWhen(days) {
  if (days === null || days === undefined) return "";
  return days < 0 ? `เลย ${-days} วัน` : days === 0 ? "วันนี้" : `อีก ${days} วัน`;
}

// One line in a list (a day's items, today, the next 14 days): click for the details.
function mclRow(e) {
  return el("button", { type: "button", class: `mcl-row mcl-rowbtn${e.late ? " late" : ""}`, style: `--c:${e.color}`, onclick: () => { mcl.pick = { type: "entry", entry: mclKey(e) }; renderMcl(); } },
    el("span", { class: "mgdot" }), el("span", { text: e.text }),
    e.kind !== "late" ? el("span", { class: "muted small", text: ` · ${e.date !== mcl.plan.today || e.kind === "job" && e.due !== e.date ? `${beDate(e.due || e.date)} · ` : ""}${mclWhen(e.days_left)}` }) : null);
}

function mclDetail(e) {
  if (e.kind === "late") {
    return [el("h3", { class: "section-title", text: e.text }), el("p", { class: "muted small", text: "เรียงจากเลยนานสุด · งานอื่นทำพร้อมรอบถ่ายน้ำมันนี้ · กดชื่อเพื่อเปิดหน้ารถ / เครื่อง" }),
      ...e.items.map((x) => mclVisit(x, true))];
  }
  if (e.kind === "job") {
    const t = e.ticket;
    return [el("div", { class: "mcl-dhead", style: `--c:${e.color}` }, el("span", { class: "mgdot" }), el("span", { class: "muted small", text: e.main })),
      el("h3", { class: "section-title", text: t.title }),
      el("p", { class: e.late ? "error small" : "muted small", text: `กำหนด ${beDate(t.due_date)}${t.due_time ? ` ${String(t.due_time).slice(0, 5)}` : ""} · ${mclWhen(e.days_left)}` }),
      t.place ? el("p", { class: "small", text: `ที่ ${t.place}` }) : null,
      t.notes ? el("p", { class: "small mcl-notes", text: t.notes }) : null,
      el("div", { class: "mcl-actions" }, el("button", { type: "button", class: "btn small primary", text: "แก้ไข", onclick: () => openMgEdit(t) }),
        el("a", { class: "btn small", href: "#mgr", text: "เปิดบอร์ด" }))];
  }
  return mclVisit(e, false);
}

function mclVisit(e, compact) {
  const href = e.kind === "truck" ? `#truck=${e.id}` : `#machine=${e.id}`;
  return el("div", { class: `mcl-visit${e.late ? " late" : ""}`, style: `--c:${e.color}` },
    compact ? null : el("div", { class: "mcl-dhead" }, el("span", { class: "mgdot" }), el("span", { class: "muted small", text: e.kind === "truck" ? "รอบซ่อมบำรุงรถ" : "รอบซ่อมบำรุงเครื่องจักร" })),
    el(compact ? "div" : "h3", { class: compact ? "mcl-vtitle" : "section-title" }, el("a", { href, text: e.label })),
    el("p", { class: e.late ? "error small" : "muted small", text: `${e.main} · ${beDate(e.date)} · ${mclWhen(e.days_left)}${e.branch ? ` · ${e.branch}` : ""}${e.at_shop ? " · อยู่ที่ร้าน" : ""}` }),
    e.together && e.together.length ? el("div", { class: "mcl-along" }, el("strong", { text: "ทำพร้อมกันเลย" }), el("ul", {}, ...e.together.map((x) => el("li", { text: x })))) : null,
    e.maybe && e.maybe.length ? el("div", { class: "mcl-along maybe" }, el("strong", { text: "ถ้ามีโอกาส" }), el("ul", {}, ...e.maybe.map((x) => el("li", { text: x })))) : null);
}
