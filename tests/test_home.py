"""The owner's home page "เช้านี้" (app/home.py): the pure builders, the guarded assembly, the caches and the owner-only endpoints."""
import ast
import inspect
import threading
import time
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from app import home as h
from app import mgr, unit_cost, users

TODAY = date(2026, 11, 21)
NOW = datetime(2026, 11, 21, 7, 42, tzinfo=mgr.THAI_TZ)


def at(hour):
    return datetime(2026, 11, 21, hour, 0, tzinfo=mgr.THAI_TZ)


def veh(vid, number, plate, cls, branch, active=True):
    return {"id": vid, "vehicle_number": number, "plate_number": plate, "vehicle_class": cls, "branch": branch, "active": active}


V = {v["id"]: v for v in (
    veh("v31", "31", "84-4607", "รถใหญ่", "สระบุรี"),
    veh("v9s", "9", "1ฒถ 8975", "รถเล็ก", "สระบุรี"),            # two trucks carry number 9, one in each branch
    veh("v9k", "9", "81-3196", "รถใหญ่", "แก่งคอย"),
    veh("vp", "83-2919", "83-2919", "รถใหญ่", "สระบุรี"),          # its number IS its plate
    veh("v27", "27", "84-2682", "รถใหญ่", "สระบุรี"),
    veh("vold", "70", "70-0001", "รถเล็ก", "สระบุรี", active=False))}


# ------------------------------------------------------------------ how a truck is written
def test_a_truck_is_always_number_plate_class_branch():
    assert h.truck_name(V["v31"]) == "#31 84-4607 รถใหญ่ สระบุรี"


def test_a_truck_whose_number_is_its_plate_is_written_once():
    assert h.truck_name(V["vp"]) == "83-2919 รถใหญ่ สระบุรี"
    assert h.truck_name(veh("x", None, "บพ 1093", "รถเล็ก", "สระบุรี")) == "บพ 1093 รถเล็ก สระบุรี"
    assert h.truck_name(veh("x", "บพ1093", "บพ 1093", "รถเล็ก", "สระบุรี")) == "บพ 1093 รถเล็ก สระบุรี"       # the same but for a space
    assert h.truck_name(veh("x", "83 2919", "83-2919", "รถใหญ่", "สระบุรี")) == "83-2919 รถใหญ่ สระบุรี"       # ... or a hyphen


def test_two_trucks_with_the_same_number_never_read_the_same():
    a, b = h.truck_name(V["v9s"]), h.truck_name(V["v9k"])
    assert a == "#9 1ฒถ 8975 รถเล็ก สระบุรี" and b == "#9 81-3196 รถใหญ่ แก่งคอย" and a != b


def test_a_truck_that_is_not_in_the_register_keeps_what_was_typed():
    assert h.truck_name(None, "77", "99-9999") == "#77 99-9999"
    assert h.truck_name(None) == "รถ (ไม่ระบุคัน)"


def test_a_truck_with_no_plate_on_record_is_never_its_number_alone():
    assert h.truck_name(None, "31", None) == "รถเบอร์ 31 (ไม่พบทะเบียน)"                       # numbers repeat across branches: say the plate is missing
    assert h.truck_name(veh("x", "31", None, "รถใหญ่", "สระบุรี")) == "รถเบอร์ 31 (ไม่พบทะเบียน) รถใหญ่ สระบุรี"
    assert h.route_name({"truck_no": "20", "branch": "สระบุรี", "vehicle_id": None}, V) == "รถสาย 20 สระบุรี (ยังไม่ผูกทะเบียน)"
    assert h.route_name({"truck_no": "31", "branch": "สระบุรี", "vehicle_id": "v31"}, V) == "รถสาย #31 84-4607 รถใหญ่ สระบุรี"


def test_dates_are_day_month_gregorian_year():
    assert h.dmy(date(2026, 11, 5)) == "05/11/2026" and h.dm(date(2026, 11, 5)) == "05/11" and h.month_text("2026-09") == "09/2026"


def test_a_database_timestamp_is_read_in_thai_time():
    assert h._thai_day("2026-11-19T18:30:00+00:00") == date(2026, 11, 20)
    assert h._thai_day("2026-11-19T03:00:00.5+00:00") == date(2026, 11, 19) and h._thai_day(None) is None


# ------------------------------------------------------------------ the repair board
def req(status="open", vid="v31", **kw):
    return {"seq": 1, "vehicle_id": vid, "truck_number_text": None, "plate_text": None, "request_date": "2026-11-21", "created_at": "2026-11-21T03:35:00+00:00",
            "urgent": False, "problem": "", "status": status, "status_changed_at": "2026-11-21T03:35:00+00:00", "shop": None, "sent_date": None,
            "fixed_by": None, "fixed_date": None, **kw}


def rows(*reqs):
    return h.request_rows(list(reqs), V, TODAY)


def test_request_days_are_counted_the_way_the_repair_board_counts_them():
    r = rows(req("open", request_date="2026-11-07"),
             req("at_shop", "v9k", sent_date="2026-10-22", status_changed_at="2026-11-01T02:00:00+00:00"),
             req("awaiting_bill", "v9s", status_changed_at="2026-11-04T18:30:00+00:00", fixed_by="อู่เอ"),          # 18:30 UTC on the 4th is the 5th here
             req("completed"))
    assert [x["status"] for x in r] == ["open", "at_shop", "awaiting_bill"]
    assert (r[0]["days_open"], r[1]["days_at_shop"], r[2]["days_in_status"]) == (14, 30, 16)
    assert r[1]["truck"] == "#9 81-3196 รถใหญ่ แก่งคอย" and r[2]["shop"] == "อู่เอ"


def test_danger_is_an_urgent_request_or_a_dangerous_word_but_not_brake_fluid_on_a_service_list():
    assert h.is_danger({"problem": "เบรกไม่อยู่"}) and h.is_danger({"problem": "ลูกหมากคันชักคันส่งเสีย"}) and h.is_danger({"problem": "ไฟหน้าหมอง", "urgent": True})
    assert not h.is_danger({"problem": "ถึงรอบบำรุง: น้ำมันเกียร์, น้ำมันเบรก / คลัตช์, น้ำมันพวงมาลัย"})
    assert not h.is_danger({"problem": "แอร์ไม่เย็น"})


def test_repair_danger_is_one_line_per_truck_and_only_requests_not_sent_yet():
    r = rows(req(problem="เบรกไม่อยู่"), req(problem="พวงมาลัยหนัก", request_date="2026-11-20"),                  # the same truck twice
             req(vid="v9k", problem="ลูกหมากเสีย", request_date="2026-11-16"),
             req("at_shop", "v9s", problem="เบรกแตก", sent_date="2026-11-20"),                                    # already at a shop
             req(vid="vp", problem="ไฟหน้าหมอง"))                                                                 # not dangerous
    out = h.todo_repair_danger(r, TODAY)
    assert [(i["key"], i["level"], i["who"]) for i in out] == [("repair_danger", "bad", "#9 81-3196 รถใหญ่ แก่งคอย"), ("repair_danger", "bad", "#31 84-4607 รถใหญ่ สระบุรี")]
    assert out[0]["title"] == "ลูกหมากเสีย" and out[0]["detail"] == "แจ้งซ่อม 16/11/2026 (5 วันแล้ว) ยังไม่ได้ส่งอู่"
    assert out[1]["title"] == "พวงมาลัยหนัก" and out[1]["detail"] == "แจ้งซ่อมเมื่อวาน ยังไม่ได้ส่งอู่ · คันนี้แจ้งไว้ 2 เรื่อง"
    assert out[0]["link"] == "#board" and out[0]["link_label"]
    assert h.todo_repair_danger(rows(req(problem="เบรกไม่อยู่")), TODAY)[0]["detail"] == "แจ้งซ่อมวันนี้ ยังไม่ได้ส่งอู่"


def test_repair_danger_shows_three_trucks_the_urgent_first_and_counts_the_rest():
    r = rows(*[req(vid=None, plate_text=f"70-000{n}", problem="เบรกไม่อยู่", request_date=f"2026-11-{10 + n}") for n in range(1, 5)],
             req(vid="v31", problem="สตาร์ทไม่ติด", urgent=True))
    out = h.todo_repair_danger(r, TODAY)
    assert [i["who"] for i in out] == ["#31 84-4607 รถใหญ่ สระบุรี", "70-0001", "70-0002"]
    assert out[-1]["detail"].endswith("และอีก 2 คัน")
    assert h.todo_repair_danger([], TODAY) == []


