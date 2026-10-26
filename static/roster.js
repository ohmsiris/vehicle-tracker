"use strict";

// ทะเบียนรถ: the fleet in one place.
//   * the list (#roster): every truck as one clickable card, plus a drivers tab. Nothing is editable
//     here, so nothing can be changed by accident.
//   * the truck page (#truck=<id>, or #truck=new): everything about one truck, laid out in the open.
//     "แก้ไข" turns the same page into a form; nothing is saved until "บันทึก".
// A truck's repair history is the maintenance log with that truck chosen (logbook.js).
//
// Data: /api/vehicles?include_inactive=true (fleet.vehicles), /api/drivers, writes via
// PATCH/POST /api/vehicles and /api/drivers (app/fleet.py -- all the rules are enforced there).
// Loaded BEFORE app.js: nothing here runs at load time.

const ro = { tab: "trucks", drivers: [], editingDriver: null };
const tk = { id: null, v: null, editing: false, draft: {}, dirty: false };

const UNSET = "__none", DRIVER_NEW = "__new";

const goTruck = (id) => goHash(`#truck=${id}`);

// ------------------------------------------------------------------ loading
async function openRoster() {
  showView("roster");
  showErr("rosterError", "");
  await refreshRoster();
}

async function refreshRoster() {
  const [ok] = await Promise.all([ensureVehicles(true), loadDrivers()]);
  if (!ok) { showErr("rosterError", `โหลดรายชื่อรถไม่สำเร็จ: ${fleet.loadError}`); return; }
  $("rosterNeedsV6").hidden = state.fleetReady !== false;
  fillRosterFilters();
  renderRoster();
  renderDrivers();
}

async function loadDrivers() {
  try {
    ro.drivers = await api("/api/drivers?include_inactive=true");
  } catch (err) {
    ro.drivers = [];
    showErr("rosterError", `โหลดรายชื่อคนขับไม่สำเร็จ: ${err.message}`);
  }
}

const activeDrivers = () => ro.drivers.filter((d) => d.active);
const driverLabel = (d) => (d.nickname ? `${d.name} (${d.nickname})` : d.name);
const driverOf = (v) => ro.drivers.find((d) => d.driver_id === v.driver_id) || null;

// After a change: the pickers on other pages (bill, repair form) read state.vehicles, so keep them in step.
// A new or retired truck changes WHICH trucks they list, which shifts their positions -- so that is only
// reloaded when no bill is being reviewed (its truck is remembered by position).
async function syncPickers(structural) {
  const byId = new Map(fleet.vehicles.map((v) => [v.vehicle_id, v]));
  for (const v of state.vehicles) {
    const f = byId.get(v.id);
    if (f) Object.assign(v, { usage: f.usage, driver: f.driver, driver_id: f.driver_id });
  }
  if (structural && !state.draft) await loadMeta();
}

// ------------------------------------------------------------------ filters
function fillRosterFilters() {
  const branch = $("roBranch"), usage = $("roUsage"), cls = $("roClass");
  const keepB = branch.value, keepU = usage.value, keepC = cls.value;
  const classes = [...new Set(fleet.vehicles.map((v) => v.vehicle_class).filter(Boolean))];
  cls.replaceChildren(el("option", { value: "", text: "รถทุกขนาด" }), ...classes.map((c) => el("option", { value: c, text: c })));
  cls.value = keepC;
  const branches = [...new Set([...(state.branches || []), ...fleet.vehicles.map((v) => v.branch).filter(Boolean)])];
  branch.replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...branches.map((b) => el("option", { value: b, text: b })));
  usage.replaceChildren(el("option", { value: "", text: "ทุกประเภท" }), ...usageList().map((u) => el("option", { value: u, text: u })),
    el("option", { value: UNSET, text: "ยังไม่ระบุประเภท" }));
  branch.value = keepB;
  usage.value = keepU;
}

// the presets, anything already used on a truck, and nothing else
function usageList() {
  const seen = [...(state.usages || [])];
  for (const v of fleet.vehicles) if (v.usage && !seen.includes(v.usage)) seen.push(v.usage);
  return seen;
}

