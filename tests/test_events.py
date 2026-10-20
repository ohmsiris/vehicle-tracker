import io
import json
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import events, supabase

VEHICLE = str(uuid.uuid4())


def jpeg(color="white", size=(60, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()


class FakeSupabase:
    """Stands in for app.supabase: records every call, fails on demand."""

    def __init__(self):
        self.uploads, self.deleted, self.rpcs = [], [], []
        self.vehicle_exists = True
        self.existing_attachments = []   # rows returned for the duplicate check
        self.fail_rpc = False
        self.fail_upload_at = None       # 0-based upload number to fail on

    def select(self, table, params=None):
        if table == "vehicles":
            return [{"id": VEHICLE}] if self.vehicle_exists else []
        if table == "event_attachments":
            return self.existing_attachments
        raise AssertionError(table)

    def storage_upload(self, path, data, content_type):
        if self.fail_upload_at is not None and len(self.uploads) == self.fail_upload_at:
            raise supabase.SupabaseError("upload boom")
        self.uploads.append((path, content_type, len(data)))

    def storage_delete(self, paths):
        self.deleted.extend(paths)

    def rpc(self, fn, args):
        if self.fail_rpc:
            raise supabase.SupabaseError("rpc boom")
        self.rpcs.append((fn, args))
        return args["p"]["event_id"]

    def public_url(self, path):
        return f"https://x.supabase.co/storage/v1/object/public/event-files/{path}"


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase()
    for name in ("select", "storage_upload", "storage_delete", "rpc", "public_url"):
        monkeypatch.setattr(events.supabase, name, getattr(f, name))
    return f


def payload(**over):
    base = {
        "vehicle_id": VEHICLE,
        "source": "bill",
        "event": {"event_date": date.today().isoformat(), "mileage": 650316, "vendor": "อู่", "total_cost": 3000,
                  "low_confidence_fields": ["mileage"]},
        "line_items": [
            {"file_index": 0, "description": "ผ้าเบรก", "category": "เบรก", "quantity": 2, "unit": "ชิ้น", "unit_cost": 1000, "line_total": 2000},
            {"file_index": 0, "description": "ค่าแรง", "category": "ค่าแรง", "line_total": 1000},
        ],
        "attachments": [{"type": "bill", "filename": "a.jpg"}, {"type": "reference_photo", "filename": "b.jpg"}],
        "bill_extractions": {"0": {"shop_name": "อู่"}},
    }
    base.update(over)
    return base


FILES = [("a.jpg", jpeg("white")), ("b.jpg", jpeg("gray"))]


def test_happy_path_uploads_then_saves_atomically(fake):
    result = events.save_event(payload(), FILES)
    assert len(fake.uploads) == 2 and len(fake.rpcs) == 1
    fn, args = fake.rpcs[0]
    p = args["p"]
    assert fn == "save_maintenance_event" and p["event_id"] == result["event_id"]
    # every stored object lives under <event_id>/ and has a matching attachment row
    paths = [a["storage_path"] for a in p["attachments"]]
    assert paths == [u[0] for u in fake.uploads]
    assert all(path.startswith(result["event_id"] + "/") and path.endswith(".jpg") for path in paths)
    # lines point at the bill attachment's id; numbering is 1..n
    bill_id = p["attachments"][0]["id"]
    assert [ln["attachment_id"] for ln in p["line_items"]] == [bill_id, bill_id]
    assert [ln["line_number"] for ln in p["line_items"]] == [1, 2]
    # the raw extraction travels with the bill, not the reference photo
    assert p["attachments"][0]["extraction"] == {"shop_name": "อู่"} and p["attachments"][1]["extraction"] is None
    assert p["low_confidence_fields"] == ["mileage"]
    assert [a["type"] for a in result["attachments"]] == ["bill", "reference_photo"]
    assert fake.deleted == []


def test_rpc_failure_removes_uploaded_files(fake):
    fake.fail_rpc = True
    with pytest.raises(supabase.SupabaseError):
        events.save_event(payload(), FILES)
    assert sorted(fake.deleted) == sorted(u[0] for u in fake.uploads) and len(fake.deleted) == 2


def test_upload_failure_removes_the_ones_already_uploaded(fake):
    fake.fail_upload_at = 1
    with pytest.raises(supabase.SupabaseError):
        events.save_event(payload(), FILES)
    assert fake.rpcs == [] and fake.deleted == [fake.uploads[0][0]]


def test_duplicate_bill_is_caught_and_can_be_overridden(fake):
    fake.existing_attachments = [{"event_id": "evt-old", "sha256": __import__("hashlib").sha256(FILES[0][1]).hexdigest(), "created_at": "2026-10-01T00:00:00Z"}]
    with pytest.raises(events.DuplicateBill) as exc:
        events.save_event(payload(), FILES)
    assert exc.value.duplicates[0]["event_id"] == "evt-old" and exc.value.duplicates[0]["filename"] == "a.jpg"
    assert fake.uploads == [] and fake.rpcs == []          # nothing written
    events.save_event(payload(), FILES, allow_duplicates=True)
    assert len(fake.rpcs) == 1


def test_reference_photos_are_not_duplicate_checked(fake):
    fake.existing_attachments = [{"event_id": "x", "sha256": "irrelevant", "created_at": "t"}]
    events.save_event(payload(), FILES)   # hashes don't match, and refs are never looked up anyway
    assert len(fake.rpcs) == 1


def test_manual_entry_needs_no_files(fake):
    p = payload(source="manual", attachments=[], bill_extractions={},
                line_items=[{"description": "เปลี่ยนน้ำมันเครื่อง", "category": "ไส้กรองและของเหลว"}])
    p["event"]["performed_by"] = "ช่างเอ๋"
    events.save_event(p, [])
    sent = fake.rpcs[0][1]["p"]
    assert sent["source"] == "manual" and sent["performed_by"] == "ช่างเอ๋" and sent["attachments"] == []
    assert sent["line_items"][0]["attachment_id"] is None


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p.update(vehicle_id="nope"), "เลือกรถ"),
    (lambda p: p.update(source="weird"), "ประเภทรายการ"),
    (lambda p: p["event"].update(event_date="31/12/2026"), "วันที่ไม่ถูกต้อง"),
    (lambda p: p["event"].update(event_date=(date.today() + timedelta(days=30)).isoformat()), "อนาคต"),
    (lambda p: p["event"].update(mileage=-5), "ติดลบ"),
    (lambda p: p["event"].update(total_cost="abc"), "ไม่ถูกต้อง"),
    (lambda p: p.update(line_items=[]), "อย่างน้อย 1 รายการ"),
    (lambda p: p["line_items"][0].update(description="  "), "ยังไม่ได้ใส่ชื่อ"),
    (lambda p: p["line_items"][0].update(category="ของแปลก"), "หมวด"),
    (lambda p: p["line_items"][0].update(file_index=1), "ไม่มีอยู่"),     # index 1 is a reference photo
    (lambda p: p["line_items"][0].update(file_index=9), "ไม่มีอยู่"),
    (lambda p: p.update(attachments=[{"type": "reference_photo", "filename": "a.jpg"}, {"type": "reference_photo", "filename": "b.jpg"}]), "ไฟล์บิล"),
    (lambda p: p.update(attachments=[{"type": "bill", "filename": "a.jpg"}]), "จำนวนไฟล์"),
])
def test_validation_rejects_bad_payloads_before_touching_anything(fake, mutate, fragment):
    p = payload()
    mutate(p)
    # line_items referencing a ref photo must be rejected even when counts match
    with pytest.raises(events.InvalidEvent) as exc:
        events.save_event(p, FILES)
    assert fragment in str(exc.value)
    assert fake.uploads == [] and fake.rpcs == []


