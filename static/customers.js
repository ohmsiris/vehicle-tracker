"use strict";

// ทะเบียนลูกค้า (owner, 10 Nov 2026): the bin-loan register from the Drive "Customer Info" sheet, each row linked to the route
// customer it is, with its ID card / form photos. The ones the import could not link for sure wait under "รอตรวจ" with suggestions.
// ID-card numbers and photos only on one customer's own dialog. Server: app/customers.py.

const cu_ = { data: null, filter: "all", q: "", photoUrls: [] };

async function openCustomers() {
  showView("customers");
  showErr("cuError", "");
  fill($("cuBody"), el("p", { class: "muted", text: "กำลังโหลด…" }));
  try { cu_.data = await api("/api/customers"); } catch (err) { fill($("cuBody"), null); showErr("cuError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  if (!cu_.data.review && cu_.filter === "review") cu_.filter = "all";
  renderCustomers();
}

const cuBins = (bins) => (bins || []).map((b) => `${b.size}-${b.no}`).join(", ");
const cuWho = (c) => (c ? `รถ ${c.truck_no} · ${c.seq}. ${c.name}` : "");

function renderCustomers() {
  const d = cu_.data;
  const q = cu_.q.trim().toLowerCase();
  const rows = d.rows.filter((r) => (cu_.filter === "review" ? !r.customer_id : cu_.filter === "check" ? r.check : true) &&
    (!q || `${r.cust_no} ${r.shop || ""} ${r.contact_name || ""} ${r.truck_no || ""} ${cuBins(r.bins)} ${cuWho(r.customer)}`.toLowerCase().includes(q)));
  const tabs = el("div", { class: "seg" },
    el("button", { type: "button", class: `segbtn${cu_.filter === "all" ? " active" : ""}`, text: `ทั้งหมด ${d.rows.length}`, onclick: () => { cu_.filter = "all"; renderCustomers(); } }),
    el("button", { type: "button", class: `segbtn${cu_.filter === "check" ? " active" : ""}`, text: `ชื่อไม่ตรง ${d.check}`, onclick: () => { cu_.filter = "check"; renderCustomers(); } }),
    el("button", { type: "button", class: `segbtn${cu_.filter === "review" ? " active" : ""}`, text: `ยังไม่จับคู่ ${d.review}`, onclick: () => { cu_.filter = "review"; renderCustomers(); } }));
  const search = el("input", { type: "search", class: "cu-search", placeholder: "ค้นหา ชื่อร้าน / ชื่อลูกค้า / เลขถัง / รถ", value: cu_.q,
    oninput: (e) => { cu_.q = e.target.value; const pos = e.target.selectionStart; renderCustomers(); const s = document.querySelector(".cu-search"); s.focus(); s.setSelectionRange(pos, pos); } });
  const table = el("table", { class: "cu-table" },
    el("thead", {}, el("tr", {}, ...["เลขที่", "ร้าน / ลูกค้า", "รถ", "ถังที่ยืม", "ในรายชื่อสาย", "รูป"].map((h) => el("th", { text: h })))),
    el("tbody", {}, ...rows.map((r) => el("tr", { class: r.customer_id && !r.check ? "" : "cu-review", onclick: () => cuOpen(r.cust_no) },
      el("td", { text: String(r.cust_no) }),
      el("td", {}, el("b", { text: r.shop && r.shop !== "บุคคล" ? r.shop : (r.contact_name || "—") }),
        r.shop && r.shop !== "บุคคล" && r.contact_name ? el("span", { class: "muted small", text: ` · ${r.contact_name}` }) : null),
      el("td", { text: r.truck_no || "" }),
      el("td", { class: "small", text: cuBins(r.bins) }),
      el("td", {}, r.customer ? el("span", { class: "small" }, r.check ? el("span", { class: "badge warn", text: "ชื่อไม่ตรง — ตรวจ" }) : null, ` ${cuWho(r.customer)}`)
        : el("span", { class: "badge warn", text: r.suggest.length ? `รอตรวจ · มีที่น่าจะใช่ ${r.suggest.length}` : "รอตรวจ · ยังไม่อยู่ในรายชื่อ" })),
      el("td", { text: r.photos ? `${r.photos}` : "" })))));
  fill($("cuBody"),
    el("div", { class: "card cu-tiles" },
      el("div", {}, el("b", { class: "cu-big", text: String(d.rows.length) }), el("span", { class: "muted small", text: "ลูกค้าในทะเบียน (ยืมถัง)" })),
      el("div", {}, el("b", { class: "cu-big", text: String(d.linked - d.check) }), el("span", { class: "muted small", text: "จับคู่แล้ว ชื่อตรง" })),
      el("div", {}, el("b", { class: "cu-big warn-text", text: String(d.check) }), el("span", { class: "muted small", text: "ถังตรงแต่ชื่อไม่ตรง — ถังอาจย้ายร้าน ต้องตรวจ" })),
      el("div", {}, el("b", { class: "cu-big warn-text", text: String(d.review) }), el("span", { class: "muted small", text: "ยังไม่จับคู่ — กดเพื่อเลือกร้าน" }))),
    el("div", { class: "cu-tools" }, tabs, search),
    rows.length ? table : el("p", { class: "muted", text: "ไม่พบ" }));
}

function cuFreePhotos() { cu_.photoUrls.forEach((u) => URL.revokeObjectURL(u)); cu_.photoUrls = []; }

async function cuPhoto(id) {
  const res = await fetch(`/api/customers/photo/${id}`, { headers: { "X-App-Token": state.token } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const u = URL.createObjectURL(await res.blob());
  cu_.photoUrls.push(u);
  return u;
}

async function cuOpen(no) {
  let r;
  try { r = await api(`/api/customers/${no}`); } catch (err) { alert(err.message); return; }
  const row = cu_.data.rows.find((x) => x.cust_no === no) || {};
  const close = () => { dlg.close(); dlg.remove(); cuFreePhotos(); };
  const field = (label, value) => (value ? el("div", { class: "cu-f" }, el("span", { class: "muted small", text: label }), el("span", { text: value })) : null);
  const photos = el("div", { class: "cu-photos" }, ...(r.photos.length ? r.photos.map((p) => {
    const box = el("a", { class: "cu-photo", href: "#", title: p.name || "", onclick: async (e) => {
      e.preventDefault();
      try { window.open(await cuPhoto(p.id), "_blank"); } catch (err) { alert(err.message); }
    } }, p.mime_type === "application/pdf" ? el("span", { text: "PDF" }) : el("span", { class: "muted small", text: "กำลังโหลดรูป…" }));
    if (p.mime_type !== "application/pdf") cuPhoto(p.id).then((u) => box.replaceChildren(el("img", { src: u, alt: p.name || "รูป" }))).catch(() => box.replaceChildren(el("span", { class: "error small", text: "โหลดรูปไม่ได้" })));
    return box;
  }) : [el("p", { class: "muted small", text: "ไม่มีรูปในโฟลเดอร์ Drive ของลูกค้านี้" })]));
  const linkBox = el("div", { class: "cu-link" });
  const drawLink = () => {
    const sug = (row.suggest || []).filter((s) => !r.customer || s.id !== r.customer.id);
    fill(linkBox,
      el("h4", { text: "เป็นร้านไหนในรายชื่อสาย" }),
      r.check ? el("p", { class: "warn-text small", text: `ชื่อในทะเบียน “${r.shop && r.shop !== "บุคคล" ? r.shop : r.contact_name || "—"}” ไม่ตรงกับรายชื่อสาย “${r.customer.name}” — ถังอาจย้ายร้าน ดูรูปแล้วยืนยัน หรือเลือกร้านที่ถูก` }) : null,
      r.check ? el("button", { class: "btn small primary", type: "button", text: "ใช่ร้านเดียวกัน ✓", onclick: () => cuLink(no, r.customer.id, close) }) : null,
      r.customer ? el("p", {}, el("b", { text: cuWho(r.customer) }), el("span", { class: "muted small", text: ` · ถัง ${r.customer.bins || "—"} · จับคู่โดย ${{ "truck+bin": "รถ + เลขถัง", "truck+bin+name": "รถ + เลขถัง + ชื่อ", name: "ชื่อร้าน", "bin+name": "เลขถัง + ชื่อ (ย้ายรถ)", manual: "เลือกเอง" }[r.match] || r.match}` }))
        : el("p", { class: "warn-text", text: "ยังไม่ได้จับคู่ — เลือกด้านล่าง" }),
      sug.length ? el("div", { class: "cu-sug" }, el("span", { class: "small", text: "น่าจะเป็น:" }), ...sug.map((s) =>
        el("button", { class: "btn small", type: "button", text: `${cuWho(s)} (ถัง ${s.bins || "—"})`, onclick: () => cuLink(no, s.id, close) }))) : null,
      el("div", { class: "cu-pick" },
        el("button", { class: "btn small", type: "button", text: `เลือกจากรายชื่อรถ ${r.truck_no || ""}`, onclick: () => cuPickFrom(no, r.truck_no, linkBox, close) }),
        r.customer ? el("button", { class: "btn small", type: "button", text: "ยกเลิกการจับคู่", onclick: () => cuLink(no, null, close) }) : null));
  };
  drawLink();
  const gps = r.gps ? el("a", { href: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(r.gps)}`, target: "_blank", rel: "noopener", text: "เปิดแผนที่" }) : null;
  const dlg = el("dialog", { class: "ss-dialog cu-dialog" },
    el("div", { class: "ss-dlg-head" }, el("h3", { text: `#${r.cust_no} · ${r.shop && r.shop !== "บุคคล" ? r.shop : r.contact_name || ""}` }),
      el("button", { class: "btn small", type: "button", text: "ปิด", onclick: close })),
    el("div", { class: "cu-fields" },
      field("ชื่อลูกค้า", r.contact_name), field("เบอร์โทร", r.phone), field("เลขบัตรประชาชน", r.id_card),
      field("ยืมถังวันที่", r.loan_date ? r.loan_date.split("-").reverse().join("/") : ""), field("ถังที่ยืม", cuBins(r.bins)),
      field("คนขับ / รถ", [r.driver_name, r.truck_no ? `รถ ${r.truck_no}` : ""].filter(Boolean).join(" · ")),
      r.gps ? el("div", { class: "cu-f" }, el("span", { class: "muted small", text: "พิกัด" }), el("span", {}, `${r.gps} `, gps)) : null,
      field("หมายเหตุ", r.note)),
    linkBox,
    el("h4", { text: `รูป (${r.photos.length})` }), photos);
  dlg.addEventListener("close", cuFreePhotos);
  document.body.append(dlg);
  dlg.showModal();
}

async function cuPickFrom(no, truck, box, close) {
  let list;
  try { list = (await api(`/api/customers/route/${encodeURIComponent(truck || "")}`)).customers; } catch (err) { alert(err.message); return; }
  const sel = el("select", { class: "cu-select" }, el("option", { value: "", text: list.length ? "— เลือกร้าน —" : "ไม่พบรายชื่อของรถนี้" }),
    ...list.map((c) => el("option", { value: c.id, text: `${c.seq}. ${c.name}${c.bins ? ` (ถัง ${c.bins})` : ""}` })));
  box.append(el("div", { class: "cu-pick" }, sel, el("button", { class: "btn small primary", type: "button", text: "ใช่ร้านนี้",
    onclick: () => { if (sel.value) cuLink(no, Number(sel.value), close); } })));
}

async function cuLink(no, customerId, close) {
  try { await api(`/api/customers/${no}/link`, jsonOpts("POST", { customer_id: customerId })); } catch (err) { alert(err.message); return; }
  close();
  openCustomers();
}
