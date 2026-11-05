"use strict";

// ผู้จัดการ › ปฏิทิน (#mgr-calendar): a Google-style calendar of what is coming.
//   * trucks / machines on the day their OIL CHANGE is due (/api/mgr/calendar, app/calendar_plan.py): "ถึงรอบถ่ายน้ำมัน", and what
//     to do in the same visit ("ทำพร้อมกันเลย") and the items with no history ("ยังไม่มีประวัติ")
//   * the board's open jobs on their due date (/api/mgr/board), and the routine work (งานประจำ) on the days it will come up
// Each day shows GROUPS, not every item: trucks, machines, one group per job type, and the repair follow-ups (ตามบิล / โทรถามคนขับ /
// ถามความคืบหน้า) as one. Overdue things are grouped on TODAY. Click a group and the panel on the right goes down a level:
// day -> group -> item (a "‹" line goes back up). Colours are the ticket types' colours from the settings page; red edge = overdue.
// Month (default) / week / year views. Loaded after mgr.js (uses mg, MG_MONTHS, openMgEdit).

const mcl = {
  view: "month", cursor: "", pick: null, plan: null, branch: "", wired: false,
  kinds: { truck: true, machine: true, job: true, routine: true },
};
const MCL_VIEWS = [["month", "เดือน"], ["week", "สัปดาห์"], ["year", "ปี"]];
const MCL_KINDS = [["truck", "รถ"], ["machine", "เครื่องจักร"], ["job", "งานบอร์ด"], ["routine", "งานประจำ"]];
const MCL_WD = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"];
const MCL_MONTH_CHIPS = 3;                       // groups a day shows in the month view before "+N เพิ่มเติม" (today shows all)
const MCL_SPREAD = { month: 3, week: 6 };         // a day with this many items or fewer shows each item instead of its groups

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

// ------------------------------------------------------------------ items and groups
function mclType(key) { return (mg.data && mg.data.types.find((x) => x.key === key)) || null; }
function mclTypeColor(key, fallback) { const t = mclType(key); return (t && t.color) || fallback; }
function mclDays(iso) { return Math.round((mclDate(iso) - mclDate(mcl.plan.today)) / 86400000); }

// Every item that can go on the calendar, filtered. `date` = the day it sits on (overdue ones sit on today); `group` = which group it joins.
function mclItems() {
  const today = mcl.plan.today;
  const keepBranch = (e) => !mcl.branch || !e.branch || e.branch === mcl.branch;
  const out = [];
  for (const e of mcl.plan.entries) {
    if (!mcl.kinds[e.kind] || !keepBranch(e)) continue;
    const late = e.days_left < 0;
    const shop = e.at_shop ? " (ที่ร้าน)" : "";
    out.push({ ...e, key: `${e.kind}:${e.id}:${e.main}`, due: e.date, date: late ? today : e.date, late,
      color: mclTypeColor(e.kind === "truck" ? "truck_plan" : "machine_plan", e.kind === "truck" ? "#92400e" : "#475569"),
      group: `${e.kind}${late ? "-late" : ""}`, text: `${e.label}${shop}` });
  }
  if (mcl.kinds.job) {
    for (const t of (mg.data ? mg.data.tickets : [])) {
      if (!t.due_date) continue;
      const days = mclDays(t.due_date);
      const follow = !!t.request_kind;                       // made by the repair board: ตามบิล / โทรถามคนขับ / ถามความคืบหน้า
      out.push({ kind: "job", key: `job:${t.id}`, ticket: t, date: days < 0 ? today : t.due_date, due: t.due_date, days_left: days, late: days < 0,
        color: follow ? mclTypeColor("chase", "#2563eb") : mclTypeColor(t.type_key, "#6b7280"), group: follow ? "follow" : `type:${t.type_key || "other"}`,
        text: `${t.due_time ? `${String(t.due_time).slice(0, 5)} ` : ""}${t.title}`, main: t.type_label || "" });
    }
  }
  if (mcl.kinds.routine) {
    for (const r of mcl.plan.routine || []) {
      out.push({ ...r, key: `routine:${r.id}`, due: r.date, late: false, color: mclTypeColor(r.type_key, "#6b7280"), group: `type:${r.type_key || "other"}`,
        text: `↻ ${r.time ? `${r.time} ` : ""}${r.label}` });
    }
  }
  return out;
}

const MCL_GROUP_ORDER = ["truck-late", "machine-late", "follow", "truck", "machine"];

