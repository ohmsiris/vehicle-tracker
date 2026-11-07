from datetime import date

import pytest

from app import acc_cheques as ac
from app import electricity as el
from app import pea_bill

# The shape of a PEA Smart Invoice's text layer (Thai words are garbled in the real PDF and are not used): meter 72/9, Oct 2569.
BILL = """#318011438503 *Document Date : 02/11/2569 *Printed : 07-11-2569 10:41:38
xx
Smart Invoice (xx)
xx 72/9 x.8 x x x 18000
Address
Due Date
020019227916
318011438503
835,380.28
20 xx 2569
Multi
G03101
GSBI9815
23062206
4224
31/10/2569
10/2569
22-33 KV
800
Peak 325.60 xx.
132.9300
43,282.01
Off Peak 428.80 xx.
0.0000
0.00
Peak 58200.00 xx
4.1839
243,502.98
Off Peak 174360.00 xx
2.6037
453,981.13
xx (Service Charge)
312.24
xx (Total Based Amount)
741,078.36
xx (Usage)
Comsumption Unit
xx
P
96.688
96.281
325.60
(xx) OP
120.575
120.039
428.80
H
120.053
119.520
426.40
xx
P
14990.460
14917.710
58200.00
(xx) OP
22563.500
22461.460
81632.00
H
20002.410
19886.500
92728.00
xx
232560.00
xx
78.427
78.052
300.00
Amount (Baht)
xx (Based Amount)
741,078.36
xx Ft x.x.69=0.1623 xx
37,744.49
xxxx
1,906.38
*xx (Discount)
xx (Sub Total)
780,729.23
xx 7.00 % (VAT)
54,651.05
xx (Total)
835,380.28
"""


def test_reads_every_field_of_a_pea_bill():
    b = pea_bill.parse_text(BILL)
    assert (b["ca_no"], b["invoice_no"], b["meter_no"], b["address"], b["bill_period"], b["read_date"]) == \
        ("020019227916", "318011438503", "23062206", "72/9", "2026-10", date(2026, 10, 31))
    assert b["multiplier"] == 800 and b["tariff"] == "4224"
    assert (b["demand_peak_kw"], b["demand_offpeak_kw"], b["demand_holiday_kw"]) == (325.6, 428.8, 426.4)
    assert (b["kwh_peak"], b["kwh_offpeak"], b["kwh_holiday"], b["kwh_total"], b["kvar_max"]) == (58200, 81632, 92728, 232560, 300)
    assert b["demand_charge"] == 43282.01 and b["ft_rate"] == 0.1623 and b["ft_amount"] == 37744.49
    assert b["pf_charge"] == 1906.38 and b["discount"] == 0 and b["vat"] == 54651.05 and b["total"] == 835380.28


def test_a_bill_that_does_not_add_up_is_refused():
    with pytest.raises(pea_bill.NotPeaBill):
        pea_bill.parse_text(BILL.replace("243,502.98", "243,999.98"))
    with pytest.raises(pea_bill.NotPeaBill):
        pea_bill.parse_text("just some pdf")


def test_the_bill_is_booked_to_the_month_used_and_explained():
    b = pea_bill.parse_text(BILL)
    e = el.entry_for(b, "สระบุรี")
    assert e["entry_date"] == "2026-10-25" and e["source_id"] == "pea:318011438503"
    debit = {ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]}
    assert debit == {"5100": 780729.23, "1400": 54651.05}
    assert sum(ln["credit"] for ln in e["lines"]) == 835380.28
    v = el.month_view({**b, "read_date": "2026-10-31", "branch": "สระบุรี"})
    assert v["kvar_limit"] == round(428.8 * 0.6197, 1) and v["kvar_over"] > 30          # why the power-factor charge is there
    assert round(v["peak_share"], 3) == round(58200 / 232560, 3)


def test_a_cheque_for_a_stored_bill_pays_it_instead_of_costing_twice():
    rows = ac.parse_sheet([[1.0, "ค่าไฟฟ้า 72/9 เดือน10/69", "ซื้อแคชเชียร์เช็ค", "", 835380.28, "", "", "", "", "", "", ""],
                           [2.0, "ค่าไฟฟ้า อื่น 10/69", "การไฟฟ้า", "", 107.0, "", "", "", "", "", "", ""]])
    out = ac.build(rows, branch="สระบุรี", file_month=date(2026, 11, 1), batch="t", bills={835380.28: "318011438503"})
    d = [{ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]} for e in out["entries"]]
    assert d[0] == {"2100": 835380.28} and d[1] == {"5100": 100.0, "1400": 7.0}


# Kaeng Khoi's bills (older layout, readable Thai, "Consumption Unit" also used by the usage-history box)
def _kk(name):
    import os
    return open(os.path.join(os.path.dirname(__file__), "fixtures", name), encoding="utf-8").read()


def test_older_layout_with_deposit_interest():
    b = pea_bill.parse_text(_kk("pea_kk_2026_02.txt"))
    assert (b["ca_no"], b["address"], b["bill_period"]) == ("020019229331", "150", "2026-02")
    assert (b["kwh_peak"], b["kwh_offpeak"], b["kwh_holiday"], b["kwh_total"]) == (159780.0, 200550.0, 182520.0, 542850.0)
    assert (b["demand_peak_kw"], b["kvar_max"], b["pf_charge"]) == (684.0, 852.0, 4429.53)
    assert b["total"] == 1941337.18 and b["deposit_interest"] == 3197.84 and not b["estimated"]
    e = el.entry_for(b, "แก่งคอย")
    cr = {ln["account"]: ln["credit"] for ln in e["lines"] if ln["credit"]}
    assert cr == {"2100": 1938139.34, "4900": 3197.84}
    assert round(sum(ln["debit"] for ln in e["lines"]), 2) == round(sum(ln["credit"] for ln in e["lines"]), 2)


def test_estimated_month():
    b = pea_bill.parse_text(_kk("pea_kk_2026_07.txt"))
    assert b["estimated"] is True
    assert (b["kwh_peak"], b["kwh_offpeak"], b["kwh_holiday"], b["kwh_total"]) == (233730.0, 229500.0, 236490.0, 699720.0)
    assert (b["demand_peak_kw"], b["kvar_max"], b["total"]) == (1056.0, 864.0, 2621193.24)
