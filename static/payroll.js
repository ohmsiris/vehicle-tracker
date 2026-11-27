"use strict";

// คำนวณเงินเดือน (#payroll, #payroll/<month>), owner, 26 Nov 2026. HQ has always worked out everyone's pay; the owner now wants
// IceBox to do it, following แบบคำนวณเงินเดือน (เสนอ).xlsx. STEP ONE is this page, a CHECKLIST: for a pay month (26th to 25th,
// named by the 25th) which inputs of the calculation IceBox already has (green tick), which are stand-ins carried from HQ's last
// sheet or from another source (amber dot) and which are missing (red cross): by kind of input and per person. It calculates nothing
// and writes nothing. Everything shown comes from GET /api/payroll/checklist (app/payroll.py): the page's own menu, so a person given
// only this menu never needs /api/employees, /api/loans, /api/attendance or /api/driver-days.
// Owner, 27 Nov 2026: the workbook's written rules are "the primary for now; make sure it's visible and intuitive to see" -- the card
// กติกาที่ใช้คิดเงิน right under the tiles, and each person's own rule in the unfolded row. แก่งคอย is set aside for now (p.hold):
// its people stay in the table, last and muted, and are in none of the counts.

const PW_MONTH_RE = /^20\d\d-(0[1-9]|1[0-2])$/;
const PW_LINK_RE = /^#[A-Za-z0-9][A-Za-z0-9\-_=/.]*$/;          // a page of this site only: the link text comes from the server
const PW_BRANCHES = ["สระบุรี", "แก่งคอย"];
const PW_GROUPS = ["DRV-A", "DRV-B", "DRV-C", "DRV-D", "DRV-E", "DRV-F", "FAC-COM", "DAY", "KK-DRV", "KK-PROD"];     // the workbook's order
const PW_NO_GROUP = "-";               // the group filter's value for people who have no pay group yet
const PW_STATUSES = ["working", "new", "probation", "resigned", "left_before"];
const PW_STATUS_SAY = { working: "ทำงาน", new: "พนักงานใหม่", probation: "ทดลองงาน", resigned: "ลาออกในงวดนี้", left_before: "ลาออกแล้ว รอคืนเงินสะสม" };
// months = the last list the server gave; def = the month it picks when none is asked for; kinds / people = what is unfolded;
// filter / branch / group / status / kind / q = the filters of the per-person table (kind = "people who are not ok for this input");
// rules = the rules card is unfolded (it opens that way); unmatched = the list under เบิกล่วงหน้า is unfolded
const pw_ = { seq: 0, data: null, months: [], def: "", busy: false, filter: "all", branch: "", group: "", status: "", kind: "", q: "", kinds: new Set(), people: new Set(), rules: true, unmatched: false };

const pwN = (v) => Number(v || 0).toLocaleString("en-US");
const pwNorm = (s) => String(s || "").toLowerCase().replace(/[\s\-_.]/g, "");
// the nickname in brackets only when it says more than the name does (the same rule as who() in app/payroll.py)
const pwNick = (p) => { const nick = String(p.nickname || "").trim(); return nick && nick !== String(p.name || "").trim().split(/\s+/)[0] ? nick : ""; };
const pwWho = (p) => `${p.code || ""} ${p.name || ""}${pwNick(p) ? ` (${pwNick(p)})` : ""}`.trim();
const pwGone = (p) => !!(p.status && p.status.key === "left_before");       // left in an earlier period: on the list only for the savings refund
// "แก่งคอยพักไว้ก่อน": the branches the server says are set aside (their people carry hold: true and are in no count)
const pwHoldSay = () => { const b = ((pw_.data || {}).hold_branches || []).filter(Boolean); return b.length ? `${b.join(", ")}พักไว้ก่อน` : "พักไว้ก่อน"; };
// where a value stands in a fixed order; anything not in it goes after, and nothing at all goes last
const pwRank = (order, v) => (!v ? order.length + 1 : order.indexOf(v) < 0 ? order.length : order.indexOf(v));
const pwDmy = (iso) => (/^\d{4}-\d\d-\d\d/.test(iso || "") ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "");

// "2026-11" -> "พ.ย. 2026 (26/10/2026 - 25/11/2026)"; on a phone the select is a tight place: "พ.ย. 2026 (26/10 - 25/11)"
function pwMonthLabel(key) {
  const [y, m] = key.split("-").map(Number);
  const py = m === 1 ? y - 1 : y, pm = m === 1 ? 12 : m - 1;
  const two = (n) => String(n).padStart(2, "0");
  const tight = typeof matchMedia === "function" && matchMedia("(max-width: 760px)").matches;
  return tight ? `${TH_MONTHS[m - 1]} ${y} (26/${two(pm)} - 25/${two(m)})` : `${TH_MONTHS[m - 1]} ${y} (26/${two(pm)}/${py} - 25/${two(m)}/${y})`;
}

// ------------------------------------------------------------------ the drawn marks (never an emoji)
// The shape differs as well as the colour: tick = has it, dot = a stand-in, cross = missing, dash = not needed, ring = for information.
const PW_MARK = {
  ok: ["tick", "ok", "ครบ"], carry: ["dot", "warn", "ใช้ค่าแทน"], warn: ["dot", "warn", "ใช้ค่าแทน"],
  miss: ["cross", "bad", "ขาด"], bad: ["cross", "bad", "ขาด"], na: ["dash", "na", "ไม่ต้องใช้"], info: ["ring", "na", "แจ้งให้ทราบ"],
};
const PW_FILE_SAY = { ok: "เรียบร้อย", warn: "ควรตรวจ", bad: "ยังไม่ได้ทำ", info: "แจ้งให้ทราบ" };
const PW_SHAPE = {
  tick: () => sv("path", { d: "M3.4 8.6l3 3 6.2-6.8" }),
  cross: () => sv("path", { d: "M4.4 4.4l7.2 7.2M11.6 4.4l-7.2 7.2" }),
  dash: () => sv("path", { d: "M5 8h6" }),
  dot: () => sv("circle", { cx: "8", cy: "8", r: "3.4", fill: "currentColor", stroke: "none" }),
  ring: () => sv("circle", { cx: "8", cy: "8", r: "3.4", "stroke-width": "1.8" }),
};
function pwMark(state, label, big) {
  const [shape, tone, say] = PW_MARK[state] || PW_MARK.na;
  return el("span", { class: `pw-mark ${tone}${big ? " big" : ""}` },
    sv("svg", { viewBox: "0 0 16 16", width: "16", height: "16", fill: "none", stroke: "currentColor", "stroke-width": "2",
      "stroke-linecap": "round", "stroke-linejoin": "round", role: "img", "aria-label": label || say }, PW_SHAPE[shape]()));
}

