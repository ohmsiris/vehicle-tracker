"use strict";

// เอกสารพนักงาน: หนังสือเตือน and บันทึกข้อความ, tracked per employee.
//   #documents       every paper (draft / issued / signed / refused / cancelled), the owner's signature picture, a new-paper dialog
//   #document=<id>   one paper: the form on the left (a draft) or its status (issued), the A4 paper on the right, drawn live, printed from the browser
// The paper is Angsana New 16 pt, the title 22 pt, the letterhead (company, address, phone) 16 pt. The owner's signature is printed only when
// "ใส่ลายเซ็น" is ticked. An issued paper is drawn from the snapshot frozen at issue (so it never changes). Data: /api/documents (app/documents.py).
// Loaded BEFORE app.js: nothing here runs at load time; helpers from app.js / board.js / employees.js are used at call time.

const dc = { all: [], opts: null, ready: true, hasSig: false, sig: null, cur: null, loans: [], draft: null };

const THAI_MONTHS_FULL = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
const BLANK_NAME = "(" + " ".repeat(36) + ")";             // an empty name in brackets that survives HTML whitespace
const DC_STATUS_CLASS = { draft: "", issued: "warn", signed: "ok", refused: "urgent", cancelled: "" };
const dcBe = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ""); return m ? { d: +m[3], m: THAI_MONTHS_FULL[+m[2] - 1], y: +m[1] + 543 } : null; };
const dcLong = (iso) => { const b = dcBe(iso); return b ? `${b.d} ${b.m} ${b.y}` : String(iso || ""); };
const dcStatusBadge = (c) => el("span", { class: `badge ${DC_STATUS_CLASS[c.status] || ""}`.trim(), text: c.status_label });

const DC_PRESETS = {
  general: { label: "ทั่วไป (เขียนเอง)", subject: "", body: "", ack: false },
  deduct: {
    label: "ขออนุมัติหักเงินเดือน (ผ่อนชำระ)", ack: true, subject: "ขออนุมัติหักชำระค่าใช้จ่าย {ชื่อ} ({รหัส})",
    body: "ตามที่ {ชื่อ} รหัสพนักงาน {รหัส} ตำแหน่ง{ตำแหน่ง} … (เล่าเหตุการณ์ / ที่มาของยอดเงิน) …\n\nจึงขอให้ดำเนินการยอดค่าใช้จ่ายจำนวน {ยอดเงิน} บาท เป็นเงินกู้ยืมของ {ชื่อ} กับบริษัท และหักชำระจากค่าจ้างเป็นรายเดือน เริ่มตั้งแต่เดือน {เริ่มหัก} ดังนี้\n{ตารางหัก}\n\nจึงเรียนมาเพื่อโปรดทราบและดำเนินการ",
  },
  raise: {
    label: "ขอปรับอัตราค่าจ้าง", ack: false, subject: "ขอปรับอัตราค่าจ้าง {ชื่อ} ({รหัส})",
    body: "บริษัท สระบุรีวารีเทพ จำกัด ขอเสนอพิจารณาปรับอัตราค่าจ้างพนักงาน ตำแหน่ง{ตำแหน่ง} จำนวน 1 ราย ดังรายละเอียดต่อไปนี้\n\nรายชื่อพนักงาน\n1. {ชื่อ} ({รหัส}) จากอัตรา … บาท/วัน เป็น … บาท/วัน\n\nเหตุผลประกอบการพิจารณา\n1. …\n\nทั้งนี้ขอให้มีผลการปรับอัตราค่าจ้างตั้งแต่วันที่ … เป็นต้นไป",
  },
};

// ------------------------------------------------------------------ loading
async function dcLoad() {
  try {
    const [opts, d] = await Promise.all([dc.opts ? Promise.resolve(dc.opts) : api("/api/documents/options"), api("/api/documents")]);
    dc.opts = opts;
    Object.assign(dc, { all: d.documents, ready: d.ready, hasSig: d.has_signature });
    $("hrLockBox").hidden = true;
    return true;
  } catch (err) {
    if (err.status === 423) { showHrLock(); return false; }
    showErr("docError", err.message);
    return false;
  }
}

async function dcSignature() {
  if (!dc.hasSig) return null;
  if (!dc.sig) { try { dc.sig = (await api("/api/documents/signature")).data_uri; } catch (e) { dc.sig = null; } }
  return dc.sig;
}

// ------------------------------------------------------------------ the list
async function openDocuments() {
  showView("documents");
  showErr("docError", "");
  if (!(await dcLoad())) return;
  $("docNeedsV24").hidden = dc.ready;
  renderSignatureBox();
  renderDocuments();
}

