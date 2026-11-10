"use strict";

// ผู้ใช้งาน (owner only, 10 Nov 2026): the people who sign in with their own name (the bookkeeper ...), the menus each one sees,
// and the one-time link each one opens to choose their own password. The owner keeps the access code. Server: app/users.py.
// Also: signing in with a name + password, the set-your-password page, and hiding the menus a person was not given.

const us_ = { data: null };

// ------------------------------------------------------------------ who is signed in
const isStaff = () => !!(state.me && state.me.role === "staff");

function staffMayOpen(hash) {
  if (!isStaff()) return true;
  return (state.me.hashes || []).includes(`#${hash}`);
}

// a person with their own sign-in sees only their menus; everyone sees who is signed in and can sign out
function applyMe() {
  const me = state.me;
  const box = $("meBox");
  if (!me) { box.hidden = true; return; }
  box.hidden = false;
  $("meName").textContent = me.name;
  $("logoutBtn").hidden = !me.username;                 // signed in with a name (not the access code)
  $("navUsers").hidden = me.role === "staff";
  if (me.role !== "staff") return;
  const keep = new Set(me.nav || []);
  document.querySelectorAll("#sidebar .sideitem").forEach((b) => { b.hidden = !keep.has(b.id); });
  document.querySelectorAll("#sidebar .side-group").forEach((g) => { g.hidden = !g.querySelector(".sideitem:not([hidden])"); });
}

// Signing in with a name on a browser that had the owner's access code must not lose that code (owner, 10 Nov 2026): it is kept
// aside, and ออกจากระบบ puts it back.
function keepOwnerCode() {
  const cur = state.token || storedToken();
  if (cur && !cur.startsWith("u_")) { try { localStorage.setItem("vt_owner_code", cur); } catch (e) { /* storage blocked */ } }
}
function ownerCode() { try { return localStorage.getItem("vt_owner_code") || ""; } catch (e) { return ""; } }

async function logout() {
  try { await api("/api/auth/logout", jsonOpts("POST", {})); } catch (e) { /* signed out on this browser anyway */ }
  saveToken(ownerCode());                                  // back to the access code if this browser had it
  location.href = "/";
}

async function loginWithName(ev) {
  ev.preventDefault();
  const err = $("loginError");
  err.hidden = true;
  const username = $("loginUser").value.trim(), password = $("loginPw").value;
  if (!username || !password) { err.textContent = "ใส่ชื่อเข้าระบบและรหัสผ่าน"; err.hidden = false; return; }
  const btn = $("loginForm").querySelector("button[type=submit]");
  btn.disabled = true; btn.textContent = "กำลังเข้าระบบ…";
  if (!isPlainAscii(password)) { err.textContent = NOT_ENGLISH_KEYS; err.hidden = false; return; }
  try {
    const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
    keepOwnerCode();
    saveToken(j.token);
    location.href = "/";
  } catch (e) {
    err.textContent = e.message; err.hidden = false;
    btn.disabled = false; btn.textContent = "เข้าระบบ";
    $("loginPw").select();
  }
}

// ------------------------------------------------------------------ the one-time link: choose your own password
async function showInvite(code) {
  for (const v of VIEW_NAMES) $(v + "View").hidden = true;
  $("tokenBox").hidden = true;
  document.body.classList.remove("booting");
  document.body.classList.add("signed-out");
  const box = $("inviteBox");
  box.hidden = false;
  let info;
  try {
    const r = await fetch(`/api/auth/invite?code=${encodeURIComponent(code)}`);
    info = await r.json();
    if (!r.ok) throw new Error(info.detail || `HTTP ${r.status}`);
  } catch (e) { fill(box, el("p", { class: "error", text: e.message })); return; }
  $("invName").textContent = info.name;
  $("invUser").textContent = info.username;
  $("invForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("invError");
    err.hidden = true;
    const a = $("invPw").value, b = $("invPw2").value;
    if (a !== b) { err.textContent = "รหัสผ่านสองช่องไม่ตรงกัน"; err.hidden = false; return; }
    if (!isPlainAscii(a)) { err.textContent = NOT_ENGLISH_KEYS; err.hidden = false; return; }
    try {
      const r = await fetch("/api/auth/invite", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code, password: a }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      keepOwnerCode();
      saveToken(j.token);
      location.href = "/";
    } catch (e) { err.textContent = e.message; err.hidden = false; }
  });
}

