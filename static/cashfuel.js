"use strict";

// น้ำมันเงินสด (#cash-fuel), redesigned with the owner 11 Nov 2026 -- like checking a repair bill:
//   1. drop the photos of the handwritten รายการเบิก pages; each is read twice (server: app/cash_fuel.py), with its own progress;
//   2. check each page: the photo big on one side, what was read on the other; the lines the reader is unsure of come first, with
//      the reason in plain words; tick or fix them, then ยืนยันหน้านี้ -- only confirmed pages count;
//   3. the month's รายการค่าน้ำมัน for HQ (Excel / PDF): the whole month by default (26 - 25, sent once a month), or either half
//      (26 - 10, 11 - 25, worked out from the lines' own dates) -- owner, 12 Nov 2026.
// #cash-fuel[/YYYY-MM] = home; #cash-fuel/review/<page id> = checking one page.

const cf_ = { month: null, batch: null, data: null, files: [], urls: {}, zoom: 1 };
const CF_TH = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
const CF_SH = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
const cfD = (iso) => { const [y, m, d] = iso.split("-").map(Number); return { y, m, d }; };
const cfShort = (iso) => { const x = cfD(iso); return `${x.d} ${CF_SH[x.m]}`; };
const cfRange = (a, b) => { if (!a) return "—"; const x = cfD(a), y = cfD(b || a); return x.m === y.m ? `${x.d}–${y.d} ${CF_SH[y.m]} ${y.y + 543}` : `${cfShort(a)} – ${cfShort(b)} ${y.y + 543}`; };
const cfShift = (month, n) => { let [y, m] = month.split("-").map(Number); m += n; while (m < 1) { m += 12; y -= 1; } while (m > 12) { m -= 12; y += 1; } return `${y}-${String(m).padStart(2, "0")}`; };