function renderSignatureBox() {
  const box = $("docSigBox");
  box.replaceChildren(
    el("span", { class: dc.hasSig ? "badge ok" : "badge warn", text: dc.hasSig ? "ลายเซ็น: ตั้งค่าแล้ว" : "ลายเซ็น: ยังไม่ได้ตั้งค่า" }),
    el("label", { class: "btn small", for: "docSigFile", text: dc.hasSig ? "เปลี่ยนรูปลายเซ็น" : "อัปโหลดรูปลายเซ็น" }),
    dc.hasSig ? el("button", { class: "btn small danger", type: "button", text: "ลบ", onclick: removeSignature }) : null,
    el("span", { class: "muted small", text: "ใช้รูปที่เซ็นบนกระดาษขาว (jpg / png) — ระบบทำพื้นหลังให้โปร่งใสเอง และพิมพ์ลายเซ็นเมื่อติ๊ก “ใส่ลายเซ็น” เท่านั้น" }));
}

// a photo / scan of the signature -> a PNG with a clear background (white becomes see-through), not wider than 700 px
async function signatureFromFile(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error("เปิดรูปนี้ไม่ได้")); i.src = url; });
    const scale = Math.min(1, 700 / img.width);
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(img.width * scale));
    c.height = Math.max(1, Math.round(img.height * scale));
    const g = c.getContext("2d");
    g.drawImage(img, 0, 0, c.width, c.height);
    const data = g.getImageData(0, 0, c.width, c.height), px = data.data;
    let minX = c.width, minY = c.height, maxX = 0, maxY = 0;
    for (let i = 0; i < px.length; i += 4) {
      const lo = Math.min(px[i], px[i + 1], px[i + 2]);
      px[i + 3] = Math.max(0, Math.min(255, (240 - lo) * 8));        // near-white -> clear, ink -> solid
      if (px[i + 3] > 40) { const p = i / 4, x = p % c.width, y = (p - x) / c.width; minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
    }
    if (maxX <= minX) throw new Error("ไม่พบลายเซ็นในรูป — ใช้รูปที่เซ็นบนกระดาษขาว");
    g.putImageData(data, 0, 0);
    const out = document.createElement("canvas");
    out.width = maxX - minX + 1;
    out.height = maxY - minY + 1;
    out.getContext("2d").drawImage(c, minX, minY, out.width, out.height, 0, 0, out.width, out.height);
    return out.toDataURL("image/png");
  } finally { URL.revokeObjectURL(url); }
}

async function uploadSignature(file) {
  if (!file) return;
  try {
    const uri = await signatureFromFile(file);
    await api("/api/documents/signature", jsonOpts("PUT", { data_uri: uri }));
    dc.hasSig = true;
    dc.sig = uri;
    renderSignatureBox();
  } catch (err) { showErr("docError", err.message); }
  $("docSigFile").value = "";
}

async function removeSignature() {
  if (!confirm("ลบรูปลายเซ็นออกจากระบบ?")) return;
  try {
    await api("/api/documents/signature", { method: "DELETE" });
    dc.hasSig = false;
    dc.sig = null;
    renderSignatureBox();
  } catch (err) { showErr("docError", err.message); }
}

function visibleDocuments() {
  const tokens = $("dcSearch").value.trim().split(/\s+/).map(empNorm).filter(Boolean);
  const type = $("dcType").value, status = $("dcStatus").value;
  $("dcClear").hidden = !(tokens.length || type || status !== "");
  return dc.all.filter((d) => !type || d.doc_type === type).filter((d) => !status || d.status === status)
    .filter((d) => !tokens.length || tokens.every((t) => empNorm([d.employee, d.code, d.number, d.subject, d.type_label].join("|")).includes(t)));
}

function renderDocuments() {
  const rows = visibleDocuments();
  const head = el("tr", {}, ...["เลขที่", "ประเภท", "พนักงาน", "เรื่อง", "วันที่", "สถานะ"].map((t) => el("th", { text: t })));
  const body = rows.map((d) => el("tr", { class: `emprow${d.status === "cancelled" ? " off" : ""}`, tabindex: "0", onclick: () => goHash(`#document=${d.id}`) },
    el("td", { class: "emcode", text: d.number || "—" }), el("td", { text: `${d.type_label}${d.doc_type === "warning" && d.warning_level != null ? ` · ${dcLevelShort(d.warning_level)}` : ""}` }),
    el("td", {}, el("div", { class: "emname", text: d.employee }), el("div", { class: "muted small", text: d.code })), el("td", { class: "small", text: d.subject }),
    el("td", { text: d.date_text }), el("td", {}, dcStatusBadge(d))));
  fill($("dcCount"), `พบ ${rows.length} ฉบับ`);
  $("dcList").replaceChildren(rows.length ? el("div", { class: "tablewrap" }, el("table", { class: "emptable lntable" }, el("thead", {}, head), el("tbody", {}, ...body)))
    : el("p", { class: "muted empty", text: dc.all.length ? "ไม่พบเอกสารที่ตรงกับตัวกรอง" : "ยังไม่มีเอกสาร — กด “+ ใบเตือน” หรือ “+ บันทึกข้อความ”" }));
}

