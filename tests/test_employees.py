from datetime import date

import pytest

from app import employees as em
from app.config import settings

TODAY = date(2026, 10, 26)


@pytest.fixture
def conf():
    """Change the (frozen) settings for one test, then put them back."""
    saved = {}

    def setter(**kw):
        for k, v in kw.items():
            saved.setdefault(k, getattr(settings, k))
            object.__setattr__(settings, k, v)
    yield setter
    for k, v in saved.items():
        object.__setattr__(settings, k, v)
OTHERS = [{"id": "1", "code": "SB-FR001", "full_name": "นายก"}, {"id": "2", "code": "SB-PD033", "full_name": "นายข"}]


def make(**kw):
    return {"code": "sb-fr200", "full_name": "  นายสมชาย   ใจดี ", "branch": "สระบุรี", "position": "ผลิต", **kw}


def test_a_new_employee_is_cleaned():
    v = em.clean_employee(make(phone=" 081 234 5678 ", start_date="2026-09-01", notes="", nickname=""), others=OTHERS, today=TODAY)
    assert v["code"] == "SB-FR200" and v["full_name"] == "สมชาย ใจดี" and v["prefix"] == "นาย" and v["gender"] == "ชาย"      # the title moved out of the name
    assert v["branch"] == "สระบุรี" and v["position"] == "ผลิต"
    assert v["phone"] == "081 234 5678" and v["start_date"] == "2026-09-01" and v["notes"] is None and v["nickname"] is None and "driver_id" not in v
    assert "status" not in v                                  # ลาออก / กลับเข้าทำงาน have their own steps


@pytest.mark.parametrize("data,fragment", [
    (make(code=""), "รหัสพนักงาน"), (make(code="ก-1"), "ตัวอักษรอังกฤษ"), (make(code="sb-fr001"), "มีรหัส SB-FR001 อยู่แล้ว"),
    (make(full_name="  "), "ชื่อ-สกุล"), (make(branch="กรุงเทพ"), "สาขา"), (make(start_date="2026-12-01"), "อนาคต"),
    (make(start_date="2569-01-01"), "ค.ศ."), (make(start_date="xx"), "ไม่ถูกต้อง"),
])
def test_bad_input_is_refused_in_thai(data, fragment):
    with pytest.raises(em.InvalidEmployee, match=fragment):
        em.clean_employee(data, others=OTHERS, today=TODAY)


def test_an_edit_only_touches_the_keys_sent_and_may_keep_its_own_code():
    existing = {"id": "1", "code": "SB-FR001", "full_name": "นายก", "start_date": "2025-01-01"}
    assert em.clean_employee({"position": "ช่าง"}, existing=existing, others=OTHERS[1:], today=TODAY) == {"position": "ช่าง"}
    assert em.clean_employee({"code": "sb-fr001"}, existing=existing, others=OTHERS[1:], today=TODAY) == {"code": "SB-FR001"}
    with pytest.raises(em.InvalidEmployee):
        em.clean_employee({"code": "SB-PD033"}, existing=existing, others=OTHERS[1:], today=TODAY)


def test_the_start_date_cannot_be_after_the_leaving_date():
    existing = {"id": "1", "code": "SB-FR001", "status": "resigned", "end_date": "2026-03-01"}
    with pytest.raises(em.InvalidEmployee, match="วันเริ่มงาน"):
        em.clean_employee({"start_date": "2026-05-01"}, existing=existing, others=[], today=TODAY)


def test_resigning_takes_a_date_and_a_reason():
    e = {"status": "active", "start_date": "2025-01-01"}
    assert em.clean_resign({"end_date": "2026-10-20", "reason": " ย้ายบ้าน "}, existing=e, today=TODAY) == {"status": "resigned", "end_date": "2026-10-20", "end_reason": "ย้ายบ้าน"}
    assert em.clean_resign({}, existing=e, today=TODAY)["end_date"] == "2026-10-26"
    assert em.clean_resign({"end_date": "2026-12-01"}, existing=e, today=TODAY)["end_date"] == "2026-12-01"        # a notice already given
    for bad, frag in (({"end_date": "2027-06-01"}, "อนาคต"), ({"end_date": "2024-12-31"}, "ก่อนวันเริ่มงาน")):
        with pytest.raises(em.InvalidEmployee, match=frag):
            em.clean_resign(bad, existing=e, today=TODAY)
    with pytest.raises(em.InvalidEmployee, match="ลาออกไปแล้ว"):
        em.clean_resign({}, existing={"status": "resigned"}, today=TODAY)


