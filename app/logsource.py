"""
Loads everything the log needs from Supabase and turns it into rows
(app/logbook.py). The result is cached for a few seconds -- changing a filter
should not mean five database round trips -- and the cache is cleared by every
endpoint that writes (a saved bill, an edit, a board action), so the log is
never stale after you record something.
"""
import threading
import time

from app import logbook, supabase

TTL_SECONDS = 15
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "rows": None}


def invalidate() -> None:
    with _lock:
        _cache.update(at=0.0, rows=None)


def load_rows() -> list[dict]:
    with _lock:
        if _cache["rows"] is not None and time.time() - _cache["at"] < TTL_SECONDS:
            return _cache["rows"]
    events = supabase.select_all("maintenance_events", {"select": "*", "status": "eq.confirmed", "order": "id.asc"})
    lines = supabase.select_all("bill_line_items", {"select": "event_id,description,category,line_total", "order": "id.asc"})
    atts = supabase.select_all("event_attachments", {"select": "event_id", "order": "id.asc"})
    requests = supabase.select_all("repair_requests", {"select": "*", "order": "seq.asc"})
    vehicles = {v["id"]: v for v in supabase.select_all("vehicles", {
        "select": "id,plate_number,vehicle_number,branch,make,model", "order": "id.asc"})}
    rows = logbook.build_rows(events=events, lines=lines, attachments=atts, requests=requests, vehicles=vehicles)
    with _lock:
        _cache.update(at=time.time(), rows=rows)
    return rows
