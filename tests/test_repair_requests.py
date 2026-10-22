import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import repair_requests as rr

TODAY = date(2026, 10, 20)


def req(status="open", **over):
    base = {"id": "r1", "status": status, "request_date": "2026-10-10", "fixed_event_id": None,
            "fixed_by": None, "fixed_date": None, "no_bill": False}
    base.update(over)
    return base


def act(request, action, **kw):
    return rr.apply_action(request, action, today=TODAY, **kw)


# ------------------------------------------------------------ the state rules
def test_open_to_awaiting_bill():
    v = act(req(), "fixed_awaiting_bill", fixed_by="  ช่างเอ๋ ")
    assert v == {"status": "awaiting_bill", "fixed_by": "ช่างเอ๋", "fixed_date": "2026-10-20"}


def test_open_to_awaiting_bill_with_explicit_date():
    v = act(req(), "fixed_awaiting_bill", fixed_by="ชมรม", fixed_date="2026-10-15")
    assert v["fixed_date"] == "2026-10-15"


@pytest.mark.parametrize("kwargs, fragment", [
    ({"fixed_by": ""}, "ชื่อ"),
    ({"fixed_by": "   "}, "ชื่อ"),
    ({"fixed_by": "x", "fixed_date": "10/10/2026"}, "วันที่ซ่อมไม่ถูกต้อง"),
    ({"fixed_by": "x", "fixed_date": "2026-12-25"}, "อนาคต"),
    ({"fixed_by": "x", "fixed_date": "2026-10-09"}, "ก่อนวันที่แจ้ง"),
])
def test_fixing_validates(kwargs, fragment):
    with pytest.raises(rr.InvalidRequest) as exc:
        act(req(), "fixed_awaiting_bill", **kwargs)
    assert fragment in str(exc.value)


def test_fixed_with_no_bill_completes_immediately_and_is_flagged():
    v = act(req(), "fixed_no_bill", fixed_by="ช่างเอ๋", reason="  เล็กน้อย ")
    assert v == {"status": "completed", "fixed_by": "ช่างเอ๋", "fixed_date": "2026-10-20", "no_bill": True, "no_bill_reason": "เล็กน้อย"}
    assert act(req(), "fixed_no_bill", fixed_by="x")["no_bill_reason"] is None


def test_cannot_mark_fixed_twice():
    for status in ("awaiting_bill", "completed", "cancelled"):
        with pytest.raises(rr.InvalidRequest):
            act(req(status), "fixed_awaiting_bill", fixed_by="x")


def test_no_bill_only_from_awaiting_bill():
    v = act(req("awaiting_bill", fixed_by="ชมรม", fixed_date="2026-10-15"), "no_bill", reason="ร้านไม่ให้บิล")
    assert v == {"status": "completed", "no_bill": True, "no_bill_reason": "ร้านไม่ให้บิล"}
    assert "fixed_by" not in v           # who/when fixed it is kept, not overwritten
    for status in ("open", "completed", "cancelled"):
        with pytest.raises(rr.InvalidRequest):
            act(req(status), "no_bill")


def test_cancel():
    assert act(req("open"), "cancel") == {"status": "cancelled"}
    assert act(req("awaiting_bill"), "cancel") == {"status": "cancelled"}
    for status in ("completed", "cancelled"):
        with pytest.raises(rr.InvalidRequest):
            act(req(status), "cancel")


def test_reopen_undoes_a_mistake_and_clears_the_fixed_info():
    cleared = {"status": "open", "fixed_by": None, "fixed_date": None, "no_bill": False, "no_bill_reason": None}
    assert act(req("awaiting_bill", fixed_by="x", fixed_date="2026-10-15"), "reopen") == cleared
    assert act(req("completed", no_bill=True, fixed_by="x", fixed_date="2026-10-15"), "reopen") == cleared
    assert act(req("cancelled"), "reopen") == cleared


def test_reopen_refused_when_a_real_record_is_attached():
    with pytest.raises(rr.InvalidRequest) as exc:
        act(req("completed", fixed_event_id="evt-1"), "reopen")
    assert "บิล" in str(exc.value)


def test_reopen_of_an_open_request_is_rejected():
    with pytest.raises(rr.InvalidRequest):
        act(req("open"), "reopen")


def test_unknown_action():
    with pytest.raises(rr.InvalidRequest):
        act(req(), "delete_everything")