const dcLevelShort = (n) => ({ 0: "ว่ากล่าวด้วยวาจา", 1: "ครั้งที่ 1", 2: "ครั้งที่ 2", 3: "ครั้งสุดท้าย" }[n] || "");

function clearDocFilters() {
  $("dcSearch").value = "";
  $("dcType").value = "";
  $("dcStatus").value = "";
  renderDocuments();
}

// ------------------------------------------------------------------ a new paper: pick the person, a draft is made, the form opens
async function newDocument(docType, employeeId) {
  if (!emp.loaded && !(await empLoad())) return;
  let person = employeeId ? emp.all.find((e) => e.id === employeeId) : null;
  if (!person) {
    const typed = prompt(`${dc.opts ? dc.opts.types[docType] : ""} ให้ใคร? พิมพ์รหัสพนักงาน (เช่น SB-FR001) หรือชื่อ`);
    if (!typed) return;
    const t = empNorm(typed);
    const hits = emp.all.filter((e) => e.status !== "resigned" && (empNorm(e.code) === t || empNorm(e.display_name).includes(t) || empNorm(e.full_name).includes(t)));
    if (hits.length !== 1) { showErr("docError", hits.length ? `พบ ${hits.length} คน — พิมพ์รหัสพนักงานให้ชัดขึ้น` : "ไม่พบพนักงานคนนี้ (ที่ยังทำงานอยู่)"); return; }
    person = hits[0];
  }
  try {
    const d = await api("/api/documents", jsonOpts("POST", { doc_type: docType, employee_id: person.id }));
    goHash(`#document=${d.id}`);
  } catch (err) { showErr("docError", err.message); showErr("empDetailError", err.message); }
}

// ------------------------------------------------------------------ one paper
async function openDocument(id) {
  showView("document");
  showErr("docDetailError", "");
  if (!id) { goHash("#documents"); return; }
  if (!dc.opts) await dcLoad();
  try {
    dc.cur = await api(`/api/documents/${id}`);
    dc.hasSig = dc.hasSig || dc.cur.with_signature;
    if (!dc.sig) { try { const s = await api("/api/documents/signature"); dc.sig = s.data_uri; dc.hasSig = !!s.data_uri; } catch (e) { /* no signature set yet */ } }
    dc.loans = dc.cur.doc_type === "memo" ? (await api(`/api/loans?employee_id=${dc.cur.employee_id}`).catch(() => ({ loans: [] }))).loans : [];
  } catch (err) {
    if (err.status === 423) { showHrLock(); return; }
    showErr("docDetailError", err.message);
    $("docForm").replaceChildren();
    $("docPaperBox").replaceChildren();
    return;
  }
  dc.draft = dc.cur.status === "draft" ? dcDraftOf(dc.cur) : null;
  renderDocument();
}

const dcDraftOf = (c) => ({ doc_date: c.doc_date, subject: c.subject, warning_level: c.warning_level, with_signature: c.with_signature, loan_id: c.loan_id || "", content: { ...c.content } });

function dcCurrent() {                                      // what the paper shows now: the form while drafting, else what was issued
  const c = dc.cur;
  return dc.draft ? { ...c, doc_date: dc.draft.doc_date, subject: dc.draft.subject, warning_level: dc.draft.warning_level, with_signature: dc.draft.with_signature, content: dc.draft.content, loan_id: dc.draft.loan_id } : c;
}

function renderDocument() {
  const c = dc.cur;
  const btn = (text, onclick, cls = "") => el("button", { class: `btn${cls ? ` ${cls}` : ""}`, type: "button", text, onclick });
  fill($("docHead"),
    el("div", { class: "tk-title" }, el("span", { class: "tk-plate", text: `${c.type_label}${c.number ? ` ${c.number}` : ""}` }), el("span", { class: "muted", text: c.employee })),
    el("div", { class: "tk-badges" }, dcStatusBadge(c), c.frozen ? el("span", { class: "muted small", text: "ออกเอกสารแล้ว — แก้ไม่ได้" }) : null));
  const actions = [];
  if (c.status === "draft") actions.push(btn("ลบร่าง", deleteDraft, "danger"), btn("บันทึกร่าง", () => saveDraft(false)), btn("ออกเอกสาร (ใส่เลขที่ + ล็อก)", issueDocument, "primary"));
  else {
    if (c.status !== "cancelled") actions.push(btn("ยกเลิกเอกสาร", cancelDocument, "danger"));
    actions.push(btn("สร้างฉบับใหม่จากฉบับนี้", duplicateDocument));
  }
  fill($("docActions"), ...actions);
  renderDocForm();
  renderDocPaper();
}