function visibleTrucks() {
  const tokens = $("roSearch").value.trim().split(/\s+/).map(vehNorm).filter(Boolean);
  const branch = $("roBranch").value, usage = $("roUsage").value, status = $("roStatus").value, cls = $("roClass").value;
  const busy = $("roBusy").checked, sort = SORTS[$("roSort").value] || SORTS.number;
  $("roClear").hidden = !(tokens.length || branch || usage || cls || busy || status !== "active" || $("roSort").value !== "number");
  return fleet.vehicles
    .filter((v) => (status === "all" ? true : status === "off" ? v.active === false : v.active !== false))
    .filter((v) => !branch || v.branch === branch)
    .filter((v) => !usage || (usage === UNSET ? !v.usage : v.usage === usage))
    .filter((v) => !cls || v.vehicle_class === cls)
    .filter((v) => !busy || v.open_requests || v.at_shop_requests || v.awaiting_bill_requests)
    .map((v) => [v, rosterScore(v, tokens)])
    .filter(([, s]) => s > 0)
    .sort((a, b) => (tokens.length ? b[1] - a[1] : 0) || sort(a[0], b[0]) || SORTS.number(a[0], b[0]))     // words typed: best match first; otherwise the chosen order
    .map(([v]) => v);
}

function clearRosterFilters() {
  $("roSearch").value = "";
  for (const id of ["roBranch", "roUsage", "roClass"]) $(id).value = "";
  $("roStatus").value = "active";
  $("roSort").value = "number";
  $("roBusy").checked = false;
  renderRoster();
}

// a truck matches when every word is somewhere in its number / plate / model / branch / driver / usage
function rosterScore(v, tokens) {
  if (!tokens.length) return 1;
  const hay = `${vehHay(v)}|${vehNorm(v.driver)}|${vehNorm(v.usage)}|${vehNorm(v.nickname)}`;
  if (!tokens.every((t) => hay.includes(t))) return 0;
  return Math.max(1, vehScore(v, tokens));
}

// ------------------------------------------------------------------ the list
function renderRoster() {
  const rows = visibleTrucks();
  const live = fleet.vehicles.filter((v) => v.active !== false);
  const by = {};
  for (const v of live) by[v.usage || ""] = (by[v.usage || ""] || 0) + 1;
  const parts = usageList().filter((u) => by[u]).map((u) => `${u} ${by[u]}`);
  if (by[""]) parts.push(`ยังไม่ระบุ ${by[""]}`);
  fill($("rosterCount"), `พบ ${rows.length} คัน จากที่ใช้งานอยู่ ${live.length} คัน`, parts.length ? ` · ${parts.join(" · ")}` : null);
  $("rosterList").replaceChildren(...(rows.length ? rows.map(truckRow)
    : [el("p", { class: "muted empty", text: "ไม่พบรถที่ตรงกับตัวกรอง" })]));
}

const workBadges = (v) => [
  v.open_requests ? el("span", { class: "badge warn", text: `รอซ่อม ${v.open_requests}` }) : null,
  v.at_shop_requests ? el("span", { class: "badge shop", text: `อยู่ที่อู่ ${v.at_shop_requests}` }) : null,
  v.awaiting_bill_requests ? el("span", { class: "badge warn", text: `รอบิล ${v.awaiting_bill_requests}` }) : null,
].filter(Boolean);

