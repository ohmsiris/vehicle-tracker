"""
One-time import of the machinery spreadsheet ("Saraburi Maintenence Sheet" in Google Sheets, downloaded as .xlsx):

  * "อะไหล่ Main"       -> machine_parts  (one row per part: location, equipment, part, spec, qty, unit ...)
  * "Motor Index Main"  -> the same rows' status / current contactor / overload / desired A, or a new row
                           for a motor the first sheet does not have
  * "Pressure Relief Valve" -> machine_parts (one row each for the machine / condenser / receiver valve)
  * "Saraburi_General", "Kaeng Khoi" -> machine_events (equipment that was changed, with vendor and reason)

plus the seven Saraburi machines. Everything here is pure (rows in, plan out) so it is unit-tested;
scripts/import_machinery.py does the reading and writing. Re-running is safe: a row whose natural key already
exists is skipped, so nothing is ever duplicated or overwritten.
"""
import re
from collections import Counter
from datetime import date, datetime
from typing import Optional

BRANCH = "สระบุรี"
# From the user's own description of the plant: the five numbered ice machines and the two brine-pond machines
# (which usually have no number: "เครื่องบ่อซอง Sabroe / MYCOM 30 ตัน").
SARABURI_MACHINES = [
    {"branch": BRANCH, "kind": "เครื่อง", "number": 1, "tube_type": "หลอดใหญ่", "make": "MYCOM", "model": "8WB-HE", "capacity_tons": 50},
    {"branch": BRANCH, "kind": "เครื่อง", "number": 2, "tube_type": "หลอดใหญ่", "make": "MYCOM", "model": "8WB-HE", "capacity_tons": 50},
    {"branch": BRANCH, "kind": "เครื่อง", "number": 3, "tube_type": "หลอดเหลี่ยม", "make": "MYCOM", "model": "8WB-HE", "capacity_tons": 50},
    {"branch": BRANCH, "kind": "เครื่อง", "number": 4, "tube_type": "หลอดเล็ก", "make": "Sabroe", "model": "SMC108L", "capacity_tons": 30},
    {"branch": BRANCH, "kind": "เครื่อง", "number": 5, "tube_type": "หลอดใหญ่", "make": "MYCOM", "model": "8WB-HE", "capacity_tons": 50},
    {"branch": BRANCH, "kind": "บ่อซอง", "number": None, "tube_type": None, "make": "Sabroe", "model": "SMC108L", "capacity_tons": 30},
    {"branch": BRANCH, "kind": "บ่อซอง", "number": None, "tube_type": None, "make": "MYCOM", "model": None, "capacity_tons": 30},
]


def squash(text) -> str:
    return re.sub(r"[\s\-_.]", "", str(text or "")).lower()


def clean(value) -> Optional[str]:
    """Trimmed text, or None for blanks and "-"; whole numbers lose their ".0" (a spec of 6307 arrives as 6307.0)."""
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        value = int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return None if text in ("", "-", "–") else text


