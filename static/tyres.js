"use strict";

// ยาง on the truck page (below the truck's details, above the plan and history): a chassis drawing seen from above, front at the top.
// Each wheel shows its tyre's age (green under 1 year, yellow 1-2 years, red older, grey dashed = nothing recorded); tap a wheel for the
// tyre on it (brand, size, since when, km used, price, shop) and the tyres it had before. "+ บันทึกเปลี่ยนยาง" puts new tyres on chosen
// wheels (it can start from a tyre bill already saved on the truck), "สลับยาง" moves tyres between wheels, "ขนาดยาง" edits the size /
// wheel count. Data: /api/vehicles/<id>/tyres (app/tyres.py). Loaded after plan.js, before app.js.

const ty = { v: null, d: null, sel: null };
const TY_STATE = { ok: "ใหม่กว่า 1 ปี", warn: "1–2 ปี", old: "เกิน 2 ปี", unknown: "ไม่ทราบวันที่ใส่", none: "ยังไม่มีข้อมูล" };

async function renderTruckTyres(v, editing) {
  const box = $("tkTyres");
  if (!v || editing || !v.vehicle_id) { box.hidden = true; fill(box); return; }
  box.hidden = false;
  if (!ty.v || ty.v.vehicle_id !== v.vehicle_id) ty.sel = null;
  ty.v = v;
  fill(box, el("p", { class: "muted small", text: "กำลังโหลดข้อมูลยาง…" }));
  try {
    ty.d = await api(`/api/vehicles/${v.vehicle_id}/tyres`);
  } catch (err) {
    fill(box, el("p", { class: "error", text: `โหลดข้อมูลยางไม่สำเร็จ: ${err.message}` }));
    return;
  }
  if (!tk.v || tk.v.vehicle_id !== v.vehicle_id || tk.editing) return;      // the page moved on while this was loading
  drawTyres();
}

const tyAge = (m) => (m == null ? "?" : m < 12 ? `${m} ด.` : `${Math.floor(m / 12)} ปี${m % 12 ? ` ${m % 12} ด.` : ""}`);
const tySpecText = (s) => {
  const size = s.front_size && s.rear_size && s.front_size === s.rear_size ? s.front_size : [s.front_size && `หน้า ${s.front_size}`, s.rear_size && `หลัง ${s.rear_size}`].filter(Boolean).join(" · ");
  return [size || "ยังไม่ได้ใส่ขนาดยาง", `${s.wheels} ล้อ`, s.radial ? "เรเดียล" : null, s.tubeless ? "ไม่มียางใน" : s.tubeless === false ? "มียางใน" : null].filter(Boolean).join(" · ");
};

function drawTyres() {
  const d = ty.d, v = ty.v;
  const btn = (text, onclick, cls = "") => el("button", { class: `btn small${cls ? ` ${cls}` : ""}`, type: "button", text, onclick });
  if (d.ready === false) {
    fill($("tkTyres"), el("section", { class: "card tycard" }, el("h3", { text: "ยาง" }), el("p", { class: "muted small", text: "ยังใช้ส่วนยางไม่ได้ — ต้องรันไฟล์ schema_v36.sql ใน Supabase ก่อน" })));
    return;
  }
  const hasTyres = d.positions.some((w) => w.tyre);
  fill($("tkTyres"), el("section", { class: "card tycard" },
    el("div", { class: "ty-head" },
      el("h3", { text: "ยาง" }),
      el("button", { class: "linkbtn small", type: "button", title: "แก้ขนาดยาง / จำนวนล้อ", text: tySpecText(d.spec), onclick: openTyreSpec }),
      el("span", { class: "grow" }),
      hasTyres ? btn("สลับยาง", openTyreRotate) : null,
      btn("+ บันทึกเปลี่ยนยาง", () => openTyreChange({}), "primary")),
    el("div", { class: "ty-body" }, tyreChassis(d), tyrePanel(d, v)),
    el("div", { class: "ty-legend small muted" },
      ...["ok", "warn", "old", "none"].map((s) => el("span", {}, el("i", { class: `ty-key ${s}` }), ` ${TY_STATE[s]}`)), " · กดที่ล้อเพื่อดูรายละเอียด")));
}

