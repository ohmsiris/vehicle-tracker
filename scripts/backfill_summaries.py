"""
Writes an AI summary into every confirmed event that has none yet. Idempotent:
an event that already has a summary (typed by a person, or written earlier) is
never touched, and running it twice does nothing the second time.

    .venv/Scripts/python.exe scripts/backfill_summaries.py            # show what it would write
    .venv/Scripts/python.exe scripts/backfill_summaries.py --write    # write it

Needs schema_v4.sql to have been run (it adds the summary column).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from app import history, summarize, supabase


def main(write: bool) -> None:
    if not history._has_summary():
        sys.exit("The summary column doesn't exist yet -- run schema_v4.sql in the Supabase SQL editor first.")
    events = supabase.select_all("maintenance_events", {
        "select": "id,event_date,vendor,performed_by,summary", "status": "eq.confirmed", "order": "event_date.asc,id.asc"})
    todo = [e for e in events if not (e.get("summary") or "").strip()]
    print(f"{len(events)} confirmed events, {len(todo)} without a summary" + ("" if write else "  (dry run: nothing will be written)"))
    for e in todo:
        label = f"{e['event_date']} {e.get('vendor') or e.get('performed_by') or ''}".strip()
        if write:
            text = summarize.fill_summary(e["id"])
        else:
            lines = supabase.select_all("bill_line_items", {
                "select": "line_number,description,category,quantity,unit", "event_id": f"eq.{e['id']}", "order": "line_number.asc"})
            text = summarize.summarize(lines, {"performed_by": e.get("performed_by")})
        print(f"  {label}: {text or '(nothing to say -- left empty)'}")


if __name__ == "__main__":
    main("--write" in sys.argv)
