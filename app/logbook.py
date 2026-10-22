"""
The maintenance log ("สมุดงานซ่อม"): every repair job in ONE filterable list.

Two kinds of row, never both for the same repair:
  * an EVENT  -- a saved bill or a manual entry (the repair has a record), dated
                 by the date on the bill / entry;
  * a REQUEST -- a repair that has no record yet or never will (still waiting,
                 at a shop, waiting for its bill, closed without a bill,
                 cancelled), dated by the date that matters for its stage.
A request whose repair already has a record is the SAME repair as that event,
so it is not listed twice; it is shown on the event ("from request #12 ...").

Everything here is a pure function over rows (no I/O) so the filters and the
CSV export are unit-tested; app/main.py loads the rows and calls them.
"""
import csv
import io
import re
from datetime import date
from typing import Optional

from app import history, shops

# Stage of a request -> label. (Events are always "done".)
STATUS_LABELS = {
    "done": "เสร็จแล้ว",
    "done_no_bill": "เสร็จ (ไม่มีบิล)",
    "awaiting_bill": "รอบิล",
    "at_shop": "อยู่ที่อู่",
    "open": "รอซ่อม",
    "cancelled": "ยกเลิก",
}
# Groups offered in the status filter.
STATUS_GROUPS = {
    "all": None,
    "active": {"open", "at_shop", "awaiting_bill"},        # everything still in progress
    "done": {"done", "done_no_bill"},
    "open": {"open"}, "at_shop": {"at_shop"}, "awaiting_bill": {"awaiting_bill"},
    "cancelled": {"cancelled"},
}
SOURCE_LABELS = {"bill": "บิล", "manual": "ช่างทำเอง", "request": "งานซ่อม (ยังไม่มีบันทึก)"}
MAX_LIMIT = 500


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_.]", "", (s or "").lower())


