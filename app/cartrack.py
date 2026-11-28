"""
Cartrack GPS (owner, 29 Nov 2026: "game changing" -- real km for the maintenance plan, when each truck goes to the shop, which routes
can be merged). Step one: the connection and a look at what the account really sends.

The Cartrack Fleet API (developer.cartrack.com): HTTP Basic auth = the Fleetweb user name + an API key the admin of the account makes at
Fleetweb > Settings > API Settings; the base for Thailand is https://fleetapi-th.cartrack.com/rest; trucks are named by their
registration (the plate). GET /vehicles/status gives the latest position, odometer and more of every vehicle (60 calls a minute).

check() never returns the key, and nothing is stored yet: it only says whether the key works, how many of the vehicles Cartrack has
match a truck here (by plate, spaces and dashes ignored), and which fields a vehicle carries -- what the next steps are built on.

The owner's Cartrack user is an HQ one (owner, 29 Nov 2026): it sees every branch's vehicles, not only Saraburi's and Kaeng Khoi's.
So the plate decides: only a vehicle whose plate is a truck in IceBox is ever used; the others are counted and left alone (and a
new branch later needs only its trucks added here).
"""
from typing import Optional

import httpx

from app.config import settings

TIMEOUT = 30.0


class CartrackError(Exception):
    """str() is a Thai message safe to show."""


def configured() -> bool:
    return bool(settings.cartrack_user and settings.cartrack_api_key)


def _get(path: str, params: Optional[dict] = None) -> dict:
    if not configured():
        raise CartrackError("ยังไม่ได้ตั้งค่า Cartrack — ใส่ CARTRACK_USER และ CARTRACK_API_KEY ในการตั้งค่าของเซิร์ฟเวอร์ (Railway > Variables)")
    try:
        r = httpx.get(f"{settings.cartrack_base}{path}", params=params or {}, auth=(settings.cartrack_user, settings.cartrack_api_key),
                      timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise CartrackError(f"ต่อ Cartrack ไม่ได้ ({type(exc).__name__})") from exc
    if r.status_code == 401:
        raise CartrackError("Cartrack ไม่รับชื่อผู้ใช้ / API key นี้ (หรือบัญชีไม่ได้อยู่ในโซนประเทศไทย) — ตรวจ CARTRACK_USER, CARTRACK_API_KEY")
    if r.status_code == 403:
        raise CartrackError("API key นี้ไม่มีสิทธิ์ดูข้อมูลรถ — ให้สิทธิ์ดูรถกับผู้ใช้ย่อยใน Fleetweb")
    if r.status_code == 429:
        raise CartrackError("เรียก Cartrack ถี่เกินไป — รอสักครู่แล้วลองใหม่")
    if r.status_code >= 400:
        raise CartrackError(f"Cartrack ตอบ {r.status_code}")
    try:
        return r.json()
    except ValueError as exc:
        raise CartrackError("Cartrack ตอบกลับมาไม่ใช่ข้อมูลที่อ่านได้") from exc


def rows_of(body) -> list[dict]:
    """The list of vehicles in a response, wherever the API puts it ({"data": [...]}, or a bare list)."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("data", "vehicles", "results", "items"):
            if isinstance(body.get(key), list):
                return [x for x in body[key] if isinstance(x, dict)]
    return []


def plate_of(row: dict) -> str:
    for key in ("registration", "registration_number", "vehicle_registration", "plate"):
        if row.get(key):
            return str(row[key]).strip()
    return ""


def summarize(body, vehicles: list[dict]) -> dict:
    """What check() shows. Pure: body = the answer to /vehicles/status, vehicles = the trucks here."""
    from app.acc_docs import norm_plate
    rows = rows_of(body)
    mine = {norm_plate(v.get("plate_number") or ""): v for v in vehicles if v.get("plate_number")}
    matched, unknown, sample = [], [], {}
    for r in rows:
        plate = plate_of(r)
        v = mine.get(norm_plate(plate)) if plate else None
        (matched if v else unknown).append({"plate": plate, "truck": (v or {}).get("vehicle_number"), "branch": (v or {}).get("branch")})
        if v and not sample:
            sample = r
    seen = {norm_plate(m["plate"]) for m in matched}
    missing = [{"plate": v.get("plate_number"), "truck": v.get("vehicle_number"), "branch": v.get("branch")}
               for k, v in mine.items() if k not in seen and v.get("active") is not False]
    fields = sorted({k for r in rows for k in r})
    by_branch: dict = {}
    for m in matched:
        by_branch[m["branch"] or "-"] = by_branch.get(m["branch"] or "-", 0) + 1
    return {"ok": True, "vehicles": len(rows), "matched": len(matched), "matched_by_branch": by_branch, "trucks": matched[:200],
            "not_here_count": len(unknown), "not_here": unknown[:40], "no_gps_count": len(missing), "no_gps": missing[:80],
            "fields": fields, "sample": {k: sample[k] for k in list(sample)[:40]},
            "meta": body.get("meta") if isinstance(body, dict) else None}


def check() -> dict:
    """Does the key work, and what does the account send? Read-only, one call."""
    from app import supabase
    body = _get("/vehicles/status")
    vehicles = supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch,active", "order": "id.asc"})
    return summarize(body, vehicles)
