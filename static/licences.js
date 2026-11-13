"use strict";

// ใบอนุญาต · เอกสาร (#licences), owner, 12 Nov 2026: the company's licences and certificates and the drivers' licences, each with its
// scans. The tiles count what has expired / falls due / has no file; ต้องทำตอนนี้ lists those first; then เอกสารบริษัท and ใบขับขี่.
// A new copy is uploaded on its row: the server keeps it (the old one as history), reads its dates, and the form below opens with them
// to confirm. The dates also reach the owner's Google Calendar through a private calendar link (server: app/licences.py).

const lc_ = { data: null, edit: null, busy: false };
const LC_PAD = (n) => String(n).padStart(2, "0");
const lcDate = (iso) => { if (!iso) return ""; const [y, m, d] = iso.slice(0, 10).split("-"); return `${d}/${m}/${y}`; };

function lcLeft(r) {
  if (r.state === "never") return ["ไม่มีวันหมดอายุ", "gr"];
  if (r.state === "nodate") return ["ยังไม่ใส่วันที่", "gr"];
  if (r.state === "submitted") return ["ยื่นแล้ว รอใบใหม่", "info"];
  const d = r.days;
  const txt = d < 0 ? `หมดแล้ว ${-d} วัน` : d === 0 ? "หมดวันนี้" : d <= 60 ? `อีก ${d} วัน` : d < 365 ? `อีก ${Math.round(d / 30)} เดือน` : `อีก ${(d / 365).toFixed(1)} ปี`;
  return [txt, r.state === "expired" ? "bad" : r.state === "soon" ? "warn" : "ok"];
}