// the whole card is the link (a real link, so it also opens in a new tab)
function truckRow(v) {
  const off = v.active === false;
  return el("a", { class: `rrow rlink${off ? " off" : ""}`, href: `#truck=${v.vehicle_id}` },
    el("div", { class: "rr-id" },
      el("div", { class: "req-head" }, ...truckHead(v), v.branch ? el("span", { class: "muted small", text: v.branch }) : null,
        off ? el("span", { class: "badge", text: "เลิกใช้แล้ว" }) : null),
      el("div", { class: "muted small", text: [`${v.make} ${v.model}`.trim(), v.vehicle_class, v.province].filter(Boolean).join(" · ") })),
    el("div", { class: "rr-cell" },
      el("span", { class: "rr-label", text: "ประเภท" }),
      v.usage ? el("span", { class: "badge shop", text: v.usage }) : el("span", { class: "muted small", text: "ยังไม่ระบุ" })),
    el("div", { class: "rr-cell" },
      el("span", { class: "rr-label", text: "คนขับหลัก" }),
      v.driver ? el("span", { class: "rr-driver", text: v.driver_nickname ? `${v.driver} (${v.driver_nickname})` : v.driver }) : el("span", { class: "muted small", text: "ยังไม่มี" }),
      v.driver_phone ? el("span", { class: "muted small", text: v.driver_phone }) : null),
    el("div", { class: "rr-badges" }, ...workBadges(v)),
    el("span", { class: "evchev", text: "›", "aria-hidden": "true" }));
}

// ------------------------------------------------------------ the truck page
const PAPER_KEYS = ["registered_date", "registration_type", "engine_number", "chassis_number", "owner_name", "holder_name"];

// the page's form values, keyed by the API field names
const draftFrom = (v) => (v ? {
  plate_number: v.plate, plate_province: v.province, vehicle_number: v.truck_number, branch: v.branch, vehicle_class: v.vehicle_class, usage: v.usage,
  make: v.make, model: v.model, color: v.color, nickname: v.nickname, notes: v.notes, main_driver_id: v.driver_id, active: v.active !== false,
  registered_date: v.registered_date, registration_type: v.registration_type, engine_number: v.engine_number, chassis_number: v.chassis_number,
  owner_name: v.owner_name, holder_name: v.holder_name,
} : {
  plate_number: "", plate_province: "", vehicle_number: "", branch: tk.lastBranch || "", vehicle_class: "", usage: "", make: "", model: "", color: "", nickname: "", notes: "",
  main_driver_id: "", active: true, registered_date: "", registration_type: "", engine_number: "", chassis_number: "", owner_name: "", holder_name: "",
});

