"use strict";

// Dashboard tab (ภาพรวม). All numbers come from /api/dashboard, which is
// computed server-side (app/dashboard.py, unit-tested); this file only draws
// them. Charts are plain HTML/CSS -- nothing is loaded from the internet.
//
// Loaded BEFORE app.js: nothing here may run at load time. It relies on
// helpers from app.js (state, $, el, api) at call time only.

const dash = { data: null };

const baht = (n) => Number(n || 0).toLocaleString("th-TH", { maximumFractionDigits: 0 });

function compactBaht(n) {
  if (n >= 100000) return `${(n / 1000).toFixed(0)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/[.]0$/, "")}k`;
  return String(Math.round(n));
}


const FLAG_LABELS = {
  open_requests: ["มีงานรอซ่อม", "warn"],
  at_shop: ["อยู่ที่อู่", "shop"],
  awaiting_bill: ["รอบิล", "warn"],
  not_serviced_lately: ["ไม่เข้าซ่อมนาน", ""],
  no_history: ["ยังไม่มีประวัติ", ""],
};

// ----------------------------------------------------------------- loading
async function loadDashboard() {
  const months = $("dashMonths").value;
  const branch = $("dashBranch").value;
  try {
    dash.data = await api(`/api/dashboard?months=${encodeURIComponent(months)}&branch=${encodeURIComponent(branch)}`);
    showDashError("");
  } catch (err) {
    showDashError(`โหลดภาพรวมไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderDashboard();
}

function showDashError(text) {
  const n = $("dashError");
  n.hidden = !text;
  n.textContent = text || "";
}

function fillBranchFilter() {
  const sel = $("dashBranch");
  if (sel.dataset.filled || !state.vehicles.length) return;
  const branches = [...new Set(state.vehicles.map((v) => v.branch).filter(Boolean))].sort();
  sel.replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...branches.map((b) => el("option", { value: b, text: b })));
  sel.dataset.filled = "1";
}

// --------------------------------------------------------------- rendering
function renderDashboard() {
  const d = dash.data;
  $("dashAsOf").textContent = `ข้อมูล ณ ${beDate(d.as_of)}`;
  const hasData = d.kpis.repairs_period > 0 || d.fleet.length > 0;

  renderKpis(d);
  renderMonthly(d);
  renderBars("chartCategory", d.by_category.map((c) => ({
    label: c.category, sub: c.category_en, value: c.amount, text: `${baht(c.amount)} · ${Math.round(c.share * 100)}%`,
  })), "ยังไม่มีรายการในบิลช่วงนี้");
  renderBars("chartTrucks", d.top_trucks.map((t) => ({
    label: [t.truck_number ? `#${t.truck_number}` : null, t.plate].filter(Boolean).join(" "),
    sub: `${t.count} ครั้ง${t.last_date ? ` · ล่าสุด ${beDate(t.last_date)}` : ""}`, value: t.amount, text: baht(t.amount),
  })), "ยังไม่มีข้อมูลรถ");
  renderBars("chartVendors", d.top_vendors.map((v) => ({
    label: v.vendor, sub: `${v.count} ครั้ง`, value: v.amount, text: baht(v.amount),
  })), "ยังไม่มีข้อมูลร้าน");
  renderFleet(d);

  const notes = [];
  if (!hasData) notes.push("ยังไม่มีข้อมูล — บันทึกบิลแรกหรือเพิ่มงานซ่อม แล้วภาพรวมจะขึ้นที่นี่");
  if (d.notes.events_without_cost) notes.push(`${d.notes.events_without_cost} รายการยังไม่มียอดเงิน (นับเป็นครั้งที่ซ่อม แต่ไม่รวมในค่าซ่อม)`);
  notes.push("ซ่อมเสร็จ = นับครั้งซ่อมละ 1 จากทุกที่มา (บิล/บันทึกที่บันทึกแล้ว + ปิดโดยไม่มีบิล + ซ่อมแล้วรอบิล) งานที่มีบิลแล้วไม่นับซ้ำกับใบแจ้งซ่อม · \"งานรอซ่อม/รอบิล\" นับจากใบแจ้งซ่อมที่ใส่ในระบบเท่านั้น");
  notes.push("ค่าซ่อม = ยอดรวมของบิลที่บันทึกแล้ว ตามวันที่ในบิล · แยกตามหมวดคิดจากรายการในบิล จึงอาจต่างจากยอดรวมเล็กน้อย (VAT/ส่วนลด)");
  notes.push("ปีที่แสดงทั้งหมดเป็น พ.ศ. (ระบบเก็บเป็น ค.ศ. ภายใน)");
  notes.push(`"ไม่เข้าซ่อมนาน" = ไม่มีบันทึกซ่อมเกิน ${d.notes.stale_days} วัน — เป็นแค่ตัวช่วยให้ไปดู ไม่ใช่กำหนดเข้าศูนย์`);
  $("dashNotes").replaceChildren(...notes.map((t) => el("div", { text: t })));
}

