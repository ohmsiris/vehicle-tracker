"""One-off (28 Nov 2026): put the Thai right in names already stored from the ice program's reports (sales_report.fix_thai) --
sales_lines.customer and the sales_program entries' party / description / source_id -- so new imports (fixed in text_of) keep matching.
Run: python scripts/fix_sales_thai.py [--write]"""
import sys

from app import supabase
from app.sales_report import fix_thai


def main(write: bool) -> None:
    names = {r["customer"] for r in supabase.select_all("sales_lines", {"select": "customer", "order": "customer.asc"})}
    bad = sorted(n for n in names if n and fix_thai(n) != n)
    for n in bad:
        rows = supabase.select_all("sales_lines", {"select": "id", "customer": f"eq.{n}", "order": "id.asc"})
        print(f"sales_lines {len(rows):5d}  {n} -> {fix_thai(n)}")
        if write:
            supabase.update("sales_lines", {"customer": f"eq.{n}"}, {"customer": fix_thai(n)})
    entries = supabase.select_all("acc_entries", {"select": "id,party,description,source_id", "source": "eq.sales_program", "order": "id.asc"})
    changed = [e for e in entries if any(fix_thai(e.get(k)) != e.get(k) for k in ("party", "description", "source_id"))]
    print(f"acc_entries {len(changed)} of {len(entries)} sales_program entries")
    if write:
        for e in changed:
            supabase.update("acc_entries", {"id": f"eq.{e['id']}"}, {k: fix_thai(e.get(k)) for k in ("party", "description", "source_id")})


if __name__ == "__main__":
    main("--write" in sys.argv)
