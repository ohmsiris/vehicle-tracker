import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import machinery as mc

M1 = "11111111-1111-1111-1111-111111111111"
M2 = "22222222-2222-2222-2222-222222222222"
P1, P2, P3, P4 = (f"0000000{n}-0000-0000-0000-000000000000" for n in "1234")
MACHINES = {M1: {"id": M1, "branch": "สระบุรี", "name": "เครื่อง 1", "kind": "เครื่อง", "number": 1, "make": "MYCOM", "active": True},
            M2: {"id": M2, "branch": "สระบุรี", "name": "เครื่องบ่อซอง Sabroe 30 ตัน", "kind": "บ่อซอง", "number": None, "make": "Sabroe", "capacity_tons": 30, "active": True}}


# ------------------------------------------------------------------ machines
def test_machine_names_follow_the_way_you_say_them():
    assert mc.machine_name({"kind": "เครื่อง", "number": 3}) == "เครื่อง 3"
    assert mc.machine_name({"kind": "บ่อซอง", "make": "Sabroe", "capacity_tons": 30}) == "เครื่องบ่อซอง Sabroe 30 ตัน"
    assert mc.machine_name({"kind": "บ่อซอง", "make": "MYCOM", "capacity_tons": 30.0}) == "เครื่องบ่อซอง MYCOM 30 ตัน"
    assert mc.machine_name({"kind": "บ่อซอง"}) == "เครื่องบ่อซอง"


def test_a_numbered_machine_needs_its_number_but_a_brine_pond_machine_does_not():
    v = mc.clean_machine({"branch": "สระบุรี", "kind": "เครื่อง", "number": "4", "tube_type": "หลอดเล็ก", "make": "Sabroe", "model": "SMC108L", "capacity_tons": "30"}, others=[])
    assert v["name"] == "เครื่อง 4" and v["number"] == 4 and v["capacity_tons"] == 30
    with pytest.raises(mc.InvalidMachinery) as exc:
        mc.clean_machine({"branch": "สระบุรี", "kind": "เครื่อง"}, others=[])
    assert "เลขเครื่อง" in str(exc.value)
    p = mc.clean_machine({"branch": "สระบุรี", "kind": "บ่อซอง", "make": "MYCOM", "capacity_tons": 30}, others=[])
    assert p["name"] == "เครื่องบ่อซอง MYCOM 30 ตัน" and p["number"] is None


def test_the_same_machine_number_twice_in_a_branch_is_refused_but_other_branches_are_fine():
    others = [{"id": "x", "branch": "สระบุรี", "kind": "เครื่อง", "number": 1, "active": True}]
    with pytest.raises(mc.InvalidMachinery) as exc:
        mc.clean_machine({"branch": "สระบุรี", "kind": "เครื่อง", "number": 1}, others=others)
    assert "มีอยู่แล้ว" in str(exc.value)
    assert mc.clean_machine({"branch": "แก่งคอย", "kind": "เครื่อง", "number": 1}, others=others)["number"] == 1


def test_machine_validation():
    for bad, fragment in (({"branch": ""}, "สาขา"), ({"branch": "ส", "kind": "อื่น"}, "ประเภทเครื่อง"), ({"branch": "ส", "number": "x"}, "ตัวเลข"),
                          ({"branch": "ส", "number": 1, "capacity_tons": -5}, "ขนาด")):
        with pytest.raises(mc.InvalidMachinery) as exc:
            mc.clean_machine(bad, others=[])
        assert fragment in str(exc.value)


def test_editing_a_machine_keeps_its_automatic_name_in_step_but_not_a_name_somebody_chose():
    auto = {"id": "a", "branch": "ส", "kind": "บ่อซอง", "make": "MYCOM", "capacity_tons": 30, "name": "เครื่องบ่อซอง MYCOM 30 ตัน"}
    assert mc.clean_machine({"make": "Sabroe"}, existing=auto, others=[])["name"] == "เครื่องบ่อซอง Sabroe 30 ตัน"
    custom = {**auto, "name": "บ่อซองเก่า"}
    assert "name" not in mc.clean_machine({"make": "Sabroe"}, existing=custom, others=[])
    assert mc.clean_machine({"name": " บ่อซองใหม่ "}, existing=auto, others=[])["name"] == "บ่อซองใหม่"


