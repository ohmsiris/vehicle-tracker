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
    assert v["code"] == "SB-FR200" and v["full_name"] == "นายสมชาย ใจดี" and v["branch"] == "สระบุรี" and v["position"] == "ผลิต"
    assert v["phone"] == "081 234 5678" and v["start_date"] == "2026-09-01" and v["notes"] is None and v["nickname"] is None and v["driver_id"] is None
    assert "status" not in v                                  # ลาออก / กลับเข้าทำงาน have their own steps


@pytest.mark.parametrize("data,fragment", [
    (make(code=""), "รหัสพนักงาน"), (make(code="ก-1"), "ตัวอักษรอังกฤษ"), (make(code="sb-fr001"), "มีรหัส SB-FR001 อยู่แล้ว"),
    (make(full_name="  "), "ชื่อ-สกุล"), (make(branch="กรุงเทพ"), "สาขา"), (make(start_date="2026-12-01"), "อนาคต"),
    (make(start_date="2569-01-01"), "ค.ศ."), (make(start_date="xx"), "ไม่ถูกต้อง"), (make(driver_id="nope"), "รหัสไม่ถูกต้อง"),
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