def test_trucks_at_a_shop_thirty_days_or_more_are_one_line_the_longest_first():
    r = rows(req("at_shop", "v9k", sent_date="2026-10-22"), req("at_shop", "vp", sent_date="2026-09-03"),
             req("at_shop", "v9s", sent_date="2026-10-23"), req("open", "v31", request_date="2026-01-01"))
    out = h.todo_at_shop_long(r)
    assert len(out) == 1 and out[0]["level"] == "bad" and out[0]["title"] == "รถอยู่อู่ 30 วันขึ้นไป 2 คัน"
    assert out[0]["detail"] == "83-2919 รถใหญ่ สระบุรี 79 วัน · #9 81-3196 รถใหญ่ แก่งคอย 30 วัน"          # 29 days is not in it
    many = rows(*[req("at_shop", vid=None, plate_text=f"70-000{n}", sent_date="2026-10-01") for n in range(5)])
    assert h.todo_at_shop_long(many)[0]["detail"].endswith("และอีก 2 คัน")
    assert h.todo_at_shop_long(rows(req("at_shop", sent_date="2026-11-01"))) == []


def test_awaiting_bill_names_the_one_waiting_longest_with_its_shop():
    r = rows(req("awaiting_bill", "v27", status_changed_at="2026-11-05T05:29:00+00:00", shop="ชมรมย์การช่าง"),
             req("awaiting_bill", "v9s", status_changed_at="2026-11-19T06:55:00+00:00", fixed_by="อีซูซุ"))
    out = h.todo_awaiting_bill(r)
    assert out[0]["level"] == "warn" and out[0]["title"] == "ซ่อมแล้ว รอบิล 2 งาน"
    assert out[0]["detail"] == "นานสุด #27 84-2682 รถใหญ่ สระบุรี 16 วัน (ชมรมย์การช่าง)"
    assert h.todo_awaiting_bill(rows(req("open"))) == []


def test_open_long_counts_requests_waiting_two_weeks_or_more():
    r = rows(req(vid="v9k", request_date="2026-10-27", problem="เช็คไฟรอบคัน"), req(request_date="2026-11-07", problem="หัวเก๋งรั่ว"),
             req(vid="vp", request_date="2026-11-08", problem="ไฟหน้าหมอง"), req("at_shop", "v27", request_date="2026-10-01", sent_date="2026-11-20"))
    out = h.todo_open_long(r)
    assert out[0]["title"] == "แจ้งซ่อมรอ 14 วันขึ้นไป 2 งาน"
    assert out[0]["detail"] == "นานสุด #9 81-3196 รถใหญ่ แก่งคอย 25 วัน: เช็คไฟรอบคัน · ยังไม่ได้ส่งอู่"
    assert h.todo_open_long(rows(req())) == []


def test_at_shop_tile_counts_trucks_and_goes_red_at_thirty_days():
    r = rows(req("at_shop", "v9k", sent_date="2026-10-22"), req("at_shop", "v9k", sent_date="2026-11-20"), req("at_shop", "v27", sent_date="2026-11-19"),
             req("open"), req("open", "vp"), req("awaiting_bill", "v9s"))
    t = h.tile_at_shop(r, 68)
    assert (t["key"], t["value"], t["suffix"], t["note"], t["tone"]) == ("at_shop", 2, "จาก 68 คัน", "รอซ่อม 2 · รอบิล 1", "bad")
    assert t["chip"] == {"text": "สด", "tone": "ok"} and t["link"] == "#board" and t["label"] == "รถอยู่ที่อู่"
    assert h.tile_at_shop(rows(req("at_shop", sent_date="2026-11-07")), 68)["tone"] == "warn"
    assert h.tile_at_shop(rows(req("at_shop", sent_date="2026-11-20")), 68)["tone"] == ""
    assert h.tile_at_shop([], 68)["value"] == 0


# ------------------------------------------------------------------ licences
def doc(kind, expires, **kw):
    return {"id": 1, "kind": kind, "title": {"driving": "ใบขับขี่", "certificate": "หนังสือรับรองบริษัท"}.get(kind, kind), "place": None, "employee_id": None,
            "expires_on": expires, "warn_days": None, "submitted_on": None, **kw}


def test_expired_papers_are_listed_one_by_one_a_driving_licence_under_the_persons_name():
    docs = [doc("driving", "2026-11-15", employee_id="e1"), doc("certificate", "2026-10-21", place="สำนักงานใหญ่"),
            doc("driving", "2026-11-01", employee_id="e2", submitted_on="2026-11-10"),          # handed in: waiting for the new one, not red
            doc("driving", "2027-06-01", employee_id="e3"), doc("pp20", None)]
    out = h.todo_licence_expired(h.licence_rows(docs, {"e1": "ศราวุธ โสภา", "e2": "x"}, TODAY))
    assert [(i["title"], i["who"], i["detail"]) for i in out] == [
        ("หนังสือรับรองบริษัท หมดอายุแล้ว 31 วัน", "สำนักงานใหญ่", "หมด 21/10/2026 · ยังไม่ได้ยื่น"),
        ("ใบขับขี่หมดอายุแล้ว 6 วัน", "ศราวุธ โสภา", "หมด 15/11/2026 · ยังไม่ได้ยื่น")]
    assert all(i["level"] == "bad" and i["key"] == "licence_expired" and i["link"] == "#licences" for i in out)


def test_more_than_three_expired_papers_end_with_one_line_for_the_rest():
    docs = [doc("driving", f"2026-11-{d:02d}", employee_id=f"e{d}") for d in range(1, 6)]
    out = h.todo_licence_expired(h.licence_rows(docs, {}, TODAY))
    assert len(out) == 4 and out[0]["title"] == "ใบขับขี่หมดอายุแล้ว 20 วัน" and out[0]["who"] == "ไม่ระบุชื่อ"
    assert out[3]["title"] == "เอกสารหมดอายุแล้วอีก 2 รายการ"


def test_papers_expiring_soon_are_one_line_with_the_nearest():
    docs = [doc("driving", "2026-12-08", employee_id="e1"), doc("hazard", "2026-12-18", title="ใบอนุญาตกิจการ", place="สำนักงานใหญ่"),
            doc("o2", "2026-12-31", title="อ.2", submitted_on="2026-11-01"), doc("driving", "2027-06-01"), doc("driving", "2026-11-15")]
    out = h.todo_licence_soon(h.licence_rows(docs, {"e1": "วินัย ทิพเนตร"}, TODAY))
    assert out[0]["level"] == "warn" and out[0]["title"] == "เอกสารใกล้หมดอายุ 2 รายการ"
    assert out[0]["detail"] == "ใกล้สุด ใบขับขี่ วินัย ทิพเนตร หมด 08/12/2026 (อีก 17 วัน) · ยื่นแล้วรอใบใหม่ 1 รายการ"
    assert h.todo_licence_soon(h.licence_rows([doc("driving", "2027-06-01")], {}, TODAY)) == []


# ------------------------------------------------------------------ engine oil
def oil(state="overdue", **kw):
    return {"key": "engine_oil", "label": "น้ำมันเครื่อง", "state": state, "days_left": -10, "due_date": "2026-11-11", "snoozed_until": None,
            "read_km_left": None, "interval_km": 10000, "cap_months": 6, **kw}


def prow(vid, *items, at_shop=None):
    return {**V[vid], "plate_number": V[vid]["plate_number"], "items": list(items), "counts": {"overdue": 0, "check": 0, "soon": 0, "ok": 1}, "at_shop": at_shop,
            "has_plan": True, "km_first": True, "km_per_day": {"value": 100}, "history_current_on": None}


def test_oil_due_counts_only_engine_oil_overdue_and_names_the_worst_by_real_km():
    plan = [prow("v31", oil(read_km_left=-4250)),
            prow("v9s", oil(read_km_left=-20604, days_left=-96), at_shop={"shop": "อู่เอ"}),
            prow("v9k", oil(days_left=-183)),                                                         # past the time cap, no reading
            prow("vp", oil("check")),                                                                 # only the estimate says so: read the odometer
            prow("v27", oil("ok"), {"key": "air_filter", "label": "กรองอากาศ", "state": "overdue", "days_left": -500, "snoozed_until": None}),
            prow("vold", oil(snoozed_until="2026-12-01"))]
    out = h.todo_oil_due(plan)
    assert len(out) == 1 and out[0]["level"] == "warn" and out[0]["title"] == "ถึงรอบถ่ายน้ำมันเครื่อง 3 คัน"
    assert out[0]["detail"] == "เลยมากสุด #9 1ฒถ 8975 รถเล็ก สระบุรี 20,604 กม. (ตามไมล์ที่อ่านจริง) · 1 คันอยู่ที่อู่แล้ว"
    assert out[0]["link"] == "#plan"


