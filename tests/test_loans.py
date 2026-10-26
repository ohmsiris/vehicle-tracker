import uuid
from datetime import date

import pytest

from app import employees as em
from app import loans as ln

TODAY = date(2026, 10, 26)
EMP = "11111111-1111-1111-1111-111111111111"


def terms(mode, **kw):
    return {"repay_mode": mode, "first_month": "2026-11", **kw}


def form(**kw):
    return {"employee_id": EMP, "kind": "accident", "amount": "5,500", "loan_date": "2026-10-20", "reason": "ค่ายกรถ", **terms("monthly_fixed", repay_amount=2000), **kw}


def loan_row(**kw):
    base = {"amount": 5500.0, "first_month": "2026-11-01", "repay_mode": "monthly_fixed", "repay_amount": 2000.0, "repay_months": None, "repay_percent": None, "schedule": None,
            "status": "active", "kind": "accident", "loan_date": "2026-10-20", "employee_id": EMP}
    return {**base, **kw}


def amounts(sch):
    return [(r["month"], r["amount"]) for r in sch]


# ------------------------------------------------------------------ the six ways to pay back
def test_monthly_fixed_pays_the_same_until_the_last_month_takes_the_rest():
    s = ln.build_schedule(loan_row(), 550_000)
    assert amounts(s) == [("2026-11", 2000.0), ("2026-12", 2000.0), ("2027-01", 1500.0)]               # the memo: 2,000 x 2 then 1,500


def test_the_start_can_be_delayed_as_far_as_wanted():
    s = ln.build_schedule(loan_row(first_month="2027-03-01"), 550_000)
    assert [r["month"] for r in s] == ["2027-03", "2027-04", "2027-05"]
    assert ln.add_month("2026-12", 1) == "2027-01" and ln.add_month("2026-01", -1) == "2025-12"


def test_months_equal_uses_whole_baht_and_the_last_month_takes_the_rest():
    s = ln.build_schedule(loan_row(repay_mode="months_equal", repay_months=3, repay_amount=None), 550_000)
    assert [r["amount"] for r in s] == [1833.0, 1833.0, 1834.0] and sum(r["amount"] for r in s) == 5500.0
    assert [r["amount"] for r in ln.build_schedule(loan_row(repay_mode="months_equal", repay_months=1, repay_amount=None), 550_000)] == [5500.0]


def test_percent_of_the_loan_each_month():
    s = ln.build_schedule(loan_row(repay_mode="percent", repay_percent=20.0, repay_amount=None), 550_000)
    assert [r["amount"] for r in s] == [1100.0] * 5


def test_lump_sum_is_one_deduction():
    assert amounts(ln.build_schedule(loan_row(repay_mode="lump_sum", repay_amount=None), 550_000)) == [("2026-11", 5500.0)]


def test_custom_is_what_was_typed():
    s = ln.build_schedule(loan_row(repay_mode="custom", repay_amount=None, schedule=[{"month": "2026-11", "amount": 1000}, {"month": "2027-02", "amount": 4500}]), 550_000)
    assert amounts(s) == [("2026-11", 1000.0), ("2027-02", 4500.0)]


def test_flexible_has_no_schedule():
    assert ln.build_schedule(loan_row(repay_mode="flexible", repay_amount=None), 550_000) == []


# ------------------------------------------------------------------ the form
def test_a_loan_is_cleaned_and_the_first_deduction_defaults_to_next_month():
    v = ln.clean_loan({**form(), "first_month": ""}, today=TODAY)
    assert v["amount"] == 5500.0 and v["first_month"] == "2026-11-01" and v["repay_amount"] == 2000.0 and v["kind"] == "accident" and v["schedule"] is None
    assert ln.clean_loan(form(first_month="2027-02"), today=TODAY)["first_month"] == "2027-02-01"          # delayed


