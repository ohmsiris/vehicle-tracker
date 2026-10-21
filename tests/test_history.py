import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import history, supabase


def L(desc, cat="ไส้กรองและของเหลว", total=100, event_id="e1", n=1):
    return {"event_id": event_id, "line_number": n, "description": desc, "category": cat, "line_total": total}


# ------------------------------------------------------------------ preview
def test_preview_names_the_biggest_parts_first_and_counts_the_rest():
    lines = [L("กรองเครื่อง", total=500), L("น้ำมันเครื่อง", total=1800), L("จาระบี", total=900), L("ซีล", total=200), L("น็อต", total=50), L("ปะเก็น", total=40)]
    assert history.preview(lines) == "น้ำมันเครื่อง, จาระบี, กรองเครื่อง, ซีล +2"


def test_preview_puts_labour_last_even_when_it_is_the_biggest_line():
    lines = [L("ค่าแรงซ่อม", cat="ค่าแรง", total=5000), L("ผ้าเบรก", cat="เบรก", total=1200)]
    assert history.preview(lines) == "ผ้าเบรก, ค่าแรงซ่อม"


def test_preview_handles_blank_missing_and_empty():
    assert history.preview([]) == ""
    assert history.preview([L("   "), L(""), {"description": None}]) == ""
    assert history.preview([{"description": " ซีล ", "category": None, "line_total": None}]) == "ซีล"


def test_preview_exactly_at_the_limit_has_no_plus():
    assert history.preview([L(f"x{i}", total=10 - i) for i in range(4)]) == "x0, x1, x2, x3"


# ------------------------------------------------------------ vehicle rows
def test_vehicle_rows_combine_identity_status_and_lifetime_totals():
    vehicles = [{"id": "v1", "plate_number": "84-4786", "plate_province": "สระบุรี", "vehicle_number": "36", "make": "Hino", "model": "Mega 500", "branch": "สระบุรี"},
                {"id": "v2", "plate_number": "81-3195", "plate_province": None, "vehicle_number": None, "make": None, "model": None, "branch": None}]
    status = {"v1": {"open_requests": 2, "awaiting_bill_requests": 1, "last_service_date": "2026-10-03", "days_since_service": 17,
                     "last_known_mileage": 516000, "next_service_mileage": 526000}}
    events = [{"vehicle_id": "v1", "total_cost": 1000}, {"vehicle_id": "v1", "total_cost": "250.5"}, {"vehicle_id": "v1", "total_cost": None}]
    rows = {r["plate"]: r for r in history.vehicle_rows(vehicles, status, events)}
    a, b = rows["84-4786"], rows["81-3195"]
    assert (a["repairs_total"], a["spend_total"], a["open_requests"], a["awaiting_bill_requests"]) == (3, 1250.5, 2, 1)
    assert (a["last_service_date"], a["days_since_service"], a["truck_number"], a["make"]) == ("2026-10-03", 17, "36", "Hino")
    # a truck with no status row and no events: zeros / empty strings, never None-crashes
    assert (b["repairs_total"], b["spend_total"], b["open_requests"], b["last_service_date"], b["truck_number"], b["branch"]) == (0, 0.0, 0, None, "", "")


# ------------------------------------------------------------- event rows
def test_event_rows_attach_preview_and_counts_per_event():
    events = [{"id": "e1", "event_date": "2026-10-03", "vendor": "ชมรม", "source": "bill", "total_cost": 3000, "low_confidence_fields": ["mileage"]},
              {"id": "e2", "event_date": "2026-09-01", "performed_by": "ช่างเอ๋", "source": None, "total_cost": None, "low_confidence_fields": []}]
    lines = [L("ผ้าเบรก", cat="เบรก", event_id="e1", total=900), L("ค่าแรง", cat="ค่าแรง", event_id="e1", total=500, n=2), L("น้ำมันเครื่อง", event_id="e2")]
    files = [{"event_id": "e1"}, {"event_id": "e1"}, {"event_id": "e1"}]
    rows = history.event_rows(events, lines, files)
    assert [(r["event_id"], r["preview"], r["line_count"], r["file_count"], r["needs_review"], r["source"]) for r in rows] == [
        ("e1", "ผ้าเบรก, ค่าแรง", 2, 3, True, "bill"), ("e2", "น้ำมันเครื่อง", 1, 0, False, "bill")]
    assert rows[1]["performed_by"] == "ช่างเอ๋"


