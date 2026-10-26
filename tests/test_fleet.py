import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import fleet, history

D1 = "11111111-1111-1111-1111-111111111111"
D2 = "22222222-2222-2222-2222-222222222222"
V1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
V2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def truck(vid, plate, number, branch="สระบุรี", *, province="", active=True, **more):
    return {"id": vid, "plate_number": plate, "plate_province": province, "vehicle_number": number, "branch": branch, "active": active, **more}


def drivers(*rows):
    return {r["id"]: r for r in rows}


DRIVERS = drivers({"id": D1, "name": "สมชาย", "active": True}, {"id": D2, "name": "ลุงหมี", "active": False})


def make(data, *, existing=None, others=(), busy=False):
    return fleet.clean_vehicle(data, existing=existing, others=list(others), drivers=DRIVERS, busy_vehicle=busy)


# ------------------------------------------------------------- vehicle rules
def test_a_new_truck_is_cleaned_and_needs_plate_and_branch():
    v = make({"plate_number": "  1กข   4267 ", "branch": "สระบุรี", "vehicle_number": " 36 ", "usage": "รถสาย", "make": "", "notes": "  "})
    assert v["plate_number"] == "1กข 4267" and v["vehicle_number"] == "36" and v["usage"] == "รถสาย"
    assert v["make"] is None and v["notes"] is None and v["main_driver_id"] is None and v["plate_province"] == ""
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({"plate_number": "", "branch": "สระบุรี"})
    assert "ทะเบียน" in str(exc.value)
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({"plate_number": "1กข 1", "branch": ""})
    assert "สาขา" in str(exc.value)


def test_a_duplicate_plate_is_refused_whatever_the_spacing():
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({"plate_number": "1กข4267", "branch": "สระบุรี"}, others=[truck(V1, "1กข 4267", "5")])
    assert "มีอยู่ในระบบแล้ว" in str(exc.value)
    # the same number in another province is another truck
    make({"plate_number": "1กข 4267", "plate_province": "นนทบุรี", "branch": "สระบุรี"}, others=[truck(V1, "1กข 4267", "5", province="กรุงเทพมหานคร")])


def test_a_fleet_number_is_unique_within_a_branch_only():
    others = [truck(V1, "A 1", "6", "สระบุรี"), truck(V2, "B 2", "7", "แก่งคอย")]
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({"plate_number": "C 3", "branch": "สระบุรี", "vehicle_number": "6"}, others=others)
    assert "เบอร์ 6" in str(exc.value)
    make({"plate_number": "C 3", "branch": "แก่งคอย", "vehicle_number": "6"}, others=others)       # truck 6 exists in both branches
    make({"plate_number": "C 3", "branch": "สระบุรี", "vehicle_number": "6"}, others=[truck(V1, "A 1", "6", active=False)])   # a retired truck frees its number


def test_editing_something_else_never_trips_over_an_old_duplicate():
    existing = truck(V1, "A 1", "6")
    others = [truck(V2, "A 1 ", "6")]                    # a pre-existing clash the data already had
    assert make({"usage": "รถหน้าโรง"}, existing=existing, others=others) == {"usage": "รถหน้าโรง"}
    with pytest.raises(fleet.InvalidFleet):
        make({"vehicle_number": "6", "branch": "สระบุรี"}, existing=existing, others=others)    # but touching the number checks it


def test_a_partial_edit_only_returns_what_was_sent():
    assert make({"usage": " รถสาย "}, existing=truck(V1, "A 1", "6")) == {"usage": "รถสาย"}
    assert make({"main_driver_id": ""}, existing=truck(V1, "A 1", "6")) == {"main_driver_id": None}


def test_main_driver_must_exist_and_still_be_driving():
    assert make({"main_driver_id": D1}, existing=truck(V1, "A 1", "6")) == {"main_driver_id": D1}
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({"main_driver_id": D2}, existing=truck(V1, "A 1", "6"))
    assert "ลุงหมี" in str(exc.value)
    with pytest.raises(fleet.InvalidFleet):
        make({"main_driver_id": "33333333-3333-3333-3333-333333333333"}, existing=truck(V1, "A 1", "6"))
    with pytest.raises(fleet.InvalidFleet):
        make({"main_driver_id": "not-a-uuid"}, existing=truck(V1, "A 1", "6"))


