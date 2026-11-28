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
     ตรวจสอบยอดรวม = the totals written on the station's bill / notebook ...). Up to October 2026 one file = one station (NM or ห้วยบง)
     and one billing period, with those sheet names as they are. From November (owner, 28 Nov 2026) the Reader prefixes every sheet
     with its station ("NM บันทึกเติมน้ำมัน", "ห้วยบง ตรวจสอบยอดรวม" ...) and may put SEVERAL stations in one workbook
     ("เติมน้ำมัน_NM_ห้วยบง_26ตค-25พย69.xlsx"): each station's sheets are then read on their own -- one batch per station in the one
     preview, each with its own key, its own total check (its own "<station> ตรวจสอบยอดรวม"), its own month vote (a month question is
     "month:<station>") and its own flags (prefixed "<station>: "). Booking books them all; a later file of one station and month
     supersedes only that station's key (app/inbox.py, by key). The sheet prefix is the station: the title only cross-checks it.
     A third station, แก่งคอย (Kaeng Khoi's trucks fill at Bangchak from Nov 2026), comes as "แก่งคอย ..." sheets, title "สาขาแก่งคอย";
     its trucks resolve to Kaeng Khoi's vehicles (truck numbers repeat between the plants) and its payer is BC_PAYER["แก่งคอย"].
     The billing period: The Reader's periods run 25th-25th (October's file starts on 25 Sep), so the month is the company month most of the
     ROW DATES (by baht) fall in, cross-checked with the period in the title and in the file name; any disagreement is a conflict for
     the owner. Rows outside that month stay in the batch (they are on the same station bill) and are listed. The station comes from
     the title ("สาขา NM" / "(สาขาห้วยบง)") and the file name; the oldest files name no station, then the format decides (NM's file is
     typed from the printed bill "ใบวางบิล/ใบกำกับภาษี", ห้วยบง's from the notebook "สมุด"), and the preview says so.
     Keys: "fuel:bangchak:<month>:<station>" (entries "...:<station>:<plate>"), so the two stations replace independently.
     MIGRATION: the scripts (import_bangchak.py, import_card_fuel_sheet.py, fill_fuel_estimates.py) booked both stations as ONE entry per
     plate per month, "fuel:bangchak:<month>:<plate>" -- that cannot be split by station, so it cannot be reproduced. Booking a station's
     file therefore also deletes those old combined entries of that month (and says so in the preview first): the other station's fuel
     for that month is then missing until its own file is dropped too.
     Nothing is lost for good: before deleting them, book() keeps a copy (lines and all) in the private accounting bucket,
     inbox/legacy/fuel-bangchak-<month>.json (one per month; the engine rewrites the row's preview after booking, so it cannot live
     there). Removing a station file (unbook) puts them back once no station file of that month is left in the books -- with the other
     station's file still booked they would count that station twice, so the copy waits for that one to go too. The preview's
     "remove_note" says this in Thai for the remove confirmation.

  3. The Bangchak Reader PDFs (สรุปรายคนขับ portrait / landscape, ตารางรายวัน) are the SAME fills as the workbook, printed. They are
     recognised and kept with the month, never booked: reading the PDF as well as the workbook is how card fuel got counted twice.

Every booked row is a cost: Dr 6120 fuel (net of VAT) + 1400 input VAT, Cr 2100 owed to the station (app/acc_cardfuel.build); the
branch is who pays the card = สระบุรี, even for a truck filed under แก่งคอย (owner, 9 Nov 2026) -- except บางจาก แก่งคอย (BC_PAYER). Cash fuel is NOT here: that comes from the
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
BC_STATIONS = ("NM", "ห้วยบง", "แก่งคอย")
# the stations the scripts' old combined entries ("fuel:bangchak:<month>:<plate>") stood for: Saraburi's two. แก่งคอย came in Nov 2026
# and was never in them, so its file neither removes nor waits for them.
LEGACY_STATIONS = ("NM", "ห้วยบง")
# who pays each station's card (the batch's branch) -- owner to confirm (28 Nov 2026): NM and ห้วยบง are on Saraburi's card (owner,
# 9 Nov 2026); แก่งคอย's statement is a tax invoice of its own for Kaeng Khoi's trucks, booked to แก่งคอย until the owner says otherwise.
BC_PAYER = {"NM": "สระบุรี", "ห้วยบง": "สระบุรี", "แก่งคอย": "แก่งคอย"}
# a station only one plant's trucks use: a truck number / plate tail both plants have resolves to that branch's truck (acc_cardfuel).
# NM and ห้วยบง are not here: Kaeng Khoi's front-of-plant trucks fill there too, so a tail both plants share stays unresolved (flagged)
# and a row with no plate is not guessed from its truck number -- exactly as before Nov 2026.
BC_TRUCKS = {"แก่งคอย": "แก่งคอย"}
BC_SHEET = "บันทึกเติมน้ำมัน"
BC_CHECK = "ตรวจสอบยอดรวม"
BC_ABOUT = "คำอธิบาย"
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
           file_total: Optional[float], sub: str = "", payer: str = PAYER, prefer: Optional[str] = None) -> tuple[dict, list[str]]:
    out = acc_cardfuel.build(month, station, by_plate, vehicles, note, payer=payer, sub=sub, prefer=prefer)
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
    return ({"key": key, "feed": "fuel_card", "source": "card_fuel", "branch": payer, "month": month, "title": title, "rows": rows,
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
    m = re.search(r"สาขา\s*(" + "|".join(BC_STATIONS) + ")", title or "", re.I)
    if m:
        sig.append({"what": "station", "source": "หัวไฟล์", "value": _station_of(m.group(1))})
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


def _station_of(text: str) -> Optional[str]:
    """'nm' -> 'NM', 'แก่งคอย' -> 'แก่งคอย'; None when it is not one of BC_STATIONS."""
    return next((st for st in BC_STATIONS if st.lower() == str(text or "").strip().lower()), None)


def _bc_groups(book: dict) -> list[tuple[str, str]]:
    """The station sheet sets of a Reader workbook: [(fill-log sheet name, prefix)] in sheet order. "" = the older unprefixed
    sheets ("บันทึกเติมน้ำมัน", "คำอธิบาย" ...); "NM" for "NM บันทึกเติมน้ำมัน" and the "NM ..." sheets beside it."""
    out = []
    for name, rows in book.items():
        if _bc_head(rows) is None:
            continue
        name_s = str(name).strip()
        out.append((name, name_s[:-len(BC_SHEET)].strip() if name_s.endswith(BC_SHEET) else ""))
    return out


def _sheet(book: dict, prefix: str, what: str):
    """A sheet of one station set: "<prefix> <what>" (any spacing); the unprefixed one for the older files."""
    if prefix:
        for name, rows in book.items():
            n = str(name).strip()
            if n.endswith(what) and n[:-len(what)].strip() == prefix:
                return rows
        return None
    return book.get(what)


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
    rows = supabase.select_all("acc_entries", {"select": "*", "source_id": f"like.fuel:bangchak:{month}:*", "order": "id.asc"})
    pre = f"fuel:bangchak:{month}:"
    # the drop box's own keys have one more segment ("<station>:<plate>"); a plate never holds a ":"
    return [r for r in rows if (r.get("source_id") or "").startswith(pre) and ":" not in r["source_id"][len(pre):]]


def read_bangchak(f: Dropped) -> dict:
    """One batch per station sheet set (one for the older files; NM + ห้วยบง from the November workbook): see the module docstring."""
    p = empty_preview(KIND, "น้ำมันบัตรบางจาก (ไฟล์ Bangchak Reader)", subkind="bangchak")
    book = workbook(f.data, f.name)
    groups = _bc_groups(book)
    multi = len(groups) > 1
    done: list[dict] = []
    for log_name, prefix in groups:
        got = _read_station(f, book, log_name, prefix, multi)
        if got is None:
            continue
        if any(d["station"] == got["station"] for d in done):
            p["flags"].append(f"มีแท็บของสถานี {got['station']} มากกว่าหนึ่งชุด (แท็บ {log_name}) — ใช้ชุดแรก ไม่นับชุดนี้")
            continue
        done.append(got)
    if not done:
        p.update(store_only=True, note="ไฟล์บางจากนี้ไม่มีรายการเติมน้ำมัน")
        return p
    vehicles = _vehicles()
    for d in done:
        tag = f"{d['station']}: " if multi else ""
        p["conflicts"] += d["conflicts"]
        p["signals"] += d["signals"]
        p["flags"] += [tag + x for x in d["flags"]]
        p.setdefault("swaps", []).extend({**x, "station": d["station"]} for x in d.get("swaps") or [])
        batch, flags = _batch(d["key"], "bangchak", d["month"], d["per"], d["counts"], vehicles,
                              f"น้ำมันบางจาก {d['station']} {month_label(d['month'])} · {d['n']} บิล ({dmy(d['lo'])}–{dmy(d['hi'])})",
                              f"จากไฟล์บางจาก {d['station']}", d["file_total"], sub=d["station"],
                              payer=BC_PAYER.get(d["station"], PAYER), prefer=BC_TRUCKS.get(d["station"]))
        p["batches"].append(batch)
        p["flags"] += flags
    for payer in dict.fromkeys(BC_PAYER.get(d["station"], PAYER) for d in done):
        p["signals"].append({"what": "branch", "source": "ผู้จ่ายบัตรน้ำมัน", "value": payer})
    months = [d["month"] for d in done]
    p["month"] = max(set(months), key=lambda m: (months.count(m), m))
    if multi:
        p["months"] = sorted(set(months))

    # the scripts' combined entries (NM + ห้วยบง in one per plate): the first of those stations' files of the month takes them
    legacy_all, notes = [], []
    for month in sorted(set(months)):
        mine = [d["station"] for d in done if d["month"] == month and d["station"] in LEGACY_STATIONS]
        if not mine:
            continue
        others = [x for x in LEGACY_STATIONS if x not in mine]
        legacy = _legacy_bangchak(month)
        if legacy:
            then = (f"ต้องลงไฟล์ของสถานี {others[0]} เดือนเดียวกันด้วย (ระบบเก็บสำเนาไว้: ถ้าลบไฟล์นี้ออกก่อนลงไฟล์ของ {others[0]} "
                    "รายการเดิมจะกลับมา)") if others else "ไฟล์นี้มีครบทั้ง 2 สถานีแล้ว (ระบบเก็บสำเนาไว้: ถ้าลบไฟล์นี้ออก รายการเดิมจะกลับมา)"
            p["flags"].append(f"{month_label(month)} มีรายการบางจากแบบเก่า {len(legacy)} รายการ (รวม 2 สถานีต่อทะเบียน แยกสถานีไม่ได้) — "
                              "จะถูกลบเมื่อลงบัญชีไฟล์นี้ " + then)
            notes.append(legacy_remove_note(month, mine))
        elif _has_copy(month):
            p["flags"].append(f"รายการบางจากแบบเก่าของ {month_label(month)} ถูกลบไปตอนลงไฟล์บางจากก่อนหน้า (เก็บสำเนาไว้) — "
                              "จะกลับมาเมื่อไม่มีไฟล์บางจากของเดือนนี้ลงบัญชีอยู่เลย")
            notes.append(legacy_remove_note(month, mine))
        legacy_all += [r["source_id"] for r in legacy]
    if notes:
        p["remove_note"] = " · ".join(notes)
    p["legacy"] = legacy_all
    p["note"] = " · ".join(f"บางจาก {d['station']} งวด {dmy(d['lo'])}–{dmy(d['hi'])} → {month_label(d['month'])}" for d in done)
    if multi:
        p["note"] = f"ไฟล์เดียว {len(done)} สถานี ({', '.join(d['station'] for d in done)}) ลงบัญชีแยกทีละสถานี: " + p["note"]
    return p


def _read_station(f: Dropped, book: dict, log_name: str, prefix: str, multi: bool) -> Optional[dict]:
    """One station's sheets of a Reader workbook: its fills by plate, station, month, file total and what to tell the owner -- or None
    when its log has no fill. multi: the workbook holds several stations, so the sheet prefix decides the station (the title only
    cross-checks it) and a month question is asked per station ("month:<station>")."""
    log = book[log_name]
    col = _bc_head(log)
    title = str(((_sheet(book, prefix, BC_ABOUT) or [[None]])[0] or [None])[0] or "")
    flags, conflicts = [], []
    fills = []
    no_plate: dict = {}                               # fill index -> "#<truck number>" for a row with no plate
    swapped: list = []                               # fills done in another truck than the route's own (shown, owner 28 Nov 2026)
    cell = lambda r, name: r[col[name]] if name in col and col[name] < len(r) else None
    for r in log:
        if not r or not isinstance(r[0], (int, float)) or isinstance(r[0], bool):
            continue                                  # header, the "รวมทั้งหมด" line (no row number) and notes
        amount = _num(r[col["จำนวนเงิน (บาท)"]]) if col["จำนวนเงิน (บาท)"] < len(r) else None
        d = _day(r[col["วันที่"]])
        if amount is None or d is None:
            flags.append(f"แถวลำดับ {r[0]:g}: ไม่มีวันที่หรือยอดเงิน — ไม่นับ")
            continue
        # the fill belongs to the ROUTE (its truck number's own plate), not to the truck that happened to be filled: a driver in a
        # borrowed truck is still doing his route, and his commission is net of his route's fuel (owner, 28 Nov 2026: route #20's
        # driver filled 1ฒฬ 4861 twelve times in November). The plate filled decides only when the row has no route plate.
        own = r[col["ทะเบียนรถประจำเบอร์นี้"]] if "ทะเบียนรถประจำเบอร์นี้" in col and col["ทะเบียนรถประจำเบอร์นี้"] < len(r) else None
        filled = r[col["ทะเบียนรถที่เติมจริง"]]
        plate = own or filled
        if own and filled and norm_plate(str(own)) != norm_plate(str(filled)):
            swapped.append({"date": d.isoformat(), "route": str(cell(r, "รถเบอร์") or "").strip(), "route_plate": str(own).strip(),
                            "filled_plate": str(filled).strip(), "driver": str(cell(r, "คนขับจริงที่เติมวันนี้") or cell(r, "ชื่อที่บันทึกในต้นฉบับ") or "").strip(),
                            "status": str(cell(r, "สถานะ") or "").strip(), "amount": amount})
        number = str(r[col["รถเบอร์"]] or "").strip() if "รถเบอร์" in col and col["รถเบอร์"] < len(r) else ""
        if not str(plate or "").strip() and re.fullmatch(r"\d{1,3}", number):
            no_plate[len(fills)] = f"#{number}"       # no plate at all: the truck number -- used only where one plant fills (below)
        fills.append((d, str(plate or "ไม่ระบุทะเบียน").strip(), amount))
    if not fills:
        return None

    # station: the sheet prefix ("NM บันทึกเติมน้ำมัน"), the title, the file name; the oldest files name none (then the format)
    tab = _station_of(prefix)
    sig = _station_signals(title, f.name)
    if multi:
        sig = [x for x in sig if x["source"] != "ชื่อไฟล์" or x["value"] == tab]          # the name lists every station of the file
    if tab:
        sig.insert(0, {"what": "station", "source": "ชื่อแท็บ", "value": tab, "weight": 2})
    if multi and tab:
        station = tab
        said = [x for x in sig if x["source"] == "หัวไฟล์" and x["value"] != tab]
        if said:
            flags.append(f"หัวไฟล์ของแท็บ {log_name} เขียนสาขา {said[0]['value']} — ใช้ชื่อแท็บ ({tab})")
    else:
        key = "station" if not multi else f"station:{prefix or log_name}"
        picked = f.choices.get(key)
        station, conflicts = value_conflict(sig, "station", {"station": picked} if picked else {}, title="สถานีบางจาก")
        conflicts = [{**c, "key": key} for c in conflicts]
        if not station:
            station = BC_STATIONS[0]
            conflicts = [{"key": key, "message": "ไม่รู้ว่าเป็นไฟล์ของสถานีไหน (ไม่มีชื่อสถานีในไฟล์)",
                          "options": [{"value": x, "label": x} for x in BC_STATIONS], "default": None}]
        elif any(x["source"].startswith("รูปแบบไฟล์") for x in sig) and not picked:
            flags.append(f"ไฟล์ไม่ได้เขียนชื่อสถานี — ถือเป็น {station} จากรูปแบบไฟล์")

    # a row with no plate goes by its truck number only at a station one plant's trucks use (BC_TRUCKS: แก่งคอย); at NM / ห้วยบง both
    # plants fill and truck numbers repeat, so it stays "ไม่ระบุทะเบียน" (flagged, not tied to a truck) as before
    if BC_TRUCKS.get(station):
        fills = [(d, no_plate.get(i, plate), a) for i, (d, plate, a) in enumerate(fills)]

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
    if multi:
        mkey = f"month:{station}"
        chosen = f.choices.get(mkey) or f.choices.get("month")
        month, mc = month_conflict(sig_m, {"month": chosen} if chosen else {})
        mc = [{**c, "key": mkey, "message": f"{station}: {c['message']}"} for c in mc]
    else:
        month, mc = month_conflict(sig_m, f.choices)
    conflicts += mc
    month = month or rows_month

    lo, hi = min(x[0] for x in fills), max(x[0] for x in fills)
    a, b = month_bounds(month)
    outside = [x for x in fills if not (a <= x[0] <= b)]
    if outside:
        days = sorted({x[0] for x in outside})
        flags.append(f"มี {len(outside)} บิล ({sum(x[2] for x in outside):,.2f} บาท) วันที่ {', '.join(dmy(d) for d in days[:6])} "
                     f"อยู่นอกเดือน {month_label(month)} ({dmy(a)}–{dmy(b)}) — รวมไว้ในงวดนี้ตามบิลของสถานี")

    file_total, bills = _bc_file_total(_sheet(book, prefix, BC_CHECK))
    rows_sum = round(sum(x[2] for x in fills), 2)
    if file_total is None:
        flags.append("ไม่พบยอดรวมทั้งงวดในแท็บ ตรวจสอบยอดรวม — เทียบยอดไม่ได้")
    elif abs(file_total - rows_sum) > 1:
        flags.append(f"ยอดรวมที่สถานีเขียนไว้ {file_total:,.2f} บาท แต่รายการรวมได้ {rows_sum:,.2f} บาท — ตรวจไฟล์ก่อนลงบัญชี")
    if bills is not None and int(bills) != len(fills):
        flags.append(f"จำนวนบิลที่สถานีเขียนไว้ {bills:g} แต่ในรายการมี {len(fills)} บิล")

    per: dict = defaultdict(float)
    counts: dict = defaultdict(int)
    for _, plate, amount in fills:
        per[plate] += amount
        counts[norm_plate(plate)] += 1
    return {"station": station, "month": month, "key": f"fuel:bangchak:{month}:{station}", "per": dict(per), "counts": dict(counts),
            "n": len(fills), "lo": lo, "hi": hi, "file_total": file_total, "flags": flags, "conflicts": conflicts, "signals": sig + sig_m,
            "swaps": swapped}


# ------------------------------------------------------------------ the scripts' combined Bangchak entries: deleted, kept, put back
COPY_DIR = "inbox/legacy"              # in the PRIVATE accounting bucket: one JSON copy per month, fuel-bangchak-<month>.json


def legacy_remove_note(month: str, stations) -> str:
    """What the page can say before the owner removes a Bangchak station file that took the month's old combined entries. stations:
    the station of that month in this file (a list for a workbook of several)."""
    mine = [stations] if isinstance(stations, str) else list(stations)
    others = [s for s in LEGACY_STATIONS if s not in mine]
    if not others:                                    # one workbook with both of the old entries' stations: removing it takes both
        return (f"ไฟล์นี้ลบรายการบางจากแบบเก่าของ {month_label(month)} (รวม 2 สถานี) — ลบไฟล์นี้แล้วรายการเดิมจะกลับมา "
                f"ถ้าไม่มีไฟล์บางจาก {' / '.join(LEGACY_STATIONS)} อื่นของเดือนนี้ลงบัญชีอยู่")
    station = " / ".join(mine)
    return (f"ไฟล์นี้ลบรายการบางจากแบบเก่าของ {month_label(month)} (รวม 2 สถานี) — ลบไฟล์นี้แล้วรายการเดิมจะกลับมา "
            f"ถ้ายังไม่ได้ลงไฟล์ของสถานี {others[0]} เดือนเดียวกัน ถ้าลงแล้ว ค่าน้ำมันบางจาก {station} เดือนนี้จะหายไปจนกว่าจะลงไฟล์ใหม่")


def _bc_parts(key: str) -> Optional[tuple[str, str]]:
    """'fuel:bangchak:2026-10:NM' -> ('2026-10', 'NM'); None for any other key."""
    parts = key.split(":")
    if len(parts) == 4 and parts[:2] == ["fuel", "bangchak"] and parts[3] in BC_STATIONS:
        return parts[2], parts[3]
    return None


def _copy_path(month: str) -> str:
    return f"{COPY_DIR}/fuel-bangchak-{month}.json"


def _snapshot(legacy: list[dict]) -> list[dict]:
    """The legacy entries with their lines, in the shape accounting._insert_entries takes (so they can be put back as they were)."""
    from app import supabase
    ids = [r["id"] for r in legacy if r.get("id") is not None]
    lines: dict = defaultdict(list)
    for i in range(0, len(ids), 100):
        for ln in supabase.select_all("acc_lines", {"select": "*", "entry_id": f"in.({','.join(str(x) for x in ids[i:i + 100])})"}):
            lines[ln.get("entry_id")].append({k: ln.get(k) for k in ("account", "debit", "credit", "branch", "vehicle_id", "machine_id", "memo")})
    out = []
    for r in legacy:
        e = {k: r.get(k) for k in ("entry_date", "branch", "source", "source_id", "party", "description", "doc_type", "doc_no")}
        if r.get("doc_id"):
            e["doc_id"] = r["doc_id"]
        e["lines"] = lines.get(r.get("id"), [])
        out.append(e)
    return out


def _save_copy(month: str, entries: list[dict]) -> None:
    import json
    from app import supabase
    data = json.dumps({"month": month, "entries": entries}, ensure_ascii=False, default=str).encode("utf-8")
    supabase.storage_upload(_copy_path(month), data, "application/json", upsert=True, bucket=supabase.ACCOUNTING_BUCKET)


def _load_copy(month: str) -> Optional[list[dict]]:
    """The kept copy of a month's old combined entries, or None (never kept, already put back, or the store cannot be read)."""
    import json
    from app import supabase
    try:
        data = supabase.storage_download(_copy_path(month), bucket=supabase.ACCOUNTING_BUCKET)
    except supabase.SupabaseError:
        return None
    try:
        return list(json.loads(data.decode("utf-8")).get("entries") or []) or None
    except (ValueError, AttributeError):
        return None


def _has_copy(month: str) -> bool:
    """For the preview only: never lets a storage hiccup stop a file from being read."""
    try:
        return bool(_load_copy(month))
    except Exception:
        return False


def _station_entries(month: str) -> bool:
    """Is any NM / ห้วยบง station file's Bangchak fuel of this month in the books ("fuel:bangchak:<month>:<station>:<plate>")? (แก่งคอย's
    never stood in the old combined entries: it neither keeps them away nor is counted twice with them.)"""
    from app import supabase
    pre = f"fuel:bangchak:{month}:"
    rows = supabase.select_all("acc_entries", {"select": "id,source_id", "source_id": f"like.{pre}*", "order": "id.asc"})
    return any((r.get("source_id") or "")[len(pre):].split(":", 1)[0] in LEGACY_STATIONS
               and ":" in (r.get("source_id") or "")[len(pre):] for r in rows)


def book(preview: dict, file_id: str) -> dict:
    """Replace this file's batches; for a Bangchak station file also delete the scripts' combined (both-station) entries of the month --
    after keeping a copy of them (lines and all), so removing the station files of the month puts them back."""
    from app import supabase
    from app.accounting import _require
    _require()
    removed = 0
    for b in preview.get("batches") or []:
        got = _bc_parts(b["key"])
        if not got or got[1] not in LEGACY_STATIONS:
            continue
        legacy = _legacy_bangchak(got[0])
        if not legacy:
            continue                                  # already taken by an earlier station file: its copy is kept as it is
        _save_copy(got[0], _snapshot(legacy))         # the copy first: if it cannot be written, nothing is deleted
        ids = [r["id"] for r in legacy]
        for i in range(0, len(ids), 100):
            supabase.delete("acc_entries", {"id": f"in.({','.join(str(x) for x in ids[i:i + 100])})"})
        removed += len(ids)
    res = book_batches(preview)
    res["legacy_removed"] = removed
    return res


def unbook(preview: dict) -> None:
    """Delete this file's batches; then, when no station file of the month is left in the books, the month's old combined Bangchak
    entries come back from the kept copy (with the other station's file still booked they would count that station twice: the copy
    waits for that file to be removed too)."""
    from app import supabase
    from app.accounting import _insert_entries
    unbook_batches(preview)
    for b in preview.get("batches") or []:
        got = _bc_parts(b["key"])
        if not got or got[1] not in LEGACY_STATIONS:
            continue
        month = got[0]
        if _station_entries(month) or _legacy_bangchak(month):
            continue
        snap = _load_copy(month)
        if not snap:
            continue
        _insert_entries(snap)
        supabase.storage_delete([_copy_path(month)], bucket=supabase.ACCOUNTING_BUCKET)


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