function mclGroupLabel(key, n) {
  if (key === "truck-late") return `รถเลยรอบถ่ายน้ำมัน ${n} คัน`;
  if (key === "machine-late") return `เครื่องจักรเลยรอบถ่ายน้ำมัน ${n} เครื่อง`;
  if (key === "truck") return `รถถึงรอบถ่ายน้ำมัน ${n} คัน`;
  if (key === "machine") return `เครื่องจักรถึงรอบ ${n} รายการ`;
  if (key === "follow") return `ติดตามรถซ่อม ${n} เรื่อง`;
  const t = mclType(key.slice(5));
  return `${t ? t.label : "อื่นๆ"} ${n} เรื่อง`;
}

// day -> its groups [{key, items, color, late, text}], in a fixed order (overdue first).
function mclGroupsByDay(items) {
  const days = new Map();
  for (const it of items) {
    if (!days.has(it.date)) days.set(it.date, new Map());
    const g = days.get(it.date);
    if (!g.has(it.group)) g.set(it.group, []);
    g.get(it.group).push(it);
  }
  const rank = (k) => { const i = MCL_GROUP_ORDER.indexOf(k); return i < 0 ? 10 : i; };
  const out = new Map();
  for (const [day, g] of days) {
    out.set(day, [...g.entries()].map(([key, list]) => {
      list.sort((a, b) => (a.days_left ?? 0) - (b.days_left ?? 0) || String(a.text).localeCompare(String(b.text)));
      return { key, day, items: list, color: list[0].color, late: list.some((x) => x.late), routineOnly: list.every((x) => x.kind === "routine"),
        text: list.length === 1 ? list[0].text : mclGroupLabel(key, list.length) };
    }).sort((a, b) => Number(b.late) - Number(a.late) || rank(a.key) - rank(b.key) || a.text.localeCompare(b.text)));
  }
  return out;
}

// The chips one day shows: its groups -- or, when the day holds only a few items, every item on its own (room for them anyway).
// Either way a chip opens the same place in the panel (an item still sits in its group there: "‹" goes back to it).
function mclDayChips(groups, view) {
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  if (total <= (MCL_SPREAD[view] || 0) && groups.some((g) => g.items.length > 1)) {
    return groups.flatMap((g) => g.items.map((it) => ({ ...g, items: [it], color: it.color, late: it.late, routineOnly: it.kind === "routine", text: it.text, single: g })));
  }
  return groups;
}

// ------------------------------------------------------------------ drawing
function renderMcl() {
  if (!mcl.plan) return;
  fill($("mclViews"), ...MCL_VIEWS.map(([k, label]) => el("button", { type: "button", class: `segbtn${mcl.view === k ? " active" : ""}`, text: label,
    onclick: () => { mcl.view = k; try { localStorage.setItem("mclView", k); } catch { /* fine */ } renderMcl(); } })));
  fill($("mclFilters"), ...MCL_KINDS.map(([k, label]) => {
    const color = k === "truck" ? mclTypeColor("truck_plan", "#92400e") : k === "machine" ? mclTypeColor("machine_plan", "#475569") : k === "job" ? "var(--accent)" : "var(--muted)";
    return el("button", { type: "button", class: `chipbtn mcl-kind${mcl.kinds[k] ? " on" : ""}`, style: `--c:${color}`, "aria-pressed": String(mcl.kinds[k]),
      onclick: () => { mcl.kinds[k] = !mcl.kinds[k]; renderMcl(); } }, el("span", { class: "mgdot" }), label);
  }));
  const byDay = mclGroupsByDay(mclItems());
  if (mcl.view === "year") mclYear(byDay);
  else if (mcl.view === "week") mclWeek(byDay);
  else mclMonth(byDay);
  mclSide(byDay);
}

// Click a group: the panel shows it (and goes straight to the item when the group holds one).
function mclOpen(day, group, item) {
  mcl.pick = { day, group: group || null, item: item || null };
  renderMcl();
}

