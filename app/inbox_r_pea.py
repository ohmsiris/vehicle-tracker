"""
กล่องรับไฟล์ › ค่าไฟฟ้า PEA: two kinds of file from the Provincial Electricity Authority, one reader (contract: app/inbox_common.py).

1. The monthly bill ("Smart Invoice" PDF from PEA e-Service): read exactly by app/pea_bill.parse_pdf (no AI; a bill whose parts do
   not add up is refused) and stored by app/electricity.save_bill, which replaces an earlier copy of the same invoice and books
   Dr 5100 + 1400 VAT / Cr 2100 owed to PEA, dated the 25th of the month used (electricity.entry_for). One batch per bill,
   key "pea:<invoice no.>" -- the same source_id the electricity page has always used, so a bill uploaded on the old checklist row
   and the same bill dropped here are one bill, never two.
   * Month: the bill period printed on the bill (PEA reads the meter on the last day of the month; "10/2569" = October's power).
   * Branch: a meter keeps its branch (owner, 24 Nov 2026 -- Kaeng Khoi's meter 150 was once uploaded on the Saraburi row and its
     2.2M booked to Saraburi): the branch of the meter's earlier bills (electricity.meter_branch). A meter never seen before takes
     the branch its address says (150 ม.8 ต.บ้านป่า อ.แก่งคอย -> แก่งคอย; 72, 72/9 ม.8 ต.ตลิ่งชัน 18000 -> สระบุรี); with neither,
     the owner is asked. If the address and the meter's history disagree, that is a conflict too.
   * Booking it asks the engine to re-book the cheque run of the FOLLOWING month (the run on the ~20th pays last month's bill): a
     cheque run booked before this bill was stored booked its PEA row as a 5100 cost; re-read now, that row pays this bill (Dr 2100).
   * Unbook: the elec_bills row and its pea:<invoice> entry.
2. The AMR report ("kWh<CA><time>.xls", really an HTML page; 15-minute kWh per meter): app/amr.parse / amr.save into
   elec_amr_readings (upsert per slot, so loading a month again replaces it). It is not money: no ledger rows, no batches.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from app import amr, electricity, pea_bill
from app.inbox_common import BRANCHES, Dropped, empty_preview, shift_month

KIND = "pea_bill"
LABEL = "ค่าไฟฟ้า PEA (ใบแจ้ง / รายงาน AMR)"
FEEDS = ("electricity",)
AMR_MARK = "กิโลวัตต์ชั่วโมง"


# ------------------------------------------------------------------ sniff
def _html(f: Dropped) -> str:
    if f.ext == ".pdf" or f.is_scan:
        return ""
    head = f.data[:400000]
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError:
        return head.decode("utf-8", errors="replace")


def is_amr(f: Dropped) -> bool:
    text = _html(f)
    return AMR_MARK in text and ("AMR" in text or "บัญชีผู้ใช้ไฟ" in text)


def sniff(f: Dropped) -> float:
    """A PEA bill: the PDF's text layer says Smart Invoice and has the bill's labels; one that pea_bill reads to the satang is sure.
    An AMR report: an HTML page (named .xls) with PEA's kWh table."""
    if f.ext == ".pdf":
        text = f.text or ""
        if "Smart Invoice" not in text:
            return 0.0
        marks = sum(m in text for m in ("Total Based Amount", "Bill Period", "CA/Ref", "Invoice no."))
        try:
            pea_bill.parse_pdf(f.data)
            return 0.98
        except pea_bill.NotPeaBill:
            return 0.5 + 0.05 * marks
    if is_amr(f):
        return 0.95
    return 0.0


# ------------------------------------------------------------------ branch (pure parts)
def address_branch(text: str) -> Optional[str]:
    """The branch the address on the bill names ('สถานที่ใช้ไฟฟ้า 150 ม.8 ต.บ้านป่า อ.แก่งคอย จ.สระบุรี 18110'). Only the line of the
    place the power is used -- the PEA office line ('การไฟฟ้าส่วนภูมิภาคอำเภอแก่งคอย') serves both branches."""
    line = next((ln for ln in (text or "").splitlines() if "สถานที่ใช้ไฟ" in ln), "")
    if not line:
        line = next((ln for ln in (text or "").splitlines() if re.search(r"\b18\d{3}\b", ln) and "โทร" not in ln), "")
    if "แก่งคอย" in line or "บ้านป่า" in line or "18110" in line:
        return "แก่งคอย"
    if "ตลิ่งชัน" in line or "18000" in line or "เมืองสระบุรี" in line:
        return "สระบุรี"
    return None