def test_oil_due_by_the_time_cap_alone_says_days():
    out = h.todo_oil_due([prow("v9k", oil(days_left=-183)), prow("v31", oil(days_left=-20))])
    assert out[0]["detail"] == "เลยมากสุด #9 81-3196 รถใหญ่ แก่งคอย 183 วัน (ครบกำหนดเวลา)"
    assert h.todo_oil_due([prow("vp", oil("soon", days_left=3))]) == []


# ------------------------------------------------------------------ the owner's board
def tk(title, due=None, **kw):
    return {"id": title, "seq": kw.pop("seq", 1), "title": title, "urgent": False, "status": "todo", "due_date": due, "recurring_id": None,
            "request_id": None, "request_kind": None, **kw}


def test_my_overdue_names_the_owners_tickets_and_only_counts_the_automatic_follow_ups():
    tickets = [tk("สั่งโอริง", "2026-10-26", seq=34), tk("จ่ายเงิน Intertek", "2026-10-30", seq=44), tk("ตามเงินหัก ประสงค์", urgent=True),
               tk("โทรถามคนขับ #12 · 1ฒบ 6257: รถปกติไหม", "2026-11-01", request_id="r1", request_kind="driver_check"),
               tk("ตามบิล #27 · 84-2682", "2026-11-05", request_id="r2", request_kind="bill_chase"),
               tk("งานพรุ่งนี้", "2026-11-22"), tk("เสร็จแล้ว", "2026-10-01", status="done")]
    out = h.todo_my_overdue(tickets, TODAY)
    assert len(out) == 1 and out[0]["level"] == "warn" and out[0]["title"] == "งานของฉันเลยกำหนด 2 เรื่อง"
    assert out[0]["detail"] == "สั่งโอริง 26 วัน · จ่ายเงิน Intertek 22 วัน · และงานตามรถ/ตามบิลอัตโนมัติอีก 2 เรื่อง · ด่วน: ตามเงินหัก ประสงค์"
    assert out[0]["link"] == "#mgr"


def test_my_overdue_says_nothing_when_only_automatic_tickets_are_late():
    assert h.todo_my_overdue([tk("ตามบิล", "2026-11-05", request_id="r2", request_kind="bill_chase")], TODAY) == []
    only_urgent = h.todo_my_overdue([tk("โทรหาธนาคาร", urgent=True)], TODAY)
    assert only_urgent[0]["title"] == "งานด่วนของฉัน 1 เรื่อง" and only_urgent[0]["detail"] == "ด่วน: โทรหาธนาคาร"
    five = h.todo_my_overdue([tk(f"งาน {n}", "2026-11-01", seq=n) for n in range(5)], TODAY)
    assert five[0]["title"] == "งานของฉันเลยกำหนด 5 เรื่อง" and five[0]["detail"].endswith("และอีก 2 เรื่อง")


# ------------------------------------------------------------------ phone sales sheets
ROUTES = [{"id": 1, "branch": "สระบุรี", "truck_no": "31", "vehicle_id": "v31", "active": True},
          {"id": 2, "branch": "สระบุรี", "truck_no": "20", "vehicle_id": None, "active": True},
          {"id": 3, "branch": "สระบุรี", "truck_no": "44", "vehicle_id": None, "active": False},
          {"id": 31, "branch": "แก่งคอย", "truck_no": "9", "vehicle_id": "v9k", "active": True},          # แก่งคอย's route: one view of both branches
          {"id": 32, "branch": "ทดสอบ", "truck_no": "ทดสอบ", "vehicle_id": None, "active": True}]


def sd(route, day, status="open", amount=0.0, asked=None):
    return {"route_id": route, "day": day, "status": status, "amount": amount, "reopen_requested_at": asked}


SHEETS = [sd(1, "2026-11-10", "closed", 4877, asked="2026-11-10T08:13:00+00:00"),
          sd(32, "2026-11-19", "closed", 579, asked="2026-11-19T06:19:00+00:00"),          # the test route asks too: never shown
          sd(2, "2026-11-20", "open", 4000), sd(1, "2026-11-20", "open", 0),               # an empty sheet is not chased
          sd(1, "2026-11-21", "closed", 5000), sd(2, "2026-11-21", "open", 300), sd(32, "2026-11-21", "closed", 999)]


NO_PLATE = "รถสาย 20 สระบุรี (ยังไม่ผูกทะเบียน)"


def test_sales_sheets_lists_the_waiting_reopen_request_and_yesterdays_open_sheets():
    out = h.todo_sales_sheets(SHEETS, ROUTES, V, NOW)
    assert len(out) == 1 and out[0]["level"] == "warn" and out[0]["title"] == "ใบขายรถสาย: คำขอแก้ใบขายรอ 11 วัน"
    assert out[0]["detail"] == f"รถสาย #31 84-4607 รถใหญ่ สระบุรี ขอแก้ใบวันที่ 10/11/2026 · เมื่อวานยังเปิดอยู่ 1 สาย (ส่งได้ถึงเที่ยง): {NO_PLATE}"
    assert out[0]["link"] == "#sales-sheets" and "ทดสอบ" not in out[0]["detail"]


def test_todays_unsent_sheets_count_only_from_six_in_the_evening():
    assert "วันนี้" not in h.todo_sales_sheets(SHEETS, ROUTES, V, at(17))[0]["detail"]
    assert h.todo_sales_sheets(SHEETS, ROUTES, V, at(18))[0]["detail"].endswith(f"วันนี้ยังไม่ส่ง 1 สาย: {NO_PLATE}")
    today_only = [sd(2, "2026-11-21", "open", 300)]
    assert h.todo_sales_sheets(today_only, ROUTES, V, at(9)) == []
    late = h.todo_sales_sheets(today_only, ROUTES, V, at(20))
    assert late[0]["title"] == "ใบขายรถสาย: วันนี้ยังไม่ส่ง 1 สาย" and late[0]["detail"] == NO_PLATE


def test_yesterdays_sheet_is_unsent_from_noon_as_on_the_sheets_page():
    one = [sd(2, "2026-11-20", "open", 4000)]
    morning = h.todo_sales_sheets(one, ROUTES, V, at(9))                   # the driver can still send it until noon
    assert morning[0]["title"] == "ใบขายรถสาย: เมื่อวานยังเปิดอยู่ 1 สาย (ส่งได้ถึงเที่ยง)" and morning[0]["detail"] == NO_PLATE
    after = h.todo_sales_sheets(one, ROUTES, V, at(13))
    assert after[0]["title"] == "ใบขายรถสาย: ยังไม่ส่ง 1 ใบ (เก่าสุด 20/11)" and after[0]["detail"] == NO_PLATE


def test_unsent_sheets_are_chased_two_weeks_back_not_only_yesterday():
    old = [sd(2, "2026-11-10", "open", 4415), sd(2, "2026-11-19", "open", 3560), sd(1, "2026-11-19", "open", 100),
           sd(1, "2026-11-18", "open", 0),                                 # nothing sold on it: not chased
           sd(2, "2026-11-06", "open", 999),                               # older than the sheets page looks back
           sd(31, "2026-11-12", "closed", 500)]
    out = h.todo_sales_sheets(old, ROUTES, V, at(8))
    assert out[0]["title"] == "ใบขายรถสาย: ยังไม่ส่ง 3 ใบ (เก่าสุด 10/11)"
    assert out[0]["detail"] == f"{NO_PLATE} 2 ใบ, รถสาย #31 84-4607 รถใหญ่ สระบุรี"
    both = h.todo_sales_sheets(old + [sd(2, "2026-11-20", "open", 50)], ROUTES, V, at(8))
    assert both[0]["detail"].endswith(f"เมื่อวานยังเปิดอยู่ 1 สาย (ส่งได้ถึงเที่ยง): {NO_PLATE}")
    assert h.SHEET_DAYS == 14


def test_the_test_route_is_in_no_sales_sheet_number():
    only_test = [sd(32, "2026-11-20", "open", 500), sd(32, "2026-11-21", "closed", 999, asked="2026-11-21T01:00:00+00:00")]
    assert h.todo_sales_sheets(only_test, ROUTES, V, at(20)) == []
    assert h.tile_phone_sales(only_test, ROUTES, TODAY)["value"] == 0
    assert 32 not in h.real_routes(ROUTES) and 32 not in h.real_routes([{"id": 32, "branch": "สระบุรี"}])          # by id, and by branch
    assert 40 not in h.real_routes([{"id": 40, "branch": "ทดสอบ"}])