def test_custom_amounts_become_consecutive_months_from_the_first():
    v = ln.clean_loan(form(**terms("custom", amounts="2000, 2000 ; 1500"), repay_amount=None), today=TODAY)
    assert v["schedule"] == [{"month": "2026-11", "amount": 2000.0}, {"month": "2026-12", "amount": 2000.0}, {"month": "2027-01", "amount": 1500.0}]
    v = ln.clean_loan(form(**terms("custom", amounts=[1000, 1000])), today=TODAY)
    assert len(v["schedule"]) == 2


@pytest.mark.parametrize("data,fragment", [
    (form(amount="0"), "มากกว่า 0"), (form(amount="abc"), "ไม่ถูกต้อง"), (form(amount="2000000"), "ไม่เกิน"), (form(employee_id="x"), "เลือกพนักงาน"), (form(kind="x"), "เลือกประเภท"),
    (form(loan_date="2026-12-01"), "อนาคต"), (form(repay_mode="x"), "เลือกวิธี"), (form(repay_amount=""), "ยอดหักต่อเดือน"), (form(repay_amount="6000"), "ไม่เกิน"),
    (form(first_month="2026-13"), "ไม่ถูกต้อง"), (form(first_month="2031-01"), "ไกลเกินไป"),
    (form(**terms("months_equal", repay_months="0")), "จำนวนเดือน"), (form(**terms("percent", repay_percent="150")), "ไม่เกิน"),
    (form(**terms("custom", amounts="3000, 3000")), "มากกว่ายอดกู้"), (form(**terms("custom", amounts="")), "ใส่ยอดหัก"),
])
def test_bad_loans_are_refused_in_thai(data, fragment):
    with pytest.raises(ln.InvalidLoan, match=fragment):
        ln.clean_loan(data, today=TODAY)


def test_the_preview_shows_the_schedule_and_what_is_left_unscheduled():
    p = ln.preview({"amount": 5500, "loan_date": "2026-10-20", **terms("custom", amounts="2000, 2000")}, TODAY)
    assert p["scheduled"] == 4000.0 and p["unscheduled"] == 1500.0 and len(p["schedule"]) == 2
    assert ln.preview({"amount": 5500, "loan_date": "2026-10-20", **terms("flexible")}, TODAY)["unscheduled"] is None


# ------------------------------------------------------------------ where a loan stands
def entry(kind, amount, month=None, **kw):
    return {"kind": kind, "amount": amount, "pay_month": f"{month}-01" if month else None, **kw}


def test_repayments_fill_the_oldest_month_first_and_overdue_months_show():
    now = date(2027, 1, 15)
    s = ln.summarize(loan_row(), [entry("repay", 2000), entry("repay", 500)], now)            # 2,500 repaid: Nov paid, Dec part, Jan (this month) due
    assert [(r["month"], r["paid"], r["state"]) for r in s["schedule"]] == [("2026-11", 2000.0, "paid"), ("2026-12", 500.0, "overdue"), ("2027-01", 0.0, "due")]
    assert s["repaid"] == 2500.0 and s["balance"] == 3000.0 and s["overdue"] == 1500.0 and s["due_now"] == 1500.0
    assert s["next"] == {"month": "2026-12", "amount": 1500.0}


def test_before_the_first_month_nothing_is_due_and_the_next_deduction_is_the_first():
    s = ln.summarize(loan_row(first_month="2027-03-01"), [], TODAY)
    assert s["overdue"] == 0 and s["due_now"] == 0 and s["next"] == {"month": "2027-03", "amount": 2000.0} and all(r["state"] == "future" for r in s["schedule"])


def test_paid_early_covers_the_later_months():
    s = ln.summarize(loan_row(), [entry("repay", 4000)], TODAY)
    assert [r["state"] for r in s["schedule"]] == ["paid", "paid", "future"] and s["next"] == {"month": "2027-01", "amount": 1500.0}


def test_borrowing_more_lengthens_the_plan_and_an_adjustment_changes_the_total():
    s = ln.summarize(loan_row(), [entry("add", 1000)], TODAY)
    assert s["total"] == 6500.0 and amounts(s["schedule"])[-1] == ("2027-02", 500.0)
    s = ln.summarize(loan_row(), [entry("adjust", -500)], TODAY)
    assert s["total"] == 5000.0 and s["balance"] == 5000.0