function renderDocForm() {
  const c = dc.cur, d = dc.draft, box = $("docForm");
  const parts = [];
  // ---- print options (always)
  const sig = el("input", { type: "checkbox", id: "dcSig", checked: dcCurrent().with_signature, onchange: onSigToggle });
  const printBar = el("div", { class: "card doc-toolbar" },
    el("label", { class: "check" }, sig, " ใส่ลายเซ็น (ผู้ลงนามของบริษัท)"), dc.hasSig ? null : el("span", { class: "small", style: "color:var(--warn)", text: "ยังไม่ได้อัปโหลดรูปลายเซ็น (หน้ารายการเอกสาร)" }),
    c.doc_type === "warning" ? el("label", { class: "check" }, el("input", { type: "checkbox", id: "dcWit", checked: !!dcCurrent().content.show_witnesses, onchange: onWitnessToggle }), " พิมพ์ช่องพยาน (กรณีไม่ยอมเซ็น)") : null,
    el("button", { class: "btn primary", type: "button", text: "พิมพ์ / บันทึกเป็น PDF", onclick: printDocument }));
  parts.push(printBar);
  if (d) parts.push(editorCard());
  else parts.push(statusCard());
  if (c.doc_type === "warning" && c.history.length) parts.push(el("section", { class: "card" }, el("h3", { text: "ใบเตือนก่อนหน้าของคนนี้" }),
    el("ul", { class: "hlist" }, ...c.history.map((h) => el("li", { class: "hrow", style: "cursor:pointer", onclick: () => goHash(`#document=${h.id}`) },
      el("span", { class: "hrow-main", text: `${h.number || "ร่าง"} · ${h.date_text}${h.level != null ? ` · ${dcLevelShort(h.level)}` : ""}` }), el("span", { class: "hrow-side muted small", text: (dc.opts.statuses[h.status] || "") }))))));
  box.replaceChildren(...parts);
}

function dcField(label, node, hint) { return el("div", { class: "dcfield" }, el("label", { text: label }), node, hint ? el("div", { class: "muted small", text: hint }) : null); }

