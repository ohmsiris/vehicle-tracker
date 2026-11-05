import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import accounting as ac

ACCOUNTS = {c: {"code": c, "name_en": c, "name_th": c, "kind": k, "section": s, "sort_order": i, "active": True}
            for i, (c, k, s) in enumerate([("1100", "asset", "assets"), ("1110", "asset", "assets"), ("1120", "asset", "assets"), ("1130", "asset", "assets"),
                                           ("1400", "asset", "assets"), ("2100", "liability", "liabilities"), ("4100", "income", "revenue"),
                                           ("5100", "cogs", "cogs"), ("5500", "cogs", "cogs"), ("5510", "cogs", "cogs"), ("6100", "expense", "selling"),
                                           ("6110", "expense", "selling"), ("7900", "expense", "admin")])}


def balanced(e):
    return round(sum(ln["debit"] for ln in e["lines"]), 2) == round(sum(ln["credit"] for ln in e["lines"]), 2)


def test_the_company_month_runs_26th_to_25th_in_two_halves():
    assert ac.period_of(date(2026, 10, 26)) == {"month": "2026-11", "half": 1}
    assert ac.period_of(date(2026, 11, 10)) == {"month": "2026-11", "half": 1}
    assert ac.period_of(date(2026, 11, 11)) == {"month": "2026-11", "half": 2}
    assert ac.period_of(date(2026, 12, 26)) == {"month": "2027-01", "half": 1}
    assert ac.period_bounds("2026-11") == (date(2026, 10, 26), date(2026, 11, 25))
    assert ac.period_bounds("2026-11", 1) == (date(2026, 10, 26), date(2026, 11, 10))
    assert ac.period_bounds("2027-01", 2) == (date(2027, 1, 11), date(2027, 1, 25))


def test_a_truck_bill_splits_repairs_tyres_and_vat_and_goes_to_the_trucks_branch():
    ev = {"id": "e1", "vehicle_id": "v1", "event_date": "2026-10-27", "vendor": "โชคพัฒนา", "total_cost": 10272, "branch": "แก่งคอย"}
    lines = [{"description": "ยางเรเดียล 225/75R14 MICHELIN", "line_total": 9600}, {"description": "VAT 7%", "line_total": 672}]
    e = ac.truck_entry(ev, lines, {"id": "v1", "branch": "สระบุรี", "vehicle_number": "55", "plate_number": "3ฒฆ-8584"})
    by = {ln["account"]: ln for ln in e["lines"]}
    assert by["6110"]["debit"] == 9600 and by["1400"]["debit"] == 672 and by["2100"]["credit"] == 10272 and "6100" not in by
    assert e["branch"] == "สระบุรี" and e["doc_type"] == "tax_invoice" and balanced(e)          # who used it: the truck's branch
    assert ac.truck_entry({**ev, "total_cost": None}, [], None) is None                         # no amount: nothing booked


def test_a_purchase_bill_books_machine_repair_or_supplies_ex_vat():
    b = {"id": "p1", "vendor_name": "วิศวภัณฑ์", "bill_date": "2026-10-20", "branch": "สระบุรี", "vat_mode": "excluded", "vat_amount": 700, "total": 10700, "purpose": "stock"}
    e = ac.purchase_entry(b)
    assert {ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]} == {"5510": 10000, "1400": 700} and balanced(e)
    assert ac.purchase_entry({**b, "purpose": "repair"})["lines"][0]["account"] == "5500"
    assert ac.purchase_entry({**b, "purpose": "reference"}) is None


def test_a_typed_entry_checks_its_fields_and_balances():
    ok = {"entry_date": "2026-11-05", "account": "5100", "branch": "สระบุรี", "amount": "100,000", "vat": "7000", "paid_from": "company", "feed": "electricity"}
    e = ac.manual_entry(ok, accounts=ACCOUNTS, today=date(2026, 11, 5))
    assert e["source"] == "electricity" and balanced(e) and {ln["account"] for ln in e["lines"]} == {"5100", "1400", "1110"}
    for bad in ({**ok, "account": "1100"}, {**ok, "branch": ""}, {**ok, "amount": "0"}, {**ok, "vat": "200000"}, {**ok, "paid_from": "x"}, {**ok, "entry_date": "2569-11-05"}):
        with pytest.raises(ac.InvalidEntry):
            ac.manual_entry(bad, accounts=ACCOUNTS, today=date(2026, 11, 5))


def test_the_pnl_opens_from_section_to_account_to_shop_to_bill():
    def row(acc, d=0, c=0, party="A", eid="x"):
        return {"account": acc, "debit": d, "credit": c, "branch": "สระบุรี", "entry": {"id": eid, "entry_date": "2026-11-01", "source": "manual", "party": party}}
    rows = [row("4100", c=500_000, eid="s"), row("5100", d=100_000, party="PEA", eid="e1"), row("6100", d=10_000, party="ช่างเก่ง", eid="t1"),
            row("6100", d=5_000, party="ช่างเก่ง", eid="t2"), row("1400", d=700, eid="t1"), row("2100", c=15_000, eid="t1")]
    t = ac.pnl_tree(rows, ACCOUNTS)
    assert t["revenue"] == 500_000 and t["cogs"] == 100_000 and t["gross_profit"] == 400_000 and t["net_profit"] == 385_000
    selling = next(s for s in t["sections"] if s["key"] == "selling")
    shop = selling["accounts"][0]["parties"][0]
    assert shop["party"] == "ช่างเก่ง" and shop["total"] == 15_000 and len(shop["entries"]) == 2       # VAT and the payable are not costs


def test_the_checklist_shows_what_is_missing_for_the_half():
    first = {f["key"]: f for f in ac.feed_status(1, {"midmonth": (3, 222_538)}, {}, 2)}
    assert "electricity" not in first and first["midmonth"]["state"] == "done" and first["truck_bill"]["state"] == "auto"
    second = {f["key"]: f for f in ac.feed_status(2, {}, {"water_city": "none"})}
    assert second["electricity"]["state"] == "missing" and second["water_city"]["state"] == "none" and "midmonth" not in second