// A link to another page of the site: only when the server sent one and this person may open that page.
function pwLink(link, label) {
  if (typeof link !== "string" || !PW_LINK_RE.test(link)) return null;
  if (isStaff() && !staffMayOpen(link.slice(1))) return null;
  return el("a", { class: "pw-link", href: link }, label || "เปิดหน้า", el("span", { "aria-hidden": "true", text: " ›" }));
}

// ------------------------------------------------------------------ loading
function openPayroll(sub) {
  showView("payroll");
  return pwLoad(PW_MONTH_RE.test(sub || "") ? sub : "", false);
}

async function pwLoad(month, fresh) {
  const seq = ++pw_.seq;                      // a slow answer for an older month must not overwrite a newer one
  showErr("pwError", "");
  const want = month || pw_.def;
  const same = !!(pw_.data && pw_.data.ready !== false && want && pw_.data.month === want);
  pw_.busy = true;
  pwDrawBar(want);
  if (!same) fill($("pwBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  const qs = new URLSearchParams();
  if (month) qs.set("month", month);
  if (fresh) qs.set("fresh", "true");
  let d;
  try { d = await api(`/api/payroll/checklist${qs.toString() ? `?${qs}` : ""}`); }
  catch (err) {
    if (seq !== pw_.seq) return;
    pw_.busy = false; pw_.data = null;
    pwDrawBar(want);
    fill($("pwBody"), el("button", { class: "btn", type: "button", text: "ลองใหม่", onclick: () => pwLoad(month, fresh) }));
    showErr("pwError", `โหลดไม่สำเร็จ: ${err.message}`);
    return;
  }
  if (seq !== pw_.seq) return;
  pw_.busy = false;
  pw_.data = d && typeof d === "object" ? d : {};
  const months = (pw_.data.months || []).filter((m) => PW_MONTH_RE.test(m));
  if (months.length) pw_.months = months;
  if (!month && PW_MONTH_RE.test(pw_.data.month || "")) pw_.def = pw_.data.month;
  pwRender();
}

// The month select (changing it sets the hash, so Back works) and the reload button.
function pwDrawBar(selected) {
  const months = pw_.months.slice();
  if (PW_MONTH_RE.test(selected || "") && !months.includes(selected)) months.push(selected);
  months.sort().reverse();
  const sel = months.length ? el("select", { class: "pw-month", "aria-label": "งวดเงินเดือน (วันที่ 26 ถึง 25 ตั้งชื่อตามเดือนของวันที่ 25)",
    onchange: (e) => goHash(`#payroll/${e.target.value}`) }, ...months.map((m) => el("option", { value: m, text: pwMonthLabel(m) }))) : null;
  if (sel && selected) sel.value = selected;
  fill($("pwBar"), sel,
    el("button", { class: "btn", type: "button", disabled: pw_.busy, title: "ดึงข้อมูลล่าสุดจากทุกแหล่งอีกครั้ง", text: pw_.busy ? "กำลังโหลด…" : "โหลดใหม่",
      onclick: () => pwLoad(selected || "", true) }));
}

// ------------------------------------------------------------------ the page
function pwRender() {
  const d = pw_.data;
  if (d.ready === false) {        // the table of pay rules is not there yet: say which file to run, and nothing else
    pw_.months = [];
    pwDrawBar("");
    fill($("pwBody"), el("div", { class: "edit-banner" }, "ยังใช้หน้านี้ไม่ได้ — ต้องรันไฟล์ ", el("strong", { text: d.need || "schema_v93.sql" }), " ใน Supabase ก่อน"));
    return;
  }
  pwDrawBar(d.month || "");
  const failed = (d.failed || []).filter(Boolean);
  // the order a phone reads it in: the numbers, the rules they will be worked out by, what to do, what to decide, then the long lists
  fill($("pwBody"),
    pwTiles(d),
    pwPeriodNote(d),
    pwRulesCard(d),
    pwFilesCard(d),
    pwDecisionsCard(d),
    pwKindsCard(d),
    pwPeopleCard(d),
    failed.length ? el("p", { class: "muted small pw-failed", text: `โหลดไม่ได้บางส่วน: ${failed.join(", ")}` }) : null);
  pwDrawPeople();
}

// One line under the tiles when the period is not an ordinary closed one: HQ already worked it out, or it is still running.
function pwPeriodNote(d) {
  const n = Number(d.hq_done || 0);
  if (n > 0) return el("p", { class: "pw-period small", text: `งวดนี้ HQ คิดและจ่ายแล้ว (มีใบ HQ ${pwN(n)} คน) — รายการหักใช้ยอดจากใบ HQ ของงวดนี้ ช่องอื่นแสดงเฉพาะข้อมูลที่ IceBox มีเอง` });
  if (d.closed === false) return el("p", { class: "pw-period small", text: `งวดนี้ยังไม่ปิด${pwDmy(d.end) ? ` (ถึง ${pwDmy(d.end)})` : ""} — ตรวจข้อมูลถึงเมื่อวานเท่านั้น` });
  return null;
}

function pwGoPeople() { pwDrawPeople(); const c = $("pwPeopleCard"); if (c) c.scrollIntoView({ block: "start" }); }

// Four tiles, all about the people being paid this period (someone who left earlier and only waits for savings, and a branch that
// is set aside, are in the first tile's small print). They are buttons: they set the per-person table's filter and take you to it.
function pwTiles(d) {
  const s = d.summary || {};
  const tile = (label, value, sub, tone, filter) => el("button", { class: `kpi kpi-btn ${tone}`.trim(), type: "button", title: "ดูรายชื่อในตาราง รายคน",
    onclick: () => { pw_.filter = filter; pw_.kind = ""; pw_.status = ""; pwGoPeople(); } },
    el("div", { class: "kpi-label", text: label }), el("div", { class: "kpi-value", text: pwN(value) }), el("div", { class: "kpi-sub", text: sub }));
  const sub = [Number(s.left_before || 0) > 0 ? `ลาออกแล้ว รอคืนเงินสะสม ${pwN(s.left_before)} คน` : "", `HQ จ่ายเอง ${pwN(s.hq_paid)} คน`, d.days ? `งวดนี้ ${pwN(d.days)} วัน` : "",
    Number(s.hold || 0) > 0 ? `${pwHoldSay()} ${pwN(s.hold)} คน` : ""];
  return el("div", { class: "pw-tiles" },
    tile("คนที่ต้องจ่าย", s.worked != null ? s.worked : s.people, sub.filter(Boolean).join(" · "), "", "all"),
    tile("พร้อมคำนวณ", s.ok, "ข้อมูลของงวดนี้ครบทุกช่องที่ต้องใช้", s.ok > 0 ? "ok" : "", "ok"),
    tile("ใช้ค่าแทนอยู่", s.carry, "ไม่ขาด แต่ยังมีช่องที่เป็นค่าแทน — ยืนยันได้ในขั้นต่อไป", s.carry > 0 ? "warn" : "", "carry"),
    tile("ยังขาดข้อมูล", s.miss, "มีช่องที่ยังไม่มีข้อมูลเลย", s.miss > 0 ? "bad" : "", "miss"));
}

// A rule as the server writes it ("ค่าแรง = วัน × 120 · ค่าคอม = (ยอดขาย − ค่าน้ำมันรถ) × 5% · ...") drawn part by part: what is paid in
// bold, each part kept whole on a wide screen. skip = leave a part out (the group table has a column of its own for เบี้ยขยัน).
function pwFormula(text, skip) {
  return String(text || "").split(" · ").filter((part) => part && !(skip && skip(part))).flatMap((part, i) => {
    const at = part.indexOf(" = ");
    const node = el("span", { class: "pw-f" }, at > 0 ? el("b", { text: part.slice(0, at) }) : null, at > 0 ? part.slice(at) : part);
    return i ? [el("span", { class: "pw-f-sep", "aria-hidden": "true", text: " · " }), node] : [node];
  });
}

// กติกาที่ใช้คิดเงิน: the workbook's written rules, right under the tiles and open (owner, 27 Nov 2026). One line per pay group
// (how it is worked out, with the group's real numbers; how many people; เบี้ยขยัน as a drawn tick or dash) and the general rules
// under it; on a phone each group becomes a small block. All of it is the server's "rules".
function pwRulesCard(d) {
  const r = d.rules || {};
  const groups = (r.groups || []).filter(Boolean), general = (r.general || []).filter(Boolean);
  if (!groups.length && !general.length) return null;
  const row = (g) => {
    const n = Number(g.people || 0), hold = Number(g.hold || 0);
    return el("tr", { class: n && hold >= n ? "hold" : "" },
      el("td", { class: "pw-r-g" }, el("b", { text: g.group || "" }), g.label ? el("span", { text: g.label }) : null,
        n && hold >= n ? el("span", { class: "pw-chip", text: "พักไว้ก่อน" }) : null),
      el("td", { class: "pw-r-f" }, ...pwFormula(g.formula, (part) => part.startsWith("เบี้ยขยัน"))),
      el("td", { class: "pw-r-n" }, n ? `${pwN(n)} คน` : el("span", { class: "muted", text: "–" }),
        hold && hold < n ? el("span", { class: "pw-r-h", text: `(พักไว้ ${pwN(hold)})` }) : null),
      el("td", { class: "pw-r-b" }, pwMark(g.bonus ? "ok" : "na", g.bonus ? "ได้เบี้ยขยัน" : "ไม่มีเบี้ยขยัน"),
        el("span", { class: "pw-r-bt", "aria-hidden": "true", text: g.bonus ? " เบี้ยขยัน" : " ไม่มีเบี้ยขยัน" })));
  };
  const body = el("div", { class: "pw-rules", id: "pwRules", hidden: !pw_.rules },
    el("div", { class: "pw-rules-in" },
      groups.length ? el("table", { class: "pw-rtable" },
        el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "กลุ่ม" }), el("th", { scope: "col", text: "คิดอย่างไร" }),
          el("th", { scope: "col", class: "pw-r-n", text: "จำนวนคน" }), el("th", { scope: "col", class: "pw-r-b", text: "เบี้ยขยัน" }))),
        el("tbody", {}, ...groups.map(row))) : null,
      general.length ? el("div", { class: "pw-rules-gen" }, el("h4", { text: "กติกาทั่วไป" }), el("ul", {}, ...general.map((t) => el("li", { text: t })))) : null));
  const btn = el("button", { class: "pw-fold", type: "button", "aria-expanded": String(pw_.rules), "aria-controls": "pwRules" },
    "กติกาที่ใช้คิดเงิน", el("span", { class: "pw-fold-chev", "aria-hidden": "true", text: "›" }));
  const card = el("div", { class: `card pw-card pw-rules-card${pw_.rules ? " open" : ""}` },
    el("h3", { class: "pw-h" }, btn, r.source ? el("span", { class: "muted small", text: ` ${r.source}` }) : null), body);
  btn.addEventListener("click", () => {
    pw_.rules = !pw_.rules;
    body.hidden = !pw_.rules; card.classList.toggle("open", pw_.rules); btn.setAttribute("aria-expanded", String(pw_.rules));
  });
  return card;
}