function editorCard() {
  const c = dc.cur, d = dc.draft;
  const input = (key, opts = {}) => {
    const i = el("input", { type: opts.type || "text", value: (opts.content ? d.content[key] : d[key]) ?? "", maxlength: opts.max || "200", placeholder: opts.ph || "" });
    if (opts.type === "date") i.max = todayISO();
    i.addEventListener("input", () => { (opts.content ? d.content : d)[key] = i.value; renderDocPaper(); });
    return i;
  };
  const area = (key, rows, ph) => {
    const t = el("textarea", { rows: String(rows), placeholder: ph || "", maxlength: "6000" });
    t.value = d.content[key] || "";
    t.addEventListener("input", () => { d.content[key] = t.value; renderDocPaper(); });
    return t;
  };
  const chips = (list, target, joiner) => el("div", { class: "dcchips" }, ...list.map((x) => el("button", { class: "chipbtn", type: "button", text: x, onclick: () => {
    d.content[target] = d.content[target] ? `${d.content[target]}${joiner}${x}` : x;
    renderDocForm();
    renderDocPaper();
  } })));
  const date = input("doc_date", { type: "date" });
  const dateCap = el("div", { class: "datecap" });
  bindDateField(date, dateCap);
  date.addEventListener("change", () => { d.doc_date = date.value; renderDocPaper(); });
  const fields = [dcField("วันที่ในเอกสาร", el("div", {}, date, dateCap))];
  if (c.doc_type === "warning") {
    const level = el("select", {}, ...Object.entries(dc.opts.levels).map(([k, v]) => el("option", { value: k, text: v })));
    level.value = String(d.warning_level ?? 1);
    level.addEventListener("change", () => { d.warning_level = Number(level.value); renderDocPaper(); });
    const suggested = `แนะนำ: ${dc.opts.levels[String(c.suggest_level)]}${c.history.length ? ` (เคยเตือนมาแล้ว ${c.history.filter((h) => h.status !== "cancelled" && h.status !== "draft").length} ฉบับ)` : ""}`;
    fields.push(dcField("ระดับการเตือน", level, suggested), dcField("เรื่อง", input("subject", { max: "200" })),
      dcField("วันที่กระทำผิด", input("offence_date", { content: true, max: "60", ph: "เช่น 1/10/2569 หรือ 20-21 ตุลาคม 2569" })),
      dcField("ความผิดที่กระทำ", el("div", {}, area("offence", 3, "เช่น ไม่ส่งซีทีสายหลายครั้ง"), chips(dc.opts.offences, "offence", " / "))),
      dcField("การลงโทษ", el("div", {}, area("punishment", 2, "เช่น หักเงินวันที่ 1/10/2569 1,000 บาท"), chips(["ตักเตือนเป็นลายลักษณ์อักษร", "หักเงิน … บาท", "พักงาน … วัน"], "punishment", " · "))),
      dcField("ยอดเงินที่หัก (บาท) — ถ้ามี", input("penalty_amount", { content: true, max: "12", ph: "เก็บไว้ใช้ตรวจสลิปเงินเดือนภายหลัง" })));
  } else {
    const preset = el("select", {}, ...Object.entries(DC_PRESETS).map(([k, v]) => el("option", { value: k, text: v.label })));
    preset.value = d.content.preset || "general";
    preset.addEventListener("change", () => {
      const p = DC_PRESETS[preset.value];
      if ((d.content.body || d.subject) && !confirm("เปลี่ยนแบบ? หัวข้อและเนื้อหาที่พิมพ์ไว้จะถูกแทนที่")) { preset.value = d.content.preset || "general"; return; }
      d.content.preset = preset.value;
      d.subject = p.subject;
      d.content.body = p.body;
      d.content.show_ack = p.ack;
      renderDocForm();
      renderDocPaper();
    });
    const loanSel = el("select", {}, el("option", { value: "", text: "— ไม่แนบ —" }), ...dc.loans.filter((l) => l.status !== "cancelled").map((l) => el("option", { value: l.id, text: `${l.kind_label} ${lnMoney(l.amount)} บาท · ${beDate(l.loan_date)} · ${l.plan}` })));
    loanSel.value = d.loan_id || "";
    loanSel.addEventListener("change", () => { d.loan_id = loanSel.value; saveDraft(true); });
    const ack = el("input", { type: "checkbox", checked: !!d.content.show_ack });
    ack.addEventListener("change", () => { d.content.show_ack = ack.checked; renderDocPaper(); });
    fields.push(dcField("แบบสำเร็จรูป", preset), dcField("เรียน", input("to", { content: true, max: "100" })), dcField("เรื่อง", input("subject", { max: "200" })),
      dcField("เนื้อหา", el("div", {}, area("body", 12, "พิมพ์เนื้อหา — เว้นบรรทัดเพื่อขึ้นย่อหน้าใหม่"),
        el("div", { class: "muted small", text: "ใส่ได้: " }), el("div", { class: "dcchips" }, ...["{ชื่อ}", "{รหัส}", "{ตำแหน่ง}", "{สาขา}", "{ยอดเงิน}", "{เริ่มหัก}", "{ตารางหัก}"].map((t) => el("button", { class: "chipbtn", type: "button", text: t, onclick: () => {
          d.content.body = `${d.content.body || ""}${t}`;
          renderDocForm();
          renderDocPaper();
        } }))))),
      dcField("แนบตารางหักจากเงินกู้", loanSel, "ยอดและตารางหักดึงจากหน้าเงินกู้ (ใช้ {ตารางหัก} กำหนดตำแหน่งตารางในเนื้อหา)"),
      el("label", { class: "check" }, ack, " พิมพ์ช่องให้พนักงานเซ็นรับทราบ / ยินยอม"));
  }
  return el("section", { class: "card dcform" }, el("h3", { text: "กรอกข้อมูล" }), ...fields, el("p", { class: "error", id: "dcError", hidden: true }));
}

function statusCard() {
  const c = dc.cur;
  const rows = [kvAll("สถานะ", "", dcStatusBadge(c)), kvAll("เลขที่", c.number), kvAll("วันที่ในเอกสาร", c.date_text), c.issued_at ? kvAll("ออกเมื่อ", dcLong(c.issued_at.slice(0, 10))) : null,
    c.acknowledged_date ? kvAll(c.status === "refused" ? "ปฏิเสธลงนามเมื่อ" : "เซ็นรับทราบเมื่อ", dcLong(c.acknowledged_date)) : null, c.witnesses ? kvAll("พยาน", c.witnesses) : null, kvAll("หมายเหตุ", c.notes)];
  const btn = (text, onclick, cls = "") => el("button", { class: `btn small${cls ? ` ${cls}` : ""}`, type: "button", text, onclick });
  const acts = c.status === "cancelled" ? [] : [btn("พนักงานเซ็นรับทราบแล้ว", () => acknowledge("signed"), "primary"), btn("ปฏิเสธลงนาม (ใส่พยาน)", () => acknowledge("refused")),
    c.status === "issued" ? null : btn("กลับเป็นรอเซ็น", () => acknowledge("issued"))].filter(Boolean);
  return el("section", { class: "card" }, el("h3", { text: "สถานะเอกสาร" }), ...rows.filter(Boolean),
    el("div", { class: "tk-link", style: "display:flex;gap:8px;flex-wrap:wrap" }, ...acts), c.status === "issued" ? el("p", { class: "muted small", text: "พิมพ์ → ให้พนักงานเซ็นบนกระดาษ → กดปุ่ม “พนักงานเซ็นรับทราบแล้ว” (หรือ “ปฏิเสธลงนาม” พร้อมชื่อพยาน)" }) : null);
}