// the chassis seen from above: front at the top; 6 wheels = dual rear, the front wheels in line with the OUTER rear ones
function tyreChassis(d) {
  const NS = "http://www.w3.org/2000/svg";
  const sv = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); for (const [k, val] of Object.entries(attrs)) n.setAttribute(k, val); if (text != null) n.textContent = text; return n; };
  const six = d.wheels === 6;
  const W = 26, H = 54, L = 70, R = 264, IN = 30;              // wheel size, x of the left / right outer wheels, inner wheel offset
  const FY = 52, RY = 214;                                      // y of the front / rear axle
  const x = { FL: L, FR: R, RL: L, RR: R, RLO: L, RRO: R, RLI: L + IN, RRI: R - IN };
  const svg = sv("svg", { viewBox: "0 0 360 300", class: "ty-svg", role: "img", "aria-label": "ผังล้อรถ มองจากด้านบน หน้ารถอยู่ด้านบน" });
  svg.append(sv("text", { x: 180, y: 14, class: "ty-front" }, "หน้ารถ"));
  svg.append(sv("line", { x1: 180, y1: FY, x2: 180, y2: RY + 10, class: "ty-frame" }));
  svg.append(sv("line", { x1: L + W, y1: FY + H / 2, x2: R, y2: FY + H / 2, class: "ty-axle" }));
  svg.append(sv("line", { x1: (six ? L + IN : L) + W, y1: RY + H / 2, x2: six ? R - IN : R, y2: RY + H / 2, class: "ty-axle" }));
  svg.append(sv("rect", { x: 160, y: RY + 10, width: 40, height: 34, rx: 6, class: "ty-diff" }));
  for (const w of d.positions) {
    const px = x[w.position], py = w.axle === "front" ? FY : RY;
    const g = sv("g", { class: `ty-wheel ${w.state}${ty.sel === w.position ? " on" : ""}`, tabindex: "0", role: "button", "aria-label": `${w.label}: ${w.tyre ? `${w.tyre.brand || ""} ${w.tyre.size || ""} ใส่มา ${tyAge(w.months)}` : "ยังไม่มีข้อมูล"}` });
    g.append(sv("rect", { x: px, y: py, width: W, height: H, rx: 6 }));
    const left = px < 180;
    const tx = six && (w.position === "RLI" || w.position === "RRI") ? px + W / 2 : left ? px - 8 : px + W + 8;
    const ty_ = six && (w.position === "RLI" || w.position === "RRI") ? py + H + 16 : py + H / 2 + 4;
    g.append(sv("text", { x: tx, y: ty_, class: "ty-age", "text-anchor": six && (w.position === "RLI" || w.position === "RRI") ? "middle" : left ? "end" : "start" }, w.tyre ? tyAge(w.months) : "–"));
    const pick = () => { ty.sel = ty.sel === w.position ? null : w.position; drawTyres(); };
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    svg.append(g);
  }
  return el("div", { class: "ty-draw" }, svg);
}

