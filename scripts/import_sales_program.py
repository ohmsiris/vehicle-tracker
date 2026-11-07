"""The ice program's "รายงานสรุปยอดสินค้าต่อลูกค้ารายวัน" PDF -> sales_lines (every line) + the books (app/acc_sales.py).

    python scripts/import_sales_program.py "<report.pdf>" สระบุรี|แก่งคอย 2026-01 2026-10 [--write]

Re-running replaces that branch's program lines and its booked months. The report must read cleanly (every day ties to its total).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_sales, sales_report  # noqa: E402


def main() -> None:
    path, branch, first, last = sys.argv[1:5]
    import pymupdf
    text = "\n=====PAGE\n".join(p.get_text() for p in pymupdf.open(path))
    r = sales_report.parse(text)
    if r["problems"]:
        print("\n".join(r["problems"][:20]))
        sys.exit(f"{len(r['problems'])} problems reading the report -- nothing written")
    sales = acc_sales.lines(branch, r["rows"])
    entries = acc_sales.build(branch, sales, (first, last))
    booked = sum(e["lines"][0]["debit"] for e in entries if not e["source_id"].endswith(":other"))
    print(f"{branch}: {len(sales)} lines, {sales[0]['sale_date']} .. {sales[-1]['sale_date']}; {len(entries)} entries, {booked:,.2f} booked {first}..{last}")
    if "--write" in sys.argv:
        from app import supabase
        from app.accounting import _insert_entries, _require
        _require()
        supabase.delete("sales_lines", {"branch": f"eq.{branch}", "source": "eq.program"})
        for i in range(0, len(sales), 1000):
            supabase.insert("sales_lines", sales[i:i + 1000])
        supabase.delete("acc_entries", {"source": "eq.sales_program", "source_id": f"like.sales:{branch}:*"})
        _insert_entries(entries)
        print("written", len(sales), "lines,", len(entries), "entries")


if __name__ == "__main__":
    main()
