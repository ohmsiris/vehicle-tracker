"use strict";

// น้ำมันเงินสด (#cash-fuel, owner 11 Nov 2026): the bookkeeper's รายการค่าน้ำมัน, done by IceBox. Photos of the handwritten รายการเบิก
// pages go in (twice a month), Claude reads every page twice, the fuel lines come out with their truck and driver; the bookkeeper
// checks them against the photo, and downloads the summary for HQ (per truck: plate, driver, every fill, total). Server: app/cash_fuel.py.

const cf_ = { month: null, batch: 0, data: null, pageUrls: {}, shown: null };
const CF_TH = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
const cfDmy = (iso) => (iso ? iso.split("-").reverse().join("/") : "");
const cfBe = (iso) => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${CF_TH[m]} ${y + 543}`; };
const cfShift = (month, n) => { let [y, m] = month.split("-").map(Number); m += n; while (m < 1) { m += 12; y -= 1; } while (m > 12) { m -= 12; y += 1; } return `${y}-${String(m).padStart(2, "0")}`; };

async function openCashFuel(sub = "") {
  showView("cashFuel");
  showErr("cfError", "");
  if (/^\d{4}-\d{2}$/.test(sub)) cf_.month = sub;
  fill($("cfBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { cf_.data = await api(`/api/cash-fuel${cf_.month ? `?month=${cf_.month}` : ""}`); }
  catch (err) { fill($("cfBody"), null); showErr("cfError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  cf_.month = cf_.data.month;
  cfRender();
}

// คนเบิก: the drivers, คนขับรถสาย and หน้าโรง in their own groups (owner, 11 Nov 2026)
function cfPeopleSelect(value, onChange) {
  const d = cf_.data, known = new Set();
  const groups = (d.groups || []).map((g) => el("optgroup", { label: g.label }, ...(d.people[g.key] || []).map((p) => {
    known.add(p.name);
    const label = `${p.nickname ? `${p.nickname} · ` : ""}${p.name}${p.truck ? ` (รถ ${p.truck})` : ""}`;
    return el("option", { value: p.name, text: label, selected: p.name === value });
  })));
  const extra = value && !known.has(value) ? el("option", { value, text: `${value} (ไม่อยู่ในรายชื่อ)`, selected: true }) : null;
  return el("select", { class: "cf-sel cf-ppl", onchange: (e) => onChange(e.target.value || null) },
    el("option", { value: "", text: "— เลือกคนเบิก —", selected: !value }), extra, ...groups);
}

function cfVehicleLabel(v) { return v ? `${v.truck_number ? `${v.truck_number} · ` : ""}${v.plate || ""}${v.branch && v.branch !== "สระบุรี" ? ` (${v.branch})` : ""}` : ""; }

function cfRender() {
  const d = cf_.data;
  const [y, m] = d.month.split("-").map(Number);
  const lines = d.lines.filter((l) => !cf_.batch || l.batch === cf_.batch);
  const counted = lines.filter((l) => l.counted);
  const toCheck = lines.filter((l) => l.note && !l.checked).length;
  const nav = el("div", { class: "ss-daynav card" },
    el("button", { class: "btn ss-arrow", type: "button", text: "‹", "aria-label": "เดือนก่อน", onclick: () => goHash(`#cash-fuel/${cfShift(d.month, -1)}`) }),
    el("div", { class: "ss-daypick" }, el("b", { class: "ss-daybig", text: `${CF_TH[m]} ${y + 543}` }), el("span", { class: "muted small", text: `${cfBe(d.from)} – ${cfBe(d.to)}` })),
    el("button", { class: "btn ss-arrow", type: "button", text: "›", "aria-label": "เดือนถัดไป", onclick: () => goHash(`#cash-fuel/${cfShift(d.month, 1)}`) }),
    el("div", { class: "seg ss-tabs" }, ...[[0, "ทั้งเดือน"], [1, "งวด 1 (26–10)"], [2, "งวด 2 (11–25)"]].map(([b, t]) =>
      el("button", { type: "button", class: `segbtn${cf_.batch === b ? " active" : ""}`, text: t, onclick: () => { cf_.batch = b; cfRender(); } }))));
  const tile = (label, value, cls = "") => el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: label }), el("b", { class: cls, text: value }));
  const tiles = el("div", { class: "ss-tiles" },
    tile("รวมค่าน้ำมันเงินสด", `฿${elN(counted.reduce((a, l) => a + Number(l.amount || 0), 0))}`),
    tile("จำนวนครั้ง", String(counted.length)),
    tile("รถ", `${new Set(counted.map((l) => l.vehicle_id || "?")).size} คัน`),
    tile("ต้องตรวจกับรูป", String(toCheck), toCheck ? "warn-text" : ""));
  // upload
  const pick = el("input", { type: "file", accept: "image/*,application/pdf", multiple: true, id: "cfFiles" });
  const status = el("span", { class: "muted small" });
  const up = el("div", { class: "card cf-upload" },
    el("b", { text: "อัปโหลดรูปรายการเบิก" }),
    el("span", { class: "muted small", text: "ถ่ายรูปหน้ารายการเบิกให้เห็นทั้งหน้า · อ่านทุกหน้า 2 รอบ (ใช้เวลาประมาณ 1 นาทีต่อชุด) · รูปเดิมอัปโหลดซ้ำไม่ได้ · เดือน / งวด ดูจากวันที่ของแต่ละบรรทัดเอง" }),
    el("div", { class: "cf-uprow" }, pick, el("button", { class: "btn primary", type: "button", text: "อ่านรูป", onclick: () => cfUpload(pick, status) }), status));
  // lines
  const vopts = d.vehicles.slice().sort((a, b) => (Number(a.truck_number) || 999) - (Number(b.truck_number) || 999));
  const pageName = Object.fromEntries(d.pages.map((p, i) => [p.id, `รูป ${i + 1}`]));
  const body = el("tbody", {}, ...lines.map((l) => {
    const save = async (patch) => {
      try { Object.assign(l, await api(`/api/cash-fuel/lines/${l.id}`, jsonOpts("PATCH", patch))); } catch (err) { alert(err.message); }
      cfPaintSummary();
    };
    const sel = el("select", { class: "cf-sel", onchange: (e) => save({ vehicle_id: e.target.value || null }) },
      el("option", { value: "", text: "— ไม่ทราบรถ —" }), ...vopts.map((v) => el("option", { value: v.id, text: cfVehicleLabel(v), selected: v.id === l.vehicle_id })));
    const amt = el("input", { class: "ss-in cf-amt", type: "text", inputmode: "decimal", value: String(Number(l.amount)), onchange: (e) => save({ amount: e.target.value }) });
    const drv = cfPeopleSelect(l.driver_name, (v) => save({ driver_name: v }));
    const day = el("input", { type: "date", value: l.day, class: "cf-day", onchange: (e) => e.target.value && save({ day: e.target.value }).then(() => openCashFuel(cf_.month)) });
    return el("tr", { class: `${l.counted ? "" : "cf-off"}${l.note && !l.checked ? " cu-review" : ""}` },
      el("td", {}, day),
      el("td", {}, sel),
      el("td", {}, drv),
      el("td", { class: "r" }, amt),
      el("td", { class: "small" }, el("div", { text: l.text || "" }), el("div", { class: "muted", text: [l.requester, l.position].filter(Boolean).join(" · ") }),
        l.note ? el("div", { class: "warn-text", text: l.note }) : null),
      el("td", {}, l.page_id ? el("button", { class: "btn small", type: "button", text: pageName[l.page_id] || "รูป", onclick: () => cfShowPage(l.page_id) }) : el("span", { class: "muted small", text: "เพิ่มเอง" })),
      el("td", { class: "c" }, el("label", { class: "cf-chk", title: "ตรวจกับรูปแล้ว" }, el("input", { type: "checkbox", checked: !!l.checked, onchange: (e) => save({ checked: e.target.checked }).then(cfRender) }), " ตรวจแล้ว")),
      el("td", { class: "c" }, el("label", { class: "cf-chk", title: "นับในรายการค่าน้ำมัน" }, el("input", { type: "checkbox", checked: !!l.counted, onchange: (e) => save({ counted: e.target.checked }).then(cfRender) }), " นับ")),
      el("td", {}, el("button", { class: "btn small", type: "button", text: "ลบ", onclick: async () => {
        if (!confirm("ลบรายการนี้?")) return;
        try { await api(`/api/cash-fuel/lines/${l.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
        openCashFuel(cf_.month);
      } })));
  }));
  const table = el("div", { class: "ss-tablewrap soft" }, el("table", { class: "ss-table cf-table" },
    el("thead", {}, el("tr", {}, ...["วันที่", "รถ", "คนเบิก", "ยอด", "ที่อ่านได้", "รูป", "", "", ""].map((h) => el("th", { text: h })))), body));
  const pages = d.pages.length ? el("div", { class: "cf-pages" }, el("span", { class: "small muted", text: "รูปในเดือนนี้:" }),
    ...d.pages.map((p, i) => el("span", { class: "cf-page" },
      el("button", { class: "btn small", type: "button", text: `รูป ${i + 1}${p.status === "error" ? " ⚠" : ""}`, title: p.name || "", onclick: () => cfShowPage(p.id) }),
      el("button", { class: "btn small", type: "button", text: "×", title: "ลบรูปนี้และรายการจากรูปนี้", onclick: async () => {
        if (!confirm(`ลบ ${p.name || "รูปนี้"} และทุกรายการที่อ่านจากรูปนี้?`)) return;
        try { await api(`/api/cash-fuel/pages/${p.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
        openCashFuel(cf_.month);
      } })))) : null;
  const viewer = el("div", { class: "cf-viewer", id: "cfViewer" }, el("p", { class: "muted small", text: "กด “รูป” ที่บรรทัดเพื่อดูหน้ารายการเบิกตรงนี้" }));
  fill($("cfBody"), nav, tiles, up, pages,
    el("div", { class: "cf-split" },
      el("div", {},
        el("div", { class: "ss-custbar" }, el("b", { text: `รายการเติมน้ำมัน (${lines.length})` }), el("span", { class: "grow" }),
          el("button", { class: "btn small", type: "button", text: "+ เพิ่มรายการเอง", onclick: cfAddLine })),
        lines.length ? table : el("p", { class: "muted", text: "ยังไม่มีรายการ — อัปโหลดรูปรายการเบิกด้านบน" })),
      viewer),
    el("div", { class: "card", id: "cfSummary" }));
  cfPaintSummary();
  if (cf_.shown) cfShowPage(cf_.shown);
}

async function cfUpload(pick, status) {
  const files = [...pick.files];
  if (!files.length) { status.textContent = "เลือกรูปก่อน"; return; }
  let added = 0, lines = 0;
  const notes = [];
  for (let i = 0; i < files.length; i += 4) {
    status.textContent = `กำลังอ่าน ${i + 1}–${Math.min(i + 4, files.length)} จาก ${files.length} รูป (อ่าน 2 รอบ)…`;
    const fd = new FormData();
    files.slice(i, i + 4).forEach((f) => fd.append("files", f));
    try {
      const res = await fetch("/api/cash-fuel/pages", { method: "POST", body: fd, headers: { "X-App-Token": state.token } });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);
      added += j.added.length; lines += j.lines;
      notes.push(...j.refused, ...j.added.filter((a) => a.error).map((a) => `${a.name}: ${a.error}`));
    } catch (err) { notes.push(err.message); }
  }
  pick.value = "";
  if (notes.length) alert(notes.join("\n"));
  status.textContent = `อ่านแล้ว ${added} รูป · ได้ ${lines} รายการเติมน้ำมัน`;
  await openCashFuel(cf_.month);
}

async function cfShowPage(id) {
  cf_.shown = id;
  const box = $("cfViewer");
  if (!box) return;
  if (!cf_.pageUrls[id]) {
    fill(box, el("p", { class: "muted small", text: "กำลังโหลดรูป…" }));
    try {
      const res = await fetch(`/api/cash-fuel/pages/${id}/image`, { headers: { "X-App-Token": state.token } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      cf_.pageUrls[id] = URL.createObjectURL(await res.blob());
    } catch (err) { fill(box, el("p", { class: "error small", text: `โหลดรูปไม่ได้: ${err.message}` })); return; }
  }
  const p = cf_.data.pages.find((x) => x.id === id) || {};
  fill(box, el("div", { class: "cf-vhead" }, el("b", { class: "small", text: p.name || "รูป" }), el("a", { href: cf_.pageUrls[id], target: "_blank", rel: "noopener", class: "small", text: "เปิดเต็มจอ" })),
    el("img", { src: cf_.pageUrls[id], alt: "หน้ารายการเบิก" }));
}

function cfPaintSummary() {
  const box = $("cfSummary");
  if (!box) return;
  const d = cf_.data;
  const byId = Object.fromEntries(d.vehicles.map((v) => [v.id, v]));
  const groups = new Map();
  d.lines.filter((l) => l.counted && (!cf_.batch || l.batch === cf_.batch)).forEach((l) => {
    const k = l.vehicle_id || "?";
    if (!groups.has(k)) groups.set(k, { v: byId[l.vehicle_id], fills: [], drivers: {} });
    const g = groups.get(k);
    g.fills.push(l);
    if (l.driver_name) g.drivers[l.driver_name] = (g.drivers[l.driver_name] || 0) + 1;
  });
  const rows = [...groups.values()].sort((a, b) => (Number((a.v || {}).truck_number) || 999) - (Number((b.v || {}).truck_number) || 999));
  const total = rows.reduce((a, g) => a + g.fills.reduce((x, l) => x + Number(l.amount || 0), 0), 0);
  const q = cf_.batch ? `&batch=${cf_.batch}` : "";
  fill(box,
    el("div", { class: "ss-custbar" }, el("b", { text: "รายการค่าน้ำมัน (ส่ง HQ)" }), el("span", { class: "grow" }),
      el("button", { class: "btn", type: "button", text: "PDF (พิมพ์)", onclick: () => ssSheetFile(`/api/cash-fuel/summary.pdf?month=${cf_.month}${q}`, "pdf") }),
      el("button", { class: "btn", type: "button", text: "Excel", onclick: () => ssSheetFile(`/api/cash-fuel/summary.xlsx?month=${cf_.month}${q}`, "xlsx") })),
    el("div", { class: "ss-tablewrap soft" }, el("table", { class: "ss-table" },
      el("thead", {}, el("tr", {}, ...["รถเบอร์", "ทะเบียนรถ", "พนักงานขับรถ", "วันที่ · จำนวน", "รวม"].map((h) => el("th", { text: h })))),
      el("tbody", {}, ...rows.map((g) => el("tr", {},
        el("td", {}, el("b", { text: (g.v || {}).truck_number || "" })),
        el("td", { text: g.v ? g.v.plate : "ไม่ทราบรถ" }),
        el("td", { text: Object.entries(g.drivers).sort((a, b) => b[1] - a[1]).map(([n]) => n).slice(0, 2).join(", ") }),
        el("td", { class: "small", text: g.fills.slice().sort((a, b) => a.day.localeCompare(b.day)).map((l) => `${cfDmy(l.day).slice(0, 5)} ${elN(l.amount)}`).join(" · ") }),
        el("td", { class: "r" }, el("b", { text: elN(g.fills.reduce((x, l) => x + Number(l.amount || 0), 0)) })))),
        el("tr", { class: "tot" }, el("td", { colspan: "4", class: "r", text: "รวม" }), el("td", { class: "r" }, el("b", { text: elN(total) })))))));
}

async function cfAddLine() {
  const d = cf_.data;
  const day = prompt("วันที่ (วว/ดด/ปปปป)", cfDmy(new Date().toISOString().slice(0, 10)));
  if (!day) return;
  const iso = typeof parseDMY === "function" ? parseDMY(day) : null;
  if (!iso) { alert("วันที่ไม่ถูกต้อง"); return; }
  const truck = prompt("รถเบอร์ (หรือทะเบียน)", "");
  if (truck == null) return;
  const v = d.vehicles.find((x) => String(x.truck_number) === truck.trim() && x.branch === "สระบุรี") || d.vehicles.find((x) => (x.plate || "").replace(/[\s-]/g, "") === truck.replace(/[\s-]/g, ""));
  const amount = prompt(`จำนวนเงิน${v ? ` (${cfVehicleLabel(v)})` : " (ไม่พบรถนี้ — เลือกรถทีหลังได้)"}`, "1000");
  if (!amount) return;
  try { await api("/api/cash-fuel/lines", jsonOpts("POST", { day: iso, vehicle_id: v ? v.id : null, amount })); } catch (err) { alert(err.message); return; }
  openCashFuel(cf_.month);
}
