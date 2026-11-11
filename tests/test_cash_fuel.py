"""น้ำมันเงินสด (app/cash_fuel.py): the pure parts."""
from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from app import cash_fuel as cf

ROSTER = [{"id": "a", "truck_number": "52", "plate": "2ฒค 3332", "branch": "สระบุรี", "driver": "มานัส ใจดี"},
          {"id": "b", "truck_number": "34", "plate": "84-4764", "branch": "สระบุรี", "driver": "ประเสริฐ ดี"},
          {"id": "c", "truck_number": "52", "plate": "70-1111", "branch": "แก่งคอย", "driver": "คนอื่น"},
          {"id": "d", "truck_number": "", "plate": "มพ 1093", "branch": "สระบุรี", "driver": None}]


def test_company_month_and_half():
    assert cf.month_of(date(2026, 10, 26)) == ("2026-11", 1)
    assert cf.month_of(date(2026, 11, 10)) == ("2026-11", 1)
    assert cf.month_of(date(2026, 11, 11)) == ("2026-11", 2)
    assert cf.month_of(date(2026, 12, 27)) == ("2027-01", 1)
    assert cf.period("2026-11") == (date(2026, 10, 26), date(2026, 11, 25))
    assert cf.period("2026-11", 2) == (date(2026, 11, 11), date(2026, 11, 25))


def test_which_truck():
    assert cf.match_vehicle("2ฒค3332", "52", ROSTER)["id"] == "a"            # number first, Saraburi's
    assert cf.match_vehicle("84-4764", None, ROSTER)["id"] == "b"
    assert cf.match_vehicle("มพ1093", None, ROSTER)["id"] == "d"
    assert cf.match_vehicle("6161", None, ROSTER) is None


def test_two_reads_that_disagree_are_flagged():
    a = [{"is_fuel": True, "date": "2026-10-26", "amount": 1000, "plate": "6161", "truck_number": None},
         {"is_fuel": False, "text": "OT"},
         {"is_fuel": True, "date": "2026-10-26", "amount": 1000, "plate": "2ฒค 3332", "truck_number": "52"}]
    b = [{"is_fuel": True, "date": "2026-10-26", "amount": 1000, "plate": "6161", "truck_number": None},
         {"is_fuel": True, "date": "2026-10-26", "amount": 1500, "plate": "2ฒค3332", "truck_number": "52"}]
    d = cf.disagreements(a, b)
    assert list(d) == [2] and "จำนวนเงิน 1500" in d[2]                    # spaces in a plate are not a disagreement


def test_who_took_the_money():
    ppl = {"route": [{"name": "มานัส ใจดี", "nickname": "นัส", "truck": "52"}], "foh": [{"name": "สมชาย ดี", "nickname": "", "truck": None}]}
    assert cf.who_took("มานัส", ppl) == "มานัส ใจดี" and cf.who_took("นัส", ppl) == "มานัส ใจดี"
    assert cf.who_took("ไม่รู้", ppl) is None and cf.who_took("", ppl) is None


def test_summary_per_truck_and_the_file():
    lines = [{"vehicle_id": "a", "day": "2026-10-30", "amount": 1000, "counted": True, "batch": 1, "driver_name": "มานัส ใจดี"},
             {"vehicle_id": "a", "day": "2026-10-28", "amount": 1000, "counted": True, "batch": 1, "driver_name": "มานัส ใจดี"},
             {"vehicle_id": "b", "day": "2026-11-12", "amount": 1500, "counted": True, "batch": 2, "driver_name": None},
             {"vehicle_id": "b", "day": "2026-11-12", "amount": 1500, "counted": False, "batch": 2}]
    s = cf.summary(lines, ROSTER)
    assert [t["truck_no"] for t in s["trucks"]] == ["34", "52"] and s["total"] == 3500
    assert s["trucks"][1]["driver"] == "มานัส" and [f["day"] for f in s["trucks"][1]["fills"]] == ["2026-10-28", "2026-10-30"]
    assert cf.summary(lines, ROSTER, batch=2)["total"] == 1500
    ws = load_workbook(BytesIO(cf.to_xlsx("2026-11", s))).active
    assert ws["A1"].value == "รายการค่าน้ำมัน" and "26 ตุลาคม 2569" in ws["A2"].value
    assert ws.cell(row=5, column=1).value == "34"


def test_a_plate_and_number_that_point_at_different_trucks_are_flagged():
    read = {"lines": [{"is_fuel": True, "date": "2026-11-03", "text": "เติมน้ำมัน 84-4764 52", "plate": "84-4764", "truck_number": "52",
                       "amount": 1000, "requester": None, "position": None, "unsure": False}]}
    ln = cf.fuel_lines(read, ROSTER, "สระบุรี", {})[0]
    assert ln["vehicle_id"] == "a" and "เป็นของรถ 34" in ln["note"]