def test_a_truck_with_work_in_progress_cannot_be_retired_but_can_be_brought_back():
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({"active": False}, existing=truck(V1, "A 1", "6"), busy=True)
    assert "งานซ่อมค้าง" in str(exc.value)
    assert make({"active": False}, existing=truck(V1, "A 1", "6")) == {"active": False}
    assert make({"active": True}, existing=truck(V1, "A 1", "6", active=False), busy=True) == {"active": True}


def test_bringing_a_retired_truck_back_checks_its_number_is_still_free():
    retired = truck(V1, "A 1", "6", active=False)
    with pytest.raises(fleet.InvalidFleet):
        make({"active": True}, existing=retired, others=[truck(V2, "B 2", "6")])


def test_usage_choices_lists_presets_then_whatever_is_in_use():
    assert fleet.usage_choices([{"usage": "รถสาย"}, {"usage": "รถส่งน้ำแข็งก้อน"}, {"usage": ""}, {}]) == ["รถสาย", "รถหน้าโรง", "รถส่งน้ำแข็งก้อน"]


# -------------------------------------------------------------- driver rules
def test_a_new_driver_needs_a_name_and_no_other_active_driver_has_it():
    d = fleet.clean_driver({"name": "  สมศักดิ์  ", "phone": " 081 ", "branch": ""}, others=[])
    assert d == {"name": "สมศักดิ์", "phone": "081", "branch": None, "notes": None}
    with pytest.raises(fleet.InvalidFleet):
        fleet.clean_driver({"name": " "}, others=[])
    with pytest.raises(fleet.InvalidFleet) as exc:
        fleet.clean_driver({"name": "สมชาย"}, others=[{"name": " สมชาย", "active": True}])
    assert "มีคนขับชื่อ" in str(exc.value)
    fleet.clean_driver({"name": "สมชาย"}, others=[{"name": "สมชาย", "active": False}])      # a driver who left doesn't block the name


def test_editing_a_driver_only_changes_what_was_sent():
    assert fleet.clean_driver({"phone": "089"}, existing={"name": "ก", "active": True}, others=[]) == {"phone": "089"}
    assert fleet.clean_driver({"active": False}, existing={"name": "ก", "active": True}, others=[]) == {"active": False}


# ---------------------------------------------------------- database wrappers
class Db:
    def __init__(self, v6=True, v7=True, v8=True):
        self.v6 = v6
        self.v7 = v7
        self.v8 = v8
        self.tables = {
            "vehicles": [truck(V1, "1กข 1", "1", usage=None, main_driver_id=None, notes=None), truck(V2, "2กข 2", "2", usage="รถสาย", main_driver_id=D1, notes=None)],
            "drivers": [{"id": D1, "name": "สมชาย", "active": True, "phone": None, "branch": None, "notes": None}],
            "repair_requests": [],
            "employees": [],
        }
        self.inserted, self.updates = [], []

    def select(self, table, params=None):
        params = params or {}
        if table == "drivers" and not self.v6:
            raise fleet.supabase.SupabaseError("no table")
        if table == "vehicles" and "usage" in params.get("select", "") and not self.v6:
            raise fleet.supabase.SupabaseError("no column")
        sel = params.get("select", "")
        if ("vehicle_class" in sel or (table == "drivers" and "nickname" in sel)) and not self.v7:
            raise fleet.supabase.SupabaseError("no v7 column")
        if table == "vehicles" and "nickname" in sel and not self.v8:
            raise fleet.supabase.SupabaseError("no v8 column")
        rows = self.tables[table]
        if table == "repair_requests" and "status" in params:
            return [dict(r) for r in rows if r["status"] in params["status"]]
        return [dict(r) for r in rows if all(r.get(k) == v[3:] for k, v in params.items() if isinstance(v, str) and v.startswith("eq.") and k in r)]

    def select_all(self, table, params=None):
        return self.select(table, params)

    def insert(self, table, rows, **kw):
        self.inserted.append((table, rows))
        row = {**rows[0], "id": "new-id"}
        self.tables[table].append(row)
        return [row]

    def update(self, table, match, values):
        self.updates.append((table, match, values))
        k, v = next(iter(match.items()))
        hit = [r for r in self.tables[table] if str(r.get(k)) == v[3:]]
        for r in hit:
            r.update(values)
        return [dict(r) for r in hit]