def _history_branch(ca_no: Optional[str], invoice_no: Optional[str]) -> Optional[str]:
    """The branch of the meter's earlier bills (electricity.meter_branch with no fallback); None for a meter never seen."""
    if not ca_no:
        return None
    if invoice_no:          # not the bill's own earlier copy
        return electricity.meter_branch({"ca_no": ca_no, "invoice_no": invoice_no}, "") or None
    from app import supabase
    known = supabase.select("elec_bills", {"select": "branch", "ca_no": f"eq.{ca_no}", "order": "bill_period.desc", "limit": "1"})
    return known[0]["branch"] if known and known[0].get("branch") else None


def _branch_choice(signals: list[dict], choices: dict) -> tuple[Optional[str], list[dict]]:
    """History first (a meter keeps its branch), then the address; disagreement or no clue at all = ask the owner."""
    if choices.get("branch") in BRANCHES:
        return choices["branch"], []
    vals = {s["source"]: s["value"] for s in signals if s.get("what") == "branch" and s.get("value")}
    hist, addr = vals.get("บิลเดือนก่อนของมิเตอร์นี้"), vals.get("ที่อยู่บนใบแจ้ง")
    if hist and addr and hist != addr:
        return None, [{"key": "branch", "message": f"สาขาไม่ตรงกัน: บิลเดือนก่อนของมิเตอร์นี้อยู่{hist} แต่ที่อยู่บนใบแจ้งเป็น{addr}",
                       "options": [{"value": hist, "label": f"{hist} (บิลเดือนก่อน)"}, {"value": addr, "label": f"{addr} (ที่อยู่บนใบแจ้ง)"}],
                       "default": hist}]
    if hist or addr:
        return hist or addr, []
    return None, [{"key": "branch", "message": "มิเตอร์ใหม่ ไม่รู้ว่าเป็นของสาขาไหน — โปรดเลือก",
                   "options": [{"value": b, "label": b} for b in BRANCHES], "default": None}]


# ------------------------------------------------------------------ read
def _json_bill(bill: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in bill.items()}


def _read_bill(f: Dropped) -> dict:
    p = empty_preview(KIND, LABEL)
    try:
        bill = pea_bill.parse_pdf(f.data)
    except pea_bill.NotPeaBill as e:
        p.update(needs_ai=True, pages=f.pages or None, note=f"ดูเหมือนใบแจ้งค่าไฟ PEA แต่อ่านตัวเลขไม่ได้ ({e}) — รูปแบบใบแจ้งอาจเปลี่ยน")
        return p
    period = bill["bill_period"]
    signals = [{"what": "month", "source": "ประจำเดือนบนใบแจ้ง", "value": period, "weight": 1}]
    hist = _history_branch(bill.get("ca_no"), bill.get("invoice_no"))
    addr = address_branch(f.text)
    if hist:
        signals.append({"what": "branch", "source": "บิลเดือนก่อนของมิเตอร์นี้", "value": hist})
    if addr:
        signals.append({"what": "branch", "source": "ที่อยู่บนใบแจ้ง", "value": addr})
    branch, conflicts = _branch_choice(signals, f.choices)
    use = branch or (conflicts[0]["default"] if conflicts else None) or BRANCHES[0]
    if f.choices.get("month") and f.choices["month"] != period:      # the bill period is printed: the owner may not move it
        p["flags"].append(f"ใบแจ้งนี้เป็นค่าไฟเดือน {period[5:7]}/{int(period[:4]) + 543} — ลงตามใบแจ้ง ไม่ใช่เดือนที่เลือก")
    entry = electricity.entry_for(bill, use)
    interest = round(float(bill.get("deposit_interest") or 0), 2)
    meter = bill.get("address") or bill.get("ca_no")
    y, m = int(period[:4]), int(period[5:7])
    rows = [{"date": entry["entry_date"], "text": f"{entry['description']} · ก่อน VAT {bill['subtotal']:,.2f} + VAT {bill['vat']:,.2f}",
             "party": entry["party"], "amount": round(float(bill["total"]), 2), "role": "cost", "account": "5100",
             "flag": "PEA ประมาณหน่วย (ไม่ได้อ่านมิเตอร์)" if bill.get("estimated") else None}]
    if interest:
        rows.append({"date": entry["entry_date"], "text": "ผลประโยชน์เงินประกันการใช้ไฟ (PEA หักจากบิลนี้)", "party": entry["party"],
                     "amount": -interest, "role": "revenue", "account": "4900", "flag": None})
    to_pay = round(float(bill["total"]) - interest, 2)
    p.update(month=period, signals=signals, conflicts=conflicts,
             note=f"ใบแจ้งค่าไฟ มิเตอร์ {meter} เดือน {m:02d}/{y + 543} · {use} · ยอดชำระ {to_pay:,.2f} บาท",
             batches=[{"key": f"pea:{bill['invoice_no']}", "feed": "electricity", "source": "electricity", "branch": use, "month": period,
                       "title": f"ค่าไฟ มิเตอร์ {meter} เดือน {m:02d}/{y + 543}", "rows": rows,
                       "file_total": to_pay, "rows_total": round(sum(r["amount"] for r in rows), 2),
                       "entries": [entry], "bill": _json_bill(bill)}])
    if bill.get("estimated"):
        p["flags"].append(f"มิเตอร์ {meter}: เดือนนี้ PEA ประมาณหน่วย (ไม่ได้อ่านมิเตอร์) — เดือนหน้าจะปรับ")
    return p


