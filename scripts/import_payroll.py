"""Payroll summaries -> the books.

    python scripts/import_payroll.py "<folder with 01..12 month folders>" 2026 [--write]

Each month folder holds that month's emailed files; a folder named "MM-แก้ไข-..." (corrected files) replaces the same group in "MM".
Without --write it prints what it would book. Re-running replaces the payroll entries of those months.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_payroll  # noqa: E402

# January 2569's Saraburi sheets use an older layout (different column order): the split is taken from their "สรุปรายหัก" box.
# Order: สะสม, เบิกล่วงหน้า, คืนเงินกู้, ส่วนกลาง, passport / ทำบัตร, ความผิด, อุปกรณ์, ปกส.
PARTS = {("2026-01", "FAC"): [5500, 162000, 7500, 4008, 0, 0, 0, 5045],
         ("2026-01", "DRV"): [6000, 84500, 4500, 3111, 0, 10560, 0, 3405]}


def main() -> None:
    root, year = sys.argv[1], sys.argv[2]
    found = {}                                          # (month, group) -> path; corrected folders win
    for d in sorted(os.listdir(root)):
        mm = d[:2]
        for f in os.listdir(os.path.join(root, d)):
            g = acc_payroll.group_of(f)
            if g and ((f"{year}-{mm}", g) not in found or "แก้ไข" in d):
                found[(f"{year}-{mm}", g)] = os.path.join(root, d, f)
    import pymupdf
    entries = []
    for (month, g), path in sorted(found.items()):
        doc = pymupdf.open(path)
        s = acc_payroll.parse_summary([ln for page in doc for ln in page.get_text().splitlines()])
        if (month, g) in PARTS and not s["parts"]:
            s["parts"] = [float(x) for x in PARTS[(month, g)]]
        e = acc_payroll.build_entry(month, g, s, os.path.basename(path))
        entries.append(e)
        back = sum(s["reimburse"])
        print(f"{month} {g:<3} wages {s['gross'] - back:>11,.0f}  (petty cash back {back:>9,.0f})  ded {s['deductions']:>9,.0f}  {'ok' if s['parts'] else 'NO BREAKDOWN'}")
    if "--write" in sys.argv:
        from app import supabase
        from app.accounting import _insert_entries, _require
        _require()
        for e in entries:
            supabase.delete("acc_entries", {"source_id": f"eq.{e['source_id']}"})
        _insert_entries(entries)
        print("written", len(entries))


if __name__ == "__main__":
    main()