# ------------------------------------------------------- database wrappers
class FakeDb:
    def __init__(self):
        self.rows = {"r1": req("open", problem="เบรกไม่อยู่")}
        self.vehicle_exists = True
        self.inserted, self.updates = [], []
        self.stale = False

    def select(self, table, params=None):
        if table == "vehicles":
            return [{"id": "v1"}] if self.vehicle_exists else []
        if table == "repair_requests":
            rid = (params or {}).get("id", "")[3:]
            return [self.rows[rid]] if rid in self.rows else []
        raise AssertionError(table)

    def insert(self, table, rows, **kw):
        self.inserted.append((table, rows))
        return [{**rows[0], "id": "new1", "seq": 7}]

    def update(self, table, match, values):
        self.updates.append((match, values))
        return [] if self.stale else [{**self.rows.get(match["id"][3:], {}), **values}]


@pytest.fixture
def db(monkeypatch):
    d = FakeDb()
    for name in ("select", "insert", "update"):
        monkeypatch.setattr(rr.supabase, name, getattr(d, name))
    return d


def test_create_cleans_and_defaults(db):
    row = rr.create({"vehicle_id": "v1", "problem": "  เบรกไม่อยู่  ", "reported_by": " สุกฤ ", "urgent": 1})
    sent = db.inserted[0][1][0]
    assert sent["problem"] == "เบรกไม่อยู่" and sent["reported_by"] == "สุกฤ" and sent["urgent"] is True
    assert sent["request_date"] == rr.today_bangkok().isoformat()
    assert row["seq"] == 7


@pytest.mark.parametrize("data, fragment", [
    ({"vehicle_id": None, "problem": "x"}, "เลือกรถ"),
    ({"vehicle_id": "v1", "problem": "   "}, "ปัญหา"),
    ({"vehicle_id": "v1", "problem": "x", "request_date": "2030-01-01"}, "อนาคต"),
])
def test_create_validation(db, data, fragment):
    with pytest.raises(rr.InvalidRequest) as exc:
        rr.create(data)
    assert fragment in str(exc.value) and db.inserted == []


def test_create_with_unknown_vehicle(db):
    db.vehicle_exists = False
    with pytest.raises(rr.InvalidRequest):
        rr.create({"vehicle_id": "ghost", "problem": "x"})


def test_act_writes_only_when_status_is_unchanged(db):
    rr.act("r1", "fixed_awaiting_bill", fixed_by="ช่างเอ๋")
    match, values = db.updates[0]
    assert match == {"id": "eq.r1", "status": "eq.open"}          # conditional write
    assert values["status"] == "awaiting_bill"


def test_act_reports_a_stale_page_instead_of_overwriting(db):
    db.stale = True       # someone else moved it between our read and write
    with pytest.raises(rr.StaleRequest):
        rr.act("r1", "cancel")


def test_act_unknown_request(db):
    with pytest.raises(rr.InvalidRequest):
        rr.act("ghost", "cancel")


def test_edit_only_touches_descriptive_fields(db):
    rr.edit("r1", {"problem": " ใหม่ ", "urgent": True, "status": "completed", "fixed_by": "hacker"})
    assert db.updates[0][1] == {"problem": "ใหม่", "urgent": True}    # status / fixed_by ignored
    with pytest.raises(rr.InvalidRequest):
        rr.edit("r1", {"problem": "  "})
    with pytest.raises(rr.InvalidRequest):
        rr.edit("r1", {"status": "completed"})


# ------------------------------------------------------------------ endpoints
def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    return main, TestClient(main.app)


