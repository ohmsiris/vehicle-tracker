"""
กล่องรับไฟล์ › น้ำมันบัตร (owner, 24 Nov 2026): the card-fuel files, read from what is inside them.

Three kinds of file land here:

  1. PT's fleet-card export (.xlsx, "รายงานการเติมน้ำมัน": a SUMMARY block, then LIST: one row per fill with DATE, TRANSACTION_ID,
     LICENSE_PLATE_NO, AMOUNT, STATUS ...). One export usually spans several months (the sample runs 07/05/2026 - 07/11/2026). The month
     comes from the ROW DATES, by company month (26th-25th). Only months the file covers COMPLETELY are booked: an edge month that starts
     or ends inside the file's range is flagged and skipped (booking it would replace a whole month's PT fuel with a few days of it).
     The owner can still book them with the choice {"partial": "book"}. Void rows (STATUS not "สำเร็จ", or a VOIDID) are skipped and
     listed; a TRANSACTION_ID seen twice is counted once. The SUMMARY "Total" Amount is checked against the rows we kept.
     Keys: "fuel:pt:<month>" -- exactly what scripts/import_pt.py wrote ("fuel:pt:<month>:<plate>"), and the estimates
     ("fuel:pt:<month>:est-...") of scripts/fill_fuel_estimates.py: dropping a PT export replaces them, never adds to them.

  2. The Bangchak Reader workbook "เติมน้ำมัน_<station>_<range>.xlsx" (sheets: คำอธิบาย, บันทึกเติมน้ำมัน = one row per fill,
     ตรวจสอบยอดรวม = the totals written on the station's bill / notebook ...). One file = one station (NM or ห้วยบง) and one billing
     period. The Reader's periods run 25th-25th (October's file starts on 25 Sep), so the month is the company month most of the
     ROW DATES (by baht) fall in, cross-checked with the period in the title and in the file name; any disagreement is a conflict for
     the owner. Rows outside that month stay in the batch (they are on the same station bill) and are listed. The station comes from
     the title ("สาขา NM" / "(สาขาห้วยบง)") and the file name; the oldest files name no station, then the format decides (NM's file is
     typed from the printed bill "ใบวางบิล/ใบกำกับภาษี", ห้วยบง's from the notebook "สมุด"), and the preview says so.
     Keys: "fuel:bangchak:<month>:<station>" (entries "...:<station>:<plate>"), so the two stations replace independently.
     MIGRATION: the scripts (import_bangchak.py, import_card_fuel_sheet.py, fill_fuel_estimates.py) booked both stations as ONE entry per
     plate per month, "fuel:bangchak:<month>:<plate>" -- that cannot be split by station, so it cannot be reproduced. Booking a station's
     file therefore also deletes those old combined entries of that month (and says so in the preview first): the other station's fuel
     for that month is then missing until its own file is dropped too.

  3. The Bangchak Reader PDFs (สรุปรายคนขับ portrait / landscape, ตารางรายวัน) are the SAME fills as the workbook, printed. They are
     recognised and kept with the month, never booked: reading the PDF as well as the workbook is how card fuel got counted twice.

Every booked row is a cost: Dr 6120 fuel (net of VAT) + 1400 input VAT, Cr 2100 owed to the station (app/acc_cardfuel.build); the
branch is who pays the card = สระบุรี, even for a truck filed under แก่งคอย (owner, 9 Nov 2026). Cash fuel is NOT here: that comes from the
petty-cash papers (app/cash_fuel.py) and must never be booked from these files.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from app import acc_cardfuel
from app.acc_docs import norm_plate
from app.inbox_common import (Dropped, book_batches, company_month, empty_preview, month_conflict, month_label, unbook_batches,
                              vote, workbook)

KIND = "card_fuel"
LABEL = "น้ำมันบัตร (PT / บางจาก)"
FEEDS = ("fuel_card",)

PAYER = acc_cardfuel.PAYER
BC_STATIONS = ("NM", "ห้วยบง")
BC_SHEET = "บันทึกเติมน้ำมัน"
BC_CHECK = "ตรวจสอบยอดรวม"
PT_OK = ("สำเร็จ", "success", "successful", "approved", "")
TH_ABBR = {"มค": 1, "กพ": 2, "มีค": 3, "เมย": 4, "พค": 5, "มิย": 6, "กค": 7, "สค": 8, "กย": 9, "ตค": 10, "พย": 11, "ธค": 12}
_ABBR = "|".join(sorted(TH_ABBR, key=len, reverse=True))
NAME_RANGE = re.compile(rf"(\d{{1,2}})({_ABBR})-(\d{{1,2}})({_ABBR})(\d{{2}})")
MONEY = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")


# ------------------------------------------------------------------ small shared helpers (inbox_r_sales uses value_conflict too)
def value_conflict(signals: list[dict], what: str, choices: dict, label=str, title: str = "") -> tuple[Optional[str], list[dict]]:
    """month_conflict for any signal ("station", "branch" ...): the value to use and the conflict to show when the signals disagree."""
    if choices.get(what):
        return choices[what], []
    best, against = vote(signals, what)
    if not against:
        return best, []
    seen: list = []
    for v in [best] + [s["value"] for s in against]:
        if v not in seen:
            seen.append(v)
    src = {v: ", ".join(s["source"] for s in signals if s.get("what") == what and s.get("value") == v) for v in seen}
    return best, [{"key": what, "message": f"{title}ไม่ตรงกัน: " + " แต่ ".join(f"{src[v]} บอก {label(v)}" for v in seen),
                   "options": [{"value": v, "label": f"{label(v)} ({src[v]})"} for v in seen], "default": best}]


def month_bounds(month: str) -> tuple[date, date]:
    """Company month "2026-11" -> (26 Oct 2026, 25 Nov 2026)."""
    y, m = int(month[:4]), int(month[5:7])
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    return date(py, pm, 26), date(y, m, 25)


def months_between(lo: date, hi: date) -> list[str]:
    out, m = [], company_month(lo)
    while m <= company_month(hi):
        out.append(m)
        y, mm = int(m[:4]), int(m[5:7]) + 1
        m = f"{y + 1}-01" if mm == 13 else f"{y}-{mm:02d}"
    return out


def dmy(d) -> str:
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return d.strftime("%d/%m/%Y")


def _num(v) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v or "").replace(",", "").strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _day(v) -> Optional[date]:
    """A cell that is a date: a datetime, or 'dd/mm/yyyy[ hh:mm:ss]' (Christian or Buddhist year)."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", str(v or ""))
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y - 543 if y > 2400 else (2500 + y - 543 if y < 100 else y)
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _vehicles() -> list[dict]:
    from app import supabase
    return supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch", "order": "id.asc"})