// ต้องอัปโหลด / ต้องทำ: the files to drop in and the buttons to press, in the server's order (the order to do them).
function pwFilesCard(d) {
  const files = (d.files || []).filter(Boolean);
  const left = files.filter((f) => f.state === "bad" || f.state === "warn").length;
  return el("div", { class: "card pw-card" },
    el("h3", { class: "pw-h" }, "ต้องอัปโหลด / ต้องทำ", files.length ? el("span", { class: "muted small", text: left ? ` เหลือ ${left} จาก ${files.length} อย่าง` : " ครบแล้ว" }) : null),
    files.length ? el("ul", { class: "pw-list" }, ...files.map((f) => el("li", { class: "pw-row" },
      pwMark(f.state === "ok" || f.state === "warn" || f.state === "bad" ? f.state : "info", PW_FILE_SAY[f.state] || PW_FILE_SAY.info, true),
      el("div", { class: "pw-row-main" }, el("strong", { text: f.title || f.key || "" }), f.summary ? el("div", { class: "small pw-sub", text: f.summary }) : null),
      pwLink(f.link, f.link_label))))
      : el("p", { class: "muted small", text: "ไม่มีอะไรต้องอัปโหลดหรือกดเพิ่มสำหรับงวดนี้" }));
}

// "ครบ a · ค่าแทน b · ขาด c จาก n คน" (parts that are zero are left out). Nobody needing it is "ไม่มีใครต้องใช้" only when that
// is known: a kind the server marks with a note ("not known yet") says so instead, and one only a branch that is set aside needs
// says that ("แก่งคอยพักไว้ก่อน 28 คน"). The numbers never include that branch (k.hold = how many of its people need this input).
function pwKindCount(k) {
  const hold = Number(k.hold || 0);
  if (!Number(k.need || 0)) return el("span", { class: `pw-count ${k.note ? "pw-unknown" : "muted"}`, text: k.note ? "ยังไม่รู้" : hold ? `${pwHoldSay()} ${pwN(hold)} คน` : "ไม่มีใครต้องใช้" });
  const parts = [["ok", "ครบ", k.ok], ["warn", "ค่าแทน", k.carry], ["bad", "ขาด", k.miss]].filter((x) => Number(x[2] || 0) > 0);
  return el("span", { class: "pw-count", title: hold ? `ไม่นับ ${pwHoldSay()} ${pwN(hold)} คน` : null },
    ...parts.flatMap(([tone, word, n], i) => [i ? " · " : null, el("span", { class: `pw-n ${tone}`, text: `${word} ${pwN(n)}` })]),
    `${parts.length ? " " : ""}จาก ${pwN(k.need)} คน`);
}

