"use strict";

// What needs attention today -- on screen, never by LINE (the phone is on do-not-disturb):
//   (no bar across the top of the pages -- it was taken out: the numbers in the menu are enough)
//   * red / amber numbers beside "บอร์ดของฉัน" and "แผนซ่อมบำรุง" in the menu
//   * a small message on screen when a ticket's own time comes, and (if switched on in ผู้จัดการ › ตั้งค่า) a browser
//     notification -- these only work while this site is open in a tab
// Data: /api/today (app/mgr_jobs.py). Loaded BEFORE app.js: nothing here runs at load time.

const td = { data: null, started: false, toastTimer: null };

const tdStore = (k, v) => {
  try {
    if (v === undefined) return localStorage.getItem(k);
    localStorage.setItem(k, v);
  } catch (e) { /* storage blocked: the reminders still show, they just may repeat */ }
  return null;
};
const tdDay = () => (td.data ? td.data.as_of : "");

// ------------------------------------------------------------------ notifications (the browser's own)
const todayNotifySupported = () => typeof Notification !== "undefined";
const todayNotifyOn = () => todayNotifySupported() && tdStore("vt_notify") === "1" && Notification.permission === "granted";

function todayNotifyStatus() {
  if (!todayNotifySupported()) return "เบราว์เซอร์นี้ไม่รองรับการแจ้งเตือน";
  if (Notification.permission === "denied") return "เบราว์เซอร์บล็อกการแจ้งเตือนของเว็บนี้ไว้ — ไปเปิดที่ตั้งค่าของเบราว์เซอร์ (ไอคอนกุญแจข้างชื่อเว็บ)";
  return todayNotifyOn() ? "เปิดอยู่บนเครื่องนี้ (ต้องเปิดหน้าเว็บนี้ค้างไว้)" : "ปิดอยู่";
}

async function todaySetNotify(on) {
  if (!on) { tdStore("vt_notify", "0"); return null; }
  if (!todayNotifySupported()) return todayNotifyStatus();
  const result = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  if (result === "granted") { tdStore("vt_notify", "1"); return null; }
  tdStore("vt_notify", "0");
  return todayNotifyStatus();
}

function todayToast(text) {
  let box = document.getElementById("todayToast");
  if (!box) { box = el("div", { class: "toast", id: "todayToast", role: "status" }); document.body.append(box); }
  box.textContent = text;
  box.hidden = false;
  clearTimeout(td.toastTimer);
  td.toastTimer = setTimeout(() => { box.hidden = true; }, 15000);
}

function todayAlert(title, body, tag) {
  todayToast(`${title}\n${body}`);
  if (todayNotifyOn()) {
    try { new Notification(title, { body, tag, icon: "/static/favicon.png" }); } catch (e) { /* some browsers only allow it from a service worker */ }
  }
}

function todayTest() {
  todayAlert("ทดสอบการแจ้งเตือน", "ถ้าเห็นข้อความนี้ แสดงว่าแจ้งเตือนทำงาน", "vt-test");
}

// ------------------------------------------------------------------ loading and drawing
async function loadToday(fresh = false) {
  try {
    td.data = await api(`/api/today${fresh ? "?fresh=true" : ""}`);
  } catch (err) {
    return;                                              // no token yet, or offline: the bar simply stays as it was
  }
  renderToday();
  announceToday();
  checkTimed();
}

function navBadge(navId, n, urgent) {
  const nav = document.getElementById(navId);
  if (!nav) return;
  let b = nav.querySelector(".navbadge");
  if (!n) { if (b) b.remove(); return; }
  if (!b) { b = el("span", { class: "navbadge" }); nav.append(b); }
  b.textContent = String(n);
  b.classList.toggle("warn", !urgent);
  b.title = urgent ? "มีเรื่องเลยกำหนด" : "มีเรื่องที่ต้องดู";
}

const tdSignature = (d) => JSON.stringify([d.as_of, d.mgr.overdue, d.mgr.today, d.mgr.follow_ups, d.mgr.urgent, d.plan.counts]);

function renderToday() {
  const d = td.data, m = d.mgr, p = d.plan;
  const mgrN = m.ready ? m.overdue + m.today + m.follow_ups : 0;
  const planOn = p.enabled && p.total > 0;
  navBadge("navMgr", mgrN, m.ready && m.overdue > 0);
  navBadge("navPlan", planOn ? p.counts.overdue + p.counts.due + p.counts.soon : 0, planOn && p.counts.overdue + p.counts.due > 0);
}

// one browser notification per day for the summary (and again if the numbers change), never for the same thing twice
function announceToday() {
  const d = td.data;
  if (!todayNotifyOn() || !d.mgr.ready) return;
  const sig = tdSignature(d);
  if (tdStore("vt_announced") === sig) return;
  const m = d.mgr, p = d.plan;
  const bits = [];
  if (m.overdue + m.today + m.follow_ups) bits.push(`บอร์ด: เลยกำหนด ${m.overdue} · วันนี้ ${m.today} · ถึงวันติดตาม ${m.follow_ups}`);
  if (p.enabled && p.total) bits.push(`รถ: เลยกำหนด ${p.counts.overdue} · ครบวันนี้ ${p.counts.due} · ใกล้ถึงรอบ ${p.counts.soon}`);
  tdStore("vt_announced", sig);
  if (bits.length) todayAlert("สรุปงานวันนี้", bits.join("\n"), "vt-summary");
}

// a ticket with its own time ("14:00"): say so when the time comes (while the site is open)
function checkTimed() {
  const d = td.data;
  if (!d || !d.mgr.ready || d.mgr.timed_enabled === false) return;
  const now = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();
  let seen = [];
  try { seen = JSON.parse(tdStore(`vt_timed_${d.as_of}`) || "[]"); } catch (e) { seen = []; }
  for (const t of d.mgr.timed) {
    const [h, m] = t.time.split(":").map(Number);
    const late = mins - (h * 60 + m);
    if (late < 0 || late > 180 || seen.includes(t.id)) continue;
    seen.push(t.id);
    todayAlert(`ถึงเวลา ${t.time}`, `${t.urgent ? "❗" : ""}${t.title}${t.place ? ` · ${t.place}` : ""}`, `vt-${t.id}`);
  }
  tdStore(`vt_timed_${d.as_of}`, JSON.stringify(seen));
}

function startToday() {
  if (td.started) return;
  td.started = true;
  loadToday();
  setInterval(() => loadToday(), 5 * 60 * 1000);
  setInterval(checkTimed, 30 * 1000);
}