async function openTruck(id) {
  showView("truck");
  showErr("tkError", "");
  tk.id = id;
  tk.dirty = false;
  if (id === "new") {
    await Promise.all([ensureVehicles(), loadDrivers()]);
    tk.v = null;
    tk.editing = true;
    tk.draft = draftFrom(null);
    renderTruck();
    return;
  }
  fill($("tkHead"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  fill($("tkBody"));
  const [ok] = await Promise.all([ensureVehicles(true), loadDrivers()]);
  if (tk.id !== id) return;                                   // the user already moved on
  if (!ok) { showErr("tkError", `โหลดข้อมูลรถไม่สำเร็จ: ${fleet.loadError}`); fill($("tkHead")); return; }
  tk.v = fleet.vehicles.find((x) => x.vehicle_id === id) || null;
  if (!tk.v) { showErr("tkError", "ไม่พบรถคันนี้"); fill($("tkHead")); return; }
  tk.editing = false;
  tk.draft = draftFrom(tk.v);
  renderTruck();
}

function renderTruck() {
  const v = tk.v;
  // ---- header
  if (!v) {
    fill($("tkHead"), el("h2", { class: "tk-title", text: "เพิ่มรถใหม่" }), el("p", { class: "muted small", text: "กรอกข้อมูลแล้วกด “บันทึก” — ข้อมูลทะเบียนและคนขับเพิ่มทีหลังได้" }));
  } else {
    fill($("tkHead"),
      el("div", { class: "req-head tk-title" }, ...truckHead(v), v.branch ? el("span", { class: "muted", text: v.branch }) : null),
      el("div", { class: "muted", text: [`${v.make} ${v.model}`.trim(), v.color, v.province].filter(Boolean).join(" · ") }),
      el("div", { class: "rr-badges" },
        v.usage ? el("span", { class: "badge shop", text: v.usage }) : null,
        v.vehicle_class ? el("span", { class: "badge", text: v.vehicle_class }) : null,
        v.active === false ? el("span", { class: "badge", text: "เลิกใช้แล้ว" }) : null,
        ...workBadges(v)));
  }
  // ---- action buttons
  const btn = (text, onclick, cls = "") => el("button", { class: `btn${cls ? ` ${cls}` : ""}`, type: "button", text, onclick });
  fill($("tkActions"), ...(tk.editing
    ? [btn("ยกเลิก", cancelTruckEdit), btn("บันทึก", saveTruck, "primary")]
    : [btn("ประวัติซ่อม", () => showTruckLog(v.vehicle_id)), btn("+ บันทึกบิล", () => billForVehicle(v)), btn("+ แจ้งซ่อม", () => requestForVehicle(v)), btn("แก้ไข", startTruckEdit, "primary")]));
  // ---- body
  fill($("tkBody"), ...(tk.editing ? editCards() : viewCards(v)));
  renderTruckPlan(v, tk.editing);
}

// ---- reading
const kvAll = (k, val, node) => el("div", { class: "kv" }, el("span", { class: "kv-k", text: k }),
  node || (val ? el("span", { class: "kv-v", text: val }) : el("span", { class: "kv-v muted", text: "–" })));
const tkCard = (title, ...rows) => el("section", { class: "card tkcard" }, el("h3", { text: title }), ...rows.filter(Boolean));

function viewCards(v) {
  const d = driverOf(v);
  const hasPaper = PAPER_KEYS.some((k) => v[k]);
  return [
    tkCard("ข้อมูลรถ",
      kvAll("ทะเบียน", v.plate), kvAll("จังหวัด", v.province), kvAll("เบอร์รถ", v.truck_number), kvAll("สาขา", v.branch),
      kvAll("ชื่อเรียกรถ", v.nickname), kvAll("ประเภทรถ", v.vehicle_class), kvAll("การใช้งาน", v.usage), kvAll("ยี่ห้อ", v.make), kvAll("รุ่น", v.model), kvAll("สี", v.color),
      kvAll("สถานะ", v.active === false ? "เลิกใช้แล้ว" : "ใช้งานอยู่")),
    tkCard("คนขับหลัก",
      kvAll("ชื่อ", d && d.name), kvAll("ชื่อเล่น", d && d.nickname),
      kvAll("เบอร์โทร", "", d && d.phone ? el("span", { class: "kv-v" }, el("span", { text: `${d.phone} ` }), callLink(d.phone, "", true)) : null),
      kvAll("สาขา", d && d.branch), kvAll("หมายเหตุ", d && d.notes),
      d ? null : el("p", { class: "muted small", text: "ยังไม่มีคนขับหลัก — กด “แก้ไข” เพื่อเลือก" })),
    tkCard("ข้อมูลทะเบียน",
      kvAll("วันจดทะเบียน", dateCaption(v.registered_date) || v.registered_date), kvAll("ประเภทตามทะเบียน", v.registration_type),
      kvAll("เลขเครื่อง", v.engine_number), kvAll("เลขตัวรถ", v.chassis_number), kvAll("ชื่อกรรมสิทธิ์", v.owner_name), kvAll("ชื่อผู้ครอบครอง", v.holder_name),
      state.fleetV7 ? (hasPaper ? null : el("p", { class: "muted small", text: "ยังไม่มีข้อมูลทะเบียน — กด “แก้ไข” เพื่อเพิ่ม" }))
        : el("p", { class: "muted small", text: "ต้องรัน schema_v7.sql ก่อนจึงจะเก็บข้อมูลทะเบียนได้" })),
    tkCard("งานซ่อม",
      kvAll("ซ่อมทั้งหมด", v.repairs_total ? `${v.repairs_total} ครั้ง` : "ยังไม่มีประวัติการซ่อม"),
      kvAll("ค่าซ่อมรวม", v.repairs_total ? `${baht(v.spend_total)} ฿` : ""),
      kvAll("ซ่อมล่าสุด", v.last_service_date ? `${beDate(v.last_service_date)} · ${ago(v.days_since_service)}` : ""),
      kvAll("ไมล์ล่าสุดที่บันทึก", v.last_known_mileage ? `${baht(v.last_known_mileage)} กม.${v.next_service_mileage ? ` → ${baht(v.next_service_mileage)}` : ""}` : ""),
      el("div", { class: "tk-link" }, el("button", { class: "btn small", type: "button", text: "ดูประวัติซ่อมทั้งหมด", onclick: () => showTruckLog(v.vehicle_id) }))),
    v.notes ? tkCard("หมายเหตุ", el("p", { class: "tk-notes", text: v.notes })) : null,
  ].filter(Boolean);
}

// ---- editing (every input writes straight into tk.draft, so re-drawing the page never loses what was typed)
function field(label, key, { type = "text", list, placeholder, maxlength = 80, caption } = {}) {
  const input = el("input", { id: `tk_${key}`, type, maxlength: String(maxlength), autocomplete: "off", value: tk.draft[key] || "" });
  if (list) input.setAttribute("list", list);
  if (placeholder) input.placeholder = placeholder;
  const cap = caption ? el("div", { class: "datecap" }) : null;
  if (cap) bindDateField(input, cap);                       // first: it corrects a พ.ศ. year typed into the ค.ศ. field
  input.addEventListener("input", () => { tk.draft[key] = input.value; tk.dirty = true; });
  return el("div", { class: "tkfield" }, el("label", { for: `tk_${key}`, text: label }), input, cap);
}

function selectField(label, key, options, { blank = "— ไม่ระบุ —", onChange } = {}) {
  const sel = el("select", { id: `tk_${key}` }, el("option", { value: "", text: blank }), ...options.map(([value, text]) => el("option", { value, text })));
  const cur = tk.draft[key] || "";
  if (cur && !options.some(([value]) => value === cur)) sel.append(el("option", { value: cur, text: cur }));
  sel.value = cur;
  sel.addEventListener("change", () => { tk.draft[key] = sel.value; tk.dirty = true; if (onChange) onChange(sel); });
  return el("div", { class: "tkfield" }, el("label", { for: `tk_${key}`, text: label }), sel);
}

function editCards() {
  const v = tk.v;
  const drivers = activeDrivers();
  const options = drivers.map((d) => [d.driver_id, driverLabel(d)]);
  const current = v && v.driver_id && !drivers.some((d) => d.driver_id === v.driver_id) ? [[v.driver_id, `${v.driver} (เลิกขับ)`]] : [];
  const chosen = ro.drivers.find((d) => d.driver_id === tk.draft.main_driver_id) || null;
  return [
    tkCard("ข้อมูลรถ", el("div", { class: "tkfields" },
      field("ทะเบียน", "plate_number", { maxlength: 30, placeholder: "เช่น 1กข 4267" }),
      field("จังหวัดป้ายทะเบียน", "plate_province", { maxlength: 40, placeholder: "เช่น กรุงเทพมหานคร" }),
      field("เบอร์รถ (เบอร์ในบริษัท)", "vehicle_number", { maxlength: 40, placeholder: "เช่น 36" }),
      state.fleetV8 ? field("ชื่อเรียกรถ (ชื่อเล่นรถ)", "nickname", { maxlength: 40, placeholder: "เช่น หัวขาว" }) : null,
      selectField("สาขา", "branch", (state.branches || []).map((b) => [b, b]), { blank: "— เลือกสาขา —" }),
      state.fleetV7 ? selectField("ประเภทรถ (ขนาด)", "vehicle_class", (state.vehicleClasses || []).map((c) => [c, c])) : null,
      field("การใช้งาน", "usage", { list: "tkUsageList", maxlength: 40, placeholder: "เช่น รถสาย / รถหน้าโรง" }),
      field("ยี่ห้อ", "make", { maxlength: 40, placeholder: "เช่น ISUZU" }),
      field("รุ่น", "model", { maxlength: 40, placeholder: "เช่น D-Max 2.5" }),
      state.fleetV7 ? field("สี", "color", { placeholder: "เช่น ขาว-เขียว" }) : null,
      el("datalist", { id: "tkUsageList" }, ...usageList().map((u) => el("option", { value: u })))),
      v ? el("label", { class: "check tk-active" }, el("input", { type: "checkbox", checked: tk.draft.active, onchange: (e) => { tk.draft.active = e.target.checked; tk.dirty = true; } }),
        " ใช้งานอยู่ ", el("span", { class: "muted small", text: "(เอาติ๊กออก = เลิกใช้ เช่น ขายแล้ว รถจะไม่ขึ้นในตัวเลือก แต่ประวัติซ่อมยังอยู่)" })) : null),
    tkCard("คนขับหลัก",
      selectField("เลือกคนขับ", "main_driver_id", [...current, ...options], { blank: "— ยังไม่มี —" }),
      el("div", { class: "tk-link" },
        el("button", { class: "btn small", type: "button", text: "+ คนขับใหม่", onclick: newDriverForTruck }),
        chosen ? el("button", { class: "btn small", type: "button", text: "แก้ไขข้อมูลคนขับคนนี้", onclick: () => openDriverDialog(chosen) }) : null),
      chosen ? el("div", { class: "tk-driverinfo" }, kvAll("ชื่อเล่น", chosen.nickname), kvAll("เบอร์โทร", chosen.phone), kvAll("สาขา", chosen.branch)) : null,
      el("p", { class: "muted small", text: "ชื่อ เบอร์โทร ของคนขับเป็นข้อมูลของตัวคนขับ แก้ได้ที่ “แก้ไขข้อมูลคนขับคนนี้” หรือในแท็บคนขับ" })),
    state.fleetV7 ? tkCard("ข้อมูลทะเบียน", el("div", { class: "tkfields" },
      field("วันจดทะเบียน", "registered_date", { type: "date", caption: true }),
      field("ประเภทตามทะเบียน", "registration_type", { placeholder: "เช่น รถบรรทุกส่วนบุคคลตู้บรรทุก" }),
      field("เลขเครื่อง", "engine_number"), field("เลขตัวรถ", "chassis_number"),
      field("ชื่อกรรมสิทธิ์ (เจ้าของ)", "owner_name"), field("ชื่อผู้ครอบครอง", "holder_name"))) : null,
    tkCard("หมายเหตุ", el("div", { class: "tkfields one" }, field("หมายเหตุ (ไม่บังคับ)", "notes", { maxlength: 300 }))),
  ].filter(Boolean);
}

function startTruckEdit() {
  tk.editing = true;
  tk.draft = draftFrom(tk.v);
  tk.dirty = false;
  showErr("tkError", "");
  renderTruck();
}

function cancelTruckEdit() {
  if (tk.dirty && !confirm("ยังไม่ได้บันทึก ยกเลิกการแก้ไขใช่ไหม?")) return;
  if (!tk.v) { goHash("#roster"); return; }                 // a half-filled new truck: back to the list
  tk.editing = false;
  tk.dirty = false;
  showErr("tkError", "");
  renderTruck();
}

async function newDriverForTruck() {
  const name = (prompt("ชื่อคนขับใหม่ (ชื่อจริง)") || "").trim();
  if (!name) return;
  try {
    const made = await api("/api/drivers", jsonOpts("POST", { name, branch: tk.draft.branch || null }));
    await loadDrivers();
    tk.draft.main_driver_id = made.driver_id;
    tk.dirty = true;
    renderTruck();
  } catch (err) {
    showErr("tkError", err.message);
  }
}

async function saveTruck() {
  showErr("tkError", "");
  const v = tk.v, body = { ...tk.draft };
  if (!state.fleetV7) for (const k of ["vehicle_class", "color", ...PAPER_KEYS]) delete body[k];
  if (!state.fleetV8) delete body.nickname;
  if (!v) delete body.active;                                // a new truck is simply active
  try {
    const res = v ? await api(`/api/vehicles/${v.vehicle_id}`, jsonOpts("PATCH", body)) : await api("/api/vehicles", jsonOpts("POST", body));
    tk.lastBranch = body.branch;
    tk.dirty = false;
    const structural = !v || (v.active !== false) !== body.active;
    await Promise.all([ensureVehicles(true), loadDrivers()]);
    await syncPickers(structural);
    if (!v) { goTruck(res.vehicle_id); return; }              // the new truck's own page
    tk.v = fleet.vehicles.find((x) => x.vehicle_id === v.vehicle_id) || v;
    tk.editing = false;
    renderTruck();
    window.scrollTo(0, 0);
  } catch (err) {
    showErr("tkError", err.message);
    window.scrollTo(0, 0);
  }
}

// ------------------------------------------------------------ the master list as a spreadsheet
function exportRoster() {
  const cols = [["เบอร์รถ", (v) => v.truck_number], ["ชื่อเรียกรถ", (v) => v.nickname], ["ทะเบียน", (v) => v.plate], ["จังหวัด", (v) => v.province], ["สาขา", (v) => v.branch],
    ["ประเภทรถ", (v) => v.vehicle_class], ["การใช้งาน", (v) => v.usage], ["ยี่ห้อ", (v) => v.make], ["รุ่น", (v) => v.model], ["สี", (v) => v.color],
    ["คนขับหลัก", (v) => v.driver], ["ชื่อเล่น", (v) => v.driver_nickname], ["เบอร์โทร", (v) => v.driver_phone],
    ["วันจดทะเบียน (ค.ศ.)", (v) => v.registered_date], ["ประเภทตามทะเบียน", (v) => v.registration_type], ["เลขเครื่อง", (v) => v.engine_number],
    ["เลขตัวรถ", (v) => v.chassis_number], ["ชื่อกรรมสิทธิ์", (v) => v.owner_name], ["ชื่อผู้ครอบครอง", (v) => v.holder_name],
    ["สถานะ", (v) => (v.active === false ? "เลิกใช้" : "ใช้งานอยู่")], ["หมายเหตุ", (v) => v.notes]];
  const q = (x) => `"${String(x ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.map(([h]) => q(h)).join(","), ...visibleTrucks().map((v) => cols.map(([, f]) => q(f(v))).join(","))];
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: `ทะเบียนรถ-${todayISO()}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ------------------------------------------------------------------ drivers
function renderDrivers() {
  const showOff = $("drvShowOff").checked;
  const rows = ro.drivers.filter((d) => showOff || d.active);
  $("driverList").replaceChildren(...(rows.length ? rows.map(driverRow)
    : [el("p", { class: "muted empty", text: "ยังไม่มีคนขับ — กด “+ เพิ่มคนขับ” หรือเลือกคนขับในหน้าข้อมูลรถ" })]));
}

function driverRow(d) {
  return el("div", { class: `rrow drow${d.active ? "" : " off"}` },
    el("div", { class: "rr-id" },
      el("div", { class: "req-head" }, el("strong", { class: "truckplate", text: d.name }),
        d.nickname ? el("span", { class: "muted", text: `(${d.nickname})` }) : null,
        d.branch ? el("span", { class: "muted small", text: d.branch }) : null,
        d.active ? null : el("span", { class: "badge", text: "เลิกขับแล้ว" })),
      el("div", { class: "muted small", text: [d.phone, d.notes].filter(Boolean).join(" · ") })),
    el("div", { class: "rr-trucks" },
      ...(d.trucks.length ? d.trucks.map((t) => el("button", { class: "chipbtn", type: "button", title: "เปิดหน้าข้อมูลรถคันนี้", text: truckLabel({ truck_number: t.truck_number, plate: t.plate }),
        onclick: () => goTruck(t.vehicle_id) })) : [el("span", { class: "muted small", text: d.active ? "ยังไม่ได้เป็นคนขับหลักของคันไหน" : "" })])),
    el("div", { class: "rr-actions" },
      d.phone ? callLink(d.phone, "", true) : null,
      el("button", { class: "btn small", type: "button", text: "แก้ไข", onclick: () => openDriverDialog(d) }),
      el("button", { class: `btn small${d.active ? " danger" : ""}`, type: "button", text: d.active ? "เลิกขับ" : "กลับมาขับ", onclick: () => toggleDriver(d) })));
}

async function toggleDriver(d) {
  if (d.active && !confirm(`${d.name} เลิกขับแล้วใช่ไหม?${d.trucks.length ? `\nรถ ${d.trucks.length} คันที่เขาเป็นคนขับหลักจะไม่มีคนขับหลัก` : ""}`)) return;
  try {
    await api(`/api/drivers/${d.driver_id}`, jsonOpts("PATCH", { active: !d.active }));
  } catch (err) {
    showErr("rosterError", err.message);
    return;
  }
  await refreshRoster();
  await syncPickers(false);
}

function openDriverDialog(d) {
  ro.editingDriver = d || null;
  $("ddTitle").textContent = d ? `แก้ไขคนขับ · ${d.name}` : "เพิ่มคนขับ";
  $("ddName").value = d ? d.name : "";
  $("ddNickRow").hidden = !state.fleetV7;
  $("ddNick").value = d ? d.nickname : "";
  $("ddPhone").value = d ? d.phone : "";
  $("ddNotes").value = d ? d.notes : "";
  $("ddBranch").replaceChildren(el("option", { value: "", text: "— ไม่ระบุ —" }), ...(state.branches || []).map((b) => el("option", { value: b, text: b })));
  $("ddBranch").value = d ? d.branch : "";
  showErr("ddError", "");
  $("drvDialog").showModal();
}

async function saveDriver() {
  const body = { name: $("ddName").value, phone: $("ddPhone").value, branch: $("ddBranch").value, notes: $("ddNotes").value };
  if (state.fleetV7) body.nickname = $("ddNick").value;
  try {
    if (ro.editingDriver) await api(`/api/drivers/${ro.editingDriver.driver_id}`, jsonOpts("PATCH", body));
    else await api("/api/drivers", jsonOpts("POST", body));
  } catch (err) {
    showErr("ddError", err.message);
    return;
  }
  $("drvDialog").close();
  await loadDrivers();
  if (!$("rosterView").hidden) { await refreshRoster(); }
  await ensureVehicles(true);                                 // trucks carry their driver's name / phone
  await syncPickers(false);
  if (!$("truckView").hidden) {                               // the truck page redraws with the new driver details (typed values are kept in tk.draft)
    tk.v = tk.v ? fleet.vehicles.find((x) => x.vehicle_id === tk.v.vehicle_id) || tk.v : null;
    renderTruck();
  }
}

// ------------------------------------------------------------------ tabs + init
function showRosterTab(tab) {
  ro.tab = tab;
  $("rosterTrucks").hidden = tab !== "trucks";
  $("rosterDrivers").hidden = tab !== "drivers";
  $("roTabTrucks").classList.toggle("active", tab === "trucks");
  $("roTabDrivers").classList.toggle("active", tab === "drivers");
}

function initRoster() {
  $("roSearch").addEventListener("input", renderRoster);
  for (const id of ["roBranch", "roUsage", "roClass", "roStatus", "roSort", "roBusy"]) $(id).addEventListener("change", renderRoster);
  $("roClear").addEventListener("click", clearRosterFilters);
  $("roAdd").addEventListener("click", () => goTruck("new"));
  $("roTabTrucks").addEventListener("click", () => showRosterTab("trucks"));
  $("roTabDrivers").addEventListener("click", () => showRosterTab("drivers"));
  $("drvAdd").addEventListener("click", () => openDriverDialog(null));
  $("drvShowOff").addEventListener("change", renderDrivers);
  $("roExport").addEventListener("click", exportRoster);
  $("ddCancel").addEventListener("click", () => $("drvDialog").close());
  $("ddSave").addEventListener("click", saveDriver);
  $("tkBack").addEventListener("click", () => {
    if (tk.editing && tk.dirty && !confirm("ยังไม่ได้บันทึก ออกจากหน้านี้ใช่ไหม?")) return;
    tk.editing = false;
    goHash("#roster");
  });
}
