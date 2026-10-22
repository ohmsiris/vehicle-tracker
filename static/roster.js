"use strict";

// ทะเบียนรถ: the fleet in one place. Trucks (what each is used for, its main driver, details,
// retire / bring back) and drivers (name, phone, which trucks). A truck's repair history is the
// maintenance log with that truck chosen (logbook.js), one tap away from each row.
//
// Data: /api/vehicles?include_inactive=true (fleet.vehicles), /api/drivers, writes via
// PATCH/POST /api/vehicles and /api/drivers (app/fleet.py -- all the rules are enforced there).
// Loaded BEFORE app.js: nothing here runs at load time.

const ro = { tab: "trucks", drivers: [], editing: null, editingDriver: null };

const USAGE_NEW = "__new", DRIVER_NEW = "__new", UNSET = "__none";

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
const kv = (k, val) => (val ? el("div", { class: "kv" }, el("span", { class: "kv-k", text: k }), el("span", { class: "kv-v", text: val })) : null);

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
  const branch = $("roBranch"), usage = $("roUsage");
  const keepB = branch.value, keepU = usage.value;
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
  const branch = $("roBranch").value, usage = $("roUsage").value, status = $("roStatus").value;
  return fleet.vehicles
    .filter((v) => (status === "all" ? true : status === "off" ? v.active === false : v.active !== false))
    .filter((v) => !branch || v.branch === branch)
    .filter((v) => !usage || (usage === UNSET ? !v.usage : v.usage === usage))
    .map((v) => [v, rosterScore(v, tokens)])
    .filter(([, s]) => s > 0)
    .sort((a, b) => b[1] - a[1] || SORTS.number(a[0], b[0]))
    .map(([v]) => v);
}

// a truck matches when every word is somewhere in its number / plate / model / branch / driver / usage
function rosterScore(v, tokens) {
  if (!tokens.length) return 1;
  const hay = `${vehHay(v)}|${vehNorm(v.driver)}|${vehNorm(v.usage)}`;
  if (!tokens.every((t) => hay.includes(t))) return 0;
  return Math.max(1, vehScore(v, tokens));
}

// ------------------------------------------------------------------ trucks
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

function truckRow(v) {
  const off = v.active === false;
  const badges = [
    off ? el("span", { class: "badge", text: "เลิกใช้แล้ว" }) : null,
    v.open_requests ? el("span", { class: "badge warn", text: `รอซ่อม ${v.open_requests}` }) : null,
    v.at_shop_requests ? el("span", { class: "badge shop", text: `อยู่ที่อู่ ${v.at_shop_requests}` }) : null,
    v.awaiting_bill_requests ? el("span", { class: "badge warn", text: `รอบิล ${v.awaiting_bill_requests}` }) : null,
  ];
  return el("div", { class: `rrow${off ? " off" : ""}` },
    el("div", { class: "rr-id clickable", role: "button", tabindex: "0", title: "ดูข้อมูลรถทั้งหมด",
      onclick: () => openVehicleInfo(v), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openVehicleInfo(v); } } },
      el("div", { class: "req-head" }, ...truckHead(v), v.branch ? el("span", { class: "muted small", text: v.branch }) : null),
      el("div", { class: "muted small", text: [`${v.make} ${v.model}`.trim(), v.vehicle_class, v.province].filter(Boolean).join(" · ") }),
      el("div", { class: "rr-badges" }, ...badges.filter(Boolean))),
    el("label", { class: "rr-field" }, el("span", { text: "ประเภท" }), usageSelect(v)),
    el("label", { class: "rr-field" }, el("span", { text: "คนขับหลัก" }), driverSelect(v)),
    el("div", { class: "rr-actions" },
      el("button", { class: "btn small", type: "button", text: "ประวัติซ่อม", onclick: () => showTruckLog(v.vehicle_id) }),
      el("button", { class: "btn small", type: "button", text: "แก้ไข", onclick: () => openVehicleDialog(v) })));
}

function usageSelect(v) {
  const names = usageList();
  if (v.usage && !names.includes(v.usage)) names.push(v.usage);
  const sel = el("select", { "aria-label": "ประเภทการใช้งาน" },
    el("option", { value: "", text: "— ยังไม่ระบุ —" }),
    ...names.map((u) => el("option", { value: u, text: u })),
    el("option", { value: USAGE_NEW, text: "+ ประเภทอื่น…" }));
  sel.value = v.usage || "";
  sel.addEventListener("change", async () => {
    let value = sel.value;
    if (value === USAGE_NEW) {
      value = (prompt("ชื่อประเภทการใช้งาน (เช่น รถส่งก้อน)") || "").trim();
      if (!value) { sel.value = v.usage || ""; return; }
    }
    await patchTruck(v, { usage: value });
  });
  return sel;
}