# ------------------------------------------------------------------ what kind of file
def _pt_head(rows: list) -> Optional[int]:
    for i, r in enumerate(rows or []):
        cells = [str(c).strip() for c in (r or []) if c is not None]
        if cells and cells[0] == "DATE" and "TRANSACTION_ID" in cells and "LICENSE_PLATE_NO" in cells and "AMOUNT" in cells:
            return i
    return None


def _bc_head(rows: list) -> Optional[dict]:
    for r in (rows or [])[:5]:
        col = {str(c).strip(): i for i, c in enumerate(r or []) if c is not None}
        if "ทะเบียนรถที่เติมจริง" in col and "จำนวนเงิน (บาท)" in col and "วันที่" in col:
            return col
    return None


def _is_bc_pdf(text: str) -> bool:
    t = text or ""
    if "ตารางรายวันตามรถเบอร์" in t or ("สรุปรายคนขับ" in t and "รวมลิตร" in t):
        return True
    # the oldest PDFs lost their Thai font ("ชืÉอเล่น", "จํานวนครัÊงทีÉเติม"): the column titles still give them away
    return "รวมลิตร" in t and "เติม" in t and bool(re.search(r"ชื.{0,2}อเล่น", t)) and bool(re.search(r"ชื.{0,2}อจริง", t))


def _type(f: Dropped) -> Optional[str]:
    if f.sheets:
        for name, rows in f.sheets.items():
            if _pt_head(rows) is not None:
                return "pt"
            if _bc_head(rows) is not None:
                return "bangchak"
    if f.ext == ".pdf" and not f.is_scan and _is_bc_pdf(f.text):
        return "bangchak_pdf"
    return None


def sniff(f: Dropped) -> float:
    return {"pt": 0.97, "bangchak": 0.95, "bangchak_pdf": 0.9}.get(_type(f) or "", 0.0)