// the same three numbers as one thin stacked bar (the count beside it says it in words)
function pwMeter(k) {
  const need = Number(k.need || 0);
  if (!need) return null;
  const part = (tone, n) => (Number(n || 0) > 0 ? el("i", { class: tone, style: `width:${(100 * Number(n) / need).toFixed(2)}%` }) : null);
  return el("span", { class: "pw-meter", "aria-hidden": "true" }, part("ok", k.ok), part("warn", k.carry), part("bad", k.miss));
}

// เบิกล่วงหน้า only: the lines of the mid-month lists that are nobody's on the pay list (no staff code, someone who left, someone HQ
// pays), folded under the kind's own line. All of it is the server's "advance_unmatched": who (as the list writes it), amount, file, why.
function pwUnmatched(list) {
  const rows = (list || []).filter(Boolean);
  if (!rows.length) return null;
  const body = el("ul", { class: "pw-who", id: "pwUnmatched", hidden: !pw_.unmatched },
    ...rows.map((r) => el("li", {}, pwMark("info", "จับคู่กับพนักงานไม่ได้"),
      el("span", {}, el("b", { text: r.who || "ไม่มีชื่อ" }), ` — ${pwN(r.amount)} บาท`, r.why ? ` · ${r.why}` : null,
        r.file ? el("span", { class: "muted", text: ` · ${r.file}` }) : null))));
  const btn = el("button", { class: "pw-fold", type: "button", "aria-expanded": String(pw_.unmatched), "aria-controls": "pwUnmatched" },
    `ชื่อในใบเบิกที่จับคู่กับพนักงานไม่ได้ ${pwN(rows.length)} รายการ`, el("span", { class: "pw-fold-chev", "aria-hidden": "true", text: "›" }));
  const box = el("div", { class: `pw-unm small${pw_.unmatched ? " open" : ""}` }, btn, body);
  btn.addEventListener("click", () => {
    pw_.unmatched = !pw_.unmatched;
    body.hidden = !pw_.unmatched; box.classList.toggle("open", pw_.unmatched); btn.setAttribute("aria-expanded", String(pw_.unmatched));
  });
  return box;
}

