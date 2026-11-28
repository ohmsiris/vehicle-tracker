"""
Load a truck's repair history from the dealership's "ประวัติการซ่อม" PDF report (app/dealer_history.py) as maintenance events (no prices).

    .venv/Scripts/python.exe scripts/import_dealer_history.py "<report1.pdf>" ["<report2.pdf>" ...] [--apply] [--vendor "<shop>"]

Needs PyMuPDF in the venv (a local tool, not a server dependency):  .venv/Scripts/python.exe -m pip install pymupdf
Each report is one truck (matched by its plate). A visit already on the site (same truck, same day AND same odometer) is skipped, so a report can be loaded again; a
visit whose day is on the site with a DIFFERENT odometer is reported, not added. The invoice day counts as the visit day too, and a visit whose odometer is already on the truck is
skipped whatever its date (a bill photographed by hand is dated on the invoice). New visits are saved like a record made by hand (source "manual": it can be deleted one by one),
with the shop อิซูซุจึงกงเฮงแก่งคอย (--vendor), the dealer's wording as lines (the explanation in brackets), ค่าแรง for labour and the AI categoriser for parts. Dry run unless --apply.
"""
import re
import sys
import warnings
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import categorize, dealer_history as dh, events, history, supabase    # noqa: E402
from app.config import settings                                                   # noqa: E402

SOURCE_NOTE = f"{history.IMPORT_MARK} (รายงานประวัติการซ่อม)"
DEFAULT_VENDOR = "อิซูซุจึงกงเฮงแก่งคอย"            # the shop shown on every visit loaded (the report itself does not say which branch); change with --vendor "<name>" or "" for none


def read_pdf(path: str) -> str:
    warnings.filterwarnings("ignore")
    import fitz
    doc = fitz.open(path)
    return "\n".join(f"##### page {i + 1}\n{p.get_text()}" for i, p in enumerate(doc))


def plate_key(text) -> str:
    return re.sub(r"\s+", "", str(text or ""))


VENDOR = DEFAULT_VENDOR


def _words(text) -> str:
    """A line's wording without the shop's tags: "ชุดปีกนกบน(อีซูซุ)(ใหม่) - BP" and "ชุดปีกนกบน" are the same line."""
    text = re.sub(r"\((?:อีซูซุ|ใหม่|ตรีเพชร|ไม่มียี่ห้อ)\)|\s-\s*BP|\s+", "", dh.normalise(str(text or "")))
    return text.lower()


def same_job(visit: dict, bills: list[dict]) -> Optional[dict]:
    """The bill already on the site that IS this visit (1ฒย4267, 28 Nov 2026: the report's 14/08 and 19/10 visits were the bills dated
    12/08 and 26/09): within 60 days, and at least 60% of the visit's lines (at least 3) are on the bill."""
    mine = {_words(ln["description"]) for ln in visit["lines"] if len(_words(ln["description"])) >= 4}
    if len(mine) < 3:
        return None
    for b in bills:
        if abs((date.fromisoformat(str(b["event_date"])[:10]) - visit["date"]).days) > 60:
            continue
        theirs = " ".join(b["words"])
        if sum(1 for w in mine if w in theirs) >= 0.6 * len(mine):
            return b
    return None


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
        kms = {e.get("mileage") for e in have if e.get("mileage")}
        bills = [b for b in supabase.select_all("maintenance_events", {"select": "id,event_date,mileage", "vehicle_id": f"eq.{v['id']}", "source": "eq.bill",
                                                                     "order": "event_date.asc,id.asc"})]
        for b in bills:
            b["words"] = [_words(x["description"]) for x in supabase.select_all("bill_line_items", {"select": "description", "event_id": f"eq.{b['id']}", "order": "id.asc"})]
        known = [(date.fromisoformat(str(e["event_date"])[:10]), e["mileage"]) for e in have if e.get("mileage")] + [(x["date"], x["mileage"]) for x in rep["visits"] if x["mileage"]]
        for visit in rep["visits"]:
            day = visit["date"].isoformat()
            twin = same_job(visit, bills)
            if twin:
                print(f"    = {day}: the same job as the bill of {twin['event_date']} already on the site -- not added")
                same.append(day)
                continue
            # the dealer stamps the reading of the INVOICE day on visits invoiced together: a reading above what the truck showed later is not his
            later = [km for d, km in known if d > visit["date"] and km < (visit["mileage"] or 0)]
            if later:
                print(f"    ~ {day}: report says {visit['mileage']:,} km but the site has {max(later):,} km after that day -- added without the km")
                visit["mileage"] = None
            # a bill photographed by hand is usually dated on the INVOICE day, not the day the truck went in: check both, and the odometer alone
            hit = by_day.get(day, []) + (by_day.get(visit["invoice_date"].isoformat(), []) if visit.get("invoice_date") else [])
            if (visit["mileage"] and visit["mileage"] in kms) or any(e.get("mileage") == visit["mileage"] for e in hit):
                same.append(day)
            elif hit:
                conflicts.append((day, visit["mileage"], [e.get("mileage") for e in hit]))
            else:
                add.append(visit)
        print(f"{name}: {rep['plate']} -> #{v['vehicle_number']} ({v['branch']}) · {len(rep['visits'])} visits in the report · already there {len(same)} · to add {len(add)} "
              f"({sum(len(x['lines']) for x in add)} lines) · conflicts {len(conflicts)}")
        for day, km, other in conflicts:
            print(f"    CONFLICT {day}: report says {km or 0:,} km, the site has {other} -- not added")
        for x in add:
            print(f"    + {x['date']}  {x['mileage'] or 0:>8,} km  {len(x['lines']):>3} lines")
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
                       "event": {"event_date": visit["date"].isoformat(), "mileage": visit["mileage"], "branch": v["branch"], "vendor": VENDOR or None,
                                 "notes": f"{SOURCE_NOTE} · ออกใบแจ้งหนี้ {visit['invoice_date'].strftime('%d/%m/')}{visit['invoice_date'].year + 543}"}}
            res = events.save_event(payload, [])
            print(f"  saved #{v['vehicle_number']} {visit['date']} {visit['mileage'] or 0:,} km -> {res['event_id']}")
    print("written")


if __name__ == "__main__":
    if "--vendor" in sys.argv:
        k = sys.argv.index("--vendor")
        VENDOR = sys.argv[k + 1]
        del sys.argv[k:k + 2]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    main(args, "--apply" in sys.argv)