def test_a_paid_off_loan_has_nothing_next():
    s = ln.summarize(loan_row(), [entry("repay", 5500)], TODAY)
    assert s["balance"] == 0 and s["next"] is None and s["overdue"] == 0 and s["progress"] == 1.0


def test_entries_are_checked_against_the_balance():
    s = ln.summarize(loan_row(), [entry("repay", 5000)], TODAY)
    ok = ln.clean_entry({"kind": "repay", "amount": "500", "pay_month": "2026-11"}, loan=loan_row(), summary=s, today=TODAY)
    assert ok["amount"] == 500.0 and ok["pay_month"] == "2026-11-01" and ok["method"] == "salary"
    for data, frag in (({"kind": "repay", "amount": 600}, "เกินยอดคงเหลือ"), ({"kind": "adjust", "amount": -1000}, "ต่ำกว่า"), ({"kind": "x", "amount": 1}, "ประเภท"),
                       ({"kind": "repay", "amount": 100, "method": "x"}, "วิธีรับเงิน"), ({"kind": "repay", "amount": 100, "entry_date": "2027-01-01"}, "อนาคต")):
        with pytest.raises(ln.InvalidLoan, match=frag):
            ln.clean_entry(data, loan=loan_row(), summary=s, today=TODAY)
    with pytest.raises(ln.InvalidLoan, match="ยกเลิกแล้ว"):
        ln.clean_entry({"kind": "repay", "amount": 1}, loan=loan_row(status="cancelled"), summary=s, today=TODAY)


def test_the_plan_is_described_in_a_sentence():
    assert ln.describe(loan_row()) == "เดือนละ 2,000 เริ่ม 2026-11"
    assert ln.describe(loan_row(repay_mode="flexible")) == "ไม่กำหนดงวด"
    assert "2,000 / 1,500" in ln.describe(loan_row(repay_mode="custom", schedule=[{"month": "2026-11", "amount": 2000}, {"month": "2026-12", "amount": 1500}]))


# ------------------------------------------------------------------ the database calls, on a small fake
class Fake:
    def __init__(self):
        self.t = {"employees": [{"id": EMP, "code": "SB-PD033", "prefix": "นาย", "full_name": "ประเสริฐศักดิ์ แสงเพ็ง", "branch": "สระบุรี", "status": "active"}],
                  "employee_loans": [], "employee_loan_entries": []}

    def _eq(self, rows, params):
        eq = {k: v[3:] for k, v in (params or {}).items() if isinstance(v, str) and v.startswith("eq.")}
        return [r for r in rows if all(str(r.get(k)) == v for k, v in eq.items())]

    def select(self, table, params=None):
        return [dict(r) for r in self._eq(self.t[table], params)]

    select_all = select

    def insert(self, table, rows, **kw):
        out = []
        for r in rows:
            row = {"id": str(uuid.uuid4()), "status": "active", "method": "salary", "created_at": "x", **r}
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
    for name in ("select", "select_all", "insert", "update", "delete"):
        monkeypatch.setattr(ln.supabase, name, getattr(d, name))
        monkeypatch.setattr(em.supabase, name, getattr(d, name))
    monkeypatch.setattr(ln, "has_v23", lambda: True)
    monkeypatch.setattr(em, "has_v21", lambda: True)
    monkeypatch.setattr(em, "today_th", lambda: TODAY)
    monkeypatch.setattr(em, "_all", lambda: [dict(r) for r in d.t["employees"]])
    return d


def test_a_loan_is_saved_and_read_back_with_its_schedule(db):
    card = ln.create_loan(form(amount=5500, **terms("custom", amounts="2000, 2000, 1500"), repay_amount=None))
    assert card["employee"] == "นายประเสริฐศักดิ์ แสงเพ็ง" and card["balance"] == 5500.0 and card["status"] == "active" and card["kind_label"] == "อุบัติเหตุ / ความเสียหาย"
    assert amounts(card["schedule"]) == [("2026-11", 2000.0), ("2026-12", 2000.0), ("2027-01", 1500.0)] and card["next"]["month"] == "2026-11"
    assert ln.list_loans()["totals"]["balance"] == 5500.0


