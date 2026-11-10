"use strict";

// สายรถ · ใบขาย (#sales-sheets) -- the office side of the drivers' phone sheet (app/route_sales.py; the drivers use /d/).
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
          r.new_customers ? el("span", { class: "badge warn", text: `ลูกค้าใหม่รอตรวจ ${r.new_customers}` }) : null),
        el("span", { class: `badge ${r.pin_set ? "ok" : ""}`, text: r.pin_set ? "ตั้งรหัสแล้ว" : "ยังไม่ได้ตั้งรหัส" }),
        last ? el("span", { class: "muted small", text: `ล่าสุด ${beDate(last.day)} · ฿${elN((last.totals || {}).amount || 0)}${flags ? ` · ⚠ ${flags}` : ""}` }) : null),
      el("div", { class: "ss-actions" },
        el("button", { class: "btn small primary", type: "button", text: r.pin_set ? "ตั้งรหัสใหม่" : "ตั้งรหัส", onclick: () => ssSetPin(r) }),
        r.pin_set ? el("button", { class: "btn small", type: "button", text: "ออกจากระบบทุกเครื่อง", onclick: () => ssSignOut(r) }) : null,
        el("button", { class: "btn small", type: "button", text: ss_.open === r.id ? "ซ่อนรายวัน" : "ดูรายวัน", onclick: () => ssToggle(r.id) })),
      ss_.open === r.id ? el("div", { id: "ssDays" }, el("p", { class: "muted small", text: "กำลังโหลด…" })) : null);
  });
  fill($("ssBody"), link, ...rows);
  if (ss_.open) ssLoadDays(ss_.open);
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
      const st = ST[x.status] || [x.status, ""];
      return el("button", { class: "ss-day", type: "button", onclick: () => ssOpenDay(x.id) },
        el("span", { text: beDate(x.day) }), el("span", {}, el("span", { class: `badge ${st[1]}`, text: st[0] })),
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
    el("div", { class: "ss-dlg-head" }, el("h3", { text: `ใบขาย ${beDate(day.day)}` }), el("button", { class: "btn small", type: "button", text: "ปิด", onclick: () => { dlg.close(); dlg.remove(); } })),
    (t.flags || []).length ? el("p", { class: "warn-text", text: `⚠ ${t.flags.join(" · ")}` }) : el("p", { class: "muted small", text: "ทุกอย่างตรง" }),
    el("div", { class: "ss-stock" },
      el("div", { class: "ss-srow head muted small" }, el("span", { text: "" }), ...["รับ", "ขาย", "ควรเหลือ", "คืน"].map((h) => el("span", { text: h }))),
      ...used.map((p) => el("div", { class: "ss-srow" }, el("b", { text: p.label }),
        el("span", { text: elN(t.loaded[p.key]) }), el("span", { text: elN(t.sold[p.key]) }), el("span", { text: elN(t.left[p.key]) }),
        el("span", { text: t.returned ? elN(t.returned[p.key]) : "—" })))),
    el("div", { class: "ss-lines" },
      ...d.lines.map((ln) => el("div", { class: "ss-line" },
        el("span", { text: `${ln.seq || ""}. ${ln.name || "?"}${ln.approved === false ? " (ใหม่)" : ""}` }),
        el("span", { class: "muted small", text: P.filter((p) => ln.qty[p.key]).map((p) => `${p.label} ${elN(ln.qty[p.key])}`).join(" · ") }),
        el("b", { text: `฿${elN(ln.amount)}` }),
        el("span", { class: "small " + (ln.billing === "invoice" ? "muted" : ln.paid < ln.amount ? "warn-text" : ""), text: ln.billing === "invoice" ? "วางบิล" : ln.paid < ln.amount ? `ค้าง ฿${elN(ln.amount - ln.paid)}` : "สด" }),
        el("span", { class: "small muted", text: ln.old_paid ? `+หนี้เก่า ฿${elN(ln.old_paid)}` : "" })))),
    el("p", { class: "muted small", text: `ขาย ฿${elN(t.amount)} · เงินสด ฿${elN(t.cash_sales)} + หนี้เก่า ฿${elN(t.old_paid)} = ต้องมี ฿${elN(t.expected_cash)} · นับได้ ${t.counted != null ? "฿" + elN(t.counted) : "—"} · ค้าง ฿${elN(t.credit)} · วางบิล ฿${elN(t.invoiced)}${t.km != null ? ` · วิ่ง ${elN(t.km)} กม.` : ""}` }),
    el("div", { class: "ss-dlg-foot" },
      day.status === "checked"
        ? el("button", { class: "btn", type: "button", text: "ยกเลิกการตรวจ (ให้คนขับแก้ได้)", onclick: () => ssCheck(dayId, false, dlg) })
        : el("button", { class: "btn primary", type: "button", text: "ตรวจแล้ว ✓", onclick: () => ssCheck(dayId, true, dlg) })));
  document.body.append(dlg);
  dlg.showModal();
}

async function ssCheck(dayId, checked, dlg) {
  try { await api(`/api/sales-routes/day/${dayId}/check`, jsonOpts("POST", { checked })); } catch (err) { alert(err.message); return; }
  dlg.close(); dlg.remove();
  if (ss_.open) ssLoadDays(ss_.open);
}
