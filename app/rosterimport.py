"""
One-time import of the master vehicle list (Drivers.xlsx) into the database.

Sheet 1 ("Vehicle Roster"): one block per branch, one row per truck -- branch, driver (full name,
nickname, phone), fleet number, size class (รถเล็ก / รถใหญ่), plate, province, make, model. A driver
written as "หน้าโรง" is not a person: it marks a truck that stays at the factory.
Sheet 2 ("รายละเอียดทะเบียน"): the registration papers by plate -- colour, engine / chassis numbers,
registration date, type, owner, holder.

Everything here is pure (rows in, plan out) so it is unit-tested; scripts/import_roster.py does the
reading and writing. After the import the site is the master and the spreadsheet is just the source.

What the import is allowed to change, so that re-running it (or running it after someone has edited
on the site) never undoes anything:
  * identity details (province, make, model, size class): the sheet wins when it has a value
  * usage, main driver, registration details, driver phone / nickname: filled only when empty
  * branch / fleet number / plate of an existing truck: never changed -- a difference is reported
"""
import re
from typing import Optional

FACTORY = "หน้าโรง"                    # in the driver column: the truck stays at the factory
USAGE_ROUTE, USAGE_FACTORY = "รถสาย", "รถหน้าโรง"
# abbreviated month names with the dots left out, because the sheet writes both "มิ.ย." and "มิย."
THAI_MONTHS = {"มค": 1, "กพ": 2, "มีค": 3, "เมย": 4, "พค": 5, "มิย": 6, "กค": 7, "สค": 8, "กย": 9, "ตค": 10, "พย": 11, "ธค": 12}


def squash(text) -> str:
    return re.sub(r"[\s\-_.]", "", str(text or "")).lower()