def _date(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _request_status(r: dict) -> str:
    s = r.get("status")
    if s == "completed":
        return "done_no_bill"       # (with a record it is excluded: its event is the row)
    return s if s in ("open", "at_shop", "awaiting_bill", "cancelled") else "open"


def _request_date(r: dict, status: str) -> Optional[str]:
    """The date that matters for where the repair is now."""
    if status == "at_shop":
        return r.get("sent_date") or r.get("request_date")
    if status in ("awaiting_bill", "done_no_bill"):
        return r.get("fixed_date") or r.get("request_date")
    return r.get("request_date")


def build_rows(*, events: list[dict], lines: list[dict], attachments: list[dict], requests: list[dict], vehicles: dict[str, dict]) -> list[dict]:
    """
    events: confirmed maintenance_events rows; lines: bill_line_items rows
    (event_id, description, category, line_total); attachments: event_attachments
    rows (event_id); requests: repair_requests rows; vehicles: id -> vehicles row.
    """
    ev_by_id = {e["id"]: e for e in events}
    ev_rows = {r["event_id"]: r for r in history.event_rows(events, lines, attachments)}
    lines_by_event: dict[str, list[dict]] = {}
    for l in lines:
        lines_by_event.setdefault(l["event_id"], []).append(l)
    closed_by: dict[str, list[dict]] = {}
    for r in requests:
        if r.get("fixed_event_id"):
            closed_by.setdefault(r["fixed_event_id"], []).append(r)

    def vehicle_fields(vid, fallback_plate="", fallback_no=""):
        v = vehicles.get(vid) or {}
        return {"vehicle_id": vid, "plate": v.get("plate_number") or fallback_plate or "", "truck_number": v.get("vehicle_number") or fallback_no or "",
                "branch": v.get("branch") or "", "model": f"{v.get('make') or ''} {v.get('model') or ''}".strip()}

    out: list[dict] = []
    for eid, er in ev_rows.items():
        e = ev_by_id[eid]
        ls = lines_by_event.get(eid, [])
        who_raw = er.get("vendor") or er.get("performed_by") or ""
        from_requests = [{"no": q.get("seq"), "problem": q.get("problem") or ""} for q in closed_by.get(eid, [])]
        row = {
            "key": f"event:{eid}", "kind": "event", "id": eid, "date": er["event_date"], "status": "done", "source": er["source"],
            **vehicle_fields(e.get("vehicle_id")),
            "shop": shops.canonical(who_raw), "shop_raw": who_raw,
            "summary": er.get("summary") or "", "preview": er.get("preview") or "",
            "categories": sorted({l["category"] for l in ls if l.get("category")}),
            "line_count": er["line_count"], "file_count": er["file_count"], "total_cost": er.get("total_cost"), "mileage": er.get("mileage"),
            "needs_review": er["needs_review"], "review_reasons": er["review_reasons"], "from_requests": from_requests,
        }
        row["_hay"] = _norm(" ".join([
            row["plate"], row["truck_number"], row["branch"], row["model"], row["shop"], who_raw, row["summary"], row["preview"],
            *(l.get("description") or "" for l in ls), *(q["problem"] for q in from_requests), e.get("notes") or "",
        ]))
        out.append(row)

    for r in requests:
        if r.get("fixed_event_id"):
            continue                                   # the same repair as its event row
        status = _request_status(r)
        shop_raw = r.get("shop") or (r.get("fixed_by") if status in ("awaiting_bill", "done_no_bill") else "") or ""
        row = {
            "key": f"request:{r['id']}", "kind": "request", "id": r["id"], "request_no": r.get("seq"),
            "date": _request_date(r, status) or r.get("request_date"), "status": status, "source": "request",
            **vehicle_fields(r.get("vehicle_id"), r.get("plate_text") or "", r.get("truck_number_text") or ""),
            "shop": shops.canonical(shop_raw), "shop_raw": shop_raw,
            "summary": "", "preview": r.get("problem") or "", "problem": r.get("problem") or "",
            "reported_by": r.get("reported_by") or "", "urgent": bool(r.get("urgent")), "on_paper": r.get("on_paper", True),
            "request_date": r.get("request_date"), "sent_date": r.get("sent_date"), "expected_return": r.get("expected_return"),
            "fixed_date": r.get("fixed_date"), "no_bill_reason": r.get("no_bill_reason"),
            "categories": [], "line_count": 0, "file_count": 0, "total_cost": None, "mileage": None, "needs_review": False, "review_reasons": [], "from_requests": [],
        }
        row["_hay"] = _norm(" ".join([row["plate"], row["truck_number"], row["branch"], row["model"], row["shop"], shop_raw, row["problem"],
                                      row["reported_by"], r.get("notes") or ""]))
        out.append(row)
    return out


# ------------------------------------------------------------------ filtering
def _parse_filters(f: dict) -> dict:
    def num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    status = f.get("status") or "all"
    return {
        "tokens": [t for t in (_norm(x) for x in str(f.get("q") or "").split()) if t],
        "date_from": _date(f.get("date_from")), "date_to": _date(f.get("date_to")),
        "branch": (f.get("branch") or "").strip(), "vehicle_id": (f.get("vehicle_id") or "").strip(),
        "shop": (f.get("shop") or "").strip(), "category": (f.get("category") or "").strip(),
        "statuses": STATUS_GROUPS.get(status, STATUS_GROUPS["all"]),
        "source": (f.get("source") or "").strip(),
        "min_cost": num(f.get("min_cost")), "max_cost": num(f.get("max_cost")),
        "review": str(f.get("review") or "").lower() in ("1", "true", "yes", "on"),
    }


def _matches(row: dict, p: dict) -> bool:
    if p["statuses"] is not None and row["status"] not in p["statuses"]:
        return False
    if p["source"] and row["source"] != p["source"]:
        return False
    if p["branch"] and row["branch"] != p["branch"]:
        return False
    if p["vehicle_id"] and row["vehicle_id"] != p["vehicle_id"]:
        return False
    if p["shop"] and row["shop"] != p["shop"]:
        return False
    if p["category"] and p["category"] not in row["categories"]:
        return False
    if p["review"] and not row["needs_review"]:
        return False
    d = _date(row["date"])
    if p["date_from"] and (d is None or d < p["date_from"]):
        return False
    if p["date_to"] and (d is None or d > p["date_to"]):
        return False
    cost = row.get("total_cost")
    if p["min_cost"] is not None and (cost is None or float(cost) < p["min_cost"]):
        return False
    if p["max_cost"] is not None and (cost is None or float(cost) > p["max_cost"]):
        return False
    return all(t in row["_hay"] for t in p["tokens"])


SORTS = {
    "date_desc": lambda r: (r["date"] or "", r["key"]),
    "date_asc": lambda r: (r["date"] or "", r["key"]),
    "cost_desc": lambda r: (float(r.get("total_cost") or 0), r["date"] or "", r["key"]),
}


def matches(rows: list[dict], filters: dict, *, sort: str = "date_desc") -> list[dict]:
    """Every row that passes the filters, in order (full internal rows -- the CSV export uses this)."""
    p = _parse_filters(filters)
    hits = [r for r in rows if _matches(r, p)]
    hits.sort(key=SORTS.get(sort, SORTS["date_desc"]), reverse=(sort != "date_asc"))
    return hits


def query(rows: list[dict], filters: dict, *, sort: str = "date_desc", limit: int = 50, offset: int = 0) -> dict:
    """Applies the filters and returns one page plus totals over ALL matches (not just the page)."""
    hits = matches(rows, filters, sort=sort)
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    return {
        "total": len(hits),
        "stats": {
            "total_cost": round(sum(float(r.get("total_cost") or 0) for r in hits), 2),
            "trucks": len({r["vehicle_id"] for r in hits if r["vehicle_id"]}),
            "by_status": {s: sum(1 for r in hits if r["status"] == s) for s in STATUS_LABELS if any(r["status"] == s for r in hits)},
        },
        "offset": offset, "limit": limit,
        "rows": [_public(r) for r in hits[offset:offset + limit]],
    }


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")} | {"status_label": STATUS_LABELS[row["status"]]}


def facets(rows: list[dict]) -> dict:
    """Choices for the filter drop-downs, with how many rows each would match (over everything)."""
    def count(values):
        c: dict[str, int] = {}
        for v in values:
            if v:
                c[v] = c.get(v, 0) + 1
        return [{"value": k, "count": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]

    shop_counts = count(r["shop"] for r in rows)
    known = {s["value"] for s in shop_counts}
    for name in shops.choices():                      # the shops you use are always offered, even before any work is logged
        if name not in known:
            shop_counts.append({"value": name, "count": 0})
    return {
        "shops": shop_counts,
        "categories": count(c for r in rows for c in r["categories"]),
        "branches": count(r["branch"] for r in rows),
        "statuses": [{"value": s, "label": STATUS_LABELS[s], "count": sum(1 for r in rows if r["status"] == s)} for s in STATUS_LABELS],
    }


# --------------------------------------------------------------------- CSV
CSV_HEADER = ["วันที่ (ค.ศ.)", "ปี พ.ศ.", "เบอร์รถ", "ทะเบียน", "สาขา", "ร้าน / ช่าง", "ประเภท", "สถานะ", "สรุปงาน", "หมวด", "จำนวนรายการ", "ยอดรวม (บาท)", "เลขไมล์"]


def to_csv(rows: list[dict]) -> str:
    """Excel-friendly CSV of already-filtered rows (the caller adds the UTF-8 BOM for Thai text)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    for r in rows:
        d = _date(r["date"])
        w.writerow([
            r["date"] or "", (d.year + 543) if d else "", r["truck_number"], r["plate"], r["branch"], r["shop_raw"] or r["shop"],
            SOURCE_LABELS.get(r["source"], r["source"]), STATUS_LABELS[r["status"]], r["summary"] or r["preview"],
            " / ".join(r["categories"]), r["line_count"] or "", r["total_cost"] if r["total_cost"] is not None else "", r["mileage"] or "",
        ])
    return buf.getvalue()