@pytest.fixture
def db(monkeypatch):
    d = Db()
    for name in ("select", "select_all", "insert", "update"):
        monkeypatch.setattr(fleet.supabase, name, getattr(d, name))
    monkeypatch.setitem(fleet._probe, "at", 0.0)
    monkeypatch.setitem(fleet._probe7, "at", 0.0)
    monkeypatch.setitem(fleet._probe8, "at", 0.0)
    monkeypatch.setattr(fleet.roster, "invalidate_roster_cache", lambda: None)
    return d


def test_create_vehicle_writes_the_cleaned_values(db):
    out = fleet.create_vehicle({"plate_number": "3กข 3", "branch": "แก่งคอย", "vehicle_number": "9", "usage": "รถหน้าโรง", "main_driver_id": D1})
    assert out == {"vehicle_id": "new-id"}
    assert db.inserted[0][1][0]["usage"] == "รถหน้าโรง" and db.inserted[0][1][0]["main_driver_id"] == D1


def test_edit_vehicle_updates_only_the_sent_fields(db):
    fleet.edit_vehicle(V1, {"usage": "รถสาย", "main_driver_id": D1})
    assert db.updates[-1] == ("vehicles", {"id": f"eq.{V1}"}, {"usage": "รถสาย", "main_driver_id": D1})


def test_edit_unknown_vehicle_is_not_found(db):
    with pytest.raises(fleet.NotFound):
        fleet.edit_vehicle("cccccccc-cccc-cccc-cccc-cccccccccccc", {"usage": "x"})
    with pytest.raises(fleet.InvalidFleet):
        fleet.edit_vehicle("garbage", {"usage": "x"})


def test_retiring_a_truck_with_open_work_is_refused(db):
    db.tables["repair_requests"].append({"vehicle_id": V1, "status": "at_shop"})
    with pytest.raises(fleet.InvalidFleet) as exc:
        fleet.edit_vehicle(V1, {"active": False})
    assert "งานซ่อมค้าง" in str(exc.value)
    fleet.edit_vehicle(V2, {"active": False})                       # another truck, nothing pending: fine
    assert db.tables["vehicles"][1]["active"] is False


def test_retiring_a_driver_frees_their_trucks(db):
    out = fleet.edit_driver(D1, {"active": False})
    assert out == {"ok": True, "freed_trucks": 1}
    assert db.tables["vehicles"][1]["main_driver_id"] is None and db.tables["drivers"][0]["active"] is False


def test_driver_list_shows_each_drivers_trucks(db):
    rows = fleet.list_drivers()
    assert rows[0]["name"] == "สมชาย" and rows[0]["trucks"][0]["plate"] == "2กข 2"


def test_everything_is_refused_with_a_clear_message_before_schema_v6(monkeypatch):
    d = Db(v6=False)
    for name in ("select", "select_all", "insert", "update"):
        monkeypatch.setattr(fleet.supabase, name, getattr(d, name))
    monkeypatch.setitem(fleet._probe, "at", 0.0)
    for call in (lambda: fleet.create_vehicle({"plate_number": "x", "branch": "สระบุรี"}), lambda: fleet.edit_vehicle(V1, {"usage": "x"}),
                 lambda: fleet.create_driver({"name": "x"}), lambda: fleet.edit_driver(D1, {"name": "y"})):
        with pytest.raises(fleet.InvalidFleet) as exc:
            call()
        assert "schema_v6.sql" in str(exc.value)
    assert not d.inserted and not d.updates
    assert fleet.drivers_by_id() == {}                  # and reads degrade to "no drivers" instead of failing