// One input of the calculation. The row unfolds (a real button: Tab, Enter / Space) to list who is not ok: people with something
// of their own to fix first, then one line for the many who share the same words ("107 คน"). The names are one press away, in the table.
// d = the whole answer: the เบิกล่วงหน้า line also carries the names of the lists that found nobody (pwUnmatched).
function pwKind(k, i, d) {
  const rows = (k.rows || []).filter(Boolean), more = Number(k.more || 0);
  const can = rows.length > 0 || more > 0;
  const key = k.key || `kind${i}`;
  const id = `pwKind${i}`;
  const body = can ? el("div", { class: "pw-kind-body", id, hidden: !pw_.kinds.has(key) }) : null;
  const draw = () => { if (!body.firstChild) fill(body,
    el("ul", { class: "pw-who" }, ...rows.map((r) => el("li", {}, pwMark(r.state),
      el("span", {}, el("b", { text: r.who || "" }), r.detail ? ` — ${r.detail}` : null)))),
    el("p", { class: "small pw-more" }, more > 0 ? `และอีก ${pwN(more)} คน · ` : null,
      el("button", { class: "pw-inline", type: "button", text: "ดูรายชื่อทั้งหมดในตาราง รายคน",
        onclick: () => { pw_.kind = key; pw_.filter = "all"; pw_.status = ""; pwGoPeople(); } }))); };
  // flat children: stacked in a narrow card, one aligned line (title | count | bar | source) in a wide one (see .pw-kinds in app.css)
  const src = [k.source ? el("span", { text: k.source }) : null,
    k.note ? el("span", { class: "pw-unknown", text: k.note }) : null,
    k.later ? el("span", { class: "pw-later" }, el("i", { text: "รอขั้นต่อไป" }), ` ${k.later}`) : null].filter(Boolean);
  // the mark's spoken label follows the words beside it: "not known yet", or only a branch that is set aside needs it
  const nobody = !Number(k.need || 0);
  const inner = [pwMark(k.state, nobody && k.note ? "ยังไม่รู้" : nobody && Number(k.hold || 0) ? pwHoldSay() : null, true),
    el("span", { class: "pw-kind-top" }, el("strong", { text: k.title || key }), pwKindCount(k)),
    pwMeter(k),
    src.length ? el("span", { class: "muted small pw-src" }, ...src) : null,
    can ? el("span", { class: "pw-chev", "aria-hidden": "true", text: "›" }) : null];
  const item = el("li", { class: `pw-kind${can && pw_.kinds.has(key) ? " open" : ""}` });
  const head = can ? el("button", { class: "pw-kind-btn", type: "button", "aria-expanded": String(pw_.kinds.has(key)), "aria-controls": id,
    onclick: () => {
      const open = !pw_.kinds.has(key);
      if (open) { pw_.kinds.add(key); draw(); } else pw_.kinds.delete(key);
      body.hidden = !open; item.classList.toggle("open", open); head.setAttribute("aria-expanded", String(open));
    } }, ...inner) : el("div", { class: "pw-kind-btn plain" }, ...inner);
  if (can && pw_.kinds.has(key)) draw();
  item.append(el("div", { class: "pw-kind-head" }, head, pwLink(k.link, k.link_label)));
  if (body) item.append(body);
  const loose = key === "advance" ? pwUnmatched((d || {}).advance_unmatched) : null;
  if (loose) item.append(loose);
  return item;
}

function pwKindsCard(d) {
  const kinds = (d.kinds || []).filter(Boolean);
  const later = kinds.some((k) => k.later);
  const hold = Number((d.summary || {}).hold || 0);
  return el("div", { class: "card pw-card" },
    el("h3", { class: "pw-h" }, "ข้อมูลที่ต้องใช้คำนวณ", kinds.length ? el("span", { class: "muted small", text: ` ${kinds.length} อย่าง ตามลำดับช่องในแบบคำนวณ` }) : null),
    hold ? el("p", { class: "muted small pw-later-say" }, el("span", { class: "pw-chip", text: "พักไว้ก่อน" }),
      ` ตัวเลขทุกบรรทัดไม่นับ${(d.hold_branches || []).filter(Boolean).join(", ")} ${pwN(hold)} คน`) : null,
    later ? el("p", { class: "muted small pw-later-say" }, el("span", { class: "pw-later" }, el("i", { text: "รอขั้นต่อไป" })),
      " = ยังไม่มีที่ลงหรือยืนยันในระบบ อัปโหลดหรือกดอะไรในหน้านี้ก็ยังไม่เป็นสีเขียว — จะทำได้ในขั้นต่อไปของงานนี้") : null,
    kinds.length ? el("ul", { class: "pw-list pw-kinds" }, ...kinds.map((k, i) => pwKind(k, i, d))) : el("p", { class: "muted small", text: "ยังไม่มีรายการข้อมูลของงวดนี้" }));
}

// ------------------------------------------------------------------ รายคน
const pwCellState = (c) => (c && ["ok", "carry", "miss"].includes(c.state) ? c.state : "na");       // anything unknown counts as "not needed"
const pwCellText = (c) => (!c ? "" : c.text || (c.value != null && c.value !== "" ? (typeof c.value === "number" ? pwN(c.value) : String(c.value)) : ""));