def read(f: Dropped) -> dict:
    t = _type(f)
    if t == "pt":
        return read_pt(f)
    if t == "bangchak":
        return read_bangchak(f)
    if t == "bangchak_pdf":
        return read_bangchak_pdf(f)
    return empty_preview(KIND, LABEL, store_only=True, note="ไม่ใช่ไฟล์น้ำมันบัตรที่อ่านได้")


# ------------------------------------------------------------------ rows / batches from per-plate totals
def _batch(key: str, station: str, month: str, by_plate: dict, counts: dict, vehicles: list[dict], title: str, note: str,
           file_total: Optional[float], sub: str = "") -> tuple[dict, list[str]]:
    out = acc_cardfuel.build(month, station, by_plate, vehicles, note, sub=sub)
    rows = []
    for e in out["entries"]:
        plate_key = e["source_id"].rsplit(":", 1)[-1]
        gross = round(sum(ln["credit"] for ln in e["lines"]), 2)
        no_truck = all(ln.get("vehicle_id") is None for ln in e["lines"])
        n = counts.get(plate_key, 0)
        rows.append({"date": e["entry_date"], "text": e["description"] + (f" · {n} ครั้ง" if n else ""), "party": e["party"],
                     "amount": gross, "role": "cost", "account": "6120",
                     "flag": "ไม่พบรถทะเบียนนี้ในระบบ — ลงไว้ไม่ผูกกับรถ" if no_truck else None})
    rows_total = round(sum(r["amount"] for r in rows), 2)
    return ({"key": key, "feed": "fuel_card", "source": "card_fuel", "branch": PAYER, "month": month, "title": title, "rows": rows,
             "file_total": file_total, "rows_total": rows_total, "entries": out["entries"]}, out["flags"])


# ------------------------------------------------------------------ 1. PT export
def _pt_plate(raw: str, by: dict, vehicles: list[dict]) -> str:
    """As scripts/import_pt.py: the exact plate, else the one vehicle whose plate ends in the same 4 digits (older exports lost the
    Thai letters: "1?? 2090"); otherwise the plate as exported."""
    v = by.get(norm_plate(raw.replace("�", "")))
    if not v:
        groups = re.findall(r"\d+", raw)
        if groups and len(groups[-1]) == 4:
            hits = [x for x in vehicles if re.sub(r"\D", "", x.get("plate_number") or "").endswith(groups[-1])]
            v = hits[0] if len(hits) == 1 else None
    return v["plate_number"] if v else raw.replace("�", "?")


def _pt_name_range(name: str) -> Optional[tuple[date, date]]:
    got = re.findall(r"(\d{4})-(\d{2})-(\d{2})", name or "")
    if len(got) < 2:
        return None
    try:
        a, b = (date(int(y), int(m), int(d)) for y, m, d in got[:2])
    except ValueError:
        return None
    return (a, b) if a <= b else None