def test_phone_sales_tile_adds_todays_sheets_and_says_how_many_routes_have_one():
    t = h.tile_phone_sales(SHEETS, ROUTES, TODAY)
    assert (t["key"], t["value"], t["unit"]) == ("phone_sales", 5300, "฿")
    assert t["chip"] == {"text": "2 จาก 3 สาย", "tone": "plain"} and t["note"] == "ส่งแล้ว 1 · ยังเปิดอยู่ 1" and t["link"] == "#sales-sheets"
    kk = h.tile_phone_sales(SHEETS + [sd(31, "2026-11-21", "closed", 100)], ROUTES, TODAY)              # every active route of both branches is the "of"
    assert kk["chip"]["text"] == "3 จาก 3 สาย" and kk["value"] == 5400


# ------------------------------------------------------------------ machines
M5 = {"id": "m5", "name": "เครื่อง 5", "kind": "เครื่อง", "number": 5, "branch": "สระบุรี", "plan": {"hours_now": {"hours": 100.0}, "visit": None, "jobs": [
    {"key": "tower", "name": "ล้างคูลลิ่งทาวเวอร์", "state": "late", "days_left": -43, "hours": 4000.0, "due_date": "2026-10-09", "with_oil": False},
    {"key": "overhaul", "name": "โอเวอร์ฮอลคอมเพรสเซอร์", "state": "never", "days_left": None, "hours": 24000.0, "due_date": None},
    {"key": "belt", "name": "ตรวจสายพาน", "state": "ok", "days_left": 30, "hours": None, "due_date": "2026-12-21"}]}}
POND = {"id": "p1", "name": "บ่อซอง", "kind": "บ่อซอง", "number": None, "make": "MYCOM", "capacity_tons": 30, "branch": "สระบุรี", "plan": {"hours_now": None, "jobs": [
    {"key": "oil", "name": "ถ่ายน้ำมันคอมเพรสเซอร์", "state": "late", "days_left": -115, "hours": 4000.0, "due_date": "2026-07-29"},
    {"key": "grease", "name": "อัดจารบี", "state": "late", "days_left": -3, "hours": None, "due_date": "2026-11-18"}],
    "visit": {"date": "2026-11-25", "days_left": 4, "together": ["grease"], "maybe": []}}}


def test_machines_lists_the_two_latest_jobs_and_marks_assumed_hours():
    out = h.todo_machines([M5, POND])
    assert len(out) == 1 and out[0]["level"] == "warn" and out[0]["title"] == "เครื่องจักรเลยรอบ 3 งาน"           # "never overhauled" is not "late"
    assert out[0]["detail"] == ("ถ่ายน้ำมันคอมเพรสเซอร์ เครื่องบ่อซอง MYCOM 30 ตัน สระบุรี เลย 115 วัน (ชั่วโมงประมาณ) · "
                                "ล้างคูลลิ่งทาวเวอร์ เครื่อง 5 สระบุรี เลย 43 วัน · และอีก 1 งาน")
    assert out[0]["link"] == "#machine-plan"
    assert h.todo_machines([{**M5, "plan": {"jobs": [M5["plan"]["jobs"][2]]}}, {"id": "x", "plan": None}]) == []


# ------------------------------------------------------------------ feeds that stopped
def test_stale_data_names_the_days_of_sales_not_typed_and_the_meters_that_stopped():
    out = h.todo_stale_data(date(2026, 11, 15), {"สระบุรี": date(2026, 11, 9), "แก่งคอย": date(2026, 11, 2)}, TODAY)
    assert [(i["key"], i["level"], i["link"]) for i in out] == [("stale_data", "warn", "#driver-days"), ("stale_data", "warn", "#electricity")]
    assert out[0]["title"] == "ยอดขายรายวันยังไม่ได้ลง 16/11-20/11" and out[0]["detail"] == "ลงไว้ถึง 15/11/2026 · ค้าง 5 วัน"
    assert out[1]["title"] == "มิเตอร์ไฟ ไม่มีข้อมูลใหม่ 19 วัน" and out[1]["detail"] == "แก่งคอย ถึง 02/11/2026 · สระบุรี ถึง 09/11/2026"
    part = h.todo_stale_data(date(2026, 11, 15), {}, TODAY, date(2026, 11, 20))                 # a newer day typed for some routes only
    assert part[0]["title"] == "ยอดขายรายวันยังไม่ได้ลง 16/11-20/11" and part[0]["detail"] == "ลงไว้ถึง 15/11/2026 · ค้าง 5 วัน · 20/11 ลงแล้วบางสาย"


def test_stale_data_is_quiet_while_the_feeds_are_within_their_slack():
    assert h.todo_stale_data(date(2026, 11, 17), {"สระบุรี": date(2026, 11, 11), "แก่งคอย": None}, TODAY) == []          # 3 days behind yesterday; 10 days old
    assert len(h.todo_stale_data(date(2026, 11, 16), {"สระบุรี": date(2026, 11, 10)}, TODAY)) == 2
    assert h.todo_stale_data(None, {}, TODAY) == []


# ------------------------------------------------------------------ the next 7 days
RULES = [{"id": "r1", "title": "สรุปค่าใช้จ่าย", "rule": {"unit": "month", "every": 1, "month_days": [26]}, "mode": "calendar", "next_due": "2026-11-26",
          "start_date": None, "end_date": None, "active": True, "time_of_day": None, "place": None, "notes": None, "type_key": None},
         {"id": "r2", "title": "สรุปบางจาก", "rule": {"unit": "month", "every": 1, "month_days": [26]}, "mode": "calendar", "next_due": "2026-11-26",
          "start_date": None, "end_date": None, "active": True, "time_of_day": None, "place": None, "notes": None, "type_key": None},
         {"id": "r3", "title": "จ่ายค่าโทรศัพท์", "rule": {"unit": "month", "every": 1, "month_days": [6]}, "mode": "calendar", "next_due": "2026-12-06",
          "start_date": None, "end_date": None, "active": True, "time_of_day": None, "place": None, "notes": None, "type_key": None}]


def test_next7_is_one_info_line_with_days_in_order():
    tickets = [tk("เช็คใบแจ้งซ่อม", "2026-11-23"), tk("ไกลไป", "2026-11-29"), tk("เลยแล้ว", "2026-11-20"),
               tk("ตามบิล", "2026-11-22", request_id="r", request_kind="bill_chase")]
    plan = [prow("v31", oil("soon", days_left=3, due_date="2026-11-24")), prow("v27", oil("soon", days_left=3, due_date="2026-11-24")),
            prow("v9k", oil("soon", days_left=5, due_date="2026-11-26")), prow("v9s", oil("check", days_left=0, due_date="2026-11-21")),
            prow("vp", oil("soon", days_left=2, due_date="2026-11-23"), at_shop={"shop": "อู่เอ"})]
    out = h.todo_next7(tickets, RULES, {("r2", "2026-11-26")}, plan, [POND], TODAY)
    assert len(out) == 1 and (out[0]["key"], out[0]["level"], out[0]["title"], out[0]["link"]) == ("next7", "info", "7 วันข้างหน้า", "#mgr-calendar")
    assert out[0]["detail"] == ("23/11 เช็คใบแจ้งซ่อม · 24/11 ถ่ายน้ำมันเครื่อง 2 คัน · 25/11 ถ่ายน้ำมันคอมเพรสเซอร์ เครื่องบ่อซอง MYCOM 30 ตัน สระบุรี · "
                                "26/11 สรุปค่าใช้จ่าย, ถ่ายน้ำมันเครื่อง #9 81-3196 รถใหญ่ แก่งคอย")


def test_next7_never_shows_a_routine_job_whose_date_has_passed():
    waiting = {**RULES[0], "id": "r9", "title": "เช็คน้ำมันเกียร์", "mode": "after_done", "next_due": "2026-11-10"}      # its last ticket is still open
    assert h.todo_next7([], [waiting], set(), [], [], TODAY) == []
    coming = {**waiting, "next_due": "2026-11-24"}
    assert h.todo_next7([], [waiting, coming], set(), [], [], TODAY)[0]["detail"] == "24/11 เช็คน้ำมันเกียร์"


def test_next7_shows_six_and_counts_the_rest():
    out = h.todo_next7([tk(f"งาน {n}", "2026-11-22", seq=n) for n in range(8)], [], set(), [], [], TODAY)
    assert out[0]["detail"] == "22/11 งาน 0, งาน 1, งาน 2, งาน 3, งาน 4, งาน 5 · และอีก 2 รายการ"
    assert h.todo_next7([], [], set(), [], [], TODAY) == []