def test_unknown_vehicle_rejected(fake):
    fake.vehicle_exists = False
    with pytest.raises(events.InvalidEvent):
        events.save_event(payload(), FILES)
    assert fake.uploads == []


def test_non_image_file_rejected_with_its_name(fake):
    with pytest.raises(events.InvalidEvent) as exc:
        events.save_event(payload(), [("a.jpg", b"not an image"), FILES[1]])
    assert "a.jpg" in str(exc.value) and fake.uploads == []


# ------------------------------------------------------------------ endpoint
def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    return main, TestClient(main.app)


def _post(client, p, files=FILES, **extra):
    return client.post(
        "/api/events?token=secret",
        data={"payload": json.dumps(p), **extra},
        files=[("files", (n, d, "image/jpeg")) for n, d in files],
    )


def test_endpoint_status_mapping(monkeypatch):
    main, client = _client(monkeypatch)

    monkeypatch.setattr(main.events, "save_event", lambda data, raws, allow_duplicates=False: {"event_id": "e1", "attachments": []})
    assert _post(client, payload()).json() == {"event_id": "e1", "attachments": []}
    assert client.post("/api/events", data={"payload": "{}"}).status_code == 403
    assert client.post("/api/events?token=secret", data={"payload": "not json"}).status_code == 400

    def invalid(*a, **k): raise events.InvalidEvent("ยังไม่ได้เลือกรถ")
    monkeypatch.setattr(main.events, "save_event", invalid)
    r = _post(client, payload())
    assert r.status_code == 422 and "เลือกรถ" in r.json()["detail"]

    def dup(*a, **k): raise events.DuplicateBill([{"filename": "a.jpg", "event_id": "old", "saved_at": "t"}])
    monkeypatch.setattr(main.events, "save_event", dup)
    r = _post(client, payload())
    assert r.status_code == 409 and r.json()["duplicates"][0]["event_id"] == "old"

    def boom(*a, **k): raise supabase.SupabaseError("secret internals")
    monkeypatch.setattr(main.events, "save_event", boom)
    r = _post(client, payload())
    assert r.status_code == 502 and "internals" not in r.text     # details stay in the log, not the response


def test_endpoint_passes_allow_duplicates_flag(monkeypatch):
    main, client = _client(monkeypatch)
    seen = {}
    monkeypatch.setattr(main.events, "save_event", lambda data, raws, allow_duplicates=False: seen.update(flag=allow_duplicates) or {"event_id": "e", "attachments": []})
    _post(client, payload(), allow_duplicates="true")
    assert seen["flag"] is True


def test_endpoint_503_when_supabase_unconfigured():
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    r = TestClient(main.app).post("/api/events?token=secret", data={"payload": "{}"})
    assert r.status_code == 503


def test_same_file_twice_in_one_batch_is_a_clear_error_not_a_db_failure(fake):
    same = jpeg("white")
    p = payload(source="manual", bill_extractions={},
                attachments=[{"type": "reference_photo", "filename": "a.jpg"}, {"type": "reference_photo", "filename": "b.jpg"}],
                line_items=[{"description": "x"}])
    with pytest.raises(events.InvalidEvent) as exc:
        events.save_event(p, [("a.jpg", same), ("b.jpg", same)])
    assert "b.jpg" in str(exc.value) and "a.jpg" in str(exc.value)
    assert fake.uploads == [] and fake.rpcs == []
