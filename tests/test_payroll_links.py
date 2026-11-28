"""คำนวณเงินเดือน (owner, 29 Nov 2026): every cell of the unfolded person names where its value is fixed (app/payroll.py person_go),
and the pay rule itself is edited on the pay page (app/payroll_rules.py, PATCH /api/payroll/rules/<id>, the owner only). Literal rows
and a fake database only -- never the live one."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import payroll as pw
from app import payroll_rules as pr
from app import users
from tests.test_payroll import (MONTH, TODAY, END, START, advance_src, box, deduct_pages, drove, emp, factory_pages, helper_src, loan, one, prev, rate,
                                route_src, rule, src, ROWS9, ROWS10)

RULES = {"to": "rules"}


def go(out, code, key):
    return one(out, code)["go"].get(key)


# --------------------------------------------------------------------------------------------- where each value is fixed
def test_a_route_driver_and_his_helper_link_to_their_own_pages():
    out = pw.build(helper_src([]), MONTH, TODAY)
    for key in ("days", "sales"):
        assert go(out, "D1", key) == [{"to": "driver-days", "emp": "D1"}]
    assert go(out, "D1", "rate") == [{"to": "employee", "emp": "D1", "part": "rates"}]
    assert go(out, "D1", "group") == [RULES] and go(out, "D1", "sso")[0] == RULES
    assert go(out, "D1", "fuel") == [{"to": "cash-fuel"}, {"to": "accounts-in", "say": "บัตรน้ำมัน"}]
    # the เด็กรถ: his own scans (เวลาทำงาน), the driver he follows (ยอดขายรายวัน of D1) and the rule that says whom he follows
    assert go(out, "H1", "days") == [{"to": "attendance", "emp": "H1"}, {"to": "driver-days", "emp": "D1", "say": "ของคนขับ"}, RULES]
    assert one(out, "H1")["rule"]["rides_with"] == "D1" and "bank_account" not in one(out, "H1")["rule"]


def test_a_second_rate_links_to_the_rule_and_the_rate_page():
    s = route_src(drove("D1", START, END))
    s["rules"][0]["rate2"] = 344
    out = pw.build(s, MONTH, TODAY)
    assert go(out, "D1", "rate") == [{"to": "employee", "emp": "D1", "part": "rates"}, RULES]
    assert go(out, "D1", "days") == [{"to": "driver-days", "emp": "D1"}, RULES]


def test_the_advance_links_to_the_page_of_the_list_it_was_read_from():
    out = pw.build(advance_src(factory_pages()), MONTH, TODAY)
    assert go(out, "SB-FR001", "advance") == [{"to": "check", "file": "f1", "n": 9, "say": "ใบเบิกกลางเดือน"}]
    assert go(out, "SB-FR008", "advance") == [{"to": "check", "file": "f1", "n": 10, "say": "ใบเบิกกลางเดือน"}]
    # on no list: the drop box to put the list in, and HQ's sheet the stand-in came from
    assert go(out, "SB-FR003", "advance") == [{"to": "accounts-in"}, {"to": "employee", "emp": "SB-FR003", "part": "pay"}]
    assert go(out, "SB-FR003", "savings") == [{"to": "employee", "emp": "SB-FR003", "part": "pay"}]
    assert one(out, "SB-FR001")["cells"]["advance"]["state"] == "ok"             # the cells themselves are unchanged


def test_common_fault_and_other_link_to_the_signed_list_and_the_letter():
    letters = [{"id": "doc-1", "employee_id": "SB-FR005", "doc_date": "2026-11-04", "status": "signed", "penalty": 200}]
    out = pw.build(advance_src(None, penalties=letters, advance_files=[box(deduct_pages(ROWS9, ROWS10), status="booked")]), MONTH, TODAY)
    page9 = {"to": "check", "file": "f1", "n": 9, "say": "ใบเบิก"}
    assert go(out, "SB-FR001", "common")[0] == page9
    assert go(out, "SB-FR008", "common")[0] == {**page9, "n": 10}
    assert go(out, "SB-FR004", "other") == [page9]
    assert go(out, "SB-FR005", "penalty") == [{"to": "document", "id": "doc-1", "say": "ใบเตือน 04/11/2026"}, page9, {"to": "documents", "emp": "SB-FR005"}]


def test_a_loan_links_to_itself_and_several_to_the_persons_list():
    one_loan = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A")], loans=[loan("A")]), MONTH, TODAY)
    assert go(one_loan, "A", "loan") == [{"to": "loan", "id": "L-A"}, {"to": "loans", "emp": "A"}]
    two = pw.build(src(employees=[emp("A")], rules=[rule("A")], rates=[rate("A")], loans=[loan("A"), loan("A", id="L-A2")]), MONTH, TODAY)
    assert go(two, "A", "loan") == [{"to": "loans", "emp": "A"}]


def test_someone_who_left_links_only_his_refund():
    out = pw.build(src(employees=[emp("G", status="resigned", end_date="2026-10-25")], rules=[rule("G")],
                       pay_tail=[{"month": "2026-10", "code": "G", "employee_id": "G", "refund": 0, "savings_balance": 2500}]), MONTH, TODAY)
    gone = one(out, "G")
    assert gone["status"]["key"] == "left_before"
    assert gone["go"] == {"refund": [{"to": "employee", "emp": "G", "part": "pay"}]}


def test_the_owners_own_sso_is_never_named_as_the_list():
    rules_ = [{**rule("A"), "sso": 0, "sso_source": f"{pw.SSO_BY_OWNER} 29/11/2026"}, rule("C")]
    out = pw.build(src(employees=[emp("A"), emp("C")], rules=rules_, rates=[rate("A"), rate("C")], pay_prev=[prev("A", {"sso": 750}), prev("C", {"sso": 625})]),
                   MONTH, TODAY)
    assert one(out, "A")["cells"]["sso"] == {"state": "ok", "value": 0, "text": "0", "note": f"ตาม {pw.SSO_BY_OWNER} 29/11/2026 · ไม่หัก"}
    assert "ไม่มีชื่อใน" not in one(out, "C")["cells"]["sso"]["note"]


# --------------------------------------------------------------------------------------------- the pay rule, checked
ME = str(uuid.UUID(int=1))
HIM = str(uuid.UUID(int=2))


@pytest.mark.parametrize("body, words", [
    ({}, "ไม่มีอะไรให้บันทึก"), ([], "ไม่มีอะไรให้บันทึก"), ({"bank_account": "1"}, "แก้ช่องนี้ในหน้านี้ไม่ได้"), ({"route_until": "2026-11-01"}, "แก้ช่องนี้"),
    ({"pay_group": "XX"}, "กลุ่มคิดเงินไม่ถูกต้อง"), ({"pay_group": None}, "กลุ่มคิดเงินไม่ถูกต้อง"),
    ({"sso": True}, "ต้องเป็นตัวเลข"), ({"sso": "abc"}, "ต้องเป็นตัวเลข"), ({"sso": "nan"}, "ต้องอยู่ระหว่าง"), ({"sso": -1}, "ต้องอยู่ระหว่าง"),
    ({"sso": 1001}, "ต้องอยู่ระหว่าง"), ({"rate2": 0}, "ต้องอยู่ระหว่าง"), ({"rate2": 5000}, "ต้องอยู่ระหว่าง"), ({"commission_pct": 101}, "ต้องอยู่ระหว่าง"),
    ({"bag_rate": "inf"}, "ต้องอยู่ระหว่าง"), ({"bonus": "yes"}, "เบี้ยขยัน"), ({"rides_with": "D1"}, "ไม่ถูกต้อง"), ({"rides_with": ME}, "เลือกตัวเองไม่ได้"),
])
def test_a_wrong_value_is_refused_with_words(body, words):
    with pytest.raises(pr.RuleError, match=words):
        pr.clean(body, ME)


def test_good_values_are_normalised():
    assert pr.clean({"pay_group": "DRV-A", "commission_pct": "5", "bag_rate": 3, "bonus": True, "rate2": "1,344", "rides_with": HIM.upper(), "sso": ""}, ME) == {
        "pay_group": "DRV-A", "commission_pct": 5.0, "bag_rate": 3.0, "bonus": True, "rate2": 1344.0, "rides_with": HIM, "sso": None}
    assert pr.clean({"sso": 0, "rides_with": None, "rate2": None}, ME) == {"sso": 0.0, "rides_with": None, "rate2": None}
    assert issubclass(pr.RuleError, pw.PayrollError)                             # the endpoint answers it as 400


class FakeDb:
    """employees and employee_pay_rules, in memory; every write is recorded."""

    def __init__(self, rules=None, people=(ME, HIM)):
        self.people, self.rules, self.writes = set(people), {r["employee_id"]: dict(r) for r in rules or []}, []

    def select(self, table, params=None):
        key = params.get("id", params.get("employee_id", "")).removeprefix("eq.")
        if table == "employees":
            assert params["select"] == "id"                                     # nothing private is ever read
            return [{"id": key}] if key in self.people else []
        assert table == "employee_pay_rules" and "bank" not in params["select"]
        return [dict(self.rules[key])] if key in self.rules else []

    def update(self, table, match, values):
        self.writes.append(("update", table, match, values))
        key = match["employee_id"].removeprefix("eq.")
        self.rules[key].update(values)
        return [dict(self.rules[key])]

    def insert(self, table, rows, **kw):
        self.writes.append(("insert", table, rows))
        for r in rows:
            self.rules[r["employee_id"]] = dict(r)
        return [dict(r) for r in rows]


@pytest.fixture
def db(monkeypatch):
    from app import supabase
    fake = FakeDb(rules=[{"employee_id": ME, "pay_group": "DAY", "sso": 750, "sso_source": "สปส.1-10 ก.ย. 2569", "bonus": False, "rate2": None, "rides_with": None}])
    for name in ("select", "update", "insert"):
        monkeypatch.setattr(supabase, name, getattr(fake, name))
    forgot = []
    monkeypatch.setattr(pw, "forget", lambda: forgot.append(1))
    fake.forgot = forgot
    return fake


def test_saving_a_changed_sso_says_it_was_the_owners(db):
    got = pr.save(ME, {"sso": 0}, TODAY)
    (kind_, table, match, values), = db.writes
    assert (kind_, table, match) == ("update", "employee_pay_rules", {"employee_id": f"eq.{ME}"})
    assert values["sso"] == 0 and values["sso_source"] == f"{pw.SSO_BY_OWNER} 26/11/2026" and "updated_at" in values
    assert got["sso"] == 0 and got["sso_source"].startswith(pw.SSO_BY_OWNER) and set(got) == {"employee_id", *pw.RULE_FIELDS}
    assert db.forgot == [1]                                                     # the pay page reads the change at once


def test_an_unchanged_sso_keeps_its_list_and_writes_nothing(db):
    got = pr.save(ME, {"sso": "750"}, TODAY)
    assert db.writes == [] and got["sso_source"] == "สปส.1-10 ก.ย. 2569"
    pr.save(ME, {"sso": "750", "rate2": 344}, TODAY)
    (_k, _t, _m, values), = db.writes
    assert "sso" not in values and "sso_source" not in values and values["rate2"] == 344


def test_an_emptied_sso_goes_back_to_hqs_sheet(db):
    pr.save(ME, {"sso": ""}, TODAY)
    assert db.writes[0][3]["sso"] is None and db.writes[0][3]["sso_source"] is None


def test_a_person_with_no_rule_needs_a_group_first(db):
    with pytest.raises(pr.RuleError, match="เลือกกลุ่มคิดเงินก่อน"):
        pr.save(HIM, {"sso": 0}, TODAY)
    assert db.writes == []
    pr.save(HIM, {"pay_group": "DAY", "rides_with": ME}, TODAY)
    (kind_, table, rows), = db.writes
    assert kind_ == "insert" and rows[0]["employee_id"] == HIM and rows[0]["pay_group"] == "DAY" and rows[0]["rides_with"] == ME


def test_nobody_unknown_is_written(db):
    with pytest.raises(pr.RuleError, match="ไม่พบพนักงานคนนี้"):
        pr.save(str(uuid.UUID(int=9)), {"pay_group": "DAY"}, TODAY)
    with pytest.raises(pr.RuleError, match="ไม่พบคนขับที่เลือก"):
        pr.save(ME, {"rides_with": str(uuid.UUID(int=9))}, TODAY)
    with pytest.raises(pr.RuleError, match="รหัสพนักงานไม่ถูกต้อง"):
        pr.save("not-an-id", {"pay_group": "DAY"}, TODAY)
    assert db.writes == []


def test_the_endpoint_is_the_owners_only(db, monkeypatch):
    from app import main
    object.__setattr__(main.settings, "app_token", "secret")
    monkeypatch.setattr(main.supabase, "configured", lambda: True)
    monkeypatch.setattr(users, "session_user", lambda t: {"id": 1, "name": "ก", "username": "a1", "menus": ["payroll"]} if t == "u_pay" else None)
    monkeypatch.setattr(users, "is_owner_user", lambda u: False)
    c = TestClient(main.app)
    url = f"/api/payroll/rules/{ME}"
    assert c.patch(url, json={"sso": 0}, headers={"X-App-Token": "u_pay"}).status_code == 403        # the pay menu reads; it does not write
    assert c.patch(url, json={"sso": 0}).status_code == 403
    assert db.writes == []
    bad = c.patch(url, json={"sso": "x"}, headers={"X-App-Token": "secret"})
    assert bad.status_code == 400 and "ต้องเป็นตัวเลข" in bad.json()["detail"]
    ok = c.patch(url, json={"sso": 0}, headers={"X-App-Token": "secret"})
    assert ok.status_code == 200 and ok.json()["sso"] == 0 and len(db.writes) == 1
