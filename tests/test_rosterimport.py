import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import rosterimport as ri

HEADER = ["ลำดับ", "สาขา", "ชื่อจริง พขร", "ชื่อเล่น", "รถเบอร์", "เบอร์โทร", "ประเภทรถ", "ทะเบียนรถ", "จังหวัด", "ยี่ห้อ", "รุ่นรถ"]


def truck_row(n, branch, driver, nick, number, phone, size, plate, province="กรุงเทพมหานคร", make="ISUZU", model="D-Max 2.5"):
    return [n, branch, driver, nick, number, phone, size, plate, province, make, model]


SHEET1 = [
    [None, None, "รถสระบุรี"], [None] * 3, HEADER,
    truck_row(1, "สระบุรี", "สมชาย ใจดี", "ชาย", "1", 959354443, "รถเล็ก", "1ฒถ 8966"),
    truck_row(2, "สระบุรี", "หน้าโรง ", None, "4", None, "รถเล็ก", "1ฒถ 8967"),
    truck_row(3, "สระบุรี", "สมชาย ใจดี", None, "5", None, "รถเล็ก", "1ฒถ 2085"),            # the same driver on a second truck
    [None] * 11,
    ["รถแก่งคอย", "รถแก่งคอย"], HEADER,
    truck_row(4, "แก่งคอย", "หน้าโรง", None, "23", None, "รถใหญ่", None, None, "ISUZU", "NKR 4ล้อ"),
    truck_row(5, "แก่งคอย", "วิชัย แก้วมณี", "ชัย", "42", 861234567, "รถใหญ่", "84-4786", "สระบุรี", "-", "-"),
]


# -------------------------------------------------------------- small parsers
@pytest.mark.parametrize("raw, expected", [
    ("11-ก.พ.-59", "2016-02-11"), ("29 ก.พ. 59", "2016-02-29"), ("25-มี.ค.-58", "2015-03-25"), ("22-เม.ย.-58", "2015-04-22"),
    ("2-ก.ย.-58", "2015-09-02"), ("25-มี.ค.-2558", "2015-03-25"), ("5/ต.ค./60", "2017-10-05"),
    ("10-มิย.-52", "2009-06-10"), ("21-มิย.-36", "1993-06-21"), ("3-กค-58", "2015-07-03"),
    (datetime(2016, 2, 11), "2016-02-11"), (None, None), ("", None), ("ไม่ทราบ", None), ("31-ก.พ.-59", None),
])
def test_thai_registration_dates(raw, expected):
    assert ri.parse_thai_date(raw) == expected


@pytest.mark.parametrize("raw, expected", [(959354443, "0959354443"), (861234567.0, "0861234567"), ("0812345678", "0812345678"), (None, None), ("", None), ("-", None)])
def test_phone_numbers_get_their_leading_zero_back(raw, expected):
    assert ri.parse_phone(raw) == expected


def test_clean_drops_blanks_and_the_dash_placeholder():
    assert ri.clean("  ISUZU  ") == "ISUZU" and ri.clean("-") is None and ri.clean("  ") is None and ri.clean(None) is None
    assert ri.clean("D-Max   2.5") == "D-Max 2.5"


# ------------------------------------------------------------- reading sheet 1
def test_both_branch_blocks_are_read_and_titles_and_blank_rows_ignored():
    m = ri.parse_master(SHEET1)
    assert [(r["branch"], r["number"]) for r in m] == [("สระบุรี", "1"), ("สระบุรี", "4"), ("สระบุรี", "5"), ("แก่งคอย", "23"), ("แก่งคอย", "42")]
    assert m[0]["phone"] == "0959354443" and m[0]["nickname"] == "ชาย" and m[0]["vehicle_class"] == "รถเล็ก"


def test_a_factory_truck_has_no_driver_and_the_factory_usage():
    m = ri.parse_master(SHEET1)
    assert (m[1]["driver_name"], m[1]["usage"]) == (None, "รถหน้าโรง")           # "หน้าโรง " with a trailing space too
    assert (m[0]["driver_name"], m[0]["usage"]) == ("สมชาย ใจดี", "รถสาย")


def test_missing_plate_and_dash_placeholders_become_none():
    m = ri.parse_master(SHEET1)
    assert m[3]["plate"] is None and m[3]["province"] is None
    assert m[4]["make"] is None and m[4]["model"] is None