def test_board_endpoints_need_the_token_and_map_errors(monkeypatch):
    main, client = _client(monkeypatch)
    monkeypatch.setattr(main.repair_requests, "list_active", lambda: [{"request_id": "r1"}])
    monkeypatch.setattr(main.repair_requests, "list_recent", lambda: [{"request_id": "r0"}])

    assert client.get("/api/requests").status_code == 403
    assert client.get("/api/requests?token=secret").json() == [{"request_id": "r1"}]
    assert client.get("/api/requests?scope=recent&token=secret").json() == [{"request_id": "r0"}]
    assert client.get("/api/requests?scope=weird&token=secret").status_code == 400

    def invalid(*a, **k): raise rr.InvalidRequest("ใส่ปัญหาที่ต้องซ่อมด้วย")
    monkeypatch.setattr(main.repair_requests, "create", invalid)
    r = client.post("/api/requests?token=secret", json={"problem": ""})
    assert r.status_code == 422 and "ปัญหา" in r.json()["detail"]

    def stale(*a, **k): raise rr.StaleRequest()
    monkeypatch.setattr(main.repair_requests, "act", stale)
    assert client.post("/api/requests/r1/action?token=secret", json={"action": "cancel"}).status_code == 409

    def boom(*a, **k): raise main.supabase.SupabaseError("secret internals")
    monkeypatch.setattr(main.repair_requests, "act", boom)
    r = client.post("/api/requests/r1/action?token=secret", json={"action": "cancel"})
    assert r.status_code == 502 and "internals" not in r.text


def test_action_endpoint_passes_fields_through(monkeypatch):
    main, client = _client(monkeypatch)
    seen = {}
    monkeypatch.setattr(main.repair_requests, "act", lambda rid, action, **kw: seen.update(rid=rid, action=action, **kw) or {"ok": True})
    r = client.post("/api/requests/abc/action?token=secret",
                    json={"action": "fixed_awaiting_bill", "fixed_by": "ช่างเอ๋", "fixed_date": "2026-10-20", "reason": ""})
    assert r.status_code == 200
    assert seen == {"rid": "abc", "action": "fixed_awaiting_bill", "fixed_by": "ช่างเอ๋", "fixed_date": "2026-10-20", "reason": "",
                    "shop": "", "sent_date": None, "expected_return": None}


def test_edit_endpoint_sends_only_fields_the_page_sent(monkeypatch):
    main, client = _client(monkeypatch)
    seen = {}
    monkeypatch.setattr(main.repair_requests, "edit", lambda rid, data: seen.update(data) or {"ok": True})
    client.patch("/api/requests/abc?token=secret", json={"urgent": True})
    assert seen == {"urgent": True}          # problem / reported_by not sent -> not touched


def test_board_503_when_supabase_unconfigured():
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    assert TestClient(main.app).get("/api/requests?token=secret").status_code == 503


def test_buddhist_year_in_the_fixed_date_gets_a_specific_message():
    with pytest.raises(rr.InvalidRequest) as exc:
        act(req(), "fixed_awaiting_bill", fixed_by="ช่างเอ๋", fixed_date="2569-10-15")
    assert "พ.ศ." in str(exc.value) and "2026" in str(exc.value)


# ------------------------------------------------- the "at the shop" step (schema_v5)
def test_send_to_shop():
    v = act(req(), "send_to_shop", shop="  อู่ชมรม ", sent_date="2026-10-12", expected_return="2026-10-25")
    assert v == {"status": "at_shop", "shop": "อู่ชมรม", "sent_date": "2026-10-12", "expected_return": "2026-10-25"}


def test_send_to_shop_defaults_to_today_and_needs_no_return_date():
    v = act(req(), "send_to_shop", shop="ศูนย์ Hino")
    assert v["sent_date"] == "2026-10-20" and v["expected_return"] is None


def test_the_shop_name_is_folded_to_the_canonical_one():
    assert act(req(), "send_to_shop", shop="ชมรมย์การช่าง")["shop"] == "อู่ชมรม"
    assert act(req(), "send_to_shop", shop="isuzu")["shop"] == "ศูนย์ Isuzu"
    assert act(req(), "send_to_shop", shop="อู่ใหม่ของเพื่อน")["shop"] == "อู่ใหม่ของเพื่อน"      # unknown shops are kept as typed


@pytest.mark.parametrize("kwargs, fragment", [
    ({"shop": ""}, "อู่"),
    ({"shop": "  "}, "อู่"),
    ({"shop": "อู่ชมรม", "sent_date": "2026-10-09"}, "ก่อนวันที่แจ้ง"),
    ({"shop": "อู่ชมรม", "sent_date": "2026-12-25"}, "อนาคต"),
    ({"shop": "อู่ชมรม", "sent_date": "2569-10-15"}, "พ.ศ."),
    ({"shop": "อู่ชมรม", "sent_date": "2026-10-15", "expected_return": "2026-10-14"}, "ก่อนวันที่ส่ง"),
    ({"shop": "อู่ชมรม", "expected_return": "2027-06-01"}, "ไกลเกินไป"),
    ({"shop": "อู่ชมรม", "expected_return": "2569-10-30"}, "พ.ศ."),
    ({"shop": "อู่ชมรม", "expected_return": "30/10/2026"}, "ไม่ถูกต้อง"),
])
def test_sending_to_a_shop_validates(kwargs, fragment):
    with pytest.raises(rr.InvalidRequest) as exc:
        act(req(), "send_to_shop", **kwargs)
    assert fragment in str(exc.value)


