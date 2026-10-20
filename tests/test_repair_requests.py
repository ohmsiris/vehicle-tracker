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
    assert seen == {"rid": "abc", "action": "fixed_awaiting_bill", "fixed_by": "ช่างเอ๋", "fixed_date": "2026-10-20", "reason": ""}


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
