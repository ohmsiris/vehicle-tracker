"""Load a transcribed batch of mid / end-of-month documents into the books.

    python scripts/import_acc_docs.py data/acc_docs/2026_10_saraburi.py          # dry run: page checks only
    python scripts/import_acc_docs.py data/acc_docs/2026_10_saraburi.py --write  # replace the batch in the database
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_docs, supabase  # noqa: E402


def load(path: str) -> dict:
    spec = importlib.util.spec_from_file_location("batch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.BATCH


def main() -> None:
    batch = load(sys.argv[1])
    if "--write" in sys.argv:
        print(acc_docs.import_batch(batch))
        return
    vehicles = supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch", "order": "id.asc"})
    out = acc_docs.build(batch, vehicles)
    for d in out["docs"]:
        if d["kind"] in ("expense", "fuel", "advance", "cover", "fuel_table"):
            print(f"{d['page']:>4} {d['kind']:<10} {d['status']:<8} read {d['read_total']:>10,.0f} written {d['written_total'] or 0:>10,.0f}  {d['title']}")
    print(len(out["entries"]), "entries")
    for p in out["problems"]:
        print("!", p)


if __name__ == "__main__":
    main()