# ----------------------------------------------------------------- the list
def test_vehicle_rows_carry_usage_driver_and_active_flag():
    vehicles = [{"id": V1, "plate_number": "A", "vehicle_number": "1", "usage": "รถสาย", "main_driver_id": D1, "active": True},
                {"id": V2, "plate_number": "B", "vehicle_number": "2", "active": False}]
    rows = {r["plate"]: r for r in history.vehicle_rows(vehicles, {}, [], drivers=DRIVERS)}
    assert (rows["A"]["usage"], rows["A"]["driver"], rows["A"]["driver_id"], rows["A"]["active"]) == ("รถสาย", "สมชาย", D1, True)
    assert (rows["B"]["usage"], rows["B"]["driver"], rows["B"]["active"]) == ("", "", False)


# ----------------------------------------------------------------- endpoints
def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    return main, TestClient(main.app)


def test_fleet_endpoints_need_the_token_and_map_errors(monkeypatch):
    main, c = _client(monkeypatch)
    for method, url in (("post", "/api/vehicles"), ("patch", f"/api/vehicles/{V1}"), ("get", "/api/drivers"), ("post", "/api/drivers"), ("patch", f"/api/drivers/{D1}")):
        kw = {} if method == "get" else {"json": {}}
        assert getattr(c, method)(url, **kw).status_code == 403
    monkeypatch.setattr(main.fleet, "create_vehicle", lambda d: (_ for _ in ()).throw(fleet.InvalidFleet("ทะเบียนซ้ำ")))
    r = c.post("/api/vehicles?token=secret", json={"plate_number": "x"})
    assert r.status_code == 422 and "ทะเบียนซ้ำ" in r.text
    # a driver is added / edited under พนักงาน now: the old write endpoints say so
    for r in (c.patch(f"/api/drivers/{D1}?token=secret", json={"name": "x"}), c.post("/api/drivers?token=secret", json={"name": "x"})):
        assert r.status_code == 410 and "พนักงาน" in r.text


def test_vehicle_endpoints_pass_the_body_through(monkeypatch):
    main, c = _client(monkeypatch)
    seen = {}
    monkeypatch.setattr(main.fleet, "edit_vehicle", lambda vid, d: seen.update(id=vid, **d) or {"ok": True})
    c.patch(f"/api/vehicles/{V1}?token=secret", json={"usage": "รถสาย", "main_driver_id": D1})
    assert seen == {"id": V1, "usage": "รถสาย", "main_driver_id": D1}


def test_vehicle_list_can_include_retired_trucks(monkeypatch):
    main, c = _client(monkeypatch)
    seen = []
    monkeypatch.setattr(main.history, "list_vehicles", lambda include_inactive=False: seen.append(include_inactive) or [])
    c.get("/api/vehicles?token=secret")
    c.get("/api/vehicles?token=secret&include_inactive=true")
    assert seen == [False, True]


def test_meta_offers_usages_and_branches(monkeypatch):
    main, c = _client(monkeypatch)
    monkeypatch.setattr(main.supabase, "configured", lambda: False)
    monkeypatch.setattr(main, "load_roster", lambda: [{"usage": "รถส่งก้อน"}])
    d = c.get("/api/meta?token=secret").json()
    assert d["usages"] == ["รถสาย", "รถหน้าโรง", "รถส่งก้อน"] and d["branches"] == ["สระบุรี", "แก่งคอย"]


# ------------------------------------------------- schema_v7: the master-sheet columns
def test_size_class_must_be_one_of_the_two_known_classes():
    assert make({"vehicle_class": "รถเล็ก"}, existing=truck(V1, "A 1", "6")) == {"vehicle_class": "รถเล็ก"}
    assert make({"vehicle_class": ""}, existing=truck(V1, "A 1", "6")) == {"vehicle_class": None}
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({"vehicle_class": "รถกลาง"}, existing=truck(V1, "A 1", "6"))
    assert "รถเล็ก" in str(exc.value)