# ------------------------------------------------------------------ the order of the list
def test_todo_is_bad_first_then_warn_then_info_keeping_the_given_order_inside_a_level():
    items = [h.item("oil_due", "warn", "a"), h.item("next7", "info", "b"), h.item("repair_danger", "bad", "c"), h.item("my_overdue", "warn", "d"),
             h.item("licence_expired", "bad", "e")]
    out = h.build_todo(items)
    assert [i["key"] for i in out["items"]] == ["repair_danger", "licence_expired", "oil_due", "my_overdue", "next7"]
    assert (out["bad"], out["warn"]) == (2, 2)                                                 # info is not counted
    assert set(out["items"][0]) == {"key", "level", "title", "who", "detail", "link", "link_label"}
    assert h.build_todo([]) == {"bad": 0, "warn": 0, "items": []}


# ------------------------------------------------------------------ tiles
def rd(day, who, money, spare=False):
    return {"day": day, "employee_id": who, "money": money, "spare": spare}


ROUTE_DAYS = [rd("2026-11-08", "a", 60000), rd("2026-11-08", "b", 40000), rd("2026-11-14", "a", 1000),
              rd("2026-11-15", "a", 70000), rd("2026-11-15", "b", 49000),
              rd("2026-11-15", "c", 9999, spare=True),                                        # the spare drove: no commission, but the ice was sold
              rd("2026-11-15", "d", None)]                                                    # no figure: not a day driven


def test_route_sales_tile_is_the_newest_typed_day_against_the_same_weekday_a_week_before():
    t = h.tile_route_sales(ROUTE_DAYS, TODAY)
    assert (t["key"], t["label"], t["value"], t["unit"]) == ("route_sales", "ขายรถสาย ทุกสาย", 128999, "฿")
    assert t["chip"] == {"text": "ถึง 15/11", "tone": "warn"}                                   # six days behind
    assert t["delta"] == {"text": "+29%", "tone": "good"} and t["note"] == "จาก 08/11/2026 · 3 สาย · สำรองขับ 1" and t["tone"] == ""
    assert t["spark"] == [100000, 1000, 128999] and t["link"] == "#driver-days"
    assert set(t) == {"key", "label", "value", "unit", "suffix", "chip", "delta", "note", "tone", "spark", "link"}


def test_the_spare_drivers_money_is_sales_and_one_switch_takes_it_out(monkeypatch):
    assert h.route_day_totals(ROUTE_DAYS)[date(2026, 11, 15)] == {"money": 128999.0, "routes": {"a", "b", "c"}, "spare": 1}
    monkeypatch.setattr(h, "COUNT_SPARE_SALES", False)                                        # the pay sheet's figure
    t = h.tile_route_sales(ROUTE_DAYS, TODAY)
    assert t["value"] == 119000 and t["delta"]["text"] == "+19%" and t["note"] == "จาก 08/11/2026 · 3 สาย · สำรองขับ 1"


def many(day, n, money=1000):
    return [rd(day, f"e{i}", money) for i in range(n)]


def test_a_partly_typed_newest_day_is_not_shown_as_the_whole_days_sales():
    full = many("2026-11-08", 30) + many("2026-11-13", 30) + many("2026-11-14", 30) + many("2026-11-15", 29, 2000)
    t = h.tile_route_sales(full + many("2026-11-20", 1, 4200), TODAY)                         # one driver typed the 20th so far
    assert t["value"] == 58000 and t["chip"] == {"text": "ถึง 15/11", "tone": "warn"} and t["delta"]["text"] == "+93%"
    assert t["note"] == "จาก 08/11/2026 · 29 จาก 30 สาย · 20/11 ลงแล้ว 1 สาย ยังไม่นับ" and t["tone"] == "warn"
    assert t["spark"] == [30000, 30000, 30000, 58000]
    assert h.sales_reach(full + many("2026-11-20", 1)) == (date(2026, 11, 15), date(2026, 11, 20))
    assert h.sales_reach(full + many("2026-11-16", 22)) == (date(2026, 11, 15), date(2026, 11, 16))      # 22 of 30 typed: not yet
    assert h.sales_reach(full + many("2026-11-16", 27)) == (date(2026, 11, 16), None)                    # 27 of 30: the day counts
    assert h.sales_reach([]) == (None, None)
    half = h.tile_route_sales(many("2026-11-08", 30) + many("2026-11-15", 10), TODAY)          # the week-before day is only compared with when it is whole
    assert half["chip"]["text"] == "ถึง 08/11" and half["delta"] is None
    daily = h.build_sales_daily(full + many("2026-11-20", 1, 4200), TODAY)
    assert daily["through"] == "2026-11-15" and daily["days"][-1] == "2026-11-15" and daily["values"][-1] == 58000


def test_route_sales_chip_tone_follows_how_far_behind_the_typing_is():
    assert h.tile_route_sales([rd("2026-11-20", "a", 5)], TODAY)["chip"] == {"text": "ถึง 20/11", "tone": "ok"}
    assert h.tile_route_sales([rd("2026-11-19", "a", 5)], TODAY)["chip"]["tone"] == "warn"
    assert h.tile_route_sales([rd("2026-11-13", "a", 5)], TODAY)["chip"]["tone"] == "bad"
    alone = h.tile_route_sales([rd("2026-11-20", "a", 5)], TODAY)
    assert alone["delta"] is None and alone["note"] == "1 สาย"                                 # nothing a week before to compare with
    assert h.tile_route_sales([], TODAY) == {"key": "route_sales", "label": "ขายรถสาย ทุกสาย", "ready": False}
    assert len(h.tile_route_sales([rd(f"2026-10-{d:02d}", "a", d) for d in range(1, 31)], TODAY)["spark"]) == 21


def inv(day, total, kind="invoice"):
    return {"doc_date": day, "kind": kind, "total": total}


def test_kk_invoice_tile_compares_the_month_so_far_with_the_same_days_last_month():
    rows_ = [inv("2026-10-05", 1000), inv("2026-10-20", 1000), inv("2026-10-25", 5000),       # the 25th is past the same-days range
             inv("2026-11-02", 1500), inv("2026-11-20", 700), inv("2026-11-20", 100, "credit_note"),
             inv(None, 9999), inv("2026-12-01", 9999)]                                        # undated / dated ahead: not counted
    t = h.tile_kk_invoice(rows_, TODAY)
    assert (t["key"], t["value"], t["unit"]) == ("kk_invoice", 600, "฿")                        # the credit note is taken off
    assert t["chip"] == {"text": "ถึง 20/11", "tone": "ok"} and t["delta"] is None
    assert t["note"] == "01-20/11 2,100 · +5% จาก 01-20/10"                                     # 2,100 against 2,000 (01-20/10), not against 7,000
    assert len(t["spark"]) == 20 and t["spark"][1] == 1500 and t["spark"][-1] == 600 and t["spark"][0] == 0
    assert h.tile_kk_invoice([inv("2026-11-20", 700)], TODAY)["note"] == "01-20/11 700"
    assert h.tile_kk_invoice([], TODAY) == {"key": "kk_invoice", "label": "แก่งคอย ออกใบกำกับ", "ready": False}


def ev(day, cost):
    return {"event_id": day, "event_date": day, "total_cost": cost}


def test_repair_spend_is_compared_like_for_like_not_with_the_whole_month():
    events = [ev("2026-11-01", 100), ev("2026-11-21", 50), ev("2026-11-19", None), ev("2026-11-22", 999),      # tomorrow: not yet
              ev("2026-10-21", 200), ev("2026-10-22", 5000), ev("2026-09-30", 7000)]                           # 22/10 is past 01-21/10
    t = h.tile_repair_mtd(events, TODAY)
    assert (t["key"], t["label"], t["value"], t["unit"]) == ("repair_mtd", "ค่าซ่อมรถ 01-21/11", 150, "฿")
    assert t["delta"] == {"text": "−25%", "tone": "good"} and t["note"] == "จาก 01-21/10 (200)"                 # lower spend is good
    assert t["chip"] == {"text": "ถึง 21/11", "tone": "ok"} and t["link"] == "#dash"
    up = h.tile_repair_mtd([ev("2026-11-05", 300), ev("2026-10-05", 200), ev("2026-10-20", 9000)], TODAY)
    assert up["label"] == "ค่าซ่อมรถ 01-05/11" and up["note"] == "จาก 01-05/10 (200)"                             # as far as the bills reach
    assert up["delta"] == {"text": "+50%", "tone": "bad"} and up["chip"]["tone"] == "bad"                       # newest bill 16 days old