function mclChip(g) {
  const on = mcl.pick && mcl.pick.day === g.day && mcl.pick.group === g.key && (!g.single || mcl.pick.item === g.items[0].key);
  const atShop = g.items.length === 1 && g.items[0].at_shop;
  return el("button", { type: "button", class: `mcl-chip${g.late ? " late" : ""}${on ? " on" : ""}${g.items.length > 1 ? " group" : ""}${atShop ? " shop" : ""}${g.routineOnly ? " routine" : ""}`,
    style: `--c:${g.color}`, title: g.text,
    onclick: (ev) => { ev.stopPropagation(); mclOpen(g.day, g.key, g.items.length === 1 ? g.items[0].key : null); } }, g.text);
}

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
    const groups = mclDayChips(byDay.get(iso) || [], "month");
    const cap = iso === today ? Infinity : MCL_MONTH_CHIPS;
    const shown = groups.slice(0, groups.length > cap + 1 ? cap : cap + 1);
    const more = groups.length - shown.length;
    cells.push(el("div", { class: `mcl-day${iso.slice(0, 7) !== first.slice(0, 7) ? " out" : ""}${iso === today ? " today" : ""}${mcl.pick && mcl.pick.day === iso ? " on" : ""}`,
      onclick: () => mclOpen(iso) },
      el("div", { class: "mcl-num", text: String(Number(iso.slice(8))) }),
      ...shown.map(mclChip),
      more > 0 ? el("button", { type: "button", class: "mcl-more", text: `+${more} เพิ่มเติม`, onclick: (ev) => { ev.stopPropagation(); mclOpen(iso); } }) : null));
  }
  fill($("mclCal"), el("div", { class: "mcl-month" }, ...cells));
}

function mclWeek(byDay) {
  const today = mcl.plan.today;
  const start = mclMonday(mcl.cursor), end = mclAdd(start, 6);
  const [sy, sm, sd] = start.split("-").map(Number), [ey, em, ed] = end.split("-").map(Number);
  $("mclTitle").textContent = sm === em ? `${sd}–${ed} ${MG_MONTHS[sm - 1]} ${sy + 543}` : `${sd} ${MG_MONTHS[sm - 1]} – ${ed} ${MG_MONTHS[em - 1]} ${ey + 543}`;
  const cols = [];
  for (let i = 0; i < 7; i++) {
    const iso = mclAdd(start, i);
    const groups = mclDayChips(byDay.get(iso) || [], "week");
    cols.push(el("div", { class: `mcl-wcol${iso === today ? " today" : ""}${mcl.pick && mcl.pick.day === iso ? " on" : ""}`, onclick: () => mclOpen(iso) },
      el("div", { class: "mcl-whead" }, el("span", { class: "muted small", text: MCL_WD[i] }), el("strong", { text: String(Number(iso.slice(8))) })),
      ...(groups.length ? groups.map(mclChip) : [el("p", { class: "muted small mcl-empty", text: "—" })])));
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
    const days = new Date(y, m, 0).getDate();
    const cells = MCL_WD.map((w) => el("div", { class: "mcl-ywd", text: w }));
    for (let i = 0, lead = (mclDate(first).getDay() + 6) % 7; i < lead; i++) cells.push(el("div"));
    for (let d = 1; d <= days; d++) {
      const iso = `${first.slice(0, 8)}${String(d).padStart(2, "0")}`;
      const groups = byDay.get(iso) || [];
      const dots = [...new Set(groups.map((g) => g.color))].slice(0, 3);
      cells.push(el("button", { type: "button", class: `mcl-yday${iso === today ? " today" : ""}${groups.some((g) => g.late) ? " late" : ""}`,
        title: groups.length ? groups.map((g) => g.text).join("\n") : "", onclick: () => { mcl.view = "month"; mcl.cursor = iso; mclOpen(iso); } },
        el("span", { text: String(d) }), groups.length ? el("span", { class: "mcl-dots" }, ...dots.map((c) => el("i", { style: `background:${c}` }))) : null));
    }
    months.push(el("div", { class: "mcl-ymonth" }, el("button", { type: "button", class: "mcl-ytitle", text: MG_MONTHS[m - 1],
      onclick: () => { mcl.view = "month"; mcl.cursor = first; renderMcl(); } }), el("div", { class: "mcl-ygrid" }, ...cells)));
  }
  fill($("mclCal"), el("div", { class: "mcl-year" }, ...months));
}

// ------------------------------------------------------------------ the side panel: overview -> day -> group -> item
function mclSide(byDay) {
  const p = mcl.pick;
  const groups = p ? byDay.get(p.day) || [] : [];
  const group = p && p.group ? groups.find((g) => g.key === p.group) : null;
  const item = group && p.item ? group.items.find((x) => x.key === p.item) : null;
  let crumb = null, body;
  if (item) {
    crumb = group.items.length > 1 ? mclCrumb(`‹ ${mclGroupLabel(group.key, group.items.length)}`, () => mclOpen(p.day, p.group)) : mclCrumb(`‹ ${beDate(p.day)}`, () => mclOpen(p.day));
    body = mclDetail(item);
  } else if (group) {
    crumb = mclCrumb(`‹ ${beDate(p.day)}`, () => mclOpen(p.day));
    body = [el("h3", { class: "mcl-h", text: mclGroupLabel(group.key, group.items.length) }), ...group.items.map((x) => mclItemRow(x, p.day, group.key))];
  } else if (p) {
    crumb = mclCrumb("‹ ภาพรวม", () => { mcl.pick = null; renderMcl(); });
    body = [el("h3", { class: "mcl-h", text: beDate(p.day) }), ...(groups.length ? groups.map(mclGroupRow) : [el("p", { class: "muted small", text: "ไม่มีรายการ" })])];
  } else {
    body = mclOverview(byDay);
  }
  fill($("mclSide"), crumb, ...[body].flat());
}

