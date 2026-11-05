"use strict";

// รถ › ยางสำรอง (#tyre-stock): the spare-tyre store, COUNTED by size (not numbered) -- app/tyre_store.py.
//   on hand per size (from the ledger), the minimum (below it = buy more), the latest moves (off a truck, onto a truck, bought, thrown away,
//   corrected by a count), and the monthly real count that catches spares that went out without being recorded ("หายไป 1 เส้น").
// Tyres go in and out mostly from the truck pages (งานยาง / บันทึกเปลี่ยนยาง); here: buy in, throw away, count, minimums.

const tst = { data: null, diffs: null };

async function openTyreStock() {
  showView("tyreStock");
  showErr("tstError", "");
  await loadTyreStock();
}

async function loadTyreStock() {
  try {
    tst.data = await api("/api/tyre-stock");
  } catch (err) {
    showErr("tstError", `โหลดยางสำรองไม่สำเร็จ: ${err.message}`);
    return;
  }
  renderTyreStock();
}

function renderTyreStock() {
  const d = tst.data;
  if (!d.ready) { fill($("tstBody"), el("p", { class: "muted", text: "ยังใช้ไม่ได้ — ต้องรันไฟล์ schema_v40.sql ใน Supabase ก่อน" })); return; }
  const low = d.sizes.filter((s) => s.low);
  const table = el("table", { class: "tst-table" },
    el("thead", {}, el("tr", {}, ...["ขนาด", "มีในสต็อก", "ขั้นต่ำ", "รถที่ใช้ขนาดนี้", ""].map((t) => el("th", { text: t })))),
    el("tbody", {}, ...d.sizes.map((s) => el("tr", { class: s.low ? "low" : "" },
      el("td", {}, el("strong", { text: s.size })),
      el("td", { class: "num" }, el("strong", { text: String(s.on_hand) }), s.on_hand < 0 ? el("span", { class: "warn-text small", text: " (ติดลบ — นับจริงแล้วแก้)" }) : null),
      el("td", { class: "num" }, el("button", { class: "linkbtn", type: "button", text: s.min ? String(s.min) : "ตั้ง", title: "ตั้งขั้นต่ำ", onclick: () => tstSetMin(s) })),
      el("td", { class: "num muted", text: s.trucks ? `${s.trucks} คัน` : "–" }),
      el("td", {}, s.low ? el("span", { class: "badge urgent", text: `ควรซื้อเพิ่ม ${s.min - s.on_hand}` }) : null)))));
  fill($("tstBody"),
    low.length ? el("div", { class: "warnings" }, el("strong", { text: "ยางสำรองต่ำกว่าขั้นต่ำ" }),
      el("ul", {}, ...low.map((s) => el("li", { text: `${s.size}: เหลือ ${s.on_hand} เส้น (ขั้นต่ำ ${s.min})` })))) : null,
    tst.diffs && tst.diffs.length ? el("div", { class: "warnings" }, el("strong", { text: "ผลการนับไม่ตรงกับระบบ — ระบบปรับตามที่นับแล้ว" }),
      el("ul", {}, ...tst.diffs.map((x) => el("li", { text: x.diff < 0 ? `${x.size}: หายไป ${-x.diff} เส้น (ระบบมี ${x.expected} นับได้ ${x.counted}) — ไปอยู่รถคันไหน? ถามช่างเอ๋ / บัญชี` : `${x.size}: เกินมา ${x.diff} เส้น (ระบบมี ${x.expected} นับได้ ${x.counted})` })))) : null,
    el("div", { class: "card tst-card" },
      el("div", { class: "tst-head" }, el("h3", { text: "ยางสำรองตามขนาด" }), el("span", { class: "muted small", text: d.last_count ? `นับจริงครั้งล่าสุด ${beDate(d.last_count)}` : "ยังไม่เคยนับจริง" }),
        el("span", { class: "grow" }),
        el("button", { class: "btn small", type: "button", text: "+ ซื้อเข้า / ทิ้ง", onclick: tstMove }),
        el("button", { class: "btn small primary", type: "button", text: "นับยางจริง", onclick: tstCount })),
      d.sizes.length ? table : el("p", { class: "muted", text: "ยังไม่มีขนาดยาง — ใส่ขนาดยางที่หน้ารถ หรือกด “นับยางจริง” / “ซื้อเข้า”" }),
      el("p", { class: "muted small", text: "ยางเข้าออกสต็อกส่วนใหญ่บันทึกที่หน้ารถ: “งานยาง” (ใช้ยางสำรอง / ถอดยางเก็บเป็นสำรอง) และ “บันทึกเปลี่ยนยาง” · ช่างเอ๋เป็นคนตัดสินว่ายางที่ถอดมาซ่อมเก็บไว้หรือทิ้ง" })),
    el("div", { class: "card tst-card" }, el("h3", { text: "ความเคลื่อนไหวล่าสุด" }),
      d.moves.length ? el("div", { class: "tst-moves" }, ...d.moves.map((m) => el("div", { class: "tst-move small" },
        el("span", { text: beDate(m.moved_on) }), el("strong", { class: m.qty > 0 ? "plus" : "minus", text: `${m.qty > 0 ? "+" : ""}${m.qty}` }),
        el("span", { text: m.size }), el("span", { class: "muted", text: [d.kinds[m.kind] || m.kind, m.truck, m.brand, m.notes].filter(Boolean).join(" · ") }))))
        : el("p", { class: "muted small", text: "ยังไม่มี" })));
}