// ------------------------------------------------------------------ the owner's page
async function openUsers() {
  showView("users");
  showErr("usError", "");
  fill($("usBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { us_.data = await api("/api/users"); } catch (err) { fill($("usBody"), null); showErr("usError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  renderUsers();
}

const usLink = (code) => `${location.origin}/?invite=${code}`;

function usShowLink(name, code) {
  const link = usLink(code);
  const dlg = el("dialog", { class: "us-dialog" },
    el("h3", { text: `ลิงก์ตั้งรหัสผ่าน · ${name}` }),
    el("p", { class: "small", text: `ส่งลิงก์นี้ให้ ${name} เปิดบนเครื่องของตัวเอง แล้วตั้งรหัสผ่านเอง (ใช้ได้ครั้งเดียว ภายใน ${us_.data ? us_.data.invite_days : 7} วัน) — ไม่มีใครรู้รหัสนอกจากเจ้าตัว` }),
    el("input", { class: "us-link", type: "text", readonly: true, value: link, onfocus: (e) => e.target.select() }),
    el("div", { class: "us-dlg-foot" },
      el("button", { class: "btn primary", type: "button", text: "คัดลอกลิงก์", onclick: async (e) => {
        try { await navigator.clipboard.writeText(link); e.target.textContent = "คัดลอกแล้ว ✓"; } catch (x) { window.prompt("คัดลอกลิงก์", link); }
      } }),
      el("button", { class: "btn", type: "button", text: "ปิด", onclick: () => { dlg.close(); dlg.remove(); openUsers(); } })));
  document.body.append(dlg);
  dlg.showModal();
}

function usMenuBoxes(selected, prefix) {
  return el("div", { class: "us-menus" }, ...us_.data.menus.map((m) => el("label", { class: "us-menu" },
    el("input", { type: "checkbox", value: m.key, "data-menu": prefix, checked: selected.includes(m.key) }), ` ${m.label}`)));
}
const usPicked = (root, prefix) => [...root.querySelectorAll(`input[data-menu="${prefix}"]:checked`)].map((x) => x.value);

function renderUsers() {
  const d = us_.data;
  const cards = d.users.map((u) => {
    const card = el("div", { class: `card us-user${u.active ? "" : " off"}` },
      el("div", { class: "us-head" },
        el("strong", { text: u.name }), el("span", { class: "muted small", text: ` · ชื่อเข้าระบบ ${u.username}` }),
        u.owner ? el("span", { class: "badge ok", text: "เจ้าของ · ทุกเมนู" }) : null,
        !u.active ? el("span", { class: "badge", text: "ปิดใช้งาน" })
          : u.password_set ? el("span", { class: "badge ok", text: "ตั้งรหัสผ่านแล้ว" })
            : el("span", { class: "badge warn", text: u.invite_open ? "รอเจ้าตัวตั้งรหัสผ่าน" : "ลิงก์หมดอายุ — สร้างใหม่" })),
      u.owner ? null : el("p", { class: "small", text: "เห็นเมนู:" }),
      u.owner ? null : usMenuBoxes(u.menus, `u${u.id}`),
      u.sessions.length ? el("p", { class: "muted small", text: `เข้าระบบอยู่ ${u.sessions.length} เครื่อง: ` +
        u.sessions.map((s) => `${s.agent || "?"} · ใช้ล่าสุด ${s.last_seen ? new Date(s.last_seen).toLocaleString("th-TH") : "—"}`).join(" · ") }) : null,
      el("div", { class: "us-actions" },
        u.owner ? null : el("button", { class: "btn small primary", type: "button", text: "บันทึกเมนู", onclick: () => usSave(u.id, { menus: usPicked(card, `u${u.id}`) }) }),
        el("button", { class: "btn small", type: "button", text: u.password_set ? "ลืมรหัส — สร้างลิงก์ใหม่" : "สร้างลิงก์ใหม่", onclick: () => usInvite(u) }),
        u.sessions.length ? el("button", { class: "btn small", type: "button", text: "ออกจากระบบทุกเครื่อง", onclick: () => usSignOut(u) }) : null,
        el("button", { class: "btn small", type: "button", text: u.active ? "ปิดใช้งาน" : "เปิดใช้งาน", onclick: () => usSave(u.id, { active: !u.active }) })));
    return card;
  });
  const add = el("form", { class: "card us-add" },
    el("h3", { text: "เพิ่มผู้ใช้" }),
    el("label", { class: "small", for: "usName", text: "ชื่อ (แสดงในแอป)" }), el("input", { id: "usName", type: "text", maxlength: "60", placeholder: "เช่น พี่นุช (บัญชี)" }),
    el("label", { class: "small", for: "usUser", text: "ชื่อเข้าระบบ (ภาษาอังกฤษตัวเล็ก / ตัวเลข)" }),
    el("input", { id: "usUser", type: "text", maxlength: "30", autocomplete: "off", autocapitalize: "none", spellcheck: "false", placeholder: "เช่น account1" }),
    el("label", { class: "us-menu" }, el("input", { type: "checkbox", id: "usOwner", onchange: (e) => { add.querySelector(".us-menus").hidden = e.target.checked; } }),
      " เจ้าของ — เห็นทุกเมนู และจัดการผู้ใช้ได้ (เช่น บัญชีของคุณเอง)"),
    el("p", { class: "small", text: "เห็นเมนู:" }), usMenuBoxes(["sales_sheets"], "new"),
    el("button", { class: "btn primary", type: "submit", text: "เพิ่ม แล้วรับลิงก์ตั้งรหัสผ่าน" }));
  add.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("usName").value.trim();
    let r;
    try { r = await api("/api/users", jsonOpts("POST", { name, username: $("usUser").value.trim(), menus: usPicked(add, "new"), owner: $("usOwner").checked })); }
    catch (err) { alert(err.message); return; }
    usShowLink(name, r.invite);
  });
  fill($("usBody"),
    el("p", { class: "muted small", text: "รหัสเข้าใช้งานเดิมยังใช้ได้เสมอ (เห็นทุกเมนู) · อยากเข้าด้วยชื่อ + รหัสผ่านของคุณเอง: เพิ่มผู้ใช้ ติ๊ก “เจ้าของ” แล้วเปิดลิงก์ตั้งรหัสผ่าน · คนอื่นเห็นเฉพาะเมนูที่ติ๊กให้ — เซิร์ฟเวอร์ไม่ยอมให้เปิดเมนูอื่นแม้พิมพ์ลิงก์เอง" }),
    ...cards, add);
}

async function usSave(id, body) {
  try { await api(`/api/users/${id}`, jsonOpts("PATCH", body)); } catch (err) { alert(err.message); return; }
  openUsers();
}

async function usInvite(u) {
  if (u.password_set && !confirm(`สร้างลิงก์ตั้งรหัสผ่านใหม่ให้ ${u.name}?\nรหัสเดิมยังใช้ได้จนกว่าเจ้าตัวจะตั้งรหัสใหม่จากลิงก์`)) return;
  let r;
  try { r = await api(`/api/users/${u.id}/invite`, jsonOpts("POST", {})); } catch (err) { alert(err.message); return; }
  usShowLink(u.name, r.invite);
}

async function usSignOut(u) {
  if (!confirm(`ให้ ${u.name} ออกจากระบบทุกเครื่อง?`)) return;
  try { await api(`/api/users/${u.id}/signout`, jsonOpts("POST", {})); } catch (err) { alert(err.message); return; }
  openUsers();
}
