"""
กล่องรับไฟล์ › รายงานขายจากโปรแกรมหน้าโรง (owner, 24 Nov 2026): the ice program's "รายงานสรุปยอดสินค้าต่อลูกค้ารายวัน" PDF.

What it is, from inside: the column titles ("สินค้า / ลูกค้า", "ราคา/หน่วย", "ราคารวม") and the day totals ("ผลรวม :"), read by
app/sales_report.parse (every day ties to its printed total, every product to its printed grand total).

Branch: never from the file name. Two independent signals --
  * the footer: "บริษัท สระบุรีวารีเทพ จำกัด(สาขาแก่งคอย)" for Kaeng Khoi, the bare company name for the head office (Saraburi);
  * the customers: the names in the file against the names each branch's program reports had in the last months (sales_lines).
Disagreement is a conflict the owner answers ({"branch": ...}).

Months: from the day rows, by company month (26th-25th). Only months the report covers completely are booked; an edge month the report
starts or ends inside (the report printed on 7 Nov holds 26 Oct - 7 Nov of November) is flagged and left alone -- whatever was booked for
it before stays. {"partial": "book"} books them anyway. The books start on 1 Jan 2026, so company month 2026-01 (26 Dec - 25 Jan) is
complete from 1 Jan (the program export for the year starts there, and that is how the books were first filled).

Replace, never add, and ONLY the months in the file: batch key "sales:<branch>:<month>" -- the same keys scripts/import_sales_program.py
wrote ("sales:<branch>:<month>:<customer>", "...:other" for the receiving branch's side, "...:invoice:cp[:est]" for Kaeng Khoi's CP),
so dropping the report replaces what the script booked. (The script wiped EVERY month of the branch; this does not.) book / unbook also
replace sales_lines (source 'program') for exactly those branch-months -- the unit-cost page and the home page read them.

Roles (one row per customer per month):
  * revenue  -- Dr 1200 / Cr 4100 (app/acc_sales.build);
  * transfer -- ice to the company's other branch (Kaeng Khoi's "สระบุรี" / "ทวาย", Saraburi's "...แก่งคอย"): Dr 1600 / Cr 4150 here,
                Dr 5060 / Cr 1600 at the other branch -- not revenue of the company;
  * info     -- in the file but booked elsewhere: Saraburi's route-truck "customers" ("58.เมืองสระบุรี-...", booked from the drivers'
                sheets) and Kaeng Khoi's CP / CPF deliveries (booked from the tax-invoice book, owner 9 Nov 2026: the program carries
                price 0 for several CP plants);
  * revenue rows flagged "จากใบกำกับภาษี" -- Kaeng Khoi's CP / CPF revenue for the month (app/acc_cp_invoice.build from the
                #tax-invoices page, spread over the program's delivery days; "ประมาณ" when the month has no invoices yet). Those rows are
                not in the file, so they are left out of rows_total (which ties to the file's own day totals, file_total) and summed
                in "extra_total".
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from app import acc_sales, sales_report
from app.inbox_common import (BRANCHES, Dropped, book_batches, empty_preview, month_label, shift_month, unbook_batches)
from app.inbox_r_fuel import dmy, month_bounds, months_between, value_conflict

KIND = "sales_program"
LABEL = "รายงานขายหน้าโรง (โปรแกรมขาย)"
FEEDS = ("sales_program",)          # not an accounting.FEEDS row (that checklist has no sales row yet); home.py's feed key

BOOKS_START = date(2026, 1, 1)
TITLE = "รายงานสรุปยอดสินค้าต่อลูกค้ารายวัน"
KK = "แก่งคอย"


def sniff(f: Dropped) -> float:
    t = f.text or ""
    if f.ext != ".pdf" or f.is_scan:
        return 0.0
    cols = sum(c in t for c in ("สินค้า / ลูกค้า", "ราคา/หน่วย", "ราคารวม", "ผลรวม"))
    if TITLE in t and cols >= 2:
        return 0.97
    return 0.9 if cols == 4 else 0.0


# ------------------------------------------------------------------ signals
def _known_customers(first: str, last: str) -> dict:
    """{branch: set of customer names} the program reports had in the file's last three months (sales_lines)."""
    from app import supabase
    out: dict = defaultdict(set)
    lo = max(first, shift_month(last, -2))
    for r in supabase.select_all("sales_lines", {"select": "branch,customer", "month": f"gte.{lo}", "and": f"(month.lte.{last})",
                                                 "source": "eq.program", "order": "id.asc"}):
        out[r["branch"]].add(r["customer"])
    return out