function driverSelect(v) {
  const drivers = activeDrivers();
  const sel = el("select", { "aria-label": "คนขับหลัก" },
    el("option", { value: "", text: "— ยังไม่มี —" }),
    ...drivers.map((d) => el("option", { value: d.driver_id, text: driverLabel(d) })),
    el("option", { value: DRIVER_NEW, text: "+ คนขับใหม่…" }));
  if (v.driver_id && !drivers.some((d) => d.driver_id === v.driver_id)) {          // a driver who has since stopped: still shown
    sel.insertBefore(el("option", { value: v.driver_id, text: `${v.driver} (เลิกขับ)` }), sel.options[1]);
  }
  sel.value = v.driver_id || "";
  sel.addEventListener("change", async () => {
    let id = sel.value;
    if (id === DRIVER_NEW) {
      const name = (prompt("ชื่อคนขับใหม่") || "").trim();
      if (!name) { sel.value = v.driver_id || ""; return; }
      try {
        id = (await api("/api/drivers", jsonOpts("POST", { name, branch: v.branch }))).driver_id;
      } catch (err) {
        showErr("rosterError", err.message);
        sel.value = v.driver_id || "";
        return;
      }
    }
    await patchTruck(v, { main_driver_id: id });
  });
  return sel;
}

async function patchTruck(v, body, { structural = false } = {}) {
  showErr("rosterError", "");
  try {
    await api(`/api/vehicles/${v.vehicle_id}`, jsonOpts("PATCH", body));
  } catch (err) {
    showErr("rosterError", err.message);
    await refreshRoster();                                // put the drop-down back to what is really saved
    return false;
  }
  await refreshRoster();
  await syncPickers(structural);
  return true;
}

// ------------------------------------------------------------ truck dialog
function openVehicleDialog(v) {
  ro.editing = v || null;
  $("vdTitle").textContent = v ? `แก้ไขข้อมูลรถ · ${truckLabel({ truck_number: v.truck_number, plate: v.plate })}` : "เพิ่มรถใหม่";
  $("vdPlate").value = v ? v.plate : "";
  $("vdProvince").value = v ? v.province : "";
  $("vdNumber").value = v ? v.truck_number : "";
  $("vdMake").value = v ? v.make : "";
  $("vdModel").value = v ? v.model : "";
  $("vdUsage").value = v ? v.usage : "";
  $("vdNotes").value = v ? v.notes : "";
  $("vdActive").checked = v ? v.active !== false : true;
  $("vdMore").hidden = !state.fleetV7;                   // these columns arrive with schema_v7.sql
  $("vdClass").replaceChildren(el("option", { value: "", text: "— ไม่ระบุ —" }), ...(state.vehicleClasses || []).map((c) => el("option", { value: c, text: c })));
  $("vdClass").value = v ? v.vehicle_class : "";
  $("vdColor").value = v ? v.color : "";
  $("vdEngine").value = v ? v.engine_number : "";
  $("vdChassis").value = v ? v.chassis_number : "";
  $("vdRegDate").value = v ? v.registered_date : "";
  $("vdRegDateCap").textContent = dateCaption($("vdRegDate").value);
  $("vdRegType").value = v ? v.registration_type : "";
  $("vdOwner").value = v ? v.owner_name : "";
  $("vdHolder").value = v ? v.holder_name : "";
  $("vdActiveRow").hidden = !v;                          // a new truck is simply active
  $("vdBranch").replaceChildren(el("option", { value: "", text: "— เลือกสาขา —" }), ...(state.branches || []).map((b) => el("option", { value: b, text: b })));
  if (v && v.branch && ![...$("vdBranch").options].some((o) => o.value === v.branch)) $("vdBranch").append(el("option", { value: v.branch, text: v.branch }));
  $("vdBranch").value = v ? v.branch : (ro.lastBranch || "");
  $("vdUsageList").replaceChildren(...usageList().map((u) => el("option", { value: u })));
  const drivers = activeDrivers();
  $("vdDriver").replaceChildren(el("option", { value: "", text: "— ยังไม่มี —" }), ...drivers.map((d) => el("option", { value: d.driver_id, text: driverLabel(d) })));
  if (v && v.driver_id && !drivers.some((d) => d.driver_id === v.driver_id)) $("vdDriver").append(el("option", { value: v.driver_id, text: `${v.driver} (เลิกขับ)` }));
  $("vdDriver").value = v ? v.driver_id : "";
  showErr("vdError", "");
  $("vehDialog").showModal();
}

