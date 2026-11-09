from app import acc_cardfuel as cf

VEH = [{"id": "a", "plate_number": "บพ 1025", "vehicle_number": "54", "branch": "สระบุรี"},
       {"id": "b", "plate_number": "บฉ 6159", "vehicle_number": "42", "branch": "แก่งคอย"},
       {"id": "c", "plate_number": "84-4607", "vehicle_number": "31", "branch": "สระบุรี"}]


def test_one_entry_per_truck_with_vat_and_the_trucks_branch():
    out = cf.build("2026-07", "pt", {"84-4607": 26000, "6159": 3000, "-1025": 7000, "#31": 107}, VEH)
    assert not out["flags"]
    by = {e["source_id"]: e for e in out["entries"]}
    e = by["fuel:pt:2026-07:844607"]
    d = {ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]}
    assert d == {"6120": 24299.07, "1400": 1700.93} and e["entry_date"] == "2026-07-25"
    assert by["fuel:pt:2026-07:6159"]["branch"] == "สระบุรี"             # a plate the sheet turned into a number
    assert by["fuel:pt:2026-07:#31"]["lines"][0]["vehicle_id"] == "c"   # a driver row with no plate, by truck number


def test_unknown_plate_is_flagged():
    out = cf.build("2026-07", "bangchak", {"ZZ 9": 500}, VEH)
    assert out["flags"] and out["entries"][0]["branch"] == "สระบุรี"


def test_card_fuel_is_booked_to_who_paid_not_whose_truck():
    vs = [{"id": "a", "plate_number": "บฉ 6159", "vehicle_number": "42", "branch": "แก่งคอย"},
          {"id": "b", "plate_number": "1ฒบ 6257", "vehicle_number": "12", "branch": "แก่งคอย"}]
    out = cf.build("2026-05", "pt", {"บฉ 6159": 1070, "1ฒบ 6257": 535}, vs)["entries"]
    assert {e["branch"] for e in out} == {"สระบุรี"} and {ln["branch"] for e in out for ln in e["lines"]} == {"สระบุรี"}
    assert {e["lines"][0]["vehicle_id"] for e in out} == {"a", "b"}          # still the same trucks