function pwPeopleCard(d) {
  const people = (d.people || []).filter(Boolean);
  const paid = people.filter((p) => !pwGone(p)), gone = people.length - paid.length;
  const counted = paid.filter((p) => !p.hold), aside = paid.length - counted.length;       // a branch that is set aside is in no count
  const n = (st) => counted.filter((p) => p.state === st).length;       // the same people the tiles count
  const seg = (kind, label, opts) => el("div", { class: "seg", role: "group", "aria-label": label },
    ...opts.map(([value, text, title]) => el("button", { class: "segbtn", type: "button", "data-pw": kind, "data-v": value, text, title: title || null,
      onclick: () => { pw_[kind] = value; pwDrawPeople(); } })));
  const extra = [aside ? `${pwHoldSay()} ${pwN(aside)} คน` : "", gone ? `ลาออกแล้ว รอคืนเงินสะสม ${pwN(gone)} คน` : ""].filter(Boolean).join(" · ");
  const branches = [...new Set(people.map((p) => p.branch).filter(Boolean))]
    .sort((a, b) => pwRank(PW_BRANCHES, a) - pwRank(PW_BRANCHES, b) || a.localeCompare(b, "th"));
  if (pw_.branch && !branches.includes(pw_.branch)) pw_.branch = "";
  const groups = new Map();           // n = the people counted, h = those set aside with their branch
  paid.forEach((p) => { if (!p.group) return; const g = groups.get(p.group) || { label: p.group_label || "", n: 0, h: 0 }; g[p.hold ? "h" : "n"] += 1; groups.set(p.group, g); });
  const none = paid.filter((p) => !p.group).length;
  if (pw_.group && !(groups.has(pw_.group) || (pw_.group === PW_NO_GROUP && none))) pw_.group = "";
  const groupN = (x) => (x.h ? (x.n ? `${pwN(x.n)} + พักไว้ ${pwN(x.h)}` : `พักไว้ก่อน ${pwN(x.h)}`) : pwN(x.n));
  const groupSel = el("select", { id: "pwGroup", "aria-label": "กลุ่มคิดเงิน", onchange: (e) => { pw_.group = e.target.value; pwDrawPeople(); } },
    el("option", { value: "", text: "ทุกกลุ่ม" }),
    ...[...groups].sort((a, b) => pwRank(PW_GROUPS, a[0]) - pwRank(PW_GROUPS, b[0]) || a[0].localeCompare(b[0]))
      .map(([g, x]) => el("option", { value: g, text: `${g}${x.label ? ` · ${x.label}` : ""} (${groupN(x)})` })),
    none ? el("option", { value: PW_NO_GROUP, text: `ยังไม่ได้ตั้งกลุ่ม (${none})` }) : null);
  groupSel.value = pw_.group;
  // every status with its count, so the few who are not simply "ทำงาน" can be found
  const byStatus = (key) => people.filter((p) => (p.status || {}).key === key).length;
  if (pw_.status && !byStatus(pw_.status)) pw_.status = "";
  const statusSel = el("select", { id: "pwStatus", "aria-label": "สถานะพนักงาน", onchange: (e) => { pw_.status = e.target.value; pwDrawPeople(); } },
    el("option", { value: "", text: "ทุกสถานะ" }),
    ...PW_STATUSES.map((key) => el("option", { value: key, disabled: !byStatus(key), text: `${PW_STATUS_SAY[key]} (${pwN(byStatus(key))})` })));
  statusSel.value = pw_.status;
  // "who is not ok for input X": the same list a kind's line counts (so never a branch that is set aside)
  const cols = (d.columns || []).filter((c) => c && c.key);
  const open = (key) => people.filter((p) => !p.hold && ["carry", "miss"].includes(pwCellState((p.cells || {})[key]))).length;
  if (pw_.kind && !open(pw_.kind)) pw_.kind = "";
  const kindSel = el("select", { id: "pwKindSel", "aria-label": "ช่องที่ยังไม่ครบ", onchange: (e) => { pw_.kind = e.target.value; pwDrawPeople(); } },
    el("option", { value: "", text: "ทุกช่อง" }),
    ...cols.filter((c) => open(c.key)).map((c) => el("option", { value: c.key, text: `ยังไม่ครบ: ${c.label || c.key} (${pwN(open(c.key))})` })));
  kindSel.value = pw_.kind;
  const legend = el("div", { class: "pw-legend small muted" },
    ...["ok", "carry", "miss", "na"].map((st) => el("span", {}, pwMark(st), ` ${st === "ok" ? "มีข้อมูลของงวดนี้" : st === "carry" ? "ค่าแทน / รอยืนยัน" : st === "miss" ? "ขาด" : "ไม่มีในงวดนี้"}`)),
    el("span", { text: "กดที่แถวเพื่อดูหมายเหตุของทุกช่อง" }));
  return el("div", { class: "card pw-card", id: "pwPeopleCard" },
    el("h3", { class: "pw-h", text: "รายคน" }),
    el("div", { class: "pw-tools", id: "pwTools" },
      // the four numbers are the people the tiles count; who else is in the table is said in words under the tools (a phone has no hover)
      seg("filter", "กรองตามความครบของข้อมูล", [["all", `ทั้งหมด ${pwN(counted.length)}`],
        ["miss", `ยังขาด ${pwN(n("miss"))}`], ["carry", `ค่าแทน ${pwN(n("carry"))}`], ["ok", `พร้อม ${pwN(n("ok"))}`]]),
      seg("branch", "กรองตามสาขา", [["", "ทุกสาขา"], ...branches.map((b) => [b, b])]),
      groupSel, statusSel, kindSel,
      el("input", { type: "text", id: "pwSearch", inputmode: "search", autocomplete: "off", placeholder: "ค้นหา: รหัส ชื่อ ชื่อเล่น สถานะ", "aria-label": "ค้นหาพนักงาน", value: pw_.q,
        oninput: (e) => { pw_.q = e.target.value; pwDrawPeople(); } }),
      el("span", { class: "muted small pw-shown", id: "pwCount", role: "status" })),
    extra ? el("p", { class: "muted small pw-extra", text: `ในตารางมีอีก ${pwN(aside + gone)} คนที่ไม่นับในตัวเลขข้างบน: ${extra}` }) : null,
    legend,
    el("div", { id: "pwPeople" }));
}