function tyrePanel(d, v) {
  const w = d.positions.find((x) => x.position === ty.sel);
  const kv = (k, val) => (val ? el("div", { class: "ty-kv" }, el("span", { class: "muted", text: k }), el("span", { text: val })) : null);
  if (!w) {
    const bills = d.bills || [];
    return el("div", { class: "ty-panel" },
      el("div", { class: "muted small", text: "กดที่ล้อเพื่อดูยางเส้นนั้น" }),
      bills.length ? el("div", { class: "ty-bills" },
        el("strong", { class: "small", text: "บิลยางที่ยังไม่ได้ใส่ตำแหน่ง" }),
        ...bills.map((b) => el("div", { class: "ty-bill small" },
          el("span", { text: `${beDate(b.date)} · ${b.description}${b.qty ? ` × ${b.qty}` : ""}${b.mileage ? ` · ไมล์ ${baht(b.mileage)}` : ""}` }),
          el("button", { class: "btn small", type: "button", text: "ใส่ตำแหน่ง", onclick: () => openTyreChange({ bill: b }) })))) : null,
      d.unknown.length ? el("div", { class: "ty-bills" },
        el("strong", { class: "small", text: `ยางที่ไม่ทราบว่าอยู่ล้อไหน (${d.unknown.length} เส้น)` }),
        ...d.unknown.map((t) => el("div", { class: "ty-bill small" },
          el("span", { text: [t.fitted_on ? beDate(t.fitted_on) : null, t.brand, t.size].filter(Boolean).join(" · ") || "ไม่มีรายละเอียด" }),
          tyrePlaceSelect(t, d)))) : null);
  }
  const t = w.tyre;
  return el("div", { class: "ty-panel" },
    el("div", { class: "ty-panel-head" }, el("strong", { text: w.label }), el("span", { class: `badge ty-badge ${w.state}`, text: TY_STATE[w.state] })),
    t ? el("div", { class: "ty-kvs small" },
      kv("ยี่ห้อ / ขนาด", [t.brand, t.size].filter(Boolean).join(" · ") || "–"),
      kv("ใส่เมื่อ", t.fitted_on ? `${beDate(t.fitted_on)} · ${tyAge(w.months)}` : "ไม่ทราบ"),
      kv("ไมล์ตอนใส่", t.fitted_km != null ? `${baht(t.fitted_km)} กม.` : ""),
      kv("ใช้ไปแล้ว", w.km_used != null ? `~${baht(w.km_used)} กม.` : ""),
      kv("ราคา / ร้าน", [t.price != null ? `${baht(t.price)} ฿/เส้น` : null, t.vendor].filter(Boolean).join(" · ")),
      kv("หมายเหตุ", t.notes))
      : el("p", { class: "muted small", text: "ยังไม่มีข้อมูลยางล้อนี้" }),
    el("div", { class: "ty-actions" },
      el("button", { class: "btn small primary", type: "button", text: "เปลี่ยนยางล้อนี้", onclick: () => openTyreChange({ positions: [w.position] }) }),
      t ? el("button", { class: "btn small", type: "button", text: "แก้ไข", onclick: () => openTyreEdit(t) }) : null),
    w.history.length ? el("div", { class: "ty-hist small" }, el("strong", { text: "ยางเส้นก่อน ๆ" }),
      ...w.history.map((h) => el("div", { class: "muted", text: [`${h.fitted_on ? beDate(h.fitted_on) : "?"} → ${h.removed_on ? beDate(h.removed_on) : "?"}`, h.brand, h.size,
        h.fitted_km != null && h.removed_km != null ? `${baht(h.removed_km - h.fitted_km)} กม.` : null].filter(Boolean).join(" · ") }))) : null);
}

// a tyre whose wheel isn't known: pick the wheel straight from the list
function tyrePlaceSelect(t, d) {
  const free = d.positions.filter((w) => !w.tyre);
  const sel = el("select", { "aria-label": "เลือกล้อ" }, el("option", { value: "", text: "ใส่ที่ล้อ…" }), ...free.map((w) => el("option", { value: w.position, text: w.label })));
  sel.addEventListener("change", () => sel.value && tyreCall(`/api/tyres/${t.id}`, jsonOpts("PATCH", { position: sel.value })));
  return free.length ? sel : el("span", { class: "muted", text: "ทุกล้อมียางแล้ว" });
}

async function tyreCall(path, opts, errBox) {
  try {
    ty.d = await api(path, opts);
  } catch (err) {
    if (errBox) { errBox.hidden = false; errBox.textContent = err.message; } else alert(err.message);
    return false;
  }
  drawTyres();
  return true;
}