const mclCrumb = (text, onclick) => el("button", { type: "button", class: "mcl-crumb", text, onclick });

function mclWhen(days) {
  if (days === null || days === undefined) return "";
  return days < 0 ? `เลย ${-days} วัน` : days === 0 ? "วันนี้" : `อีก ${days} วัน`;
}

// A group as one line in the panel: its colour, name and count; click to go into it.
function mclGroupRow(g) {
  const one = g.items.length === 1 ? g.items[0] : null;
  return el("button", { type: "button", class: `mcl-row mcl-rowbtn${g.late ? " late" : ""}`, style: `--c:${g.color}`,
    onclick: () => mclOpen(g.day, g.key, one ? one.key : null) },
    el("span", { class: "mgdot" }), el("span", { class: "mcl-rowtext", text: g.text }),
    one ? el("span", { class: "muted small", text: mclWhen(one.days_left) }) : el("span", { class: "mcl-count", text: "›" }));
}

function mclItemRow(x, day, group) {
  return el("button", { type: "button", class: `mcl-row mcl-rowbtn${x.late ? " late" : ""}`, style: `--c:${x.color}`, onclick: () => mclOpen(day, group, x.key) },
    el("span", { class: "mgdot" }), el("span", { class: "mcl-rowtext", text: x.text }),
    el("span", { class: "muted small", text: x.late ? mclWhen(x.days_left) : x.kind === "truck" || x.kind === "machine" ? x.main : "" }));
}

// Nothing picked: today's groups, the next 14 days (by day), and what has no oil change on record yet.
function mclOverview(byDay) {
  const today = mcl.plan.today;
  const out = [el("h3", { class: "mcl-h", text: `วันนี้ · ${beDate(today)}` })];
  const todays = byDay.get(today) || [];
  out.push(...(todays.length ? todays.map(mclGroupRow) : [el("p", { class: "muted small", text: "ไม่มีรายการ" })]));
  out.push(el("h3", { class: "mcl-h", text: `อีก ${mcl.plan.soon_days} วันข้างหน้า` }));
  let any = false;
  for (let i = 1; i <= mcl.plan.soon_days; i++) {
    const day = mclAdd(today, i), gs = byDay.get(day) || [];
    if (!gs.length) continue;
    any = true;
    out.push(el("button", { type: "button", class: "mcl-dayhead", text: `${beDate(day)} · อีก ${i} วัน`, onclick: () => mclOpen(day) }), ...gs.map(mclGroupRow));
  }
  if (!any) out.push(el("p", { class: "muted small", text: "ไม่มีรายการ" }));
  const keep = (e) => mcl.kinds[e.kind] && (!mcl.branch || !e.branch || e.branch === mcl.branch);
  const nodate = mcl.plan.no_date.filter(keep);
  const check = nodate.filter((e) => e.green_tick), waiting = nodate.filter((e) => !e.green_tick);
  if (check.length) out.push(el("h3", { class: "mcl-h mcl-check", text: `ติ๊กเขียวแล้ว แต่ไม่มีบันทึกถ่ายน้ำมัน (${check.length}) — ควรเช็ค` }), ...check.map((e) => mclNoDate(e, true)));
  if (waiting.length) out.push(el("details", { class: "mcl-wait" }, el("summary", { class: "muted small", text: `ประวัติยังไม่ครบ ยังไม่มีวันถ่ายน้ำมัน (${waiting.length})` }),
    ...waiting.map((e) => mclNoDate(e, false))));
  return out;
}

function mclNoDate(e, strong) {
  const href = e.kind === "truck" ? `#truck=${e.id}` : `#machine=${e.id}`;
  return el("div", { class: `mcl-row${strong ? "" : " quiet"}` }, el("a", { href, text: e.label }), el("span", { class: "muted small", text: ` · ${e.main}${e.branch ? ` · ${e.branch}` : ""}` }));
}

// ------------------------------------------------------------------ one item
function mclDetail(x) {
  if (x.kind === "job") return mclJob(x);
  if (x.kind === "routine") return mclRoutine(x);
  return mclVisit(x);
}