def test_the_head_count_is_by_position_for_people_still_working():
    rows = [{"position": "ผลิต", "status": "active"}, {"position": "ผลิต", "status": "active"}, {"position": "ช่าง", "status": "active"},
            {"position": "ช่าง", "status": "resigned"}, {"position": None, "status": "active"}]
    s = em.summarize(rows)
    assert s["active"] == 4 and s["resigned"] == 1 and s["by_position"] == [("ผลิต", 2), ("ช่าง", 1), ("ยังไม่ระบุ", 1)]


def test_the_pin_is_only_asked_when_one_is_set(conf):
    em.check_pin("")                                          # none configured: open to the app token
    conf(hr_pin="4321")
    with pytest.raises(em.PinRequired):
        em.check_pin("")
    with pytest.raises(em.PinRequired):
        em.check_pin("0000")
    em.check_pin("4321")


def test_nothing_can_be_deleted_while_something_is_attached(monkeypatch):
    monkeypatch.setattr(em, "has_v21", lambda: True)
    monkeypatch.setattr(em, "_get", lambda eid: {"id": "e1", "status": "active"})
    monkeypatch.setattr(em, "REFERENCED_BY", [("employee_loans", "employee_id")])
    monkeypatch.setattr(em.supabase, "select", lambda table, params: [{"id": "x"}])
    with pytest.raises(em.InvalidEmployee, match="ลาออก"):
        em.delete_employee("e1")
    deleted = []
    monkeypatch.setattr(em.supabase, "select", lambda table, params: [])
    monkeypatch.setattr(em.supabase, "delete", lambda table, match: deleted.append((table, match)))
    em.delete_employee("e1")
    assert deleted == [("employees", {"id": "eq.e1"})]


def test_every_endpoint_of_the_section_is_locked_by_the_pin(conf):
    from fastapi.testclient import TestClient
    from app.main import app
    conf(app_token="tok", supabase_url="http://x", supabase_service_key="k", hr_pin="4321")
    c = TestClient(app)
    h = {"X-App-Token": "tok"}
    assert c.get("/api/employees", headers=h).status_code == 423
    assert c.post("/api/employees", headers=h, json={}).status_code == 423
    assert c.delete("/api/employees/x", headers=h).status_code == 423
    assert c.get("/api/employees", headers={**h, "X-HR-Pin": "0000"}).status_code == 423
    assert c.get("/api/employees").status_code == 403           # no app token at all


@pytest.mark.parametrize("name,prefix,rest,gender", [
    ("นายจรูญ  มินฉิมพลี", "นาย", "จรูญ มินฉิมพลี", "ชาย"), ("นางสาวกัญญพัชร ศรีไชยมูล", "นางสาว", "กัญญพัชร ศรีไชยมูล", "หญิง"),
    ("นางสำเนา ทิพเนตร", "นาง", "สำเนา ทิพเนตร", "หญิง"), ("นาง สาวิตรี", "นาง", "สาวิตรี", "หญิง"), ("น.ส. สมศรี ใจดี", "นางสาว", "สมศรี ใจดี", "หญิง"),
    ("นายใหญ่( Mr. TUN LIN )", "นาย", "ใหญ่( Mr. TUN LIN )", "ชาย"), ("Mr. Aung Aung", "นาย", "Aung Aung", "ชาย"), ("Mrs. New San", "นาง", "New San", "หญิง"),
    ("Miss Aay Aay Ma", "นางสาว", "Aay Aay Ma", "หญิง"),
    ("ตุ๊กตา ( Mrs. THU ZAR WIN )", "นาง", "ตุ๊กตา ( Mrs. THU ZAR WIN )", "หญิง"),          # the title is inside the name: kept there, the gender is known
    ("ปิยวรรณ (หญิง) ถิ่นช่วง", None, "ปิยวรรณ (หญิง) ถิ่นช่วง", "หญิง"),                    # only a hint: no prefix, the gender is
    ("สุกัญญา", None, "สุกัญญา", None), ("นายกฤษดา", "นาย", "กฤษดา", "ชาย"), ("นาย", None, "นาย", None),
])
def test_the_prefix_and_gender_are_read_from_the_name(name, prefix, rest, gender):
    assert em.split_prefix(name) == (prefix, rest, gender)


