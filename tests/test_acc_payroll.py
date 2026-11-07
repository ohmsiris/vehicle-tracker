from app import acc_payroll as ap

# The numbers of a summary's last page as the PDF gives them (Thai labels garbled and dropped): Kaeng Khoi, October 2569.
TAIL = ["1,221", "426,460", "158,654", "158,528", "13,175", "809,483", "150,505", "658,978", "11,500", "135,000", "500", "-", "1,500",
        "300", "-", "1,705", "14,250", "2,000", "12,250", "394,300"]


def test_total_row_and_breakdown():
    s = ap.parse_summary(TAIL)
    assert (s["gross"], s["deductions"], s["net"]) == (809483.0, 150505.0, 658978.0)
    assert s["parts"] == [11500.0, 135000.0, 500.0, 0.0, 1500.0, 300.0, 0.0, 1705.0]


def test_petty_cash_rows_are_not_wages():
    toks = ["xx ค่าใช้จ่าย 11-25/10/69", "-", "40,320", "-", "40,320", "xx เงินสด11-25/10/2569", "171,000", "-", "171,000"] + TAIL
    s = ap.parse_summary(toks)
    assert s["reimburse"] == [40320.0, 171000.0]
    e = ap.build_entry("2026-10", "KK", s)
    d = {ln["account"]: ln["debit"] for ln in e["lines"] if ln["debit"]}
    c = {ln["account"]: ln["credit"] for ln in e["lines"] if ln["credit"]}
    assert d == {"5400": 809483 - 211320}
    assert c["1500"] == 135000 and c["7400"] if "7400" in c else True
    assert c["1100"] == 658978 - 211320 and c["7110"] == 1705 and c["2400"] == 11500
    assert round(sum(d.values()), 2) == round(sum(c.values()), 2)


def test_groups():
    assert ap.group_of("สรุปเงินเดือน พขร. สระบุรี ตุลาคม 2569 -.pdf") == "DRV"
    assert ap.group_of("สรุป-เงินเดือนแก่งคอย ตุลาคม 2569.pdf") == "KK"
    assert ap.group_of("รับสด-เงินเดือน ช่าง.pdf") is None
