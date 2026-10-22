"""Recording "done" from the maintenance plan as a repair record, and deleting such a record again."""
import uuid
from datetime import date

import pytest

from app import events, supabase
from app import service_plan as sp

TODAY = date(2026, 10, 22)
VID = str(uuid.uuid4())


def test_every_item_name_is_read_back_as_that_item():
    """The record's line text is the item's own name, and the plan must classify it back to the same item."""
    for key in sp.ITEMS:
        assert sp.classify_line(sp.item_label(key)) == key, key


def test_every_item_has_a_real_category():
    from app.extraction import CATEGORIES
    for key in sp.ITEMS:
        assert sp.ITEM_CATEGORY[key] in CATEGORIES


def test_clean_service_orders_items_like_the_plan():
    c = sp.clean_service({"vehicle_id": VID, "items": ["wheel_grease", "engine_oil", "coolant"], "done_date": "2026-10-20",
                          "odometer_km": "54,000", "performed_by": " ช่างเอ๋ ", "notes": ""}, today=TODAY)
    assert c["items"] == ["engine_oil", "coolant", "wheel_grease"]
    assert c["done_date"] == date(2026, 10, 20) and c["odometer_km"] == 54000 and c["performed_by"] == "ช่างเอ๋" and c["notes"] is None


@pytest.mark.parametrize("patch", [{"items": []}, {"items": ["nope"]}, {"vehicle_id": "x"}, {"done_date": "2026-10-23"}, {"done_date": "2569-10-20"}, {"odometer_km": "abc"}])
def test_clean_service_rejects(patch):
    data = {"vehicle_id": VID, "items": ["engine_oil"], "done_date": "2026-10-20", **patch}
    with pytest.raises(sp.InvalidPlan):
        sp.clean_service(data, today=TODAY)


def test_the_payload_is_a_valid_manual_event():
    clean = sp.clean_service({"vehicle_id": VID, "items": ["engine_oil", "air_filter", "injector_washers"], "done_date": "2026-10-20", "odometer_km": 54000}, today=TODAY)
    payload = sp.service_payload(clean, clean["items"])
    events._validate(payload, 0)                              # what save_event checks before it writes anything
    assert payload["source"] == "manual" and payload["event"]["mileage"] == 54000 and payload["event"]["event_date"] == "2026-10-20"
    assert [ln["description"] for ln in payload["line_items"]] == ["น้ำมันเครื่อง", "กรองอากาศ", "แหวนรองหัวฉีด"]
    assert [ln["category"] for ln in payload["line_items"]] == ["ไส้กรองและของเหลว", "ไส้กรองและของเหลว", "เครื่องยนต์"]


def test_a_saved_record_is_read_back_by_the_plan_as_done_that_day():
    clean = sp.clean_service({"vehicle_id": VID, "items": ["engine_oil", "gearbox_oil"], "done_date": "2026-10-20", "odometer_km": 54000}, today=TODAY)
    payload = sp.service_payload(clean, clean["items"])
    event = {"id": "e1", "vehicle_id": VID, "event_date": "2026-10-20", "mileage": 54000, "source": "manual",
             "bill_line_items": [{"description": ln["description"]} for ln in payload["line_items"]]}
    hits = sp.bill_hits([event])
    assert sorted(h["item_key"] for h in hits) == ["engine_oil", "gearbox_oil"]
    assert all(h["source"] == "manual" and h["odometer_km"] == 54000 and h["event_id"] == "e1" for h in hits)


def test_things_already_recorded_that_day_are_not_recorded_twice():
    hits = [{"vehicle_id": VID, "item_key": "engine_oil", "done_date": "2026-10-20"},
            {"vehicle_id": VID, "item_key": "coolant", "done_date": "2026-10-19"},
            {"vehicle_id": "other", "item_key": "gearbox_oil", "done_date": "2026-10-20"}]
    assert sp.drop_done_already(["engine_oil", "coolant", "gearbox_oil"], VID, date(2026, 10, 20), hits) == ["coolant", "gearbox_oil"]
    assert sp.drop_done_already(["engine_oil"], VID, date(2026, 10, 20), hits) == []


# ------------------------------------------------------------------ deleting
class FakeDb:
    SupabaseError = supabase.SupabaseError           # events.py catches supabase.SupabaseError

    def __init__(self, source="manual", closes=(), paths=()):
        self.source, self.closes, self.paths = source, list(closes), list(paths)
        self.deleted, self.storage_deleted = [], []

    def select(self, table, params=None):
        if table == "maintenance_events":
            return [{"id": VID, "source": self.source}] if self.source else []
        if table == "repair_requests":
            return [{"seq": s} for s in self.closes]
        if table == "event_attachments":
            return [{"storage_path": p} for p in self.paths]
        raise AssertionError(table)

    def delete(self, table, match):
        self.deleted.append((table, match))

    def storage_delete(self, paths):
        self.storage_deleted.extend(paths)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(events, "supabase", fake)
    return fake


def test_a_manual_record_can_be_deleted_with_its_photos(db):
    db.paths = ["a/1.jpg"]
    events.delete_event(VID)
    assert db.deleted == [("maintenance_events", {"id": f"eq.{VID}"})] and db.storage_deleted == ["a/1.jpg"]


def test_a_bill_cannot_be_deleted(db):
    db.source = "bill"
    with pytest.raises(events.InvalidEvent, match="บิล"):
        events.delete_event(VID)
    assert db.deleted == []


def test_a_record_that_closed_a_board_request_cannot_be_deleted(db):
    db.closes = [7, 9]
    with pytest.raises(events.InvalidEvent, match="#7, #9"):
        events.delete_event(VID)
    assert db.deleted == []


def test_deleting_something_that_is_not_there(db):
    db.source = None
    with pytest.raises(events.EventNotFound):
        events.delete_event(VID)
    with pytest.raises(events.EventNotFound):
        events.delete_event("not-a-uuid")


def test_photos_that_fail_to_delete_do_not_undo_the_delete(monkeypatch):
    class Boom(FakeDb):
        def storage_delete(self, paths):
            raise supabase.SupabaseError("boom")

    fake = Boom(paths=["a/1.jpg"])
    monkeypatch.setattr(events, "supabase", fake)
    events.delete_event(VID)
    assert fake.deleted