def test_registration_date_is_validated_and_a_buddhist_year_gets_a_specific_message():
    assert make({"registered_date": "2016-02-11"}, existing=truck(V1, "A 1", "6")) == {"registered_date": "2016-02-11"}
    assert make({"registered_date": ""}, existing=truck(V1, "A 1", "6")) == {"registered_date": None}
    for bad, fragment in (("11/02/2016", "ไม่ถูกต้อง"), ("2559-02-11", "พ.ศ."), ("2999-01-01", "พ.ศ.")):
        with pytest.raises(fleet.InvalidFleet) as exc:
            make({"registered_date": bad}, existing=truck(V1, "A 1", "6"))
        assert fragment in str(exc.value)


def test_registration_text_fields_are_trimmed_and_blank_becomes_none():
    v = make({"color": "  ขาว-เขียว ", "engine_number": "", "owner_name": "บจ.ก", "holder_name": " "}, existing=truck(V1, "A 1", "6"))
    assert v == {"color": "ขาว-เขียว", "engine_number": None, "owner_name": "บจ.ก", "holder_name": None}


def test_a_driver_gets_a_nickname():
    assert fleet.clean_driver({"nickname": " ชาย "}, existing={"name": "ก", "active": True}, others=[]) == {"nickname": "ชาย"}
    assert fleet.clean_driver({"name": "สมชาย ใจดี", "nickname": ""}, others=[])["nickname"] is None


def test_v7_fields_are_written_once_the_migration_is_in(db):
    fleet.edit_vehicle(V1, {"vehicle_class": "รถใหญ่", "color": "ขาว", "registered_date": "2016-02-11"})
    assert db.updates[-1][2] == {"vehicle_class": "รถใหญ่", "color": "ขาว", "registered_date": "2016-02-11"}
    fleet.edit_driver(D1, {"nickname": "ชาย"})
    assert db.updates[-1][2] == {"nickname": "ชาย"}


def test_v7_fields_are_refused_with_a_clear_message_before_schema_v7_but_the_rest_still_works(monkeypatch):
    d = Db(v6=True, v7=False)
    for name in ("select", "select_all", "insert", "update"):
        monkeypatch.setattr(fleet.supabase, name, getattr(d, name))
    monkeypatch.setitem(fleet._probe, "at", 0.0)
    monkeypatch.setitem(fleet._probe7, "at", 0.0)
    monkeypatch.setitem(fleet._probe8, "at", 0.0)
    monkeypatch.setattr(fleet.roster, "invalidate_roster_cache", lambda: None)
    with pytest.raises(fleet.InvalidFleet) as exc:
        fleet.edit_vehicle(V1, {"color": "ขาว"})
    assert "schema_v7.sql" in str(exc.value)
    with pytest.raises(fleet.InvalidFleet) as exc:
        fleet.edit_driver(D1, {"nickname": "ชาย"})
    assert "schema_v7.sql" in str(exc.value)
    fleet.edit_vehicle(V1, {"usage": "รถสาย"})                       # the v6 features are unaffected
    assert d.tables["vehicles"][0]["usage"] == "รถสาย"
    assert fleet.has_v7() is False


def test_vehicle_rows_carry_the_registration_details_and_the_drivers_nickname_and_phone():
    vehicles = [{"id": V1, "plate_number": "A", "vehicle_number": "1", "main_driver_id": D1, "vehicle_class": "รถเล็ก", "color": "ขาว",
                 "registered_date": "2016-02-11", "owner_name": "บจ.ก"}]
    drv = {D1: {"id": D1, "name": "สมชาย", "nickname": "ชาย", "phone": "0959354443", "active": True}}
    r = history.vehicle_rows(vehicles, {}, [], drivers=drv)[0]
    assert (r["vehicle_class"], r["color"], r["registered_date"], r["owner_name"]) == ("รถเล็ก", "ขาว", "2016-02-11", "บจ.ก")
    assert (r["driver_nickname"], r["driver_phone"]) == ("ชาย", "0959354443") and r["engine_number"] == ""


def test_meta_reports_whether_schema_v7_is_in_and_the_size_classes(monkeypatch):
    main, c = _client(monkeypatch)
    monkeypatch.setattr(main.supabase, "configured", lambda: False)
    monkeypatch.setattr(main, "load_roster", lambda: [])
    d = c.get("/api/meta?token=secret").json()
    assert d["fleet_v7"] is False and d["vehicle_classes"] == ["รถเล็ก", "รถใหญ่"]