def test_repair_spend_is_compared_up_to_the_day_the_bills_reach_not_up_to_today():
    def daily(month, days):
        return [ev(f"2026-{month:02d}-{d:02d}", 20000) for d in days]
    half = h.tile_repair_mtd(daily(10, range(1, 32)) + daily(11, range(1, 15)), TODAY)             # the same spend a day, bills typed to the 14th
    assert (half["label"], half["value"], half["note"]) == ("ค่าซ่อมรถ 01-14/11", 280000, "จาก 01-14/10 (280,000)")
    assert half["delta"] == {"text": "0%", "tone": "flat"} and half["chip"] == {"text": "ถึง 14/11", "tone": "warn"}
    first = h.tile_repair_mtd(daily(11, range(1, 31)), date(2026, 12, 1))                          # the 1st: no bill yet is not "-100%"
    assert (first["label"], first["value"], first["delta"], first["note"]) == ("ค่าซ่อมรถ 12/2026", 0, None, "เดือนนี้ยังไม่มีบิล")
    assert first["chip"] == {"text": "ถึง 30/11", "tone": "ok"}
    empty = h.tile_repair_mtd([], TODAY)
    assert empty["value"] == 0 and empty["delta"] is None and empty["chip"] is None and empty["note"] == "เดือนนี้ยังไม่มีบิล"
    assert h.tile_repair_mtd([], TODAY, date(2026, 9, 28))["chip"] == {"text": "ถึง 28/09", "tone": "bad"}      # every number carries the date it reaches


def test_the_same_days_of_the_month_before():
    assert h.same_days_before(date(2026, 11, 21)) == (date(2026, 10, 1), date(2026, 10, 21))
    assert h.same_days_before(date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28))       # a shorter month ends on its last day
    assert h.same_days_before(date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 15))
    assert h.prev_month_start(date(2026, 1, 15)) == date(2025, 12, 1)


def test_percent_changes_read_like_the_page_shows_them():
    assert h.delta(119, 100) == {"text": "+19%", "tone": "good"} and h.delta(105.4, 100)["text"] == "+5.4%"
    assert h.delta(69, 100) == {"text": "−31%", "tone": "bad"} and h.delta(69, 100, lower_is_good=True)["tone"] == "good"
    assert h.delta(100.2, 100) == {"text": "+0.2%", "tone": "flat"} and h.delta(100, 100)["text"] == "0%"
    assert h.delta(5, 0) is None and h.delta(5, None) is None


def test_payables_tile_warns_when_a_bill_is_over_sixty_days():
    t = h.tile_payables({"owed": 253958.89, "shops": 4, "bills": 34, "over60": 109331.0})
    assert (t["key"], t["value"], t["note"], t["tone"], t["link"]) == ("payables", 253959, "4 ร้าน · เกิน 60 วัน 109,331", "warn", "#payables")
    calm = h.tile_payables({"owed": 1000, "shops": 1, "over60": 0})
    assert calm["tone"] == "" and calm["note"] == "1 ร้าน · ไม่มีบิลเกิน 60 วัน" and calm["chip"] == {"text": "สด", "tone": "ok"}


# ------------------------------------------------------------------ the books
def acc(code, kind, section):
    return {"code": code, "name_en": code, "name_th": code, "kind": kind, "section": section}


ACC = {a["code"]: a for a in (acc("4100", "income", "revenue"), acc("5100", "cogs", "cogs"), acc("5300", "cogs", "cogs"), acc("6120", "expense", "selling"))}
MONTHS = ["2026-08", "2026-09", "2026-10", "2026-11"]


def ln(account, branch, day, debit=0.0, credit=0.0):
    return {"account": account, "branch": branch, "date": day, "debit": debit, "credit": credit}


LINES = [ln("4100", "สระบุรี", "2026-08-10", credit=1000), ln("5100", "สระบุรี", "2026-08-10", 300), ln("5300", "สระบุรี", "2026-08-10", 100),
         ln("4100", "แก่งคอย", "2026-08-10", credit=500), ln("5100", "แก่งคอย", "2026-08-10", 700), ln("5300", "แก่งคอย", "2026-08-10", 100),
         ln("4100", "สระบุรี", "2026-09-10", credit=1000), ln("5100", "สระบุรี", "2026-09-10", 200), ln("5300", "สระบุรี", "2026-09-10", 120),
         ln("4100", "แก่งคอย", "2026-09-10", credit=400), ln("5100", "แก่งคอย", "2026-09-10", 600), ln("5300", "แก่งคอย", "2026-09-10", 80),
         ln("4100", "สระบุรี", "2026-10-10", credit=900), ln("5100", "สระบุรี", "2026-10-10", 200), ln("5300", "สระบุรี", "2026-10-10", 5),      # the cheque run is not booked
         ln("4100", "แก่งคอย", "2026-10-10", credit=300), ln("5100", "แก่งคอย", "2026-10-10", 500),
         ln("6120", "สระบุรี", "2026-11-10", 50)]                                                                                            # costs only: no sales yet


def test_the_incomplete_month_rule_is_the_unit_cost_pages_rule():
    assert unit_cost.incomplete_flags([200.0, 200.0, 5.0]) == [False, False, True]              # under 30% of the other months' average
    assert unit_cost.incomplete_flags([200.0, 200.0, 70.0]) == [False, False, False]
    assert unit_cost.incomplete_flags([200.0]) == [False] and unit_cost.incomplete_flags([]) == []
    assert {"5300", "5330", "5200"} <= unit_cost.CHEQUE_ACCOUNTS and "5100" not in unit_cost.CHEQUE_ACCOUNTS


def test_books_give_profit_per_branch_for_months_with_sales_and_flag_the_incomplete_one():
    books = h.build_books(LINES, ACC, MONTHS, "2026-11")
    assert books["months"] == ["2026-08", "2026-09", "2026-10"]                                 # November has costs but no sales: left out
    assert books["total"] == [300, 400, 495]
    assert books["branches"] == [{"name": "สระบุรี", "values": [600, 680, 695]}, {"name": "แก่งคอย", "values": [-300, -280, -200]}]
    assert books["incomplete"] == [2]


def test_profit_is_the_last_complete_month_and_says_which_later_month_is_not_counted():
    out = h.build_profit(h.build_books(LINES, ACC, MONTHS, "2026-11"))
    assert out == {"ready": True, "month": "2026-09", "total": 400, "branches": [{"name": "สระบุรี", "value": 680}, {"name": "แก่งคอย", "value": -280}],
                   "note": "10/2026 ยังไม่ครบ จึงยังไม่นับ", "link": "#accounts"}


def test_the_month_still_running_is_never_complete():
    more = LINES + [ln("5300", "สระบุรี", "2026-10-11", 200), ln("4100", "สระบุรี", "2026-11-10", credit=800), ln("5300", "สระบุรี", "2026-11-10", 200)]
    books = h.build_books(more, ACC, MONTHS, "2026-11")
    assert books["months"][-1] == "2026-11" and books["incomplete"] == [3]                      # its supplies are booked, but the month is not over
    assert h.build_profit(books)["month"] == "2026-10" and h.build_profit(books)["note"] == "11/2026 ยังไม่ครบ จึงยังไม่นับ"
    assert h.build_profit({"months": ["2026-11"], "total": [1], "branches": [], "incomplete": [0]}) == {"ready": False}
    assert h.build_profit(h.build_books(LINES[:12], ACC, MONTHS, "2026-11"))["note"] == ""


def test_company_months_run_from_the_26th():
    assert h.company_months(date(2026, 11, 21), 3) == ["2026-09", "2026-10", "2026-11"]
    assert h.company_months(date(2026, 11, 26), 2) == ["2026-11", "2026-12"] and h.company_months(date(2026, 12, 26), 1) == ["2027-01"]


def test_trends_are_every_calendar_day_to_the_newest_typed_one_and_the_profit_by_branch():
    daily = h.build_sales_daily(ROUTE_DAYS, TODAY)
    assert daily == {"ready": True, "days": [f"2026-11-{d:02d}" for d in range(8, 16)], "values": [100000, None, None, None, None, None, 1000, 128999],
                     "through": "2026-11-15", "through_tone": "warn", "note": "2 สายต่อวัน สระบุรี"}          # a day nobody typed is a gap, not squeezed out
    assert h.build_sales_daily([rd("2026-11-20", "a", 5)], TODAY)["through_tone"] == "ok"            # the tile chip's limits: a day behind is normal
    assert h.build_sales_daily([rd("2026-11-19", "a", 5)], TODAY)["through_tone"] == "warn"
    assert len(h.build_sales_daily([rd(f"2026-10-{d:02d}", "a", d) for d in range(1, 32)] + [rd(f"2026-11-{d:02d}", "a", d) for d in range(1, 11)], TODAY)["days"]) == 35
    assert h.build_sales_daily([], TODAY) == {"ready": False}
    books = h.build_books(LINES, ACC, MONTHS, "2026-11")
    assert h.build_profit_branch(books) == {"ready": True, "months": ["2026-08", "2026-09", "2026-10"], "series": books["branches"], "incomplete": [2]}
    assert h.build_trends({"route_days": ROUTE_DAYS, "books": books}, TODAY) == {"sales_daily": daily, "profit_branch": h.build_profit_branch(books), "failed": []}
    assert h.build_trends({"route_days": h._FAILED, "books": h._FAILED}, TODAY) == {
        "sales_daily": {"ready": False}, "profit_branch": {"ready": False}, "failed": ["sales_daily", "profit_branch"]}