// The table only (the toolbar stays, so the search box keeps its focus while typing).
function pwDrawPeople() {
  const d = pw_.data, box = $("pwPeople");
  if (!d || !box) return;
  document.querySelectorAll("#pwTools .segbtn").forEach((b) => {
    const on = pw_[b.dataset.pw] === b.dataset.v;
    b.classList.toggle("active", on); b.setAttribute("aria-pressed", String(on));
  });
  [["pwStatus", "status"], ["pwKindSel", "kind"]].forEach(([id, k]) => { const s = $(id); if (s && s.value !== pw_[k]) s.value = pw_[k]; });
  const cols = (d.columns || []).filter((c) => c && c.key), all = (d.people || []).filter(Boolean);
  const q = pwNorm(pw_.q);
  // "ยังขาด / ค่าแทน / พร้อม" are about the people being paid (what the tiles count); someone who only waits for savings shows under
  // "ทั้งหมด" and under his own status, and the people of a branch that is set aside under "ทั้งหมด" or when their branch is chosen
  const mine = (p) => !p.hold || pw_.branch === p.branch;
  const rows = all.filter((p) => (pw_.filter === "all" || (p.state === pw_.filter && mine(p) && (!pwGone(p) || pw_.status === "left_before")))
    && (!pw_.branch || p.branch === pw_.branch)
    && (!pw_.group || (!pwGone(p) && (pw_.group === PW_NO_GROUP ? !p.group : p.group === pw_.group)))
    && (!pw_.status || (p.status || {}).key === pw_.status)
    && (!pw_.kind || (mine(p) && ["carry", "miss"].includes(pwCellState((p.cells || {})[pw_.kind]))))
    && (!q || pwNorm(`${p.code} ${p.name} ${p.nickname || ""} ${(p.status || {}).label || ""}`).includes(q)));
  $("pwCount").textContent = rows.length === all.length ? `แสดง ${pwN(rows.length)} คน` : `แสดง ${pwN(rows.length)} จาก ${pwN(all.length)} คน`;
  if (!rows.length) { fill(box, el("p", { class: "muted small pw-none", text: all.length ? "ไม่มีใครตรงกับตัวกรองนี้" : "ยังไม่มีรายชื่อของงวดนี้" })); return; }
  // Narrow columns: every input except the pay group (the heading of each block names it) and the ones nobody on the list has
  // anything in this period (they are named under the table, and each has its line in the card above).
  const has = (c) => all.some((p) => pwCellState((p.cells || {})[c.key]) !== "na");
  const ins = cols.filter((c) => c.key !== "group" && has(c));
  const empty = cols.filter((c) => c.key !== "group" && !has(c));
  const ncol = 5 + ins.length;
  const groups = new Map();           // one heading per branch + pay group; inside a group the people keep the server's order
  rows.forEach((p) => { const k = pwGone(p) ? "~gone" : `${p.branch || ""}|${p.group || ""}`; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p); });
  // the people counted first, then those who only wait for savings, and a branch that is set aside last
  const sorted = [...groups.values()].sort((a, b) => Number(!!a[0].hold) - Number(!!b[0].hold) || Number(pwGone(a[0])) - Number(pwGone(b[0]))
    || pwRank(PW_BRANCHES, a[0].branch) - pwRank(PW_BRANCHES, b[0].branch)
    || String(a[0].branch || "").localeCompare(String(b[0].branch || ""), "th")
    || pwRank(PW_GROUPS, a[0].group) - pwRank(PW_GROUPS, b[0].group) || String(a[0].group || "").localeCompare(String(b[0].group || "")));
  const body = el("tbody", {});
  for (const list of sorted) {
    const p0 = list[0], gone = pwGone(p0), hold = !!p0.hold;
    const n = (st) => (hold ? 0 : list.filter((p) => p.state === st).length);       // a block that is set aside shows no red or amber count
    body.append(el("tr", { class: `pw-group${hold ? " hold" : ""}` }, el("th", { colspan: String(ncol), scope: "colgroup" }, el("span", { class: "pw-group-in" },
      el("b", { text: gone ? "ลาออกแล้ว · รอคืนเงินสะสม" : [p0.branch, p0.group || "ยังไม่ได้ตั้งกลุ่ม"].filter(Boolean).join(" · ") }),
      !gone && p0.group_label ? ` ${p0.group_label}` : null,
      el("span", { class: "muted", text: ` · ${pwN(list.length)} คน` }),
      hold ? " " : null, hold ? el("span", { class: "pw-chip", text: "พักไว้ก่อน" }) : null,
      n("miss") ? el("span", { class: "pw-n bad", text: ` · ขาด ${pwN(n("miss"))}` }) : null,
      n("carry") ? el("span", { class: "pw-n warn", text: ` · ค่าแทน ${pwN(n("carry"))}` }) : null))));
    list.forEach((p) => body.append(...pwPersonRows(p, cols, ins)));
  }
  const th = (text, cls) => el("th", { scope: "col", class: cls || null, text });
  const head = el("thead", {}, el("tr", {},
    th("รหัส", "pw-c-code"), th("ชื่อ", "pw-c-name"), th("ตำแหน่ง", "pw-c-pos"), th("สถานะ", "pw-c-st"),
    ...ins.map((c) => el("th", { scope: "col", class: "pw-c-in", title: c.label || null }, el("abbr", { title: c.label || null, text: c.short || c.label || c.key }))),
    el("th", { scope: "col", class: "pw-c-caret", "aria-label": "ดูรายละเอียด" })));
  fill(box, el("div", { class: "pw-tablewrap", tabindex: "0", role: "region", "aria-label": "ตารางรายคน เลื่อนซ้ายขวาได้" }, el("table", { class: "pw-table" }, head, body)),
    empty.length ? el("p", { class: "muted small pw-empty-cols", text: `ช่องที่ยังไม่มีข้อมูลของใครในงวดนี้ ไม่แสดงในตาราง: ${empty.map((c) => c.label || c.key).join(", ")}` }) : null);
}

const PW_STATUS_TONE = { new: "info", probation: "warn", resigned: "bad", left_before: "bad" };
const PW_STATE_SAY = { ok: "พร้อมคำนวณ", carry: "ใช้ค่าแทนอยู่", miss: "ยังขาดข้อมูล" };

// One input cell: the mark, and the value beside it when there is one. The note is the hover title (and is in the unfolded row).
function pwCell(c, col) {
  const st = pwCellState(c);
  const text = st === "na" ? "" : pwCellText(c);
  return el("td", { class: `pw-in ${st}`, title: c && c.note ? `${col.label || col.key}: ${c.note}` : null }, pwMark(st), text ? el("span", { class: "pw-v", text }) : null);
}