async function openLicences() {
  showView("licences");
  showErr("lcError", "");
  if (!lc_.data) fill($("lcBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { lc_.data = await api("/api/licences"); }
  catch (err) { fill($("lcBody"), null); showErr("lcError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  lcRender();
}

function lcRender() {
  const d = lc_.data, t = d.tiles;
  const tile = (label, n, tone) => el("div", { class: `lc-tile ${tone || ""}` }, el("div", { class: "small muted", text: label }), el("b", { text: String(n) }));
  const tiles = el("div", { class: "lc-tiles" }, tile("หมดแล้ว", t.expired, t.expired ? "bad" : ""), tile("ใกล้หมด", t.soon, t.soon ? "warn" : ""),
    tile("ยังไม่มีไฟล์", t.nofile), tile("ใช้ได้", t.ok));
  const bar = el("div", { class: "lc-bar" }, el("span", { class: "grow" }),
    el("button", { class: "btn", type: "button", text: "ปฏิทิน Google", onclick: lcCalendar }),
    el("button", { class: "btn primary", type: "button", text: "+ เพิ่มเอกสาร", onclick: () => lcOpenEdit(null) }));
  const parts = [bar, tiles, el("div", { id: "lcEdit" }), el("div", { id: "lcCal" })];
  parts.push(el("h3", { class: "lc-h", text: "ต้องทำตอนนี้" }),
    d.act.length ? lcTable(d.act, true) : el("p", { class: "muted small", text: "ไม่มีเอกสารที่ต้องต่อหรือขอใหม่ในช่วงนี้" }));
  d.sections.forEach((s) => {
    parts.push(el("h3", { class: "lc-h", text: `${s.label} (${s.count})` }),
      s.rows.length ? lcTable(s.rows, false) : el("p", { class: "muted small", text: "ทั้งหมดอยู่ใน ต้องทำตอนนี้ ด้านบน" }));
  });
  fill($("lcBody"), ...parts);
  if (lc_.edit) lcDrawEdit();
}

function lcName(r) {
  if (r.kind === "driving") return [`ใบขับขี่ · ${r.person || "?"}${r.nickname ? ` (${r.nickname})` : ""}`, r.truck ? `รถ ${r.truck}` : ""];
  return [r.title, [r.place ? `${r.place}${r.place_note ? ` (${r.place_note})` : ""}` : "", r.doc_no ? `เลขที่ ${r.doc_no}` : "", r.kind_note].filter(Boolean).join(" · ")];
}

function lcTable(rows, act) {
  return el("div", { class: "lc-wrap" }, el("table", { class: "lc-table" },
    el("tbody", {}, ...rows.map((r) => {
      const [name, sub] = lcName(r);
      const [left, tone] = lcLeft(r);
      const input = el("input", { type: "file", accept: "image/*,application/pdf", hidden: true, onchange: (e) => e.target.files[0] && lcUpload(r, e.target.files[0]) });
      return el("tr", {},
        el("td", {}, el("div", { class: "lc-name" }, r.kind === "driving" && r.employee_id
          ? el("a", { href: `#employee=${r.employee_id}`, text: name, title: "เปิดประวัติพนักงาน" }) : el("span", { text: name })),
          sub ? el("div", { class: "small muted", text: sub }) : null,
          r.old_files.length ? el("div", { class: "small muted" }, el("span", { text: "ฉบับเก่า: " }),
            ...r.old_files.map((f, i) => el("button", { class: "linkish small", type: "button", text: `${i ? " · " : ""}${lcDate(f.uploaded_at)}`, title: f.name, onclick: () => lcView(f.id) }))) : null,
          el("div", { class: "lc-acts small" },
          r.file ? el("button", { class: "linkish", type: "button", text: "ดูไฟล์", onclick: () => lcView(r.file.id) }) : el("span", { class: "muted", text: "ยังไม่มีไฟล์" }),
          r.file ? el("button", { class: "linkish", type: "button", text: "ดาวน์โหลด", onclick: () => lcView(r.file.id, true) }) : null,
          input, el("button", { class: "linkish", type: "button", text: r.file ? "อัปโหลดใบใหม่" : "อัปโหลด", onclick: () => input.click() }),
          act && r.state !== "nodate" && r.state !== "never" ? el("button", { class: "linkish", type: "button", text: r.submitted_on ? "ยังไม่ได้ยื่น" : "ยื่นแล้ว รอใบใหม่",
            onclick: () => lcSave(r.id, { submitted_on: r.submitted_on ? null : new Date().toISOString().slice(0, 10) }) }) : null,
          el("button", { class: "linkish", type: "button", text: "แก้ไข", onclick: () => lcOpenEdit(r) }))),
        el("td", { class: "lc-date", text: r.expires_on ? lcDate(r.expires_on) : "—" }),
        el("td", { class: "lc-state" }, el("span", { class: `lc-badge ${tone}`, text: left })));
    }))));
}

async function lcView(fileId, download = false) {
  const w = download ? null : window.open("", "_blank");
  if (w) w.document.write("<p style='font-family:sans-serif;padding:16px'>กำลังเปิด…</p>");
  try {
    const res = await fetch(`/api/licences/files/${fileId}${download ? "?download=1" : ""}`, { headers: { "X-App-Token": state.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    if (w) { w.location.href = url; return; }
    const m = /filename\*=UTF-8''([^;]+)/.exec(res.headers.get("Content-Disposition") || "");
    const a = el("a", { href: url, download: m ? decodeURIComponent(m[1]) : "file" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (err) { if (w) w.close(); alert(err.message); }
}

async function lcSave(id, body) {
  try { await api(`/api/licences/${id}`, jsonOpts("PATCH", body)); await openLicences(); }
  catch (err) { alert(err.message); }
}

async function lcUpload(r, file) {
  lc_.edit = { row: r, reading: true, file: file.name };
  lcDrawEdit();
  const fd = new FormData();
  fd.append("file", file);
  let res;
  try { res = await api(`/api/licences/${r.id}/files`, { method: "POST", body: fd }); }
  catch (err) { lc_.edit = null; lcDrawEdit(); alert(`อัปโหลดไม่สำเร็จ: ${err.message}`); return; }
  const s = res.suggest || {};
  lc_.edit = { row: r, uploaded: true, readError: res.read && res.read.error, values: {
    issued_on: s.issued_on || "", expires_on: s.expires_on || r.expires_on || "", doc_no: s.doc_no || r.doc_no || "" } };
  try { lc_.data = await api("/api/licences"); } catch (_) { /* the form still works */ }
  lcRender();
}

function lcOpenEdit(r) {
  lc_.edit = { row: r, values: r ? { kind: r.kind, title: r.title, place: r.place || "", employee_id: r.employee_id || "", doc_no: r.doc_no || "",
    issued_on: r.issued_on || "", expires_on: r.expires_on || "", warn_days: r.warn_days == null ? "" : r.warn_days, note: r.note || "" }
    : { kind: "other", title: "", place: "สำนักงานใหญ่", employee_id: "", doc_no: "", issued_on: "", expires_on: "", warn_days: "", note: "" } };
  lcDrawEdit();
}

function lcDrawEdit() {
  const box = $("lcEdit");
  if (!box) return;
  const e = lc_.edit;
  if (!e) { fill(box, null); return; }
  if (e.reading) { fill(box, el("section", { class: "card lc-form" }, el("p", { text: `กำลังเก็บไฟล์และอ่านวันที่จาก ${e.file}… (ประมาณ 10–30 วินาที)` }))); box.scrollIntoView({ block: "nearest" }); return; }
  const d = lc_.data, v = e.values, r = e.row;
  const input = (key, attrs = {}) => el("input", { type: "text", value: v[key] == null ? "" : v[key], oninput: (ev) => { v[key] = ev.target.value; }, onchange: (ev) => { v[key] = ev.target.value; }, ...attrs });
  const field = (label, ctl) => el("label", { class: "lc-field" }, el("span", { class: "small muted", text: label }), ctl);
  const kind = el("select", { onchange: (ev) => { v.kind = ev.target.value; lcDrawEdit(); } },
    ...d.kinds.map((k) => el("option", { value: k.key, text: k.label, selected: k.key === v.kind })));
  const isDriver = (r ? r.kind : v.kind) === "driving";
  const fields = [];
  if (!r) fields.push(field("ประเภท", kind));
  if (isDriver) {
    if (!r) fields.push(field("คนขับ", el("select", { onchange: (ev) => { v.employee_id = ev.target.value; } }, el("option", { value: "", text: "— เลือก —" }),
      ...d.drivers.map((p) => el("option", { value: p.id, text: `${p.name}${p.truck ? ` · รถ ${p.truck}` : ""}`, selected: p.id === v.employee_id })))));
  } else {
    fields.push(field("ชื่อเอกสาร", input("title", { placeholder: (d.kinds.find((k) => k.key === v.kind) || {}).label || "" })),
      field("สาขา", el("select", { onchange: (ev) => { v.place = ev.target.value; } }, el("option", { value: "", text: "— ไม่ระบุ —" }),
        ...d.places.map((p) => el("option", { value: p, text: p, selected: p === v.place })))));
  }
  fields.push(field("เลขที่", input("doc_no")), field("วันที่ออก", input("issued_on", { type: "date" })),
    field("วันหมดอายุ / ครบกำหนด", input("expires_on", { type: "date" })),
    field("เตือนก่อน (วัน)", input("warn_days", { type: "number", min: "0", placeholder: "ตามประเภท" })), field("หมายเหตุ", input("note")));
  const title = r ? (e.uploaded ? `ไฟล์ใหม่ของ ${lcName(r)[0]} — ตรวจวันที่แล้วกดบันทึก` : `แก้ไข ${lcName(r)[0]}`) : "เพิ่มเอกสาร";
  fill(box, el("section", { class: "card lc-form" }, el("h3", { text: title }),
    e.readError ? el("p", { class: "warn-text small", text: e.readError }) : e.uploaded ? el("p", { class: "muted small", text: "วันที่ด้านล่างอ่านจากไฟล์ — แก้ได้ถ้าไม่ตรง" }) : null,
    el("div", { class: "lc-grid" }, ...fields),
    el("div", { class: "lc-formbar" },
      r && !e.uploaded ? el("button", { class: "btn danger", type: "button", text: "เอาออกจากรายการ", onclick: lcRemove }) : null,
      el("span", { class: "grow" }),
      el("button", { class: "btn", type: "button", text: "ยกเลิก", onclick: () => { lc_.edit = null; lcDrawEdit(); } }),
      el("button", { class: "btn primary", type: "button", text: "บันทึก", onclick: lcSubmit }))));
  box.scrollIntoView({ block: "nearest" });
}

async function lcSubmit() {
  const e = lc_.edit, v = { ...e.values };
  if (!e.row && v.kind === "driving" && !v.employee_id) { alert("เลือกคนขับก่อน"); return; }
  if (e.row) { delete v.kind; delete v.employee_id; }
  if (e.uploaded) v.submitted_on = null;                       // the new paper is here: the reminders start again from its date
  try {
    if (e.row) await api(`/api/licences/${e.row.id}`, jsonOpts("PATCH", v));
    else await api("/api/licences", jsonOpts("POST", v));
    lc_.edit = null;
    await openLicences();
  } catch (err) { alert(err.message); }
}

async function lcRemove() {
  const r = lc_.edit.row;
  if (!window.confirm(`เอา “${lcName(r)[0]}” ออกจากรายการ? (ไฟล์ที่อัปโหลดไว้ยังเก็บอยู่)`)) return;
  try { await api(`/api/licences/${r.id}`, { method: "DELETE" }); lc_.edit = null; await openLicences(); }
  catch (err) { alert(err.message); }
}

function lcCalendar() {
  const box = $("lcCal");
  if (box.childNodes.length) { fill(box, null); return; }
  const url = `${location.origin}${lc_.data.feed_path}`;
  const inp = el("input", { type: "text", value: url, readonly: true, class: "lc-url", onclick: (e) => e.target.select() });
  fill(box, el("section", { class: "card lc-form" }, el("h3", { text: "ส่งวันที่เข้า Google Calendar" }),
    el("p", { class: "small", text: "ทำครั้งเดียว: เปิด Google Calendar บนคอมพิวเตอร์ › ปฏิทินอื่นๆ (+) › จาก URL › วางลิงก์นี้ › เพิ่มปฏิทิน" }),
    el("div", { class: "lc-urlrow" }, inp, el("button", { class: "btn", type: "button", text: "คัดลอก",
      onclick: async () => { try { await navigator.clipboard.writeText(url); } catch (_) { inp.select(); document.execCommand("copy"); } } })),
    el("p", { class: "small muted", text: "ทุกวันหมดอายุ / วันครบกำหนดจะขึ้นในปฏิทินชื่อ “IceBox ใบอนุญาต · เอกสาร” พร้อมวันเตือนล่วงหน้า · Google อัปเดตเองทุกครึ่งวันถึงหนึ่งวัน · ลิงก์นี้เป็นความลับ อย่าส่งต่อ" })));
}

// the driver's own record: their licence, linked both ways (owner, 12 Nov 2026)
async function lcEmployeeBox(person) {
  const box = document.getElementById("empLicBox");
  if (!box) return;
  let d;
  try { d = await api(`/api/licences/employee/${person.id}`); } catch (_) { box.closest("section").remove(); return; }
  if (!d.docs.length) { fill(box, el("p", { class: "muted small" }, el("span", { text: "ยังไม่มีในทะเบียน · " }), el("a", { href: "#licences", text: "ใบอนุญาต · เอกสาร" }))); return; }
  fill(box, ...d.docs.map((x) => {
    const [left, tone] = lcLeft(x);
    return el("div", { class: "lc-emprow" }, el("span", { text: x.title }), el("span", { text: x.expires_on ? lcDate(x.expires_on) : "—" }),
      el("span", { class: `lc-badge ${tone}`, text: left }));
  }), el("p", { class: "small" }, el("a", { href: "#licences", text: "เปิดในหน้า ใบอนุญาต · เอกสาร →" })));
}