# --------------------------------------------------------------------- parts
def part(**kw):
    return mc.clean_part({"location": "เครื่อง 1", **kw}, machine_ids={M1, M2})


def test_a_part_is_cleaned_and_multi_line_cells_keep_their_lines():
    p = part(equipment=" พัดลม ", part="Magnetic Contactor", spec=" Fuji SC-N2 ", qty="2", contactor="Fuji SC-N8 (180) 220V\n\n Fuji SC-N6 (125) 220V ", motor_hp="7.5")
    assert p["equipment"] == "พัดลม" and p["qty"] == 2 and p["motor_hp"] == 7.5
    assert p["contactor"] == "Fuji SC-N8 (180) 220V\nFuji SC-N6 (125) 220V"


def test_a_part_needs_a_location_and_something_to_say():
    with pytest.raises(mc.InvalidMachinery):
        mc.clean_part({"location": " ", "part": "x"}, machine_ids=set())
    with pytest.raises(mc.InvalidMachinery) as exc:
        part()
    assert "อย่างน้อย" in str(exc.value)


def test_part_status_machine_and_numbers_are_checked():
    assert part(part="มอเตอร์", status="Good", machine_id=M1)["machine_id"] == M1
    assert part(part="มอเตอร์", machine_id="")["machine_id"] is None
    for bad, fragment in (({"status": "Great"}, "สถานะ"), ({"machine_id": "33333333-3333-3333-3333-333333333333"}, "ไม่พบเครื่อง"),
                          ({"machine_id": "nope"}, "รหัส"), ({"qty": "abc"}, "จำนวน"), ({"motor_hp": -1}, "แรงม้า")):
        with pytest.raises(mc.InvalidMachinery) as exc:
            part(part="มอเตอร์", **bad)
        assert fragment in str(exc.value)


def test_a_partial_part_edit_returns_only_what_was_sent_and_cannot_empty_the_row():
    existing = {"id": "p", "location": "เครื่อง 1", "part": "มอเตอร์", "spec": "x"}
    assert mc.clean_part({"qty": 3}, existing=existing, machine_ids=set()) == {"qty": 3}
    with pytest.raises(mc.InvalidMachinery):
        mc.clean_part({"part": "", "spec": ""}, existing=existing, machine_ids=set())


PARTS = [
    {"id": P1, "machine_id": M1, "location": "เครื่อง 1", "equipment": "พัดลม", "part": "Breaker", "spec": "Fuji BW50EAG 40A"},
    {"id": P2, "machine_id": M1, "location": "เครื่อง 1", "equipment": "ปั๊มน้ำเย็น", "part": "มอเตอร์", "spec": "Calpeda NM65/12C/B", "status": "Good"},
    {"id": P3, "machine_id": None, "location": "โรงซอง 1", "equipment": "โม่", "part": "สายพาน", "spec": "B-82"},
    {"id": P4, "machine_id": M2, "location": "ซอง", "equipment": "ปั๊มคูลลิ่ง 1", "part": "มอเตอร์", "spec": "Calpeda NM 50/16B/B"},
]


def ids(rows):
    return [p["id"][7] for p in rows]


def test_parts_search_needs_every_word_and_ignores_spaces_and_dashes():
    assert ids(mc.filter_parts(PARTS, MACHINES, q="bw50eag")) == ["1"]
    assert ids(mc.filter_parts(PARTS, MACHINES, q="calpeda มอเตอร์")) == ["2", "4"]
    assert ids(mc.filter_parts(PARTS, MACHINES, q="NM 65 12C")) == ["2"]
    assert ids(mc.filter_parts(PARTS, MACHINES, q="calpeda เครื่อง1 ปั๊มน้ำเย็น")) == ["2"]
    assert ids(mc.filter_parts(PARTS, MACHINES, q="calpeda ซอง บ่อซอง")) == ["4"]               # the machine's own name counts too
    assert mc.filter_parts(PARTS, MACHINES, q="ไม่มีอะไรแบบนี้") == []