async function saveVehicle() {
  const body = {
    plate_number: $("vdPlate").value, plate_province: $("vdProvince").value, vehicle_number: $("vdNumber").value, branch: $("vdBranch").value,
    make: $("vdMake").value, model: $("vdModel").value, usage: $("vdUsage").value, main_driver_id: $("vdDriver").value, notes: $("vdNotes").value,
  };
  const v = ro.editing;
  if (v) body.active = $("vdActive").checked;
  if (state.fleetV7) {
    Object.assign(body, { vehicle_class: $("vdClass").value, color: $("vdColor").value, engine_number: $("vdEngine").value, chassis_number: $("vdChassis").value,
      registered_date: $("vdRegDate").value, registration_type: $("vdRegType").value, owner_name: $("vdOwner").value, holder_name: $("vdHolder").value });
  }
  const structural = !v || (v.active !== false) !== body.active;
  try {
    if (v) await api(`/api/vehicles/${v.vehicle_id}`, jsonOpts("PATCH", body));
    else await api("/api/vehicles", jsonOpts("POST", body));
  } catch (err) {
    showErr("vdError", err.message);
    return;
  }
  ro.lastBranch = body.branch;
  $("vehDialog").close();
  await refreshRoster();
  await syncPickers(structural);
  if (!$("logView").hidden) renderTruckHeader();
  reopenInfo();
}

// ------------------------------------------------- everything about one truck
function openVehicleInfo(v) {
  ro.info = v;
  const d = driverOf(v);
  fill($("viTitle"), ...truckHead(v), v.branch ? el("span", { class: "muted", text: v.branch }) : null);
  $("viSub").textContent = [`${v.make} ${v.model}`.trim(), v.color, v.province].filter(Boolean).join(" · ");
  fill($("viBadges"),
    v.usage ? el("span", { class: "badge shop", text: v.usage }) : null,
    v.vehicle_class ? el("span", { class: "badge", text: v.vehicle_class }) : null,
    v.active === false ? el("span", { class: "badge", text: "เลิกใช้แล้ว" }) : null,
    v.open_requests ? el("span", { class: "badge warn", text: `รอซ่อม ${v.open_requests}` }) : null,
    v.at_shop_requests ? el("span", { class: "badge shop", text: `อยู่ที่อู่ ${v.at_shop_requests}` }) : null,
    v.awaiting_bill_requests ? el("span", { class: "badge warn", text: `รอบิล ${v.awaiting_bill_requests}` }) : null);

  const section = (title, ...rows) => el("section", { class: "visection" }, el("h4", { text: title }), ...rows.filter(Boolean));
  const phone = d && d.phone ? el("div", { class: "kv" }, el("span", { class: "kv-k", text: "เบอร์โทร" }),
    el("a", { class: "kv-v link", href: `tel:${d.phone}`, text: d.phone })) : null;
  const hasPaper = ["registered_date", "registration_type", "engine_number", "chassis_number", "owner_name", "holder_name"].some((k) => v[k]);

  const driverRows = d ? [kv("ชื่อ", d.name), kv("ชื่อเล่น", d.nickname), phone, kv("สาขา", d.branch), kv("หมายเหตุ", d.notes),
    el("button", { class: "btn small", type: "button", text: "แก้ไขข้อมูลคนขับ", onclick: () => openDriverDialog(d) })]
    : [el("p", { class: "muted small", text: "ยังไม่มีคนขับหลัก — เลือกได้ที่หน้ารายการรถ หรือกดแก้ไข" })];
  const paperRows = hasPaper ? [
    kv("วันจดทะเบียน", dateCaption(v.registered_date) || v.registered_date), kv("ประเภทตามทะเบียน", v.registration_type), kv("เลขเครื่อง", v.engine_number),
    kv("เลขตัวรถ", v.chassis_number), kv("ชื่อกรรมสิทธิ์", v.owner_name), kv("ชื่อผู้ครอบครอง", v.holder_name)]
    : [el("p", { class: "muted small", text: state.fleetV7 ? "ยังไม่มีข้อมูลทะเบียน — กดแก้ไขเพื่อเพิ่ม" : "ต้องรัน schema_v7.sql ก่อนจึงจะเก็บข้อมูลทะเบียนได้" })];

  fill($("viBody"),
    section("ข้อมูลรถ",
      kv("ทะเบียน", [v.plate, v.province].filter(Boolean).join(" · ")), kv("เบอร์รถ", v.truck_number), kv("สาขา", v.branch),
      kv("ประเภทรถ", v.vehicle_class), kv("การใช้งาน", v.usage), kv("ยี่ห้อ / รุ่น", `${v.make} ${v.model}`.trim()), kv("สี", v.color), kv("หมายเหตุ", v.notes)),
    section("คนขับหลัก", ...driverRows),
    section("ข้อมูลทะเบียน", ...paperRows),
    section("งานซ่อม",
      kv("ซ่อมทั้งหมด", v.repairs_total ? `${v.repairs_total} ครั้ง · รวม ${baht(v.spend_total)} ฿` : "ยังไม่มีประวัติการซ่อม"),
      kv("ซ่อมล่าสุด", v.last_service_date ? `${beDate(v.last_service_date)} · ${ago(v.days_since_service)}` : ""),
      kv("ไมล์ล่าสุดที่บันทึก", v.last_known_mileage ? `${baht(v.last_known_mileage)} กม.${v.next_service_mileage ? ` → ${baht(v.next_service_mileage)}` : ""}` : "")));
  $("vehInfoDialog").showModal();
}