function kpi(label, value, sub, tone = "", hint = "") {
  return el("div", { class: `kpi ${tone}`.trim(), title: hint || null },
    el("div", { class: "kpi-label", text: label }),
    el("div", { class: "kpi-value", text: value }),
    Array.isArray(sub)
      // a list of short parts: each one wraps as a unit instead of breaking mid-word
      ? el("div", { class: "kpi-sub" }, ...sub.flatMap((p, i) => [i ? " · " : null, el("span", { class: "nowrap", text: p })]))
      : sub ? el("div", { class: "kpi-sub", text: sub }) : null);
}

function doneBreakdown(t) {
  if (!t.total) return "ยังไม่มีงานที่ซ่อมเสร็จ";
  return [t.records ? `มีบิล ${t.records}` : null, t.no_bill ? `ไม่มีบิล ${t.no_bill}` : null, t.awaiting ? `รอบิล ${t.awaiting}` : null].filter(Boolean);
}

function renderKpis(d) {
  const k = d.kpis;
  let vs = "";
  if (k.spend_last_month > 0) {
    const pct = Math.round(((k.spend_this_month - k.spend_last_month) / k.spend_last_month) * 100);
    vs = `เดือนก่อน ${baht(k.spend_last_month)} (${pct >= 0 ? "+" : ""}${pct}%)`;
  } else if (k.spend_last_month === 0 && d.months > 1) {
    vs = "เดือนก่อนไม่มีค่าซ่อม";
  }
  $("kpiRow").replaceChildren(
    kpi("ค่าซ่อมเดือนนี้", `${baht(k.spend_this_month)} ฿`, `จาก ${k.repairs_this_month} บิล/บันทึก · ${vs}`.replace(/ · $/, "")),
    kpi(`ค่าซ่อม ${d.months} เดือน`, `${baht(k.spend_period)} ฿`, `จาก ${k.repairs_period} บิล/บันทึก`),
    // these two come from the FOH requests you add -- a snapshot of right now, not a monthly count
    kpi("งานรอซ่อม · ตอนนี้", String(k.open_requests),
      k.open_requests ? (k.avg_days_to_fix === null ? "จากใบแจ้งซ่อมที่ใส่ไว้" : `แจ้ง→ซ่อมเฉลี่ย ${k.avg_days_to_fix} วัน`) : "ไม่มีงานค้าง",
      k.open_requests ? "warn" : "", "นับจากใบแจ้งซ่อมที่ใส่ในระบบเท่านั้น"),
    kpi("อยู่ที่อู่ · ตอนนี้", String(k.at_shop || 0),
      k.at_shop ? `นานสุด ${k.longest_at_shop_days} วัน` : "ไม่มีรถที่อู่",
      k.longest_at_shop_days >= 14 ? "bad" : k.at_shop ? "warn" : "", "รถที่ส่งซ่อมแล้วยังไม่กลับ นับจากวันที่ส่ง"),
    kpi("ซ่อมแล้ว รอบิล · ตอนนี้", String(k.awaiting_bill),
      k.awaiting_bill ? `นานสุด ${k.oldest_awaiting_days} วัน` : "ไม่มีบิลค้าง",
      k.oldest_awaiting_days >= 7 ? "bad" : k.awaiting_bill ? "warn" : "", "นับจากใบแจ้งซ่อมที่ใส่ในระบบเท่านั้น"),
    // every repair once, however it was recorded -- does not depend on drivers filling in the FOH sheet
    kpi("ซ่อมเสร็จเดือนนี้", String(k.done_this_month.total), doneBreakdown(k.done_this_month), "",
      "นับครั้งซ่อมละ 1 จากทุกที่มา: บิล/บันทึก + งานที่ปิดโดยไม่มีบิล + งานที่ซ่อมแล้วรอบิล (ถ้าบิลเข้าภายหลังจะไม่นับซ้ำ)"),
  );
}

