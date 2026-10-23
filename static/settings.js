"use strict";

// ตั้งค่า (#settings): the things that used to be written in the code, changed here instead.
//   * the repair shops: the name, the words that mean it on a bill, who to call
// One shop is one place: a dealership with several departments (ศูนย์บริการ, ศูนย์ซ่อมสีและตัวถัง) is a single shop whose
// departments are its contacts. The bills keep the department as written; the log, the board and the dashboard see one shop.
// Data: /api/settings/shops (app/shops.py). More settings can be added here later. Loaded BEFORE app.js.

const st = { data: null };

async function openAppSettings() {
  showView("settings");
  showErr("stError", "");
  await loadAppSettings();
}

async function loadAppSettings() {
  try {
    st.data = await api("/api/settings/shops");
  } catch (err) {
    showErr("stError", `โหลดตั้งค่าไม่สำเร็จ: ${err.message}`);
    return;
  }
  $("stNeedsV15").hidden = st.data.ready !== false;
  renderAppSettings();
}

function renderAppSettings() {
  fill($("stShops"), ...st.data.shops.map(shopCard), newShopCard());
}

// the board's shop buttons, the call buttons and the log's shop filter all read the shop list: tell them
async function stRefreshElsewhere() {
  if (typeof loadMeta === "function") await loadMeta();
}

function shopCard(s) {
  const d = { name: s.name, active: s.active !== false, aliases: [...s.aliases], contacts: s.contacts.map((c) => ({ ...c })) };
  const msg = el("span", { class: "muted small" });
  const name = el("input", { type: "text", maxlength: "60", value: d.name, "aria-label": "ชื่อร้าน" });
  name.addEventListener("input", () => { d.name = name.value; });
  const active = el("input", { type: "checkbox" });
  active.checked = d.active;
  active.addEventListener("change", () => { d.active = active.checked; });

  const aliasBox = el("div", { class: "chips" });
  const drawAliases = () => {
    const input = el("input", { type: "text", maxlength: "60", placeholder: "คำที่เจอในบิล เช่น จึงกงเฮง", "aria-label": "เพิ่มคำ" });
    const add = () => { const w = input.value.trim(); if (w && !d.aliases.includes(w)) { d.aliases.push(w); drawAliases(); } };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    fill(aliasBox, ...d.aliases.map((w, i) => el("span", { class: "stword" }, w, el("button", { type: "button", title: "เอาคำนี้ออก", "aria-label": `เอา ${w} ออก`, text: "✕", onclick: () => { d.aliases.splice(i, 1); drawAliases(); } }))),
      el("span", { class: "stword-add" }, input, el("button", { class: "btn small", type: "button", text: "+ เพิ่มคำ", onclick: add })));
  };
  drawAliases();

  const contactBox = el("div", { class: "stcontacts" });
  const drawContacts = () => {
    fill(contactBox, ...d.contacts.map((c, i) => {
      const label = el("input", { type: "text", maxlength: "40", value: c.label, placeholder: "แผนก / ชื่อคน เช่น ศูนย์บริการ", "aria-label": "แผนก" });
      const phone = el("input", { type: "tel", maxlength: "30", value: c.phone, placeholder: "081-234-5678", "aria-label": "เบอร์โทร" });
      label.addEventListener("input", () => { c.label = label.value; });
      phone.addEventListener("input", () => { c.phone = phone.value; });
      return el("div", { class: "strow" }, label, phone, el("button", { class: "btn small", type: "button", title: "ลบเบอร์นี้", text: "✕", onclick: () => { d.contacts.splice(i, 1); drawContacts(); } }));
    }), el("button", { class: "btn small", type: "button", text: "+ เพิ่มเบอร์ / แผนก", onclick: () => { d.contacts.push({ label: "", phone: "" }); drawContacts(); } }));
  };
  drawContacts();

  const save = async () => {
    try {
      st.data = await api(`/api/settings/shops/${s.id}`, jsonOpts("PATCH", { name: d.name, active: d.active, aliases: d.aliases, contacts: d.contacts }));
    } catch (err) { msg.textContent = err.message; msg.className = "error small"; return; }
    await stRefreshElsewhere();
    renderAppSettings();
  };
  return el("section", { class: `card stshop${d.active ? "" : " off"}` },
    el("div", { class: "strow head" }, name, el("label", { class: "inline small" }, active, "ใช้งาน"),
      s.in_house ? el("span", { class: "badge", text: "งานที่ทำเองในโรงงาน" }) : null),
    el("div", { class: "muted small", text: "คำที่เจอในบิลหรือที่พิมพ์ แล้วให้นับเป็นร้านนี้ (ชื่อร้านเองนับอยู่แล้ว)" }), aliasBox,
    el("div", { class: "muted small sthead", text: "เบอร์โทร — ร้านเดียวหลายแผนกก็ใส่เป็นหลายเบอร์ในร้านเดียว" }), contactBox,
    el("div", { class: "pl-set-actions" }, el("button", { class: "btn small primary", type: "button", text: "บันทึก", onclick: save }), msg));
}

function newShopCard() {
  const name = el("input", { type: "text", maxlength: "60", placeholder: "ชื่อร้าน / อู่ / ศูนย์ ใหม่", "aria-label": "ชื่อร้านใหม่" });
  const msg = el("span", { class: "muted small" });
  const add = async () => {
    try {
      st.data = await api("/api/settings/shops", jsonOpts("POST", { name: name.value }));
    } catch (err) { msg.textContent = err.message; msg.className = "error small"; return; }
    await stRefreshElsewhere();
    renderAppSettings();
  };
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  return el("section", { class: "card stshop stnew" }, el("div", { class: "strow head" }, name, el("button", { class: "btn small primary", type: "button", text: "+ เพิ่มร้าน", onclick: add })),
    el("div", { class: "muted small", text: "เพิ่มแล้วค่อยใส่คำที่เจอในบิลและเบอร์โทรในการ์ดของร้าน" }), msg);
}