def test_the_same_fleet_number_may_exist_once_per_size_class_in_a_branch():
    big = truck(V1, "86-3139", "2", "แก่งคอย", vehicle_class="รถใหญ่")
    small = {"plate_number": "1ฒถ 2087", "branch": "แก่งคอย", "vehicle_number": "2", "vehicle_class": "รถเล็ก"}
    assert make(small, others=[big])["vehicle_number"] == "2"                       # another size: fine
    with pytest.raises(fleet.InvalidFleet) as exc:
        make({**small, "vehicle_class": "รถใหญ่"}, others=[big])                    # the same size: still a clash
    assert "เบอร์ 2" in str(exc.value)
    with pytest.raises(fleet.InvalidFleet):
        make({k: v for k, v in small.items() if k != "vehicle_class"}, others=[big])    # size not filled in: assumed the same, so the message asks for it


def test_moving_a_truck_to_another_branch_checks_the_number_there_per_size_class():
    big = truck(V1, "86-3139", "2", "แก่งคอย", vehicle_class="รถใหญ่")
    mine = truck(V2, "1ฒถ 2087", "2", "สระบุรี", vehicle_class="รถเล็ก")
    assert make({"branch": "แก่งคอย"}, existing=mine, others=[big]) == {"branch": "แก่งคอย"}
    with pytest.raises(fleet.InvalidFleet):
        make({"branch": "แก่งคอย"}, existing={**mine, "vehicle_class": None}, others=[big])
    with pytest.raises(fleet.InvalidFleet):
        make({"vehicle_class": "รถใหญ่"}, existing={**mine, "branch": "แก่งคอย"}, others=[big])      # changing the size into a clash is caught too


# ------------------------------------------------ schema_v8: the truck's own nickname
def test_a_truck_nickname_is_trimmed_and_blank_becomes_none():
    assert make({"nickname": "  หัวขาว "}, existing=truck(V1, "A 1", "6")) == {"nickname": "หัวขาว"}
    assert make({"nickname": " "}, existing=truck(V1, "A 1", "6")) == {"nickname": None}


def test_the_truck_nickname_is_saved_once_schema_v8_is_in(db):
    fleet.edit_vehicle(V1, {"nickname": "หัวน้ำเงิน"})
    assert db.updates[-1][2] == {"nickname": "หัวน้ำเงิน"}


def test_the_truck_nickname_is_refused_before_schema_v8_but_everything_else_works(monkeypatch):
    d = Db(v8=False)
    for name in ("select", "select_all", "insert", "update"):
        monkeypatch.setattr(fleet.supabase, name, getattr(d, name))
    monkeypatch.setitem(fleet._probe, "at", 0.0)
    monkeypatch.setitem(fleet._probe7, "at", 0.0)
    monkeypatch.setitem(fleet._probe8, "at", 0.0)
    monkeypatch.setattr(fleet.roster, "invalidate_roster_cache", lambda: None)
    with pytest.raises(fleet.InvalidFleet) as exc:
        fleet.edit_vehicle(V1, {"nickname": "หัวขาว"})
    assert "schema_v8.sql" in str(exc.value)
    fleet.edit_vehicle(V1, {"color": "ขาว"})                                # the v7 columns still work
    assert d.tables["vehicles"][0]["color"] == "ขาว" and fleet.has_v8() is False


def test_vehicle_rows_carry_the_truck_nickname():
    r = history.vehicle_rows([{"id": V1, "plate_number": "บฉ 6161", "vehicle_number": "บฉ 6161", "nickname": "หัวขาว"}], {}, [])[0]
    assert r["nickname"] == "หัวขาว"


def test_meta_reports_whether_schema_v8_is_in(monkeypatch):
    main, c = _client(monkeypatch)
    monkeypatch.setattr(main.supabase, "configured", lambda: False)
    monkeypatch.setattr(main, "load_roster", lambda: [])
    assert c.get("/api/meta?token=secret").json()["fleet_v8"] is False
