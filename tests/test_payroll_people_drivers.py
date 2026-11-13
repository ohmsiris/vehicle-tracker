import os

import pytest

from app import payroll_people_drivers as pd

HEAD = [  # rows 2-4 of "ตารางรายรับ-จ่ายพนง." (A..U), as in the September 2569 drivers' file
    ["ที่", "scan  no.", "รายชื่อ", " ", "รายรับ", None, None, None, None, None, None, "คงเหลือ", "รายจ่าย"],
    [None, None, None, None, "วันทำงาน / OT.", None, None, None, None, "รวมรับ", "รวมจ่าย", None, "สะสม", "เบิกล่วงหน้า", "เงินยืม",
     "ส่วนกลาง", "ทำบัตร", "เงินขาด/กระสอบไม่ครบ", "ความผิด/อื่นๆ", "ปกส.", "ค้างเงิน"],
    [None, None, None, None, "วัน", "เงิน", None, "เงิน", "ซอง%"],
]


def _row(code, name, dept, days, wage, sales, comm, bag, gross, ded, net, deds=(), bag_formula=None):
    """-> (values row, formulas row) for columns A..T."""
    v = [None, code, name, dept, days, wage, sales, comm, bag, gross, ded, net] + [None] * 8
    for i, x in deds:
        v[12 + i] = x
    f = list(v)
    f[8] = bag_formula if bag_formula is not None else bag
    return v, f


def _sheet(*rows):
    values = [list(r) for r in HEAD] + [r[0] for r in rows]
    formulas = [list(r) for r in HEAD] + [r[1] for r in rows]
    return values, formulas


def _check_invariants(r):
    assert abs(sum(r["parts"].values()) + r["refund"] - r["gross"]) <= 1
    assert abs(r["gross"] - r["deduct_total"] - r["net"]) <= 1
    assert abs(sum(r["deductions"].values()) - r["deduct_total"]) <= 1


def test_xlsx_bonus_comes_from_the_bag_formula():
    values, formulas = _sheet(
        _row("SB-PD002", "นายสำเริง(หม่ำ) ทองพูล", "ขับรถหลอด+ซอง (ใหญ่)", 31, 6293, 73140, 2559.9, 2491, 11343.9, 3553, 7790.9,
             deds=[(1, 3000), (3, 553)], bag_formula="=497*3+1000"),
        _row("SB-PD001", "นายทวาย  กำจันทร์", "ขับรถ ซอง (ใหญ่)", 30, 11040, 19070, 381.4, 2769, 14190.4, 4486, 9704.4,
             deds=[(1, 3000), (3, 1057), (7, 429)], bag_formula="=923*3"),
        _row("SB-PD006", "นายสะอาด", "ขับรถหลอด+ซอง (ใหญ่)", 31, 3720, 135810, 6790.5, 1310, 11820.5, 3000, 8820.5,
             deds=[(1, 3000)], bag_formula="=1000+31*10"),
        _row("SB-PD007", "นายนาวิน  บุญพยุง", "พขร.(รถปลีก-เล็ก)", 31, 10850, 99020, 1980.4, 1000, 13830.4, 3954, 9876.4,
             deds=[(1, 3000), (3, 490), (7, 464)]),
        _row("SB-PD028", "นายเกรียงไกร เกษลา", "พขร.(รถปลีก-เล็ก)", 31, 10850, 114158, 2283.16, None, 13133.16, 4464, 8669.16,
             deds=[(1, 3000), (6, 1000), (7, 464)]),
        _row(None, "", "", None, 0, None, 0, None, 0, 0, 0),                                   # blank formula row: no code
        _row("SB-PD019", None, "พขร.(รถปลีก-เล็ก)", None, 0, None, 0, None, 0, 0, 0),           # code but nothing paid
    )
    out = {r["code"]: r for r in pd.parse_xlsx_rows(values, formulas, "2026-09")}
    assert sorted(out) == ["SB-PD001", "SB-PD002", "SB-PD006", "SB-PD007", "SB-PD028"]
    p2 = out["SB-PD002"]
    assert p2["parts"] == {"wage": 6293, "commission": 2559.9, "bag": 1491, "bonus": 1000, "ot": 0, "other": 0}
    assert p2["deductions"]["advance"] == 3000 and p2["deductions"]["common"] == 553 and p2["deduct_total"] == 3553
    assert p2["group"] == "drivers" and p2["source"] == "xlsx" and p2["days"] == 31.0 and p2["dept"] == "ขับรถหลอด+ซอง(ใหญ่)"
    assert out["SB-PD001"]["parts"]["bonus"] == 0 and out["SB-PD001"]["parts"]["bag"] == 2769
    assert out["SB-PD001"]["deductions"]["sso"] == 429
    assert out["SB-PD006"]["parts"]["bonus"] == 1000 and out["SB-PD006"]["parts"]["bag"] == 310
    assert out["SB-PD007"]["parts"]["bonus"] == 1000 and out["SB-PD007"]["parts"]["bag"] == 0
    assert out["SB-PD028"]["deductions"]["other"] == 1000
    for r in out.values():
        _check_invariants(r)
        assert r["refund"] == 0 and not r["flags"]