def test_parts_filters_combine():
    assert ids(mc.filter_parts(PARTS, MACHINES, machine_id=M1)) == ["1", "2"]
    assert ids(mc.filter_parts(PARTS, MACHINES, location="โรงซอง 1")) == ["3"]
    assert ids(mc.filter_parts(PARTS, MACHINES, part="มอเตอร์", machine_id=M1)) == ["2"]
    assert ids(mc.filter_parts(PARTS, MACHINES)) == ["1", "2", "3", "4"]


# -------------------------------------------------------------------- events
TODAY = date(2026, 10, 22)


def event(**kw):
    return mc.clean_event({"machine_id": M1, "event_date": "2026-10-20", "summary": "เปลี่ยนสายพานพัดลม", **kw}, machines=MACHINES, today=TODAY)


def test_a_report_takes_its_branch_from_the_machine_and_defaults_the_kind():
    e = event(equipment=" พัดลม ", cost="1,250.50", vendor="ช่างเอ๋")
    assert e["branch"] == "สระบุรี" and e["kind"] == "ซ่อม" and e["cost"] == 1250.5 and e["equipment"] == "พัดลม" and e["event_date"] == "2026-10-20"


def test_a_report_with_no_machine_needs_a_branch_and_can_name_an_area():
    e = mc.clean_event({"event_date": "2026-09-11", "summary": "เปลี่ยนมาตรวัดน้ำ", "branch": "สระบุรี", "area": "ระบบน้ำ", "kind": "เปลี่ยนอะไหล่"}, machines=MACHINES, today=TODAY)
    assert e["branch"] == "สระบุรี" and e["area"] == "ระบบน้ำ" and "machine_id" not in e
    with pytest.raises(mc.InvalidMachinery) as exc:
        mc.clean_event({"event_date": "2026-09-11", "summary": "x"}, machines=MACHINES, today=TODAY)
    assert "สาขา" in str(exc.value)


@pytest.mark.parametrize("bad, fragment", [
    ({"summary": " "}, "ทำอะไร"), ({"event_date": ""}, "วันที่ทำ"), ({"event_date": "20/10/2026"}, "ไม่ถูกต้อง"), ({"event_date": "2569-10-20"}, "พ.ศ."),
    ({"event_date": "2026-10-30"}, "อนาคต"), ({"kind": "พัง"}, "ประเภทงาน"), ({"cost": "abc"}, "ตัวเลข"), ({"cost": -5}, "ค่าใช้จ่าย"),
    ({"machine_id": "33333333-3333-3333-3333-333333333333"}, "ไม่พบเครื่อง"),
])
def test_report_validation(bad, fragment):
    with pytest.raises(mc.InvalidMachinery) as exc:
        event(**bad)
    assert fragment in str(exc.value)


def test_a_partial_report_edit_only_returns_what_was_sent():
    existing = {"id": "e", "machine_id": M1, "branch": "สระบุรี", "event_date": "2026-10-20", "kind": "ซ่อม", "summary": "x"}
    assert mc.clean_event({"cost": 500}, existing=existing, machines=MACHINES, today=TODAY) == {"cost": 500, "branch": "สระบุรี"}
    assert mc.clean_event({"summary": "ใหม่"}, existing=existing, machines=MACHINES, today=TODAY)["summary"] == "ใหม่"


EVENTS = [
    {"id": "a", "machine_id": M1, "branch": "สระบุรี", "event_date": "2026-10-20", "kind": "ซ่อม", "summary": "เปลี่ยนสายพานพัดลม", "cost": 1000},
    {"id": "b", "machine_id": None, "branch": "สระบุรี", "area": "ระบบน้ำ", "event_date": "2026-09-11", "kind": "เปลี่ยนอะไหล่", "summary": "เปลี่ยนมาตรวัดน้ำ", "brand": "SANWA"},
    {"id": "c", "machine_id": None, "branch": "แก่งคอย", "event_date": "2026-09-11", "kind": "เปลี่ยนอะไหล่", "summary": "ยาง diaphragm แตก", "vendor": "PMV Engineering"},
]


