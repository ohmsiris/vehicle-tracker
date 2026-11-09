"""
บัญชี › น้ำมันบัตร: fuel bought on credit at the stations (Bangchak, PT), billed to the company once a month -- one entry per truck per
station per company month, so the books and the km / fuel check (acc_docs.fuel_check, account 6120) see each truck's fuel.

Source today: the owner's "Drivers" Google Sheet --
  * "Fuel2": a block per month; the driver table's "Fuel Bangchak" column (= the handwritten Bangchak page "26-25/M"), and beside it the
    PT statement by plate (all trucks, หน้าโรง too). Its "Fuel Cash" column is NOT used: that is petty-cash fuel, already counted from the
    mid / end-of-month papers.
  * "Fuel": PT's transaction list for one month (plate, km, litres, amount), added up by plate.

Each entry (dated the 25th, the end of the company month): Dr 6120 fuel (net of VAT) + 1400 input VAT (the stations' monthly bills are
tax invoices in the company's name), Cr 2100 owed to the station. The truck decides the branch, except
SHEET_BRANCH: a truck of one branch whose card fuel is on the other branch's sheet is booked where it landed (owner, 9 Nov 2026: บฉ 6159
is Kaeng Khoi's but moved to Saraburi; its card fuel on Saraburi's sheet is Saraburi's, the cash fuel on Kaeng Khoi's papers stays there).
build is pure and unit-tested.
"""
import re
from datetime import date

from app.acc_docs import norm_plate

STATIONS = {"bangchak": "บางจาก", "pt": "PT"}
SHEET_BRANCH = {"บฉ6159": "สระบุรี"}          # norm_plate -> branch whose sheet its card fuel is on


def find_vehicle(key: str, by: dict, vehicles: list[dict]):
    """A plate as written; "#12" = truck number 12 (a driver row with no plate); a plate the sheet turned into a number ("-1025" for
    บพ-1025, "6159" for บฉ 6159) matches the one vehicle whose plate ends in those digits."""
    key = str(key).strip()
    if key.startswith("#"):      # truck numbers repeat between branches: the route trucks are Saraburi's
        hits = [v for v in vehicles if str(v.get("vehicle_number")) == key[1:]]
        return next((v for v in hits if v.get("branch") == "สระบุรี"), hits[0] if hits else None)
    v = by.get(norm_plate(key))
    if v:
        return v
    digits = re.sub(r"\D", "", key)
    if len(digits) == 4:
        hits = [x for x in vehicles if re.sub(r"\D", "", x.get("plate_number") or "").endswith(digits)]
        if len(hits) == 1:
            return hits[0]
    return None


def build(month: str, station: str, by_plate: dict, vehicles: list[dict], source_note: str = "") -> dict:
    by = {norm_plate(v["plate_number"]): v for v in vehicles if v.get("plate_number")}
    y, m = (int(x) for x in month.split("-"))
    entries, flags = [], []
    for plate, amount in sorted(by_plate.items()):
        amount = round(float(amount), 2)
        if amount <= 0:
            continue
        v = find_vehicle(plate, by, vehicles)
        if not v:
            flags.append(f"{STATIONS[station]} {month}: ไม่พบรถทะเบียน {plate} ({amount:,.2f}) — ลงสาขาสระบุรีไว้")
        branch = SHEET_BRANCH.get(norm_plate(v["plate_number"])) if v else None
        branch = branch or (v or {}).get("branch") or "สระบุรี"
        vat = round(amount * 7 / 107, 2)
        lines = [("6120", round(amount - vat, 2), 0.0), ("1400", vat, 0.0), ("2100", 0.0, amount)]
        label = f"#{v['vehicle_number']} · {v['plate_number']}" if v else plate
        entries.append({"entry_date": date(y, m, 25).isoformat(), "branch": branch, "source": "card_fuel",
                        "source_id": f"fuel:{station}:{month}:{norm_plate(plate)}", "party": STATIONS[station], "doc_type": "tax_invoice", "doc_no": None,
                        "description": f"น้ำมัน{STATIONS[station]} {label} {m:02d}/{y + 543}" + (f" · {source_note}" if source_note else ""),
                        "lines": [{"account": a, "debit": d, "credit": c, "branch": branch, "vehicle_id": (v or {}).get("id"), "machine_id": None,
                                   "memo": None} for a, d, c in lines]})
    return {"entries": entries, "flags": flags}