def _customer_signal(names: set, known: dict) -> Optional[dict]:
    only = {b: len(names & (known.get(b, set()) - set().union(*(known.get(o, set()) for o in BRANCHES if o != b)))) for b in BRANCHES}
    best = max(only, key=only.get)
    rest = max(v for b, v in only.items() if b != best)
    if only[best] >= 3 and only[best] >= 3 * rest:
        return {"what": "branch", "source": f"รายชื่อลูกค้า ({only[best]} ชื่อเคยอยู่ในรายงานของสาขานี้)", "value": best}
    return None


# ------------------------------------------------------------------ reading
def _parse(data: bytes) -> tuple[str, dict]:
    text = sales_report.text_of(data)
    return text, sales_report.parse(text)


def _complete(month: str, lo: date, hi: date) -> bool:
    a, b = month_bounds(month)
    return lo <= max(a, BOOKS_START) and hi >= b


def _kk_cp_entries(file_lines: list[dict], lo: date, hi: date, first: str, last: str) -> dict:
    """Kaeng Khoi's CP / CPF entries for the booked months, by month. The invoices are spread over a whole CALENDAR month's delivery
    days, so the program lines of the days around the file come from sales_lines (what earlier reports stored) and the file's own days
    from the file."""
    from app import acc_cp_invoice, supabase, tax_invoices
    stored = supabase.select_all("sales_lines", {"select": "sale_date,customer,amount", "branch": f"eq.{KK}", "source": "eq.program",
                                                 "order": "id.asc"})
    sales = [s for s in stored if not (lo.isoformat() <= str(s["sale_date"])[:10] <= hi.isoformat())] + file_lines
    out: dict = defaultdict(list)
    for e in acc_cp_invoice.build(tax_invoices.book(), sales, first, last):
        out[e["source_id"].split(":")[2]].append(e)
    return out