def test_an_expected_return_may_be_in_the_future():
    assert act(req(), "send_to_shop", shop="อู่ชมรม", expected_return="2026-11-15")["expected_return"] == "2026-11-15"


def test_resending_from_the_shop_changes_the_details_but_other_states_cannot_send():
    at_shop = req("at_shop", shop="อู่ชมรม", sent_date="2026-10-12")
    assert act(at_shop, "send_to_shop", shop="โชคพัฒนา", sent_date="2026-10-13")["shop"] == "โชคพัฒนา"
    for status in ("awaiting_bill", "completed", "cancelled"):
        with pytest.raises(rr.InvalidRequest):
            act(req(status), "send_to_shop", shop="อู่ชมรม")


def test_returned_moves_to_awaiting_bill_and_credits_the_shop():
    at_shop = req("at_shop", shop="อู่ช่างเก่ง", sent_date="2026-10-12")
    assert act(at_shop, "returned") == {"status": "awaiting_bill", "fixed_by": "อู่ช่างเก่ง", "fixed_date": "2026-10-20"}
    v = act(at_shop, "returned", fixed_date="2026-10-18", fixed_by="ช่างเก่ง (คุณหนึ่ง)")
    assert v["fixed_date"] == "2026-10-18" and v["fixed_by"] == "ช่างเก่ง (คุณหนึ่ง)"


def test_returned_validates():
    at_shop = req("at_shop", shop="อู่ชมรม", sent_date="2026-10-12")
    with pytest.raises(rr.InvalidRequest) as exc:
        act(at_shop, "returned", fixed_date="2026-10-11")
    assert "ก่อนวันที่ส่งซ่อม" in str(exc.value)
    with pytest.raises(rr.InvalidRequest):
        act(at_shop, "returned", fixed_date="2026-12-25")
    for status in ("open", "awaiting_bill", "completed", "cancelled"):
        with pytest.raises(rr.InvalidRequest):
            act(req(status, sent_date="2026-10-12"), "returned")


def test_returned_without_a_bill_closes_it_flagged():
    at_shop = req("at_shop", shop="อู่ชมรม", sent_date="2026-10-12")
    v = act(at_shop, "returned_no_bill", reason=" ไม่ให้บิล ")
    assert v == {"status": "completed", "fixed_by": "อู่ชมรม", "fixed_date": "2026-10-20", "no_bill": True, "no_bill_reason": "ไม่ให้บิล"}


def test_cancel_from_the_shop():
    assert act(req("at_shop", shop="x", sent_date="2026-10-12"), "cancel") == {"status": "cancelled"}


def test_reopen_from_the_shop_recalls_the_truck():
    v = act(req("at_shop", shop="อู่ชมรม", sent_date="2026-10-12", expected_return="2026-10-20"), "reopen")
    assert v == {"status": "open", "shop": None, "sent_date": None, "expected_return": None}


def test_reopen_after_return_steps_back_to_the_shop_not_to_the_start():
    cleared = {"fixed_by": None, "fixed_date": None, "no_bill": False, "no_bill_reason": None}
    back = req("awaiting_bill", shop="อู่ชมรม", sent_date="2026-10-12", fixed_by="อู่ชมรม", fixed_date="2026-10-18")
    assert act(back, "reopen") == {"status": "at_shop", **cleared}
    closed = req("completed", no_bill=True, shop="อู่ชมรม", sent_date="2026-10-12", fixed_by="อู่ชมรม", fixed_date="2026-10-18")
    assert act(closed, "reopen") == {"status": "at_shop", **cleared}


def test_a_request_never_sent_anywhere_still_reopens_to_open_and_touches_no_v5_column():
    v = act(req("awaiting_bill", fixed_by="ช่างเอ๋", fixed_date="2026-10-15"), "reopen")
    assert v["status"] == "open" and not set(v) & set(rr.V5_COLUMNS)