def test_report_filters():
    f = lambda **kw: [e["id"] for e in mc.filter_events(EVENTS, MACHINES, **kw)]
    assert f() == ["a", "b", "c"]
    assert f(machine_id=M1) == ["a"] and f(area="ระบบน้ำ") == ["b"] and f(kind="เปลี่ยนอะไหล่") == ["b", "c"] and f(branch="แก่งคอย") == ["c"]
    assert f(date_from="2026-10-01") == ["a"] and f(date_to="2026-09-30") == ["b", "c"]
    assert f(q="sanwa") == ["b"] and f(q="เครื่อง 1 สายพาน") == ["a"] and f(q="pmv diaphragm") == ["c"]


# --------------------------------------------------------- database wrappers
class Db:
    def __init__(self, ready=True):
        self.ready = ready
        self.tables = {"machines": [dict(MACHINES[M1]), dict(MACHINES[M2])], "machine_parts": [dict(p) for p in PARTS], "machine_events": [dict(e) for e in EVENTS]}
        self.inserted, self.updates, self.deleted = [], [], []

    def select(self, table, params=None):
        if not self.ready:
            raise mc.supabase.SupabaseError("no table")
        params = params or {}
        rows = self.tables[table]
        if "id" in params and str(params["id"]).startswith("eq."):
            rows = [r for r in rows if r["id"] == params["id"][3:]]
        if params.get("active") == "eq.true":
            rows = [r for r in rows if r.get("active", True)]
        return [dict(r) for r in rows]

    select_all = select

    def insert(self, table, rows, **kw):
        self.inserted.append((table, rows))
        row = {**rows[0], "id": "new-id"}
        self.tables[table].append(row)
        return [row]

    def update(self, table, match, values):
        self.updates.append((table, match, values))
        return [{"id": "x", **values}]

    def delete(self, table, match):
        self.deleted.append((table, match))


@pytest.fixture
def db(monkeypatch):
    d = Db()
    for name in ("select", "select_all", "insert", "update", "delete"):
        monkeypatch.setattr(mc.supabase, name, getattr(d, name))
    monkeypatch.setitem(mc._probe, "at", 0.0)
    return d


def test_listing_machines_counts_parts_and_reports(db):
    rows = {m["id"]: m for m in mc.list_machines()}
    assert rows[M1]["part_count"] == 2 and rows[M1]["event_count"] == 1 and rows[M1]["last_event_date"] == "2026-10-20"
    assert rows[M2]["part_count"] == 1 and rows[M2]["event_count"] == 0 and rows[M2]["last_event_date"] is None


def test_listing_parts_pages_and_offers_filter_choices(db):
    res = mc.list_parts(machine_id=M1)
    assert res["total"] == 2 and {f["value"] for f in res["facets"]["locations"]} == {"เครื่อง 1", "โรงซอง 1", "ซอง"}
    assert mc.list_parts(limit=1, offset=1)["rows"][0]["id"] == P2


def test_listing_reports_gives_totals_over_every_match_and_the_machine_name(db):
    res = mc.list_events(machine_id=M1)
    assert res["total"] == 1 and res["stats"] == {"count": 1, "total_cost": 1000.0} and res["rows"][0]["machine_name"] == "เครื่อง 1"
    assert mc.list_events()["stats"]["count"] == 3


def test_writes_go_through_the_rules(db):
    assert mc.create_part({"location": "เครื่อง 2", "part": "สายพาน", "spec": "B-82", "qty": 4}) == {"part_id": "new-id"}
    assert db.inserted[-1][1][0]["qty"] == 4
    mc.edit_part(P1, {"status": "New"})
    assert db.updates[-1][2] == {"status": "New"}
    with pytest.raises(mc.NotFound):
        mc.edit_part("33333333-3333-3333-3333-333333333333", {"status": "New"})
    mc.delete_part(P1)
    assert db.deleted[-1] == ("machine_parts", {"id": f"eq.{P1}"})
    mc.create_event({"machine_id": M2, "event_date": "2026-10-01", "summary": "เปลี่ยนซีล"})
    assert db.inserted[-1][1][0]["branch"] == "สระบุรี"
    mc.create_machine({"branch": "สระบุรี", "kind": "เครื่อง", "number": 6})
    mc.edit_machine(M1, {"notes": "ตรวจแล้ว"})
    assert db.updates[-1][2] == {"notes": "ตรวจแล้ว"}


