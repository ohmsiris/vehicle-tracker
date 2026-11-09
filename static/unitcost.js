"use strict";

// บัญชี › งบกำไรขาดทุน › ต้นทุนต่อหน่วย (app/unit_cost.py): what one tube bag (25 kg) and one block (~190 kg) cost, for the month picked
// at the top of the page, and month by month underneath. How much was made comes from the meters (not in the books), so the
// assumptions are listed at the bottom for the owner to correct.
// Colors: one per cost type, the same in every chart and table on this tab.

const uc_ = { data: null, branch: null };
const UC_COLOR = { elec: "var(--el-peak)", labour: "var(--el-off)", pack: "var(--el-hol)", supply: "var(--el-ft)", repair: "var(--el-dem)",
  admin: "var(--el-m2)", delivery: "var(--el-vat)" };
const UC_PRODUCT = { tube: { name: "น้ำแข็งหลอด", unit: "ถุง", size: "ถุง 25 กก." }, block: { name: "น้ำแข็งซอง", unit: "ก้อน", size: "ก้อน ~190 กก." } };

async function loadUnitCost(seq) {
  const branch = $("acxBranch").value;
  $("acxHalf").hidden = true;
  if (!branch) { fill($("acxBody"), el("div", { class: "card" }, el("p", { class: "muted", text: "ต้นทุนต่อหน่วย: เลือกสาขา (ยังไม่มีแบบรวมสองสาขา)" }))); return; }
  if (uc_.branch !== branch || !uc_.data) {
    fill($("acxBody"), el("p", { class: "muted", text: "กำลังคำนวณต้นทุนต่อหน่วย…" }));
    try {
      const data = await api(`/api/accounts/unit-cost?branch=${encodeURIComponent(branch)}`);
      if (seq !== acx.seq) return;
      uc_.data = data; uc_.branch = branch;
    } catch (err) { if (seq !== acx.seq) return; fill($("acxBody"), null); showErr("acxError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  }
  if (seq !== acx.seq) return;
  renderUnitCost();
}

function renderUnitCost() {
  const d = uc_.data;
  if (!d.ready || !d.months.length) {
    fill($("acxBody"), el("div", { class: "card" }, el("p", { class: "muted", text: `ต้นทุนต่อหน่วย ${d.branch}: ${d.why || "ยังไม่มีเดือนที่มีทั้งข้อมูลมิเตอร์และบัญชี"}` })));
    return;
  }
  const pick = $("acxMonth").value;
  const m = d.months.find((x) => x.month === pick);
  const range = `${elMon(d.months[0].month)}–${elMon(d.months[d.months.length - 1].month)}`;
  const parts = [];
  if (pick !== "year" && !m) parts.push(el("div", { class: "card" }, el("p", { class: "muted", text: `เดือนนี้ยังไม่มีข้อมูลการผลิต (มี ${range}) — ดูรายเดือนด้านล่าง` })));
  if (m) parts.push(ucMonth(m));
  parts.push(ucTrend(d), ucAssumptions(d));
  fill($("acxBody"), ...parts);
}

// ---- one month: a card per product, then the breakdown
function ucMonth(m) {
  const card = (key) => {
    const x = m[key], p = UC_PRODUCT[key];
    const margin = x.price != null ? x.price - x.delivered : null;
    return el("div", { class: "card uc-card" },
      el("div", { class: "uc-card-head" }, el("strong", { text: p.name }), el("span", { class: "muted small", text: ` · ${p.size}` })),
      el("div", { class: "uc-big" }, el("span", { text: `${elN(x.plant, 2)} ฿` }), el("small", { class: "muted", text: ` ต่อ${p.unit} หน้าโรง` })),
      el("div", { class: "uc-rows" },
        el("div", {}, el("span", { class: "muted", text: "+ ค่าส่งเฉลี่ย" }), el("b", { text: `${elN(x.per_unit.delivery, 2)} ฿` })),
        el("div", {}, el("span", { class: "muted", text: "= ส่งถึงลูกค้า" }), el("b", { text: `${elN(x.delivered, 2)} ฿` })),
        el("div", {}, el("span", { class: "muted", text: `ราคาขายเฉลี่ย (ใบขายในโปรแกรม ${elN(x.price_qty)} ${p.unit})` }), el("b", { text: x.price != null ? `${elN(x.price, 2)} ฿` : "–" })),
        margin != null ? el("div", { class: margin < 0 ? "uc-bad" : "uc-good" }, el("span", { text: "เหลือต่อหน่วย หลังค่าส่ง" }), el("b", { text: `${margin < 0 ? "−" : ""}${elN(Math.abs(margin), 2)} ฿` })) : null),
      el("p", { class: "muted small", text: `ผลิตประมาณ ${elN(x.units)} ${p.unit} (${elN(x.kg / 1000)} ตัน)` }));
  };
  const cats = uc_.data.categories;
  const table = el("div", { class: "card acx-feeds" }, el("h3", { text: "แยกตามประเภทต้นทุน" }),
    el("div", { class: "uc-table" },
      el("div", { class: "uc-tr head muted small" }, el("span", { text: "ประเภท" }), el("span", { text: "หลอด ฿/ถุง" }), el("span", { text: "ซอง ฿/ก้อน" }), el("span", { text: "รวมทั้งเดือน ฿" })),
      ...cats.map((c) => el("div", { class: "uc-tr" },
        el("span", {}, el("i", { class: "uc-dot", style: `background:${UC_COLOR[c.key]}` }), c.label),
        el("span", { text: elN(m.tube.per_unit[c.key], 2) }), el("span", { text: elN(m.block.per_unit[c.key], 2) }),
        el("span", { class: "muted", text: elN(m.tube.total[c.key] + m.block.total[c.key]) }))),
      el("div", { class: "uc-tr total" }, el("span", { text: "ส่งถึงลูกค้า" }), el("span", { text: elN(m.tube.delivered, 2) }), el("span", { text: elN(m.block.delivered, 2) }), el("span", {}))));
  return el("div", {},
    el("p", { class: "uc-head" }, el("strong", { text: `ต้นทุนต่อหน่วย · ${elMon(m.month)}` }), el("span", { class: "muted small", text: ` (${beDate(m.start)} – ${beDate(m.end)}) · ${uc_.data.branch}` })),
    m.incomplete ? el("p", { class: "badge warn uc-warn", text: "⚠ เดือนนี้ยังไม่ครบ: ถุง เกลือ เคมี ที่จ่ายด้วยเช็คยังไม่เข้า (มากับเช็คเดือนถัดไป) — ต้นทุนจริงสูงกว่านี้" }) : null,
    el("div", { class: "uc-grid" }, card("tube"), card("block")), table);
}

// ---- month by month: cost per bag and per block, stacked by cost type (two charts: different units, never one axis)
function ucTrend(d) {
  const ms = d.months.filter((x) => !x.incomplete);
  const keys = d.categories.map((c) => [c.key, c.label, UC_COLOR[c.key]]);
  const rows = (k) => ms.map((x) => ({ bill_period: x.month, ...x[k].per_unit }));
  const skipped = d.months.filter((x) => x.incomplete).map((x) => elMon(x.month));
  return el("div", { class: "card el-panel" },
    el("div", { class: "el-panel-head" }, el("h3", { text: "รายเดือน" }),
      el("span", { class: "muted small", text: `ต้นทุนส่งถึงลูกค้า แยกประเภท${skipped.length ? ` · ไม่แสดง ${skipped.join(", ")} (ยังไม่ครบ)` : ""}` })),
    elLegend(keys.map(([, l, c]) => [l, c])),
    el("div", { class: "el-grid" },
      elChart("น้ำแข็งหลอด · บาทต่อถุง", "ถุง 25 กก.", elStack(rows("tube"), keys, (v) => `${elN(v, 2)} ฿`)),
      elChart("น้ำแข็งซอง · บาทต่อก้อน", "ก้อน ~190 กก.", elStack(rows("block"), keys, (v) => `${elN(v, 2)} ฿`))));
}

function ucAssumptions(d) {
  return el("div", { class: "card acx-feeds" }, el("h3", { text: "คิดอย่างไร (สมมติฐาน — แก้ได้ ถ้าตัวเลขไม่ตรงกับหน้างาน)" }),
    el("ul", { class: "uc-assume muted small" }, ...d.assumptions.map((a) => el("li", { text: a }))));
}