def test_columns_are_found_by_header_name_not_position():
    shuffled = [["ลำดับ", "ทะเบียนรถ", "สาขา", "รถเบอร์", "ชื่อจริง พขร"], [1, "1กข 1", "สระบุรี", "9", "ก ข"]]
    m = ri.parse_master(shuffled)
    assert (m[0]["plate"], m[0]["branch"], m[0]["number"], m[0]["driver_name"]) == ("1กข 1", "สระบุรี", "9", "ก ข")


# ------------------------------------------------------------- reading sheet 2
def test_registration_sheet_is_keyed_by_plate_ignoring_spacing():
    rows = [[None, "ทะเบียน", "จังหวัด", "ยี่ห้อ", "สี", "เลขเครื่อง", "เลขตัวรถ", "วันจดทะเบียน", "ประเภท", "ชื่อกรรมสิทธิ์", "ชื่อผู้ครอบครอง"],
            [1, "1ฒฒ1987", "กทม.", "ISUZU", "ขาว-เขียว", "4JK1", "MP1", "11-ก.พ.-59", "รถบรรทุก", "บจ.เจ้าของ", "บจ.ผู้ถือ"],
            [2, None, None, None, None, None, None, None, None, None, None]]
    reg = ri.parse_registration(rows)
    assert list(reg) == [ri.squash("1ฒฒ 1987")]
    r = reg[ri.squash("1ฒฒ 1987")]
    assert r["color"] == "ขาว-เขียว" and r["registered_date"] == "2016-02-11" and r["owner_name"] == "บจ.เจ้าของ" and r["holder_name"] == "บจ.ผู้ถือ"


# ------------------------------------------------------------------ the plan
def db_truck(vid, plate, number, branch="สระบุรี", **kw):
    return {"id": vid, "plate_number": plate, "vehicle_number": number, "branch": branch, "plate_province": "กรุงเทพมหานคร", "make": "ISUZU",
            "model": "D-Max 2.5", "usage": None, "main_driver_id": None, "vehicle_class": None, "active": True, **kw}


def plan_for(vehicles, drivers=(), registrations=None):
    return ri.build_plan(ri.parse_master(SHEET1), registrations or {}, list(vehicles), list(drivers))


VEHICLES = [db_truck("v1", "1ฒถ 8966", "1"), db_truck("v4", "1ฒถ 8967", "4"), db_truck("v5", "1ฒถ 2085", "5"), db_truck("v42", "84-4786", "42", "แก่งคอย", model="NPR")]


def test_matching_trucks_get_class_usage_and_their_driver():
    p = plan_for(VEHICLES)
    assert p["matched"] == 4
    ups = {u["label"].split()[1]: u for u in p["vehicle_updates"]}
    assert ups["#1"]["values"]["vehicle_class"] == "รถเล็ก" and ups["#1"]["values"]["usage"] == "รถสาย" and ups["#1"]["driver_key"] == ri.squash("สมชาย ใจดี")
    assert ups["#4"]["values"]["usage"] == "รถหน้าโรง" and ups["#4"]["driver_key"] is None


def test_one_driver_on_two_trucks_is_created_once_and_linked_to_both():
    p = plan_for(VEHICLES)
    assert len(p["driver_creates"]) == 2                                   # สมชาย (once) and วิชัย
    assert p["driver_creates"][ri.squash("สมชาย ใจดี")] == {"name": "สมชาย ใจดี", "nickname": "ชาย", "phone": "0959354443", "branch": "สระบุรี"}
    linked = [u for u in p["vehicle_updates"] if u["driver_key"] == ri.squash("สมชาย ใจดี")]
    assert len(linked) == 2


def test_an_existing_driver_is_reused_and_only_gets_blanks_filled():
    existing = [{"id": "d1", "name": "สมชาย  ใจดี", "nickname": None, "phone": "0999999999"}]
    p = plan_for(VEHICLES, existing)
    assert ri.squash("สมชาย ใจดี") not in p["driver_creates"]
    assert p["driver_updates"] == [{"id": "d1", "name": "สมชาย  ใจดี", "values": {"nickname": "ชาย"}}]      # phone already there: kept


def test_a_truck_missing_from_the_database_is_created_and_one_without_a_plate_is_skipped():
    p = plan_for(VEHICLES[:3])
    assert [c["values"]["plate_number"] for c in p["vehicle_creates"]] == ["84-4786"]
    assert p["vehicle_creates"][0]["values"]["usage"] == "รถสาย" and p["vehicle_creates"][0]["driver_key"] == ri.squash("วิชัย แก้วมณี")
    assert len(p["skipped"]) == 1 and "#23" in p["skipped"][0]


