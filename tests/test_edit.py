import sys
import time
import uuid
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import events, history, supabase

EID = str(uuid.uuid4())
VID = str(uuid.uuid4())
ATT = str(uuid.uuid4())


def edit_payload(**over):
    base = {
        "vehicle_id": VID,
        "event": {"event_date": date.today().isoformat(), "vendor": "ชมรม", "performed_by": None, "branch": "สระบุรี",
                  "mileage": 473366, "next_service_mileage": 493366, "total_cost": 3000, "notes": "", "summary": "ถ่ายน้ำมัน + เบรก"},
        "line_items": [
            {"attachment_id": ATT, "description": "  ผ้าเบรก ", "category": "เบรก", "quantity": 2, "unit": "ชิ้น", "unit_cost": 1000, "line_total": 2000,
             "low_confidence_fields": ["quantity"]},
            {"attachment_id": None, "description": "ค่าแรง", "category": "ค่าแรง", "line_total": 1000, "low_confidence_fields": []},
        ],
    }
    base.update(over)
    return base


class FakeDb:
    def __init__(self):
        self.rpcs = []
        self.vehicle_exists = True
        self.rpc_error = None

    def select(self, table, params=None):
        assert table == "vehicles"
        return [{"id": VID}] if self.vehicle_exists else []

    def rpc(self, fn, args):
        if self.rpc_error:
            raise self.rpc_error
        self.rpcs.append((fn, args))
        return args["p"]["event_id"]


@pytest.fixture
def db(monkeypatch):
    d = FakeDb()
    monkeypatch.setattr(events.supabase, "select", d.select)
    monkeypatch.setattr(events.supabase, "rpc", d.rpc)
    return d


# ---------------------------------------------------------------- the call
def test_update_sends_one_atomic_rpc_with_trimmed_lines_and_their_flags(db):
    events.update_event(EID, edit_payload())
    (fn, args), = db.rpcs
    p = args["p"]
    assert fn == "update_maintenance_event" and p["event_id"] == EID and p["vehicle_id"] == VID
    assert p["summary"] == "ถ่ายน้ำมัน + เบรก" and p["mileage"] == 473366 and p["event_date"] == date.today().isoformat()
    assert [l["description"] for l in p["line_items"]] == ["ผ้าเบรก", "ค่าแรง"]                         # trimmed
    assert p["line_items"][0]["attachment_id"] == ATT and p["line_items"][1]["attachment_id"] is None   # which bill page a line came from is preserved
    assert p["line_items"][0]["low_confidence_fields"] == ["quantity"]                                  # untouched lines keep their flags


def test_update_without_a_vehicle_leaves_the_truck_alone(db):
    events.update_event(EID, edit_payload(vehicle_id=None))
    assert db.rpcs[0][1]["p"]["vehicle_id"] is None


# -------------------------------------------------------------- validation
@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p["event"].update(event_date="19/08/2026"), "วันที่ไม่ถูกต้อง"),
    (lambda p: p["event"].update(event_date="2569-08-19"), "พ.ศ."),                    # พ.ศ. typed as ค.ศ.
    (lambda p: p["event"].update(event_date="2999-01-01"), "พ.ศ."),
    (lambda p: p["event"].update(mileage=-1), "ติดลบ"),
    (lambda p: p["event"].update(total_cost="abc"), "ไม่ถูกต้อง"),
    (lambda p: p["event"].update(summary="ก" * 501), "ยาวเกินไป"),
    (lambda p: p["event"].update(notes="ก" * 2001), "ยาวเกินไป"),
    (lambda p: p.update(vehicle_id="not-a-uuid"), "รถที่เลือกไม่ถูกต้อง"),
    (lambda p: p.update(line_items=[]), "อย่างน้อย 1 รายการ"),
    (lambda p: p["line_items"][0].update(description="   "), "ยังไม่ได้ใส่ชื่อ"),
    (lambda p: p["line_items"][0].update(category="ของแปลก"), "หมวด"),
    (lambda p: p["line_items"][0].update(attachment_id="nope"), "ไฟล์ที่ไม่ถูกต้อง"),
    (lambda p: p["line_items"][0].update(quantity=-2), "ติดลบ"),
])
def test_validation_rejects_before_touching_the_database(db, mutate, fragment):
    p = edit_payload()
    mutate(p)
    with pytest.raises(events.InvalidEvent) as exc:
        events.update_event(EID, p)
    assert fragment in str(exc.value) and db.rpcs == []