def read_pt(f: Dropped) -> dict:
    p = empty_preview(KIND, "น้ำมันบัตร PT (ไฟล์จาก PT)", subkind="pt")
    book = workbook(f.data, f.name)
    rows = next((r for r in book.values() if _pt_head(r) is not None), [])
    head = _pt_head(rows)
    col = {str(h).strip(): i for i, h in enumerate(rows[head]) if h is not None}

    # the SUMMARY block: the row "Total" under the header row that has "Amount"
    file_total, void_count = None, None
    for i in range(head):
        hdr = {str(h).strip(): j for j, h in enumerate(rows[i] or []) if h is not None}
        if "Amount" in hdr:
            for r in rows[i + 1:head]:
                if r and str(r[0] or "").strip().lower() == "total":
                    file_total = _num(r[hdr["Amount"]])
                    void_count = _num(r[hdr["Void"]]) if "Void" in hdr else None
                    break
            break

    def cell(r, name):
        j = col.get(name)
        return r[j] if j is not None and j < len(r) else None

    fills, voids, dups, seen = [], [], 0, set()
    for r in rows[head + 1:]:
        if not r or not r[0] or not cell(r, "LICENSE_PLATE_NO"):
            continue
        tid = str(cell(r, "TRANSACTION_ID") or "")
        if tid and tid in seen:
            dups += 1
            continue
        seen.add(tid)
        d, amount = _day(r[0]), _num(cell(r, "AMOUNT"))
        if d is None or amount is None:
            p["flags"].append(f"แถว {tid}: อ่านวันที่หรือยอดเงินไม่ได้ — ไม่นับ")
            continue
        status = str(cell(r, "STATUS") or "").strip()
        void_id = str(cell(r, "REMARK (VOIDID)") or "").strip()
        if status.lower() not in PT_OK or void_id or "void" in status.lower() or "ยกเลิก" in status:
            voids.append((d, str(cell(r, "LICENSE_PLATE_NO")), amount, status or "Void"))
            continue
        fills.append((d, str(cell(r, "LICENSE_PLATE_NO")), amount, tid))
    if not fills:
        p.update(store_only=True, note="ไฟล์ PT นี้ไม่มีรายการเติมน้ำมัน")
        return p

    lo, hi = min(x[0] for x in fills), max(x[0] for x in fills)
    rng = _pt_name_range(f.name)        # the export's own range: may only widen the covered days by a day or two at each end
    cover_lo = rng[0] if rng and 0 <= (lo - rng[0]).days <= 2 and rng[1] >= hi else lo
    cover_hi = rng[1] if rng and 0 <= (rng[1] - hi).days <= 2 and rng[0] <= lo else hi
    rows_total = round(sum(x[2] for x in fills), 2)
    p["file_total"], p["rows_total"] = file_total, rows_total
    p["signals"].append({"what": "range", "source": "วันที่ในแถว", "value": f"{dmy(lo)} - {dmy(hi)}"})
    p["signals"].append({"what": "branch", "source": "ผู้ถือบัตร / ผู้จ่าย", "value": PAYER})
    if file_total is not None and abs(file_total - rows_total) > 1:
        void_sum = round(sum(v[2] for v in voids), 2)
        hint = " (ตรงกับยอดรวมที่นับแถวยกเลิกด้วย)" if voids and abs(file_total - rows_total - void_sum) <= 1 else ""
        p["flags"].append(f"ยอดรวม SUMMARY {file_total:,.2f} บาท แต่แถวที่นับได้ {rows_total:,.2f} บาท{hint} — ตรวจไฟล์ก่อนลงบัญชี")
    if voids:
        p["flags"].append(f"ไม่นับแถวที่ยกเลิก (Void) {len(voids)} แถว รวม {sum(v[2] for v in voids):,.2f} บาท: "
                          + ", ".join(f"{dmy(v[0])} {v[1]} {v[2]:,.2f}" for v in voids[:10]))
    elif void_count:
        p["flags"].append(f"SUMMARY บอกว่ามีรายการ Void {void_count:g} รายการ แต่ไม่พบแถวที่ยกเลิกในรายการ")
    if dups:
        p["flags"].append(f"เลขรายการ (TRANSACTION_ID) ซ้ำ {dups} แถว — นับครั้งเดียว")

    vehicles = _vehicles()
    by = {norm_plate(v["plate_number"]): v for v in vehicles if v.get("plate_number")}
    per: dict = defaultdict(lambda: defaultdict(float))
    counts: dict = defaultdict(lambda: defaultdict(int))
    for d, plate, amount, _ in fills:
        m, k = company_month(d), _pt_plate(plate, by, vehicles)
        per[m][k] += amount
        counts[m][norm_plate(k)] += 1
    partial_ok = f.choices.get("partial") == "book"
    booked = []
    for m in months_between(lo, hi):
        if m not in per:
            continue
        a, b = month_bounds(m)
        total = round(sum(per[m].values()), 2)
        n = sum(counts[m].values())
        if not (cover_lo <= a and cover_hi >= b):
            msg = (f"{month_label(m)}: ไฟล์มีแค่ {dmy(max(a, lo))}–{dmy(min(b, hi))} ไม่ครบเดือน ({dmy(a)}–{dmy(b)}) "
                   f"{n} ครั้ง {total:,.2f} บาท")
            if not partial_ok:
                p["flags"].append(msg + " — ไม่ลงบัญชี (ลงเดือนนี้จากไฟล์ที่ครบเดือน)")
                continue
            p["flags"].append(msg + " — ลงตามที่เลือก (แทนที่ยอด PT ทั้งเดือน)")
        batch, flags = _batch(f"fuel:pt:{m}", "pt", m, dict(per[m]), dict(counts[m]), vehicles,
                              f"น้ำมัน PT {month_label(m)} · {n} ครั้ง", "จากไฟล์ PT", None)
        p["batches"].append(batch)
        p["flags"] += flags
        booked.append(m)
    p["months"] = booked
    p["month"] = booked[-1] if booked else None
    if booked:
        p["signals"].append({"what": "month", "source": "วันที่ในแถว (เดือนล่าสุดที่ครบ)", "value": booked[-1]})
        p["note"] = (f"PT {dmy(lo)}–{dmy(hi)}: ลงบัญชี {len(booked)} เดือนที่ครบ ({', '.join(month_label(m) for m in booked)}) "
                     "แทนที่ยอด PT เดิมของเดือนเหล่านั้น")
    else:
        p.update(store_only=True, note="ไฟล์ PT นี้ไม่ครบเดือนใดเลย (26–25) — เก็บไว้ ไม่ลงบัญชี")
    return p