// ------------------------------------------------------------------ the paper
function dcFill(text, snap) {                                   // {ชื่อ} {รหัส} ... in a memo's words
  const e = snap.employee, l = snap.loan;
  const map = { "{ชื่อ}": e.display_name, "{รหัส}": e.code, "{ตำแหน่ง}": e.position, "{สาขา}": e.branch, "{ยอดเงิน}": l ? lnMoney(l.amount) : "…", "{เริ่มหัก}": l && l.first_month ? dcLong(`${l.first_month}-01`).replace(/^1 /, "") : "…" };
  return Object.entries(map).reduce((s, [k, v]) => s.split(k).join(v), text || "");
}

function loanTable(l) {
  if (!l || !l.schedule.length) return null;
  let left = l.amount;
  return el("table", { class: "dp-table" }, el("thead", {}, el("tr", {}, ...["เดือนที่หัก", "ยอดหัก (บาท)", "คงเหลือ"].map((t) => el("th", { text: t })))),
    el("tbody", {}, ...l.schedule.map((r) => { left = Math.round((left - r.amount) * 100) / 100; return el("tr", {}, el("td", { text: `${THAI_MONTHS_FULL[+r.month.slice(5) - 1]} ${+r.month.slice(0, 4) + 543}` }), el("td", { class: "r", text: lnMoney(r.amount) }), el("td", { class: "r", text: lnMoney(Math.max(left, 0)) })); })));
}

function sigBlock(c, snap, withSig, lead) {
  return el("div", { class: "dp-sig" }, lead ? el("div", { text: lead }) : null,
    el("div", { class: "dp-sigline" }, el("span", { text: "ลงชื่อ" }), el("span", { class: "dp-signame" }, withSig && dc.sig ? el("img", { src: dc.sig, alt: "" }) : null)),
    el("div", { text: `(${snap.signer.name})` }), el("div", { text: snap.signer.title }));
}

function ackBlock(snap, lines) {
  return el("div", { class: "dp-sig" }, ...lines.map((t) => el("div", { text: t })), el("div", { class: "dp-sigline" }, el("span", { text: "ลงชื่อ" }), el("span", { class: "dp-signame" })),
    el("div", { text: `(${snap.employee.display_name})` }), el("div", { text: "พนักงาน" }));
}

