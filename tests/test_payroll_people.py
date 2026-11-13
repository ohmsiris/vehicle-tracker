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