function mclVisit(e) {
  const href = e.kind === "truck" ? `#truck=${e.id}` : `#machine=${e.id}`;
  const what = e.kind === "truck" && e.main === "น้ำมันเครื่อง" ? "ถ่ายน้ำมันเครื่อง" : e.main;
  const head = e.late ? `เลยรอบ${what} ${-e.days_left} วัน` : `ถึงรอบ${what}`;
  const shop = e.at_shop;
  return [
    el("div", { class: "mcl-dhead", style: `--c:${e.color}` }, el("span", { class: "mgdot" }), el("span", { class: "muted small", text: e.kind === "truck" ? "รอบซ่อมบำรุงรถ" : "รอบซ่อมบำรุงเครื่องจักร" })),
    el("h3", { class: "mcl-title-link" }, el("a", { href, text: e.label })),
    el("div", { class: `mcl-headline${e.late ? " late" : ""}` }, el("strong", { text: head }),
      el("span", { class: "small", text: `${beDate(e.due)}${e.late ? "" : ` · ${mclWhen(e.days_left)}`}${e.branch ? ` · ${e.branch}` : ""}` })),
    shop ? el("div", { class: "mcl-shop" }, el("strong", { text: `อยู่ที่ร้าน${shop.shop ? ` ${shop.shop}` : ""}` }),
      el("span", { class: "small", text: `${shop.since ? `ตั้งแต่ ${beDate(shop.since)} · ` : ""}ฝากทำรอบนี้พร้อมกันได้เลย` })) : null,
    e.together && e.together.length ? el("div", { class: "mcl-along" }, el("strong", { text: "ทำพร้อมกันเลย" }), el("ul", {}, ...e.together.map((t) => el("li", { text: t })))) : null,
    e.maybe && e.maybe.length ? el("div", { class: "mcl-along nohist" }, el("strong", { text: "ยังไม่มีประวัติ" }), el("ul", {}, ...e.maybe.map((t) => el("li", { text: t })))) : null,
  ];
}

function mclJob(x) {
  const t = x.ticket;
  const follow = { driver_check: "โทรถามคนขับหลังรถกลับจากร้าน", bill_chase: "ตามบิลจากร้าน", progress: "ถามความคืบหน้าร้าน" }[t.request_kind];
  return [
    el("div", { class: "mcl-dhead", style: `--c:${x.color}` }, el("span", { class: "mgdot" }), el("span", { class: "muted small", text: follow ? `ติดตามรถซ่อม · ${follow}` : x.main })),
    el("h3", { class: "mcl-h", text: t.title }),
    el("p", { class: x.late ? "error small" : "muted small", text: `กำหนด ${beDate(t.due_date)}${t.due_time ? ` ${String(t.due_time).slice(0, 5)}` : ""} · ${mclWhen(x.days_left)}` }),
    t.place ? el("p", { class: "small", text: `ที่ ${t.place}` }) : null,
    t.waiting_on ? el("p", { class: "small", text: `ติดต่อ ${t.waiting_on}${t.waiting_phone ? ` · ${t.waiting_phone}` : ""}` }) : null,
    t.notes ? el("p", { class: "small mcl-notes", text: t.notes }) : null,
    el("div", { class: "mcl-actions" }, el("button", { type: "button", class: "btn small primary", text: "แก้ไข", onclick: () => openMgEdit(t) }),
      el("a", { class: "btn small", href: "#mgr", text: "เปิดบอร์ด" })),
  ];
}

function mclRoutine(x) {
  return [
    el("div", { class: "mcl-dhead", style: `--c:${x.color}` }, el("span", { class: "mgdot" }), el("span", { class: "muted small", text: `งานประจำ${x.main ? ` · ${x.main}` : ""}` })),
    el("h3", { class: "mcl-h", text: x.label }),
    el("p", { class: "muted small", text: `${beDate(x.date)}${x.time ? ` ${x.time}` : ""} · ${mclWhen(x.days_left)}${x.described ? ` · ${x.described}` : ""}` }),
    x.place ? el("p", { class: "small", text: `ที่ ${x.place}` }) : null,
    x.notes ? el("p", { class: "small mcl-notes", text: x.notes }) : null,
    el("p", { class: "muted small", text: "ยังไม่อยู่บนบอร์ด — ถึงวันแล้วระบบสร้างเรื่องให้เอง" }),
    el("div", { class: "mcl-actions" }, el("a", { class: "btn small", href: "#mgr-recurring", text: "ดูงานประจำ" })),
  ];
}