def test_xlsx_refund_only_from_overrides_and_typed_lump_sums_are_flagged():
    values, formulas = _sheet(
        _row("SB-PD025", "นายสุนทรา  ศรีวะสุข-ป่วยลาออกคืนสะสม", "พขร.หน้าโรง", None, 0, None, 0, 20500, 20500, 0, 20500),
        _row("SB-PD038", "นายสมพงษ์ (รักษาตัว)-คืนสะสม", "เด็กรถมานัส", None, 0, None, 0, 21300, 21300, 2160, 19140,
             deds=[(6, 2160)]),
    )
    sep = {r["code"]: r for r in pd.parse_xlsx_rows(values, formulas, "2026-09")}
    assert sep["SB-PD025"]["refund"] == 20500 and sep["SB-PD025"]["parts"]["bag"] == 0 and not sep["SB-PD025"]["flags"]
    assert sep["SB-PD038"]["refund"] == 0 and sep["SB-PD038"]["parts"]["bag"] == 21300
    assert any("คืนสะสม" in f for f in sep["SB-PD038"]["flags"])
    may = {r["code"]: r for r in pd.parse_xlsx_rows(values, formulas, "2026-05")}
    assert may["SB-PD025"]["refund"] == 0 and may["SB-PD025"]["parts"]["bag"] == 20500     # override is for one month only
    for r in list(sep.values()) + list(may.values()):
        _check_invariants(r)


def test_xlsx_header_must_be_the_drivers_table():
    with pytest.raises(ValueError):
        pd.header_columns([["ที่", "รายชื่อ"], [], []])


# ---- PDF: words as pymupdf gives them (x0, y0, x1, y1, text), laid out like the October 2569 summary

HEADER_WORDS = [(240, 69, 251, 77, "วัน"), (265, 66, 280, 74, "เงิน"), (322, 66, 337, 74, "เงิน"), (348, 69, 365, 77, "ซอง%"),
                (377, 60, 406, 68, "รวมรับ"), (419, 60, 447, 68, "รวมจ่าย"), (457, 51, 491, 59, "คงเหลือ"), (500, 63, 519, 71, "สะสม"),
                (526, 63, 548, 71, "เบิกล่วงหน้า"), (556, 63, 576, 71, "เงินยืม"), (583, 63, 600, 71, "ส่วนกลาง"), (607, 63, 611, 71, "ท"),
                (611, 63, 623, 71, "ำบัตร"), (630, 66, 657, 74, "เงินขาด/กระสอบไม่ครบ"), (663, 66, 681, 74, "ความผิด/อื่นๆ"),
                (688, 63, 703, 71, "ปกส."), (711, 63, 730, 71, "ค้างเงิน"), (790, 54, 821, 62, "สะสมยกไป"), (633, 15, 655, 23, "จัดส่ง")]