# --------------------------------------------------------- database layer
class FakeDb:
    def __init__(self):
        self.calls = []
        self.events, self.lines, self.atts, self.requests = [], [], [], []
        self.event_row = None

    def select_all(self, table, params, page=1000):
        self.calls.append((table, dict(params)))
        if table == "maintenance_events":
            return self.events
        in_ids = (params.get("event_id") or "")
        if table == "bill_line_items":
            if in_ids.startswith("in.("):
                ids = set(in_ids[4:-1].split(","))
                return [l for l in self.lines if l["event_id"] in ids]
            return [l for l in self.lines if l["event_id"] == in_ids[3:]]
        if table == "event_attachments":
            if in_ids.startswith("in.("):
                ids = set(in_ids[4:-1].split(","))
                return [a for a in self.atts if a["event_id"] in ids]
            return [a for a in self.atts if a.get("event_id") == in_ids[3:]]
        raise AssertionError(table)

    def select(self, table, params=None):
        self.calls.append((table, dict(params or {})))
        if table == "maintenance_events":
            return [self.event_row] if self.event_row else []
        if table == "repair_requests":
            return self.requests
        raise AssertionError(table)


@pytest.fixture
def db(monkeypatch):
    d = FakeDb()
    monkeypatch.setattr(history.supabase, "select_all", d.select_all)
    monkeypatch.setattr(history.supabase, "select", d.select)
    monkeypatch.setattr(history.supabase, "public_url", lambda path: f"https://x/{path}")
    monkeypatch.setattr(history, "_has_summary", lambda: True)      # the probe has its own tests below
    return d


def test_vehicle_events_only_asks_for_confirmed_events_of_that_truck(db):
    vid = str(uuid.uuid4())
    history.vehicle_events(vid)
    table, params = db.calls[0]
    assert table == "maintenance_events" and params["vehicle_id"] == f"eq.{vid}" and params["status"] == "eq.confirmed"
    assert params["order"].startswith("event_date.desc")


def test_vehicle_events_chunks_the_in_filters_so_urls_stay_short(db):
    db.events = [{"id": f"e{i:03}", "event_date": "2026-10-01", "total_cost": 1, "low_confidence_fields": []} for i in range(120)]
    db.lines = [L("x", event_id=f"e{i:03}") for i in range(120)]
    rows = history.vehicle_events(str(uuid.uuid4()))
    assert len(rows) == 120 and all(r["line_count"] == 1 for r in rows)
    line_queries = [p for t, p in db.calls if t == "bill_line_items"]
    assert len(line_queries) == 3 and all(p["event_id"].count(",") + 1 <= history.CHUNK for p in line_queries)   # 120 ids -> 50 + 50 + 20


def test_vehicle_events_for_a_truck_without_history_makes_no_line_queries(db):
    assert history.vehicle_events(str(uuid.uuid4())) == []
    assert [t for t, _ in db.calls] == ["maintenance_events"]


@pytest.mark.parametrize("bad", ["nope", "123", "' or 1=1 --", "00000000-0000-0000-0000-00000000000g", ""])
def test_malformed_ids_are_not_found_and_never_reach_the_database(db, bad):
    with pytest.raises(history.NotFound):
        history.vehicle_events(bad)
    with pytest.raises(history.NotFound):
        history.event_detail(bad)
    assert db.calls == []


def test_event_detail_shape(db):
    eid = str(uuid.uuid4())
    db.event_row = {"id": eid, "event_date": "2026-10-03", "created_at": "2026-10-03T05:00:00+00:00", "vendor": "ชมรม", "source": "bill", "total_cost": 3000, "vehicle_id": "v1",
                    "low_confidence_fields": ["event_date", "vendor"], "vehicles": {"plate_number": "84-4786", "plate_province": None, "vehicle_number": "36", "branch": "สระบุรี", "make": "Hino", "model": None}}
    db.lines = [{"event_id": eid, "line_number": 1, "description": "ผ้าเบรก", "line_total": 2000}, {"event_id": eid, "line_number": 2, "description": "ค่าแรง", "line_total": 1000}]
    db.atts = [{"event_id": eid, "id": "a1", "type": "bill", "mime_type": "image/jpeg", "storage_path": f"{eid}/a1.jpg"}]
    db.requests = [{"seq": 12, "problem": "เบรกหลังมีเสียง"}]
    d = history.event_detail(eid)
    assert d["vehicle"] == {"vehicle_id": "v1", "plate": "84-4786", "province": "", "truck_number": "36", "branch": "สระบุรี", "make": "Hino", "model": ""}
    assert [l["description"] for l in d["lines"]] == ["ผ้าเบรก", "ค่าแรง"]
    assert d["attachments"] == [{"id": "a1", "type": "bill", "mime_type": "image/jpeg", "url": f"https://x/{eid}/a1.jpg"}]
    assert d["requests"] == [{"seq": 12, "problem": "เบรกหลังมีเสียง"}] and d["event"]["needs_review"] is False
    assert "vehicles" not in d["event"]
    # the event query is restricted to confirmed events
    assert any(p.get("status") == "eq.confirmed" for t, p in db.calls if t == "maintenance_events")


