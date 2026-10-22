"use strict";

// รถอยู่ที่ไหน: the trucks that are out at a shop right now, grouped by shop. It reads the same
// list as the repair board (board.active) so the two can never disagree; the buttons open the
// board's own dialogs. Loaded BEFORE app.js: nothing here runs at load time.

async function openWhere() {
  showView("where");
  await ensureVehicles();                // the trucks' main drivers (shown on each row)
  await loadBoard();                     // fresh data; renderWhere() runs at the end of loadBoard
}

function renderWhere() {
  if ($("whereView").hidden) return;
  const out = board.active.filter((r) => r.status === "at_shop");
  const groups = new Map();
  for (const r of out) {
    if (!groups.has(r.shop)) groups.set(r.shop, []);
    groups.get(r.shop).push(r);
  }
  // the shops holding the most trucks first, then the longest-held
  const shops = [...groups.entries()].map(([name, rows]) => ({ name, rows: rows.sort((a, b) => atShopDays(b) - atShopDays(a)) }))
    .sort((a, b) => b.rows.length - a.rows.length || atShopDays(b.rows[0]) - atShopDays(a.rows[0]));
  const late = out.filter((r) => r.expected_return && r.expected_return < todayISO()).length;

  fill($("whereSummary"),
    el("strong", { text: out.length ? `${out.length} คันอยู่ที่อู่ · ${shops.length} ที่` : "ตอนนี้ไม่มีรถที่ส่งซ่อมอยู่" }),
    late ? el("span", { class: "badge urgent", text: `เลยกำหนด ${late} คัน` }) : null);
  $("whereList").replaceChildren(...(shops.length ? shops.map(shopSection)
    : [el("p", { class: "muted empty", text: "รถที่ส่งซ่อมจะมาแสดงตรงนี้ (กด “ส่งซ่อม →” ที่หน้างานซ่อม)" })]));
}

function shopSection(s) {
  const longest = atShopDays(s.rows[0]);
  return el("section", { class: "shopcard" },
    el("div", { class: "shophead" },
      el("h3", { text: s.name }),
      el("span", { class: "badge shop", text: `${s.rows.length} คัน` }),
      el("span", { class: "grow" }),
      ageChip(longest, "at_shop")),
    ...s.rows.map(whereRow));
}

// who normally drives it (and their number), from the fleet list
function driverLine(r) {
  const v = (fleet.vehicles || []).find((x) => x.vehicle_id === r.vehicle_id);
  if (!v || !v.driver) return null;
  return el("div", { class: "muted small", text: `คนขับหลัก ${v.driver}${v.driver_nickname ? ` (${v.driver_nickname})` : ""}${v.driver_phone ? ` · ${v.driver_phone}` : ""}` });
}

function whereRow(r) {
  const days = atShopDays(r);
  const overdue = r.expected_return && r.expected_return < todayISO() ? daysSince(r.expected_return) : 0;
  const when = [`ส่งเมื่อ ${thDate(r.sent_date)}`, r.expected_return ? `คาดว่ากลับ ${thDate(r.expected_return)}` : "ยังไม่ระบุวันกลับ"].join(" · ");
  return el("div", { class: `truckrow${overdue ? " late" : ""}` },
    el("div", { class: "truckmain" },
      el("div", { class: "req-head" },
        ...truckHead(r),
        r.branch ? el("span", { class: "muted small", text: r.branch }) : null,
        r.urgent ? el("span", { class: "badge urgent", text: "ด่วน" }) : null,
        overdue ? el("span", { class: "badge urgent", text: `เลยกำหนด ${overdue} วัน` }) : null),
      el("div", { class: "truckproblem", text: r.problem }),
      driverLine(r),
      el("div", { class: "muted small", text: `${when} · อยู่มา ${days === 0 ? "วันนี้" : `${days} วัน`}` })),
    el("div", { class: "truckbtns" },
      el("button", { class: "btn primary small", type: "button", text: "รถกลับมาแล้ว ✓", onclick: () => openReturnDialog(r) }),
      el("button", { class: "btn small", type: "button", text: "เปลี่ยนอู่ / วันที่", onclick: () => openSendDialog(r) }),
      el("button", { class: "btn small", type: "button", text: "ขั้นตอน", onclick: () => showBoardRequest(r) })));
}
