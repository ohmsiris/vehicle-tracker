"""คุณรัชนิดา's cheque books (the owner's mother pays the parts / tyre shops from two books) -> the books, as a TOP-UP over the bills.

    python scripts/import_mum_cheques.py [--write]

The real bills (truck repairs and factory purchases entered in the app) are the source of truth. Each cheque-sheet line is one shop's
total for one month; only what the app's bills for that shop and month do NOT already cover is booked (source mum_cheques, marked
"ส่วนที่ยังไม่มีบิล"). Add more bills in the app, run this again, and the top-up shrinks to nothing. Re-running replaces every top-up.

Also books:
  * the truck hire-purchase instalment (งวดรถ) 431,108 a month, Saraburi, paid by the bank (source loan) -- owner, 9 Nov 2026
  * an approximate monthly figure for workshops paid in cash (shops not on the cheque books: ชมรมย์, Isuzu, ...) for the months before
    their bills are in the app, from the average of the months that have them (source repair_estimate, "ประมาณ")
"""
import os
import re
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import supabase  # noqa: E402

# shop key -> (match pattern on the bill's party, expense account, has VAT invoice)
SHOPS = {
    "โชคพัฒนา": (r"โชคพัฒนา", "6110", True),
    "ปัญจบวร": (r"ปัญจบวร", "6100", True),
    "สุขอนันต์": (r"สุขอนันต์", "5510", True),
    "วิศวภัณฑ์": (r"วิศวภัณฑ์|วิศวะภัณฑ์", "5510", False),
    "วิบูลย์เจริญ": (r"วิบูลย์เจริญ", "5510", True),
    "สระบุรีอะไหล่": (r"สระบุรีอะไหล่", "6100", False),
    "ดร ไดนาโม": (r"ไดนาโม แอร์|ดร\.? ?ไดนาโม", "6100", False),
}
CO, MUM, CASH = "1110", "1120", "1100"     # company book, คุณรัชนิดา's book, paid in cash
S, K = "สระบุรี", "แก่งคอย"
# (month, branch, shop, amount, paid from) -- read from the owner's photos of the cheque sheets, 9 Nov 2026
SHEETS = [
    ("2026-01", S, "โชคพัฒนา", 69052, CO), ("2026-01", S, "ปัญจบวร", 62182, CO), ("2026-01", S, "สุขอนันต์", 8914, CO),
    ("2026-01", K, "วิศวภัณฑ์", 37547, MUM),
    ("2026-02", S, "โชคพัฒนา", 9600, CO), ("2026-02", S, "ปัญจบวร", 30549, CO), ("2026-02", S, "สุขอนันต์", 2526, CO),
    ("2026-02", S, "สระบุรีอะไหล่", 290, CASH), ("2026-02", S, "วิศวภัณฑ์", 30885, MUM),
    ("2026-03", S, "โชคพัฒนา", 58399 + 8856, CO), ("2026-03", S, "ปัญจบวร", 16589, CO),
    ("2026-03", S, "สระบุรีอะไหล่", 900, MUM), ("2026-03", S, "วิศวภัณฑ์", 27655, MUM), ("2026-03", S, "วิบูลย์เจริญ", 3372, MUM),
    ("2026-04", S, "โชคพัฒนา", 78390, CO), ("2026-04", S, "ปัญจบวร", 52989, CO),
    ("2026-04", S, "สระบุรีอะไหล่", 1520, CASH), ("2026-04", S, "วิศวภัณฑ์", 22824, MUM),
    ("2026-05", S, "โชคพัฒนา", 90769, CO), ("2026-05", S, "ปัญจบวร", 51769, CO), ("2026-05", S, "สุขอนันต์", 64635, CO),
    ("2026-05", S, "สระบุรีอะไหล่", 2800, MUM), ("2026-05", S, "วิศวภัณฑ์", 63770, MUM), ("2026-05", S, "วิบูลย์เจริญ", 6308, MUM),
    ("2026-06", S, "โชคพัฒนา", 28473, CO), ("2026-06", S, "ปัญจบวร", 51065, CO), ("2026-06", S, "สุขอนันต์", 36632, CO),
    ("2026-06", S, "วิศวภัณฑ์", 35354, MUM),
    ("2026-07", S, "โชคพัฒนา", 68436, CO), ("2026-07", S, "ปัญจบวร", 50143, CO), ("2026-07", S, "สุขอนันต์", 35253, CO),
    ("2026-07", S, "สระบุรีอะไหล่", 160, CASH), ("2026-07", S, "วิศวภัณฑ์", 37486, MUM),
    ("2026-08", S, "โชคพัฒนา", 53003, CO), ("2026-08", S, "ปัญจบวร", 44305, CO), ("2026-08", S, "สุขอนันต์", 14745, CO),
    ("2026-08", S, "วิศวภัณฑ์", 45984, MUM), ("2026-08", S, "วิบูลย์เจริญ", 36599, MUM), ("2026-08", S, "ดร ไดนาโม", 3893, MUM),
    ("2026-09", S, "โชคพัฒนา", 135371, CO), ("2026-09", S, "ปัญจบวร", 21939, CO), ("2026-09", S, "สุขอนันต์", 49969, CO),
    ("2026-09", S, "วิศวภัณฑ์", 16147, MUM), ("2026-09", S, "วิบูลย์เจริญ", 29941, MUM),
    ("2026-10", S, "โชคพัฒนา", 99430 + 4450, CO), ("2026-10", S, "ปัญจบวร", 31139, CO), ("2026-10", S, "สุขอนันต์", 10941, CO),
    ("2026-10", S, "สระบุรีอะไหล่", 1200, CASH), ("2026-10", S, "วิศวภัณฑ์", 14920, MUM), ("2026-10", S, "วิบูลย์เจริญ", 21577, MUM),
]
INSTALMENT = 431108
BANK_LOAN = 1050000          # owner, 9 Nov 2026: the bank takes 1,050,000 a month (interest inside); ends Mar 2027
INSTALMENT_MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]
ACC_INSTALMENT = ("6400", "งวดรถ (เช่าซื้อรถ)")