def test_event_detail_missing_or_unconfirmed_is_not_found(db):
    with pytest.raises(history.NotFound):
        history.event_detail(str(uuid.uuid4()))


# ----------------------------------------------------------------- endpoints
def _client(monkeypatch, configured=True):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: configured)
    return main, TestClient(main.app)


def test_history_endpoints_auth_and_error_mapping(monkeypatch):
    main, client = _client(monkeypatch)
    for path in ("/api/vehicles", f"/api/vehicles/{uuid.uuid4()}/events", f"/api/events/{uuid.uuid4()}"):
        assert client.get(path).status_code == 403, path

    monkeypatch.setattr(main.history, "list_vehicles", lambda: [{"plate": "A"}])
    assert client.get("/api/vehicles?token=secret").json() == [{"plate": "A"}]

    def missing(*a): raise main.history.NotFound()
    monkeypatch.setattr(main.history, "event_detail", missing)
    assert client.get(f"/api/events/{uuid.uuid4()}?token=secret").status_code == 404

    def boom(*a): raise supabase.SupabaseError("secret internals")
    monkeypatch.setattr(main.history, "vehicle_events", boom)
    r = client.get(f"/api/vehicles/{uuid.uuid4()}/events?token=secret")
    assert r.status_code == 502 and "internals" not in r.text


def test_history_endpoints_503_when_unconfigured(monkeypatch):
    main, client = _client(monkeypatch, configured=False)
    assert client.get("/api/vehicles?token=secret").status_code == 503


# ------------------------------------------------------------ review reasons
def ev_(**o):
    return {"event_date": "2026-10-10", "created_at": "2026-10-20T05:00:00+00:00", "total_cost": 3000, "low_confidence_fields": [], **o}


def test_a_consistent_bill_needs_no_review_even_if_the_ai_once_doubted_it():
    lines = [{"line_total": 2000}, {"line_total": 1000}]
    assert history.review_reasons(ev_(low_confidence_fields=["event_date", "total_cost", "vendor", "branch"]), lines) == []


def test_total_that_disagrees_with_the_lines_is_flagged_and_clears_when_fixed():
    r = history.review_reasons(ev_(total_cost=4000), [{"line_total": 2000}, {"line_total": 1000}])
    assert len(r) == 1 and "ยอดรวมไม่ตรงกับรายการ" in r[0] and "3,000.00" in r[0] and "4,000.00" in r[0]
    assert history.review_reasons(ev_(total_cost=3000), [{"line_total": 2000}, {"line_total": 1000}]) == []


def test_a_rounding_difference_is_not_a_problem():
    assert history.review_reasons(ev_(total_cost=3000.6), [{"line_total": 2000}, {"line_total": 1000}]) == []


def test_missing_total_or_missing_lines():
    assert history.review_reasons(ev_(total_cost=None), [{"line_total": 500}]) == []          # nothing to compare against
    assert any("ไม่มีรายการ" in r for r in history.review_reasons(ev_(), []))


def test_a_date_far_from_when_it_was_saved_is_flagged():
    lines = [{"line_total": 3000}]
    assert history.review_reasons(ev_(event_date="2024-10-18"), lines) != []                  # the "2567 read as 2024" case
    assert history.review_reasons(ev_(event_date="2026-10-23"), lines) != []                  # days in the future
    assert history.review_reasons(ev_(event_date="2025-10-21"), lines) == []                  # a year old: plausible (late bill)
    assert history.review_reasons(ev_(event_date="2026-10-21", created_at="2026-10-21T01:00:00+00:00"), lines) == []   # same day
    assert history.review_reasons(ev_(event_date="garbage"), lines) == ["วันที่ไม่ถูกต้อง"]


def test_event_rows_expose_the_reasons():
    rows = history.event_rows([{"id": "e1", "event_date": "2026-10-10", "created_at": "2026-10-20T00:00:00+00:00", "total_cost": 500, "low_confidence_fields": ["x"]},
                               {"id": "e2", "event_date": "2026-10-10", "created_at": "2026-10-20T00:00:00+00:00", "total_cost": 500, "low_confidence_fields": ["x"]}],
                              [{"event_id": "e1", "line_total": 100, "description": "a"}, {"event_id": "e2", "line_total": 500, "description": "b"}], [])
    assert [(r["needs_review"], len(r["review_reasons"])) for r in rows] == [(True, 1), (False, 0)]