function buildPaper(c) {
  const snap = c.render, head = snap.letterhead, b = dcBe(c.doc_date), co = c.content || {};
  const paper = el("div", { class: "docpaper", id: "docPaper" },
    el("div", { class: "dp-letterhead" }, el("div", { class: "dp-co", text: head.name }), el("div", { text: head.address }), el("div", { text: head.phone })), el("hr", { class: "dp-rule" }));
  if (c.doc_type === "warning") {
    const lv = c.warning_level;
    paper.append(el("div", { class: "dp-title", text: lv === 0 ? "บันทึกการตักเตือนด้วยวาจา" : "หนังสือเตือน" }),
      lv ? el("div", { class: "dp-center", text: lv === 3 ? "(เตือนครั้งสุดท้าย)" : `(เตือนเป็นลายลักษณ์อักษร ครั้งที่ ${lv})` }) : null,
      el("div", { class: "dp-right", text: b ? `วันที่ ${b.d} เดือน ${b.m} พ.ศ. ${b.y}` : "วันที่ ……" }),
      dpRow("เรื่อง", c.subject), dpRow("เรียน", snap.employee.display_name), dpRow("", `รหัสพนักงาน ${snap.employee.code}     ตำแหน่ง ${snap.employee.position}`),
      el("div", { class: "dp-gap" }), dpRow("วันที่กระทำผิด", co.offence_date), dpRow("ความผิด", co.offence), dpRow("การลงโทษ", co.punishment), el("div", { class: "dp-gap" }),
      el("div", { text: "จึงแจ้งมาเพื่อทราบ" }), el("div", { class: "dp-gap" }),
      sigBlock(c, snap, c.with_signature, `ในนามบริษัท สระบุรีวารีเทพ จำกัด`), el("div", { class: "dp-gap" }),
      ackBlock(snap, ["รับทราบความผิดและการลงโทษ", "โดยยอมรับว่ากระทำผิดจริง"]));
    if (co.show_witnesses) paper.append(el("div", { class: "dp-gap" }), el("div", { class: "dp-witness" }, el("div", { text: "กรณีพนักงานไม่ลงชื่อรับทราบ" }),
      el("div", { class: "dp-wrow" }, el("div", {}, el("div", { class: "dp-sigline" }, el("span", { text: "ลงชื่อ" }), el("span", { class: "dp-signame" })), el("div", { text: BLANK_NAME }), el("div", { text: "พยาน" })),
        el("div", {}, el("div", { class: "dp-sigline" }, el("span", { text: "ลงชื่อ" }), el("span", { class: "dp-signame" })), el("div", { text: BLANK_NAME }), el("div", { text: "พยาน" })))));
  } else {
    paper.append(el("div", { class: "dp-title ul", text: "บันทึกข้อความ" }),
      el("div", { class: "dp-meta" }, el("span", { text: c.number ? `เลขที่ ${c.number}` : "" }), el("span", { text: b ? `วันที่ ${b.d} ${b.m} ${b.y}` : "วันที่ ……" })),
      dpRow("เรื่อง", dcFill(c.subject, snap)), dpRow("เรียน", co.to || ""));
    const body = dcFill(co.body, snap), paras = body.split(/\n\s*\n/).filter((x) => x.trim());
    let tableDone = false;
    const table = loanTable(snap.loan);
    for (const p of paras) {
      if (p.trim() === "{ตารางหัก}") { tableDone = true; if (table) paper.append(table.cloneNode(true)); continue; }
      if (p.includes("{ตารางหัก}")) { const [a, z] = p.split("{ตารางหัก}"); tableDone = true; paper.append(el("p", { class: "dp-p", text: a.trim() })); if (table) paper.append(table.cloneNode(true)); if (z.trim()) paper.append(el("p", { class: "dp-p", text: z.trim() })); continue; }
      paper.append(el("p", { class: "dp-p", text: p.trim() }));
    }
    if (table && !tableDone) paper.append(table);
    paper.append(el("div", { class: "dp-gap" }), el("div", { class: "dp-sig" }, el("div", { text: "ขอแสดงความนับถือ" }),
      el("div", { class: "dp-sigline", style: "justify-content:center" }, el("span", { class: "dp-signame wide" }, c.with_signature && dc.sig ? el("img", { src: dc.sig, alt: "" }) : null)),
      el("div", { text: `(${snap.signer.name})` }), el("div", { text: snap.signer.title }), el("div", { text: "บริษัท สระบุรีวารีเทพ จำกัด" })));
    if (co.show_ack) paper.append(el("div", { class: "dp-gap" }), ackBlock(snap, ["ข้าพเจ้ารับทราบและยินยอมให้หักเงินตามรายการข้างต้น"]));
  }
  return paper;
}

function dpRow(k, v) {
  return el("div", { class: "dp-row" }, el("span", { class: "dp-k", text: k }), el("span", { class: "dp-v", text: v || "" }));
}

function renderDocPaper() {
  const box = $("docPaperBox");
  if (!box || !dc.cur) return;
  box.replaceChildren(buildPaper(dcCurrent()));
}