// ---- dialogs (built on demand; one <dialog> reused)
function tyreDialog(title, body, onSave, saveText = "บันทึก") {
  let dlg = $("tyDialog");
  if (!dlg) { dlg = el("dialog", { id: "tyDialog", class: "wide" }); document.body.append(dlg); }
  const err = el("p", { class: "error", hidden: true });
  const save = el("button", { class: "btn primary", type: "button", text: saveText });
  save.addEventListener("click", async () => { save.disabled = true; err.hidden = true; const ok = await onSave(err); save.disabled = false; if (ok) dlg.close(); });
  fill(dlg, el("h3", { text: title }), body, err,
    el("div", { class: "dialog-actions" }, el("span", { class: "grow" }), el("button", { class: "btn", type: "button", text: "ยกเลิก", onclick: () => dlg.close() }), save));
  dlg.showModal();
}

const tyField = (label, input, extra) => el("label", { class: "ty-field" }, el("span", { class: "muted small", text: label }), input, extra || null);
const tyInput = (value, attrs = {}) => { const i = el("input", { type: "text", autocomplete: "off", ...attrs }); i.value = value ?? ""; return i; };

function openTyreChange({ positions = [], bill = null }) {
  const d = ty.d, s = d.spec;
  const picks = d.positions.map((w) => {
    const box = el("input", { type: "checkbox", value: w.position });
    box.checked = positions.includes(w.position);
    return el("label", { class: "check ty-pick" }, box, ` ${w.label}${w.tyre ? "" : " (ว่าง)"}`);
  });
  const date = tyInput(bill ? bill.date : todayISO(), { type: "date" });
  const cap = el("div", { class: "datecap" });
  bindDateField(date, cap);
  const front = positions.length && positions.every((p) => p === "FL" || p === "FR");
  const size = tyInput(bill && bill.size ? bill.size : front ? s.front_size : s.rear_size || s.front_size, { placeholder: "เช่น 225/75R14" });
  const brand = tyInput(bill ? bill.brand : "", { placeholder: "เช่น Michelin XCD2" });
  const km = tyInput(bill ? bill.mileage : "", { inputmode: "numeric", placeholder: "เลขไมล์ตอนเปลี่ยน" });
  const price = tyInput(bill && bill.unit_cost ? Math.round(bill.unit_cost) : "", { inputmode: "decimal", placeholder: "ราคาต่อเส้น" });
  const vendor = tyInput(bill ? bill.vendor : "", { placeholder: "ร้าน" });
  const unknown = tyInput("", { inputmode: "numeric", placeholder: "0" });
  const notes = tyInput("", { maxlength: "300" });
  const billNote = bill ? el("p", { class: "muted small", text: `จากบิล ${beDate(bill.date)} · ${bill.description}${bill.qty ? ` × ${bill.qty} เส้น — เลือกล้อให้ครบ ${bill.qty} ล้อ` : ""}` }) : null;
  tyreDialog("บันทึกเปลี่ยนยาง", el("div", {},
    billNote,
    el("div", { class: "muted small", text: "ล้อที่เปลี่ยน" }), el("div", { class: "ty-picks" }, ...picks),
    el("div", { class: "ty-fields" },
      tyField("วันที่เปลี่ยน", date, cap), tyField("เลขไมล์", km), tyField("ขนาด", size), tyField("ยี่ห้อ / รุ่น", brand),
      tyField("ราคาต่อเส้น (฿)", price), tyField("ร้าน", vendor),
      tyField("ไม่ทราบว่าล้อไหน (เส้น)", unknown), tyField("หมายเหตุ", notes))),
    (err) => tyreCall(`/api/vehicles/${ty.v.vehicle_id}/tyres`, jsonOpts("POST", {
      positions: picks.map((p) => p.querySelector("input")).filter((b) => b.checked).map((b) => b.value), unknown_count: unknown.value,
      fitted_on: date.value, fitted_km: km.value, size: size.value, brand: brand.value, price: price.value, vendor: vendor.value, notes: notes.value,
      event_id: bill ? bill.event_id : null,
    }), err));
}