function tstDialog(title, body, onSave, saveText = "บันทึก") {
  let dlg = $("tstDialog");
  if (!dlg) { dlg = el("dialog", { id: "tstDialog" }); document.body.append(dlg); }
  const err = el("p", { class: "error", hidden: true });
  const save = el("button", { class: "btn primary", type: "button", text: saveText });
  save.onclick = async () => {
    save.disabled = true;
    err.hidden = true;
    try { await onSave(); dlg.close(); renderTyreStock(); } catch (e) { err.textContent = e.message; err.hidden = false; }
    save.disabled = false;
  };
  fill(dlg, el("h3", { text: title }), body, err, el("div", { class: "dialog-actions" }, el("span", { class: "grow" }), el("button", { class: "btn", type: "button", text: "ยกเลิก", onclick: () => dlg.close() }), save));
  dlg.showModal();
}

const tstInput = (v, attrs = {}) => { const i = el("input", { type: "text", autocomplete: "off", ...attrs }); i.value = v ?? ""; return i; };
const tstField = (label, node) => el("label", { class: "ty-field" }, el("span", { class: "muted small", text: label }), node);

function tstMove() {
  const kind = el("select", {}, el("option", { value: "purchase", text: "ซื้อเข้า (+)" }), el("option", { value: "scrap", text: "ทิ้ง (−)" }));
  const size = tstInput("", { placeholder: "เช่น 225/75R14 หรือ 7.50-16", list: "tstSizes" });
  const sizes = el("datalist", { id: "tstSizes" }, ...tst.data.sizes.map((s) => el("option", { value: s.size })));
  const qty = tstInput("1", { inputmode: "numeric" });
  const brand = tstInput("", { placeholder: "ยี่ห้อ (ถ้ามี)" });
  const date = tstInput(todayISO(), { type: "date" });
  const notes = tstInput("", { placeholder: "เช่น ซื้อจากโชคพัฒนา" });
  tstDialog("ซื้อเข้า / ทิ้ง", el("div", { class: "ty-fields" }, tstField("ซื้อเข้า หรือ ทิ้ง", kind), tstField("ขนาด", el("div", {}, size, sizes)), tstField("จำนวน (เส้น)", qty),
    tstField("ยี่ห้อ", brand), tstField("วันที่", date), tstField("หมายเหตุ", notes)),
    async () => { tst.data = await api("/api/tyre-stock/moves", jsonOpts("POST", { kind: kind.value, size: size.value, qty: qty.value, brand: brand.value, moved_on: date.value, notes: notes.value })); });
}

function tstCount() {
  const rows = tst.data.sizes.map((s) => ({ s, input: tstInput("", { inputmode: "numeric", placeholder: String(s.on_hand) }) }));
  const extraSize = tstInput("", { placeholder: "ขนาดอื่นที่เจอ เช่น 7.50-16" });
  const extraQty = tstInput("", { inputmode: "numeric", placeholder: "จำนวน" });
  tstDialog("นับยางสำรองจริง", el("div", {},
    el("p", { class: "muted small", text: "ใส่จำนวนที่นับได้จริงในคลัง (ช่องว่าง = ไม่ได้นับขนาดนั้น) — ถ้าไม่ตรงกับระบบ จะขึ้นว่าหายไป / เกินมากี่เส้น" }),
    el("div", { class: "tst-count" }, ...rows.map((x) => el("label", { class: "tst-count-row" }, el("strong", { text: x.s.size }), el("span", { class: "muted small", text: `ระบบมี ${x.s.on_hand}` }), x.input)),
      el("div", { class: "tst-count-row" }, extraSize, el("span"), extraQty))),
    async () => {
      const counts = Object.fromEntries(rows.filter((x) => x.input.value.trim() !== "").map((x) => [x.s.size, x.input.value]));
      if (extraSize.value.trim() && extraQty.value.trim()) counts[extraSize.value] = extraQty.value;
      const res = await api("/api/tyre-stock/count", jsonOpts("POST", { counts }));
      tst.diffs = res.diffs;
      tst.data = res;
    }, "บันทึกผลนับ");
}

function tstSetMin(s) {
  const n = tstInput(s.min || "", { inputmode: "numeric" });
  tstDialog(`ขั้นต่ำ ${s.size}`, el("div", {}, el("p", { class: "muted small", text: "มีน้อยกว่านี้ = ขึ้นเตือนให้ซื้อเพิ่ม (0 = ไม่เตือน)" }), tstField("ขั้นต่ำ (เส้น)", n)),
    async () => { tst.data = await api("/api/tyre-stock/min", jsonOpts("POST", { size: s.size, min: n.value || "0" })); });
}