// after an edit made on top of the info dialog, show it again with the saved values
function reopenInfo() {
  if (!$("vehInfoDialog").open || !ro.info) return;
  const fresh = fleet.vehicles.find((x) => x.vehicle_id === ro.info.vehicle_id);
  if (fresh) openVehicleInfo(fresh);
}

// ------------------------------------------------------------ the master list as a spreadsheet
function exportRoster() {
  const cols = [["เบอร์รถ", (v) => v.truck_number], ["ทะเบียน", (v) => v.plate], ["จังหวัด", (v) => v.province], ["สาขา", (v) => v.branch],
    ["ประเภทรถ", (v) => v.vehicle_class], ["การใช้งาน", (v) => v.usage], ["ยี่ห้อ", (v) => v.make], ["รุ่น", (v) => v.model], ["สี", (v) => v.color],
    ["คนขับหลัก", (v) => v.driver], ["ชื่อเล่น", (v) => v.driver_nickname], ["เบอร์โทร", (v) => v.driver_phone],
    ["วันจดทะเบียน (ค.ศ.)", (v) => v.registered_date], ["ประเภทตามทะเบียน", (v) => v.registration_type], ["เลขเครื่อง", (v) => v.engine_number],
    ["เลขตัวรถ", (v) => v.chassis_number], ["ชื่อกรรมสิทธิ์", (v) => v.owner_name], ["ชื่อผู้ครอบครอง", (v) => v.holder_name],
    ["สถานะ", (v) => (v.active === false ? "เลิกใช้" : "ใช้งานอยู่")], ["หมายเหตุ", (v) => v.notes]];
  const q = (x) => `"${String(x ?? "").replace(/"/g, '""')}"`;
  const lines = [cols.map(([h]) => q(h)).join(","), ...visibleTrucks().map((v) => cols.map(([, f]) => q(f(v))).join(","))];
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
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
    : [el("p", { class: "muted empty", text: "ยังไม่มีคนขับ — กด “+ เพิ่มคนขับ” หรือเลือก “+ คนขับใหม่…” ที่ช่องคนขับหลักของรถ" })]));
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
      ...(d.trucks.length ? d.trucks.map((t) => el("button", { class: "chipbtn", type: "button", title: "ดูประวัติซ่อมของรถคันนี้", text: truckLabel({ truck_number: t.truck_number, plate: t.plate }),
        onclick: () => showTruckLog(t.vehicle_id) })) : [el("span", { class: "muted small", text: d.active ? "ยังไม่ได้เป็นคนขับหลักของคันไหน" : "" })])),
    el("div", { class: "rr-actions" },
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
  await refreshRoster();
  await syncPickers(false);
  reopenInfo();
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
  for (const id of ["roBranch", "roUsage", "roStatus"]) $(id).addEventListener("change", renderRoster);
  $("roAdd").addEventListener("click", () => openVehicleDialog(null));
  $("roTabTrucks").addEventListener("click", () => showRosterTab("trucks"));
  $("roTabDrivers").addEventListener("click", () => showRosterTab("drivers"));
  $("drvAdd").addEventListener("click", () => openDriverDialog(null));
  $("drvShowOff").addEventListener("change", renderDrivers);
  $("vdCancel").addEventListener("click", () => $("vehDialog").close());
  $("vdSave").addEventListener("click", saveVehicle);
  bindDateField($("vdRegDate"), $("vdRegDateCap"));
  $("roExport").addEventListener("click", exportRoster);
  $("viClose").addEventListener("click", () => $("vehInfoDialog").close());
  $("viHistory").addEventListener("click", () => { $("vehInfoDialog").close(); showTruckLog(ro.info.vehicle_id); });
  $("viEdit").addEventListener("click", () => openVehicleDialog(ro.info));
  $("ddCancel").addEventListener("click", () => $("drvDialog").close());
  $("ddSave").addEventListener("click", saveDriver);
}
