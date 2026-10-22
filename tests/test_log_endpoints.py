import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import logbook, logsource, supabase

from tests.test_logbook import build, ev, ln, rq  # noqa: E402  (shared fixtures)


def _client(monkeypatch, rows=None, configured=True):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: configured)
    if rows is not None:
        monkeypatch.setattr(main.logsource, "load_rows", lambda: rows)
    return main, TestClient(main.app)


@pytest.fixture
def rows():
    return build(
        events=[ev("e1", "v1", "2026-10-10", vendor="ชมรมย์การช่าง", total=3000), ev("e2", "v2", "2026-09-20", vendor="โชคพัฒนา", total=500)],
        lines=[ln("e1", "น้ำมันเครื่อง", "ไส้กรองและของเหลว", 3000), ln("e2", "ลูกปืน", "ช่วงล่าง", 500)],
        requests=[rq("r1", "v1", "at_shop", seq=1, shop="24 ไดนาโม", sent_date="2026-10-12")])


def test_log_requires_the_token(monkeypatch, rows):
    main, c = _client(monkeypatch, rows)
    assert c.get("/api/log").status_code == 403
    assert c.get("/api/log.csv").status_code == 403
    assert c.get("/api/log?token=wrong").status_code == 403


def test_log_returns_rows_totals_and_facets(monkeypatch, rows):
    main, c = _client(monkeypatch, rows)
    d = c.get("/api/log?token=secret").json()
    assert d["total"] == 3 and len(d["rows"]) == 3 and d["rows"][0]["key"] == "request:r1"
    assert d["stats"]["total_cost"] == 3500 and d["stats"]["trucks"] == 2
    assert {s["value"] for s in d["facets"]["shops"]} >= {"ชมรมย์การช่าง", "โชคพัฒนา", "24 ไดนาโม", "ฮีโน่ เอเซีย หนองแค"}
    assert all("_hay" not in r for r in d["rows"]) and d["rows"][0]["status_label"] == "อยู่ที่อู่"


def test_every_filter_is_wired_through_the_url(monkeypatch, rows):
    main, c = _client(monkeypatch, rows)
    get = lambda qs: [r["key"] for r in c.get(f"/api/log?token=secret&{qs}").json()["rows"]]
    assert get("q=ลูกปืน") == ["event:e2"]
    assert get("status=active") == ["request:r1"]
    assert get("shop=ชมรมย์การช่าง") == ["event:e1"]
    assert get("branch=แก่งคอย") == ["event:e2"]
    assert get("category=ช่วงล่าง") == ["event:e2"]
    assert get("date_from=2026-10-01&date_to=2026-10-10") == ["event:e1"]
    assert get("min_cost=1000") == ["event:e1"]
    assert get("source=request") == ["request:r1"]
    assert get("vehicle_id=v2") == ["event:e2"]
    assert get("sort=cost_desc")[0] == "event:e1" and get("sort=date_asc")[0] == "event:e2"
    assert len(get("limit=1")) == 1 and get("limit=1&offset=1") != get("limit=1")


def test_csv_download_is_a_spreadsheet_with_a_bom_and_respects_filters(monkeypatch, rows):
    main, c = _client(monkeypatch, rows)
    r = c.get("/api/log.csv?token=secret&shop=ชมรมย์การช่าง")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    assert r.content.startswith("﻿".encode("utf-8"))                       # Excel needs it for the Thai text
    text = r.content.decode("utf-8-sig")
    assert "ชมรมย์การช่าง" in text and "โชคพัฒนา" not in text                  # only the filtered rows
    assert text.splitlines()[0].startswith(logbook.CSV_HEADER[0])


def test_csv_is_not_limited_to_one_page(monkeypatch):
    many = build(events=[ev(f"e{i}", "v1", "2026-10-10", vendor="x", total=1) for i in range(600)])
    main, c = _client(monkeypatch, many)
    assert len(c.get("/api/log?token=secret&limit=500").json()["rows"]) == 500
    assert len(c.get("/api/log.csv?token=secret").content.decode("utf-8-sig").splitlines()) == 601


def test_log_errors_are_mapped_without_leaking_details(monkeypatch):
    main, c = _client(monkeypatch)
    def boom(): raise supabase.SupabaseError("secret internals")
    monkeypatch.setattr(main.logsource, "load_rows", boom)
    r = c.get("/api/log?token=secret")
    assert r.status_code == 502 and "internals" not in r.text
    main2, c2 = _client(monkeypatch, configured=False)
    assert c2.get("/api/log?token=secret").status_code == 503


# ---------------------------------------------------------------- the cache
def test_rows_are_cached_briefly_and_every_write_clears_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(logsource.supabase, "select_all", lambda t, p, page=1000: calls.append(t) or [])
    logsource.invalidate()
    logsource.load_rows(); n = len(calls)
    logsource.load_rows(); assert len(calls) == n                              # second call served from the cache
    logsource.invalidate()
    logsource.load_rows(); assert len(calls) == 2 * n                          # a write forced a fresh read
    logsource._cache["at"] = time.time() - logsource.TTL_SECONDS - 1
    logsource.load_rows(); assert len(calls) == 3 * n                          # and it expires on its own


def test_saving_editing_or_moving_a_repair_clears_the_log_cache(monkeypatch):
    main, c = _client(monkeypatch)
    cleared = []
    monkeypatch.setattr(main.logsource, "invalidate", lambda: cleared.append(1))
    monkeypatch.setattr(main.events, "save_event", lambda *a, **k: {"event_id": "e1", "attachments": []})
    monkeypatch.setattr(main.events, "update_event", lambda *a, **k: None)
    monkeypatch.setattr(main.repair_requests, "create", lambda d: {"id": "r"})
    monkeypatch.setattr(main.repair_requests, "act", lambda *a, **k: {"id": "r"})
    monkeypatch.setattr(main.repair_requests, "edit", lambda *a, **k: {"id": "r"})
    assert c.post("/api/events?token=secret", data={"payload": "{}"}).status_code == 200
    assert c.patch("/api/events/00000000-0000-0000-0000-000000000000?token=secret", json={}).status_code == 200
    assert c.post("/api/requests?token=secret", json={"problem": "x"}).status_code == 200
    assert c.post("/api/requests/r/action?token=secret", json={"action": "cancel"}).status_code == 200
    assert c.patch("/api/requests/r?token=secret", json={"urgent": True}).status_code == 200
    assert len(cleared) == 5
