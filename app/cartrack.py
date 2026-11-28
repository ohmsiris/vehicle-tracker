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
import re
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


# The plate itself inside whatever Cartrack wrote: "1ฒถ-8966 สระบุรี", "สบ 84-4607", "2 ฒข 3727" -> 1ฒถ8966, 844607, 2ฒข3727. A Thai
# plate is (a digit +) one to three letters + up to four digits, or two/three digits + four digits (a truck's green plate).
_LETTERS = re.compile(r"(?<!\d)(\d{0,2}\s*[\u0e01-\u0e2e]{1,3})[\s.\-]*(\d{1,4})(?!\d)")
_NUMBERS = re.compile(r"(?<!\d)(\d{2,3})[\s.\-]*(\d{4})(?!\d)")
_NAME_KEYS = ("vehicle_name", "name", "description", "vehicle_description", "alias", "display_name")


def cores(text) -> list[str]:
    """Every plate-shaped piece of a text, the green-plate form (84-4607) first: in "สบ 84-4607" the province's "สบ 84" is one too,
    so a match tries them all."""
    s = str(text or "")
    out = [m.group(1) + m.group(2) for m in _NUMBERS.finditer(s)] + [m.group(1) + m.group(2) for m in _LETTERS.finditer(s)]
    return list(dict.fromkeys(re.sub(r"[\s.\-]", "", c) for c in out))


def core_plate(text) -> str:
    got = cores(text)
    return max(got, key=lambda c: len(_digits(c))) if got else ""


def name_of(row: dict) -> str:
    """The vehicle's own name in Cartrack (often a number or the driver), to help the owner tell which truck it is."""
    for key in _NAME_KEYS:
        if row.get(key) and isinstance(row[key], (str, int)):
            return str(row[key]).strip()[:60]
    return ""


def _digits(s) -> str:
    return re.sub(r"\D", "", s or "")


def summarize(body, vehicles: list[dict]) -> dict:
    """What check() shows. Pure: body = the answer to /vehicles/status, vehicles = the trucks here.

    A vehicle is ours only when its plate is a truck's plate here (the owner's login sees every HQ branch). The plate is looked for in
    the registration first, then in the vehicle's name and other text -- by its core (letters + digits), so a province or spacing
    does not matter. Never by the number alone: a Cartrack plate whose last four digits are those of exactly one of our trucks
    without GPS is only a guess for the owner to look at ("อาจเป็น")."""
    rows = rows_of(body)
    mine: dict = {}
    for v in vehicles:
        k = core_plate(v.get("plate_number"))  # the fullest plate-shaped piece
        if k:
            mine.setdefault(k, v)
    matched, unknown, sample, seen = [], [], {}, set()
    for r in rows:
        plate, name = plate_of(r), name_of(r)
        texts = [plate, name] + [x for x in r.values() if isinstance(x, str)]
        k = next((c for t in texts for c in cores(t) if c in mine and c not in seen), None)
        v = mine.get(k) if k else None
        if v:
            seen.add(k)
            matched.append({"plate": plate or name, "name": name, "truck": v.get("vehicle_number"), "branch": v.get("branch")})
            if not sample:
                sample = r
        else:
            unknown.append({"plate": plate, "name": name, "core": core_plate(plate) or core_plate(name)})
    missing = [{"plate": v.get("plate_number"), "truck": v.get("vehicle_number"), "branch": v.get("branch")}
               for k, v in mine.items() if k not in seen and v.get("active") is not False]
    last4: dict = {}
    for m in missing:
        last4.setdefault(_digits(m["plate"])[-4:], []).append(m)
    for u in unknown:
        d = _digits(u["core"])
        hits = last4.get(d[-4:]) if len(d) >= 4 else None
        if hits and len(hits) == 1:
            u["guess"] = hits[0]
    unknown.sort(key=lambda u: (not u.get("guess"), u["plate"] or u["name"]))
    fields = sorted({k for r in rows for k in r})
    by_branch: dict = {}
    for m in matched:
        by_branch[m["branch"] or "-"] = by_branch.get(m["branch"] or "-", 0) + 1
    return {"ok": True, "vehicles": len(rows), "matched": len(matched), "matched_by_branch": by_branch, "trucks": matched[:200],
            "not_here_count": len(unknown), "not_here": unknown[:150], "no_gps_count": len(missing), "no_gps": missing[:80],
            "fields": fields, "sample": {k: sample[k] for k in list(sample)[:40]},
            "meta": body.get("meta") if isinstance(body, dict) else None}


def check() -> dict:
    """Does the key work, and what does the account send? Read-only, one call."""
    from app import supabase
    body = _get("/vehicles/status")
    vehicles = supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch,active", "order": "id.asc"})
    return summarize(body, vehicles)