def test_repaying_it_all_closes_the_loan_and_borrowing_more_opens_it_again(db):
    lid = ln.create_loan(form())["id"]
    ln.add_entry(lid, {"kind": "repay", "amount": 3000})
    card = ln.add_entry(lid, {"kind": "repay", "amount": 2500})
    assert card["status"] == "closed" and card["balance"] == 0 and card["closed_date"] == "2026-10-26"
    card = ln.add_entry(lid, {"kind": "add", "amount": 1000})
    assert card["status"] == "active" and card["balance"] == 1000.0
    first = card["entries"][0]["id"]
    card = ln.delete_entry(lid, first)
    assert card["repaid"] == 2500.0 and len(card["entries"]) == 2


def test_a_resigned_employee_cannot_get_a_new_loan_and_a_borrower_cannot_be_deleted(db, monkeypatch):
    ln.create_loan(form())
    monkeypatch.setattr(em, "_get", lambda i: db.t["employees"][0])
    with pytest.raises(em.InvalidEmployee, match="ลาออก"):
        em.delete_employee(EMP)                               # the loan points at them
    db.t["employees"][0]["status"] = "resigned"
    with pytest.raises(ln.InvalidLoan, match="ลาออกแล้ว"):
        ln.create_loan(form())


def test_a_mistaken_loan_can_be_deleted_only_while_it_has_no_entries_and_a_cancelled_one_stays(db):
    lid = ln.create_loan(form())["id"]
    ln.add_entry(lid, {"kind": "repay", "amount": 100})
    with pytest.raises(ln.InvalidLoan, match="ยกเลิก"):
        ln.delete_loan(lid)
    assert ln.cancel_loan(lid, {"note": "ทดสอบ"})["status"] == "cancelled"
    assert ln.list_loans()["totals"]["active"] == 0 and len(ln.list_loans()["loans"]) == 1
    other = ln.create_loan(form())["id"]
    ln.delete_loan(other)
    assert len(ln.list_loans()["loans"]) == 1


def test_editing_the_plan_changes_the_schedule_but_not_the_principal(db):
    lid = ln.create_loan(form())["id"]
    card = ln.edit_loan(lid, {**terms("months_equal", repay_months=2, first_month="2027-01"), "memo_ref": "บันทึก 2/2569"})
    assert amounts(card["schedule"]) == [("2027-01", 2750.0), ("2027-02", 2750.0)] and card["amount"] == 5500.0 and card["memo_ref"] == "บันทึก 2/2569"
    ln.add_entry(lid, {"kind": "repay", "amount": 5500})
    with pytest.raises(ln.InvalidLoan, match="ยังผ่อนอยู่"):
        ln.edit_loan(lid, {"notes": "x"})


def test_what_payroll_deducts_in_a_month_and_recording_it(db):
    ln.create_loan(form(first_month="2026-11"))
    ln.create_loan(form(first_month="2027-01", amount=1000, repay_amount=500))
    m = ln.month_deductions("2026-11")
    assert [(r["code"], r["due"]) for r in m["rows"]] == [("SB-PD033", 2000.0)] and m["total_due"] == 2000.0
    assert ln.month_deductions("2026-10")["rows"] == []
    assert ln.apply_month("2026-11")["recorded"] == 1
    again = ln.month_deductions("2026-11")
    assert again["total_due"] == 0 and again["rows"][0]["recorded"] == 2000.0
    assert ln.apply_month("2026-11")["recorded"] == 0                       # nothing is recorded twice
    assert ln.month_deductions("2026-12")["rows"][0]["due"] == 2000.0
    assert sorted(r["due"] for r in ln.month_deductions("2027-01")["rows"]) == [500.0, 1500.0]        # the first loan's last instalment and the delayed one's first


def test_an_unpaid_month_carries_over_as_overdue(db):
    ln.create_loan(form(first_month="2026-09"))
    m = ln.month_deductions("2026-11")
    assert m["rows"][0]["overdue_before"] == 4000.0 and m["total_overdue"] == 4000.0
