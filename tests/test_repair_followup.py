from datetime import date

import pytest

from app import repair_followup as fu
from app import repair_requests as rr

TODAY = date(2026, 10, 23)
VEHICLE = {"id": "v1", "plate_number": "1ฒถ 8966", "vehicle_number": "1", "branch": "สระบุรี", "usage": "รถสาย", "main_driver_id": "d1"}


def at_shop(**over):
    base = {"id": "r1", "status": "at_shop", "request_date": "2026-10-10", "sent_date": "2026-10-20", "shop": "ชมรมย์การช่าง",
            "fixed_event_id": None, "fixed_by": None, "fixed_date": None, "no_bill": False, "vehicle_id": "v1", "problem": "น้ำหม้อน้ำหาย"}
    return {**base, **over}


# ------------------------------------------------------------ the rules
def test_a_car_that_came_back_is_asked_about_the_day_after():
    assert fu.check_values(date(2026, 10, 22)) == {"check_status": "pending", "check_due": "2026-10-23", "checked_at": None, "check_note": None}


def test_returning_from_a_shop_starts_the_question_only_when_the_tables_exist():
    with_v17 = rr.apply_action(at_shop(), "returned", fixed_date="2026-10-22", today=TODAY, with_check=True)
    assert with_v17["status"] == "awaiting_bill" and with_v17["check_status"] == "pending" and with_v17["check_due"] == "2026-10-23"
    old = rr.apply_action(at_shop(), "returned", fixed_date="2026-10-22", today=TODAY)
    assert "check_status" not in old
    no_bill = rr.apply_action(at_shop(), "returned_no_bill", fixed_date="2026-10-22", today=TODAY, with_check=True)
    assert no_bill["status"] == "completed" and no_bill["check_status"] == "pending"        # still asked, even with no bill


def test_taking_the_return_back_clears_the_question():
    returned = at_shop(status="awaiting_bill", fixed_date="2026-10-22", check_status="pending")
    undone = rr.apply_action(returned, "reopen", today=TODAY, with_check=True)
    assert undone["status"] == "at_shop" and undone["check_status"] is None and undone["check_due"] is None


def test_the_answer_must_be_one_of_three_and_a_problem_needs_words():
    assert fu.clean_answer("ok", "  ") == ("ok", None)
    assert fu.clean_answer("problem", " ยังมีเสียง ") == ("problem", "ยังมีเสียง")
    for bad in (("maybe", ""), ("problem", "")):
        with pytest.raises(fu.InvalidFollowup):
            fu.clean_answer(*bad)


def test_a_progress_note_and_its_optional_reminder():
    assert fu.clean_update("  รออะไหล่  2 วัน ", None) == ("รออะไหล่ 2 วัน", None)
    assert fu.clean_update("ok", "3") == ("ok", 3)
    for bad in (("", None), ("x", "0x"), ("x", 99)):
        with pytest.raises(fu.InvalidFollowup):
            fu.clean_update(*bad)


def test_the_manager_tickets():
    req = {**at_shop(status="awaiting_bill", fixed_date="2026-10-22"), "reported_by": "สม"}
    driver = fu.ticket_specs("driver_check", req, VEHICLE, due=date(2026, 10, 23), call={"name": "สม", "phone": "081-111-2222"})
    assert driver["request_kind"] == "driver_check" and driver["request_id"] == "r1" and driver["vehicle_id"] == "v1"
    assert driver["title"] == "โทรถามคนขับ #1 · 1ฒถ 8966: รถปกติไหม" and driver["due_date"] == "2026-10-23" and driver["place"] == "สระบุรี"
    assert driver["waiting_phone"] == "081-111-2222" and "ชมรมย์การช่าง" in driver["notes"]
    bill = fu.ticket_specs("bill_chase", req, VEHICLE, due=date(2026, 10, 23), shop_phone="081-928-5474")
    assert bill["title"] == "ตามบิล #1 · 1ฒถ 8966 จาก ชมรมย์การช่าง" and bill["type_key"] == "chase" and bill["waiting_phone"] == "081-928-5474"
    nobody = fu.ticket_specs("driver_check", req, {**VEHICLE, "branch": "อื่น"}, due=date(2026, 10, 23), call=None)
    assert nobody["waiting_phone"] is None and nobody["place"] is None