async function openCashFuel(sub = "") {
  showView("cashFuel");
  showErr("cfError", "");
  if (sub.startsWith("review/")) return cfReview(Number(sub.slice(7)));
  if (/^\d{4}-\d{2}$/.test(sub)) cf_.month = sub;
  document.querySelector("#cashFuelView .page-head").hidden = false;
  fill($("cfBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { cf_.data = await api(`/api/cash-fuel${cf_.month ? `?month=${cf_.month}` : ""}`); }
  catch (err) { fill($("cfBody"), null); showErr("cfError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  if (cf_.month !== cf_.data.month) cf_.batch = null;
  cf_.month = cf_.data.month;
  cfHome();
}

// ------------------------------------------------------------------ home: upload, pages to check, the month
function cfHome() {
  const d = cf_.data;
  fill($("cfBody"), cfUploadCard(), cfPendingCard(), cfMonthCard());
}

function cfUploadCard() {
  const input = el("input", { type: "file", accept: "image/*,application/pdf", multiple: true, hidden: true });
  const list = el("ul", { class: "filelist cf-files" });
  const go = el("button", { class: "btn primary", type: "button", text: "อ่านรูป", disabled: true, onclick: () => cfRead(list, go) });
  const paint = () => {
    fill(list, ...cf_.files.map((f, i) => el("li", { class: `cf-file ${f.state || ""}` },
      el("span", { class: "cf-fname", text: f.file.name }),
      el("span", { class: "cf-fstate", text: f.msg || "พร้อมอ่าน" }),
      !f.state ? el("button", { class: "btn small", type: "button", text: "เอาออก", onclick: () => { cf_.files.splice(i, 1); paint(); } }) : null)));
    const waiting = cf_.files.filter((f) => !f.state).length;
    go.disabled = !waiting;
    go.textContent = waiting ? `อ่านรูป ${waiting} รูป` : "อ่านรูป";
  };
  const add = (files) => { cf_.files.push(...[...files].map((file) => ({ file }))); paint(); };
  input.addEventListener("change", () => { add(input.files); input.value = ""; });
  const drop = el("div", { class: "drop cf-drop", tabindex: "0", role: "button", onclick: () => input.click(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } } },
    el("b", { text: "ลากรูปหรือ PDF รายการเบิกมาวางที่นี่ หรือแตะเพื่อเลือก" }),
    el("span", { class: "muted small", text: "ถ่ายให้เห็นทั้งหน้า · หลายรูปได้ · PDF หลายหน้าจะแยกเป็นหน้าละรูป · อ่านแต่ละหน้า 2 รอบ" }), input);
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => add(e.dataTransfer.files));
  paint();
  return el("section", { class: "card cf-card" }, el("h3", { text: "1 · อัปโหลดรูปรายการเบิก" }), drop, list, el("div", { class: "cf-actions" }, go));
}

async function cfRead(list, go) {
  const todo = cf_.files.filter((f) => !f.state);
  go.disabled = true;
  const one = async (f) => {
    f.state = "busy"; f.msg = /\.pdf$/i.test(f.file.name) ? "กำลังอ่านทุกหน้า (หน้าละ 2 รอบ)… PDF หลายหน้าใช้เวลาหลายนาที" : "กำลังอ่าน (2 รอบ)…"; cfPaintFiles(list);
    const fd = new FormData();
    fd.append("files", f.file);
    try {
      const res = await fetch("/api/cash-fuel/pages", { method: "POST", body: fd, headers: { "X-App-Token": state.token } });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);
      const pages = j.added.length, bad = j.added.filter((a) => a.error).length;
      if (!pages && j.refused.length) { f.state = "bad"; f.msg = j.refused[0].replace(/^[^:]*:\s*/, ""); }
      else if (pages && bad === pages) { f.state = "bad"; f.msg = `อ่านไม่สำเร็จ: ${j.added[0].error}`; }
      else {
        f.state = "ok";
        f.msg = `อ่านแล้ว${pages > 1 ? ` ${pages} หน้า` : ""} · ${j.lines} รายการเติมน้ำมัน${bad ? ` · อ่านไม่ได้ ${bad} หน้า` : ""}${j.refused.length ? ` · ข้าม ${j.refused.length} หน้าที่อัปโหลดแล้ว` : ""}`;
        f.page = (j.added.find((a) => !a.error) || j.added[0]).page_id;
      }
    } catch (err) { f.state = "bad"; f.msg = err.message; }
    cfPaintFiles(list);
  };
  for (let i = 0; i < todo.length; i += 2) await Promise.all(todo.slice(i, i + 2).map(one));     // two at a time
  const first = cf_.files.find((f) => f.state === "ok" && f.page);
  cf_.files = cf_.files.filter((f) => f.state === "bad");                                          // the read ones move to "check"
  if (first) goHash(`#cash-fuel/review/${first.page}`);
  else openCashFuel(cf_.month);
}

function cfPaintFiles(list) {
  fill(list, ...cf_.files.map((f) => el("li", { class: `cf-file ${f.state || ""}` }, el("span", { class: "cf-fname", text: f.file.name }),
    el("span", { class: "cf-fstate", text: f.msg || "พร้อมอ่าน" }))));
}

function cfPendingCard() {
  const p = cf_.data.pending;
  return el("section", { class: "card cf-card" },
    el("h3", { text: `2 · รอตรวจกับรูป${p.length ? ` (${p.length} รูป)` : ""}` }),
    p.length ? el("div", { class: "cf-queue" }, ...p.map((x, i) => el("button", { class: `cf-qitem${x.to_check ? " warn" : ""}`, type: "button",
      onclick: () => goHash(`#cash-fuel/review/${x.id}`) },
      el("b", { text: `รูปที่ ${i + 1}` }),
      el("span", { class: "small", text: `${cfRange(x.from, x.to)} · ${x.lines} รายการ · ฿${elN(x.total)}` }),
      x.status === "error" ? el("span", { class: "badge bad", text: "อ่านไม่สำเร็จ" })
        : x.to_check ? el("span", { class: "badge warn", text: `ต้องดูกับรูป ${x.to_check} รายการ` }) : el("span", { class: "badge ok", text: "อ่านชัดทุกรายการ" }),
      el("span", { class: "cf-qgo", text: "ตรวจ ›" }))))
      : el("p", { class: "muted small", text: "ไม่มีรูปรอตรวจ — ทุกรูปยืนยันแล้ว" }));
}

function cfMonthCard() {
  const d = cf_.data;
  const [y, m] = d.month.split("-").map(Number);
  if (cf_.batch == null) cf_.batch = 0;                                   // the whole month first; the halves are a choice
  const nav = el("div", { class: "cf-mnav" },
    el("button", { class: "btn small", type: "button", text: "‹", "aria-label": "เดือนก่อน", onclick: () => goHash(`#cash-fuel/${cfShift(d.month, -1)}`) }),
    el("b", { text: `เดือน ${CF_TH[m]} ${y + 543}` }), el("span", { class: "muted small", text: `(${cfRange(d.from, d.to)})` }),
    el("button", { class: "btn small", type: "button", text: "›", "aria-label": "เดือนถัดไป", onclick: () => goHash(`#cash-fuel/${cfShift(d.month, 1)}`) }));
  const cur = d.periods.find((p) => p.batch === cf_.batch) || d.periods[0];
  const label = (p) => p.batch === 0 ? "ทั้งเดือน" : p.batch === 1 ? "ครึ่งแรก" : "ครึ่งหลัง";
  const cards = el("div", { class: "cf-pick" },
    el("div", { class: "cf-ptot" }, el("span", { class: "cf-ptotal", text: `฿${elN(cur.total)}` }),
      el("span", { class: "muted small", text: cur.fills ? `${label(cur)} · ${cur.fills} ครั้ง · ${cur.trucks} คัน` : "ยังไม่มีรายการที่ยืนยัน" }),
      cur.other_total ? el("span", { class: "small", text: `คนขับรถสาย ฿${elN(cur.route_total)} · ไม่ใช่คนขับรถสาย ฿${elN(cur.other_total)}` }) : null),
    el("div", { class: "seg" }, ...d.periods.map((p) => el("button", { type: "button", class: `segbtn${cf_.batch === p.batch ? " active" : ""}`,
      title: `${cfRange(p.from, p.to)} · ฿${elN(p.total)}`, onclick: () => { cf_.batch = p.batch; cfHome(); } },
      label(p), p.batch ? el("span", { class: "cf-segsub", text: cfRange(p.from, p.to) }) : null))));
  return el("section", { class: "card cf-card" }, el("h3", { text: "3 · รายการค่าน้ำมัน (ส่ง HQ)" }), nav,
    el("p", { class: "muted small", text: "นับเฉพาะรูปที่ยืนยันแล้ว · งวดดูจากวันที่ของแต่ละรายการเอง" }), cards, cfSummary());
}

function cfSummary() {
  const d = cf_.data, p = d.periods.find((x) => x.batch === cf_.batch);
  const byId = Object.fromEntries(d.vehicles.map((v) => [v.id, v]));
  // by who took the money (owner, 12 Nov 2026): คนขับรถสาย first, even in a รถหน้าโรง; everyone else below, as in the file
  const routeNames = new Set(((d.people || {}).route || []).map((p) => p.name.trim()));
  const isRoute = (l) => routeNames.has((l.driver_name || "").trim());
  const groups = new Map();
  d.lines.filter((l) => !(l.dup_of && !l.counted) && (!cf_.batch || l.batch === cf_.batch)).forEach((l) => {
    const k = `${isRoute(l) ? 0 : 1}:${l.vehicle_id || "?"}`;
    if (!groups.has(k)) groups.set(k, { v: byId[l.vehicle_id], route: isRoute(l), fills: [], drivers: {} });
    const g = groups.get(k);
    g.fills.push(l);
    const n = (l.driver_name || "").split(" ")[0];
    if (n) g.drivers[n] = (g.drivers[n] || 0) + 1;
  });
  const rows = [...groups.values()].sort((a, b) => (a.route ? 0 : 1) - (b.route ? 0 : 1)
    || (Number((a.v || {}).truck_number) || 999) - (Number((b.v || {}).truck_number) || 999));
  const sum = (gs) => gs.reduce((x, g) => x + g.fills.reduce((y, l) => y + Number(l.amount || 0), 0), 0);
  const truckRow = (g) => el("tr", {},
        el("td", {}, el("b", { text: (g.v || {}).truck_number || "" })),
        el("td", { text: g.v ? g.v.plate : "ไม่มีทะเบียนรถ" }),
        el("td", { text: Object.entries(g.drivers).sort((a, b) => b[1] - a[1]).map(([n]) => n).slice(0, 2).join(", ") }),
        el("td", { class: "small" }, ...g.fills.slice().sort((a, b) => a.day.localeCompare(b.day)).map((l) =>
          el("button", { class: "cf-fill", type: "button", title: l.page_id ? "เปิดรูปที่อ่านรายการนี้" : "เพิ่มเอง",
            text: `${cfShort(l.day)} ${elN(l.amount)}`, onclick: () => l.page_id && goHash(`#cash-fuel/review/${l.page_id}`) }))),
        el("td", { class: "r" }, el("b", { text: elN(g.fills.reduce((x, l) => x + Number(l.amount || 0), 0)) })));
  const route = rows.filter((g) => g.route), other = rows.filter((g) => !g.route);
  const totRow = (label, gs) => el("tr", { class: "tot" }, el("td", { colspan: "4", class: "r", text: label }), el("td", { class: "r" }, el("b", { text: elN(sum(gs)) })));
  const q = `month=${cf_.month}&batch=${cf_.batch}`;
  return el("div", { class: "cf-summary" },
    el("div", { class: "ss-custbar" }, el("b", { text: `${cf_.batch ? (cf_.batch === 1 ? "ครึ่งแรก " : "ครึ่งหลัง ") : "ทั้งเดือน "}${cfRange(p.from, p.to)}` }), el("span", { class: "muted small", text: "ไฟล์ส่ง HQ: ตามวันที่ และ ตามรถ (คนขับรถสาย) · คนอื่นแยกอีกแผ่น" }), el("span", { class: "grow" }),
      el("button", { class: "btn small", type: "button", text: "+ เพิ่มรายการเอง", onclick: cfAddLine }),
      el("button", { class: "btn", type: "button", text: "PDF (พิมพ์)", title: "ตามวันที่ และ ตามรถ (คนขับรถสาย) · คนอื่นแยกอีกหน้า", disabled: !rows.length, onclick: () => ssSheetFile(`/api/cash-fuel/summary.pdf?${q}`, "pdf") }),
      el("button", { class: "btn", type: "button", text: "Excel", title: "แผ่นงาน ตามวันที่ และ ตามรถ (คนขับรถสาย) · คนอื่นแยกอีกแผ่น", disabled: !rows.length, onclick: () => ssSheetFile(`/api/cash-fuel/summary.xlsx?${q}`, "xlsx") })),
    rows.length ? el("div", { class: "ss-tablewrap soft" }, el("table", { class: "ss-table" },
      el("thead", {}, el("tr", {}, ...["รถเบอร์", "ทะเบียนรถ", "พนักงานขับรถ", "วันที่ · จำนวน", "รวม"].map((h) => el("th", { text: h })))),
      el("tbody", {}, ...route.map(truckRow), totRow(other.length ? "รวม คนขับรถสาย (ส่ง HQ)" : "รวม", route),
        ...(other.length ? [el("tr", { class: "cf-sechead" }, el("td", { colspan: "5", text: "ไม่ใช่คนขับรถสาย (หน้าโรง ช่าง ไม่มีชื่อ) — แผ่นแยกในไฟล์" })),
          ...other.map(truckRow), totRow("รวม ไม่ใช่คนขับรถสาย", other)] : []))))
      : el("p", { class: "muted", text: "ยังไม่มีรายการที่ยืนยันในงวดนี้" }));
}

// ------------------------------------------------------------------ checking one page against its photo
async function cfReview(id) {
  document.querySelector("#cashFuelView .page-head").hidden = true;
  fill($("cfBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  let d;
  try { d = await api(`/api/cash-fuel/pages/${id}`); } catch (err) { fill($("cfBody"), null); showErr("cfError", err.message); return; }
  cf_.review = d;
  cf_.zoom = 1;
  const pos = d.queue.indexOf(id);
  const next = d.queue.find((x) => x !== id && d.queue.indexOf(x) > pos) || d.queue.find((x) => x !== id);
  const img = el("img", { alt: "หน้ารายการเบิก", class: "cf-img" });
  const photo = el("div", { class: "cf-photo" }, el("p", { class: "muted small", text: "กำลังโหลดรูป…" }));
  const setZoom = (z) => { cf_.zoom = z; img.style.width = `${z * 100}%`; };
  cfImage(id).then((u) => { img.src = u; fill(photo, img); }).catch((err) => fill(photo, el("p", { class: "error small", text: `โหลดรูปไม่ได้: ${err.message}` })));
  const head = el("div", { class: "cf-rhead" },
    el("button", { class: "btn small", type: "button", text: "‹ กลับ", onclick: () => goHash(`#cash-fuel/${cf_.month || ""}`) }),
    el("b", { text: d.page.confirmed ? "รูปนี้ยืนยันแล้ว" : pos >= 0 ? `ตรวจรูปที่ ${pos + 1} จาก ${d.queue.length}` : "ตรวจรูป" }),
    el("span", { class: "muted small", text: d.page.name || "" }), el("span", { class: "grow" }),
    el("span", { class: "cf-zoom" }, ...[[1, "พอดี"], [1.6, "ขยาย"], [2.4, "ขยายมาก"]].map(([z, t]) =>
      el("button", { class: "btn small", type: "button", text: t, onclick: () => setZoom(z) }))));
  // same order as the photo, top to bottom (owner, 11 Nov: doubtful-first hid the first row); doubtful ones are marked, and a button jumps to the next
  const lines = d.lines.slice().sort((a, b) => a.seq - b.seq);
  const doubts = lines.filter(cfDoubt).length;
  const nextDoubt = () => {
    const c = [...document.querySelectorAll(".cf-lrow.doubt")];
    if (!c.length) return;
    const top = c.find((x) => x.getBoundingClientRect().top > 140) || c[0];
    top.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const renumber = () => [...ltable.querySelectorAll("td.cf-n")].forEach((td, i) => { td.textContent = String(i + 1); });
  const removed = (l) => { d.lines = d.lines.filter((x) => x !== l); renumber(); if (cf_.paintConfirm) cf_.paintConfirm(); };
  const ltable = el("table", { class: "cf-ltable" },
    el("thead", {}, el("tr", {}, ...["#", "วันที่", "รถ", "คนเบิก", "ยอด (บาท)", ""].map((t) => el("th", { text: t })))),
    ...lines.map((l, i) => cfLineCard(l, d, i + 1, removed)));
  const totRow = el("tr", { class: "cf-totrow" });
  const tfoot = el("tfoot", {}, totRow);
  ltable.append(tfoot);
  const addRow = async () => {
    const last = d.lines[d.lines.length - 1];
    let row;
    try { row = await api("/api/cash-fuel/lines", jsonOpts("POST", { page_id: id, day: last ? last.day : new Date().toISOString().slice(0, 10), amount: 0 })); }
    catch (err) { alert(err.message); return; }
    d.lines.push(row);
    const body = cfLineCard(row, d, d.lines.length, removed);
    ltable.insertBefore(body, tfoot);
    renumber();
    if (cf_.paintConfirm) cf_.paintConfirm();
    const sel = body.querySelector("select"); if (sel) sel.focus();
  };
  const list = el("div", { class: "cf-lines" },
    d.page.status === "error" ? el("p", { class: "error", text: `อ่านรูปนี้ไม่สำเร็จ: ${d.page.error || ""} — ลบรูปนี้แล้วถ่ายใหม่ หรือเพิ่มรายการเอง` }) : null,
    d.page.confirmed ? el("p", { class: "ok-text small", text: `ยืนยันแล้วโดย ${d.page.confirmed_by || "—"} · ${new Date(d.page.confirmed_at).toLocaleString("th-TH")}` }) : null,
    doubts ? el("div", { class: "cf-doubthead" }, el("p", { class: "warn-text", text: `อ่านได้ ${lines.length} รายการ เรียงตามรูป · มี ${doubts} รายการที่ระบบไม่แน่ใจ (แถวสีส้ม) — ดูกับรูป แล้วกด “ถูกแล้ว” หรือแก้ให้ถูก` }),
      el("button", { class: "btn small", type: "button", text: "ไปรายการที่ต้องดู ›", onclick: nextDoubt }))
      : el("p", { class: "muted small", text: `อ่านได้ ${lines.length} รายการเติมน้ำมัน · ดูกับรูปอีกครั้งแล้วกดยืนยัน` }),
    ltable,
    d.page.confirmed ? null : el("button", { class: "btn small cf-addrow", type: "button", text: "+ เพิ่มแถว", title: "บรรทัดที่ระบบอ่านไม่เจอ", onclick: addRow }));
  const total = () => d.lines.filter((l) => l.counted).reduce((a, l) => a + Number(l.amount || 0), 0);
  const confirmBtn = el("button", { class: "btn primary", type: "button", onclick: async () => {
    try { await api(`/api/cash-fuel/pages/${id}/confirm`, jsonOpts("POST", { confirmed: true })); } catch (err) { alert(err.message); return; }
    if (typeof todayToast === "function") todayToast("ยืนยันแล้ว ✓");
    goHash(next ? `#cash-fuel/review/${next}` : `#cash-fuel/${cf_.month || ""}`);
  } });
  const why = el("span", { class: "warn-text small cf-why-bar" });
  const paintConfirm = () => {
    const rows = [...ltable.querySelectorAll("tbody.cf-lrow")];
    const openNo = rows.map((b, i) => (b.classList.contains("doubt") ? i + 1 : 0)).filter(Boolean);
    const all = d.lines.reduce((a, l) => a + Number(l.amount || 0), 0);
    const foh = d.lines.filter((l) => !l.counted && !l.dup_of).reduce((a, l) => a + Number(l.amount || 0), 0);
    const w = d.page.written_total, other = Number(d.page.other_total || 0);
    const gap = w == null ? null : Number(w) - all - other;
    fill(totRow, el("td", {}),
      el("td", { colspan: "3" }, el("div", { text: `รวมทั้งหน้า ${d.lines.length} รายการ` }),
        w == null ? null : el("div", { class: `small ${Math.abs(gap) < 1 ? "ok-text" : "warn-text"}`,
          text: `ยอดรวมที่เขียนในรูป ฿${elN(w)}${other ? ` (มีรายการอื่นที่ไม่ใช่น้ำมัน ฿${elN(other)})` : ""} · ${Math.abs(gap) < 1 ? "ตรง" : `ไม่ตรง ต่าง ฿${elN(Math.abs(gap))}`}` }),
        foh ? el("div", { class: "small muted", text: `รถสาย ฿${elN(total())} · หน้าโรง ฿${elN(foh)}` }) : null),
      el("td", { class: "cf-amount", text: `฿${elN(all)}` }), el("td", {}));
    confirmBtn.textContent = d.page.confirmed ? "ยืนยันแล้ว" : "ยืนยันหน้านี้";
    confirmBtn.disabled = !!openNo.length || d.page.confirmed;
    why.textContent = d.page.confirmed || !openNo.length ? "" : `ยังกดไม่ได้: แถวที่ ${openNo.slice(0, 6).join(", ")}${openNo.length > 6 ? " …" : ""} ยังต้องดู (สีส้ม)`;
  };
  cf_.paintConfirm = paintConfirm;
  paintConfirm();
  const bar = el("div", { class: "ss-savebar cf-rbar" },
    confirmBtn, why,
    d.page.confirmed ? el("button", { class: "btn", type: "button", text: "แก้ไข (ยกเลิกการยืนยัน)", onclick: async () => {
      try { await api(`/api/cash-fuel/pages/${id}/confirm`, jsonOpts("POST", { confirmed: false })); } catch (err) { alert(err.message); return; }
      cfReview(id);
    } }) : null,
    next ? el("button", { class: "btn", type: "button", text: "ข้ามไปรูปถัดไป ›", onclick: () => goHash(`#cash-fuel/review/${next}`) }) : null,
    el("span", { class: "grow" }),
    el("button", { class: "btn danger", type: "button", text: "ลบรูปนี้", onclick: async () => {
      if (!confirm("ลบรูปนี้และทุกรายการที่อ่านจากรูปนี้?")) return;
      try { await api(`/api/cash-fuel/pages/${id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
      goHash(next ? `#cash-fuel/review/${next}` : `#cash-fuel/${cf_.month || ""}`);
    } }));
  fill($("cfBody"), head, el("div", { class: "cf-review" }, photo, el("div", { class: "cf-right" }, list, bar)));
}

// a row still to sort out (orange): the reader was unsure and nobody has looked, or it counts but has no truck / no amount
// every row needs ทะเบียน (or ไม่มีทะเบียนรถ), a date, ผู้เบิก (or ไม่มีชื่อผู้เบิก) and an amount (owner, 11 Nov 2026)
const cfMissing = (l) => !((l.vehicle_id || l.no_truck) && l.day && (l.driver_name || l.no_name) && Number(l.amount) > 0);
const cfDoubt = (l) => cfReasons(l).length > 0;
function cfReasons(l) {
  const out = [];
  if (!l.vehicle_id && !l.no_truck) out.push("ยังไม่ได้เลือกรถ (หรือ “ไม่มีทะเบียนรถ”)");
  if (!l.day) out.push("ยังไม่มีวันที่");
  if (!l.driver_name && !l.no_name) out.push("ยังไม่ได้เลือกผู้เบิก (หรือ “ไม่มีชื่อผู้เบิก”)");
  if (!(Number(l.amount) > 0)) out.push("ยังไม่มียอด");
  // "ไม่พบรถ" is settled once a truck (or ไม่มีทะเบียนรถ) is chosen; while none is, the line above says so
  if (l.note && !l.checked && !l.dup_of) out.push(...l.note.split(" · ").filter((r) => r && r !== "ไม่พบรถ" && !r.startsWith("ซ้ำกับ")).map(cfPlain));
  return out;
}

async function cfImage(id) {
  if (cf_.urls[id]) return cf_.urls[id];
  const res = await fetch(`/api/cash-fuel/pages/${id}/image`, { headers: { "X-App-Token": state.token } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  cf_.urls[id] = URL.createObjectURL(await res.blob());
  return cf_.urls[id];
}

// one line = one table row (owner, 11 Nov: side by side with the photo, no scrolling); a doubtful line gets an orange row under it saying why
function cfLineCard(l, d, n, onRemove) {
  const body = el("tbody", { class: "cf-lrow" });
  const save = async (patch) => {
    try { Object.assign(l, await api(`/api/cash-fuel/lines/${l.id}`, jsonOpts("PATCH", patch))); } catch (err) { alert(err.message); }
    paint();
    if (cf_.paintConfirm) cf_.paintConfirm();
  };
  const vopts = d.vehicles.slice().sort((a, b) => (Number(a.truck_number) || 999) - (Number(b.truck_number) || 999));
  const del = async () => {
    if (!confirm("ลบแถวนี้?")) return;
    try { await api(`/api/cash-fuel/lines/${l.id}`, { method: "DELETE" }); } catch (err) { alert(err.message); return; }
    body.remove();
    if (onRemove) onRemove(l);
  };
  const paint = () => {
    const doubt = cfDoubt(l);
    const foh = !l.counted && !l.dup_of && ((d.people || {}).foh || []).some((p) => p.name === l.driver_name);
    body.className = `cf-lrow${doubt ? " doubt" : ""}`;
    const reasons = cfReasons(l);
    fill(body,
      el("tr", {},
        el("td", { class: "cf-n" }, n ? String(n) : ""),
        el("td", {}, el("input", { type: "date", class: "cf-day", value: l.day, onchange: (e) => e.target.value && save({ day: e.target.value }) })),
        el("td", {}, el("select", { onchange: (e) => save(e.target.value === "none" ? { no_truck: true } : { vehicle_id: e.target.value || null, no_truck: false }) },
          el("option", { value: "", text: "— เลือก —", selected: !l.vehicle_id && !l.no_truck }),
          el("option", { value: "none", text: "ไม่มีทะเบียนรถ", selected: !!l.no_truck }), ...vopts.map((v) => el("option", { value: v.id, text: cfVehicleLabel(v), selected: v.id === l.vehicle_id })))),
        el("td", {}, cfPeopleSelect(d, l.driver_name, (v) => save(v === "none" ? { no_name: true } : { driver_name: v, no_name: false }), l.no_name)),
        el("td", { class: "cf-amount" }, el("input", { type: "text", inputmode: "decimal", value: String(Number(l.amount)), onchange: (e) => save({ amount: e.target.value }) })),
        el("td", { class: "cf-acts" },
          doubt && !cfMissing(l) ? el("button", { class: "btn small primary", type: "button", text: "ถูกแล้ว", title: "ดูกับรูปแล้ว ถูกต้อง", onclick: () => save({ checked: true }) })
            : !doubt && l.note && l.checked ? el("span", { class: "ok-text small", text: "✓ ดูแล้ว" }) : null,
          foh ? el("span", { class: "cf-tag", text: "หน้าโรง", title: "อยู่ในยอดรวมของหน้านี้ แต่ไม่อยู่ในรายงานส่ง HQ" }) : null,
          el("button", { class: "btn small cf-del", type: "button", text: "ลบ", title: "ลบแถวนี้ (อ่านผิด หรือไม่ใช่ค่าน้ำมัน)", onclick: del }))),
      doubt ? el("tr", { class: "cf-whyrow" }, el("td", {}), el("td", { colspan: "5" }, reasons.join(" · "))) : null,
      !l.counted && l.dup_of ? el("tr", { class: "cf-inforow" }, el("td", {}), el("td", { colspan: "5" }, "ซ้ำกับรายการในรูปอื่นที่นับแล้ว — ไม่นับซ้ำ ",
        el("button", { class: "btn small", type: "button", text: "ไม่ซ้ำ นับด้วย", onclick: () => save({ counted: true }) }))) : null);
  };
  paint();
  return body;
}

function cfPlain(r) {   // the reader's notes in plain words
  if (r.startsWith("อ่านรอบสองได้")) return `อ่าน 2 รอบได้ไม่ตรงกัน — รอบที่สองเห็น ${r.replace("อ่านรอบสองได้", "").trim()}`;
  if (r.startsWith("รอบสองไม่เห็น")) return "อ่าน 2 รอบได้ไม่ตรงกัน — รอบที่สองไม่เห็นบรรทัดนี้";
  if (r === "อ่านไม่ชัด — ตรวจกับรูป") return "ลายมือไม่ชัด — ระบบไม่แน่ใจตัวเลข";
  if (r === "ไม่พบรถ") return "ไม่ได้เขียนทะเบียน และไม่รู้ว่าคนเบิกขับรถคันไหน — เลือกรถ";
  if (r.startsWith("ไม่ได้เขียนทะเบียน")) return `${r} (ตาม IceBox) — ดูว่าถูกคันไหม`;
  if (r === "อ่านวันที่ไม่ได้") return "อ่านวันที่ไม่ได้ — ใส่วันที่";
  return r;
}

function cfVehicleLabel(v) { return v ? `${v.truck_number ? `${v.truck_number} · ` : ""}${v.plate || ""}${v.branch && v.branch !== "สระบุรี" ? ` (${v.branch})` : ""}` : ""; }

// คนเบิก: the drivers, คนขับรถสาย and หน้าโรง in their own groups (owner, 11 Nov 2026)
function cfPeopleSelect(d, value, onChange, noName = false) {
  const known = new Set();
  const groups = (d.groups || []).map((g) => el("optgroup", { label: g.label }, ...(d.people[g.key] || []).map((p) => {
    known.add(p.name);
    return el("option", { value: p.name, text: `${p.name}${p.nickname && !p.name.startsWith(p.nickname) ? ` (${p.nickname})` : ""}${p.truck ? ` · รถ ${p.truck}` : ""}`, selected: p.name === value });
  })));
  const extra = value && !known.has(value) ? el("option", { value, text: `${value} (ไม่อยู่ในรายชื่อ)`, selected: true }) : null;
  return el("select", { onchange: (e) => onChange(e.target.value || null) }, el("option", { value: "", text: "— เลือก —", selected: !value && !noName }),
    el("option", { value: "none", text: "ไม่มีชื่อผู้เบิก", selected: !!noName }), extra, ...groups);
}

async function cfAddLine() {
  const d = cf_.data;
  const day = prompt("วันที่ (วว/ดด/ปปปป)", "");
  if (!day) return;
  const iso = typeof parseDMY === "function" ? parseDMY(day) : null;
  if (!iso) { alert("วันที่ไม่ถูกต้อง"); return; }
  const truck = prompt("รถเบอร์ (หรือทะเบียน)", "");
  if (truck == null) return;
  const v = d.vehicles.find((x) => String(x.truck_number) === truck.trim() && x.branch === "สระบุรี") || d.vehicles.find((x) => (x.plate || "").replace(/[\s-]/g, "") === truck.replace(/[\s-]/g, ""));
  const amount = prompt(`ยอด (บาท)${v ? ` · ${cfVehicleLabel(v)}` : " · ไม่พบรถนี้"}`, "1000");
  if (!amount) return;
  try { await api("/api/cash-fuel/lines", jsonOpts("POST", { day: iso, vehicle_id: v ? v.id : null, amount })); } catch (err) { alert(err.message); return; }
  openCashFuel(cf_.month);
}
