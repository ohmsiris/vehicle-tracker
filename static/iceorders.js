"use strict";

// ยอดสั่งน้ำแข็ง (#ice-orders, owner 11 Nov 2026): every truck's order for a day (tomorrow by default) -- what the drivers sent from
// their phones, and what the office typed in from paper / a call -- with the total per product, so หน้าโรง knows how much to have
// ready. Server: app/route_sales.orders_for / save_order.

const io_ = { day: null, data: null };

async function openIceOrders(sub = "") {
  showView("iceOrders");
  showErr("ioError", "");
  if (/^\d{4}-\d{2}-\d{2}$/.test(sub)) io_.day = sub;
  fill($("ioBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { io_.data = await api(`/api/ice-orders${io_.day ? `?day=${io_.day}` : ""}`); }
  catch (err) { fill($("ioBody"), null); showErr("ioError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  io_.day = io_.data.day;
  ioRender();
}

const ioDmy = (iso) => (iso ? iso.split("-").reverse().join("/") : "");
const ioAdd = (iso, n) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const ioWhen = (iso) => (iso ? new Date(iso).toLocaleString("th-TH", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");

function ioRender() {
  const d = io_.data, P = d.products;
  const used = P.filter((p) => d.total[p.key] || d.rows.some((r) => r.q[p.key]) || ["lod", "lek", "mo"].includes(p.key));
  const rel = d.day === ioAdd(d.today, 1) ? "พรุ่งนี้" : d.day === d.today ? "วันนี้" : "";
  const nav = el("div", { class: "ss-daynav card" },
    el("button", { class: "btn ss-arrow", type: "button", text: "‹", "aria-label": "วันก่อน", onclick: () => goHash(`#ice-orders/${ioAdd(d.day, -1)}`) }),
    el("div", { class: "ss-daypick" }, el("b", { class: "ss-daybig", text: ioDmy(d.day) }), el("span", { class: "muted small", text: `ยอดสั่งสำหรับ${rel ? ` ${rel}` : "วันนี้"}` })),
    el("button", { class: "btn ss-arrow", type: "button", text: "›", "aria-label": "วันถัดไป", onclick: () => goHash(`#ice-orders/${ioAdd(d.day, 1)}`) }),
    el("div", { class: "ss-dayjump" },
      d.day !== ioAdd(d.today, 1) ? el("button", { class: "btn small", type: "button", text: "พรุ่งนี้", onclick: () => goHash(`#ice-orders/${ioAdd(d.today, 1)}`) }) : null,
      el("button", { class: "btn small", type: "button", text: "พิมพ์", onclick: ioPrint })));
  const tiles = el("div", { class: "ss-tiles" },
    el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: "สั่งแล้ว" }), el("b", { text: `${d.ordered} / ${d.rows.length} คัน` })),
    ...used.map((p) => el("div", { class: "ss-tile" }, el("span", { class: "muted small", text: `รวม${p.label}` }), el("b", { text: elN(d.total[p.key] || 0) }))));
  const body = el("tbody", {}, ...d.rows.map((r) => {
    const ins = used.map((p) => el("input", { class: "ss-in", type: "text", inputmode: "numeric", value: r.q[p.key] ? String(r.q[p.key]) : "", "aria-label": `รถ ${r.truck_no} ${p.label}` }));
    const save = el("button", { class: "btn small", type: "button", text: "บันทึก", hidden: true, onclick: async () => {
      const q = Object.fromEntries(used.map((p, i) => [p.key, Number(ins[i].value.replace(/,/g, "")) || 0]).filter(([, v]) => v > 0));
      save.disabled = true;
      try { await api(`/api/ice-orders/${r.route_id}`, jsonOpts("PUT", { day: d.day, q })); } catch (err) { alert(err.message); save.disabled = false; return; }
      openIceOrders(d.day);
    } });
    ins.forEach((x) => x.addEventListener("input", () => { save.hidden = false; }));
    return el("tr", { class: Object.keys(r.q).length ? "sold" : "" },
      el("td", {}, el("b", { text: r.truck_no })),
      el("td", {}, el("span", { text: r.driver || "—" }), el("div", { class: "ss-bins", text: r.route || "" })),
      ...ins.map((x) => el("td", { class: "c" }, x)),
      el("td", { class: "small muted", text: Object.keys(r.q).length ? `${r.by_name || ""} · ${ioWhen(r.updated_at)}` : "ยังไม่สั่ง" }),
      el("td", {}, save));
  }));
  const table = el("div", { class: "ss-tablewrap soft" }, el("table", { class: "ss-table" },
    el("thead", {}, el("tr", {}, el("th", { text: "รถ" }), el("th", { text: "คนขับ / สาย" }), ...used.map((p) => el("th", { class: "c", text: p.label })),
      el("th", { text: "สั่งโดย" }), el("th", { text: "" }))), body));
  fill($("ioBody"), nav, tiles,
    el("p", { class: "muted small", text: "คนขับสั่งจากโทรศัพท์ (การ์ด “สั่งน้ำแข็งพรุ่งนี้”) · ถ้าคนขับเขียนกระดาษหรือโทรมา ใส่ตัวเลขในตารางแล้วกดบันทึก" }), table);
}

function ioPrint() {
  const d = io_.data, P = d.products.filter((p) => d.total[p.key] || d.rows.some((r) => r.q[p.key]) || ["lod", "lek", "mo"].includes(p.key));
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const n = (v) => (v ? Number(v).toLocaleString("en-US") : "");
  const w = window.open("", "_blank");
  if (!w) { alert("เบราว์เซอร์บล็อกหน้าต่างใหม่ — อนุญาตป๊อปอัปของเว็บนี้แล้วลองใหม่"); return; }
  w.document.write(`<!doctype html><html lang="th"><head><meta charset="utf-8"><title>ยอดสั่งน้ำแข็ง ${ioDmy(d.day)}</title><style>
@font-face { font-family: S; src: url("${location.origin}/static/fonts/sarabun-regular.ttf"); } @font-face { font-family: S; src: url("${location.origin}/static/fonts/sarabun-bold.ttf"); font-weight: 700; }
body { font-family: S, sans-serif; margin: 12mm; color: #000; } h1 { font-size: 18pt; margin: 0 0 8px; }
table { border-collapse: collapse; width: 100%; font-size: 12pt; } th, td { border: 1px solid #000; padding: 4px 8px; } th { background: #eee; }
td.c, th.c { text-align: center; } tr.tot td { font-weight: 700; } @page { size: A4 portrait; margin: 10mm; }</style></head><body>
<h1>ยอดสั่งน้ำแข็ง วันที่ ${ioDmy(d.day)}</h1><table><tr><th>รถ</th><th>คนขับ</th>${P.map((p) => `<th class="c">${p.label}</th>`).join("")}<th>หมายเหตุ</th></tr>
${d.rows.map((r) => `<tr><td class="c">${esc(r.truck_no)}</td><td>${esc(r.driver)}</td>${P.map((p) => `<td class="c">${n(r.q[p.key])}</td>`).join("")}<td>${Object.keys(r.q).length ? "" : "ยังไม่สั่ง"}</td></tr>`).join("")}
<tr class="tot"><td colspan="2" class="c">รวม</td>${P.map((p) => `<td class="c">${n(d.total[p.key])}</td>`).join("")}<td></td></tr></table></body></html>`);
  w.document.close();
  w.onload = () => setTimeout(() => w.print(), 300);
}