def test_a_title_typed_into_the_name_box_moves_to_its_own_field():
    v = em.clean_employee(make(full_name="นางสาวพลอย รักดี"), others=[], today=TODAY)
    assert (v["prefix"], v["full_name"], v["gender"]) == ("นางสาว", "พลอย รักดี", "หญิง")
    assert em.display_name(v["prefix"], v["full_name"]) == "นางสาวพลอย รักดี"


def test_a_chosen_prefix_decides_the_gender_and_beats_one_in_the_name():
    v = em.clean_employee(make(full_name="สมชาย ใจดี", prefix="นาย", gender="หญิง"), others=[], today=TODAY)
    assert (v["prefix"], v["gender"]) == ("นาย", "ชาย")
    v = em.clean_employee(make(full_name="นายสมชาย ใจดี", prefix="นาง"), others=[], today=TODAY)
    assert (v["prefix"], v["full_name"], v["gender"]) == ("นาง", "สมชาย ใจดี", "หญิง")


def test_without_a_prefix_the_gender_is_set_by_hand_or_left_empty():
    v = em.clean_employee(make(full_name="สุกัญญา"), others=[], today=TODAY)
    assert v["prefix"] is None and v["gender"] is None
    assert em.clean_employee(make(full_name="สุกัญญา", gender="หญิง"), others=[], today=TODAY)["gender"] == "หญิง"
    assert em.clean_employee(make(full_name="ปิยวรรณ (หญิง) ถิ่นช่วง"), others=[], today=TODAY)["gender"] == "หญิง"


def test_clearing_the_prefix_on_an_edit_and_bad_values():
    existing = {"id": "1", "code": "SB-FR001", "prefix": "นาย", "gender": "ชาย", "full_name": "จรูญ"}
    v = em.clean_employee({"prefix": "", "gender": "หญิง"}, existing=existing, others=[], today=TODAY)
    assert v == {"prefix": None, "gender": "หญิง"}
    for bad, frag in (({"prefix": "คุณ"}, "นาย / นาง / นางสาว"), ({"gender": "x"}, "ชาย หรือ หญิง")):
        with pytest.raises(em.InvalidEmployee, match=frag):
            em.clean_employee(bad, existing=existing, others=[], today=TODAY)


def test_the_head_count_also_counts_by_gender():
    rows = [{"status": "active", "gender": "ชาย"}, {"status": "active", "gender": "หญิง"}, {"status": "active", "gender": "หญิง"},
            {"status": "active"}, {"status": "resigned", "gender": "ชาย"}]
    assert em.summarize(rows)["by_gender"] == {"ชาย": 1, "หญิง": 2, "ยังไม่ระบุ": 1}


# ------------------------------------------------- a driver is ONE record, edited here
class FakeDb:
    """employees / drivers / vehicles as lists; just what sync_driver and delete_employee use."""
    def __init__(self, employees=(), drivers=(), vehicles=()):
        self.t = {"employees": [dict(r) for r in employees], "drivers": [dict(r) for r in drivers], "vehicles": [dict(r) for r in vehicles]}
        self.n = 0

    def _match(self, rows, match):
        return [r for r in rows if all(r.get(k) == v[3:] for k, v in match.items())]

    def select(self, table, params=None):
        params = params or {}
        return [dict(r) for r in self._match(self.t[table], {k: v for k, v in params.items() if isinstance(v, str) and v.startswith("eq.")})]

    def insert(self, table, rows, **kw):
        self.n += 1
        row = {**rows[0], "id": f"new{self.n}"}
        self.t[table].append(row)
        return [row]

    def update(self, table, match, values):
        rows = self._match(self.t[table], match)
        for r in rows:
            r.update(values)
        return rows

    def delete(self, table, match):
        for r in self._match(self.t[table], match):
            self.t[table].remove(r)