function openTyreRotate() {
  const d = ty.d;
  const rows = d.positions.filter((w) => w.tyre).map((w) => {
    const sel = el("select", { "aria-label": `${w.label} ไปล้อ` }, ...d.positions.map((x) => el("option", { value: x.position, text: x.position === w.position ? `${x.label} (อยู่ที่เดิม)` : x.label })));
    sel.value = w.position;
    return { w, sel, row: el("div", { class: "ty-rot" }, el("span", { text: `${w.label} · ${[w.tyre.brand, tyAge(w.months)].filter(Boolean).join(" · ")}` }), el("span", { class: "muted", text: "→" }), sel) };
  });
  tyreDialog("สลับยาง", el("div", {}, el("p", { class: "muted small", text: "เลือกว่ายางแต่ละเส้นย้ายไปล้อไหน — อายุและไมล์ของยางติดไปกับเส้นนั้น" }), ...rows.map((r) => r.row)),
    (err) => tyreCall(`/api/vehicles/${ty.v.vehicle_id}/tyres/rotate`, jsonOpts("POST", { moves: Object.fromEntries(rows.map((r) => [r.w.position, r.sel.value])) }), err));
}

function openTyreSpec() {
  const s = ty.d.spec;
  const front = tyInput(s.front_size, { placeholder: "เช่น 205/70R15" }), rear = tyInput(s.rear_size, { placeholder: "เช่น 225/75R14" });
  const wheels = el("select", {}, el("option", { value: "4", text: "4 ล้อ" }), el("option", { value: "6", text: "6 ล้อ (หลังคู่)" }));
  wheels.value = String(s.wheels);
  const radial = el("input", { type: "checkbox", checked: !!s.radial }), tubeless = el("input", { type: "checkbox", checked: !!s.tubeless });
  const notes = tyInput(s.notes, { maxlength: "300" });
  tyreDialog("ขนาดยาง", el("div", { class: "ty-fields" },
    tyField("ขนาดยางหน้า", front), tyField("ขนาดยางหลัง", rear), tyField("จำนวนล้อ", wheels), tyField("หมายเหตุ", notes),
    el("label", { class: "check" }, radial, " เรเดียล"), el("label", { class: "check" }, tubeless, " ไม่มียางใน (Tubeless)")),
    (err) => tyreCall(`/api/vehicles/${ty.v.vehicle_id}/tyre-spec`, jsonOpts("PUT", { front_size: front.value, rear_size: rear.value, wheels: wheels.value,
      radial: radial.checked, tubeless: tubeless.checked, notes: notes.value }), err));
}

function openTyreEdit(t) {
  const date = tyInput(t.fitted_on, { type: "date" }), cap = el("div", { class: "datecap" });
  bindDateField(date, cap);
  const km = tyInput(t.fitted_km, { inputmode: "numeric" }), size = tyInput(t.size), brand = tyInput(t.brand), price = tyInput(t.price, { inputmode: "decimal" });
  const vendor = tyInput(t.vendor), notes = tyInput(t.notes, { maxlength: "300" });
  const del = el("button", { class: "btn small danger", type: "button", text: "ลบยางเส้นนี้", onclick: async () => {
    if (!confirm("ลบข้อมูลยางเส้นนี้ใช่ไหม? (ใช้เมื่อบันทึกผิด — ถ้าเปลี่ยนยางให้ใช้ “เปลี่ยนยางล้อนี้”)")) return;
    if (await tyreCall(`/api/tyres/${t.id}`, { method: "DELETE" })) { $("tyDialog").close(); ty.sel = null; drawTyres(); }
  } });
  tyreDialog("แก้ไขยาง", el("div", { class: "ty-fields" },
    tyField("วันที่ใส่", date, cap), tyField("เลขไมล์ตอนใส่", km), tyField("ขนาด", size), tyField("ยี่ห้อ / รุ่น", brand),
    tyField("ราคาต่อเส้น (฿)", price), tyField("ร้าน", vendor), tyField("หมายเหตุ", notes), el("div", {}, del)),
    (err) => tyreCall(`/api/tyres/${t.id}`, jsonOpts("PATCH", { fitted_on: date.value, fitted_km: km.value, size: size.value, brand: brand.value,
      price: price.value, vendor: vendor.value, notes: notes.value }), err));
}