def test_everything_is_refused_with_a_clear_message_before_schema_v9_and_reads_are_empty(monkeypatch):
    d = Db(ready=False)
    for name in ("select", "select_all", "insert", "update", "delete"):
        monkeypatch.setattr(mc.supabase, name, getattr(d, name))
    monkeypatch.setitem(mc._probe, "at", 0.0)
    assert mc.list_machines() == [] and mc.list_parts()["rows"] == [] and mc.list_events()["rows"] == []
    for call in (lambda: mc.create_machine({"branch": "ส", "number": 1}), lambda: mc.create_part({"location": "x", "part": "y"}),
                 lambda: mc.create_event({"event_date": "2026-10-01", "summary": "x", "branch": "ส"}), lambda: mc.delete_part(P1)):
        with pytest.raises(mc.InvalidMachinery) as exc:
            call()
        assert "schema_v9.sql" in str(exc.value)
    assert not d.inserted and not d.deleted


# ----------------------------------------------------------------- endpoints
def _client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    return main, TestClient(main.app)


def test_machinery_endpoints_need_the_token_and_map_errors(monkeypatch):
    main, c = _client(monkeypatch)
    for method, url in (("get", "/api/machines"), ("post", "/api/machines"), ("get", f"/api/machines/{M1}"), ("patch", f"/api/machines/{M1}"),
                        ("get", "/api/machine-parts"), ("post", "/api/machine-parts"), ("patch", f"/api/machine-parts/{M1}"), ("delete", f"/api/machine-parts/{M1}"),
                        ("get", "/api/machine-events"), ("post", "/api/machine-events"), ("patch", f"/api/machine-events/{M1}"), ("get", "/api/machinery/options")):
        kw = {"json": {}} if method in ("post", "patch") else {}
        assert getattr(c, method)(url, **kw).status_code == 403, (method, url)

    def invalid(d):
        raise mc.InvalidMachinery("ใส่ว่าทำอะไรด้วย")
    monkeypatch.setattr(main.machinery, "create_event", invalid)
    r = c.post("/api/machine-events?token=secret", json={})
    assert r.status_code == 422 and "ทำอะไร" in r.text
    monkeypatch.setattr(main.machinery, "get_machine", lambda i: (_ for _ in ()).throw(mc.NotFound()))
    assert c.get(f"/api/machines/{M1}?token=secret").status_code == 404

    def boom(d):
        raise main.supabase.SupabaseError("secret internals")
    monkeypatch.setattr(main.machinery, "create_machine", boom)
    r = c.post("/api/machines?token=secret", json={})
    assert r.status_code == 502 and "internals" not in r.text


def test_filters_are_passed_through_the_urls(monkeypatch):
    main, c = _client(monkeypatch)
    seen = {}
    monkeypatch.setattr(main.machinery, "list_parts", lambda **kw: seen.update(parts=kw) or {"total": 0, "rows": []})
    monkeypatch.setattr(main.machinery, "list_events", lambda **kw: seen.update(events=kw) or {"total": 0, "rows": []})
    c.get("/api/machine-parts?token=secret&q=breaker&machine_id=m&location=L&part=P&limit=10&offset=5")
    c.get("/api/machine-events?token=secret&q=x&machine_id=m&area=a&kind=ซ่อม&branch=ส&date_from=2026-01-01&date_to=2026-02-01")
    assert seen["parts"] == {"q": "breaker", "machine_id": "m", "location": "L", "part": "P", "limit": 10, "offset": 5}
    assert seen["events"]["kind"] == "ซ่อม" and seen["events"]["date_from"] == "2026-01-01" and seen["events"]["limit"] == 50


def test_options_say_whether_the_machinery_tables_exist(monkeypatch):
    main, c = _client(monkeypatch)
    monkeypatch.setattr(main.machinery, "has_v9", lambda: False)
    d = c.get("/api/machinery/options?token=secret").json()
    assert d["ready"] is False and "ซ่อม" in d["event_kinds"] and d["kinds"] == ["เครื่อง", "บ่อซอง"]
