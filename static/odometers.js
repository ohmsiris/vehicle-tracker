"use strict";

// รถ › จดเลขไมล์ (#odometers): every truck's odometer typed in one go -- monthly around the 16th (the owner notes them down when the
// mid-month advance is paid on the 17th). The plan goes by km first, so fresh readings keep its dates right. app/service_plan.py.
// Each row: the latest figure, what it should read about now (latest + km per day), the box; a figure lower than the last is refused,
// one far above the estimate is flagged yellow (check it, or the truck simply drove more).

const odo = { data: null, values: {} };

async function openOdometers() {
  showView("odometers");
  showErr("odoError", "");
  try { odo.data = await api("/api/plan/odometer-sheet"); } catch (err) { showErr("odoError", `โหลดไม่สำเร็จ: ${err.message}`); return; }
  if (!$("odoDate").value) $("odoDate").value = odo.data.today;
  if (!odo.wired) {
    odo.wired = true;
    bindDateField($("odoDate"), $("odoDateCap"));
    $("odoSave").onclick = saveOdometers;
    $("odoBranch").onchange = renderOdometers;
  }
  const branches = [...new Set(odo.data.trucks.map((t) => t.branch).filter(Boolean))];
  const keep = $("odoBranch").value;
  $("odoBranch").replaceChildren(el("option", { value: "", text: "ทุกสาขา" }), ...branches.map((b) => el("option", { value: b, text: b })));
  $("odoBranch").value = branches.includes(keep) ? keep : "";
  renderOdometers();
}

function odoCheck(t, raw) {
  const km = Number(String(raw).replace(/,/g, ""));
  if (!raw || !km) return { cls: "", text: "" };
  const last = t.odometer;
  if (last && km < last.km) return { cls: "bad", text: `น้อยกว่าครั้งก่อน (${baht(last.km)}) — พิมพ์ผิด?` };
  if (!last) return { cls: "", text: "เลขไมล์แรกของรถคันนี้" };
  const days = Math.max(1, Math.round((new Date(`${$("odoDate").value || odo.data.today}T00:00:00`) - new Date(`${last.date}T00:00:00`)) / 86400000));
  if (days < 7) return { cls: "", text: `+${baht(km - last.km)} กม. จากครั้งก่อน (${beDate(last.date)})` };      // too close to judge a daily rate
  const per = (km - last.km) / days;
  if (per > 1000) return { cls: "bad", text: `${baht(Math.round(per))} กม./วัน — เป็นไปไม่ได้ ตรวจอีกที` };
  if (t.expected && km > t.expected * 1.08 && km - t.expected > 1500) return { cls: "warn", text: `+${baht(km - last.km)} กม. (${Math.round(per)} กม./วัน) มากกว่าที่คาด — ตรวจอีกที` };
  return { cls: "ok", text: `+${baht(km - last.km)} กม. ใน ${days} วัน (${Math.round(per)} กม./วัน)` };
}

function renderOdometers() {
  const b = $("odoBranch").value;
  const trucks = odo.data.trucks.filter((t) => !b || t.branch === b);
  fill($("odoList"), ...trucks.map((t) => {
    const input = el("input", { type: "text", inputmode: "numeric", placeholder: t.odometer_broken ? "ไมล์เสีย" : t.expected ? `≈ ${baht(t.expected)}` : "เลขไมล์", value: odo.values[t.id] || "", disabled: t.odometer_broken });
    const hint = el("span", { class: "small" });
    const paint = () => { const c = odoCheck(t, input.value); hint.className = `small odo-hint ${c.cls}`; hint.textContent = c.text; };
    input.addEventListener("input", () => { odo.values[t.id] = input.value; paint(); odoCount(); });
    paint();
    return el("div", { class: `odo-row${t.odometer_broken ? " broken" : ""}` },
      el("a", { href: `#truck=${t.id}`, class: "odo-name" }, el("strong", { text: t.label }), el("span", { class: "muted small", text: ` ${t.branch || ""}` })),
      el("span", { class: "muted small", text: t.odometer_broken ? "ไมล์เสีย — นับตามเวลา" : t.odometer ? `ล่าสุด ${baht(t.odometer.km)} · ${beDate(t.odometer.date)}` : "ยังไม่มีเลขไมล์" }),
      input, hint);
  }));
  odoCount();
}

function odoCount() {
  const n = Object.values(odo.values).filter((v) => String(v).trim()).length;
  $("odoSave").textContent = n ? `บันทึกเลขไมล์ ${n} คัน` : "บันทึกเลขไมล์";
  $("odoSave").disabled = !n;
}

async function saveOdometers() {
  const readings = Object.entries(odo.values).filter(([, v]) => String(v).trim()).map(([vehicle_id, km]) => ({ vehicle_id, km: String(km).replace(/,/g, "") }));
  $("odoSave").disabled = true;
  showErr("odoError", "");
  try {
    const res = await api("/api/plan/readings/bulk", jsonOpts("POST", { date: $("odoDate").value, readings }));
    odo.data = res;
    odo.values = {};
    renderOdometers();
    $("odoDone").textContent = `บันทึกแล้ว ${res.saved} คัน ✓`;
    $("odoDone").hidden = false;
  } catch (err) {
    showErr("odoError", `บันทึกไม่สำเร็จ: ${err.message}`);
    odoCount();
  }
}