# ------------------------------------------------------------------ how far each feed reaches
def test_fresh_gives_every_feed_the_date_it_reaches_and_a_tone():
    out = h.build_fresh({"tax": date(2026, 11, 20), "repairs": date(2026, 11, 19), "route_sales": date(2026, 11, 15), "meters": date(2026, 11, 2),
                         "sales_program": date(2026, 11, 3), "books": "2026-09", "elec_bills": "2026-10", "payroll": None}, TODAY)
    by = {x["label"]: (x["value"], x["tone"]) for x in out}
    assert by["สถานะงานซ่อม"] == by["บอร์ดของฉัน"] == by["ใบอนุญาต"] == ("สด", "ok")
    assert by["ใบกำกับภาษีแก่งคอย"] == ("20/11", "ok") and by["บิลซ่อมรถ"] == ("19/11", "ok") and by["ยอดขายรถสายรายวัน"] == ("15/11", "warn")
    assert by["มิเตอร์ไฟ"] == ("02/11", "bad") and by["โปรแกรมขายหน้าโรง"] == ("03/11", "warn")
    assert by["ปิดบัญชีถึง"] == ("09/2026", "warn") and by["ใบแจ้งค่าไฟ"] == ("10/2026", "ok") and "เงินเดือน" not in by      # an unknown feed is left out
    assert all(set(x) == {"label", "value", "tone"} for x in out)
    assert h.build_fresh({"tax": date(2025, 12, 30)}, TODAY)[-1] == {"label": "ใบกำกับภาษีแก่งคอย", "value": "30/12/2025", "tone": "bad"}
    assert h._oldest([date(2026, 11, 9), None, date(2026, 11, 2)]) == date(2026, 11, 2) and h._oldest([None]) is None


# ------------------------------------------------------------------ the whole answer
def sources(**over):
    books = h.build_books(LINES, ACC, MONTHS, "2026-11")
    src = {
        "requests": [req(problem="เบรกไม่อยู่"), req("at_shop", "vp", sent_date="2026-09-03"),
                     req("awaiting_bill", "v27", status_changed_at="2026-11-05T05:29:00+00:00", shop="ชมรมย์การช่าง"),
                     req(vid="v9k", request_date="2026-10-27", problem="เช็คไฟรอบคัน")],
        "vehicles": {k: v for k, v in V.items()},
        "tickets": [tk("สั่งโอริง", "2026-10-26"), tk("เช็คใบแจ้งซ่อม", "2026-11-23")],
        "routine": {"rules": RULES, "made": set()},
        "sheets": {"days": SHEETS, "routes": ROUTES},
        "route_days": ROUTE_DAYS,
        "tax": [inv("2026-11-20", 700), inv("2026-10-20", 1000)],
        "repair_bills": {"rows": [ev("2026-11-19", 100), ev("2026-10-21", 200)], "newest": date(2026, 11, 19)},
        "licences": {"docs": [doc("driving", "2026-11-15", employee_id="e1"), doc("driving", "2026-12-08", employee_id="e2")], "names": {"e1": "ก", "e2": "ข"}},
        "plan": [prow("v31", oil(read_km_left=-4250))],
        "machines": [M5],
        "payables": {"owed": 253958.89, "shops": 4, "over60": 109331.0},
        "feeds": {"meters_sbr": date(2026, 11, 9), "meters_kk": date(2026, 11, 2), "program_sbr": date(2026, 11, 7), "program_kk": date(2026, 11, 3),
                  "elec_bills": "2026-10", "payroll": "2026-10", "failed": []},
        "books": books}
    return {**src, **over}


def test_the_home_answer_has_the_contracts_shape_and_the_todo_order():
    out = h.build_home(sources(), TODAY, NOW)
    assert list(out) == ["as_of", "time", "todo", "tiles", "profit", "fresh", "failed"]
    assert out["as_of"] == "2026-11-21" and out["time"] == "07:42" and out["failed"] == []
    assert [i["key"] for i in out["todo"]["items"]] == [
        "repair_danger", "licence_expired", "at_shop_long",                                                     # bad
        "oil_due", "my_overdue", "licence_soon", "awaiting_bill", "open_long", "sales_sheets", "machines", "stale_data", "stale_data",      # warn
        "next7"]                                                                                                # info
    assert (out["todo"]["bad"], out["todo"]["warn"]) == (3, 9)
    assert [i["level"] for i in out["todo"]["items"]] == ["bad"] * 3 + ["warn"] * 9 + ["info"]
    assert [t["key"] for t in out["tiles"]] == ["route_sales", "kk_invoice", "phone_sales", "at_shop", "repair_mtd", "payables"]
    assert out["tiles"][3]["suffix"] == "จาก 5 คัน"                                               # the active fleet; the retired truck is not in it
    assert out["profit"]["month"] == "2026-09" and out["profit"]["total"] == 400
    fresh = {x["label"]: x["value"] for x in out["fresh"]}
    assert fresh["มิเตอร์ไฟ"] == "02/11" and fresh["โปรแกรมขายหน้าโรง"] == "03/11"          # one view: as far as the branch furthest behind
    assert fresh["ปิดบัญชีถึง"] == "09/2026" and fresh["บิลซ่อมรถ"] == "19/11" and fresh["ยอดขายรถสายรายวัน"] == "15/11" and fresh["เงินเดือน"] == "10/2026"


def test_one_part_failing_is_named_and_the_rest_of_the_page_still_shows():
    feeds = {**sources()["feeds"], "payroll": None, "failed": ["payroll"]}
    out = h.build_home(sources(payables=h._FAILED, machines=h._FAILED, books=h._FAILED, feeds=feeds), TODAY, NOW)
    assert out["failed"] == ["todo.machines", "todo.next7", "tiles.payables", "profit", "fresh.payroll"]
    assert out["tiles"][5] == {"key": "payables", "label": "ค้างจ่ายร้านค้า", "ready": False} and out["tiles"][0]["value"] == 128999
    assert out["profit"] == {"ready": False}
    assert "machines" not in [i["key"] for i in out["todo"]["items"]] and out["todo"]["bad"] == 3
    assert "เงินเดือน" not in [x["label"] for x in out["fresh"]] and "ปิดบัญชีถึง" not in [x["label"] for x in out["fresh"]]


def test_a_builder_that_raises_is_named_too(monkeypatch):
    monkeypatch.setattr(h, "todo_oil_due", lambda rows_: 1 / 0)
    out = h.build_home(sources(), TODAY, NOW)
    assert out["failed"] == ["todo.oil_due"] and "oil_due" not in [i["key"] for i in out["todo"]["items"]] and len(out["tiles"]) == 6


def test_with_every_source_down_the_page_is_still_an_answer():
    out = h.build_home({name: h._FAILED for name in h.HOME_SOURCES}, TODAY, NOW)
    assert out["todo"] == {"bad": 0, "warn": 0, "items": []} and out["profit"] == {"ready": False}
    assert [t.get("ready") for t in out["tiles"]] == [False] * 6
    assert [x["value"] for x in out["fresh"]] == ["สด", "สด", "สด"]
    assert {"todo.repair_danger", "todo.licence_expired", "todo.oil_due", "todo.sales_sheets", "tiles.route_sales", "tiles.at_shop", "profit",
            "fresh.feeds"} <= set(out["failed"])


# ------------------------------------------------------------------ gathering and the caches
def test_a_source_that_raises_does_not_take_the_page_down(monkeypatch):
    def boom(today, fresh):
        raise RuntimeError("db down")
    monkeypatch.setattr(h, "HOME_SOURCES", {"route_days": lambda today, fresh: ROUTE_DAYS, "tax": boom})
    h.forget()
    out = h.load(fresh=True)
    assert out["tiles"][0]["value"] == 128999 and out["tiles"][1] == {"key": "kk_invoice", "label": "แก่งคอย ออกใบกำกับ", "ready": False}
    assert "tiles.kk_invoice" in out["failed"] and out["as_of"] and len(out["time"]) == 5