@pytest.fixture
def fake(monkeypatch):
    def make_db(**kw):
        d = FakeDb(**kw)
        for name in ("select", "insert", "update", "delete"):
            monkeypatch.setattr(em.supabase, name, getattr(d, name))
        monkeypatch.setattr(em.fleet, "has_v6", lambda: True)
        monkeypatch.setattr(em.fleet, "has_v7", lambda: True)
        monkeypatch.setattr(em.fleet, "_changed", lambda: None)
        monkeypatch.setattr(em, "has_v21", lambda: True)
        monkeypatch.setattr(em, "REFERENCED_BY", [])                  # (the loans module adds its table here; this fake has none)
        monkeypatch.setattr(em, "_all", lambda: [dict(r) for r in d.t["employees"]])
        monkeypatch.setattr(em, "_drivers", lambda: {r["id"]: r for r in d.t["drivers"]})
        return d
    return make_db


E1, E2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def emp(id_, **kw):
    return {"id": id_, "code": f"SB-{id_}", "full_name": "สมชาย ใจดี", "nickname": "ชาย", "phone": "081", "branch": "สระบุรี", "position": "พนักงานขับรถสาย",
            "status": "active", "driver_id": None, "notes": None, **kw}


def test_a_driver_position_creates_the_driver_record_and_links_it(fake):
    d = fake(employees=[emp(E1)])
    em.sync_driver(d.t["employees"][0])
    assert d.t["drivers"] == [{"id": "new1", "name": "สมชาย ใจดี", "phone": "081", "branch": "สระบุรี", "notes": None, "active": True, "nickname": "ชาย"}]
    assert d.t["employees"][0]["driver_id"] == "new1"


def test_other_positions_get_no_driver_record(fake):
    d = fake(employees=[emp(E1, position="ผลิต")])
    em.sync_driver(d.t["employees"][0])
    assert d.t["drivers"] == [] and d.t["employees"][0]["driver_id"] is None


def test_editing_the_employee_updates_the_driver_record(fake):
    d = fake(employees=[emp(E1, driver_id="d1")], drivers=[{"id": "d1", "name": "เก่า", "phone": None, "active": True}])
    em.edit_employee(E1, {"phone": "099 111", "nickname": "ใหม่"})
    assert d.t["drivers"][0]["phone"] == "099 111" and d.t["drivers"][0]["nickname"] == "ใหม่" and d.t["drivers"][0]["name"] == "สมชาย ใจดี"


def test_resigning_switches_the_driver_off_and_frees_their_trucks_and_coming_back_switches_on(fake):
    d = fake(employees=[emp(E1, driver_id="d1")], drivers=[{"id": "d1", "name": "x", "active": True}], vehicles=[{"id": "v1", "vehicle_number": "6", "main_driver_id": "d1"}])
    em.resign(E1, {"end_date": "2026-10-20"})
    assert d.t["drivers"][0]["active"] is False and d.t["vehicles"][0]["main_driver_id"] is None
    em.reinstate(E1)
    assert d.t["drivers"][0]["active"] is True


def test_an_employee_on_a_truck_cannot_be_deleted_but_a_mistake_takes_its_driver_record_with_it(fake):
    d = fake(employees=[emp(E1, driver_id="d1"), emp(E2, driver_id="d2")], drivers=[{"id": "d1", "name": "a", "active": True}, {"id": "d2", "name": "b", "active": True}],
             vehicles=[{"id": "v1", "vehicle_number": "6", "main_driver_id": "d1"}])
    with pytest.raises(em.InvalidEmployee, match="#6"):
        em.delete_employee(E1)
    em.delete_employee(E2)
    assert [e["id"] for e in d.t["employees"]] == [E1] and [x["id"] for x in d.t["drivers"]] == ["d1"]


def test_the_driver_positions():
    assert all(em.is_driver_position(p) for p in ("พนักงานขับรถ", "พนักงานขับรถสาย", "พนักงานขับรถหน้าโรง"))
    assert not any(em.is_driver_position(p) for p in ("ผลิต", "", None, "ช่าง"))