def _w(x0, x1, y, t):
    return (x0, y, x1, y + 8, t)


def _pdf_page():
    y1, y2, y3 = 87, 429, 513
    return HEADER_WORDS + [
        _w(33, 37, y1, "1"), _w(46, 80, y1, "SB-PD001"), _w(89, 120, y1, "นายทวาย"), _w(127, 131, y1, "ก"), _w(132, 160, y1, "ำจันทร์"),
        _w(184, 200, y1 + 3, "ขับรถ"), _w(202, 212, y1 + 3, "ซอง"), _w(214, 230, y1 + 3, "(ใหญ่)"),
        _w(244, 252, y1, "29"), _w(262, 285, y1, "10,672"), _w(292, 312, y1, "17,040"), _w(327, 332, y1, "341"), _w(347, 365, y1, "2,688"),
        _w(387, 410, y1, "13,701"), _w(429, 447, y1, "4,339"), _w(473, 491, y1, "9,362"), _w(529, 547, y1, "3,000"),
        _w(587, 600, y1, "910"), _w(691, 703, y1, "429"), _w(744, 748, y1, "-"), _w(799, 821, y1, "21,450"),
        _w(29, 37, y2, "22"), _w(46, 80, y2, "SB-PD028"), _w(244, 252, y2, "30"), _w(262, 285, y2, "10,500"), _w(321, 340, y2, "1,915"),
        _w(387, 410, y2, "12,415"), _w(429, 447, y2, "4,884"), _w(473, 491, y2, "7,531"), _w(529, 547, y2, "3,000"),
        _w(643, 656, y2, "420"), _w(691, 703, y2, "464"), _w(799, 821, y2, "20,000"),
        _w(88, 124, y2 + 3, "นายเกรียงไกร"), _w(125, 183, y2 + 3, "เกษลา-ไม่ให้เบี้ยขยัน"), _w(184, 230, y2 + 3, "พขร.(รถปลีก-เล็ก)"),
        _w(292, 312, y2 + 3, "95,743"), _w(663, 680, y2 + 3, "1,000"),
        _w(29, 37, y3, "27"), _w(46, 80, y3, "SB-PD034"), _w(89, 120, y3, "นายแผน"), _w(121, 150, y3, "อักษรกิ่ง"),
        _w(185, 215, y3, "พขร.หน้าโรง"), _w(244, 252, y3, "30"), _w(262, 285, y3, "12,900"), _w(329, 333, y3, "-"),
        _w(347, 365, y3, "1,000"), _w(387, 410, y3, "13,900"), _w(429, 447, y3, "3,000"), _w(469, 491, y3, "10,900"),
        _w(529, 547, y3, "3,000"),
        _w(119, 130, 600, "รวม"), _w(383, 410, 600, "448,092"), _w(421, 447, 600, "130,656"),
    ]