// What the unfolded row shows: every input this person needs as "label: value: note", then the ones with nothing in this period.
function pwDetail(p, cols) {
  const cells = p.cells || {};
  const used = cols.filter((c) => pwCellState(cells[c.key]) !== "na");
  const off = cols.filter((c) => !used.includes(c));
  // two rates inside the period (a raise, a change of work) are both said: the rate cell's own words, "350 / 400"
  const has2 = p.rate2 != null && p.rate2 !== "";
  const rateSay = !has2 && (cells.rate || {}).text ? cells.rate.text : pwN(p.rate);
  const rate = pwGone(p) ? "" : p.rate != null && p.rate !== "" ? `ค่าแรง ${rateSay} บาท/วัน${has2 ? ` · อัตราที่สอง ${pwN(p.rate2)} บาท/วัน` : ""}` : "ยังไม่มีค่าแรงในระบบ";
  const st = p.status || {};
  const missing = (p.missing || []).filter(Boolean);
  return el("div", { class: "pw-panel" },
    el("div", { class: "pw-panel-head" }, el("b", { text: pwWho(p) }),
      el("span", { class: "muted small", text: [p.position || "", p.group ? `${p.group}${p.group_label ? ` ${p.group_label}` : ""}` : "", rate, st.label ? `${st.label}${st.detail ? ` (${st.detail})` : ""}` : ""].filter(Boolean).join(" · ") }),
      p.hold ? el("span", { class: "pw-chip", text: pwHoldSay() }) : null),
    // this person's own rule, in the workbook's words with his numbers, above the notes
    p.formula ? el("p", { class: "pw-formula" }, el("span", { class: "pw-formula-k", text: "คิดอย่างไร" }), ...pwFormula(p.formula)) : null,
    missing.length ? el("p", { class: "small pw-lack", text: `ขาด: ${missing.join(", ")}` }) : null,
    used.length ? el("dl", { class: "pw-notes" }, ...used.map((c) => {
      const x = cells[c.key], xs = pwCellState(x), text = pwCellText(x);
      return el("div", { class: `pw-note ${xs}` }, el("dt", {}, pwMark(xs), ` ${c.label || c.key}`),
        el("dd", {}, text ? el("b", { class: "pw-v", text }) : null, text && x.note ? " · " : null, x.note || (text ? null : el("span", { class: "muted", text: PW_MARK[xs][2] }))));
    })) : el("p", { class: "muted small", text: "คนนี้ไม่มีช่องที่ต้องใช้ในการคำนวณ" }),
    off.length && used.length && !pwGone(p) ? el("p", { class: "muted small pw-off", text: `ไม่มีในงวดนี้: ${off.map((c) => c.label || c.key).join(", ")}` }) : null);
}

// cols = every column (the unfolded row lists them all), ins = the ones drawn as narrow columns
function pwPersonRows(p, cols, ins) {
  const id = p.id || p.code || "";
  const cells = p.cells || {}, st = p.status || {};
  const cell = el("td", { colspan: String(5 + ins.length) });
  const detail = el("tr", { class: "pw-detail", hidden: !pw_.people.has(id) }, cell);
  const btn = el("button", { class: "pw-caret", type: "button", "aria-expanded": String(pw_.people.has(id)), "aria-label": `ดูหมายเหตุของ ${pwWho(p)}`, text: "›" });
  const tone = ["ok", "carry", "miss"].includes(p.state) ? p.state : "";
  const nick = pwNick(p);
  // someone set aside with his branch: the row is muted and its edge carries no colour (his own marks stay, to be read when wanted)
  const row = el("tr", { class: `pw-person ${p.hold ? "hold" : tone}${pw_.people.has(id) ? " open" : ""}`,
    onclick: () => {                // the button's own click bubbles up to here, so keyboard and mouse share one path
      const open = detail.hidden;
      if (open) { pw_.people.add(id); if (!cell.firstChild) cell.append(pwDetail(p, cols)); } else pw_.people.delete(id);
      detail.hidden = !open; row.classList.toggle("open", open); btn.setAttribute("aria-expanded", String(open));
    } },
    // the person's own state as a shape too (tick / dot / cross), not only as the colour of the row's edge. On a phone the name
    // (and a status that is not plain "ทำงาน") sits under the code, so the first ticks show without scrolling sideways.
    el("td", { class: "pw-c-code" }, tone ? pwMark(tone, PW_STATE_SAY[tone]) : null, tone ? " " : null, p.code || "",
      el("div", { class: "pw-c-sub" }, `${p.name || ""}${nick ? ` (${nick})` : ""}`,
        st.key && st.key !== "working" && st.label ? el("span", { class: `lc-badge ${PW_STATUS_TONE[st.key] || ""}`.trim(), text: st.label }) : null)),
    el("td", { class: "pw-c-name" }, p.name || "", nick ? el("span", { class: "muted", text: ` (${nick})` }) : null),
    el("td", { class: "pw-c-pos", title: p.position || null, text: p.position || "" }),
    el("td", { class: "pw-c-st" }, el("span", { class: `lc-badge ${PW_STATUS_TONE[st.key] || ""}`.trim(), text: st.label || "–" }),
      st.detail ? el("div", { class: "muted pw-st-d", text: st.detail }) : null),
    ...ins.map((c) => pwCell(cells[c.key], c)),
    el("td", { class: "pw-c-caret" }, btn));
  if (pw_.people.has(id)) cell.append(pwDetail(p, cols));
  return [row, detail];
}

function pwDecisionsCard(d) {
  // what the owner still has to settle, then what he said he would bring (x.wait): the same list, the waiting ones marked
  const list = (d.decisions || []).filter(Boolean);
  if (!list.length) return null;
  const wait = list.filter((x) => x.wait).length, ask = list.length - wait;
  return el("div", { class: "card pw-card" },
    el("h3", { class: "pw-h" }, "เรื่องที่ต้องตัดสินใจก่อนคำนวณ",
      el("span", { class: "muted small", text: ` ${[ask ? `${ask} เรื่อง` : "", wait ? `รอเจ้าของ ${wait} เรื่อง` : ""].filter(Boolean).join(" · ")}` })),
    el("ol", { class: "pw-decisions" }, ...list.map((x) => el("li", {}, el("strong", { text: x.title || "" }),
      x.wait ? " " : null, x.wait ? el("span", { class: "pw-chip wait", text: "รอเจ้าของ" }) : null,
      x.detail ? el("div", { class: "small pw-sub", text: x.detail }) : null))));
}
