"""
The fleet list, used two ways: shown to the model as plate-reading hints
(a strong hint, not ground truth -- the prompt says so), and for a
deterministic plate -> vehicle lookup after extraction.

Reads data/vehicle_roster.csv for now (copied from the NongTick repo's
roster). Once the vehicles table in Supabase is the source of truth, only
load_roster() changes -- everything else takes plain dict rows.

Truck numbers are NOT unique across branches (e.g. "truck #6" exists in
both Saraburi and Kaeng Khoi), so lookups are keyed on PLATE only.
"""
import csv
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger("vehicle_tracker.roster")


_cache: dict = {"at": 0.0, "rows": None}
CACHE_SECONDS = 60


def _load_from_supabase() -> list[dict]:
    from app import fleet, supabase
    v6 = fleet.has_v6()                    # usage + main driver exist only after schema_v6.sql
    rows = supabase.select("vehicles", {
        "select": "id,plate_number,plate_province,vehicle_number,make,model,branch" + (",usage,main_driver_id" if v6 else ""),
        "active": "eq.true", "order": "branch.asc,vehicle_number.asc", "limit": "1000",
    })
    drivers = {d["id"]: d["name"] for d in supabase.select("drivers", {"select": "id,name", "limit": "1000"})} if v6 else {}
    # Same keys the CSV used (and the page/prompt expect), plus the row id.
    return [{
        "id": r["id"], "branch": r["branch"] or "", "truck_number": r["vehicle_number"] or "",
        "plate": r["plate_number"], "province": r["plate_province"] or "",
        "make": r["make"] or "", "model": r["model"] or "",
        "usage": r.get("usage") or "", "driver_id": r.get("main_driver_id") or "", "driver": drivers.get(r.get("main_driver_id"), ""),
    } for r in rows]


def _load_from_csv() -> list[dict]:
    try:
        with open(settings.roster_csv_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        logger.warning("roster file not found at %s -- continuing without fleet hints", settings.roster_csv_path)
        return []


def load_roster() -> list[dict]:
    """The fleet list. Supabase's `vehicles` table is the source of truth
    (cached for a minute -- it changes rarely and every extraction wants it);
    the CSV copy is only a fallback when Supabase isn't configured or can't
    be reached, so a database hiccup doesn't stop bills being read."""
    import time
    from app import supabase
    if not supabase.configured():
        return _load_from_csv()
    if _cache["rows"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["rows"]
    try:
        rows = _load_from_supabase()
    except Exception:
        logger.exception("could not load vehicles from Supabase -- falling back to the CSV copy")
        return _cache["rows"] if _cache["rows"] is not None else _load_from_csv()
    _cache.update(at=time.time(), rows=rows)
    return rows


def invalidate_roster_cache() -> None:
    _cache.update(at=0.0, rows=None)


def roster_prompt_text(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = []
    for r in rows:
        bits = [f"branch={r.get('branch','')}", f"truck#={r.get('truck_number','')}", f"plate={r.get('plate','')}"]
        if r.get("province"):
            bits.append(f"province={r['province']}")
        if r.get("vehicle_type"):
            bits.append(r["vehicle_type"])
        make_model = f"{r.get('make','')} {r.get('model','')}".strip()
        if make_model:
            bits.append(make_model)
        lines.append("- " + ", ".join(bits))
    return (
        "Known fleet vehicles -- a small, mostly closed fleet of roughly "
        "50-70 trucks across both branches, so treat a plate NOT on this "
        "list as the unusual case worth double-checking, not the norm. "
        "That said, this list is manager-maintained and can still be "
        "slightly out of date (a new vehicle, a re-plated truck), so it's "
        "a strong hint, not absolute ground truth. IMPORTANT: truck "
        "numbers are NOT unique across branches -- e.g. 'truck #6' exists "
        "in BOTH Saraburi and Kaeng Khoi as two entirely different real "
        "vehicles, and most numbers 1-10 are duplicated this way. Never "
        "assume which branch a bare truck number belongs to from this "
        "list alone -- only use it to help read an ambiguous PLATE, since "
        "plates (unlike truck numbers) are genuinely unique per "
        "vehicle:\n" + "\n".join(lines)
    )


def normalize_plate(plate: str) -> str:
    return "".join((plate or "").split())


_VEHICLE_NUMBER_LABEL_PREFIXES = ("รถเบอร์", "เบอร์รถ", "เบอร์")


def clean_vehicle_number(raw: str) -> str:
    """The model sometimes writes "เบอร์ 34" into vehicle_number even though
    the schema asks for digits only; strip the label so it doesn't show
    doubled wherever the value is displayed already-labeled."""
    value = (raw or "").strip()
    for prefix in _VEHICLE_NUMBER_LABEL_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):].strip(" :.-")
    return value


def lookup_vehicle(plate: str, rows: list[dict]) -> Optional[dict]:
    """Plate -> roster row, tolerating minor formatting differences (a
    missing space, an added province suffix). None if nothing matches OR if
    the plate matches genuinely different vehicles -- safer to leave it
    unresolved for a human than to silently guess."""
    plate_norm = normalize_plate(plate)
    if not plate_norm:
        return None

    def fuzzy(row_plate_norm: str) -> bool:
        return bool(row_plate_norm) and (
            row_plate_norm == plate_norm or row_plate_norm in plate_norm or plate_norm in row_plate_norm
        )

    candidates = [r for r in rows if r.get("plate") and fuzzy(normalize_plate(r["plate"]))]
    if not candidates:
        return None
    exact = [r for r in candidates if normalize_plate(r["plate"]) == plate_norm]
    pool = exact or candidates
    distinct = {(r.get("branch", ""), r.get("truck_number", "")) for r in pool}
    if len(distinct) > 1:
        logger.warning("plate %r matches multiple roster entries ambiguously: %s", plate, distinct)
        return None
    return pool[0]
