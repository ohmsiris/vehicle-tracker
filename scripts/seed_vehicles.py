"""
One-off (idempotent): loads the fleet roster CSV into Supabase's `vehicles`
table. Re-running is safe -- a vehicle already there (same plate + province)
is left untouched, so edits made in Supabase afterwards aren't overwritten.

    .venv/Scripts/python.exe scripts/seed_vehicles.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from app import supabase
from app.roster import load_roster


def main() -> None:
    rows, seen, skipped = [], set(), []
    for r in load_roster():
        plate = (r.get("plate") or "").strip()
        province = (r.get("province") or "").strip()
        if not plate:
            skipped.append(r)
            continue
        key = (plate, province)
        if key in seen:
            skipped.append(r)
            continue
        seen.add(key)
        rows.append({
            "plate_number": plate,
            "plate_province": province,
            "vehicle_number": (r.get("truck_number") or "").strip() or None,
            "make": (r.get("make") or "").strip() or None,
            "model": (r.get("model") or "").strip() or None,
            "branch": (r.get("branch") or "").strip() or None,
            "active": True,
        })
    inserted = supabase.insert("vehicles", rows, on_conflict="plate_number,plate_province", ignore_duplicates=True)
    print(f"roster rows: {len(rows) + len(skipped)} | skipped (no plate / duplicate): {len(skipped)} | newly inserted: {len(inserted)}")
    for r in skipped:
        print("  skipped:", r.get("branch"), r.get("truck_number"), repr(r.get("plate")))


if __name__ == "__main__":
    main()