# ------------------------------------------------------------------ 2. Bangchak Reader workbook
def _name_end(name: str) -> Optional[date]:
    """'เติมน้ำมัน_NM_25กย-25ตค69.xlsx' -> 25 Oct 2026 (the last day of the period in the name)."""
    m = NAME_RANGE.search(name or "")
    if not m:
        return None
    try:
        return date(2500 + int(m.group(5)) - 543, TH_ABBR[m.group(4)], int(m.group(3)))
    except ValueError:
        return None


def _name_month(name: str) -> Optional[str]:
    """'NM_กันยา69.pdf' -> '2026-09' (a station period is named after the month it ends in, which is also its company month)."""
    from app.acc_cp_invoice import THAI_LONG
    for stem, mo in THAI_LONG.items():
        m = re.search(stem + r"\D{0,4}?(25\d\d|\d\d)(?!\d)", name or "")
        if m:
            y = int(m.group(1))
            return f"{(y if y > 2400 else 2500 + y) - 543}-{mo:02d}"
    return None


def _title_end(title: str) -> Optional[date]:
    """'... — งวด 25/9/69 – 25/10/69' -> 25 Oct 2026."""
    got = [_day(x) for x in re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", title or "")]
    got = [d for d in got if d]
    return got[-1] if got else None


def _station_signals(title: str, name: str) -> list[dict]:
    sig = []
    m = re.search(r"สาขา\s*(NM|ห้วยบง)", title or "", re.I)
    if m:
        sig.append({"what": "station", "source": "หัวไฟล์", "value": "NM" if m.group(1).upper() == "NM" else "ห้วยบง"})
    for st in BC_STATIONS:
        if re.search(rf"(^|[_\s]){st}([_\s.]|$)", name or "", re.I):
            sig.append({"what": "station", "source": "ชื่อไฟล์", "value": st, "weight": 0.5})
    if not sig and title:
        # the oldest Reader files name no station: NM's is typed from the printed bill, ห้วยบง's from the station's notebook
        if "ใบวางบิล" in title:
            sig.append({"what": "station", "source": "รูปแบบไฟล์ (ใบวางบิล = NM)", "value": "NM", "weight": 0.5})
        elif "รถสาย" in title:
            sig.append({"what": "station", "source": "รูปแบบไฟล์ (สมุดรถสาย = ห้วยบง)", "value": "ห้วยบง", "weight": 0.5})
    return sig


def _bc_file_total(rows: list) -> tuple[Optional[float], Optional[float]]:
    """(baht, bills) the station's own paper says for the whole period: the 'รวมทั้งงวด' / 'รวมทั้งเล่ม' lines of ตรวจสอบยอดรวม,
    column D (what was written on the bill / notebook -- column C is the Reader's own sum)."""
    baht = bills = None
    for r in rows or []:
        label = str((r or [None])[0] or "")
        if not label.startswith("รวมทั้ง") or len(r) < 4:
            continue
        v = _num(r[3])
        if v is None:
            continue
        if "ยอดเงิน" in label and "สะสม" not in label:
            baht = v
        elif "จำนวนบิล" in label or "จำนวนเที่ยว" in label:
            bills = v
    return baht, bills


def _legacy_bangchak(month: str) -> list[dict]:
    """The scripts' Bangchak entries of a month: one per plate for both stations, "fuel:bangchak:<month>:<plate>" (no station)."""
    from app import supabase
    rows = supabase.select_all("acc_entries", {"select": "id,source_id", "source_id": f"like.fuel:bangchak:{month}:*", "order": "id.asc"})
    pre = f"fuel:bangchak:{month}:"
    # the drop box's own keys have one more segment ("<station>:<plate>"); a plate never holds a ":"
    return [r for r in rows if (r.get("source_id") or "").startswith(pre) and ":" not in r["source_id"][len(pre):]]


def read_bangchak(f: Dropped) -> dict:
    p = empty_preview(KIND, "น้ำมันบัตรบางจาก (ไฟล์ Bangchak Reader)", subkind="bangchak")
    book = workbook(f.data, f.name)
    log_name = next((n for n, r in book.items() if _bc_head(r) is not None), None)
    log = book[log_name]
    col = _bc_head(log)
    title = str(((book.get("คำอธิบาย") or [[None]])[0] or [None])[0] or "")
    fills = []
    for r in log:
        if not r or not isinstance(r[0], (int, float)) or isinstance(r[0], bool):
            continue                                  # header, the "รวมทั้งหมด" line (no row number) and notes
        amount = _num(r[col["จำนวนเงิน (บาท)"]]) if col["จำนวนเงิน (บาท)"] < len(r) else None
        d = _day(r[col["วันที่"]])
        if amount is None or d is None:
            p["flags"].append(f"แถวลำดับ {r[0]:g}: ไม่มีวันที่หรือยอดเงิน — ไม่นับ")
            continue
        plate = r[col["ทะเบียนรถที่เติมจริง"]] or (r[col["ทะเบียนรถประจำเบอร์นี้"]] if "ทะเบียนรถประจำเบอร์นี้" in col else None)
        fills.append((d, str(plate or "ไม่ระบุทะเบียน").strip(), amount))
    if not fills:
        p.update(store_only=True, note="ไฟล์บางจากนี้ไม่มีรายการเติมน้ำมัน")
        return p

    # station
    sig = _station_signals(title, f.name)
    station, conflicts = value_conflict(sig, "station", f.choices, title="สถานีบางจาก")
    if not station:
        station = BC_STATIONS[0]
        conflicts = [{"key": "station", "message": "ไม่รู้ว่าเป็นไฟล์ของสถานีไหน (ไม่มีชื่อสถานีในไฟล์)",
                      "options": [{"value": s, "label": s} for s in BC_STATIONS], "default": None}]
    elif any(s["source"].startswith("รูปแบบไฟล์") for s in sig) and not f.choices.get("station"):
        p["flags"].append(f"ไฟล์ไม่ได้เขียนชื่อสถานี — ถือเป็น {station} จากรูปแบบไฟล์")
    p["conflicts"] += conflicts

    # month: the company month most of the baht falls in, the period in the title, the period in the file name
    by_month: dict = defaultdict(float)
    for d, _, a in fills:
        by_month[company_month(d)] += a
    rows_month = max(by_month.items(), key=lambda kv: kv[1])[0]
    sig_m = [{"what": "month", "source": "วันที่ในแถว", "value": rows_month, "weight": 2}]
    if _title_end(title):
        sig_m.append({"what": "month", "source": "งวดในหัวไฟล์", "value": company_month(_title_end(title))})
    if _name_end(f.name):
        sig_m.append({"what": "month", "source": "ชื่อไฟล์", "value": company_month(_name_end(f.name)), "weight": 0.5})
    month, mc = month_conflict(sig_m, f.choices)
    p["conflicts"] += mc
    month = month or rows_month
    p["signals"] = sig + sig_m
    p["signals"].append({"what": "branch", "source": "ผู้จ่ายบัตรน้ำมัน", "value": PAYER})
    p["month"] = month

    lo, hi = min(x[0] for x in fills), max(x[0] for x in fills)
    a, b = month_bounds(month)
    outside = [x for x in fills if not (a <= x[0] <= b)]
    if outside:
        days = sorted({x[0] for x in outside})
        p["flags"].append(f"มี {len(outside)} บิล ({sum(x[2] for x in outside):,.2f} บาท) วันที่ {', '.join(dmy(d) for d in days[:6])} "
                          f"อยู่นอกเดือน {month_label(month)} ({dmy(a)}–{dmy(b)}) — รวมไว้ในงวดนี้ตามบิลของสถานี")

    file_total, bills = _bc_file_total(book.get(BC_CHECK))
    rows_sum = round(sum(x[2] for x in fills), 2)
    if file_total is None:
        p["flags"].append("ไม่พบยอดรวมทั้งงวดในแท็บ ตรวจสอบยอดรวม — เทียบยอดไม่ได้")
    elif abs(file_total - rows_sum) > 1:
        p["flags"].append(f"ยอดรวมที่สถานีเขียนไว้ {file_total:,.2f} บาท แต่รายการรวมได้ {rows_sum:,.2f} บาท — ตรวจไฟล์ก่อนลงบัญชี")
    if bills is not None and int(bills) != len(fills):
        p["flags"].append(f"จำนวนบิลที่สถานีเขียนไว้ {bills:g} แต่ในรายการมี {len(fills)} บิล")

    vehicles = _vehicles()
    per: dict = defaultdict(float)
    counts: dict = defaultdict(int)
    for _, plate, amount in fills:
        per[plate] += amount
        counts[norm_plate(plate)] += 1
    key = f"fuel:bangchak:{month}:{station}"
    batch, flags = _batch(key, "bangchak", month, dict(per), dict(counts), vehicles,
                          f"น้ำมันบางจาก {station} {month_label(month)} · {len(fills)} บิล ({dmy(lo)}–{dmy(hi)})",
                          f"จากไฟล์บางจาก {station}", file_total, sub=station)
    p["batches"].append(batch)
    p["flags"] += flags
    legacy = _legacy_bangchak(month)
    if legacy:
        other = [s for s in BC_STATIONS if s != station][0]
        p["flags"].append(f"{month_label(month)} มีรายการบางจากแบบเก่า {len(legacy)} รายการ (รวม 2 สถานีต่อทะเบียน แยกสถานีไม่ได้) — "
                          f"จะถูกลบเมื่อลงบัญชีไฟล์นี้ ต้องลงไฟล์ของสถานี {other} เดือนเดียวกันด้วย")
    p["legacy"] = [r["source_id"] for r in legacy]
    p["note"] = f"บางจาก {station} งวด {dmy(lo)}–{dmy(hi)} → {month_label(month)}"
    return p


def book(preview: dict, file_id: str) -> dict:
    """Replace this file's batches; for a Bangchak station file also delete the scripts' combined (both-station) entries of the month."""
    from app import supabase
    from app.accounting import _require
    _require()
    for b in preview.get("batches") or []:
        if b["key"].startswith("fuel:bangchak:"):
            ids = [r["id"] for r in _legacy_bangchak(b["month"])]
            for i in range(0, len(ids), 100):
                supabase.delete("acc_entries", {"id": f"in.({','.join(str(x) for x in ids[i:i + 100])})"})
    return book_batches(preview)


def unbook(preview: dict) -> None:
    unbook_batches(preview)


# ------------------------------------------------------------------ 3. Bangchak Reader PDFs: store only
def read_bangchak_pdf(f: Dropped) -> dict:
    text = f.text or ""
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    what = "ตารางรายวัน" if "ตารางรายวัน" in text else "สรุปรายคนขับ"
    sig = _station_signals(first, f.name)
    station, _ = vote(sig, "station")
    end = _title_end(first) or _name_end(f.name)
    month = company_month(end) if end else _name_month(f.name)
    money = [float(x.replace(",", "")) for x in (ln.strip() for ln in text.splitlines()) if MONEY.match(x)]
    total = max(money) if money else None
    p = empty_preview(KIND, "PDF จาก Bangchak Reader (สำเนาของไฟล์ Excel)", subkind="bangchak_pdf", store_only=True, month=month)
    p["signals"] = sig + ([{"what": "month", "source": "งวดในหัวกระดาษ", "value": month}] if month else [])
    p["file_total"] = total
    p["note"] = (f"PDF {what}{(' สถานี ' + station) if station else ''}{(' ' + month_label(month)) if month else ''}"
                 f"{(f' (ยอดรวม {total:,.2f} บาท)' if total else '')} พิมพ์มาจากไฟล์ Excel เติมน้ำมัน_สถานี_งวด.xlsx ชุดเดียวกัน — "
                 "เก็บไว้กับเดือน ไม่ลงบัญชี (ถ้าลงทั้ง PDF และ Excel ค่าน้ำมันจะถูกนับสองครั้ง) ให้ลงไฟล์ Excel แทน")
    return p