function renderMonthly(d) {
  const box = $("chartMonthly");
  const max = Math.max(...d.monthly.map((m) => m.spend), 0);
  if (max === 0) {
    box.replaceChildren(el("p", { class: "muted small empty", text: "ยังไม่มีค่าซ่อมในช่วงนี้" }));
    return;
  }
  box.replaceChildren(el("div", { class: "cols" }, ...d.monthly.map((m, i) => {
    const current = i === d.monthly.length - 1;
    return el("div", { class: "col", title: `${beMonth(m.month)}: ${baht(m.spend)} บาท · ซ่อม ${m.repairs} ครั้ง` },
      el("span", { class: "col-val", text: m.spend ? compactBaht(m.spend) : "" }),
      el("div", { class: "col-track" }, el("div", { class: `col-bar${current ? " current" : ""}`, style: `height:${Math.max(m.spend ? 3 : 0, (m.spend / max) * 100)}%` })),
      // month over the full พ.ศ. year, on two lines so the year is never cut or ambiguous
      el("span", { class: "col-label" }, TH_MONTHS[+m.month.slice(5, 7) - 1], el("br"), String(+m.month.slice(0, 4) + 543)));
  })));
}

// horizontal bars: [{label, sub, value, text}]
function renderBars(id, items, emptyText) {
  const box = $(id);
  if (!items.length) {
    box.replaceChildren(el("p", { class: "muted small empty", text: emptyText }));
    return;
  }
  const max = Math.max(...items.map((i) => i.value), 1);
  box.replaceChildren(...items.map((i) =>
    el("div", { class: "hbar" },
      el("div", { class: "hbar-head" },
        el("span", {}, el("strong", { text: i.label }), i.sub ? el("span", { class: "hbar-sub muted small", text: i.sub }) : null),
        el("span", { class: "hbar-text", text: i.text })),
      el("div", { class: "hbar-track" }, el("div", { class: "hbar-fill", style: `width:${Math.max(1, (i.value / max) * 100)}%` })))));
}

function renderFleet(d) {
  const onlyAttention = $("fleetAttention").checked;
  const rows = d.fleet.filter((f) => !onlyAttention || f.flags.length);
  const head = ["รถ", "สาขา", "เข้าซ่อมล่าสุด", "ไมล์ล่าสุด → ครั้งต่อไป", "งานค้าง", `ค่าซ่อม ${d.months} ด.`, "ควรดู"]
    .map((t) => el("th", { text: t }));
  if (!rows.length) {
    $("fleetTable").replaceChildren(el("tbody", {}, el("tr", {}, el("td", { colspan: "7", class: "muted small", text: onlyAttention ? "ไม่มีคันที่ต้องดู 🎉" : "ยังไม่มีรถในระบบ" }))));
    return;
  }
  $("fleetTable").replaceChildren(
    el("thead", {}, el("tr", {}, ...head)),
    el("tbody", {}, ...rows.map((f) => el("tr", {},
      el("td", {}, el("strong", { text: f.truck_number ? `#${f.truck_number} ` : "" }), f.plate),
      el("td", { text: f.branch || "" }),
      el("td", { text: f.last_service_date ? `${beDate(f.last_service_date)} (${f.days_since_service} วันก่อน)` : "–" }),
      el("td", { text: f.last_known_mileage ? `${baht(f.last_known_mileage)}${f.next_service_mileage ? ` → ${baht(f.next_service_mileage)}` : ""}` : "–" }),
      el("td", { text: [f.open_requests ? `รอซ่อม ${f.open_requests}` : null, f.at_shop_requests ? `อยู่ที่อู่ ${f.at_shop_requests}` : null, f.awaiting_bill_requests ? `รอบิล ${f.awaiting_bill_requests}` : null].filter(Boolean).join(" · ") || "–" }),
      el("td", { class: "num", text: f.spend_period ? baht(f.spend_period) : "–" }),
      el("td", {}, ...f.flags.map((fl) => el("span", { class: `badge ${FLAG_LABELS[fl][1]}`.trim(), text: FLAG_LABELS[fl][0] })))))));
}

// --------------------------------------------------------------------- init
function initDashboard() {
  fillBranchFilter();
  $("dashMonths").addEventListener("change", loadDashboard);
  $("dashBranch").addEventListener("change", loadDashboard);
  $("dashRefresh").addEventListener("click", loadDashboard);
  $("fleetAttention").addEventListener("change", () => dash.data && renderFleet(dash.data));
}