def clean(value) -> Optional[str]:
    """Trimmed text, or None for blanks and the sheet's "-" placeholder."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return None if text in ("", "-", "–") else text


def parse_phone(value) -> Optional[str]:
    """Excel keeps phone numbers as numbers and so drops the leading 0 (959354443 -> 0959354443)."""
    if value in (None, ""):
        return None
    digits = re.sub(r"\D", "", str(int(value)) if isinstance(value, (int, float)) else str(value))
    if not digits:
        return None
    if len(digits) == 9 and digits[0] != "0":
        digits = "0" + digits
    return digits


def parse_thai_date(value) -> Optional[str]:
    """'11-ก.พ.-59' / '29 ก.พ. 59' / '25-มี.ค.-2558' -> '2016-02-11' (ISO, ค.ศ.). Years are พ.ศ.; two digits means 25xx."""
    if value is None:
        return None
    if hasattr(value, "date"):                       # a real Excel date
        value = value.date()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    m = re.match(r"^\s*(\d{1,2})[\s\-/]+([^\s\-/\d]+)[\s\-/]+(\d{2,4})\s*$", str(value))
    if not m:
        return None
    day, month_text, year = int(m.group(1)), m.group(2), int(m.group(3))
    month = THAI_MONTHS.get(month_text.replace(".", ""))
    if month is None:
        return None
    year = 2500 + year if year < 100 else year
    year -= 543 if year >= 2400 else 0
    try:
        from datetime import date
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# ----------------------------------------------------------------- reading
def parse_master(rows: list[list]) -> list[dict]:
    """The truck rows of sheet 1. Header rows (first cell 'ลำดับ') tell which column is which, so a block whose
    columns were shuffled still reads right. A block starts with a title such as 'รถแก่งคอย'; a row inside it
    belongs to that branch even if its own สาขา cell says otherwise (a truck that moved between branches keeps
    the old text in that cell) -- the disagreement is passed on as branch_note so it gets reported."""
    out, cols, block_branch = [], {}, None
    for n, row in enumerate(rows, 1):
        first = clean(row[0]) if row else None
        if first == "ลำดับ":
            cols = {clean(c): i for i, c in enumerate(row) if clean(c)}
            continue
        filled = [clean(c) for c in row if clean(c)]
        title = re.match(r"^รถ(\S+)$", filled[0]) if filled and len(filled) <= 3 and not isinstance(row[0], (int, float)) else None
        if title:
            block_branch, cols = title.group(1), {}
            continue
        if not cols or not filled:
            continue
        # a data row has its running number, or at least a plate / fleet number (some rows lack the number)
        if not isinstance(row[0], (int, float)) and not (clean(row[cols["ทะเบียนรถ"]]) if "ทะเบียนรถ" in cols and cols["ทะเบียนรถ"] < len(row) else None)                 and not (clean(row[cols["รถเบอร์"]]) if "รถเบอร์" in cols and cols["รถเบอร์"] < len(row) else None):
            continue

        def cell(name):
            i = cols.get(name)
            return row[i] if i is not None and i < len(row) else None

        driver = clean(cell("ชื่อจริง พขร"))
        at_factory = driver == FACTORY
        own_branch = clean(cell("สาขา"))
        branch = block_branch or own_branch
        out.append({
            "row": n, "branch": branch,
            "branch_note": f"แถว {n}: คอลัมน์สาขาเขียนว่า {own_branch} แต่อยู่ในกลุ่มรถ{block_branch} — ใช้ {block_branch}" if block_branch and own_branch and own_branch != block_branch else None, "number": clean(cell("รถเบอร์")), "plate": clean(cell("ทะเบียนรถ")),
            "province": clean(cell("จังหวัด")), "make": clean(cell("ยี่ห้อ")), "model": clean(cell("รุ่นรถ")),
            "vehicle_class": clean(cell("ประเภทรถ")),
            "driver_name": None if at_factory else driver, "nickname": clean(cell("ชื่อเล่น")), "phone": parse_phone(cell("เบอร์โทร")),
            "usage": USAGE_FACTORY if at_factory else (USAGE_ROUTE if driver else None),
        })
    return out


def parse_registration(rows: list[list]) -> dict[str, dict]:
    """Sheet 2 by squashed plate."""
    cols, out = {}, {}
    for row in rows:
        names = [clean(c) for c in row]
        if "ทะเบียน" in names and not cols:
            cols = {name: i for i, name in enumerate(names) if name}
            continue
        if not cols:
            continue

        def cell(name):
            i = cols.get(name)
            return row[i] if i is not None and i < len(row) else None

        plate = clean(cell("ทะเบียน"))
        if not plate:
            continue
        out[squash(plate)] = {
            "color": clean(cell("สี")), "engine_number": clean(cell("เลขเครื่อง")), "chassis_number": clean(cell("เลขตัวรถ")),
            "registered_date": parse_thai_date(cell("วันจดทะเบียน")), "registration_type": clean(cell("ประเภท")),
            "owner_name": clean(cell("ชื่อกรรมสิทธิ์")), "holder_name": clean(cell("ชื่อผู้ครอบครอง")),
        }
    return out


# ---------------------------------------------------------------- the plan
def _label(v: dict) -> str:
    return f"{v.get('branch') or ''} #{v.get('vehicle_number') or v.get('number') or '?'} {v.get('plate_number') or v.get('plate') or ''}".strip()


def build_plan(master: list[dict], registrations: dict[str, dict], vehicles: list[dict], drivers: list[dict]) -> dict:
    """What to write, without writing it. `vehicles` / `drivers` are the current database rows."""
    by_plate = {squash(v["plate_number"]): v for v in vehicles}
    by_number: dict[tuple, list] = {}               # a number can repeat inside a branch: a big truck #2 and a small truck #2
    for v in vehicles:
        if v.get("vehicle_number"):
            by_number.setdefault((v.get("branch") or "", squash(v["vehicle_number"])), []).append(v)
    known_drivers = {squash(d["name"]): d for d in drivers if d.get("name")}
    plan = {"vehicle_updates": [], "vehicle_creates": [], "driver_creates": {}, "driver_updates": [], "conflicts": [], "skipped": [],
            "differences": [], "matched": 0, "db_not_in_sheet": []}
    seen = set()

    for m in master:
        if m.get("branch_note"):
            plan["differences"].append(m["branch_note"])
        by_p = by_plate.get(squash(m["plate"])) if m["plate"] else None
        candidates = by_number.get((m["branch"] or "", squash(m["number"])), []) if m["number"] else []
        if len(candidates) > 1:                                       # narrow by size class, else by the plate we already matched
            candidates = [c for c in candidates if c.get("vehicle_class") == m["vehicle_class"]] or ([by_p] if by_p in candidates else candidates)
        by_n = candidates[0] if len(candidates) == 1 else None
        if len(candidates) > 1 and not by_p:
            plan["conflicts"].append(f"แถว {m['row']}: เบอร์ {m['number']} ของสาขา{m['branch']} ตรงกับรถหลายคัน และไม่มีทะเบียนให้ตัดสิน — ข้ามแถวนี้")
            continue
        if by_p and by_n and by_p["id"] != by_n["id"]:
            plan["conflicts"].append(f"แถว {m['row']}: ทะเบียน {m['plate']} ตรงกับ {_label(by_p)} แต่เบอร์ {m['number']} ตรงกับ {_label(by_n)} — ข้ามแถวนี้")
            continue
        truck = by_p or by_n
        reg = registrations.get(squash(m["plate"]), {}) if m["plate"] else {}

        driver_key = None
        if m["driver_name"]:
            driver_key = squash(m["driver_name"])
            existing = known_drivers.get(driver_key)
            if existing:
                fill = {k: m[k] for k in ("nickname", "phone") if m[k] and not existing.get(k)}
                if fill:
                    plan["driver_updates"].append({"id": existing["id"], "name": existing["name"], "values": fill})
            elif driver_key not in plan["driver_creates"]:
                plan["driver_creates"][driver_key] = {"name": m["driver_name"], "nickname": m["nickname"], "phone": m["phone"], "branch": m["branch"]}

        if truck is None:
            if not m["plate"]:
                plan["skipped"].append(f"แถว {m['row']}: {m['branch']} #{m['number']} ไม่มีทะเบียน — เพิ่มเองในหน้าทะเบียนรถเมื่อมีทะเบียน")
                continue
            values = {"plate_number": m["plate"], "plate_province": m["province"] or "", "branch": m["branch"], "vehicle_number": m["number"],
                      "make": m["make"], "model": m["model"], "vehicle_class": m["vehicle_class"], "usage": m["usage"], **{k: v for k, v in reg.items() if v}}
            plan["vehicle_creates"].append({"label": _label({**m}), "values": values, "driver_key": driver_key})
            continue

        seen.add(truck["id"])
        plan["matched"] += 1
        values = {}
        for key, col in (("plate_province", "province"), ("make", "make"), ("model", "model")):
            if m[col] and (truck.get(key) or "") != m[col]:
                if truck.get(key) and squash(truck[key]) != squash(m[col]):
                    plan["differences"].append(f"{_label(truck)}: {col} {truck[key]!r} → {m[col]!r} (ตามไฟล์)")
                values[key] = m[col]
        if m["vehicle_class"] and truck.get("vehicle_class") != m["vehicle_class"]:
            values["vehicle_class"] = m["vehicle_class"]
        if m["usage"] and not truck.get("usage"):
            values["usage"] = m["usage"]
        for key, val in reg.items():
            if val and not truck.get(key):
                values[key] = val
        if (m["branch"] or "") != (truck.get("branch") or "") or (m["number"] and squash(m["number"]) != squash(truck.get("vehicle_number"))):
            plan["differences"].append(f"{_label(truck)}: ในไฟล์เป็น {m['branch']} #{m['number']} (ไม่เปลี่ยนให้)")
        link = None
        if driver_key:
            existing = known_drivers.get(driver_key)
            if truck.get("main_driver_id"):
                if existing and truck["main_driver_id"] != existing["id"]:
                    plan["differences"].append(f"{_label(truck)}: คนขับหลักในระบบไม่ตรงกับไฟล์ ({m['driver_name']}) — ไม่เปลี่ยนให้")
            else:
                link = driver_key
        if values or link:
            plan["vehicle_updates"].append({"id": truck["id"], "label": _label(truck), "values": values, "driver_key": link})

    plan["db_not_in_sheet"] = [_label(v) for v in vehicles if v["id"] not in seen and v.get("active", True)]
    return plan
