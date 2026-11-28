"""
บัญชี › น้ำมันบัตร: fuel bought on credit at the stations (Bangchak, PT), billed to the company once a month -- one entry per truck per
station per company month, so the books and the km / fuel check (acc_docs.fuel_check, account 6120) see each truck's fuel.

Source today: the owner's "Drivers" Google Sheet --
  * "Fuel2": a block per month; the driver table's "Fuel Bangchak" column (= the handwritten Bangchak page "26-25/M"), and beside it the
    PT statement by plate (all trucks, หน้าโรง too). Its "Fuel Cash" column is NOT used: that is petty-cash fuel, already counted from the
    mid / end-of-month papers.
  * "Fuel": PT's transaction list for one month (plate, km, litres, amount), added up by plate.

Each entry (dated the 25th, the end of the company month): Dr 6120 fuel (net of VAT) + 1400 input VAT (the stations' monthly bills are
tax invoices in the company's name), Cr 2100 owed to the station. The branch is WHO PAID, not whose truck it is (owner, 9 Nov 2026):
every card-fuel line is on Saraburi's sheet and Saraburi pays it, so it is Saraburi's cost even for a truck the app files under
Kaeng Khoi (trucks move between the plants; the truck keeps its branch in the app). Cash fuel on Kaeng Khoi's papers stays there.
build is pure and unit-tested.

Keys: source_id "fuel:<station>:<month>:<plate>" -- the scripts and the Drivers sheet booked Bangchak's two stations (NM, ห้วยบง) as ONE
entry per plate per month. The drop box (app/inbox_r_fuel.py, 24 Nov 2026) reads each station's file on its own, so it passes sub=<station>
and the key becomes "fuel:bangchak:<month>:<station>:<plate>": dropping one station's file replaces that station only. PT keeps the old
key (one file has every PT fill). Since Nov 2026 Kaeng Khoi's trucks fill at a third Bangchak station (แก่งคอย): its file passes
prefer="แก่งคอย", so a truck number (or a 4-digit plate tail) that both plants have resolves to Kaeng Khoi's truck, and its own payer.
A stand-in's fills (owner, 28 Nov 2026: someone drove the route in place of its own driver -- that day's fuel comes off nobody's
commission, but stays the route's for the accounts) are booked by the drop box as their OWN entry of the same plate, tail="sub":
"fuel:bangchak:<month>:<station>:<plate>:sub". Payroll leaves those out of the driver's fuel; everything else reads them as fuel.
"""
import re
from datetime import date
from typing import Optional

from app.acc_docs import norm_plate

STATIONS = {"bangchak": "บางจาก", "pt": "PT"}
PAYER = "สระบุรี"                 # the sheet the card fuel is on, and who pays it


def find_vehicle(key: str, by: dict, vehicles: list[dict], prefer: Optional[str] = None):
    """A plate as written; "#12" = truck number 12 (a driver row with no plate); a plate the sheet turned into a number ("-1025" for
    บพ-1025, "6159" for บฉ 6159) matches the one vehicle whose plate ends in those digits. Truck numbers repeat between branches:
    prefer = the branch whose truck it is when both have one (the route trucks are Saraburi's unless the station says otherwise, e.g.
    บางจาก แก่งคอย); a 4-digit tail two trucks share is resolved by prefer only when the caller gave one."""
    key = str(key).strip()
    if key.startswith("#"):
        hits = [v for v in vehicles if str(v.get("vehicle_number")) == key[1:]]
        return next((v for v in hits if v.get("branch") == (prefer or "สระบุรี")), hits[0] if hits else None)
    v = by.get(norm_plate(key))
    if v:
        return v
    digits = re.sub(r"\D", "", key)
    if len(digits) == 4:
        hits = [x for x in vehicles if re.sub(r"\D", "", x.get("plate_number") or "").endswith(digits)]
        if len(hits) == 1:
            return hits[0]
        mine = [x for x in hits if prefer and x.get("branch") == prefer]
        if len(mine) == 1:
            return mine[0]
    return None


def build(month: str, station: str, by_plate: dict, vehicles: list[dict], source_note: str = "", payer: str = PAYER, sub: str = "",
          prefer: Optional[str] = None, tail: str = "") -> dict:
    """sub: an extra key segment after the month (the Bangchak station, from the drop box); "" keeps the scripts' keys.
    prefer: the branch whose trucks fill at this station (find_vehicle); None keeps the scripts' lookup.
    tail: an extra key segment after the plate ("sub" = a stand-in's fills, owner 28 Nov 2026); "" keeps the keys as they were."""
    by: dict = {}
    for v in vehicles:
        if v.get("plate_number") and (norm_plate(v["plate_number"]) not in by or (prefer and v.get("branch") == prefer)):
            by[norm_plate(v["plate_number"])] = v
    y, m = (int(x) for x in month.split("-"))
    entries, flags = [], []
    for plate, amount in sorted(by_plate.items()):
        amount = round(float(amount), 2)
        if amount <= 0:
            continue
        v = find_vehicle(plate, by, vehicles, prefer)
        if not v:
            flags.append(f"{STATIONS[station]}{(' ' + sub) if sub else ''} {month}: ไม่พบรถทะเบียน {plate} ({amount:,.2f}) — ลงไว้ไม่ผูกกับรถ")
        branch = payer
        vat = round(amount * 7 / 107, 2)
        lines = [("6120", round(amount - vat, 2), 0.0), ("1400", vat, 0.0), ("2100", 0.0, amount)]
        label = f"#{v['vehicle_number']} · {v['plate_number']}" if v else plate
        entries.append({"entry_date": date(y, m, 25).isoformat(), "branch": branch, "source": "card_fuel",
                        "source_id": f"fuel:{station}:{month}:{(sub + ':') if sub else ''}{norm_plate(plate)}{(':' + tail) if tail else ''}", "party": STATIONS[station], "doc_type": "tax_invoice", "doc_no": None,
                        "description": f"น้ำมัน{STATIONS[station]}{(' ' + sub) if sub else ''} {label} {m:02d}/{y + 543}" + (f" · {source_note}" if source_note else ""),
                        "lines": [{"account": a, "debit": d, "credit": c, "branch": branch, "vehicle_id": (v or {}).get("id"), "machine_id": None,
                                   "memo": None} for a, d, c in lines]})
    return {"entries": entries, "flags": flags}
