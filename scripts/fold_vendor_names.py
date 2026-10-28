"""
Store every saved bill's shop under the shop's own name (app/shops.py fold_vendor): "บริษัท จึงกงเฮงอีซูซุ จำกัด (ศูนย์ซ่อมสีและตัวถังแก่งคอย)" -> "อิซูซุจึงกงเฮงแก่งคอย".

    .venv/Scripts/python.exe scripts/fold_vendor_names.py [--apply] [--only "<text>"]

--only: change just the bills whose shop contains this text (e.g. จึงกงเฮง).

Only names the shops list recognises are changed; in-house names, people and unknown shops are left. Dry run unless --apply.
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import shops, supabase                                  # noqa: E402


def main(apply: bool, only: str = "") -> None:
    rows = supabase.select_all("maintenance_events", {"select": "id,vendor", "vendor": "not.is.null", "order": "id.asc"})
    todo = [(r, shops.fold_vendor(r["vendor"])) for r in rows if shops.fold_vendor(r["vendor"]) != r["vendor"] and only in r["vendor"]]
    for (old, new), n in collections.Counter((r["vendor"], new) for r, new in todo).items():
        print(f"  {n:3} x {old}  ->  {new}")
    print(f"{len(todo)} of {len(rows)} bills to change")
    if apply:
        for r, new in todo:
            supabase.update("maintenance_events", {"id": f"eq.{r['id']}"}, {"vendor": new})
        print("written")
    else:
        print("dry run -- nothing written (add --apply)")


if __name__ == "__main__":
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
    main("--apply" in sys.argv, only)