def test_the_old_actions_write_no_v5_column_so_they_work_before_the_migration():
    for action, kw in (("fixed_awaiting_bill", {"fixed_by": "x"}), ("fixed_no_bill", {"fixed_by": "x"}), ("cancel", {})):
        assert not set(act(req(), action, **kw)) & set(rr.V5_COLUMNS)


@pytest.fixture
def v5(monkeypatch):
    monkeypatch.setitem(rr._v5_probe, "at", 1e18)       # "probed a moment ago": no database call
    monkeypatch.setitem(rr._v5_probe, "ok", True)


@pytest.fixture
def no_v5(monkeypatch):
    monkeypatch.setitem(rr._v5_probe, "at", 1e18)
    monkeypatch.setitem(rr._v5_probe, "ok", False)


def test_create_straight_at_a_shop_and_off_the_paper(db, v5):
    rr.create({"vehicle_id": "v1", "problem": "เพลาขาด", "shop": "ชมรมย์การช่าง", "on_paper": False, "expected_return": None})
    row = db.inserted[0][1][0]
    assert row["status"] == "at_shop" and row["shop"] == "อู่ชมรม" and row["on_paper"] is False
    assert row["sent_date"] == row["request_date"] and row["expected_return"] is None


def test_a_plain_create_sends_no_v5_column(db, no_v5):
    rr.create({"vehicle_id": "v1", "problem": "เบรก", "on_paper": None, "shop": None})
    assert not set(db.inserted[0][1][0]) & set(rr.V5_COLUMNS) and "status" not in db.inserted[0][1][0]


def test_v5_features_are_refused_with_a_clear_message_until_the_migration_is_run(db, no_v5):
    with pytest.raises(rr.InvalidRequest) as exc:
        rr.create({"vehicle_id": "v1", "problem": "เบรก", "shop": "อู่ชมรม"})
    assert "schema_v5.sql" in str(exc.value)
    with pytest.raises(rr.InvalidRequest) as exc:
        rr.create({"vehicle_id": "v1", "problem": "เบรก", "on_paper": False})
    assert "schema_v5.sql" in str(exc.value)
    with pytest.raises(rr.InvalidRequest) as exc:
        rr.act("r1", "send_to_shop", shop="อู่ชมรม")
    assert "schema_v5.sql" in str(exc.value) and not db.updates
    rr.act("r1", "cancel")                                    # the old actions are unaffected
    assert db.updates[-1][1] == {"status": "cancelled"}


def test_sending_a_request_to_a_shop_updates_it_only_if_still_open(db, v5):
    rr.act("r1", "send_to_shop", shop="โชคพัฒนา", sent_date="2026-10-12")
    match, values = db.updates[-1]
    assert match == {"id": "eq.r1", "status": "eq.open"} and values["status"] == "at_shop" and values["shop"] == "โชคพัฒนา"


def test_the_action_endpoint_carries_the_shop_fields(monkeypatch):
    main, client = _client(monkeypatch)
    seen = {}
    monkeypatch.setattr(main.repair_requests, "act", lambda rid, action, **kw: seen.update(action=action, **kw) or {"ok": True})
    client.post("/api/requests/abc/action?token=secret", json={"action": "send_to_shop", "shop": "อู่ชมรม", "sent_date": "2026-10-12", "expected_return": "2026-10-20"})
    assert seen["shop"] == "อู่ชมรม" and seen["sent_date"] == "2026-10-12" and seen["expected_return"] == "2026-10-20"


def test_the_new_request_endpoint_carries_shop_and_on_paper(monkeypatch):
    main, client = _client(monkeypatch)
    seen = {}
    monkeypatch.setattr(main.repair_requests, "create", lambda d: seen.update(d) or {"id": "x"})
    client.post("/api/requests?token=secret", json={"problem": "เพลาขาด", "shop": "อู่ชมรม", "on_paper": False})
    assert seen["shop"] == "อู่ชมรม" and seen["on_paper"] is False


def test_meta_offers_the_shop_list(monkeypatch):
    main, client = _client(monkeypatch)
    monkeypatch.setattr(main, "load_roster", lambda: [])
    shops_ = client.get("/api/meta?token=secret").json()["shops"]
    assert "อู่ชมรม" in shops_ and "ศูนย์ Hino" in shops_ and "ช่างเอ๋ (ในโรงงาน)" in shops_
