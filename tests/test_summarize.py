import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import summarize, supabase


class FakeClient:
    """Stands in for the Anthropic client; answers (or fails) from a queue."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"summary": a}) if not a.startswith("{") else a)])


@pytest.fixture
def client(monkeypatch):
    def install(*answers):
        c = FakeClient(answers)
        monkeypatch.setattr(summarize, "get_client", lambda: c)
        return c
    return install


LINES = [{"description": "น้ำมันเครื่อง", "category": "ไส้กรองและของเหลว", "quantity": 12, "unit": "ลิตร"},
         {"description": "กรองเครื่อง", "category": "ไส้กรองและของเหลว", "quantity": 1, "unit": "ลูก"},
         {"description": "ค่าแรง", "category": "ค่าแรง", "quantity": None, "unit": None}]


# ------------------------------------------------------------- summarize()
def test_returns_the_models_line_cleaned_up(client):
    client('  "ถ่ายน้ำมันเครื่อง เปลี่ยนไส้กรอง".  \n')
    assert summarize.summarize(LINES) == "ถ่ายน้ำมันเครื่อง เปลี่ยนไส้กรอง"


def test_prompt_contains_the_lines_but_no_prices(client):
    c = client("x")
    summarize.summarize([{**LINES[0], "line_total": 1800, "unit_cost": 150}], {"performed_by": "ช่างเอ๋"})
    prompt = c.calls[0]["messages"][0]["content"]
    assert "น้ำมันเครื่อง" in prompt and "x12 ลิตร" in prompt and "ช่างเอ๋" in prompt
    assert "1800" not in prompt and "150" not in prompt            # money never goes to the model


def test_a_very_long_answer_is_capped(client):
    client("ก" * 400)
    assert len(summarize.summarize(LINES)) <= summarize.MAX_SUMMARY_CHARS


def test_nothing_to_summarize_makes_no_call(client):
    c = client()
    assert summarize.summarize([]) is None
    assert summarize.summarize([{"description": "  "}, {"description": None}]) is None
    assert c.calls == []


def test_empty_answer_means_none(client):
    client("")
    assert summarize.summarize(LINES) is None


def test_retries_once_then_gives_up_quietly(client):
    c = client(RuntimeError("api down"), "ถ่ายน้ำมัน")
    assert summarize.summarize(LINES) == "ถ่ายน้ำมัน" and len(c.calls) == 2
    c2 = client(RuntimeError("down"), RuntimeError("still down"))
    assert summarize.summarize(LINES) is None and len(c2.calls) == summarize.MAX_ATTEMPTS      # never raises


def test_garbage_json_is_treated_as_failure(client):
    client("{not json", "{not json either")
    assert summarize.summarize(LINES) is None


def test_only_the_first_80_lines_are_sent(client):
    c = client("x")
    summarize.summarize([{"description": f"รายการ {i}"} for i in range(200)])
    assert c.calls[0]["messages"][0]["content"].count("\n") <= summarize.MAX_LINES + 1


# ------------------------------------------------------------ fill_summary()
class FakeDb:
    def __init__(self, summary=None, has_column=True):
        self.event = {"id": "e1", "summary": summary, "performed_by": None}
        self.updates = []
        self.update_result = [{"id": "e1"}]
        self.has_column = has_column

    def select(self, table, params=None):
        assert table == "maintenance_events"
        return [self.event]

    def select_all(self, table, params, page=1000):
        assert table == "bill_line_items"
        return LINES

    def update(self, table, match, values):
        self.updates.append((match, values))
        return self.update_result


@pytest.fixture
def db(monkeypatch):
    d = FakeDb()
    monkeypatch.setattr(summarize.supabase, "select", d.select)
    monkeypatch.setattr(summarize.supabase, "select_all", d.select_all)
    monkeypatch.setattr(summarize.supabase, "update", d.update)
    from app import history
    monkeypatch.setattr(history, "_has_summary", lambda: d.has_column)
    return d


def test_fill_writes_into_an_empty_summary_conditionally(db, client):
    client("ถ่ายน้ำมันเครื่อง")
    assert summarize.fill_summary("e1") == "ถ่ายน้ำมันเครื่อง"
    (match, values), = db.updates
    assert match == {"id": "eq.e1", "summary": "is.null"}          # only if STILL empty when written
    assert values == {"summary": "ถ่ายน้ำมันเครื่อง"}


def test_fill_never_overwrites_a_summary_a_person_typed(db, client):
    db.event["summary"] = "เปลี่ยนเบรกหลัง"
    c = client("something else")
    assert summarize.fill_summary("e1") is None
    assert db.updates == [] and c.calls == []                       # the model isn't even asked


def test_fill_treats_whitespace_only_as_empty(db, client):
    db.event["summary"] = "   "
    client("ถ่ายน้ำมัน")
    assert summarize.fill_summary("e1") == "ถ่ายน้ำมัน"


def test_fill_loses_a_race_gracefully(db, client):
    client("AI text")
    db.update_result = []                                           # someone saved their own summary meanwhile
    assert summarize.fill_summary("e1") is None


def test_fill_does_nothing_before_schema_v4(db, client):
    db.has_column = False
    c = client("x")
    assert summarize.fill_summary("e1") is None and c.calls == [] and db.updates == []


def test_fill_leaves_it_empty_when_the_model_has_nothing(db, client):
    client(RuntimeError("down"), RuntimeError("down"))
    assert summarize.fill_summary("e1") is None and db.updates == []


# ---------------------------------------------------------------- endpoints
def _client(monkeypatch, key="k"):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    object.__setattr__(main.settings, "anthropic_api_key", key)
    return main, TestClient(main.app)


def test_summarize_endpoint(monkeypatch):
    main, c = _client(monkeypatch)
    body = {"lines": [{"description": "น้ำมันเครื่อง", "quantity": 12, "unit": "ลิตร"}], "performed_by": "ช่างเอ๋"}
    assert c.post("/api/summarize", json=body).status_code == 403
    seen = {}
    monkeypatch.setattr(main.summarize, "summarize", lambda lines, ctx=None: seen.update(lines=lines, ctx=ctx) or "ถ่ายน้ำมันเครื่อง")
    r = c.post("/api/summarize?token=secret", json=body)
    assert r.status_code == 200 and r.json() == {"summary": "ถ่ายน้ำมันเครื่อง"}
    assert seen["lines"][0]["description"] == "น้ำมันเครื่อง" and seen["ctx"] == {"performed_by": "ช่างเอ๋"}
    monkeypatch.setattr(main.summarize, "summarize", lambda *a, **k: None)
    assert c.post("/api/summarize?token=secret", json=body).status_code == 502
    assert c.post("/api/summarize?token=secret", json={"lines": [{"description": "x"}] * 81}).status_code == 422
    object.__setattr__(main.settings, "anthropic_api_key", "")
    assert c.post("/api/summarize?token=secret", json=body).status_code == 503


def test_saving_a_bill_schedules_the_summary_after_replying_and_survives_its_failure(monkeypatch):
    main, c = _client(monkeypatch)
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    monkeypatch.setattr(main.events, "save_event", lambda data, raws, allow_duplicates=False: {"event_id": "e9", "attachments": []})
    calls = []

    def boom(event_id):
        calls.append(event_id)
        raise RuntimeError("model exploded")
    monkeypatch.setattr(main.summarize, "fill_summary", boom)
    r = c.post("/api/events?token=secret", data={"payload": "{}"})
    assert r.status_code == 200 and r.json()["event_id"] == "e9"        # the save is unaffected by the summary failing
    assert calls == ["e9"]

    calls.clear()
    object.__setattr__(main.settings, "anthropic_api_key", "")           # no key configured: no summary attempted
    assert c.post("/api/events?token=secret", data={"payload": "{}"}).status_code == 200 and calls == []