# ------------------------------------------------------------ the two boards in step
class FakeDb:
    def __init__(self, request):
        self.request = request
        self.tickets, self.updates = [], []
        self.inserted = []

    def select(self, table, params=None):
        params = params or {}
        if table == "repair_requests":
            return [self.request]
        if table == "vehicles":
            return [VEHICLE]
        if table == "mgr_tickets":
            if params.get("select") == "request_id,request_kind":
                return []
            return [t for t in self.tickets if t["request_id"] == params.get("request_id", "")[3:] and t["request_kind"] == params.get("request_kind", "")[3:] and t["status"] in ("todo", "doing", "waiting")]
        if table == "repair_request_updates":
            return []
        return []

    def select_all(self, table, params):
        return self.select(table, params)

    def insert(self, table, rows, **kw):
        self.inserted.append((table, rows))
        if table == "mgr_tickets":
            self.tickets.extend({**r, "id": f"t{len(self.tickets)}"} for r in rows)
        return [{**rows[0], "id": "new1"}]

    def update(self, table, match, values):
        self.updates.append((table, match, values))
        if table == "repair_requests":
            self.request = {**self.request, **values}
            return [self.request]
        return []


@pytest.fixture
def db(monkeypatch):
    d = FakeDb(at_shop(status="awaiting_bill", fixed_date="2026-10-22", check_status="pending", check_due="2026-10-23", reported_by="สม"))
    for name in ("select", "select_all", "insert", "update"):
        monkeypatch.setattr(fu.supabase, name, getattr(d, name))
    monkeypatch.setitem(fu._probe, "ok", True)
    monkeypatch.setattr(fu.mgr, "has_v12", lambda: True)
    monkeypatch.setattr(fu.mgr, "list_types", lambda include_inactive=False: [{"key": "fix"}, {"key": "chase"}])
    monkeypatch.setattr(fu.fleet, "has_v6", lambda: False)
    monkeypatch.setattr(fu.shops, "directory", lambda: {"ชมรมย์การช่าง": {"contacts": [{"label": "ช่างลม", "phone": "081-928-5474"}]}})
    return d


def test_coming_back_from_a_shop_makes_both_reminders_for_the_day_after(db):
    fu.after_action(at_shop(), db.request, "returned")
    kinds = sorted(t["request_kind"] for t in db.tickets)
    assert kinds == ["bill_chase", "driver_check"]
    assert all(t["due_date"] == "2026-10-23" and t["request_id"] == "r1" for t in db.tickets)
    fu.after_action(at_shop(), db.request, "returned")                       # a repeat never doubles them up
    assert len(db.tickets) == 2


def test_no_bill_return_only_asks_the_driver(db):
    fu.after_action(at_shop(), {**db.request, "status": "completed"}, "returned_no_bill")
    assert [t["request_kind"] for t in db.tickets] == ["driver_check"]


def test_answering_the_driver_closes_the_ticket_and_a_problem_opens_a_new_job(db, monkeypatch):
    created = []
    monkeypatch.setattr(rr, "create", lambda data: created.append(data) or {"id": "new-job"})
    out = fu.answer_check("r1", "problem", "ยังมีน้ำหยด")
    assert out == {"check_status": "problem", "new_request_id": "new-job"}
    assert created[0]["vehicle_id"] == "v1" and created[0]["problem"] == "กลับจากซ่อมแล้วยังมีปัญหา: ยังมีน้ำหยด"
    ticket_update = next(u for u in db.updates if u[0] == "mgr_tickets")
    assert ticket_update[2]["status"] == "done" and "driver_check" in ticket_update[1]["request_kind"]
    with pytest.raises(fu.InvalidFollowup):                                   # already answered
        db.request["check_status"] = "ok"
        fu.answer_check("r1", "ok")


def test_finishing_the_manager_ticket_answers_the_question(db):
    fu.on_ticket_closed({"request_id": "r1", "request_kind": "driver_check"}, "done")
    assert db.request["check_status"] == "ok"
    db.request["check_status"] = "pending"
    fu.on_ticket_closed({"request_id": "r1", "request_kind": "driver_check"}, "cancelled")
    assert db.request["check_status"] == "skipped"
    db.request["check_status"] = "pending"
    fu.on_ticket_closed({"request_id": "r1", "request_kind": "bill_chase"}, "done")        # the bill ticket says nothing about the car
    assert db.request["check_status"] == "pending"


def test_a_saved_bill_closes_the_chase_ticket(db):
    fu.after_action(at_shop(), db.request, "returned")
    fu.on_bill_saved(["r1"])
    closing = [u for u in db.updates if u[0] == "mgr_tickets"][-1]
    assert closing[2]["status"] == "done" and closing[1]["request_kind"] == "in.(bill_chase)"


def test_a_progress_note_can_set_a_reminder_that_replaces_the_old_one(db):
    db.request = at_shop()                                                    # at the shop
    fu.add_update("r1", "รออะไหล่ 2 วัน", 2)
    assert [t["request_kind"] for t in db.tickets] == ["shop_update"] and db.tickets[0]["notes"].endswith("รออะไหล่ 2 วัน")
    assert any(t == "repair_request_updates" for t, _ in db.inserted)
    with pytest.raises(fu.InvalidFollowup):
        db.request = at_shop(status="open")
        fu.add_update("r1", "x")