function printDocument() {
  if (dc.draft && !confirm("ยังเป็นร่าง (ไม่มีเลขที่) — พิมพ์เป็นตัวอย่างใช่ไหม? ถ้าจะใช้จริงให้กด “ออกเอกสาร” ก่อน")) return;
  document.body.classList.add("printing-doc");
  const done = () => { document.body.classList.remove("printing-doc"); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
}

// ------------------------------------------------------------------ actions
async function onSigToggle() {
  const on = $("dcSig").checked;
  if (dc.draft) { dc.draft.with_signature = on; renderDocPaper(); return; }
  try { dc.cur = await api(`/api/documents/${dc.cur.id}/print-options`, jsonOpts("POST", { with_signature: on })); renderDocPaper(); } catch (err) { showErr("docDetailError", err.message); }
}

async function onWitnessToggle() {
  const on = $("dcWit").checked;
  if (dc.draft) { dc.draft.content.show_witnesses = on; renderDocPaper(); return; }
  try { dc.cur = await api(`/api/documents/${dc.cur.id}/print-options`, jsonOpts("POST", { show_witnesses: on })); renderDocPaper(); } catch (err) { showErr("docDetailError", err.message); }
}

function draftBody() {
  const d = dc.draft;
  return { doc_date: d.doc_date, subject: d.subject, warning_level: d.warning_level, with_signature: d.with_signature, loan_id: d.loan_id || null, content: d.content };
}

async function saveDraft(quiet) {
  try {
    dc.cur = await api(`/api/documents/${dc.cur.id}`, jsonOpts("PATCH", draftBody()));
    dc.draft = dcDraftOf(dc.cur);
    if (quiet) { renderDocPaper(); return true; }
    renderDocument();
    return true;
  } catch (err) { showErr("docDetailError", err.message); return false; }
}

async function issueDocument() {
  if (!(await saveDraft(true))) return;
  if (!confirm(`ออกเอกสารฉบับนี้?\n\nได้เลขที่ตามลำดับของสาขา${dc.cur.branch} และ “แก้ไขไม่ได้อีก” (ถ้าผิดต้องยกเลิกแล้วทำใหม่)`)) return;
  try {
    dc.cur = await api(`/api/documents/${dc.cur.id}/issue`, jsonOpts("POST", {}));
    dc.draft = null;
    renderDocument();
  } catch (err) { showErr("docDetailError", err.message); }
}

async function deleteDraft() {
  if (!confirm("ลบร่างนี้?")) return;
  try { await api(`/api/documents/${dc.cur.id}`, { method: "DELETE" }); goHash("#documents"); } catch (err) { showErr("docDetailError", err.message); }
}

async function cancelDocument() {
  const note = prompt(`ยกเลิก ${dc.cur.type_label} ${dc.cur.number}?\n(เก็บไว้ในประวัติ ไม่นับเป็นการเตือนอีก) — เหตุผล:`);
  if (note === null) return;
  try { dc.cur = await api(`/api/documents/${dc.cur.id}/cancel`, jsonOpts("POST", { note })); renderDocument(); } catch (err) { showErr("docDetailError", err.message); }
}

async function duplicateDocument() {
  try { const d = await api(`/api/documents/${dc.cur.id}/duplicate`, jsonOpts("POST", {})); goHash(`#document=${d.id}`); } catch (err) { showErr("docDetailError", err.message); }
}

async function acknowledge(kind) {
  const body = { kind };
  if (kind !== "issued") {
    const date = prompt("วันที่ (ค.ศ. เช่น 2026-10-26) — เว้นว่าง = วันนี้", todayISO());
    if (date === null) return;
    body.date = date.trim() || todayISO();
  }
  if (kind === "refused") {
    const w = prompt("ชื่อพยานที่รับรู้การปฏิเสธลงนาม (คั่นด้วย ,)");
    if (!w) return;
    body.witnesses = w;
  }
  try { dc.cur = await api(`/api/documents/${dc.cur.id}/acknowledge`, jsonOpts("POST", body)); renderDocument(); } catch (err) { showErr("docDetailError", err.message); }
}

// ------------------------------------------------------------------ on a person's page
async function renderEmployeeDocs(person) {
  const box = document.getElementById("empDocsBox");
  if (!box) return;
  try {
    const d = await api(`/api/documents?employee_id=${person.id}`);
    if (!d.ready) { box.replaceChildren(el("p", { class: "muted small", text: "ต้องรัน schema_v24.sql ก่อนจึงใช้เอกสารได้" })); return; }
    const counting = d.documents.filter((x) => x.doc_type === "warning" && ["issued", "signed", "refused"].includes(x.status));
    box.replaceChildren(
      el("div", { class: "hstats" }, hStat(counting.length, "ใบเตือนที่ออกแล้ว", counting.length >= 2 ? "warn" : ""), hStat(d.documents.filter((x) => x.doc_type === "memo" && x.status !== "cancelled").length, "บันทึกข้อความ")),
      d.documents.length ? el("ul", { class: "hlist" }, ...d.documents.map((x) => el("li", { class: "hrow", style: "cursor:pointer", onclick: () => goHash(`#document=${x.id}`) },
        el("span", { class: "hrow-main", text: `${x.type_label}${x.warning_level != null && x.doc_type === "warning" ? ` · ${dcLevelShort(x.warning_level)}` : ""} · ${x.date_text}${x.number ? ` · ${x.number}` : ""}` }),
        el("span", { class: "hrow-side muted small", text: x.status_label })))) : el("p", { class: "muted small", text: "ไม่มีเอกสาร" }),
      el("div", { style: "display:flex;gap:8px;flex-wrap:wrap" }, el("button", { class: "btn small", type: "button", text: "+ ใบเตือน", onclick: () => newDocument("warning", person.id) }),
        el("button", { class: "btn small", type: "button", text: "+ บันทึกข้อความ", onclick: () => newDocument("memo", person.id) })));
  } catch (err) {
    box.replaceChildren(el("p", { class: "muted small", text: err.status === 423 ? "ล็อกอยู่ (ใส่ PIN)" : err.message }));
  }
}

// ------------------------------------------------------------------ init
function initDocuments() {
  $("dcSearch").addEventListener("input", renderDocuments);
  for (const id of ["dcType", "dcStatus"]) $(id).addEventListener("change", renderDocuments);
  $("dcClear").addEventListener("click", clearDocFilters);
  $("dcNewWarning").addEventListener("click", () => newDocument("warning"));
  $("dcNewMemo").addEventListener("click", () => newDocument("memo"));
  $("docSigFile").addEventListener("change", (e) => uploadSignature(e.target.files[0]));
  $("dcBack").addEventListener("click", () => goHash("#documents"));
}
