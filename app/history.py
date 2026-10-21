"""
Read-only history for the "ค้นหารถ" pages: the list of trucks with their
repair totals, one truck's repair events, and one event in full (the
"mock bill" view). Only CONFIRMED events are ever shown.

Everything that decides what the page shows (the one-line preview of an
event, the totals per truck) is a pure function so it is unit-tested; the
database calls are thin.
"""
import uuid
from typing import Optional

from app import supabase

PREVIEW_LINES = 4          # how many line descriptions the one-line preview names
CHUNK = 50                 # ids per `in.(...)` filter, to keep request URLs short
NON_DESCRIPTIVE = {"ค่าแรง"}   # labour says little about WHAT was done -- named last


class NotFound(Exception):
    pass


def _uuid_or_404(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFound()


def _chunks(seq: list, n: int = CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ----------------------------------------------------------------- pure
def preview(lines: list[dict]) -> str:
    """
    A short "what was done" for the history list: the biggest parts/jobs by
    cost, named first, labour last, e.g. "น้ำมันเครื่อง, กรองเครื่อง, จาระบี +3".
    A stand-in until events carry a written summary of their own.
    """
    named = [l for l in lines if (l.get("description") or "").strip()]
    if not named:
        return ""
    parts = sorted((l for l in named if l.get("category") not in NON_DESCRIPTIVE), key=lambda l: -(float(l.get("line_total") or 0)))
    labour = [l for l in named if l.get("category") in NON_DESCRIPTIVE]
    ordered = [l["description"].strip() for l in parts + labour]
    shown = ordered[:PREVIEW_LINES]
    rest = len(ordered) - len(shown)
    return ", ".join(shown) + (f" +{rest}" if rest else "")


def vehicle_rows(vehicles: list[dict], status: dict[str, dict], events: list[dict]) -> list[dict]:
    """One row per truck: identity + live status + lifetime repair count / spend."""
    spend: dict[str, list] = {}
    for e in events:
        s = spend.setdefault(e["vehicle_id"], [0, 0.0])
        s[0] += 1
        s[1] += float(e.get("total_cost") or 0)
    out = []
    for v in vehicles:
        st = status.get(v["id"], {})
        n, total = spend.get(v["id"], [0, 0.0])
        out.append({
            "vehicle_id": v["id"], "plate": v["plate_number"], "province": v.get("plate_province") or "",
            "truck_number": v.get("vehicle_number") or "", "branch": v.get("branch") or "",
            "make": v.get("make") or "", "model": v.get("model") or "",
            "open_requests": int(st.get("open_requests") or 0), "awaiting_bill_requests": int(st.get("awaiting_bill_requests") or 0),
            "last_service_date": st.get("last_service_date"), "days_since_service": st.get("days_since_service"),
            "last_known_mileage": st.get("last_known_mileage"), "next_service_mileage": st.get("next_service_mileage"),
            "repairs_total": n, "spend_total": round(total, 2),
        })
    return out


def event_rows(events: list[dict], lines: list[dict], attachments: list[dict]) -> list[dict]:
    """Events (newest first, as given) with the preview and counts the list needs."""
    by_event: dict[str, list] = {}
    for l in lines:
        by_event.setdefault(l["event_id"], []).append(l)
    files: dict[str, int] = {}
    for a in attachments:
        files[a["event_id"]] = files.get(a["event_id"], 0) + 1
    out = []
    for e in events:
        ls = by_event.get(e["id"], [])
        out.append({
            "event_id": e["id"], "event_date": e["event_date"], "vendor": e.get("vendor"), "performed_by": e.get("performed_by"),
            "source": e.get("source") or "bill", "total_cost": e.get("total_cost"), "mileage": e.get("mileage"),
            "preview": preview(ls), "line_count": len(ls), "file_count": files.get(e["id"], 0),
            "needs_review": bool(e.get("low_confidence_fields")),
        })
    return out


# --------------------------------------------------------------- database
def list_vehicles() -> list[dict]:
    vehicles = supabase.select_all("vehicles", {
        "select": "id,plate_number,plate_province,vehicle_number,make,model,branch", "active": "eq.true",
        "order": "branch.asc,vehicle_number.asc,id.asc"})
    status = {r["vehicle_id"]: r for r in supabase.select_all("v_vehicle_status", {
        "select": "vehicle_id,open_requests,awaiting_bill_requests,last_service_date,days_since_service,last_known_mileage,next_service_mileage",
        "order": "vehicle_id.asc"})}
    events = supabase.select_all("v_events", {"select": "vehicle_id,total_cost", "order": "event_id.asc"})
    return vehicle_rows(vehicles, status, events)


def vehicle_events(vehicle_id: str) -> list[dict]:
    vid = _uuid_or_404(vehicle_id)
    events = supabase.select_all("maintenance_events", {
        "select": "id,event_date,vendor,performed_by,source,total_cost,mileage,low_confidence_fields,created_at",
        "vehicle_id": f"eq.{vid}", "status": "eq.confirmed", "order": "event_date.desc,created_at.desc,id.asc"})
    ids = [e["id"] for e in events]
    lines, attachments = [], []
    for chunk in _chunks(ids):
        lines += supabase.select_all("bill_line_items", {
            "select": "event_id,line_number,description,category,line_total", "event_id": f"in.({','.join(chunk)})",
            "order": "event_id.asc,line_number.asc"})
        attachments += supabase.select_all("event_attachments", {
            "select": "id,event_id", "event_id": f"in.({','.join(chunk)})", "order": "id.asc"})
    return event_rows(events, lines, attachments)


def event_detail(event_id: str) -> dict:
    eid = _uuid_or_404(event_id)
    rows = supabase.select("maintenance_events", {
        "select": "id,event_date,vendor,performed_by,source,branch,mileage,next_service_mileage,total_cost,notes,low_confidence_fields,"
                  "extraction_model,created_at,confirmed_at,vehicle_id,vehicles(plate_number,plate_province,vehicle_number,branch,make,model)",
        "id": f"eq.{eid}", "status": "eq.confirmed", "limit": "1"})
    if not rows:
        raise NotFound()
    e = rows[0]
    v = e.pop("vehicles", None) or {}
    lines = supabase.select_all("bill_line_items", {
        "select": "line_number,description,category,quantity,unit,unit_cost,line_total,was_low_confidence,attachment_id",
        "event_id": f"eq.{eid}", "order": "line_number.asc"})
    atts = supabase.select_all("event_attachments", {
        "select": "id,type,mime_type,storage_path,sort_order", "event_id": f"eq.{eid}", "order": "sort_order.asc,id.asc"})
    requests = supabase.select("repair_requests", {
        "select": "seq,problem,reported_by,fixed_by,request_date", "fixed_event_id": f"eq.{eid}", "order": "seq.asc"})
    return {
        "event": {**e, "needs_review": bool(e.get("low_confidence_fields"))},
        "vehicle": {"vehicle_id": e["vehicle_id"], "plate": v.get("plate_number"), "province": v.get("plate_province") or "",
                    "truck_number": v.get("vehicle_number") or "", "branch": v.get("branch") or "",
                    "make": v.get("make") or "", "model": v.get("model") or ""},
        "lines": lines,
        "attachments": [{"id": a["id"], "type": a["type"], "mime_type": a["mime_type"], "url": supabase.public_url(a["storage_path"])} for a in atts],
        "requests": requests,
    }