def ym(d: str) -> str:
    return d[:7]


def cm(d: date) -> str:
    """Company month (26th - 25th)."""
    if d.day <= 25:
        return f"{d.year}-{d.month:02d}"
    return f"{d.year + 1}-01" if d.month == 12 else f"{d.year}-{d.month + 1:02d}"


def app_bills() -> list[dict]:
    """Every truck-repair / purchase entry the app made from a bill: {date, branch, party, gross}."""
    out = []
    for src in ("truck_bill", "purchase"):
        for e in supabase.select_all("acc_entries", {"select": "entry_date,branch,party,acc_lines(debit,account)", "source": f"eq.{src}", "order": "id.asc"}):
            out.append({"date": e["entry_date"], "branch": e["branch"], "party": e.get("party") or "",
                        "gross": sum(float(ln["debit"]) for ln in e["acc_lines"]), "src": src})
    return out


def main() -> None:
    bills = app_bills()
    covered = defaultdict(float)
    for b in bills:
        for key, (pat, _, _) in SHOPS.items():
            if re.search(pat, b["party"]):
                covered[(ym(b["date"]), b["branch"], key)] += b["gross"]
    entries = []
    print("## top-up over the bills")
    for month, branch, shop, amount, paid in SHEETS:
        _, acc, vat = SHOPS[shop]
        have = round(covered.get((month, branch, shop), 0.0), 2)
        rest = round(amount - have, 2)
        print(f"   {month} {branch:<8} {shop:<14} sheet {amount:>10,.0f}  bills {have:>10,.0f}  book {max(rest, 0):>10,.0f}")
        if rest <= 0.5:
            continue
        net = round(rest / 1.07, 2) if vat else rest
        y, m = (int(x) for x in month.split("-"))
        lines = [{"account": acc, "debit": net, "credit": 0.0, "branch": branch}]
        if vat:
            lines.append({"account": "1400", "debit": round(rest - net, 2), "credit": 0.0, "branch": branch})
        lines.append({"account": paid, "debit": 0.0, "credit": rest, "branch": branch})
        entries.append({"entry_date": date(y, m, 25).isoformat(), "branch": branch, "source": "mum_cheques",
                        "source_id": f"mum:{month}:{branch}:{shop}", "party": shop, "doc_type": "cheque", "doc_no": None,
                        "description": f"{shop} {m:02d}/{y + 543} · ยอดในใบสรุปจ่ายเช็ค {amount:,.0f} · ส่วนที่ยังไม่มีบิลในแอป {rest:,.0f}",
                        "lines": lines})

    print("## truck instalment")
    for month in INSTALMENT_MONTHS:
        y, m = (int(x) for x in month.split("-"))
        entries.append({"entry_date": date(y, m, 25).isoformat(), "branch": S, "source": "loan", "source_id": f"instalment:{month}",
                        "party": "งวดรถ (เช่าซื้อ)", "doc_type": "bank", "doc_no": None,
                        "description": f"งวดรถ เช่าซื้อ {m:02d}/{y + 543} · ธนาคารหัก 431,108 (ทั้งก้อน ยังไม่แยกดอกเบี้ย/เงินต้น)",
                        "lines": [{"account": ACC_INSTALMENT[0], "debit": INSTALMENT, "credit": 0.0, "branch": S},
                                  {"account": "1110", "debit": 0.0, "credit": INSTALMENT, "branch": S}]})

    print("## bank loan")
    for month in INSTALMENT_MONTHS:
        y, m = (int(x) for x in month.split("-"))
        entries.append({"entry_date": date(y, m, 25).isoformat(), "branch": S, "source": "loan", "source_id": f"bankloan:{month}",
                        "party": "เงินกู้ธนาคาร", "doc_type": "bank", "doc_no": None,
                        "description": f"ผ่อนเงินกู้ธนาคาร {m:02d}/{y + 543} · ธนาคารหัก 1,050,000 (รวมดอกเบี้ย ยังไม่แยก) · หมด มี.ค. 2570",
                        "lines": [{"account": "6410", "debit": BANK_LOAN, "credit": 0.0, "branch": S},
                                  {"account": "1110", "debit": 0.0, "credit": BANK_LOAN, "branch": S}]})

    print("## workshops paid in cash (not on the cheque books): estimate the months with no bills")
    other = defaultdict(float)
    for b in bills:
        if b["src"] != "truck_bill" or "ร้านนัท" in b["party"] or any(re.search(p, b["party"]) for p, _, _ in SHOPS.values()):   # ร้านนัท: one-off engines / axles
            continue
        other[(cm(date.fromisoformat(b["date"])), b["branch"])] += b["gross"]
    full_from = {S: "2026-08", K: "2026-10"}      # bills are entered month after month from here on
    for branch in (S, K):
        base = sorted(m for (m, br) in other if br == branch and full_from[branch] <= m <= "2026-10")
        if not base:
            continue
        avg = round(sum(other[(m, branch)] for m in base) / len(base), -3)
        print(f"   {branch}: full bills in {base} -> about {avg:,.0f} a month")
        for m in range(1, 11):
            month = f"2026-{m:02d}"
            if month >= full_from[branch]:
                break
            amt = round(avg - other.get((month, branch), 0.0), -2)
            print(f"      {month}: bills already {other.get((month, branch), 0):,.0f} -> estimate {max(amt, 0):,.0f}")
            if amt <= 0:
                continue
            avg_m = amt
            entries.append({"entry_date": date(2026, m, 25).isoformat(), "branch": branch, "source": "repair_estimate",
                            "source_id": f"repair-est:{month}:{branch}", "party": "อู่ซ่อมรถ (จ่ายเงินสด)", "doc_type": "estimate", "doc_no": None,
                            "description": f"ค่าซ่อมรถ อู่ที่จ่ายเงินสด (ประมาณ) {m:02d}/2569 · ค่าเฉลี่ยจากบิลเดือน {', '.join(base)} {avg:,.0f} ลบบิลที่มีแล้ว",
                            "lines": [{"account": "6100", "debit": avg_m, "credit": 0.0, "branch": branch},
                                      {"account": CASH, "debit": 0.0, "credit": avg_m, "branch": branch}]})

    total = defaultdict(float)
    for e in entries:
        total[e["source"]] += sum(ln["debit"] for ln in e["lines"])
    print({k: round(v) for k, v in total.items()}, len(entries), "entries")
    if "--write" in sys.argv:
        from app.accounting import _insert_entries, _require
        _require()
        if not supabase.select("acc_accounts", {"select": "code", "code": f"eq.{ACC_INSTALMENT[0]}"}):
            supabase.insert("acc_accounts", [{"code": ACC_INSTALMENT[0], "name_th": ACC_INSTALMENT[1], "name_en": "Truck hire-purchase instalments",
                                              "kind": "expense", "section": "admin", "sort_order": 640, "active": True}])
        for src in ("mum_cheques", "loan", "repair_estimate"):
            supabase.delete("acc_entries", {"source": f"eq.{src}"})
        _insert_entries(entries)
        print("written")


if __name__ == "__main__":
    main()