def test_the_page_answers_from_a_short_cache_and_fresh_reads_again(monkeypatch):
    calls = []
    monkeypatch.setattr(h, "HOME_SOURCES", {"route_days": lambda today, fresh: calls.append(fresh) or ROUTE_DAYS})
    monkeypatch.setattr(h, "TREND_SOURCES", {"route_days": lambda today, fresh: calls.append(("trends", fresh)) or ROUTE_DAYS})
    h.forget()
    assert h.load() is h.load() and calls == [False]
    h.load(fresh=True)
    assert calls == [False, True]
    assert h.trends() is h.trends() and h.trends()["sales_daily"]["through"] == "2026-11-15"
    h.trends(fresh=True)
    assert calls[2:] == [("trends", False), ("trends", True)]
    assert h.HOME_SECONDS <= 60 and h.TRENDS_SECONDS <= h.HOME_SECONDS and h.BOOKS_SECONDS >= 600      # the chart never lags the tile beside it


def test_the_slow_sources_keep_their_own_cache_and_fresh_goes_past_it(monkeypatch):
    calls = []
    for probe in ("has_v10", "has_v13"):
        monkeypatch.setattr(h.service_plan, probe, lambda: True)
    monkeypatch.setattr(h.mgr, "has_v12", lambda: True)
    monkeypatch.setattr(h.fleet, "has_v32", lambda: True)
    monkeypatch.setattr(h.service_plan, "overview", lambda today=None: calls.append("plan") or {"vehicles": [prow("v31", oil())]})
    monkeypatch.setattr(h.payables, "load", lambda today: calls.append("payables") or {"tiles": {"owed": 5}})
    monkeypatch.setattr(h.shops, "refresh", lambda: calls.append("shops.refresh"))
    h.forget()
    assert h._src_plan(TODAY, False) == h._src_plan(TODAY, False) and calls == ["plan"]
    h._src_plan(TODAY, True)
    assert calls == ["plan", "plan"]
    assert h._src_payables(TODAY, False) == {"owed": 5} and h._src_payables(TODAY, False) == {"owed": 5} and calls[2:] == ["payables"]
    h._src_payables(TODAY, True)
    assert calls[3:] == ["shops.refresh", "payables"]                       # the shop names' own 30-second copy is dropped too
    h.forget()


def test_an_error_is_never_kept_in_the_cache():
    h.forget()
    try:
        h._cached("x", 100, lambda: 1 / 0)
    except ZeroDivisionError:
        pass
    assert h._cached("x", 100, lambda: 7) == 7 and h._cached("x", 100, lambda: 8) == 7 and h._cached("x", 100, lambda: 9, fresh=True) == 9
    h.forget()


def test_an_answer_with_a_failed_part_is_not_kept(monkeypatch):
    """One bad read must not stay on the page: the next look reads again (a moment later), a good answer is kept as usual."""
    books = h.build_books(LINES, ACC, MONTHS, "2026-11")
    asked = []

    def flaky(today, fresh):
        asked.append(fresh)
        if len(asked) == 1:
            raise RuntimeError("db blip")
        return books
    monkeypatch.setattr(h, "TREND_SOURCES", {"route_days": lambda today, fresh: ROUTE_DAYS, "books": flaky})
    monkeypatch.setattr(h, "RETRY_SECONDS", 0)
    h.forget()
    first = h.trends()
    assert first["profit_branch"] == {"ready": False} and first["failed"] == ["profit_branch"] and first["sales_daily"]["ready"]
    second = h.trends()
    assert second["profit_branch"]["ready"] and second["failed"] == [] and asked == [False, False]
    assert h.trends() is second and asked == [False, False]                                   # the good answer is kept
    h.forget()
    calls = []
    assert h._cached("z", 100, lambda: calls.append(1) or {"failed": ["x"]}, keep=lambda v: not v["failed"]) == {"failed": ["x"]}
    assert h._cached("z", 100, lambda: calls.append(1) or {"failed": []}, keep=lambda v: not v["failed"]) == {"failed": []} and len(calls) == 2
    monkeypatch.setattr(h, "RETRY_SECONDS", 5)
    h.forget()
    h._cached("z", 100, lambda: calls.append(1) or {"failed": ["x"]}, keep=lambda v: not v["failed"])
    h._cached("z", 100, lambda: calls.append(1) or {"failed": []}, keep=lambda v: not v["failed"])
    assert len(calls) == 3                                                                    # ... but not hammered: kept a few seconds
    h.forget()


def test_a_refresh_that_waited_behind_an_ordinary_read_still_reads_again():
    h.forget()
    go, calls, got = threading.Event(), [], {}

    def slow():
        calls.append(1)
        n = len(calls)
        go.wait(2)
        return n
    plain = threading.Thread(target=lambda: got.__setitem__("plain", h._cached("w", 100, slow)))
    plain.start()
    time.sleep(0.1)                                                       # the ordinary read is under way
    fresh = threading.Thread(target=lambda: got.__setitem__("fresh", h._cached("w", 100, slow, fresh=True)))
    fresh.start()
    time.sleep(0.1)                                                       # the refresh waits its turn behind it
    go.set()
    plain.join(3)
    fresh.join(3)
    assert got == {"plain": 1, "fresh": 2} and h._cached("w", 100, slow) == 2
    h.forget()


def test_two_refreshes_at_once_read_once():
    h.forget()
    go, calls, got = threading.Event(), [], []

    def slow():
        calls.append(1)
        go.wait(2)
        return len(calls)
    threads = [threading.Thread(target=lambda: got.append(h._cached("y", 100, slow, fresh=True))) for _ in range(2)]
    for th in threads:
        th.start()
    time.sleep(0.1)                                                       # both have asked; one is reading, the other waits its turn
    go.set()
    for th in threads:
        th.join(2)
    assert calls == [1] and got == [1, 1]
    h.forget()


# ------------------------------------------------------------------ what the page must never do
def _calls(module) -> set:
    out = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            out.add(f"{node.func.value.id}.{node.func.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Attribute):
            out.add(f"{getattr(node.func.value.value, 'id', '?')}.{node.func.value.attr}.{node.func.attr}")
    return out


def test_the_home_page_only_reads():
    calls = _calls(h)
    assert "accounting.report" not in calls and "mgr.board" not in calls               # they rebuild entries / make recurring tickets
    assert not {c for c in calls if c.startswith("supabase.") and c.split(".")[1] in ("insert", "update", "delete", "rpc", "storage_upload", "storage_delete")}
    assert "date.today" not in calls                                                   # Thai time only
    assert "accounting.year_rows" in calls and "unit_cost.incomplete_flags" in calls and "service_plan.priority" in calls


def test_the_old_cards_are_gone():
    assert not hasattr(h, "HOME_SOON") and not hasattr(h, "build_stock") and not hasattr(h, "build_machinery")


# ------------------------------------------------------------------ the endpoints
def test_both_home_endpoints_are_the_owners_alone(monkeypatch):
    """Owner, 21 Nov 2026: nobody else sees the home page -- neither the page's data nor its charts, even with every menu ticked."""
    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    asked = []
    monkeypatch.setattr(main.home, "load", lambda fresh=False: asked.append(("home", fresh)) or {"as_of": "2026-11-21"})
    monkeypatch.setattr(main.home, "trends", lambda fresh=False: asked.append(("trends", fresh)) or {"sales_daily": {"ready": False}})
    everything = list(users.MENUS) + ["home"]
    monkeypatch.setattr(users, "session_user", lambda t: {"id": 1, "name": "บัญชี", "username": "acc1", "menus": everything} if t == "u_all"
                        else {"id": 2, "name": "เจ้าของ", "username": "boss", "menus": [], "role": "owner"} if t == "u_boss" else None)
    c = TestClient(main.app)
    for path in ("/api/home", "/api/home/trends"):
        assert not users.allowed(users.clean_menus(everything), path)
        assert c.get(path, headers={"X-App-Token": "u_all"}).status_code == 403            # a staff login with every menu
        assert c.get(f"{path}?token=u_all").status_code == 403
        assert c.get(path, headers={"X-App-Token": "u_nobody"}).status_code == 403         # an unknown login
        assert c.get(path).status_code == 403                                              # no login at all
        assert c.get(path, headers={"X-App-Token": "u_boss"}).status_code == 200
        assert c.get(path, headers={"X-App-Token": "secret"}).status_code == 200
    assert asked == [("home", False), ("home", False), ("trends", False), ("trends", False)]           # the refused calls never reached the data
    assert c.get("/api/home?fresh=true", headers={"X-App-Token": "secret"}).json() == {"as_of": "2026-11-21"}
    assert c.get("/api/home/trends?fresh=true", headers={"X-App-Token": "secret"}).json() == {"sales_daily": {"ready": False}}
    assert asked[-2:] == [("home", True), ("trends", True)]
