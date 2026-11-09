"""
ไฟฟ้า: PEA bills, kept with every detail (demand kW, peak / off-peak / holiday kWh, Ft, power-factor charge ...) -- schema_v46 elec_bills.

* A PEA PDF uploaded on the electricity row of the checklist is read exactly (app/pea_bill.py, no AI) and saved here.
* It books the cost (accrual, the month used, dated the 25th): Dr 5100 electricity (sub total before VAT) + 1400 Input VAT, Cr 2100
  accounts payable -- the HQ cheque that pays it later clears 2100 (acc_cheques matches the cheque to the bill by amount).
* summary(): per meter, month by month, with what each month cost and why (demand, peak share, power factor).
Meters are named by their address on the bill (Saraburi: 72 and 72/9, ม.8 ต.ตลิ่งชัน; Kaeng Khoi: 150, ม.8 ต.บ้านป่า).
"""
from datetime import date
from typing import Optional

from app import pea_bill, supabase

FIELDS = ("ca_no", "meter_no", "address", "invoice_no", "bill_period", "read_date", "tariff", "multiplier", "demand_peak_kw", "demand_offpeak_kw",
          "demand_holiday_kw", "kwh_peak", "kwh_offpeak", "kwh_holiday", "kwh_total", "kvar_max", "demand_rate", "demand_charge", "rate_peak",
          "rate_offpeak", "energy_peak", "energy_offpeak", "service_charge", "pf_charge", "base_amount", "ft_rate", "ft_amount", "discount",
          "subtotal", "vat", "total", "estimated", "deposit_interest")
PF_LIMIT = 0.6197          # PEA: kVAR above 61.97% of the month's highest kW (power factor below 0.85) is charged


def entry_for(bill: dict, branch: str) -> dict:
    """The accrual entry of one bill: dated the 25th of the month used."""
    y, m = (int(x) for x in bill["bill_period"].split("-"))
    meter = f"มิเตอร์ {bill.get('address') or bill['ca_no']}"
    net = round(bill["subtotal"], 2)
    interest = round(float(bill.get("deposit_interest") or 0), 2)
    lines = [{"account": "5100", "debit": net, "credit": 0.0, "branch": branch, "vehicle_id": None, "machine_id": None, "memo": None},
             {"account": "1400", "debit": round(bill["vat"], 2), "credit": 0.0, "branch": branch, "vehicle_id": None, "machine_id": None, "memo": None},
             {"account": "2100", "debit": 0.0, "credit": round(bill["total"] - interest, 2), "branch": branch, "vehicle_id": None, "machine_id": None, "memo": None}]
    if interest:      # PEA paid interest on the deposit by taking it off this bill: other income, and less to pay
        lines.append({"account": "4900", "debit": 0.0, "credit": interest, "branch": branch, "vehicle_id": None, "machine_id": None,
                      "memo": "ผลประโยชน์เงินประกันการใช้ไฟ"})
    desc = (f"ค่าไฟ {meter} เดือน {m:02d}/{y + 543} · {bill['kwh_total']:,.0f} หน่วย · demand {bill['demand_peak_kw']:,.1f} kW"
            + (f" · ค่า power factor {bill['pf_charge']:,.2f}" if bill.get("pf_charge") else "")
            + (" · PEA ประมาณหน่วย (ไม่ได้อ่านมิเตอร์)" if bill.get("estimated") else ""))
    return {"entry_date": date(y, m, 25).isoformat(), "branch": branch, "source": "electricity", "source_id": f"pea:{bill['invoice_no']}",
            "party": f"PEA {meter}", "description": desc, "doc_type": "tax_invoice", "doc_no": bill["invoice_no"], "lines": lines}


def save_bill(bill: dict, branch: str, file_id: Optional[str] = None) -> dict:
    """Store one read bill and book it (a bill already saved is replaced, so re-uploading is harmless)."""
    from app.accounting import _insert_entries
    row = {k: (bill[k].isoformat() if isinstance(bill.get(k), date) else bill.get(k)) for k in FIELDS} | {"branch": branch, "file_id": file_id}
    supabase.delete("elec_bills", {"invoice_no": f"eq.{bill['invoice_no']}"})
    supabase.insert("elec_bills", [row])
    supabase.delete("acc_entries", {"source_id": f"eq.pea:{bill['invoice_no']}"})
    _insert_entries([entry_for(bill, branch)])
    return {"invoice_no": bill["invoice_no"], "meter": bill.get("address"), "period": bill["bill_period"], "total": bill["total"]}


def read_upload(name: str, data: bytes, branch: str, file_id: str) -> Optional[dict]:
    """Called for each file uploaded on the electricity row: a PEA PDF is read and saved; a PEA AMR report (.xls, 15-minute kWh) is saved
    to the meter's readings (the branch comes from the meter's bills when there are any); anything else is just kept."""
    if name.lower().endswith((".xls", ".htm", ".html")):
        from app import amr
        try:
            rep = amr.parse(data.decode("utf-8", errors="replace"))
        except amr.NotAmr:
            return None
        if not rep["month"]:
            return None
        known = supabase.select("elec_bills", {"select": "branch", "ca_no": f"eq.{rep['ca_no']}", "limit": "1"})
        return amr.save(rep, known[0]["branch"] if known else branch)
    if not name.lower().endswith(".pdf"):
        return None
    try:
        bill = pea_bill.parse_pdf(data)
    except pea_bill.NotPeaBill:
        return None
    return save_bill(bill, branch, file_id)


def month_view(b: dict) -> dict:
    """One bill with what it means: cost per kWh, peak share, what demand and power factor cost, how far over the kVAR limit."""
    kw_max = max(b.get("demand_peak_kw") or 0, b.get("demand_offpeak_kw") or 0, b.get("demand_holiday_kw") or 0)
    kwh = float(b.get("kwh_total") or 0)
    total = float(b["total"])
    peak = float(b.get("kwh_peak") or 0)
    limit = round(kw_max * PF_LIMIT, 1) if kw_max else None
    shift_saving = round(peak * 0.1 * (float(b.get("rate_peak") or 0) - float(b.get("rate_offpeak") or 0)), 0)
    text = {"estimated", "ca_no", "meter_no", "address", "invoice_no", "bill_period", "read_date", "tariff", "branch", "id", "file_id", "created_at", "due_date"}
    return {**{k: (b[k] if k in text or b[k] is None else float(b[k])) for k in b},
            "baht_per_kwh": round(total / kwh, 3) if kwh else None,
            "peak_share": round(peak / kwh, 3) if kwh else None,
            "kvar_limit": limit, "kvar_over": round(float(b.get("kvar_max") or 0) - limit, 1) if limit is not None else None,
            "shift10_saving": shift_saving}


def summary(branch: str = "", months: int = 12) -> list[dict]:
    """Per meter, newest month first."""
    params = {"select": "*", "order": "bill_period.desc,ca_no.asc"}
    if branch:
        params["branch"] = f"eq.{branch}"
    rows = supabase.select_all("elec_bills", params)
    meters: dict = {}
    for r in rows:
        m = meters.setdefault(r["ca_no"], {"ca_no": r["ca_no"], "meter": r.get("address") or r["ca_no"], "branch": r["branch"], "months": []})
        if len(m["months"]) < months:
            m["months"].append(month_view(r))
    return sorted(meters.values(), key=lambda m: (m["branch"], m["meter"]))
