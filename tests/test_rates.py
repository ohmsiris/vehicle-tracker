import uuid
from datetime import date

import pytest

from app import employees as em
from app import rates as rt

TODAY = date(2026, 10, 27)
E1 = "11111111-1111-1111-1111-111111111111"


def rate(day, value, kind="normal"):
    return {"id": str(uuid.uuid4()), "daily_rate": value, "effective_from": day, "kind": kind, "review": False}


def test_a_rate_is_cleaned():
    v = rt.clean_rate({"daily_rate": "1,200.5", "effective_from": "2026-11-01", "kind": "probation", "reason": " ผ่านทดลองงาน ", "review": True, "review_note": " ตรวจ "}, today=TODAY)
    assert v == {"daily_rate": 1200.5, "effective_from": "2026-11-01", "kind": "probation", "reason": "ผ่านทดลองงาน", "memo_ref": None, "review": True, "review_note": "ตรวจ"}
    assert rt.clean_rate({"daily_rate": 390}, today=TODAY)["effective_from"] == "2026-10-27"          # no date: starts today
    assert rt.clean_rate({"daily_rate": 390, "review_note": "x"}, today=TODAY)["review_note"] is None   # a note only goes with a flag


@pytest.mark.parametrize("data,frag", [
    ({"daily_rate": ""}, "ไม่ถูกต้อง"), ({"daily_rate": "0"}, "มากกว่า 0"), ({"daily_rate": "99999"}, "ไม่เกิน"), ({"daily_rate": 390, "effective_from": "2569-01-01"}, "ค.ศ."),
    ({"daily_rate": 390, "effective_from": "2027-06-01"}, "ไกลเกินไป"), ({"daily_rate": 390, "effective_from": "xx"}, "วันที่"), ({"daily_rate": 390, "kind": "x"}, "ประเภท"),
])
def test_bad_rates_are_refused_in_thai(data, frag):
    with pytest.raises(rt.InvalidRate, match=frag):
        rt.clean_rate(data, today=TODAY)


def test_the_rate_for_a_day_is_the_latest_one_that_had_started():
    rows = [rate("2026-01-01", 357, "probation"), rate("2026-04-01", 400), rate("2026-11-01", 440)]
    assert rt.rate_on(rows, date(2025, 12, 31)) is None
    assert rt.rate_on(rows, date(2026, 3, 31))["daily_rate"] == 357
    assert rt.rate_on(rows, date(2026, 4, 1))["daily_rate"] == 400 and rt.rate_on(rows, TODAY)["daily_rate"] == 400
    assert rt.rate_on(rows, date(2026, 11, 1))["daily_rate"] == 440


def test_probation_is_three_months_from_the_start_and_can_be_ended_early():
    assert rt.default_probation_end(date(2026, 10, 15)) == date(2027, 1, 14)
    assert rt.default_probation_end(date(2026, 11, 30)) == date(2027, 2, 27)             # no 30 Feb: the month's last day, less one
    assert rt.default_probation_end(date(2026, 12, 1)) == date(2027, 2, 28) and rt.default_probation_end(None) is None
    assert rt.probation_state(None, TODAY) == {"on": False, "days_left": None, "soon": False}
    s = rt.probation_state("2026-11-20", TODAY)
    assert s["on"] and s["days_left"] == 24 and s["soon"]
    assert rt.probation_state("2026-12-31", TODAY)["soon"] is False
    assert rt.probation_state("2026-10-27", TODAY)["on"] and rt.probation_state("2026-10-27", TODAY)["days_left"] == 0
    assert rt.probation_state("2026-10-26", TODAY)["on"] is False and rt.probation_state("2026-10-26", TODAY)["past"] is True


def test_an_employee_can_get_a_probation_end_and_a_payer():
    ok = em.clean_employee({"code": "SB-FR200", "full_name": "นายใหม่ มาก", "branch": "สระบุรี", "start_date": "2026-10-20", "probation_end": "2027-01-19", "pay_group": "hq"}, others=[], today=TODAY)
    assert ok["probation_end"] == "2027-01-19" and ok["pay_group"] == "hq"
    for bad, frag in (({"probation_end": "2026-10-01"}, "ก่อนวันเริ่มงาน"), ({"pay_group": "x"}, "ผู้จ่าย")):
        with pytest.raises(em.InvalidEmployee, match=frag):
            em.clean_employee({"code": "SB-FR201", "full_name": "x", "branch": "สระบุรี", "start_date": "2026-10-20", **bad}, others=[], today=TODAY)
    assert em.clean_employee({"probation_end": ""}, existing={"id": "1", "code": "SB-FR001"}, others=[], today=TODAY) == {"probation_end": None}