def read(f: Dropped) -> dict:
    p = empty_preview(KIND, LABEL)
    text, r = _parse(f.data)
    if r["problems"]:
        p["flags"] += [f"อ่านไม่ตรงยอด: {x}" for x in r["problems"][:20]]
        p["note"] = f"อ่านรายงานไม่ครบ ({len(r['problems'])} จุดไม่ตรงกับยอดที่พิมพ์ไว้) — ไม่ลงบัญชี"
        return p
    if not r["rows"]:
        p.update(store_only=True, note="รายงานนี้ไม่มีรายการขาย")
        return p

    days = sorted({x["date"] for x in r["rows"]})
    lo, hi = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    months = months_between(lo, hi)
    printed = sales_report.printed_on(text)
    p["signals"].append({"what": "range", "source": "วันที่ในรายงาน", "value": f"{dmy(lo)} - {dmy(hi)}"})
    if printed:
        p["signals"].append({"what": "printed", "source": "วันที่พิมพ์", "value": dmy(printed)})
        if days[-1] > printed:
            p["flags"].append(f"มีรายการวันที่ {dmy(days[-1])} หลังวันที่พิมพ์รายงาน {dmy(printed)} — ตรวจไฟล์")

    # branch: the footer and the customers
    sig = []
    foot = sales_report.branch_in(text)
    if foot:
        sig.append({"what": "branch", "source": "ท้ายรายงาน (ชื่อบริษัท/สาขา)", "value": foot, "weight": 2})
    names = {x["customer"] for x in r["rows"]}
    cs = _customer_signal(names, _known_customers(months[0], months[-1]))
    if cs:
        sig.append(cs)
    branch, conflicts = value_conflict(sig, "branch", f.choices, title="สาขา")
    if branch and branch not in BRANCHES:
        conflicts = [{"key": "branch", "message": f"ท้ายรายงานบอกสาขา \"{branch}\" ซึ่งไม่ใช่สาขาที่รู้จัก",
                      "options": [{"value": b, "label": b} for b in BRANCHES], "default": None}]
        branch = None
    if not branch:
        conflicts = conflicts or [{"key": "branch", "message": "ไม่รู้ว่าเป็นรายงานของสาขาไหน (ไม่มีชื่อสาขาท้ายรายงาน และรายชื่อลูกค้าไม่ชัด)",
                                   "options": [{"value": b, "label": b} for b in BRANCHES], "default": None}]
        p.update(signals=p["signals"] + sig, conflicts=conflicts, note="เลือกสาขาก่อน แล้วจะแสดงรายการ")
        return p
    p["signals"] += sig
    p["conflicts"] += conflicts

    lines = acc_sales.lines(branch, r["rows"])
    p["file_total"] = round(sum(t["amount"] for t in r["totals"]), 2)
    p["rows_total"] = round(sum(x["amount"] for x in r["rows"]), 2)
    if abs(p["file_total"] - p["rows_total"]) > 1:
        p["flags"].append(f"ยอดรวมท้ายรายงาน {p['file_total']:,.2f} แต่รายการรวมได้ {p['rows_total']:,.2f}")

    partial_ok = f.choices.get("partial") == "book"
    booked = []
    for m in months:
        if _complete(m, lo, hi):
            booked.append(m)
            continue
        a, b = month_bounds(m)
        amt = sum(x["amount"] for x in lines if x["month"] == m)
        msg = (f"{month_label(m)}: รายงานมีแค่ {dmy(max(a, lo))}–{dmy(min(b, hi))} ไม่ครบเดือน ({dmy(a)}–{dmy(b)}) "
               f"ยอด {amt:,.2f} บาท")
        if partial_ok:
            booked.append(m)
            p["flags"].append(msg + " — ลงตามที่เลือก (แทนที่ยอดทั้งเดือน)")
        else:
            p["flags"].append(msg + " — ไม่ลงบัญชี (ยอดเดิมของเดือนนี้ไม่ถูกแตะ)")
    if not booked:
        p.update(store_only=True, note=f"รายงาน{branch} {dmy(lo)}–{dmy(hi)} ไม่ครบเดือนใดเลย (26–25) — เก็บไว้ ไม่ลงบัญชี")
        return p

    cp = _kk_cp_entries(lines, lo, hi, booked[0], booked[-1]) if branch == KK else {}
    day_total: dict = defaultdict(float)
    for d in r["days"]:
        day_total[acc_sales.company_month(d["date"])] += d["amount"]
    for m in booked:
        p["batches"].append(_batch(branch, m, [x for x in lines if x["month"] == m], cp.get(m, []), round(day_total[m], 2)))
    p["months"] = booked
    p["month"] = booked[-1]
    p["signals"].append({"what": "month", "source": "วันที่ในรายงาน (เดือนล่าสุดที่ครบ)", "value": booked[-1]})
    p["note"] = (f"รายงานขาย{branch} {dmy(lo)}–{dmy(hi)}: ลงบัญชี {len(booked)} เดือน ({', '.join(month_label(m) for m in booked)}) "
                 "แทนที่ยอดเดิมของเดือนเหล่านั้นเท่านั้น")
    return p


