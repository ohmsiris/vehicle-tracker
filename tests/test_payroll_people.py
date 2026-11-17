from app import payroll_people as pp


def _row(month, **kw):
    base = {"month": month, "days": 31, "parts": {"wage": 10850, "commission": 1980.4, "bag": 0, "bonus": 1000, "ot": 0, "other": 0}, "refund": 0,
            "gross": 13830.4, "deductions": {"advance": 3000, "sso": 600}, "deduct_total": 3600, "net": 10230.4, "flags": []}
    base.update(kw)
    return base


def test_money_that_hqs_note_says_is_not_pay_is_shown_apart():
    r = _row("2026-07", parts={"wage": 2100, "commission": 0, "bag": 0, "bonus": 0, "ot": 0, "other": 10000}, gross=12100,
             flags=["ซอง% 10,000: หมายเหตุ 'นางสาวพิว-ลาออก08/07/69'"])
    out = pp.not_pay(r)
    assert out["refund"] == 10000 and out["parts"]["other"] == 0 and out["gross"] == 12100
    big = pp.not_pay(_row("2026-05", parts={"wage": 0, "commission": 0, "bag": 21300, "bonus": 0, "ot": 0, "other": 0}, gross=21300,
                          flags=["ซอง% typed 21,300 (no formula) and the name says คืนสะสม: savings refund / back pay? counted as bag pay"]))
    assert big["refund"] == 21300 and big["parts"]["bag"] == 0
    plain = _row("2026-08", parts={"wage": 4000, "commission": 0, "bag": 12000, "bonus": 0, "ot": 0, "other": 0},
                 flags=["ซอง% 12,000 is far above normal bag pay: savings refund / back pay? counted as bag pay"])
    assert pp.not_pay(plain) is plain                                             # no note from HQ: left for the owner


def test_the_page_months_newest_first_and_the_tiles():
    v = pp.view([_row("2026-09"), _row("2026-10", refund=20500, net=30730.4)])
    assert [m["month"] for m in v["months"]] == ["2026-10", "2026-09"]
    assert v["months"][0]["pay"] == 13830.4                                       # the refund is not pay
    assert v["tiles"]["pay"] == 27660.8 and v["tiles"]["days"] == 62 and v["tiles"]["avg_net"] == 10230.4 and v["tiles"]["year"] == 2569
    assert set(pp.FILES) == {f"2026-{m:02d}" for m in range(1, 11)} and all(len(g) == 3 for g in pp.FILES.values())


def _details(**kw):
    d = {"position": " ขับรถซอง (ใหญ่) ", "rate": 368, "sales": 99020, "commission_pct": 2.0, "loan_before": 5000, "loan_paid": 1000,
         "loan_after": 4000, "savings_balance": 12500,
         "lines": [{"side": "income", "label": "ค่าแรง", "key": "wage", "amount": 10850},
                   {"side": "deduction", "label": "เงินขาด/กระสอบไม่ครบ", "key": "shortage", "amount": 120},
                   {"side": "deduction", "label": "ปกส.", "key": "sso", "amount": 600}],
         "notes": [{"label": "ค้างเงิน", "text": "ยืม 5,000 ผ่อน 1,000"}]}
    d.update(kw)
    return d


def test_hqs_details_pass_through_per_month():
    v = pp.view([_row("2026-10", details=_details())])
    d = v["months"][0]["details"]
    assert d["position"] == "ขับรถซอง (ใหญ่)" and d["rate"] == 368 and d["sales"] == 99020 and d["commission_pct"] == 2.0
    assert (d["loan_before"], d["loan_paid"], d["loan_after"], d["savings_balance"]) == (5000, 1000, 4000, 12500)
    assert [x["label"] for x in d["lines"]] == ["ค่าแรง", "เงินขาด/กระสอบไม่ครบ", "ปกส."]
    assert d["lines"][1] == {"side": "deduction", "label": "เงินขาด/กระสอบไม่ครบ", "key": "shortage", "amount": 120.0}
    assert d["notes"] == [{"label": "ค้างเงิน", "text": "ยืม 5,000 ผ่อน 1,000"}]


def test_old_rows_without_details_still_show():
    empty = {"position": None, "rate": None, "sales": None, "commission_pct": None, "loan_before": None, "loan_paid": None,
             "loan_after": None, "savings_balance": None, "lines": [], "notes": []}
    v = pp.view([_row("2026-09"), _row("2026-08", details={}), _row("2026-07", details=None)])
    assert all(m["details"] == empty for m in v["months"])
    t = v["tiles"]
    assert t["savings_balance"] is None and t["loan_after"] is None and t["savings_month"] is None and t["loan_month"] is None
    assert t["pay"] == 41491.2                                                    # the old tiles unchanged
    assert pp.view([_row("2026-09", details='{"savings_balance": 300}')])["months"][0]["details"]["savings_balance"] == 300.0


def test_tiles_latest_balances_and_the_years_deductions_by_type():
    v = pp.view([_row("2025-12", details=_details(savings_balance=99, loan_after=99), deductions={"savings": 500}, deduct_total=500),
                 _row("2026-08", details=_details(savings_balance=11000, loan_after=5000)),
                 _row("2026-09", details=_details(savings_balance=12500, loan_after=0)),
                 _row("2026-10", details=_details(savings_balance=None, loan_after=None))])  # October has neither: September's figures
    t = v["tiles"]
    assert (t["savings_balance"], t["savings_month"]) == (12500, "2026-09")
    assert (t["loan_after"], t["loan_month"]) == (0, "2026-09")                   # nothing owed is still a figure
    assert t["deductions"]["advance"] == 9000 and t["deductions"]["sso"] == 1800 and t["deductions"]["savings"] == 0   # 2026 only
    assert set(t["deductions"]) == set(pp.DEDUCTION_LABELS) and t["deduct_total"] == 10800


def test_no_loan_is_none_whichever_reader_wrote_it():
    from app import payroll_people as pp
    zero = pp.details_of({"details": {"loan_before": 0, "loan_paid": 0, "loan_after": 0, "savings_balance": 0}})
    assert (zero["loan_before"], zero["loan_paid"], zero["loan_after"]) == (None, None, None)
    paid_off = pp.details_of({"details": {"loan_before": 2500, "loan_paid": 2500, "loan_after": 0}})
    assert paid_off["loan_after"] == 0.0 and paid_off["loan_paid"] == 2500.0
