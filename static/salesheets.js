"use strict";

// รถสาย · ใบขาย (#sales-sheets) -- the office side of the drivers' phone sheet (app/route_sales.py; the drivers use /d/).
//   * every route: driver, customers, whether a PIN is set, the last day sent; set / reset the PIN, sign the phones out
//   * one route's days: what was sold, cash expected vs counted, km, what does not add up; open a day, mark it checked

const ss_ = { routes: null, open: null, days: null };

async function openSalesSheets() {
  showView("salesSheets");
  showErr("ssError", "");
  fill($("ssBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { const r = await api("/api/sales-routes"); ss_.routes = r.routes; ss_.today = r.today; }
  catch (err) { fill($("ssBody"), null); showErr("ssError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  renderSalesSheets();
}

function ssLink() { return `${location.origin}/d/`; }

function renderSalesSheets() {
  const link = el("div", { class: "card ss-link" },
    el("strong", { text: "ลิงก์สำหรับคนขับ (ลิงก์เดียวทุกคน)" }),
    el("div", { class: "ss-linkrow" }, el("code", { text: ssLink() }),
      el("button", { class: "btn small", type: "button", text: "คัดลอก", onclick: async (e) => {
        try { await navigator.clipboard.writeText(ssLink()); e.target.textContent = "คัดลอกแล้ว ✓"; } catch (x) { window.prompt("คัดลอกลิงก์", ssLink()); }
      } })),
    el("p", { class: "muted small", text: "คนขับเปิดลิงก์ เลือกชื่อ ใส่รหัส 4 หลักครั้งเดียว แล้วโทรศัพท์จะจำไว้ · ให้กด \"เพิ่มไปยังหน้าจอหลัก\" เพื่อเปิดเหมือนแอป · ต้องตั้งรหัสให้ก่อน คนขับถึงจะเห็นชื่อ" }));
  const rows = ss_.routes.map((r) => {
    const last = r.last_day;
    const flags = last && last.totals && last.totals.flags ? last.totals.flags.length : 0;
    return el("div", { class: `card ss-route${ss_.open === r.id ? " open" : ""}` },
      el("div", { class: "ss-head" },
        el("b", { class: "ss-no", text: r.truck_no.replace("KK-", "แก่งคอย ") }),
        el("div", { class: "ss-who" }, el("strong", { text: r.driver_name || "—" }), el("span", { class: "muted small", text: ` · ${r.name} · ลูกค้า ${r.customers}` }),
          r.walkins ? el("span", { class: "badge bad", text: `คนขับเพิ่มลูกค้าเอง ${r.walkins} ร้าน — รอตรวจ` }) : null,
          r.reopen_requests ? el("span", { class: "badge warn", text: `ขอแก้ใบขาย ${r.reopen_requests}` }) : null,
          r.unsent ? el("span", { class: "badge bad", text: `ไม่ได้ส่งใบขาย ${r.unsent} วัน` }) : null),
        el("span", { class: `badge ${r.pin_set ? "ok" : ""}`, text: r.pin_set ? "ตั้งรหัสแล้ว" : "ยังไม่ได้ตั้งรหัส" }),
        last ? el("span", { class: "muted small", text: `ล่าสุด ${beDate(last.day)} · ฿${elN((last.totals || {}).amount || 0)}${flags ? ` · ⚠ ${flags}` : ""}` }) : null),
      r.pin_set ? ssPhones(r) : null,
      el("div", { class: "ss-actions" },
        el("button", { class: "btn small primary", type: "button", text: r.pin_set ? "ตั้งรหัสใหม่" : "ตั้งรหัส", onclick: () => ssSetPin(r) }),
        r.pin_set ? el("button", { class: "btn small", type: "button", text: "ออกจากระบบทุกเครื่อง", onclick: () => ssSignOut(r) }) : null,
        el("button", { class: "btn small", type: "button", text: ss_.open === r.id ? "ซ่อนรายวัน" : "ดูรายวัน", onclick: () => ssToggle(r.id) }),
        el("button", { class: "btn small", type: "button", text: "ใบงานเปล่า (พิมพ์)", title: "ใบงานคนขับแบบกระดาษ ยังไม่กรอก — รายชื่อ ถัง ราคา ล่าสุด", onclick: () => ssPrintSheet(r.id, "") }),
        el("button", { class: "btn small", type: "button", text: "ใบงานเปล่า (Excel)", onclick: () => ssSheetXlsx(r.id, "") })),
      ss_.open === r.id ? el("div", { id: "ssDays" }, el("p", { class: "muted small", text: "กำลังโหลด…" })) : null);
  });
  fill($("ssBody"), link, ...rows);
  if (ss_.open) ssLoadDays(ss_.open);
}

function ssAgo(iso) {
  if (!iso) return "—";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 15) return "เพิ่งใช้";
  if (min < 60) return `${min} นาทีก่อน`;
  if (min < 24 * 60) return `${Math.round(min / 60)} ชม.ก่อน`;
  return new Date(iso).toLocaleDateString("th-TH", { day: "numeric", month: "short" });
}

function ssPhones(r) {
  const ph = r.phones || [];
  if (!ph.length) return el("p", { class: "ss-phones muted small", text: "ยังไม่มีโทรศัพท์เข้าระบบ" });
  return el("div", { class: "ss-phones" },
    el("span", { class: `small${ph.length > 1 ? " warn-text" : ""}`, text: `เข้าระบบอยู่ ${ph.length} เครื่อง` }),
    ...ph.map((x) => el("span", { class: "ss-phone small", text: `${x.agent} · ใช้ล่าสุด ${ssAgo(x.last_seen)} · เข้าระบบ ${new Date(x.since).toLocaleDateString("th-TH", { day: "numeric", month: "short" })}` })));
}

async function ssSetPin(r) {
  const pin = window.prompt(`รหัส 4 หลักสำหรับ ${r.driver_name || "สาย " + r.truck_no}\n(บอกคนขับเอง อย่าส่งในกลุ่มไลน์)`, "");
  if (pin == null) return;
  if (!/^\d{4}$/.test(pin.trim())) { alert("รหัสต้องเป็นตัวเลข 4 หลัก"); return; }
  try { await api(`/api/sales-routes/${r.id}/pin`, jsonOpts("POST", { pin: pin.trim() })); } catch (err) { alert(err.message); return; }
  await openSalesSheets();
}

async function ssSignOut(r) {
  if (!confirm(`ออกจากระบบทุกโทรศัพท์ของ ${r.driver_name}? (ต้องใส่รหัสใหม่ตอนเปิดครั้งหน้า)`)) return;
  try { const x = await api(`/api/sales-routes/${r.id}/signout`, jsonOpts("POST", {})); alert(`ออกจากระบบแล้ว ${x.signed_out} เครื่อง`); } catch (err) { alert(err.message); }
}

function ssToggle(id) { ss_.open = ss_.open === id ? null : id; renderSalesSheets(); }

async function ssLoadDays(routeId) {
  let d;
  try { d = await api(`/api/sales-routes/${routeId}/days`); } catch (err) { fill($("ssDays"), el("p", { class: "error", text: err.message })); return; }
  if (!d.days.length) { fill($("ssDays"), el("p", { class: "muted small", text: "ยังไม่มีใบขายจากโทรศัพท์" })); return; }
  const ST = { open: ["กำลังขาย", ""], closed: ["ส่งแล้ว รอตรวจ", "warn"], checked: ["ตรวจแล้ว", "ok"] };
  fill($("ssDays"), el("div", { class: "ss-days" },
    el("div", { class: "ss-day head muted small" }, ...["วันที่", "สถานะ", "ขาย", "เงินสด ต้องมี / นับได้", "ค้าง", "วางบิล", "กม.", "ที่ไม่ตรง"].map((t) => el("span", { text: t }))),
    ...d.days.map((x) => {
      const t = x.totals || {};
      const st = x.unsent ? ["ไม่ได้ส่ง", "bad"] : ST[x.status] || [x.status, ""];
      return el("button", { class: "ss-day", type: "button", onclick: () => ssOpenDay(x.id) },
        el("span", { text: beDate(x.day) }), el("span", {}, el("span", { class: `badge ${st[1]}`, text: st[0] }),
          x.reopen_requested_at ? el("span", { class: "badge warn", text: "ขอแก้" }) : null),
        el("span", { text: `฿${elN(t.amount || 0)}` }),
        el("span", { class: t.counted != null && Math.abs(t.counted - t.expected_cash) >= 1 ? "warn-text" : "", text: `฿${elN(t.expected_cash || 0)} / ${t.counted != null ? "฿" + elN(t.counted) : "—"}` }),
        el("span", { text: `฿${elN(t.credit || 0)}` }), el("span", { text: `฿${elN(t.invoiced || 0)}` }),
        el("span", { text: t.km != null ? elN(t.km) : "—" }),
        el("span", { class: (t.flags || []).length ? "warn-text" : "muted", text: (t.flags || []).join(" · ") || "ตรง" }));
    })));
}

async function ssOpenDay(dayId) {
  let d;
  try { d = await api(`/api/sales-routes/day/${dayId}`); } catch (err) { alert(err.message); return; }
  const day = d.day, t = day.totals || {}, P = d.products;
  const used = P.filter((p) => (t.loaded || {})[p.key] || (t.sold || {})[p.key]);
  const dlg = el("dialog", { class: "ss-dialog" },
    el("div", { class: "ss-dlg-head" }, el("h3", { text: `ใบขาย ${beDate(day.day)}` }),
      el("span", { class: "ss-dlg-tools" },
        el("button", { class: "btn small", type: "button", text: "พิมพ์ใบงาน / PDF", onclick: () => ssPrintSheet(day.route_id, day.day) }),
        el("button", { class: "btn small", type: "button", text: "Excel", onclick: () => ssSheetXlsx(day.route_id, day.day) }),
        el("button", { class: "btn small", type: "button", text: "ปิด", onclick: () => { dlg.close(); dlg.remove(); } }))),
    (t.flags || []).length ? el("p", { class: "warn-text", text: `⚠ ${t.flags.join(" · ")}` }) : el("p", { class: "muted small", text: "ทุกอย่างตรง" }),
    el("div", { class: "ss-stock" },
      el("div", { class: "ss-srow head muted small" }, el("span", { text: "" }), ...["รับ", "ขาย", "เหลือ", "หน้าโรงนับ"].map((h) => el("span", { text: h }))),
      ...used.map((p) => el("div", { class: "ss-srow" }, el("b", { text: p.label }),
        el("span", { text: elN(t.loaded[p.key]) }), el("span", { text: elN(t.sold[p.key]) }), el("span", { text: elN(t.left[p.key]) }),
        el("input", { class: "ss-ret", type: "number", inputmode: "numeric", min: "0", "data-ret": p.key, "aria-label": `${p.label} ที่คืน`,
          value: day.returns_foh && day.returns_foh[p.key] != null ? day.returns_foh[p.key] : "", placeholder: elN(Math.max(t.left[p.key], 0)) }))),
      used.length ? el("div", { class: "ss-retsave" },
        el("span", { class: "muted small", text: day.returns_at ? `หน้าโรงนับแล้ว ${new Date(day.returns_at).toLocaleString("th-TH")}` : "หน้าโรงยังไม่ได้นับน้ำแข็งที่คืน" }),
        el("button", { class: "btn small", type: "button", text: "บันทึกน้ำแข็งคืน", onclick: () => ssSaveReturns(dayId, dlg) })) : null),
    el("div", { class: "ss-lines" },
      ...d.lines.map((ln) => el("div", { class: "ss-line" },
        ln.walkin
          ? el("span", {}, el("span", { class: "badge bad", text: "เพิ่มเอง" }), ` ${ln.name}${ln.phone ? ` · โทร ${ln.phone}` : ""} `,
              ln.walkin_key ? el("button", { class: "btn small", type: "button", text: "เพิ่มเข้ารายชื่อสาย", title: "ให้ร้านนี้อยู่ในรายชื่อของสายนี้ ราคาตามที่คนขับใส่",
                onclick: () => ssWalkin(dayId, ln, dlg) }) : null)
          : el("span", { text: `${ln.seq || ""}. ${ln.name || "?"}` }),
        el("span", { class: "muted small", text: P.filter((p) => ln.qty[p.key]).map((p) => `${p.label} ${elN(ln.qty[p.key])}`).join(" · ") }),
        el("b", { text: `฿${elN(ln.amount)}` }),
        el("span", { class: "small " + (ln.free ? "muted" : ln.billing === "invoice" ? "muted" : ln.paid < ln.amount ? "warn-text" : ""),
          text: (ln.free ? "ฟรี" : ln.billing === "invoice" ? "วางบิล" : ln.paid < ln.amount ? `ค้าง ฿${elN(ln.amount - ln.paid)}` : "สด") + (Number(ln.discount) ? ` · ลด ฿${elN(ln.discount)}` : "") +
            (Number(ln.waived) ? (ln.waived_reason === "factory" ? ` · โรงงาน ฿${elN(ln.waived)}` : ` · อื่นๆ ฿${elN(ln.waived)}`) : "") }),
        el("span", { class: "small muted", text: ln.old_paid ? `+หนี้เก่า ฿${elN(ln.old_paid)}` : "" })))),
    t.commission_sales != null ? el("p", { class: "small", text: `ยอดคิดค่าคอมคนขับ ฿${elN(t.commission_sales)}${t.factory_waived ? ` (รวมที่ลด / แจกเพราะน้ำแข็งเสีย ฿${elN(t.factory_waived)} — โรงงานรับ ไม่หักคนขับ)` : ""}` }) : null,
    el("p", { class: "muted small", text: `${t.discount ? `ส่วนลด ฿${elN(t.discount)} · ` : ""}ขาย ฿${elN(t.amount)} · เงินสด ฿${elN(t.cash_sales)} + หนี้เก่า ฿${elN(t.old_paid)} = ต้องมี ฿${elN(t.expected_cash)} · นับได้ ${t.counted != null ? "฿" + elN(t.counted) : "—"} · ค้าง ฿${elN(t.credit)} · วางบิล ฿${elN(t.invoiced)}${t.km != null ? ` · วิ่ง ${elN(t.km)} กม.` : ""}` }),
    day.reopen_requested_at ? el("p", { class: "warn-text", text: `คนขับขอแก้ใบขายนี้ (${new Date(day.reopen_requested_at).toLocaleString("th-TH")})` }) : null,
    (day.reopen_log || []).length ? el("p", { class: "muted small", text: "ประวัติ: " + day.reopen_log.map((x) => `${x.what === "reopened" ? "ออฟฟิศเปิดให้แก้" : "คนขับขอแก้"} ${new Date(x.at).toLocaleString("th-TH")}`).join(" · ") }) : null,
    el("div", { class: "ss-dlg-foot" },
      day.status !== "open" ? el("button", { class: "btn", type: "button", text: "เปิดให้คนขับแก้", onclick: () => ssReopen(dayId, dlg) })
        : el("span", { class: day.unsent ? "warn-text small" : "muted small", text: day.unsent ? "คนขับไม่ได้กดส่ง — ตัวเลขตามที่โทรศัพท์บันทึกไว้ล่าสุด (ยังไม่ได้นับเงิน / ไมล์)" : "คนขับยังไม่ได้ส่ง" }),
      day.status === "checked"
        ? el("button", { class: "btn", type: "button", text: "ยกเลิกการตรวจ", onclick: () => ssCheck(dayId, false, dlg) })
        : day.status === "closed" || day.unsent ? el("button", { class: "btn primary", type: "button", text: "ตรวจแล้ว ✓", onclick: () => ssCheck(dayId, true, dlg) }) : null));
  document.body.append(dlg);
  dlg.showModal();
}

async function ssWalkin(dayId, ln, dlg) {
  const P = { lod: "ใหญ่", lek: "เล็ก", mo: "บด" };
  const price = Object.entries(ln.prices || {}).map(([k, v]) => `${P[k] || k} ฿${elN(v)}`).join(" · ");
  if (!confirm(`เพิ่ม "${ln.name}" เข้ารายชื่อสายนี้ (ต่อท้ายสาย)
ราคา: ${price || "—"}
แก้ราคา / ลำดับได้ทีหลัง`)) return;
  try { await api(`/api/sales-routes/day/${dayId}/walkin`, jsonOpts("POST", { key: ln.walkin_key })); } catch (err) { alert(err.message); return; }
  dlg.close(); dlg.remove();
  ssOpenDay(dayId);
}

async function ssCheck(dayId, checked, dlg) {
  try { await api(`/api/sales-routes/day/${dayId}/check`, jsonOpts("POST", { checked })); } catch (err) { alert(err.message); return; }
  dlg.close(); dlg.remove();
  if (ss_.open) ssLoadDays(ss_.open);
  if (typeof loadSalesAttention === "function") loadSalesAttention();
}

async function ssReopen(dayId, dlg) {
  if (!confirm("เปิดใบขายนี้ให้คนขับแก้? คนขับต้องแก้แล้วกดส่งใหม่")) return;
  try { await api(`/api/sales-routes/day/${dayId}/reopen`, jsonOpts("POST", {})); } catch (err) { alert(err.message); return; }
  dlg.close(); dlg.remove();
  await openSalesSheets();
}

async function ssSaveReturns(dayId, dlg) {
  const back = {};
  dlg.querySelectorAll("input[data-ret]").forEach((i) => { if (i.value !== "") back[i.getAttribute("data-ret")] = Number(i.value); });
  try { await api(`/api/sales-routes/day/${dayId}/returns`, jsonOpts("POST", { returns: back })); } catch (err) { alert(err.message); return; }
  dlg.close(); dlg.remove();
  ssOpenDay(dayId);
  if (ss_.open) ssLoadDays(ss_.open);
}


// ------------------------------------------------------------------ ใบงานคนขับ: the paper sheet, from IceBox (app/route_sheet.py)
async function ssSheetXlsx(routeId, day) {
  let res;
  try {
    res = await fetch(`/api/sales-routes/${routeId}/sheet.xlsx${day ? `?day=${day}` : ""}`, { headers: { "X-App-Token": state.token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  } catch (err) { alert(`โหลดไม่สำเร็จ: ${err.message}`); return; }
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
  const a = el("a", { href: URL.createObjectURL(await res.blob()), download: m ? decodeURIComponent(m[1]) : "sheet.xlsx" });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

// The print view: the same sheet as HTML in a new tab, A4, black and white; the browser's print dialog saves it as PDF.
async function ssPrintSheet(routeId, day) {
  const w = window.open("", "_blank");
  if (!w) { alert("เบราว์เซอร์บล็อกหน้าต่างใหม่ — อนุญาตป๊อปอัปของเว็บนี้แล้วลองใหม่"); return; }
  w.document.write("<p style='font-family:sans-serif'>กำลังโหลด…</p>");
  let s;
  try { s = await api(`/api/sales-routes/${routeId}/sheet${day ? `?day=${day}` : ""}`); }
  catch (err) { w.document.body.textContent = `โหลดไม่สำเร็จ: ${err.message}`; return; }
  w.document.open();
  w.document.write(ssSheetHtml(s));
  w.document.close();
  w.onload = () => setTimeout(() => w.print(), 300);
}

function ssSheetHtml(s) {
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const n = (v) => (v == null || v === "" || v === 0 ? "" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 }));
  const d = s.day ? s.day.split("-").reverse().join("/") : "....................";
  const P = s.products, sm = s.summary || {};
  const cols = ["ลูกค้า", "ถัง", "ราคา", ...P.map((p) => p.label), "รวมเงิน", "เก่ายกมา", "จ่ายเก่า", "จ่ายสด", "รวมจ่าย", "ค้าง"];
  const head = `<tr class="hd"><td colspan="${cols.length}"><div class="hdgrid">
      <span>รถเบอร์ <b class="big">${esc(s.truck_no)}</b></span><span>พนักงานขับรถ <b class="big">${esc(s.driver)}</b></span><span>วันที่ <b class="big">${esc(d)}</b></span>
      <span>สาย <b class="big">${esc(s.route)}</b></span><span>เบอร์โทรศัพท์ <b class="big">${esc(s.phone)}</b></span><span></span></div></td></tr>
    <tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>`;
  const body = s.rows.map((r) => `<tr class="${r.sold ? "sold" : ""}"><td class="nm">${esc(r.name)}</td><td class="bins">${esc(r.bins).replace(/, /g, "<br>")}</td>
      <td class="c">${esc(r.price)}</td>${P.map((p) => `<td class="c">${n(r.q[p.key])}</td>`).join("")}
      <td class="r">${n(r.amount)}</td><td class="r">${n(r.owed_before)}</td><td class="r">${n(r.old_paid)}</td><td class="r">${n(r.paid)}</td>
      <td class="r">${n(r.paid_all)}</td><td class="r">${r.invoice && r.amount ? "วางบิล" : n(r.owed_after)}</td></tr>`).join("");
  const t = s.totals;
  const foot = `<tr class="tot"><td colspan="3" class="c">รวมถุง</td>${P.map((p) => `<td class="c">${n(t.q[p.key])}</td>`).join("")}<td colspan="6"></td></tr>
    <tr class="tot"><td colspan="3" class="c">รวมเงิน</td>${P.map(() => "<td></td>").join("")}<td class="r">${n(t.amount)}</td><td class="r">${n(t.owed_before)}</td>
      <td class="r">${n(t.old_paid)}</td><td class="r">${n(t.paid)}</td><td class="r">${n(t.paid_all)}</td><td class="r">${n(t.owed_after)}</td></tr>`;
  const loads = (sm.loads && sm.loads.length ? sm.loads.map((q, i) => [String(i + 1), q]) : [["1", {}], ["2", {}], ["3", {}]])
    .concat([["รวม", sm.loaded || {}], ["คืน", sm.returned || {}], ["ขายได้", sm.sold || {}], ["ขาด(-) / เกิน(+)", sm.diff || {}]]);
  const stock = `<table class="box"><tr><th>รับ</th>${P.map((p) => `<th>${p.label}</th>`).join("")}</tr>${loads.map(([l, q]) =>
    `<tr><td class="c">${l}</td>${P.map((p) => `<td class="c">${n(q[p.key])}</td>`).join("")}</tr>`).join("")}</table>`;
  const money = [["รวมขาย", sm.amount], ["วางบิล", sm.invoiced], ["ค้าง (ขายเชื่อ)", sm.credit], ["ขายสด", sm.cash_sales], ["จ่ายเก่า", sm.old_paid],
    ["รวมรับเงิน", sm.expected_cash], ["ค้างยกมา", sm.owed_before], ["ค้างยกไป", sm.owed_after]];
  const notes = [1000, 500, 100, 50, 20].map((v) => [`${v.toLocaleString()} =`, (sm.notes || {})[String(v)]]).concat([["เหรียญ =", sm.coins], ["รวมเงิน", sm.counted]]);
  const kv = (list, cls) => `<table class="box ${cls}">${list.map(([l, v]) => `<tr><td>${l}</td><td class="r b">${n(v)}</td></tr>`).join("")}</table>`;
  return `<!doctype html><html lang="th"><head><meta charset="utf-8"><title>ใบงานคนขับ รถ ${esc(s.truck_no)} ${esc(d)}</title>
<link rel="stylesheet" href="/static/fonts/fonts.css"><style>
@page { size: A4 portrait; margin: 8mm; }
* { box-sizing: border-box; }
body { font-family: "Noto Sans Thai", Tahoma, sans-serif; color: #000; background: #fff; margin: 0; font-size: 10.5px; }
table { border-collapse: collapse; width: 100%; }
.main { table-layout: fixed; }
.main td { overflow-wrap: anywhere; }
.main th, .main td, .box th, .box td { border: 1px solid #000; padding: 2px 3px; }
.main th { font-size: 10px; font-weight: 700; }
.main thead .hd td { border: none; padding: 0 0 4px; }
.hdgrid { display: grid; grid-template-columns: 1.1fr 1.6fr 1fr; gap: 2px 10px; font-size: 11px; }
.big { font-size: 14px; }
.nm { font-size: 10.5px; } tr.sold .nm { font-weight: 700; }
.bins { font-size: 8px; text-align: center; line-height: 1.15; }
.c { text-align: center; } .r { text-align: right; } .b { font-weight: 700; }
tr { page-break-inside: avoid; }
.tot td { font-weight: 700; }
.under { display: grid; grid-template-columns: 1.5fr 1fr .8fr; gap: 10px; margin-top: 10px; page-break-inside: avoid; }
.km { display: grid; grid-template-columns: repeat(5, 1fr); gap: 0; margin-top: 10px; border: 1px solid #000; page-break-inside: avoid; }
.km span { border-right: 1px solid #000; padding: 6px 4px; } .km span:last-child { border-right: 0; }
.flags { margin-top: 6px; font-size: 9.5px; }
.bar { position: sticky; top: 0; background: #f3f3f3; padding: 8px; display: flex; gap: 8px; align-items: center; font-size: 13px; }
.bar button { font: inherit; padding: 4px 12px; }
@media print { .bar { display: none; } }
</style></head><body>
<div class="bar"><button onclick="print()">พิมพ์ / บันทึกเป็น PDF</button><span>เลือกเครื่องพิมพ์ “Save as PDF” เพื่อบันทึกเป็นไฟล์ · กระดาษ A4 แนวตั้ง</span></div>
<table class="main"><colgroup><col style="width:23%"><col style="width:9%"><col style="width:7%">${P.map(() => '<col style="width:4.6%">').join("")}<col style="width:7%"><col style="width:6.2%"><col style="width:6.2%"><col style="width:6.2%"><col style="width:6.2%"><col style="width:6.2%"></colgroup><thead>${head}</thead><tbody>${body}${foot}</tbody></table>
<div class="under">${stock}${kv(money, "money")}${kv(notes, "notes")}</div>
<div class="km"><span>ไมล์ใหม่ <b>${n(sm.km_end)}</b></span><span>ไมล์เก่า <b>${n(sm.km_start)}</b></span><span>รถวิ่ง <b>${n(sm.km)}</b> กม.</span><span>ตรวจ</span><span>ตรวจ</span></div>
${(sm.flags || []).length ? `<div class="flags">ที่ไม่ตรง: ${sm.flags.map(esc).join(" · ")}</div>` : ""}
</body></html>`;
}