def _batch(branch: str, month: str, ms: list[dict], cp_entries: list[dict], file_total: float) -> dict:
    kk = branch == KK
    entries = acc_sales.build(branch, ms, (month, month), skip_cp=kk) + cp_entries
    per: dict = defaultdict(float)
    for s in ms:
        per[s["customer"]] += s["amount"]
    rows = []
    when = f"{month}-25"
    for cust, amt in sorted(per.items(), key=lambda kv: -kv[1]):
        amt = round(amt, 2)
        if not amt:
            continue                                   # tanks lent (ยืมถัง...): 0 baht, kept in sales_lines only
        if acc_sales.is_route(branch, cust):
            role, acct, flag = "info", None, "ยอดรถสาย — นับจากใบขายคนขับแล้ว ไม่นับซ้ำ"
        elif kk and acc_sales._cp_channel(cust):
            role, acct, flag = "info", None, "CP/CPF — ลงตามใบกำกับภาษีแทน (แถวด้านล่าง)"
        elif acc_sales.is_internal(branch, cust):
            role, acct, flag = "transfer", "4150", f"น้ำแข็งให้สาขา{acc_sales.OTHER[branch]} — ไม่ใช่รายได้ของบริษัท"
        else:
            role, acct, flag = "revenue", "4100", None
        rows.append({"date": when, "text": cust, "party": cust, "amount": amt, "role": role, "account": acct, "flag": flag})
    rows_total = round(sum(x["amount"] for x in rows), 2)
    extra = 0.0
    for e in cp_entries:
        amt = round(sum(ln["debit"] for ln in e["lines"]), 2)
        extra += amt
        est = e["source_id"].endswith(":est")
        rows.append({"date": e["entry_date"], "text": e["description"], "party": e["party"], "amount": amt, "role": "revenue",
                     "account": "4100", "source": "invoice",
                     "flag": "ประมาณ — ยังไม่มีใบกำกับเดือนนี้" if est else "จากใบกำกับภาษี (ไม่อยู่ในไฟล์นี้)"})
    return {"key": f"sales:{branch}:{month}", "feed": "sales_program", "source": "sales_program", "branch": branch, "month": month,
            "title": f"ขายหน้าโรง{branch} {month_label(month)} · {len(per)} ลูกค้า", "rows": rows, "file_total": file_total,
            "rows_total": rows_total, "extra_total": round(extra, 2), "line_count": len(ms), "entries": entries}


# ------------------------------------------------------------------ booking (entries + sales_lines of exactly these branch-months)
def _file_bytes(file_id: str) -> bytes:
    """The dropped file itself (the preview does not carry 20,000 program lines): inbox_files.storage_path in the private bucket."""
    from app import supabase
    row = supabase.select("inbox_files", {"select": "storage_path", "id": f"eq.{file_id}", "limit": "1"})
    if not row:
        raise ValueError("ไม่พบไฟล์ที่อัปโหลด")
    return supabase.storage_download(row[0]["storage_path"], bucket=supabase.ACCOUNTING_BUCKET)


def _delete_lines(preview: dict) -> None:
    from app import supabase
    for b in preview.get("batches") or []:
        supabase.delete("sales_lines", {"branch": f"eq.{b['branch']}", "month": f"eq.{b['month']}", "source": "eq.program"})


def book(preview: dict, file_id: str) -> dict:
    from app import supabase
    from app.accounting import InvalidEntry, _require
    _require()
    batches = preview.get("batches") or []
    _, r = _parse(_file_bytes(file_id))
    want = {b["month"]: b for b in batches}
    branch = batches[0]["branch"] if batches else None
    lines = [x for x in acc_sales.lines(branch, r["rows"]) if x["month"] in want] if branch else []
    got: dict = defaultdict(int)
    for x in lines:
        got[x["month"]] += 1
    for m, b in want.items():
        if b.get("line_count") is not None and got[m] != b["line_count"]:
            raise InvalidEntry(f"ไฟล์ที่เก็บไว้อ่านได้ {got[m]} รายการใน {month_label(m)} แต่ตัวอย่างมี {b['line_count']} — อ่านไฟล์ใหม่ก่อนลงบัญชี")
    for b in batches:                                    # a preview read before 28 Nov 2026 still has the font's stand-ins in its names
        for e in b.get("entries") or []:
            for k in ("party", "description", "source_id"):
                e[k] = sales_report.fix_thai(e.get(k))
    out = book_batches(preview)
    _delete_lines(preview)
    for i in range(0, len(lines), 1000):
        supabase.insert("sales_lines", lines[i:i + 1000])
    return out


def unbook(preview: dict) -> None:
    unbook_batches(preview)
    _delete_lines(preview)