class Fake:
    def __init__(self):
        self.t = {"employees": [{"id": E1, "code": "SB-KK062", "full_name": "ธนวัฒน์ บุตรดี", "branch": "แก่งคอย", "status": "active", "start_date": "2026-03-26", "probation_end": "2026-06-25", "pay_group": "company"}],
                  "employee_rates": [], "attendance_punches": [], "attendance_reviews": []}

    def _eq(self, rows, params):
        eq = {k: v[3:] for k, v in (params or {}).items() if isinstance(v, str) and v.startswith("eq.")}
        return [r for r in rows if all(str(r.get(k)) == v for k, v in eq.items())]

    def select(self, table, params=None):
        return [dict(r) for r in self._eq(self.t[table], params)]

    select_all = select

    def insert(self, table, rows, **kw):
        out = []
        for r in rows:
            row = {"id": str(uuid.uuid4()), **r}
            self.t[table].append(row)
            out.append(dict(row))
        return out

    def update(self, table, match, values):
        rows = self._eq(self.t[table], match)
        for r in rows:
            r.update(values)
        return rows

    def delete(self, table, match):
        for r in self._eq(self.t[table], match):
            self.t[table].remove(r)


@pytest.fixture
def db(monkeypatch):
    d = Fake()
    for mod in (rt, em):
        for name in ("select", "select_all", "insert", "update", "delete"):
            monkeypatch.setattr(mod.supabase, name, getattr(d, name))
    monkeypatch.setattr(rt, "has_v25", lambda: True)
    monkeypatch.setattr(em, "has_v21", lambda: True)
    monkeypatch.setattr(em, "today_th", lambda: TODAY)
    return d


def test_a_raise_is_a_new_rate_and_the_old_one_stays_as_history(db):
    rt.add_rate(E1, {"daily_rate": 357, "effective_from": "2026-03-26", "kind": "probation"})
    out = rt.add_rate(E1, {"daily_rate": 400, "effective_from": "2026-06-26", "reason": "ผ่านทดลองงาน", "memo_ref": "บันทึก 15 ก.ค. 2569"})
    assert [r["daily_rate"] for r in out["rates"]] == [400.0, 357.0] and out["current"]["daily_rate"] == 400.0 and out["current"]["kind"] == "normal"
    assert out["probation"]["on"] is False and out["default_probation_end"] == "2026-06-25"


def test_a_second_rate_on_the_same_date_is_a_correction(db):
    rt.add_rate(E1, {"daily_rate": 390, "effective_from": "2026-09-01"})
    out = rt.add_rate(E1, {"daily_rate": 400, "effective_from": "2026-09-01", "review": True, "review_note": "ตรวจ"})
    assert len(out["rates"]) == 1 and out["current"]["daily_rate"] == 400.0 and out["current"]["review"] is True


def test_a_rate_can_be_removed_and_an_employee_with_rates_cannot_be_deleted(db, monkeypatch):
    out = rt.add_rate(E1, {"daily_rate": 357, "effective_from": "2026-03-26"})
    monkeypatch.setattr(em, "_get", lambda i: db.t["employees"][0])
    monkeypatch.setattr(em, "REFERENCED_BY", [("employee_rates", "employee_id")])
    with pytest.raises(em.InvalidEmployee, match="ลาออก"):
        em.delete_employee(E1)
    assert rt.delete_rate(out["rates"][0]["id"])["rates"] == []
    with pytest.raises(em.NotFound):
        rt.delete_rate(str(uuid.uuid4()))


def test_the_list_shows_each_persons_current_rate_and_probation(db, monkeypatch):
    monkeypatch.setattr(em, "_all", lambda: [dict(r) for r in db.t["employees"]])
    monkeypatch.setattr(em, "_drivers", lambda: {})
    rt.add_rate(E1, {"daily_rate": 400, "effective_from": "2026-06-26"})
    db.t["employees"][0]["probation_end"] = "2026-11-10"
    card = em.list_employees()["employees"][0]
    assert card["daily_rate"] == 400.0 and card["rate_since"] == "2026-06-26" and card["probation"]["on"] and card["probation"]["soon"] and card["pay_group"] == "company"
