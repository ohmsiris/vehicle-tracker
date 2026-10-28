"""
Load a truck's repair history from the dealership's "ประวัติการซ่อม" PDF report (app/dealer_history.py) as maintenance events (no prices).

    .venv/Scripts/python.exe scripts/import_dealer_history.py "<report1.pdf>" ["<report2.pdf>" ...] [--apply]

Needs PyMuPDF in the venv (a local tool, not a server dependency):  .venv/Scripts/python.exe -m pip install pymupdf
Each report is one truck (matched by its plate). A visit already on the site (same truck, same day AND same odometer) is skipped, so a report can be loaded again; a
visit whose day is on the site with a DIFFERENT odometer is reported, not added. New visits are saved like a record made by hand (source "manual": it can be deleted one by one),
with the dealer's wording as lines (the explanation in brackets), ค่าแรง for labour and the AI categoriser for parts. Dry run unless --apply.
"""
import re
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import categorize, dealer_history as dh, events, history, supabase    # noqa: E402
from app.config import settings                                                   # noqa: E402

SOURCE_NOTE = f"{history.IMPORT_MARK} (รายงานประวัติการซ่อม)"


def read_pdf(path: str) -> str:
    warnings.filterwarnings("ignore")
    import fitz
    doc = fitz.open(path)
    return "\n".join(f"##### page {i + 1}\n{p.get_text()}" for i, p in enumerate(doc))


def plate_key(text) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def main(paths: list[str], apply: bool) -> None:
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch,active", "order": "id.asc"})
    by_plate = {plate_key(v["plate_number"]): v for v in vehicles}
    plan = []
    for path in paths:
        rep = dh.parse_report_text(read_pdf(path))
        v = by_plate.get(plate_key(rep["plate"]))
        name = Path(path).name
        if not v:
            print(f"{name}: plate {rep['plate']} {rep['province']} is not on the site -- skipped")
            continue
        have = supabase.select_all("maintenance_events", {"select": "id,event_date,mileage", "vehicle_id": f"eq.{v['id']}", "order": "event_date.asc,id.asc"})
        by_day = {}
        for e in have:
            by_day.setdefault(str(e["event_date"])[:10], []).append(e)
        add, same, conflicts = [], [], []
        for visit in rep["visits"]:
            day = visit["date"].isoformat()
            hit = by_day.get(day, [])
            if any(e.get("mileage") == visit["mileage"] for e in hit):
                same.append(day)
            elif hit:
                conflicts.append((day, visit["mileage"], [e.get("mileage") for e in hit]))
            else:
                add.append(visit)
        print(f"{name}: {rep['plate']} -> #{v['vehicle_number']} ({v['branch']}) · {len(rep['visits'])} visits in the report · already there {len(same)} · to add {len(add)} "
              f"({sum(len(x['lines']) for x in add)} lines) · conflicts {len(conflicts)}")
        for day, km, other in conflicts:
            print(f"    CONFLICT {day}: report says {km:,} km, the site has {other} -- not added")
        for x in add:
            print(f"    + {x['date']}  {x['mileage']:>8,} km  {len(x['lines']):>3} lines")
        plan.append((v, add))
    total = sum(len(a) for _, a in plan)
    print(f"\n{total} visits to add")
    if not apply or not total:
        print("dry run -- nothing written (add --apply)" if not apply else "nothing to add")
        return
    use_ai = bool(settings.anthropic_api_key)
    cache: dict = {}
    for v, add in plan:
        for visit in add:
            parts = [ln for ln in visit["lines"] if ln["kind"] != "ค่าแรง"]
            if use_ai:
                todo = [{"description": ln["description"], "unit": ln["unit"]} for ln in parts if ln["description"] not in cache]
                for i in range(0, len(todo), categorize.MAX_ITEMS):
                    chunk = todo[i:i + categorize.MAX_ITEMS]
                    try:
                        for it, cat in zip(chunk, categorize.categorize(chunk)):
                            cache[it["description"]] = cat
                    except RuntimeError as exc:
                        print("  categoriser failed, those lines stay without a category:", exc)
            lines = [{"description": dh.line_text(ln), "category": "ค่าแรง" if ln["kind"] == "ค่าแรง" else cache.get(ln["description"]),
                      "quantity": ln["quantity"], "unit": ln["unit"]} for ln in visit["lines"]]
            payload = {"vehicle_id": v["id"], "source": "manual", "attachments": [], "line_items": lines,
                       "event": {"event_date": visit["date"].isoformat(), "mileage": visit["mileage"], "branch": v["branch"], "vendor": None,
                                 "notes": f"{SOURCE_NOTE} · ออกใบแจ้งหนี้ {visit['invoice_date'].strftime('%d/%m/')}{visit['invoice_date'].year + 543}"}}
            res = events.save_event(payload, [])
            print(f"  saved #{v['vehicle_number']} {visit['date']} {visit['mileage']:,} km -> {res['event_id']}")
    print("written")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    main(args, "--apply" in sys.argv)
