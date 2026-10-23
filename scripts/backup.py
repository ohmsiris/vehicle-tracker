"""
Backs up ALL the vehicle tracker's data and photos to the backups/ folder.

    .venv/Scripts/python.exe scripts/backup.py

Read-only against Supabase. Safe to run as often as you like; photos already
backed up are not downloaded again. See app/backup.py for what is written and
how each run is checked. backups/ is gitignored: it is real business data.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app import supabase
from app.backup import run_backup


def main() -> int:
    if not supabase.configured():
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set (.env).")
        return 2
    m = run_backup(supabase.select_all, supabase.storage_download, ROOT / "backups")
    print("Rows   :", ", ".join(f"{t} {n}" for t, n in m["rows"].items()))
    if m.get("tables_not_in_this_database"):
        print("Not in this database yet (nothing to save):", ", ".join(m["tables_not_in_this_database"]))
    f = m["files"]
    print(f"Files  : {f['expected']} expected, {f['downloaded_now']} downloaded now, {f['already_had']} already had ({f['bytes'] / 1_000_000:.1f} MB total)")
    if m["ok"]:
        print("RESULT : OK -- everything was read back and checked.")
        return 0
    print("RESULT : PROBLEMS --")
    for problem in m["problems"]:
        print("  ", problem)
    return 1


if __name__ == "__main__":
    sys.exit(main())