def test_sheet_wins_on_make_and_model_but_a_dash_never_blanks_anything():
    p = plan_for(VEHICLES)
    up = next(u for u in p["vehicle_updates"] if "#42" in u["label"])
    assert "model" not in up["values"] and "make" not in up["values"]            # the sheet has "-" there


def test_a_changed_model_is_applied_and_reported():
    sheet = [HEADER, truck_row(1, "สระบุรี", None, None, "1", None, "รถเล็ก", "1ฒถ 8966", model="D-Max 1.9")]
    p = ri.build_plan(ri.parse_master(sheet), {}, [db_truck("v1", "1ฒถ 8966", "1")], [])
    assert p["vehicle_updates"][0]["values"]["model"] == "D-Max 1.9" and any("D-Max 2.5" in d for d in p["differences"])


def test_registration_details_fill_blanks_but_never_overwrite():
    reg = {ri.squash("1ฒถ 8966"): {"color": "ขาว", "engine_number": "E1", "chassis_number": None, "registered_date": "2016-02-11",
                                    "registration_type": None, "owner_name": "บจ.ก", "holder_name": "บจ.ข"}}
    p = plan_for([db_truck("v1", "1ฒถ 8966", "1", color="เขียว")], registrations=reg)
    v = p["vehicle_updates"][0]["values"]
    assert "color" not in v and v["engine_number"] == "E1" and v["registered_date"] == "2016-02-11" and "chassis_number" not in v


def test_a_usage_or_driver_someone_set_on_the_site_is_never_replaced():
    existing = [{"id": "d9", "name": "คนอื่น", "nickname": None, "phone": None}]
    p = plan_for([db_truck("v1", "1ฒถ 8966", "1", usage="รถหน้าโรง", main_driver_id="d9")], existing)
    assert "usage" not in p["vehicle_updates"][0]["values"] and p["vehicle_updates"][0]["driver_key"] is None


def test_rerunning_after_the_import_has_nothing_left_to_do():
    done = [db_truck("v1", "1ฒถ 8966", "1", usage="รถสาย", vehicle_class="รถเล็ก", main_driver_id="d1"),
            db_truck("v4", "1ฒถ 8967", "4", usage="รถหน้าโรง", vehicle_class="รถเล็ก"),
            db_truck("v5", "1ฒถ 2085", "5", usage="รถสาย", vehicle_class="รถเล็ก", main_driver_id="d1"),
            db_truck("v42", "84-4786", "42", "แก่งคอย", usage="รถสาย", vehicle_class="รถใหญ่", main_driver_id="d2", plate_province="สระบุรี")]
    drivers = [{"id": "d1", "name": "สมชาย ใจดี", "nickname": "ชาย", "phone": "0959354443"}, {"id": "d2", "name": "วิชัย แก้วมณี", "nickname": "ชัย", "phone": "0861234567"}]
    p = plan_for(done, drivers)
    assert not p["vehicle_updates"] and not p["driver_creates"] and not p["driver_updates"] and not p["vehicle_creates"]


def test_plate_and_number_pointing_at_different_trucks_is_a_conflict_not_a_guess():
    sheet = [HEADER, truck_row(1, "สระบุรี", None, None, "4", None, "รถเล็ก", "1ฒถ 8966")]     # plate of truck 1, number of truck 4
    p = ri.build_plan(ri.parse_master(sheet), {}, [db_truck("v1", "1ฒถ 8966", "1"), db_truck("v4", "1ฒถ 8967", "4")], [])
    assert len(p["conflicts"]) == 1 and not p["vehicle_updates"]


def test_trucks_in_the_database_but_not_on_the_sheet_are_listed_and_the_branch_or_number_is_never_changed():
    p = plan_for(VEHICLES + [db_truck("v17", "1ฒถ 2092", "17"), db_truck("vold", "9กก 9", "99", active=False)])
    assert p["db_not_in_sheet"] == ["สระบุรี #17 1ฒถ 2092"]                      # a retired truck is not "missing"
    renumbered = ri.build_plan(ri.parse_master([HEADER, truck_row(1, "สระบุรี", None, None, "7", None, "รถเล็ก", "1ฒถ 8966")]), {},
                               [db_truck("v1", "1ฒถ 8966", "1")], [])
    assert "vehicle_number" not in renumbered["vehicle_updates"][0]["values"] and any("#7" in d for d in renumbered["differences"])