def test_unknown_truck_is_rejected(db):
    db.vehicle_exists = False
    with pytest.raises(events.InvalidEvent):
        events.update_event(EID, edit_payload())
    assert db.rpcs == []


def test_malformed_event_id_is_not_found(db):
    with pytest.raises(events.EventNotFound):
        events.update_event("nope", edit_payload())
    assert db.rpcs == []


# ------------------------------------------------------ database error mapping
def test_missing_migration_is_reported_specifically(db):
    db.rpc_error = supabase.SupabaseError('rpc update_maintenance_event failed: HTTP 404 {"code":"PGRST202","message":"Could not find the function"}')
    with pytest.raises(events.MigrationMissing):
        events.update_event(EID, edit_payload())


def test_event_not_found_in_the_database(db):
    db.rpc_error = supabase.SupabaseError('rpc failed: HTTP 400 {"code":"P0002","message":"event x not found or not confirmed"}')
    with pytest.raises(events.EventNotFound):
        events.update_event(EID, edit_payload())


def test_other_database_errors_propagate(db):
    db.rpc_error = supabase.SupabaseError("rpc failed: HTTP 500 boom")
    with pytest.raises(supabase.SupabaseError):
        events.update_event(EID, edit_payload())


# --------------------------------------------------------------- endpoint
def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    return main, TestClient(main.app)


def test_patch_endpoint_status_mapping(monkeypatch):
    main, client = _client(monkeypatch)
    url = f"/api/events/{EID}?token=secret"
    assert client.patch(f"/api/events/{EID}", json=edit_payload()).status_code == 403

    seen = {}
    monkeypatch.setattr(main.events, "update_event", lambda eid, body: seen.update(eid=eid, body=body))
    r = client.patch(url, json=edit_payload())
    assert r.status_code == 200 and r.json() == {"ok": True} and seen["eid"] == EID and seen["body"]["event"]["vendor"] == "ชมรม"

    def raises(exc):
        def f(*a): raise exc
        return f
    for exc, code in ((events.InvalidEvent("ใส่ชื่อ"), 422), (events.EventNotFound(), 404), (events.MigrationMissing(), 503),
                      (supabase.SupabaseError("secret internals"), 502)):
        monkeypatch.setattr(main.events, "update_event", raises(exc))
        r = client.patch(url, json=edit_payload())
        assert r.status_code == code, (exc, r.text)
        assert "internals" not in r.text
    monkeypatch.setattr(main.events, "update_event", raises(events.MigrationMissing()))
    assert "schema_v4" in client.patch(url, json=edit_payload()).json()["detail"]      # tells the user exactly what to run


# ---------------------------------------------------- the summary column probe
def test_summary_is_only_requested_once_the_column_exists(monkeypatch):
    calls = []
    state = {"column_exists": False}

    def fake_select(table, params=None):
        calls.append(params["select"])
        if not state["column_exists"]:
            raise supabase.SupabaseError("column maintenance_events.summary does not exist")
        return []
    monkeypatch.setattr(history.supabase, "select", fake_select)
    monkeypatch.setitem(history._summary_probe, "at", 0.0)
    assert history._has_summary() is False
    assert history._has_summary() is False and len(calls) == 1          # cached: not re-probed on every page load
    # 30 s later, after the SQL has been run, it is noticed without restarting the server
    state["column_exists"] = True
    history._summary_probe["at"] = time.time() - 31
    assert history._has_summary() is True and len(calls) == 2


def test_event_rows_prefer_the_written_summary_data():
    events_ = [{"id": "e1", "event_date": "2026-10-03", "summary": "  ถ่ายน้ำมัน  ", "low_confidence_fields": []},
               {"id": "e2", "event_date": "2026-10-02", "summary": "   ", "low_confidence_fields": []},
               {"id": "e3", "event_date": "2026-10-01", "low_confidence_fields": []}]
    rows = history.event_rows(events_, [], [])
    assert [r["summary"] for r in rows] == ["ถ่ายน้ำมัน", None, None]