def test_pdf_rows_by_column_position():
    out = {r["code"]: r for r in pd.parse_pdf_words([_pdf_page()], "2026-10")}
    assert sorted(out) == ["SB-PD001", "SB-PD028", "SB-PD034"]
    a = out["SB-PD001"]
    assert a["name"] == "นายทวาย กำจันทร์" and a["dept"] == "ขับรถซอง(ใหญ่)" and a["source"] == "pdf"
    assert a["days"] == 29 and a["gross"] == 13701 and a["deduct_total"] == 4339 and a["net"] == 9362
    # ซอง% 2,688 is bag money only: 29 days < the 30-day company month, so no 1,000 bonus (checked against every xlsx formula)
    assert a["parts"] == {"wage": 10672, "commission": 341, "bag": 2688, "bonus": 0, "ot": 0, "other": 0}
    assert a["deductions"] == {"savings": 0, "advance": 3000, "loan": 0, "common": 910, "card": 0, "shortage": 0, "other": 0, "sso": 429}
    assert any("guessed 0" in f for f in a["flags"])
    b = out["SB-PD028"]
    assert b["name"] == "นายเกรียงไกร เกษลา" and b["dept"] == "พขร.(รถปลีก-เล็ก)" and "marked ไม่ให้เบี้ยขยัน" in b["flags"]
    assert b["parts"]["commission"] == 1915 and b["parts"]["bonus"] == 0
    assert b["deductions"]["shortage"] == 420 and b["deductions"]["other"] == 1000 and b["deductions"]["sso"] == 464
    c = out["SB-PD034"]
    assert c["dept"] == "พขร.หน้าโรง" and c["parts"]["wage"] == 12900 and c["parts"]["bonus"] == 1000 and c["parts"]["bag"] == 0
    assert any("1,000 guessed" in f for f in c["flags"])
    for r in out.values():
        _check_invariants(r)


def test_fix_thai_rejoins_sara_am():
    assert pd.fix_thai("นายส ำรวย  อสุรินทร์") == "นายสำรวย อสุรินทร์"
    assert pd.fix_thai("นายทวาย ก าจันทร์") == "นายทวาย กำจันทร์"          # fonts that map ำ to า
    assert pd.fix_thai("นายส าเริง(หม ่า) ทองพูล") == "นายสำเริง(หม่ำ) ทองพูล"


def test_period_days_and_deduction_labels():
    assert (pd.period_days("2026-01"), pd.period_days("2026-03"), pd.period_days("2026-10")) == (31, 28, 30)
    assert pd.deduction_key("เงินขาด/ความผิด/กระสอบ") == "shortage" and pd.deduction_key("ความผิด/อื่นๆ") == "other"
    assert pd.deduction_key("อุปกรณ์/อื่นๆ") == "other" and pd.deduction_key("สะสมยกไป") is None and pd.deduction_key("สะสม") == "savings"
    with pytest.raises(ValueError):
        pd.parse_file("x.docx", "2026-10")


# ---- the real HQ files, when this machine has them

BASE = r"E:\Saraburi\ClaudeCode\CP Accounting\เงินเดือน\รายละเอียดเงินเดือน 2569"
SEP = os.path.join(BASE, "09", "เงินเดือน พขร. สระบุรี กันยายน 2569 -.xlsx")
OCT = os.path.join(BASE, "10", "สรุปเงินเดือน พขร. สระบุรี ตุลาคม 2569 -.pdf")


@pytest.mark.skipif(not os.path.exists(SEP), reason="HQ payroll file not on this machine")
def test_real_september_xlsx_adds_up_to_the_group_total():
    rows = pd.parse_file(SEP, "2026-09")
    assert len(rows) == 36 and all(r["code"].startswith("SB-PD") for r in rows)
    assert abs(sum(r["gross"] for r in rows) - 487905) <= 1
    assert abs(sum(r["deduct_total"] for r in rows) - 125054) <= 1
    assert abs(sum(r["net"] for r in rows) - 362851) <= 1
    by = {r["code"]: r for r in rows}
    assert by["SB-PD025"]["refund"] == 20500
    for r in rows:
        _check_invariants(r)


@pytest.mark.skipif(not os.path.exists(OCT), reason="HQ payroll file not on this machine")
def test_real_october_pdf_adds_up_to_the_group_total():
    rows = pd.parse_file(OCT, "2026-10")
    assert len(rows) == 37
    assert sum(r["deduct_total"] for r in rows) == 130656
    assert abs(sum(r["gross"] for r in rows) - 448092) <= 3          # each person's cells are printed rounded to the baht
    assert abs(sum(r["net"] for r in rows) - 317436) <= 3
    for r in rows:
        _check_invariants(r)