def _read_amr(f: Dropped) -> dict:
    p = empty_preview(KIND, LABEL)
    try:
        rep = amr.parse(f.data.decode("utf-8", errors="replace"))
    except amr.NotAmr:
        p.update(store_only=True, note="ไม่ใช่รายงาน AMR ของ กฟภ.")
        return p
    meter = rep.get("ca_no") or "-"
    if not rep["month"]:
        p.update(store_only=True, note=f"รายงาน AMR มิเตอร์ {meter} ไม่มีข้อมูล (ไฟล์ว่าง) — เก็บไว้เฉย ๆ")
        return p
    signals = [{"what": "month", "source": "ประจำเดือนในรายงาน AMR", "value": rep["month"], "weight": 1}]
    hist = _history_branch(rep.get("ca_no"), None)
    if hist:
        signals.append({"what": "branch", "source": "บิลเดือนก่อนของมิเตอร์นี้", "value": hist})
    branch, conflicts = _branch_choice(signals, f.choices)
    days = sorted({r["slot_start"][:10] for r in rep["rows"]})
    kwh = round(sum(r["kwh"] for r in rep["rows"]))
    y, m = int(rep["month"][:4]), int(rep["month"][5:7])
    p.update(month=rep["month"], signals=signals, conflicts=conflicts,
             note=f"รายงาน AMR (ไฟราย 15 นาที) มิเตอร์ {meter} เดือน {m:02d}/{y + 543} · {len(days)} วัน · {len(rep['rows'])} ช่วง · "
                  f"{kwh:,} หน่วย · ไม่ลงบัญชี (เก็บเป็นข้อมูลการใช้ไฟ)",
             amr={"ca_no": rep.get("ca_no"), "meter_no": rep.get("meter_no"), "month": rep["month"], "branch": branch,
                  "rows": [[r["slot_start"], r["tou"], r["kwh"]] for r in rep["rows"]]})
    return p


def read(f: Dropped) -> dict:
    if f.ext == ".pdf":
        return _read_bill(f)
    if is_amr(f):
        return _read_amr(f)
    p = empty_preview(KIND, LABEL)
    p.update(store_only=True, note="ไม่ใช่ใบแจ้งค่าไฟ PEA หรือรายงาน AMR")
    return p


# ------------------------------------------------------------------ book / unbook
def book(preview: dict, file_id: str) -> dict:
    """A bill: electricity.save_bill (replaces the same invoice in elec_bills and its pea:<invoice> entry). file_id stays None: elec_bills.file_id
    points at acc_files (the old checklist upload), not at the drop box's inbox_files. An AMR report: amr.save (upsert per slot)."""
    if preview.get("amr"):
        a = preview["amr"]
        branch = a.get("branch")
        if not branch:
            raise ValueError("ยังไม่ได้เลือกสาขาของมิเตอร์นี้")
        rep = {"ca_no": a["ca_no"], "meter_no": a.get("meter_no"), "month": a["month"],
               "rows": [{"slot_start": s, "tou": t, "kwh": k} for s, t, k in a["rows"]]}
        amr.save(rep, branch)
        return {"batches": [], "booked": 0.0, "rebook": []}
    keys, total, rebook = [], 0.0, []
    for b in preview.get("batches") or []:
        bill = b["bill"]
        electricity.save_bill(bill, b["branch"], None)
        keys.append(b["key"])
        total += round(float(bill["total"]), 2)
        nxt = {"kind": "cheques", "month": shift_month(bill["bill_period"], 1)}
        if nxt not in rebook:
            rebook.append(nxt)
    return {"batches": keys, "booked": round(total, 2), "rebook": rebook}


def unbook(preview: dict) -> None:
    """A bill: its elec_bills row and its pea:<invoice> entry. An AMR report: nothing (readings are data, and the same month comes again)."""
    for b in preview.get("batches") or []:
        inv = (b.get("bill") or {}).get("invoice_no") or b["key"].split(":", 1)[1]
        electricity.delete_bill(inv)