def clean_lines(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        value = int(value)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in str(value).splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text or None


def number(value) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value == int(value) else value
    try:
        n = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return int(n) if n == int(n) else n


def machine_for(location: str, machines: list[dict]) -> Optional[dict]:
    """The machine a sheet location belongs to: "เครื่อง 3" / "หลอด 3" -> ice machine 3; "ซอง MYCOM" -> the MYCOM
    brine-pond machine. Anything else (โรงซอง 1, ลานโหลด, ระบบน้ำ ...) is an area, not a machine."""
    loc = re.sub(r"\s+", " ", str(location or "")).strip()
    m = re.match(r"^(?:เครื่อง|หลอด)\s*(\d+)$", loc)
    if m:
        return next((x for x in machines if x.get("kind") == "เครื่อง" and x.get("number") == int(m.group(1))), None)
    m = re.match(r"^ซอง\s+(\S+)$", loc)
    if m:
        return next((x for x in machines if x.get("kind") == "บ่อซอง" and squash(x.get("make")) == squash(m.group(1))), None)
    return None


# ----------------------------------------------------------------- reading
def parse_main(rows: list[list]) -> list[dict]:
    """"อะไหล่ Main": header row, then one row per part."""
    out = []
    for n, row in enumerate(rows[1:], 2):
        cells = list(row) + [None] * (14 - len(row))
        location, equipment, part, spec, qty, unit, bore, hp, delta, star, link, breaker, contactor, overload = cells[:14]
        if not clean(location) and not clean(equipment) and not clean(part):
            continue
        qty_n = number(qty)
        note = f"จำนวน: {clean(qty)}" if qty_n is None and clean(qty) else None
        out.append({
            "row": n, "location": clean(location), "equipment": clean(equipment), "part": clean(part), "spec": clean_lines(spec),
            "qty": qty_n, "unit": clean(unit), "bearing_bore": clean(bore), "motor_hp": number(hp), "delta_a": clean(delta), "star_a": clean(star),
            "link": clean(link), "breaker": clean_lines(breaker), "contactor": clean_lines(contactor), "overload": clean_lines(overload), "notes": note,
        })
    return [r for r in out if r["location"]]


def parse_motors(rows: list[list]) -> list[dict]:
    """"Motor Index Main": No, Location, มอเตอร์, Model, HP, Delta A, Star A, Link, Breaker, Magnetic, Overload, Status, Current Magnetic, Current Overload, Desired A."""
    out = []
    for n, row in enumerate(rows[1:], 2):
        cells = list(row) + [None] * (15 - len(row))
        no, location, name, model, hp, delta, star, link, breaker, magnetic, overload, status, cur_mag, cur_over, desired = cells[:15]
        if not isinstance(no, (int, float)) or not clean(location) or not clean(name):
            continue
        out.append({"row": n, "location": clean(location), "equipment": clean(name), "spec": clean_lines(model), "motor_hp": number(hp), "delta_a": clean(delta),
                    "star_a": clean(star), "link": clean(link), "breaker": clean_lines(breaker), "contactor": clean_lines(magnetic), "overload": clean_lines(overload),
                    "status": clean(status), "current_contactor": clean_lines(cur_mag), "current_overload": clean_lines(cur_over), "desired_a": clean(desired)})
    return out


def parse_prv(rows: list[list]) -> list[dict]:
    """"Pressure Relief Valve": per machine, the valve on the machine / condenser / receiver (model, PSI, count)."""
    out = []
    for row in rows:
        cells = list(row) + [None] * (11 - len(row))
        label, _spec, m_model, m_psi, m_qty, c_model, c_psi, c_qty, r_model, r_psi, r_qty = cells[:11]
        if isinstance(label, (int, float)):
            location = f"เครื่อง {int(label)}"
        elif isinstance(label, str) and label.strip().startswith("ซอง "):
            location = label.strip()
        else:
            continue
        for part, model, psi, qty in (("เครื่อง", m_model, m_psi, m_qty), ("คอนเดนเซอร์", c_model, c_psi, c_qty), ("Receiver", r_model, r_psi, r_qty)):
            if clean(model):
                out.append({"location": location, "equipment": "Pressure Relief Valve", "part": part, "spec": clean(model), "qty": number(qty),
                            "unit": "ตัว" if number(qty) else None, "notes": f"{clean(psi)} PSI" if clean(psi) and number(psi) is not None else None})
    return out


def parse_changes(rows: list[list], branch: str) -> list[dict]:
    """"Saraburi_General" / "Kaeng Khoi": No., Equipment, Brand, Model, Serial Number, Vendor, Contact, Date Changed, Reason for Changing."""
    out = []
    for row in rows[1:]:
        cells = list(row) + [None] * (9 - len(row))
        _no, equipment, brand, model, serial, vendor, contact, when, reason = cells[:9]
        if not clean(equipment) or not isinstance(when, (datetime, date)):
            continue
        out.append({"branch": branch, "equipment": clean(equipment), "event_date": (when.date() if isinstance(when, datetime) else when).isoformat(),
                    "kind": "เปลี่ยนอะไหล่", "summary": f"เปลี่ยน {clean(equipment)}", "reason": clean(reason), "brand": clean(brand), "model": clean(model),
                    "serial_number": clean(serial), "vendor": clean(vendor), "vendor_contact": clean(contact)})
    return out


# --------------------------------------------------------------------- plan
def merge_motors(parts: list[dict], motors: list[dict]) -> list[dict]:
    """Folds the motor index into the parts rows (status, current contactor / overload, desired A); a motor with
    no matching row becomes a new "มอเตอร์" row. Returns the combined list (new dicts; inputs are not changed)."""
    merged = [dict(p) for p in parts]
    used: set[int] = set()
    for m in motors:
        hit = next((i for i, p in enumerate(merged)
                    if i not in used and squash(p["location"]) == squash(m["location"]) and squash(p.get("equipment")) == squash(m["equipment"])
                    and p.get("part") in ("มอเตอร์", "ปั๊มน้ำ")), None)
        extra = {k: m.get(k) for k in ("status", "current_contactor", "current_overload", "desired_a")}
        if hit is not None:
            used.add(hit)
            merged[hit].update({k: v for k, v in extra.items() if v})
            for k in ("spec", "motor_hp", "delta_a", "star_a", "link", "breaker", "contactor", "overload"):
                if not merged[hit].get(k) and m.get(k) is not None:
                    merged[hit][k] = m[k]
        else:
            merged.append({"location": m["location"], "equipment": m["equipment"], "part": "มอเตอร์", "spec": m["spec"], "qty": 1, "unit": "ตัว",
                           **{k: m.get(k) for k in ("motor_hp", "delta_a", "star_a", "link", "breaker", "contactor", "overload")}, **extra})
    return merged


def part_key(p: dict) -> tuple:
    return (squash(p.get("location")), squash(p.get("equipment")), squash(p.get("part")), squash(p.get("spec")))


def event_key(e: dict) -> tuple:
    return (e.get("branch"), e.get("event_date"), squash(e.get("equipment")), squash(e.get("summary")))


def build_plan(*, parts: list[dict], motors: list[dict], prv: list[dict], changes: list[dict], machines: list[dict],
               existing_parts: list[dict], existing_events: list[dict]) -> dict:
    """`machines` = the machines that exist AFTER the machine step (with ids); existing_* are what is already in the database."""
    combined = merge_motors(parts, motors) + prv
    # Two identical-looking rows are legitimate (the sheet lists a part twice), so rows are matched by key AND by how many
    # times the key has been seen: the n-th identical row is skipped only if the database already holds n of them.
    have = Counter(part_key(p) for p in existing_parts)
    seen: Counter = Counter()
    new_parts, skipped = [], 0
    for sort, p in enumerate(combined):
        key = part_key(p)
        seen[key] += 1
        if seen[key] <= have[key]:
            skipped += 1
            continue
        machine = machine_for(p["location"], machines)
        values = {k: v for k, v in p.items() if k != "row" and v is not None}
        values["sort_order"] = sort
        if machine:
            values["machine_id"] = machine["id"]
        new_parts.append(values)
    have_events = {event_key(e) for e in existing_events}
    new_events = [{k: v for k, v in e.items() if v is not None} for e in changes if event_key(e) not in have_events]
    return {"parts": new_parts, "events": new_events, "parts_skipped": skipped, "events_skipped": len(changes) - len(new_events),
            "unlinked_locations": sorted({p["location"] for p in new_parts if "machine_id" not in p})}
